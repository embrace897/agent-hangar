"""AG-UI (https://docs.ag-ui.com) na central: o protocolo de agente para a interface do app de alguém (CopilotKit,
React Native, Slack, Teams). Os agentes de chat respondem no próprio runtime (POST /agui); aqui ficam os dois casos
que o runtime não atende sozinho:

- **Digital employee:** a conversa vira uma TAREFA, e assim passa pela alçada. Quando uma ação precisa de aprovação
  (ou o funcionário pergunta algo, ou a entrega vai para revisão), a rodada termina com `outcome: interrupt`. Quem
  decide é quem a alçada manda, no portal, no Slack, no Teams ou por e-mail; o app mostra o pedido e, com `resume`,
  volta a acompanhar a tarefa. A decisão nunca vem do app: a chave que o app usa não ganha o poder de aprovar.
- **Agente com harness:** a conversa vira um job no container efêmero, e o resultado volta como mensagem.

Um interrupt também cobre a tarefa que demora mais do que a conexão aguenta (`hangar.still_running`): retomar
volta a acompanhar a mesma tarefa.
"""
import asyncio
import json
import re
import time
import uuid

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from .. import config, tenancy
from ..db import SessionLocal
from ..models import Employee, EmployeeTask, HumanRequest, TaskEvent
from .common import PlatformError

WAIT_S = 600  # quanto uma rodada acompanha a tarefa antes de devolver "ainda trabalhando" (o app retoma)
POLL_S = 1.0
KEEPALIVE_S = 10
FINAL = ("done", "failed", "cancelled")
NEEDS_PERSON = ("waiting_human", "in_review")
# hangar-task-<tarefa>.<último evento visto> | hangar-decision-<pedido>.<último evento visto>: retomar mostra o que o
# app perdeu enquanto não acompanhava (o cursor só lê eventos de uma tarefa que a própria pessoa pediu)
# o que um evento da tarefa leva ao app: o que aconteceu, nunca o conteúdo (argumentos, resultados, quem decidiu).
# A chave é de uma pessoa, mas quem usa o app pode ser outra; o detalhe fica na linha do tempo da tarefa no portal.
EVENT_FIELDS = {"tool", "action_type", "mode", "decision", "run", "id", "request", "kind", "status", "state", "steps",
                "attempt", "stage", "due_at", "platform", "escalated", "task", "wait"}
_ID = re.compile(r"^hangar-(task|decision)-(\d+)(?:\.(\d+))?$")


def sse(ev: dict) -> str:
    return "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"


