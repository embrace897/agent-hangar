"""Coordenação e recuperação de tarefas dos Digital employees.

- Dependências: uma tarefa pode depender de outras. Enquanto alguma não terminou, ela fica `blocked`; quando todas
  concluem, ela entra na fila com o resultado de cada uma no prompt. Uma dependência que falha deixa a tarefa travada
  (aparece na caixa de entrada) até alguém continuar a dependência ou removê-la.
- Revisão: com `review_required` (na tarefa) ou `review_policy=manager` (no cargo), o resultado não conclui direto: a
  tarefa vai para `in_review` e o revisor (o gestor, ou quem foi indicado) aprova ou pede mudanças, que voltam para o
  funcionário como mais uma rodada.
- Pausar: segura a tarefa (e as que ela repassou) em `on_hold` até alguém retomar.
- Recuperação: o diagnóstico explica por que a tarefa parou, quem tem a próxima ação e o que fazer. "Continuar" uma
  tarefa com falha concede mais rodadas e retoma do checkpoint; se a última rodada pode ter feito algo fora da
  plataforma (uma ação que não é só leitura), pede confirmação antes, para não repetir às cegas.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Agent, Employee, EmployeeTask, HumanRequest, TaskDependency, TaskEvent, User, now
from . import employee_tasks as tasksmod
from .access import Access
from .common import PlatformError, audit, iso

HOLDABLE = ("new", "blocked", "waiting_task", "waiting_budget")
EXTRA_RUNS = 3


def _task(db: Session, acc: Access, task_id: int, write: bool = True) -> tuple[EmployeeTask, Employee, Agent]:
    from .task_thread import _task as thread_task
    return thread_task(db, acc, task_id, write=write)


# ------------------------------------------------------------------ dependências
def _ancestors(db: Session, task_id: int, seen: set | None = None) -> set[int]:
    """As tarefas de que esta depende, direta ou indiretamente."""
    seen = seen if seen is not None else set()
    for d in db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task_id)):
        if d not in seen:
            seen.add(d)
            _ancestors(db, d, seen)
    return seen


def pending_deps(db: Session, task: EmployeeTask) -> list[EmployeeTask]:
    ids = list(db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task.id)))
    return [t for t in (db.get(EmployeeTask, i) for i in ids) if t is not None and t.status != "done"]


def add_dependency(db: Session, acc: Access, task_id: int, on_id: int) -> dict:
    t, e, a = _task(db, acc, task_id)
    other, _, _ = _task(db, acc, on_id, write=False)
    if t.id == other.id or t.id in _ancestors(db, other.id):
        raise PlatformError("isso criaria um ciclo entre as tarefas")
    if t.status not in ("new", "blocked", "on_hold"):
        raise PlatformError(f"só tarefas que ainda não começaram ganham dependências (#{t.id} está {t.status})")
    if db.scalar(select(TaskDependency.id).where(TaskDependency.task_id == t.id, TaskDependency.depends_on_id == other.id)):
        raise PlatformError("essa dependência já existe")
    db.add(TaskDependency(task_id=t.id, depends_on_id=other.id, created_by=acc.p.name, created_at=now()))
    if other.status != "done" and t.status == "new":
        t.status = "blocked"
    tasksmod.event(db, t, "dependency", {"on": other.id, "by": acc.p.name})
    db.commit()
    audit(db, acc.p.name, "employee.task.dependency", a.slug, f"#{t.id} depende de #{other.id}")
    return deps_of(db, t)


def remove_dependency(db: Session, acc: Access, task_id: int, on_id: int) -> dict:
    t, _, a = _task(db, acc, task_id)
    d = db.scalar(select(TaskDependency).where(TaskDependency.task_id == t.id, TaskDependency.depends_on_id == on_id))
    if d is None:
        raise PlatformError("essa dependência não existe")
    db.delete(d)
    tasksmod.event(db, t, "dependency", {"removed": on_id, "by": acc.p.name})
    db.commit()
    if t.status == "blocked" and not pending_deps(db, t):
        _unblock(db, t)
    return deps_of(db, t)


def deps_of(db: Session, t: EmployeeTask) -> dict:
    on = [db.get(EmployeeTask, i) for i in db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == t.id))]
    by = [db.get(EmployeeTask, i) for i in db.scalars(select(TaskDependency.task_id).where(TaskDependency.depends_on_id == t.id))]
    return {"depends_on": [{"id": x.id, "title": x.title, "status": x.status} for x in on if x],
            "blocks": [{"id": x.id, "title": x.title, "status": x.status} for x in by if x]}


def _unblock(db: Session, t: EmployeeTask):
    e = db.get(Employee, t.employee_id)
    en = tasksmod.lang_of(e) == "en"
    parts = []
    for i in db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == t.id)):
        d = db.get(EmployeeTask, i)
        if d is not None and d.status == "done":
            parts.append((f"PREREQUISITE TASK #{d.id} DONE ({d.title}):\n" if en else
                          f"TAREFA PRÉVIA #{d.id} CONCLUÍDA ({d.title}):\n") + (d.result or "")[:4000])
    if parts:
        cp = tasksmod.last_checkpoint(db, t)
        msgs = list(cp.messages) if cp else []
        tasksmod.checkpoint(db, t, msgs + [{"role": "user", "content": "\n\n".join(parts)}])
    t.status = "new"
    tasksmod.event(db, t, "unblocked", {"deps": len(parts)})
    db.commit()


def task_finished(db: Session, t: EmployeeTask):
    """Chamado quando uma tarefa termina: as que dependiam dela andam (ou ficam travadas, se ela falhou)."""
    for dep in db.scalars(select(TaskDependency).where(TaskDependency.depends_on_id == t.id)).all():
        child = db.get(EmployeeTask, dep.task_id)
        if child is None or child.status != "blocked":
            continue
        if t.status == "done":
            if not pending_deps(db, child):
                _unblock(db, child)
        else:
            tasksmod.event(db, child, "dependency_failed", {"on": t.id, "status": t.status})
            db.commit()


# ------------------------------------------------------------------ revisão antes de concluir
def needs_review(t: EmployeeTask, e: Employee) -> bool:
    return not t.probation and (t.review_required or e.review_policy == "manager")


def send_to_review(db: Session, t: EmployeeTask, e: Employee, result: str) -> EmployeeTask:
    reviewer = t.reviewer_user_id or e.manager_user_id
    t.result, t.status = result[:20000], "in_review"
    r = HumanRequest(employee_id=e.id, task_id=t.id, kind="review", action_type="review", mode="approve",
                     question=result[:2000], action_payload={"result": result[:20000]}, assigned_user_id=reviewer,
                     fallback_user_id=(e.backup_user_ids or [None])[0], approvals=[], created_at=now())
    db.add(r)
    db.flush()
    tasksmod.event(db, t, "review", {"request": r.id, "reviewer": reviewer})
    db.commit()
    tasksmod._notify_request(db, e, r)
    return t


def decide_review(db: Session, r: HumanRequest, e: Employee, t: EmployeeTask | None, decision: str, reason: str, who: str):
    if decision not in ("approve", "reject"):
        raise PlatformError("revisão: decision = approve (concluir) | reject (pedir mudanças, com reason)")
    if decision == "reject" and not reason:
        raise PlatformError("diga o que mudar (reason)")
    r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", decision, reason, who, now()
    if t is None:
        db.commit()
        return
    tasksmod.event(db, t, "review_decision", {"request": r.id, "decision": decision, "by": who})
    if decision == "approve":
        db.commit()
        tasksmod._finish(db, t, "done", result=t.result)
        return
    en = tasksmod.lang_of(e) == "en"
    t.status = "waiting_human"  # a revisão é uma decisão: o _resume devolve a tarefa para a fila com o pedido de mudança
    tasksmod._resume(db, t, (f"REVIEW #{r.id}: CHANGES REQUESTED by {who}: {reason}\nRedo the result taking this into account."
                             if en else f"REVISÃO #{r.id}: MUDANÇAS PEDIDAS por {who}: {reason}\nRefaça o resultado levando "
                             "isso em conta."))
    db.commit()


# ------------------------------------------------------------------ pausar (a tarefa e as que ela repassou)
def _tree(db: Session, t: EmployeeTask) -> list[EmployeeTask]:
    out, todo = [t], [t.id]
    while todo:
        kids = list(db.scalars(select(EmployeeTask).where(EmployeeTask.parent_task_id.in_(todo))))
        out += kids
        todo = [k.id for k in kids]
    return out


def hold(db: Session, acc: Access, task_id: int, on: bool) -> dict:
    t, e, a = _task(db, acc, task_id)
    if acc.p.user_id != t.requester_user_id:
        from .employees import find
        find(db, acc, a.slug, "manage")
    if on and t.status == "in_progress":
        raise PlatformError("a tarefa está numa rodada agora: pause depois que a rodada terminar (ou cancele)")
    changed = 0
    for x in _tree(db, t):
        if on and x.status in HOLDABLE:
            tasksmod.event(db, x, "hold", {"on": True, "by": acc.p.name, "was": x.status})
            x.status = "on_hold"
            changed += 1
        elif not on and x.status == "on_hold":
            x.status = "blocked" if pending_deps(db, x) else "new"
            tasksmod.event(db, x, "hold", {"on": False, "by": acc.p.name})
            changed += 1
    db.commit()
    audit(db, acc.p.name, "employee.task.hold" if on else "employee.task.resume", a.slug, f"#{t.id} ({changed})")
    return {"changed": changed, "status": t.status}


# ------------------------------------------------------------------ diagnóstico e continuar
def _last_run_side_effects(db: Session, t: EmployeeTask) -> list[str]:
    evs = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == t.id).order_by(TaskEvent.id.desc()).limit(200)))
    out = []
    for ev in evs:
        if ev.kind == "run":
            break
        p = ev.payload or {}
        if ev.kind == "gate" and p.get("action_type") not in (None, "read") and p.get("mode") in ("auto", "notify"):
            out.append(f"{p.get('tool')} ({p.get('action_type')})")
    return list(dict.fromkeys(reversed(out)))


def _name(db: Session, uid: int | None) -> str:
    u = db.get(User, uid) if uid else None
    return (u.name or u.email) if u else ""


def diagnosis(db: Session, t: EmployeeTask) -> dict | None:
    e = db.get(Employee, t.employee_id)
    manager = _name(db, e.manager_user_id) if e else ""
    if t.status == "failed":
        se = _last_run_side_effects(db, t)
        return {"state": "failed", "cause": t.error or "falha", "owner": manager, "side_effects": se,
                "actions": ["continue"], "attempts": t.attempts, "runs": t.runs,
                "limit": config.EMPLOYEE_MAX_RUNS + (t.extra_runs or 0)}
    if t.status == "waiting_budget":
        ev = db.scalar(select(TaskEvent).where(TaskEvent.task_id == t.id, TaskEvent.kind == "budget").order_by(TaskEvent.id.desc()))
        p = (ev.payload if ev else {}) or {}
        return {"state": "budget", "cause": f"orçamento parado: {p.get('scope', '')}", "owner": "", "actions": ["budgets"]}
    if t.status in ("waiting_human", "in_review"):
        r = db.scalar(select(HumanRequest).where(HumanRequest.task_id == t.id, HumanRequest.status == "open")
                      .order_by(HumanRequest.id.desc()))
        return {"state": "decision", "cause": f"pedido #{r.id} ({r.kind})" if r else "aguardando decisão",
                "owner": _name(db, r.assigned_user_id) if r else manager, "actions": ["decide"], "request": r.id if r else None}
    if t.status == "waiting_task":
        child = db.get(EmployeeTask, t.waiting_on) if t.waiting_on else None
        return {"state": "colleague", "cause": f"esperando a tarefa #{child.id}" if child else "esperando um colega",
                "owner": "", "actions": ["open_child"], "child": child.id if child else None}
    if t.status == "blocked":
        pend = pending_deps(db, t)
        failed = [d for d in pend if d.status in ("failed", "cancelled", "expired")]
        return {"state": "dependency", "cause": ("dependência sem concluir: " + ", ".join(f"#{d.id} ({d.status})" for d in pend))
                if pend else "dependências", "owner": t.requester if failed else "", "actions": ["dependencies"],
                "failed_deps": [d.id for d in failed]}
    if t.status == "on_hold":
        return {"state": "hold", "cause": "pausada", "owner": manager, "actions": ["resume"]}
    if t.status == "new" and t.not_before and tasksmod._aware(t.not_before) > now():
        return {"state": "retry", "cause": f"nova tentativa às {iso(tasksmod._aware(t.not_before))}", "owner": "",
                "actions": [], "attempts": t.attempts}
    return None


def continue_task(db: Session, acc: Access, task_id: int, confirm: bool = False, note: str = "") -> dict:
    t, e, a = _task(db, acc, task_id)
    if acc.p.user_id != t.requester_user_id:
        from .employees import find
        find(db, acc, a.slug, "manage")
    if t.status != "failed":
        raise PlatformError(f"só tarefas com falha continuam (#{t.id} está {t.status})")
    se = _last_run_side_effects(db, t)
    if se and not confirm:
        raise PlatformError("a última rodada pode ter feito algo fora da plataforma: " + ", ".join(se) +
                            ". Confira e confirme (confirm=true) para continuar sem repetir.")
    en = tasksmod.lang_of(e) == "en"
    msg = ((f"CONTINUE: the previous round failed ({t.error or 'error'}). Continue from where you stopped." +
            (f" Do NOT repeat what was already done: {', '.join(se)}." if se else "")) if en else
           (f"CONTINUAR: a rodada anterior falhou ({t.error or 'erro'}). Continue de onde parou." +
            (f" NÃO repita o que já foi feito: {', '.join(se)}." if se else "")))
    if note.strip():
        msg += f"\n{acc.p.name}: {note.strip()[:2000]}"
    cp = tasksmod.last_checkpoint(db, t)
    tasksmod.checkpoint(db, t, (list(cp.messages) if cp else []) + [{"role": "user", "content": msg}])
    t.status, t.error, t.finished_at, t.attempts, t.not_before = "new", "", None, 0, None
    t.extra_runs = (t.extra_runs or 0) + EXTRA_RUNS
    tasksmod.event(db, t, "continued", {"by": acc.p.name, "side_effects": se})
    db.commit()
    audit(db, acc.p.name, "employee.task.continue", a.slug, f"#{t.id}")
    return {"status": t.status, "extra_runs": t.extra_runs}


# ------------------------------------------------------------------ revisor e dependências na criação
def reviewer_id(db: Session, email: str | None) -> int | None:
    if not email:
        return None
    u = db.scalar(select(User).where(User.email == email.strip().lower(), User.active.is_(True)))
    if u is None:
        raise PlatformError(f"revisor '{email}' não é uma pessoa da plataforma")
    return u.id


def check_blockers(db: Session, acc: Access, ids: list[int] | None) -> list[EmployeeTask]:
    out = []
    for i in ids or []:
        t, _, _ = _task(db, acc, int(i), write=False)
        out.append(t)
    return out


def attach_blockers(db: Session, t: EmployeeTask, blockers: list[EmployeeTask], who: str):
    for b in blockers:
        db.add(TaskDependency(task_id=t.id, depends_on_id=b.id, created_by=who, created_at=now()))
    if any(b.status != "done" for b in blockers):
        t.status = "blocked"
        tasksmod.event(db, t, "dependency", {"on": [b.id for b in blockers]})
    elif blockers:  # todas já concluídas: o resultado delas entra no prompt agora
        db.flush()
        _unblock(db, t)
    db.commit()

