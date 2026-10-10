"""A conversa da tarefa: quem pediu (ou quem gerencia o funcionário) escreve na tarefa, inclusive enquanto ele trabalha.

- Enquanto uma rodada roda, a mensagem fica na fila: dá para editar ou descartar até ela ser entregue. A próxima
  rodada começa entregando a fila, na ordem. Se a rodada terminaria a tarefa e chegou mensagem no meio, a tarefa segue
  para mais uma rodada em vez de encerrar.
- Tarefa concluída ou com falha: a mensagem reabre a tarefa (acompanhamento: "agora faça também…").
- Cada resposta do agente (o que ele diz ao fim de uma rodada) entra na conversa.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Agent, Employee, EmployeeTask, TaskMessage, now
from . import employee_tasks as tasksmod
from .access import Access, Forbidden
from .common import PlatformError, audit, iso

MAX_BODY = 8000
REOPEN = ("done", "failed")


def _task(db: Session, acc: Access, task_id: int, write: bool = False) -> tuple[EmployeeTask, Employee, Agent]:
    from .employees import find
    t = db.get(EmployeeTask, task_id)
    if t is None:
        raise PlatformError(f"tarefa #{task_id} não existe")
    e = db.get(Employee, t.employee_id)
    a = db.get(Agent, e.agent_id)
    from .employees import can_read_task
    if not can_read_task(acc, t, e):
        raise Forbidden(f"tarefa #{task_id} não encontrada ou sem acesso")
    if write and not (acc.p.user_id and acc.p.user_id == t.requester_user_id):
        find(db, acc, a.slug, "assign")  # escrever: quem pediu, ou quem pode entregar tarefas a ele
    return t, e, a


def message_dict(m: TaskMessage) -> dict:
    return {"id": m.id, "author": m.author, "agent": m.status == "agent", "body": m.body, "status": m.status,
            "at": iso(tasksmod._aware(m.created_at)), "delivered_at": iso(tasksmod._aware(m.delivered_at)) if m.delivered_at else None}


def list_messages(db: Session, acc: Access, task_id: int) -> list[dict]:
    t, _, _ = _task(db, acc, task_id)
    return [message_dict(m) for m in db.scalars(select(TaskMessage).where(TaskMessage.task_id == t.id,
                                                                          TaskMessage.status != "discarded")
                                                .order_by(TaskMessage.id))]


def post(db: Session, acc: Access, task_id: int, body: str) -> dict:
    t, e, a = _task(db, acc, task_id, write=True)
    body = (body or "").strip()
    if not body:
        raise PlatformError("escreva a mensagem")
    if len(body) > MAX_BODY:
        raise PlatformError(f"mensagem longa demais (máx. {MAX_BODY} caracteres)")
    if t.status in ("cancelled", "expired"):
        raise PlatformError(f"a tarefa está {t.status}: crie uma nova")
    if t.status in REOPEN and t.probation:
        raise PlatformError("tarefas do período de experiência não reabrem")
    m = TaskMessage(task_id=t.id, author=acc.p.name, user_id=acc.p.user_id, body=body, status="queued", created_at=now())
    db.add(m)
    if t.status in REOPEN:  # acompanhamento: a tarefa volta para a fila e a mensagem vai na próxima rodada
        t.status, t.finished_at, t.error, t.attempts = "new", None, "", 0
        tasksmod.event(db, t, "reopened", {"by": acc.p.name})
    tasksmod.event(db, t, "message", {"by": acc.p.name, "queued": t.status == "in_progress"})
    db.commit()
    audit(db, acc.p.name, "employee.task.message", a.slug, f"#{t.id}")
    return {**message_dict(m), "task_status": t.status}


def _own(db: Session, acc: Access, task_id: int, message_id: int) -> TaskMessage:
    t, _, _ = _task(db, acc, task_id, write=True)
    m = db.get(TaskMessage, message_id)
    if m is None or m.task_id != t.id:
        raise PlatformError("mensagem não encontrada")
    if m.status != "queued":
        raise PlatformError("a mensagem já foi entregue ao agente")
    if m.user_id != acc.p.user_id and not acc.p.is_admin:
        raise Forbidden("só quem escreveu muda a mensagem na fila")
    return m


def edit(db: Session, acc: Access, task_id: int, message_id: int, body: str) -> dict:
    m = _own(db, acc, task_id, message_id)
    if not (body or "").strip():
        raise PlatformError("escreva a mensagem (ou descarte)")
    m.body = body.strip()[:MAX_BODY]
    db.commit()
    return message_dict(m)


def discard(db: Session, acc: Access, task_id: int, message_id: int) -> dict:
    m = _own(db, acc, task_id, message_id)
    m.status = "discarded"
    db.commit()
    return message_dict(m)


# ------------------------------------------------------------------ chamado pelo executor das tarefas
def deliver(db: Session, task: EmployeeTask, messages: list[dict], lang: str) -> list[dict]:
    """Começo de uma rodada: a fila entra na conversa do agente, na ordem."""
    queued = list(db.scalars(select(TaskMessage).where(TaskMessage.task_id == task.id, TaskMessage.status == "queued")
                             .order_by(TaskMessage.id)))
    if not queued:
        return messages
    head = "MESSAGE from" if lang == "en" else "MENSAGEM de"
    for m in queued:
        messages = [*messages, {"role": "user", "content": f"{head} {m.author}:\n{m.body}"}]
        m.status, m.delivered_at = "delivered", now()
    tasksmod.event(db, task, "messages_delivered", {"count": len(queued)})
    return messages


def pending(db: Session, task: EmployeeTask) -> bool:
    return db.scalar(select(TaskMessage.id).where(TaskMessage.task_id == task.id, TaskMessage.status == "queued")) is not None


def agent_said(db: Session, task: EmployeeTask, text: str):
    text = (text or "").strip()
    if text:
        db.add(TaskMessage(task_id=task.id, author="agent", body=text[:20000], status="agent", created_at=now()))
