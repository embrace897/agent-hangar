"""Modo maximizador (por projeto): mais trabalho entregue por pessoa que supervisiona, sem esconder nada.

Num projeto em modo maximizador, os Digital employees:
- têm **mais rodadas e mais passos** por tarefa (vão até o fim em vez de parar no meio);
- recebem a instrução de **ir até o fim**: dividir o trabalho grande e repassar partes a colegas do organograma,
  acompanhando até concluir;
- **tentam de novo sozinhos** quando uma tarefa falha por motivo técnico (com o erro anterior na conversa);
- mandam um **relatório** periódico para quem criou o projeto, e o projeto ganha um **painel de supervisão**.

O que não muda: a alçada (nada que exige uma pessoa passa a ser feito sem ela; o piso da empresa vale), o orçamento
do projeto (estourou, para) e a auditoria. Falha por alçada negada, orçamento ou cancelamento não é repetida.
"""
from datetime import timedelta

from sqlalchemy import func, select

from .. import config
from ..models import EmployeeTask, HumanRequest, Project, TaskEvent, TaskMessage, User, now
from .access import Access, Forbidden
from .common import PlatformError, audit, iso

DEFAULTS = {"extra_rounds": 20, "max_steps": 60, "auto_retry": 1, "delegate": True, "report_hours": 24}
CAPS = {"extra_rounds": (0, 50), "max_steps": (6, 100), "auto_retry": (0, 3), "report_hours": (1, 168)}
NO_RETRY = ("alçada", "negad", "orçamento", "budget", "cancel", "expir", "limite de", "fechado no")


def settings(p: Project | None) -> dict | None:
    a = (p.autonomy or {}) if p is not None else {}
    return {**DEFAULTS, **a} if a.get("mode") == "maximizer" else None


def of_task(db, task: EmployeeTask) -> dict | None:
    return settings(db.get(Project, task.project_id)) if task.project_id else None


def set_autonomy(db, acc: Access, slug: str, changes: dict) -> dict:
    from .goals import can_edit_project, find_project
    p = find_project(db, acc, slug)
    from .access import RANK
    if not (acc.p.is_admin or RANK.get(acc.teams.get(p.team_id, ""), 0) >= RANK["maintainer"]):
        raise Forbidden("quem mantém o time (ou o admin) liga o modo maximizador")
    assert can_edit_project(acc, p)
    mode = changes.get("mode", (p.autonomy or {}).get("mode", "normal"))
    if mode not in ("maximizer", "normal"):
        raise PlatformError("mode: maximizer | normal")
    cur = dict(p.autonomy or {})
    cur["mode"] = mode
    for k, (lo, hi) in CAPS.items():
        if changes.get(k) is not None:
            v = int(changes[k])
            if not lo <= v <= hi:
                raise PlatformError(f"{k}: de {lo} a {hi}")
            cur[k] = v
    if changes.get("delegate") is not None:
        cur["delegate"] = bool(changes["delegate"])
    p.autonomy = cur
    db.commit()
    audit(db, acc.p.name, "project.autonomy", p.slug, f"{mode} {cur}")
    return {"autonomy": {**DEFAULTS, **cur} if mode == "maximizer" else cur}


# ------------------------------------------------------------------ o que muda na tarefa
def extra_rounds(db, task: EmployeeTask) -> int:
    s = of_task(db, task)
    return int(s["extra_rounds"]) if s else 0


def max_steps(db, task: EmployeeTask) -> int | None:
    s = of_task(db, task)
    return int(s["max_steps"]) if s else None


def prompt(db, task: EmployeeTask, en: bool = False) -> str:
    s = of_task(db, task)
    if not s:
        return ""
    deleg = ((" Split large work and hand parts off to colleagues in your org chart (handoff_task with wait=true), "
              "following up until everything is done.") if en else
             (" Divida trabalho grande e repasse partes a colegas do organograma (handoff_task com wait=true), "
              "acompanhando até tudo terminar.")) if s.get("delegate") else ""
    return (("MAXIMIZER MODE (this project): go all the way to the end — don't stop to confirm what is already clear, "
             "and don't hand back half-done work." + deleg + " Your authority still applies: whatever needs a person "
             "still waits for one.") if en else
            ("MODO MAXIMIZADOR (este projeto): vá até o fim — não pare para confirmar o que já está claro e não devolva "
             "trabalho pela metade." + deleg + " Sua alçada continua valendo: o que exige uma pessoa continua esperando "
             "por ela."))