def text_message(text: str) -> list[dict]:
    mid = f"msg-{uuid.uuid4().hex[:12]}"
    return [{"type": "TEXT_MESSAGE_START", "messageId": mid, "role": "assistant"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": text},
            {"type": "TEXT_MESSAGE_END", "messageId": mid}]


def ids(body: dict) -> tuple[str, str]:
    return str(body.get("threadId") or uuid.uuid4()), str(body.get("runId") or uuid.uuid4())


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def last_user_text(body: dict) -> str:
    return next((_text(m.get("content")) for m in reversed(body.get("messages") or [])
                 if isinstance(m, dict) and m.get("role") == "user"), "")


def task_request(body: dict) -> tuple[str, str]:
    """Título (a primeira linha do último pedido) e corpo da tarefa (o pedido, o contexto do app e o histórico
    recente da conversa, para o funcionário não começar do zero)."""
    msgs = [m for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    ask = last_user_text(body).strip()
    if not ask:
        raise PlatformError("mande ao menos uma mensagem do usuário (role=user)")
    title = ask.splitlines()[0][:120]
    parts = [ask]
    ctx = [c for c in body.get("context") or [] if isinstance(c, dict)]
    if ctx:
        parts.append("Contexto do app:\n" + "\n".join(f"- {c.get('description', '')}: {c.get('value', '')}" for c in ctx))
    history = [m for m in msgs[:-1] if _text(m.get("content")).strip()][-10:]
    if history:
        parts.append("Conversa até aqui:\n" + "\n".join(
            f"{'Pessoa' if m['role'] == 'user' else 'Você'}: {_text(m.get('content'))[:1500]}" for m in history))
    return title, "\n\n".join(parts)


# ------------------------------------------------------------------ Digital employee
def employee_of(db, agent_id: int) -> Employee | None:
    return db.scalar(select(Employee).where(Employee.agent_id == agent_id))


def _interrupt(r: HumanRequest, task: EmployeeTask, seen: int) -> dict:
    reason = {"approval": "hangar.approval", "question": "hangar.question", "capability": "hangar.capability",
              "review": "hangar.review"}.get(r.kind, f"hangar.{r.kind}")
    if r.kind == "approval":
        what = f"A ação '{r.tool_name}' ({r.action_type}) precisa de aprovação."
    elif r.kind == "review":
        what = "A entrega está em revisão."
    else:
        what = (r.question or "O Digital employee precisa de uma resposta.")[:500]
    it = {"id": f"hangar-decision-{r.id}.{seen}", "reason": reason,
          "message": f"{what} Quem decide é quem a alçada indica, no Agent Hangar (portal, Slack, Teams ou e-mail). "
                     "Retome depois para acompanhar a tarefa.",
          # o app só mostra; a decisão é tomada no hangar. Nenhum argumento da ação sai daqui (DLP).
          "metadata": {"taskId": task.id, "requestId": r.id, "kind": r.kind, "actionType": r.action_type or None,
                       "tool": r.tool_name or None, "decideUrl": f"{config.PUBLIC_BASE_URL}/app/#/decisions"}}
    if r.expires_at:
        it["expiresAt"] = r.expires_at.isoformat()
    return it


def _still_running(task: EmployeeTask, seen: int) -> dict:
    return {"id": f"hangar-task-{task.id}.{seen}", "reason": "hangar.still_running",
            "message": "A tarefa continua no Agent Hangar. Retome para acompanhar.",
            "metadata": {"taskId": task.id, "status": task.status}}


def _snapshot(task_id: int, after: int, org: int | None) -> dict:
    """O estado da tarefa em dados simples (a conexão com o banco não fica presa enquanto o app lê o stream)."""
    with tenancy.using(org), SessionLocal() as db:
        task = db.get(EmployeeTask, task_id)
        evs = db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.id > after).order_by(TaskEvent.id))
        reqs = db.scalars(select(HumanRequest).where(HumanRequest.task_id == task_id, HumanRequest.status == "open")
                          .order_by(HumanRequest.id))
        events = [(ev.id, ev.kind, {k: v for k, v in (ev.payload or {}).items() if k in EVENT_FIELDS}) for ev in evs]
        seen = events[-1][0] if events else after
        # o que o app pode mostrar ao vivo: a situação e o plano (títulos e etapas, sem notas)
        board = {"taskId": task.id, "status": task.status,
                 "plan": [{"title": str(x.get("title", ""))[:200], "status": x.get("status", "todo")}
                          for x in (task.plan or []) if isinstance(x, dict)]}
        return {"status": task.status, "result": task.result, "error": task.error, "events": events, "board": board,
                "interrupts": [_interrupt(r, task, seen) for r in reqs] if task.status in NEEDS_PERSON else [],
                "still_running": _still_running(task, seen)}


def start_or_resume(db, acc, e: Employee, slug: str, body: dict) -> tuple[int, int, bool]:
    """(id da tarefa, último evento já visto, é retomada?). Retomar só vale para uma tarefa deste funcionário pedida
    pela mesma pessoa (ou por um admin), e não repete os eventos já mostrados."""
    resume = [r for r in body.get("resume") or [] if isinstance(r, dict)]
    if resume:
        m = _ID.match(str(resume[0].get("interruptId") or ""))
        if not m:
            raise PlatformError("interruptId desconhecido: retome com o id que veio no interrupt")
        if m.group(1) == "task":
            task = db.get(EmployeeTask, int(m.group(2)))
        else:
            r = db.get(HumanRequest, int(m.group(2)))
            task = db.get(EmployeeTask, r.task_id) if r and r.task_id else None
        if task is None or task.employee_id != e.id:
            raise PlatformError("essa tarefa não é deste agente")
        if not acc.p.is_admin and task.requester_user_id != acc.p.user_id:
            raise PlatformError("só quem pediu a tarefa acompanha por aqui")
        if all(r.get("status") == "cancelled" for r in resume) and task.status not in FINAL:
            from . import employees
            employees.cancel_task(db, acc, task.id)
            db.refresh(task)
        return task.id, int(m.group(3) or 0), True
    from . import employees
    title, text = task_request(body)
    t = employees.assign(db, acc, slug, title, text, 2, None, "agui")
    return t["id"], 0, False


