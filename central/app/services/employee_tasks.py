"""Digital employee: tarefas, gate, humano no circuito e o executor em segundo plano.

Uma tarefa roda em rodadas. Cada rodada chama o agente (com o cabeçalho X-Hangar-Task) a partir do último checkpoint.
Quando o runtime do agente encontra uma ação que a alçada não libera, ele pergunta ao gate (/internal/gate): o gate
cria o pedido de decisão e a rodada termina em "aguardando". A decisão humana grava uma mensagem no checkpoint e
recoloca a tarefa na fila; na rodada seguinte o agente refaz a chamada e o gate libera exatamente aquela ação, uma vez.
Sem decisão no prazo: escala para o substituto; sem decisão de novo, expira em "não fazer" e a tarefa é avisada.
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .. import config, deploy, tenancy
from ..db import SessionLocal
from ..models import Agent, Employee, EmployeeTask, HumanRequest, TaskCheckpoint, TaskEvent, TeamMember, User, now
from . import employee_gate as gatemod
from .access import Access, Forbidden
from .common import PlatformError, audit, iso
from .usage import record_usage

log = logging.getLogger("hangar.employees")
WAIT_MARK = re.compile(r"^\[\[HANGAR_WAITING:(\d+)\]\]\s*", re.S)
JOB_MARK = re.compile(r"\[job #(\d+)")  # resultado de um job de código (runtime: _a2a_call)
TERMINAL = ("done", "failed", "cancelled", "expired")
BLOCKING = ("approval", "question", "capability", "plan", "review_fields", "doc_change", "message")  # pausam a tarefa até alguém decidir
SHADOW_REAL = ("read", "delegate")  # no modo sombra estas rodam de verdade; o resto é simulado
QUIET = ("notice", "shadow")  # registros que não esperam decisão na caixa de Decisões
WORKER = f"{os.environ.get('REPLICA_ID') or os.environ.get('HOSTNAME') or 'central'}-{os.getpid()}"
HTTP = lambda: httpx.Client(timeout=900)  # noqa: E731  (os testes trocam)
_pool = ThreadPoolExecutor(max_workers=max(1, config.EMPLOYEE_WORKERS), thread_name_prefix="employee")
_stop = threading.Event()


def _aware(dt):
    from datetime import UTC
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def event(db: Session, task: EmployeeTask, kind: str, payload: dict | None = None):
    db.add(TaskEvent(task_id=task.id, kind=kind, payload=payload or {}))
    task.updated_at = now()


def last_checkpoint(db: Session, task: EmployeeTask) -> TaskCheckpoint | None:
    return db.scalar(select(TaskCheckpoint).where(TaskCheckpoint.task_id == task.id).order_by(TaskCheckpoint.seq.desc()))


def checkpoint(db: Session, task: EmployeeTask, messages: list[dict]):
    cp = last_checkpoint(db, task)
    db.add(TaskCheckpoint(task_id=task.id, seq=(cp.seq + 1) if cp else 1, messages=messages))
    event(db, task, "checkpoint", {"messages": len(messages)})


def lang_of(e: Employee | None) -> str:
    """Idioma do cargo (pt | en): o prompt da tarefa e as instruções do runtime seguem a língua em que o cargo foi escrito."""
    if e is None:
        return "pt"
    from .composer import _lang_pt
    return "pt" if _lang_pt(" ".join([e.title or "", e.mission or "", *(e.responsibilities or [])])) else "en"


def task_jobs(db: Session, task: EmployeeTask) -> list[int]:
    """Jobs de código já registrados na linha do tempo da tarefa."""
    return [int(ev.payload.get("job")) for ev in db.scalars(select(TaskEvent).where(
        TaskEvent.task_id == task.id, TaskEvent.kind == "job")) if ev.payload.get("job")]


def retry_at(attempts: int):
    """Espera crescente entre tentativas de uma rodada que falhou: 30 s, 60 s, 120 s… até 10 min."""
    return now() + timedelta(seconds=min(config.EMPLOYEE_RETRY_BASE_S * 2 ** max(attempts - 1, 0), 600))


def task_prompt(task: EmployeeTask, lang: str = "pt", lessons: list | None = None, extra: list | None = None) -> str:
    en = lang == "en"
    parts = [f"{'TASK' if en else 'TAREFA'} #{task.id}: {task.title}"]
    if task.body:
        parts.append(task.body)
    meta = [f"{'Requested by' if en else 'Pedida por'}: {task.requester or '—'}"]
    if task.due_at:
        meta.append(f"{'Due' if en else 'Prazo'}: {_aware(task.due_at).isoformat(timespec='minutes')}")
    if task.probation:
        meta.append("This is a probation task: do it as you would in real work." if en else
                    "Esta é uma tarefa do período de experiência: faça como faria no trabalho real.")
    parts.append("\n".join(meta))
    if (task.mode or "work") == "copilot":  # modo par: a pessoa faz; o funcionário orienta, revisa e explica
        parts.append("PAIR MODE: the person does the work. Suggest the next step, explain why, check what they did "
                     "against the procedure and point out problems. Do not take actions that change anything — reading "
                     "is fine." if en else
                     "MODO PAR: quem faz é a pessoa. Sugira o próximo passo, explique o porquê, confira o que ela fez contra o "
                     "procedimento e aponte problemas. Não execute ações que mudam algo — ler e consultar pode.")
    if lessons:  # o que o gestor ensinou (aplicado a partir das sugestões de aprendizado)
        head = "Lessons from your manager (follow them):" if en else "Lições do seu gestor (siga-as):"
        parts.append(head + "\n" + "\n".join(f"- {x['text']}" for x in lessons if x.get("text")))
    parts.extend(extra or [])  # plano, colegas para repasse e trabalhos anteriores parecidos (employee_teamwork)
    parts.append("When you finish, answer with the final result of the task. If information is missing, use ask_human. "
                 "If you lack a tool or specialist the task needs, use request_capability instead of improvising."
                 if en else "Quando terminar, responda com o resultado final da tarefa. Se faltar informação, use ask_human. "
                 "Se faltar uma ferramenta ou especialista que a tarefa exige, use request_capability em vez de improvisar.")
    return "\n\n".join(parts)


def create_task(db: Session, e: Employee, title: str, body: str = "", source: str = "portal", requester: str = "",
                requester_user_id: int | None = None, priority: int = 2, due_at=None, probation: bool = False,
                expected: str = "", status: str = "new", dedupe_key: str = "",
                routine_id: int | None = None, plan_approval: bool = False, project_id: int | None = None,
                goal_id: int | None = None) -> EmployeeTask:
    if not (title or "").strip():
        raise PlatformError("a tarefa precisa de um título")
    if e.status == "offboarded":
        raise PlatformError("este Digital employee foi desligado")
    t = EmployeeTask(employee_id=e.id, title=title.strip()[:300], body=body or "", source=source, requester=requester,
                     requester_user_id=requester_user_id, priority=max(1, min(int(priority or 2), 3)), due_at=due_at,
                     probation=probation, expected=expected or "", status=status, dedupe_key=(dedupe_key or "")[:200],
                     routine_id=routine_id, plan_approval=bool(plan_approval), project_id=project_id, goal_id=goal_id)
    db.add(t)
    db.flush()
    from . import employee_teamwork as teamwork
    past = [] if probation else teamwork.recall(db, e, t.title, t.body)
    extra = [] if probation else teamwork.prompt_extras(db, e, t, past)
    from .goals import task_context
    ctx = task_context(db, t, lang_of(e) == "en")  # projeto, instruções e a cadeia de metas: o porquê da tarefa
    if ctx:
        extra = [ctx, *extra]
    event(db, t, "created", {"source": source, "requester": requester, "probation": probation,
                             **({"routine": routine_id} if routine_id else {}),
                             **({"dedupe_key": t.dedupe_key} if t.dedupe_key else {}),
                             **({"recalled": [p["id"] for p in past]} if past else {})})
    db.add(TaskCheckpoint(task_id=t.id, seq=1, messages=[{"role": "user", "content": task_prompt(
        t, lang_of(e), None if probation else e.lessons, extra)}]))
    db.commit()
    return t


# ------------------------------------------------------------------ quem decide
def _team_maintainers(db: Session, team_id: int | None) -> list[int]:
    if not team_id:
        return []
    return [m.user_id for m in db.scalars(select(TeamMember).where(TeamMember.team_id == team_id,
                                                                   TeamMember.role == "maintainer"))]


def _route(db: Session, e: Employee, approver: str, exclude: int | None = None) -> tuple[int | None, int | None]:
    """(responsável, substituto). approver: manager | team_maintainer | user:<e-mail>. exclude: quem não pode decidir
    (separação de funções)."""
    if approver.startswith("user:"):
        u = db.scalar(select(User).where(User.email == approver[5:].strip().lower()))
        first = [u.id] if u else []
    elif approver == "team_maintainer":
        first = _team_maintainers(db, e.team_id)
    else:
        first = [e.manager_user_id]
    pool = [*first, e.manager_user_id, *(e.backup_user_ids or []), *_team_maintainers(db, e.team_id)]
    seen, order = set(), []
    for uid in pool:
        if uid and uid not in seen and uid != exclude:
            seen.add(uid)
            order.append(uid)
    return (order[0] if order else None), (order[1] if len(order) > 1 else None)


def can_decide(db: Session, acc: Access, r: HumanRequest, e: Employee) -> bool:
    if acc.p.is_admin:
        return True
    uid = acc.p.user_id
    if not uid:
        return False
    allowed = {r.assigned_user_id, r.fallback_user_id, e.manager_user_id, *(e.backup_user_ids or []),
               *_team_maintainers(db, e.team_id)}
    return uid in allowed


# ------------------------------------------------------------------ gate (chamado pelo runtime do agente)
def gate(db: Session, agent: Agent, task_id: int | None, tool: str, ref: str, args: dict, rationale: str = "",
         kind: str = "tool") -> dict:
    """allow | wait (pedido de decisão aberto) | deny. kind='ask_human': o agente pergunta algo ao gestor; 'plan': o
    plano da tarefa (sem alçada; pode exigir aprovação); 'handoff': repasse a um colega (alçada `delegate`).
    Com DLP ligado, os argumentos chegam mascarados ([CPF_1]); a decisão e a execução usam os valores reais, e o
    resultado que volta ao modelo vai mascarado de novo."""
    from . import governance
    real = governance.unmask(db, task_id, args or {})
    out = _gate_any(db, agent, task_id, tool, ref, real, rationale, kind)
    task = db.get(EmployeeTask, task_id) if task_id else None
    if task is not None and isinstance(out.get("result"), str):
        out["result"] = governance.mask(db, task, out["result"])
        db.commit()
    if real != (args or {}) and out.get("status") == "allowed" and "result" not in out:
        out["args"] = real  # o runtime executa a ferramenta dele com os valores reais
    return out


def _gate_any(db: Session, agent: Agent, task_id: int | None, tool: str, ref: str, args: dict, rationale: str = "",
              kind: str = "tool") -> dict:
    if kind == "platform":  # ferramentas da plataforma (análise, extração, calculadoras…): rodam na central
        from . import platform_tools
        return platform_tools.run(db, agent, task_id, tool, args, rationale)
    if kind == "files":  # arquivos da própria tarefa: a plataforma executa, sem alçada (nada sai dela)
        from . import task_files
        e = db.scalar(select(Employee).where(Employee.agent_id == agent.id))
        task = db.get(EmployeeTask, task_id) if task_id else None
        if e is None or task is None or task.employee_id != e.id:
            return {"status": "denied", "message": "arquivos só dentro de uma tarefa deste Digital employee"}
        return task_files.agent_tool(db, e, task, tool, args)
    if kind in ("plan", "handoff"):
        from . import employee_teamwork as teamwork
        e = db.scalar(select(Employee).where(Employee.agent_id == agent.id))
        task = db.get(EmployeeTask, task_id) if task_id else None
        if e is None or task is None or task.employee_id != e.id:
            return {"status": "denied", "message": "plano e repasse só dentro de uma tarefa deste Digital employee"}
        if kind == "plan":
            return teamwork.update_plan(db, e, task, args)
        bad = teamwork.check_handoff(db, e, task, args)
        if bad:
            return bad
        out = _gate(db, agent, task_id, tool, ref, args, rationale, "tool")
        if out.get("status") != "allowed":
            return out
        return {**out, **teamwork.handoff(db, e, task, args)}
    return _gate(db, agent, task_id, tool, ref, args, rationale, kind)


def _plugin_policy(db: Session, agent: Agent, ref: str) -> str:
    """Política da ação na instalação do plugin (mcp:plugin-<nome>:<ferramenta>): off | ask | allow."""
    parts = (ref or "").split(":")
    if len(parts) != 3 or parts[0] != "mcp" or not parts[1].startswith("plugin-"):
        return "allow"
    from . import plugins
    try:
        _, i = plugins.install_for(db, agent, parts[1][len("plugin-"):])
    except PlatformError:
        return "allow"
    return plugins.policy_of(i, parts[2])


def _gate(db: Session, agent: Agent, task_id: int | None, tool: str, ref: str, args: dict, rationale: str,
          kind: str) -> dict:
    e = db.scalar(select(Employee).where(Employee.agent_id == agent.id))
    if e is None:
        return {"status": "allowed"}
    if e.status in ("paused", "offboarded"):
        return {"status": "denied", "message": f"Digital employee {e.status}: nenhuma ação é executada."}
    task = db.get(EmployeeTask, task_id) if task_id else None
    if task is not None and task.employee_id != e.id:
        return {"status": "denied", "message": "tarefa de outro Digital employee"}
    if kind == "ask_human":
        if task is None:
            return {"status": "denied", "message": "perguntas ao gestor só dentro de uma tarefa"}
        q = str(args.get("question") or "").strip()[:4000]
        assigned, fallback = _route(db, e, "manager")
        r = HumanRequest(employee_id=e.id, task_id=task.id, kind="question", question=q, rationale=rationale[:4000],
                         assigned_user_id=assigned, fallback_user_id=fallback, action_payload={"options": args.get("options")},
                         expires_at=now() + timedelta(minutes=config.DECISION_EXPIRES_MIN), approvals=[])
        db.add(r)
        db.flush()
        task.status = "waiting_human"
        event(db, task, "human_request", {"id": r.id, "kind": "question", "question": q[:300]})
        db.commit()
        _notify_request(db, e, r)
        return {"status": "waiting", "request_id": r.id,
                "message": f"Pergunta #{r.id} enviada ao gestor; a tarefa continua quando ele responder."}

    if kind == "capability":  # falta uma ferramenta/especialista: o gestor decide (nunca o agente se dá poderes)
        if task is None:
            return {"status": "denied", "message": "pedidos de capacidade só dentro de uma tarefa"}
        from .employee_work import capability_options
        need = str(args.get("need") or "").strip()[:2000]
        why = str(args.get("why") or rationale or "").strip()[:4000]
        if not need:
            return {"status": "denied", "message": "diga o que falta (need)"}
        assigned, fallback = _route(db, e, "manager")
        r = HumanRequest(employee_id=e.id, task_id=task.id, kind="capability", question=need, rationale=why,
                         assigned_user_id=assigned, fallback_user_id=fallback,
                         action_payload={"need": need, "why": why, "options": capability_options(db, e, need)},
                         expires_at=now() + timedelta(minutes=config.DECISION_EXPIRES_MIN), approvals=[])
        db.add(r)
        db.flush()
        task.status = "waiting_human"
        event(db, task, "human_request", {"id": r.id, "kind": "capability", "need": need[:300]})
        db.commit()
        _notify_request(db, e, r)
        return {"status": "waiting", "request_id": r.id,
                "message": f"Pedido de capacidade #{r.id} enviado ao gestor; a tarefa continua quando ele decidir."}

    cat = gatemod.classify(db, ref)
    if task is not None and (task.mode or "work") == "copilot" and cat.action_type != "read":
        event(db, task, "gate", {"tool": tool, "ref": ref, "action_type": cat.action_type, "mode": "copilot"})
        db.commit()
        return {"status": "denied", "action_type": cat.action_type,
                "message": f"MODO PAR: '{tool}' muda algo ({cat.action_type}) e quem faz é a pessoa. Descreva o que ela deve "
                           "fazer e por quê."}
    ev = gatemod.evaluate(db, e, cat.action_type, args)
    mode = ev["mode"]
    pol = _plugin_policy(db, agent, ref)  # o mantenedor desligou a ação ou exige aprovação a cada uso
    if pol == "off":
        mode, ev = "never", {**ev, "source": "plugin: ação desligada"}
    elif pol == "ask" and mode in ("auto", "notify"):
        mode, ev = "approve", {**ev, "source": "plugin: pede aprovação a cada uso"}
    info = {"action_type": cat.action_type, "mode": mode, "source": ev["source"]}
    if task is not None:
        event(db, task, "gate", {"tool": tool, "ref": ref, **info})
    if mode == "never":
        db.commit()
        return {"status": "denied", **info, "message": f"A alçada não permite '{cat.action_type}' ({tool}): não execute."}
    if e.shadow and cat.action_type not in SHADOW_REAL:
        assigned, _ = _route(db, e, "manager")
        r = HumanRequest(employee_id=e.id, task_id=task.id if task else None, kind="shadow", action_type=cat.action_type,
                         mode=mode, tool_ref=ref, tool_name=tool, action_payload=args, payload_hash=gatemod.payload_hash(ref, args),
                         rationale=rationale[:4000], risk=cat.risk, reversible=cat.reversible, assigned_user_id=assigned,
                         approvals=[])
        db.add(r)
        db.flush()
        if task is not None:
            event(db, task, "shadow", {"id": r.id, "tool": tool, "action_type": cat.action_type, "would_be": mode})
        db.commit()
        return {"status": "simulated", **info, "request_id": r.id,
                "message": f"[MODO SOMBRA] '{tool}' NÃO foi executada: este Digital employee está em modo sombra e as "
                           f"ações que mudam algo são só registradas para o gestor revisar ({cat.action_type}; na vida real: "
                           f"{mode}). Continue a tarefa como se a ação tivesse dado certo e diga no resultado o que você fez."}
    if mode in ("auto", "notify"):
        if mode == "notify":
            assigned, _ = _route(db, e, "manager")
            db.add(HumanRequest(employee_id=e.id, task_id=task.id if task else None, kind="notice",
                                action_type=cat.action_type, mode=mode, tool_ref=ref, tool_name=tool,
                                action_payload=args, rationale=rationale[:4000], risk=cat.risk,
                                assigned_user_id=assigned, approvals=[]))
        db.commit()
        return {"status": "allowed", **info}
    # approve / approve_2: só dentro de uma tarefa, e só a ação exata que uma pessoa liberou
    if task is None:
        db.commit()
        return {"status": "denied", **info,
                "message": f"'{cat.action_type}' exige aprovação: peça como tarefa (assign_task) para o gestor decidir."}
    h = gatemod.payload_hash(ref, args)
    granted = db.scalar(select(HumanRequest).where(HumanRequest.task_id == task.id, HumanRequest.grant_hash == h,
                                                   HumanRequest.grant_used.is_(False), HumanRequest.status == "decided"))
    if granted is not None:
        granted.grant_used = True
        event(db, task, "tool", {"tool": tool, "approved_by": granted.decided_by, "request": granted.id})
        db.commit()
        return {"status": "allowed", **info, "request_id": granted.id}
    pending = db.scalar(select(HumanRequest).where(HumanRequest.task_id == task.id, HumanRequest.payload_hash == h,
                                                   HumanRequest.status == "open"))
    if pending is None:
        exclude = task.requester_user_id if ev["separation"] else None
        assigned, fallback = _route(db, e, ev["approver"], exclude)
        minutes = ev["expires_in_min"] or config.DECISION_EXPIRES_MIN
        pending = HumanRequest(employee_id=e.id, task_id=task.id, kind="approval", action_type=cat.action_type, mode=mode,
                               tool_ref=ref, tool_name=tool, action_payload=args, payload_hash=h,
                               rationale=rationale[:4000], risk=cat.risk, reversible=cat.reversible,
                               assigned_user_id=assigned, fallback_user_id=fallback,
                               expires_at=now() + timedelta(minutes=minutes), approvals=[])
        db.add(pending)
        db.flush()
        event(db, task, "human_request", {"id": pending.id, "kind": "approval", "tool": tool, **info})
        db.commit()
        _notify_request(db, e, pending)
    task.status = "waiting_human"
    db.commit()
    who = "duas pessoas" if mode == "approve_2" else "uma pessoa"
    return {"status": "waiting", "request_id": pending.id, **info,
            "message": f"'{tool}' ({cat.action_type}) precisa da aprovação de {who}: pedido #{pending.id}."}


# ------------------------------------------------------------------ decisões
def request_dict(db: Session, r: HumanRequest, e: Employee | None = None) -> dict:
    e = e or db.get(Employee, r.employee_id)
    a = db.get(Agent, e.agent_id) if e else None
    task = db.get(EmployeeTask, r.task_id) if r.task_id else None
    names = {u.id: (u.name or u.email) for u in db.scalars(select(User).where(
        User.id.in_([x for x in (r.assigned_user_id, r.fallback_user_id) if x])))}
    return {"id": r.id, "kind": r.kind, "employee": a.slug if a else None, "employee_title": e.title if e else "",
            "employee_name": a.name if a else "", "task": {"id": task.id, "title": task.title, "requester": task.requester}
            if task else None, "action_type": r.action_type, "mode": r.mode, "tool": r.tool_name, "tool_ref": r.tool_ref,
            "payload": r.action_payload, "rationale": r.rationale, "risk": r.risk, "reversible": r.reversible,
            "question": r.question, "assigned_to": names.get(r.assigned_user_id), "fallback_to": names.get(r.fallback_user_id),
            "escalated": r.escalated, "expires_at": iso(_aware(r.expires_at)) if r.expires_at else None, "status": r.status,
            "approvals": r.approvals or [], "decision": r.decision, "edited_payload": r.edited_payload, "reason": r.reason,
            "decided_by": r.decided_by, "decided_at": iso(_aware(r.decided_at)) if r.decided_at else None,
            "created_at": iso(_aware(r.created_at))}


def _decide_capability(db: Session, acc: Access, r: HumanRequest, e: Employee, task, decision: str, edit: dict,
                       reason: str, who: str) -> dict:
    """attach (edit={"specialist": slug}) | build (devolve o pedido pronto; segue aberto) | reject | instruct."""
    from . import employee_work as workmod
    if decision not in ("attach", "build", "reject", "instruct"):
        raise PlatformError("capacidade: decision = attach (edit={'specialist': slug}) | build | reject | instruct")
    slug = _slug(db, e)
    if decision == "build":
        prompt = workmod.build_prompt(e, slug, r.question, lang_of(e) == "en")
        r.action_payload = {**(r.action_payload or {}), "build_requested_by": who}
        r.expires_at = now() + timedelta(days=7)  # construir um agente leva mais que o prazo de uma decisão
        db.commit()
        if task:
            event(db, task, "note", {"capability": r.id, "build": who})
            db.commit()
        audit(db, who, "employee.capability.build", slug, f"#{r.id}")
        return {**request_dict(db, r, e), "build_prompt": prompt}
    if decision in ("reject", "instruct") and not reason and decision == "instruct":
        raise PlatformError("escreva a instrução em reason")
    if decision == "attach":
        target = str(edit.get("specialist") or "").strip()
        if not target:
            raise PlatformError("diga qual agente anexar: edit={'specialist': '<slug>'}")
        from .employees import find
        find(db, acc, slug, "manage")
        outcome = workmod.attach_specialist(db, acc, e, target)
        r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", "attach", reason, who, now()
        r.edited_payload = {"specialist": target}
        r.action_payload = {**(r.action_payload or {}), "pending": outcome == "approval_pending"}
        db.commit()
        if task:
            event(db, task, "decision", {"id": r.id, "decision": "attach", "specialist": target, "by": who})
            db.commit()
        if outcome != "approval_pending":
            workmod.resume_after_attach(db, e, r)
        audit(db, who, "employee.decision.attach", slug, f"#{r.id} {target} {outcome}")
        out = request_dict(db, r, e)
        out["attach"] = outcome
        if outcome == "approval_pending":
            out["message"] = ("Anexado numa versão nova. O time exige a aprovação de outra pessoa para produção: a tarefa "
                              "continua quando o pedido em Aprovações for aprovado.")
        return out
    r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", decision, reason, who, now()
    if task:
        event(db, task, "decision", {"id": r.id, "decision": decision, "by": who})
        if decision == "reject":
            _resume(db, task, f"CAPACIDADE #{r.id}: RECUSADO por {who}" + (f" ({reason})" if reason else "") +
                    ". NÃO tente contornar. Conclua o que for possível sem ela e explique o que ficou faltando.")
        else:
            _resume(db, task, f"INSTRUÇÃO de {who} sobre a capacidade #{r.id}: {reason}")
    db.commit()
    audit(db, who, f"employee.decision.{decision}", slug, f"#{r.id}")
    return request_dict(db, r, e)


def pending_for(db: Session, acc: Access) -> list[dict]:
    """Decisões abertas que esta pessoa pode tomar (as atribuídas a ela primeiro)."""
    rows = db.scalars(select(HumanRequest).where(HumanRequest.status == "open").order_by(HumanRequest.created_at)).all()
    out = []
    for r in rows:
        if r.kind == "shadow":
            continue  # revisados na aba Sombra do funcionário
        e = db.get(Employee, r.employee_id)
        if e and can_decide(db, acc, r, e):
            d = request_dict(db, r, e)
            d["mine"] = r.assigned_user_id == acc.p.user_id
            out.append(d)
    return sorted(out, key=lambda d: (not d["mine"], d["kind"] == "notice", d["created_at"]))


def _resume(db: Session, task: EmployeeTask, message: str):
    """Grava a decisão no checkpoint e recoloca a tarefa na fila (se não houver outra decisão bloqueando)."""
    cp = last_checkpoint(db, task)
    checkpoint(db, task, [*(cp.messages if cp else []), {"role": "user", "content": message}])
    blocking = db.scalar(select(HumanRequest.id).where(HumanRequest.task_id == task.id, HumanRequest.status == "open",
                                                       HumanRequest.kind.in_(BLOCKING)))
    if task.status == "waiting_human" and not blocking:
        task.status, task.not_before = "new", None


def _call_text(r: HumanRequest, payload: dict) -> str:
    return f"{r.tool_name}({json.dumps(payload, ensure_ascii=False, sort_keys=True)})"


def decide(db: Session, acc: Access, request_id: int, decision: str, edit: dict | None = None,
           reason: str = "") -> dict:
    out = _decide(db, acc, request_id, decision, edit, reason)
    r = db.get(HumanRequest, request_id)
    if r is not None and r.chat_refs:
        from . import notify
        notify.request_changed(r)  # a mensagem no Slack mostra o desfecho e perde os botões
    return out


def _decide(db: Session, acc: Access, request_id: int, decision: str, edit: dict | None, reason: str) -> dict:
    r = db.get(HumanRequest, request_id)
    if r is None:
        raise PlatformError(f"pedido #{request_id} não existe")
    e = db.get(Employee, r.employee_id)
    if not can_decide(db, acc, r, e):
        raise Forbidden("você não pode decidir este pedido")
    if r.status != "open":
        raise PlatformError(f"pedido #{r.id} já está {r.status}")
    task = db.get(EmployeeTask, r.task_id) if r.task_id else None
    who = acc.p.name
    reason = (reason or "").strip()[:4000]

    if r.kind == "notice":
        r.status, r.decision, r.decided_by, r.decided_at = "decided", "ack", who, now()
        db.commit()
        return request_dict(db, r, e)

    if r.kind == "question":
        if decision not in ("answer", "instruct") or not reason:
            raise PlatformError("responda a pergunta (decision='answer' e o texto em reason)")
        r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", "answer", reason, who, now()
        if task:
            event(db, task, "decision", {"id": r.id, "decision": "answer", "by": who})
            _resume(db, task, f"RESPOSTA de {who} à sua pergunta #{r.id}: {reason}\nContinue a tarefa com essa informação.")
        audit(db, who, "employee.decision.answer", _slug(db, e), f"#{r.id}")
        return request_dict(db, r, e)

    if r.kind == "capability":
        return _decide_capability(db, acc, r, e, task, decision, edit or {}, reason, who)

    if r.kind == "review":  # o resultado antes de concluir: aprovar ou pedir mudanças
        from . import task_flow
        task_flow.decide_review(db, r, e, task, decision, reason, who)
        audit(db, who, f"employee.review.{r.decision}", _slug(db, e), f"#{r.id}")
        return request_dict(db, r, e)

    if r.kind == "plan":  # plano de uma tarefa longa: aprovar (ou editar) antes da execução
        from . import employee_teamwork as teamwork
        teamwork.decide_plan(db, r, e, task, decision, edit or {}, reason, who)
        db.commit()
        audit(db, who, f"employee.plan.{r.decision}", _slug(db, e), f"#{r.id}")
        return request_dict(db, r, e)

    if r.kind == "shadow":  # revisão do modo sombra: nada a executar, só o veredito (alimenta carreira e lições)
        if decision not in ("agree", "disagree"):
            raise PlatformError("modo sombra: decision = agree (teria aprovado) | disagree (não teria; diga por quê)")
        if decision == "disagree" and not reason:
            raise PlatformError("diga por que você não teria aprovado (reason): vira lição")
        r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", decision, reason, who, now()
        if task:
            event(db, task, "decision", {"id": r.id, "decision": decision, "by": who, "shadow": True})
        db.commit()
        audit(db, who, f"employee.shadow.{decision}", _slug(db, e), f"#{r.id}")
        return request_dict(db, r, e)

    if r.kind == "admission":
        if decision not in ("approve", "reject"):
            raise PlatformError("admissão: decision = approve | reject")
        from .employees import admit
        outcome = admit(db, acc, e, decision == "approve", reason)  # se o ship falhar, o pedido continua aberto
        r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", decision, reason, who, now()
        for dup in db.scalars(select(HumanRequest).where(HumanRequest.employee_id == e.id, HumanRequest.kind == "admission",
                                                         HumanRequest.status == "open", HumanRequest.id != r.id)):
            dup.status, dup.decided_by, dup.decided_at = "cancelled", who, now()
        db.commit()
        out = request_dict(db, r, e)
        out["employee_status"] = outcome
        if outcome == "approval_pending":
            out["message"] = ("Admitido. O time exige a aprovação de outra pessoa para produção: o pedido está em "
                              "Aprovações e ele fica ativo quando for aprovado.")
        return out

    from . import decision_kinds, platform_tools
    platform_tools._load()  # os módulos que trazem tipos de pedido (campos extraídos, alteração de documento…)
    if r.kind in decision_kinds.HANDLERS:
        out = decision_kinds.HANDLERS[r.kind](db, acc, r, e, task, decision, edit or {}, reason, who)
        audit(db, who, f"employee.{r.kind}.{r.decision or decision}", _slug(db, e), f"#{r.id}")
        return out

    # aprovação de uma ação exata
    if decision not in ("approve", "approve_edited", "reject", "instruct"):
        raise PlatformError("decision = approve | approve_edited | reject | instruct")
    sep = gatemod.floor_of(db).get(r.action_type)
    if task is not None and acc.p.user_id and acc.p.user_id == task.requester_user_id and sep and sep.separation \
            and decision in ("approve", "approve_edited"):
        raise Forbidden("separação de funções: quem pediu a tarefa não aprova esta ação")
    if decision in ("approve", "approve_edited"):
        payload = r.action_payload
        if decision == "approve_edited":
            if not isinstance(edit, dict) or not edit:
                raise PlatformError("approve_edited: mande os argumentos editados em `edit`")
            payload = edit
            r.edited_payload = edit
            r.approvals = []  # a ação mudou: as aprovações anteriores eram de outra versão
        approvals = [a for a in (r.approvals or [])]
        if any(a.get("user_id") == acc.p.user_id and acc.p.user_id for a in approvals):
            raise PlatformError("você já aprovou; falta outra pessoa (approve_2)")
        approvals.append({"user_id": acc.p.user_id, "by": who, "at": now().isoformat()})
        r.approvals = approvals
        if r.mode == "approve_2" and len(approvals) < 2:
            if task:
                event(db, task, "decision", {"id": r.id, "decision": "first_approval", "by": who})
            db.commit()
            audit(db, who, "employee.decision.approve_1of2", _slug(db, e), f"#{r.id}")
            return request_dict(db, r, e)
        r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", decision, reason, \
            ", ".join(a["by"] for a in approvals), now()
        r.grant_hash = gatemod.payload_hash(r.tool_ref, payload)
        msg = (f"DECISÃO #{r.id}: APROVADO por {r.decided_by}"
               + (f" (com edição; motivo: {reason})" if decision == "approve_edited" else (f" — {reason}" if reason else ""))
               + ". Execute agora, uma única vez, exatamente esta chamada:\n"
               + f"use {r.tool_name} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n"
               + "Depois continue a tarefa.")
    elif decision == "reject":
        r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", "reject", reason, who, now()
        msg = (f"DECISÃO #{r.id}: RECUSADO por {who}" + (f" — motivo: {reason}" if reason else "")
               + f". NÃO execute {_call_text(r, r.action_payload)}. Continue sem essa ação se for possível, ou encerre a "
                 "tarefa explicando o que ficou pendente.")
    else:
        if not reason:
            raise PlatformError("instruct: escreva a instrução em reason")
        r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", "instruct", reason, who, now()
        msg = (f"DECISÃO #{r.id}: INSTRUÇÃO de {who}: {reason}\nNÃO execute a ação pedida como estava; siga a instrução.")
    if task:
        event(db, task, "decision", {"id": r.id, "decision": r.decision, "by": who})
        _resume(db, task, msg)
    db.commit()
    audit(db, who, f"employee.decision.{r.decision}", _slug(db, e), f"#{r.id} {r.tool_name} {r.action_type}")
    return request_dict(db, r, e)


def _slug(db: Session, e: Employee) -> str:
    a = db.get(Agent, e.agent_id)
    return a.slug if a else str(e.id)


# ------------------------------------------------------------------ prazos
def sweep(db: Session) -> dict:
    """Decisões vencidas: escala para o substituto uma vez; depois expira em "não fazer". Tarefas presas: voltam."""
    out = {"escalated": 0, "expired": 0, "requeued": 0, "failed": 0}
    t = now()
    for r in db.scalars(select(HumanRequest).where(HumanRequest.status == "open",
                                                   HumanRequest.kind.in_(BLOCKING),
                                                   HumanRequest.expires_at.is_not(None))).all():
        if _aware(r.expires_at) > t:
            continue
        e = db.get(Employee, r.employee_id)
        task = db.get(EmployeeTask, r.task_id) if r.task_id else None
        if not r.escalated and r.fallback_user_id and r.fallback_user_id != r.assigned_user_id:
            span = (_aware(r.expires_at) - _aware(r.created_at)) if r.created_at else timedelta(minutes=config.DECISION_EXPIRES_MIN)
            r.assigned_user_id, r.escalated, r.reminded = r.fallback_user_id, True, False
            r.expires_at = t + max(span, timedelta(minutes=5))
            if task:
                event(db, task, "human_request", {"id": r.id, "escalated": True})
            db.commit()
            _notify_request(db, e, r)
            out["escalated"] += 1
            continue
        r.status, r.decided_at, r.decided_by = "expired", t, "prazo"
        if task:
            event(db, task, "decision", {"id": r.id, "decision": "expired"})
            what = _call_text(r, r.action_payload) if r.kind == "approval" else f"o pedido #{r.id}"
            _resume(db, task, f"DECISÃO #{r.id}: EXPIROU sem resposta. NÃO execute {what}. Encerre a tarefa explicando o "
                              "que ficou pendente e por quê.")
        db.commit()
        audit(db, "prazo", "employee.decision.expired", _slug(db, e) if e else "", f"#{r.id}")
        if r.chat_refs:
            from . import notify
            notify.request_changed(r)
        out["expired"] += 1
    for task in db.scalars(select(EmployeeTask).where(EmployeeTask.status == "in_progress")).all():
        e = db.get(Employee, task.employee_id)
        limit = timedelta(minutes=(e.task_time_limit_min if e else 30) + 5)
        if task.started_at and t - _aware(task.started_at) > limit:
            task.attempts += 1
            if task.attempts >= 3:
                task.status, task.error, task.finished_at = "failed", "tempo esgotado em 3 tentativas", t
                out["failed"] += 1
            else:
                task.status, task.not_before = "new", retry_at(task.attempts)
                out["requeued"] += 1
            event(db, task, "error", {"error": "rodada sem resposta: volta para a fila", "attempt": task.attempts})
            db.commit()
    return out


# ------------------------------------------------------------------ executor
_last_budget_sweep = 0.0
_last_retention = 0.0


def tick() -> list[int]:
    """Reserva tarefas na fila (de Digital employees em experiência ou ativos) para esta réplica executar."""
    claimed = []
    global _last_budget_sweep
    budgets_now = time.monotonic() - _last_budget_sweep > 60  # avisos e paradas de orçamento, mesmo sem chamada nova
    if budgets_now:
        _last_budget_sweep = time.monotonic()
    global _last_retention
    retention_now = time.monotonic() - _last_retention > 3600
    if retention_now:
        _last_retention = time.monotonic()
    for org_id in tenancy.active_orgs():  # cada empresa com a própria sessão e o próprio contexto (Slack, fuso, avisos)
        with tenancy.using(org_id), SessionLocal() as db:
            sweep(db)
            from . import notify
            notify.sweep(db)  # alerta antes de expirar, resumo diário e prazos de tarefa
            from .employee_work import fire_routines
            fire_routines(db)
            from .employee_teamwork import poll_inboxes
            poll_inboxes(db)  # caixa de e-mail como canal de entrada
            if budgets_now:
                from .budgets import sweep as budget_sweep
                budget_sweep(db)
            from .employees import daily_reports
            daily_reports(db)
            from . import correspondence, meetings, playbooks, work_queues
            work_queues.sweep(db)  # SLA estourado
            playbooks.sweep(db)  # execuções agendadas (@bd 1 08:00…)
            correspondence.sweep(db)  # lembretes sem resposta e promessas vencidas
            meetings.sweep(db)  # briefings antes das reuniões
            if retention_now:
                from . import governance, knowledge
                governance.retention_sweep(db)  # conteúdo vencido pela política de retenção
                knowledge.sweep(db)  # fontes da base de conhecimento (uma por vez, uma vez por dia)
    with SessionLocal() as db:  # a fila é uma só: reserva entre todas as empresas
        rows = db.execute(select(EmployeeTask.id, EmployeeTask.probation, Employee.status)
                          .join(Employee, Employee.id == EmployeeTask.employee_id)
                          .where(EmployeeTask.status == "new", Employee.status.in_(("probation", "active")),
                                 Employee.runtime != "external",  # o worker externo busca as dele pelo heartbeat
                                 or_(EmployeeTask.not_before.is_(None), EmployeeTask.not_before <= now()))
                          .order_by(EmployeeTask.priority, EmployeeTask.created_at).limit(config.EMPLOYEE_WORKERS * 2)).all()
        for tid, probation, est in rows:
            if est == "active" and probation:
                continue  # experiência já encerrada
            res = db.execute(update(EmployeeTask).where(EmployeeTask.id == tid, EmployeeTask.status == "new")
                             .values(status="in_progress", worker=WORKER, started_at=now(), updated_at=now()))
            db.commit()
            if res.rowcount == 1:
                claimed.append(tid)
    return claimed


def run_task(task_id: int) -> EmployeeTask | None:
    """Uma rodada da tarefa, dentro da empresa dela."""
    with tenancy.using(tenancy.org_of(EmployeeTask, task_id)):
        return _run_task(task_id)


def _run_task(task_id: int) -> EmployeeTask | None:
    """Uma rodada da tarefa: chama o agente a partir do último checkpoint."""
    with SessionLocal() as db:
        task = db.get(EmployeeTask, task_id)
        if task is None or task.status != "in_progress":
            return task
        e = db.get(Employee, task.employee_id)
        a = db.get(Agent, e.agent_id)
        env = "stage" if (task.probation or e.status == "probation") else "prod"
        if task.runs >= config.EMPLOYEE_MAX_RUNS + (task.extra_runs or 0):
            return _finish(db, task, "failed", error=f"limite de {config.EMPLOYEE_MAX_RUNS + (task.extra_runs or 0)} rodadas atingido")
        if task.cost_usd > e.task_budget_usd:
            return _finish(db, task, "failed", error=f"orçamento da tarefa (US$ {e.task_budget_usd}) estourado")
        from . import budgets
        stop = budgets.stopped(db, a, task.project_id)
        if stop:  # orçamento do escopo parado: espera alguém liberar, sem perder o checkpoint
            task.status, task.worker = "waiting_budget", ""
            event(db, task, "budget", stop)
            db.commit()
            return task
        cp = last_checkpoint(db, task)
        messages = list(cp.messages if cp else [{"role": "user", "content": task_prompt(task, lang_of(e))}])
        from . import task_thread
        delivered = task_thread.deliver(db, task, messages, lang_of(e))  # o que chegou na conversa da tarefa
        if len(delivered) != len(messages):  # já no checkpoint: uma rodada que falhe não perde as mensagens
            messages = delivered
            checkpoint(db, task, messages)
        from . import governance
        why = governance.llm_blocked(db, task, a)
        if why:  # dado mais sensível do que a conexão de LLM pode receber
            return _finish(db, task, "failed", error=why)
        messages = governance.mask_messages(db, task, messages)  # DLP: o modelo vê [CPF_1], não o CPF
        task.runs += 1
        event(db, task, "run", {"run": task.runs, "env": env})
        slug, limit_min = a.slug, e.task_time_limit_min
        db.commit()
    t0 = now()
    data, err = None, ""
    try:
        with HTTP() as http:
            resp = http.post(deploy.internal_url(slug, env) + "/v1/chat/completions",
                             headers={"X-Channel": "employee", "X-Hangar-Task": str(task_id),
                                      "X-Session-Id": f"task-{task_id}"},
                             json={"messages": messages}, timeout=max(60, limit_min * 60))
        resp.raise_for_status()
        data = resp.json()
    except Exception as ex:  # noqa: BLE001 — a rodada falha, a tarefa volta para a fila (com limite de tentativas)
        err = str(ex)[:500]
    with SessionLocal() as db:
        task = db.get(EmployeeTask, task_id)
        a = db.get(Agent, db.get(Employee, task.employee_id).agent_id)
        record_usage(db, a.id, env, "employee", "openai", int((now() - t0).total_seconds() * 1000), data is not None, data)
        if data is None:
            task.attempts += 1
            event(db, task, "error", {"error": err, "attempt": task.attempts})
            if task.attempts >= 3:
                return _finish(db, task, "failed", error=err)
            task.status, task.not_before = "new", retry_at(task.attempts)
            event(db, task, "note", {"retry_at": iso(task.not_before)})
            db.commit()
            return task
        usage = data.get("usage") or {}
        task.cost_usd = round(task.cost_usd + float(usage.get("cost_usd") or 0), 6)
        task.tokens += int(usage.get("total_tokens") or 0)
        for step in data.get("x_trace") or []:
            if step.get("tool"):
                event(db, task, "tool", {"tool": step["tool"], "args": step.get("args"), "result": str(step.get("result"))[:300]})
        content = (data["choices"][0]["message"].get("content") or "").strip()
        # jobs de código (agente com harness) ficam na linha do tempo, venham do rastro ou só da resposta
        seen = {int(j) for j in task_jobs(db, task)}
        for jid in dict.fromkeys(JOB_MARK.findall(content + " ".join(str(s.get("result")) for s in data.get("x_trace") or []))):
            if int(jid) not in seen:
                event(db, task, "job", {"job": int(jid)})
        m = WAIT_MARK.match(content)
        note = WAIT_MARK.sub("", content)
        messages = messages + ([{"role": "assistant", "content": note}] if note else [])
        checkpoint(db, task, messages)
        from . import task_thread
        task_thread.agent_said(db, task, note)
        if m:
            open_req = db.scalar(select(HumanRequest.id).where(HumanRequest.task_id == task.id, HumanRequest.status == "open",
                                                               HumanRequest.kind.in_(BLOCKING)))
            child = db.get(EmployeeTask, task.waiting_on) if task.waiting_on else None
            from . import correspondence
            task.status = ("waiting_human" if open_req else
                           "waiting_task" if child is not None and child.status not in TERMINAL else
                           "waiting_external" if correspondence.waiting(db, task) else
                           "new")  # decidido (ou a repassada terminou) enquanto a rodada terminava: segue
            db.commit()
            return task
        from . import correspondence
        if correspondence.waiting(db, task) and not task_thread.pending(db, task):  # espera a resposta de fora
            task.status = "waiting_external"
            event(db, task, "note", {"waiting": "resposta de fora"})
            db.commit()
            return task
        if task_thread.pending(db, task):  # chegou mensagem durante a rodada: mais uma rodada antes de encerrar
            task.status = "new"
            event(db, task, "note", {"continue": "mensagem nova na conversa"})
            db.commit()
            return task
        from . import task_flow
        e = db.get(Employee, task.employee_id)
        if task_flow.needs_review(task, e):  # o resultado passa pelo revisor antes de concluir
            return task_flow.send_to_review(db, task, e, content)
        return _finish(db, task, "done", result=content)


def _finish(db: Session, task: EmployeeTask, status: str, result: str = "", error: str = "") -> EmployeeTask:
    from . import governance
    result, error = governance.unmask(db, task.id, result), governance.unmask(db, task.id, error)  # as pessoas veem o real
    task.status, task.finished_at = status, now()
    if status == "done" and task.due_at and _aware(task.finished_at) > _aware(task.due_at):
        event(db, task, "due", {"stage": "late", "due_at": iso(_aware(task.due_at))})
    if result:
        task.result = result[:20000]
    if error:
        task.error = error
    event(db, task, "done" if status == "done" else "error", {"status": status, "error": error[:300]})
    db.commit()
    from . import employee_teamwork as teamwork
    teamwork.child_finished(db, task)  # repassada por outra tarefa: a de origem retoma com o resultado
    teamwork.reply_by_email(db, task)  # veio por e-mail de alguém da empresa: a resposta volta por e-mail
    from . import chat_tasks
    chat_tasks.task_finished(db, task)  # veio do Slack ou do Teams: o resultado volta para lá
    from . import employee_quality
    employee_quality.task_finished(db, task)  # a amostra vai para o juiz de qualidade
    from . import task_flow
    task_flow.task_finished(db, task)  # as tarefas que dependiam desta andam (ou travam, se falhou)
    from . import playbooks, work_queues
    work_queues.task_finished(db, task)  # o item da fila acompanha (ou vai para as pessoas, se falhou)
    playbooks.task_finished(db, task)  # a próxima etapa do processo começa
    if task.probation:
        from .employees import probation_progress
        probation_progress(db, db.get(Employee, task.employee_id))
    return task


def _notify_request(db: Session, e: Employee, r: HumanRequest):
    """Aviso no webhook de relatórios do Digital employee (canal do time, com o link do portal) e, para quem está com
    o pedido, a mensagem pessoal com os botões de decidir (services/notify)."""
    if not e:
        return
    from . import notify
    notify.request_opened(r, "escalated" if r.escalated else "new")
    if not e.report_webhook:
        return
    from .schedules import HTTP as SHTTP
    from .schedules import check_webhook
    slug = _slug(db, e)
    what = r.question if r.kind == "question" else f"{r.tool_name} ({r.action_type})"
    text = (f"*{e.title}* precisa de você — pedido #{r.id}{' (escalado)' if r.escalated else ''}: {what}\n"
            f"{config.PUBLIC_BASE_URL}/app/#/decisions")
    try:
        with SHTTP() as http:
            http.post(check_webhook(e.report_webhook), json={"text": text, "employee": slug, "request_id": r.id,
                                                             "kind": r.kind})
    except Exception:  # noqa: BLE001 — o aviso nunca derruba o fluxo
        pass


def _loop():
    log.info("Digital employees: executor ligado (a cada %ss)", config.SCHEDULER_TICK_S)
    while not _stop.wait(config.SCHEDULER_TICK_S):
        try:
            for tid in tick():
                _pool.submit(run_task, tid)
        except Exception:
            log.exception("falha no executor de Digital employees")


def start():
    if not config.SCHEDULER_ENABLED:
        return
    _stop.clear()
    threading.Thread(target=_loop, name="employees", daemon=True).start()


def stop():
    _stop.set()