def retry(db, task: EmployeeTask, error: str) -> bool:
    """Falhou por motivo técnico num projeto maximizador: tenta de novo (até auto_retry vezes), com o erro na conversa."""
    s = of_task(db, task)
    if not s or not s.get("auto_retry") or task.probation:
        return False
    if any(w in (error or "").lower() for w in NO_RETRY):
        return False
    done = db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.kind == "auto_retry")) or 0
    if done >= int(s["auto_retry"]):
        return False
    from . import employee_tasks as tasksmod
    db.add(TaskMessage(task_id=task.id, author="plataforma", user_id=None, status="queued", created_at=now(),
                       body=(f"A tentativa anterior falhou: {error[:1500]}\nTente de novo por outro caminho (modo maximizador, "
                             f"tentativa {done + 2}).")))
    task.status, task.error, task.attempts, task.finished_at = "new", "", 0, None
    task.extra_runs = (task.extra_runs or 0) + 3
    tasksmod.event(db, task, "auto_retry", {"error": error[:300], "attempt": done + 2})
    db.commit()
    return True


# ------------------------------------------------------------------ supervisão e relatório
def supervision(db, acc: Access, slug: str, days: int = 7) -> dict:
    from .goals import find_project
    return _summary(db, find_project(db, acc, slug), max(1, min(int(days or 7), 90)))


def _summary(db, p: Project, days: int) -> dict:
    since = now() - timedelta(days=days)
    tasks = list(db.scalars(select(EmployeeTask).where(EmployeeTask.project_id == p.id, EmployeeTask.created_at >= since)))
    ids = [t.id for t in tasks]
    by = {}
    for t in tasks:
        by[t.status] = by.get(t.status, 0) + 1
    reqs = list(db.scalars(select(HumanRequest).where(HumanRequest.task_id.in_(ids or [-1]))))
    deciders = {r.decided_by for r in reqs if r.status == "decided" and r.decided_by}
    ev = lambda kind: db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id.in_(ids or [-1]), TaskEvent.kind == kind)) or 0  # noqa: E731
    done = by.get("done", 0)
    return {"project": p.slug, "mode": (p.autonomy or {}).get("mode", "normal"), "settings": settings(p) or {}, "days": days,
            "tasks": len(tasks), "by_status": by, "done": done, "retries": ev("auto_retry"), "handoffs": ev("handoff"),
            "human_decisions": sum(1 for r in reqs if r.status == "decided"), "waiting_now": sum(1 for r in reqs if r.status == "open"),
            "supervisors": len(deciders), "done_per_supervisor": round(done / max(1, len(deciders)), 1),
            "cost_usd": round(sum(t.cost_usd or 0 for t in tasks), 4),
            "blocked": [{"task": t.id, "title": t.title, "status": t.status} for t in tasks if t.status in ("waiting_human", "failed")][:20],
            "last_report": (p.autonomy or {}).get("last_report"), "last_report_at": (p.autonomy or {}).get("last_report_at")}


def _report_text(s: dict) -> str:
    st = s["by_status"]
    return (f"Projeto {s['project']} (últimos {s['days']} dias): {s['tasks']} tarefas — {st.get('done', 0)} concluídas, "
            f"{st.get('in_progress', 0) + st.get('new', 0)} andando, {st.get('waiting_human', 0)} esperando uma pessoa, "
            f"{st.get('failed', 0)} falharam ({s['retries']} tentativas automáticas). {s['handoffs']} repasses entre "
            f"Digital employees; {s['human_decisions']} decisões de pessoas ({s['supervisors']} pessoa(s): "
            f"{s['done_per_supervisor']} entregas por pessoa). Custo US$ {s['cost_usd']}.")


def sweep(db, at=None) -> int:
    from datetime import datetime

    from . import employee_tasks as tasksmod
    at, n = at or now(), 0
    for p in db.scalars(select(Project).where(Project.status == "active")).all():
        s = settings(p)
        if not s:
            continue
        last = (p.autonomy or {}).get("last_report_at")
        if last and tasksmod._aware(datetime.fromisoformat(last)) > at - timedelta(hours=int(s["report_hours"])):
            continue
        sup = _summary(db, p, max(1, int(s["report_hours"]) // 24))
        text = _report_text(sup)
        p.autonomy = {**(p.autonomy or {}), "last_report": text, "last_report_at": iso(at)}
        db.commit()
        u = db.scalar(select(User).where(User.email == (p.created_by or "").lower()))
        if u is not None:
            from . import notify
            cfg = notify.org_cfg(db)
            via = notify.route(cfg, u)
            if via:
                notify._bg(_tell, u.id, f"Relatório do projeto {p.name}", text, p.slug)
        n += 1
    return n


def _tell(db, user_id: int, title: str, text: str, slug: str):
    from . import notify
    u = db.get(User, user_id)
    cfg = notify.org_cfg(db)
    via = notify.route(cfg, u) if u else None
    if via:
        notify._send_text(db, cfg, u, via, title, [text[:1500]], f"{config.PUBLIC_BASE_URL}/app/#/projects/{slug}")