async def employee_stream(task_id: int, after: int, thread: str, run: str, wait_s: float | None = None):
    """Acompanha a tarefa e traduz para eventos AG-UI até ela terminar, precisar de uma pessoa ou passar do tempo."""
    org = tenancy.current()
    wait_s = WAIT_S if wait_s is None else wait_s
    yield sse({"type": "RUN_STARTED", "threadId": thread, "runId": run})
    yield sse({"type": "CUSTOM", "name": "hangar.task", "value": {"taskId": task_id}})
    t0 = last_ping = time.monotonic()
    board = None
    while True:
        snap = await run_in_threadpool(_snapshot, task_id, after, org)
        if snap["board"] != board:  # estado compartilhado: a tarefa numa chave própria, sem mexer no resto do app
            board = snap["board"]
            yield sse({"type": "STATE_DELTA", "delta": [{"op": "add", "path": "/hangarTask", "value": board}]})
        for ev_id, kind, payload in snap["events"]:
            after = ev_id
            if kind == "tool" and payload.get("tool"):
                yield sse({"type": "STEP_STARTED", "stepName": str(payload["tool"])})
                yield sse({"type": "STEP_FINISHED", "stepName": str(payload["tool"])})
            yield sse({"type": "CUSTOM", "name": "hangar.task_event", "value": {"kind": kind, "payload": payload}})
            last_ping = time.monotonic()
        if snap["status"] in FINAL:
            if snap["status"] == "done":
                for x in text_message(snap["result"] or "(tarefa concluída sem texto)"):
                    yield sse(x)
                yield sse({"type": "RUN_FINISHED", "threadId": thread, "runId": run, "result": {"taskId": task_id}})
            else:
                yield sse({"type": "RUN_ERROR", "message": snap["error"] or f"tarefa {snap['status']}",
                           "code": f"task_{snap['status']}"})
            return
        if snap["interrupts"]:
            yield sse({"type": "RUN_FINISHED", "threadId": thread, "runId": run,
                       "outcome": {"type": "interrupt", "interrupts": snap["interrupts"]}})
            return
        if time.monotonic() - t0 > wait_s:
            yield sse({"type": "RUN_FINISHED", "threadId": thread, "runId": run,
                       "outcome": {"type": "interrupt", "interrupts": [snap["still_running"]]}})
            return
        if time.monotonic() - last_ping > KEEPALIVE_S:
            yield ": keepalive\n\n"
            last_ping = time.monotonic()
        await asyncio.sleep(POLL_S)


# ------------------------------------------------------------------ agente com harness
async def harness_stream(invoke, body: dict):
    """O pedido vira um job no container do harness; enquanto ele roda, keepalive; no fim, a resposta (e o diff,
    num evento próprio, para o app que quiser mostrar)."""
    thread, run = ids(body)
    yield sse({"type": "RUN_STARTED", "threadId": thread, "runId": run})
    text = last_user_text(body)
    if not text.strip():
        yield sse({"type": "RUN_ERROR", "message": "mande ao menos uma mensagem do usuário (role=user)", "code": "bad_input"})
        return
    yield sse({"type": "STEP_STARTED", "stepName": "job"})
    job = asyncio.ensure_future(invoke(text))
    while not job.done():
        done, _ = await asyncio.wait({job}, timeout=KEEPALIVE_S)
        if not done:
            yield ": keepalive\n\n"
    yield sse({"type": "STEP_FINISHED", "stepName": "job"})
    try:
        r = job.result()
    except PlatformError as e:
        yield sse({"type": "RUN_ERROR", "message": str(e), "code": "job_error"})
        return
    for x in text_message(r.get("reply") or "(sem resposta)"):
        yield sse(x)
    yield sse({"type": "CUSTOM", "name": "hangar.job", "value": {"jobId": r.get("job_id"), "status": r.get("status"),
                                                                 "diff": r.get("diff")}})
    yield sse({"type": "CUSTOM", "name": "hangar.usage", "value": r.get("usage") or {}})
    yield sse({"type": "RUN_FINISHED", "threadId": thread, "runId": run})
