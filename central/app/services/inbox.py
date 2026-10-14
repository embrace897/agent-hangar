"""Caixa de entrada pessoal e busca na empresa.

Caixa: o que precisa de mim, num lugar só, com lido e arquivado por pessoa (arquivar não conclui nem cancela nada).
- decisões que eu posso tomar;
- tarefas que eu pedi, com novidade desde a última vez que abri;
- tarefas paradas (falha, prazo vencido, orçamento) dos Digital employees que eu gerencio;
- paradas e avisos de orçamento dos escopos pelos quais eu respondo.

Busca: agentes, Digital employees, tarefas, projetos, metas, plugins e skills, sempre filtrada pelo que a pessoa pode
ver. Postgres usa ILIKE; SQLite, LIKE (sem diferença de maiúsculas para ASCII).
"""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    Agent,
    BudgetIncident,
    BudgetPolicy,
    Employee,
    EmployeeTask,
    Goal,
    InboxState,
    Plugin,
    Project,
    Skill,
    now,
)
from . import employee_tasks as tasksmod
from .access import Access
from .common import PlatformError, iso

VIEWS = ("all", "unread", "decisions", "blocked", "mine", "archived")
STUCK = ("failed", "waiting_budget")


def _states(db: Session, user_id: int) -> dict[str, InboxState]:
    return {s.item: s for s in db.scalars(select(InboxState).where(InboxState.user_id == user_id))}


def _employees_i_manage(db: Session, acc: Access) -> dict[int, Agent]:
    from .employees import find
    out = {}
    for e in db.scalars(select(Employee).where(Employee.status != "offboarded")):
        a = db.get(Agent, e.agent_id)
        if a is None:
            continue
        if acc.p.user_id and acc.p.user_id in (e.manager_user_id, e.owner_user_id, *(e.backup_user_ids or [])):
            out[e.id] = a
            continue
        try:
            find(db, acc, a.slug, "manage")
            out[e.id] = a
        except Exception:  # noqa: BLE001
            continue
    return out


def items(db: Session, acc: Access, view: str = "all") -> dict:
    if not acc.p.user_id:
        raise PlatformError("a caixa de entrada é de uma pessoa (entre com a sua conta)")
    if view not in VIEWS:
        raise PlatformError(f"view: {' | '.join(VIEWS)}")
    st = _states(db, acc.p.user_id)
    out: dict[str, dict] = {}

    def add(key, kind, title, at, url, **extra):
        s = st.get(key)
        unread = s is None or s.read_at is None or (at is not None and tasksmod._aware(s.read_at) < tasksmod._aware(at))
        out[key] = {"key": key, "kind": kind, "title": title, "at": iso(tasksmod._aware(at)) if at else None, "url": url,
                    "unread": unread, "archived": bool(s and s.archived), **extra}

    for d in tasksmod.pending_for(db, acc):  # decisões
        if d["kind"] == "notice":
            continue
        add(f"decision:{d['id']}", "decision", d.get("question") or (d.get("task") or {}).get("title") or d.get("tool") or d["kind"],
            db.get(tasksmod.HumanRequest, d["id"]).created_at, "#/decisions",
            employee=d.get("employee"), decision_kind=d["kind"], mine=d.get("mine"), task_id=(d.get("task") or {}).get("id"))
    mine = select(EmployeeTask).where(EmployeeTask.requester_user_id == acc.p.user_id).order_by(EmployeeTask.updated_at.desc()).limit(100)
    for t in db.scalars(mine):  # o que eu pedi
        e = db.get(Employee, t.employee_id)
        a = db.get(Agent, e.agent_id) if e else None
        add(f"task:{t.id}", "task", t.title, t.updated_at, f"#/tasks/{t.id}", task_id=t.id, status=t.status,
            employee=a.slug if a else "", employee_name=a.name if a else "", requested=True,
            blocked=t.status in STUCK or t.due_state in ("overdue", "escalated"))
    managed = _employees_i_manage(db, acc)
    if managed:  # o que travou nos funcionários que eu gerencio
        q = select(EmployeeTask).where(EmployeeTask.employee_id.in_(list(managed)), or_(
            EmployeeTask.status.in_(STUCK), EmployeeTask.due_state.in_(("overdue", "escalated")))).order_by(
            EmployeeTask.updated_at.desc()).limit(100)
        for t in db.scalars(q):
            if t.status in ("done", "cancelled") or f"task:{t.id}" in out:
                continue
            a = managed[t.employee_id]
            add(f"task:{t.id}", "task", t.title, t.updated_at, f"#/tasks/{t.id}", task_id=t.id, status=t.status,
                employee=a.slug, employee_name=a.name, requested=False, blocked=True)
    from . import issues as issuesmod
    for x in issuesmod.inbox_items(db, acc):  # issues em que estou inscrito, com novidade de outra pessoa ou agente
        i = x["issue"]
        add(f"issue:{i.id}", "issue", f"{x['key']} {i.title}", x["at"], f"#/issues/{x['key']}", issue=x["key"],
            actor=x["actor"], activity=x["kind"], mention=x["kind"] == "mention", mine=x["kind"] == "mention",
            blocked=x["kind"] == "run_failed")
    from .budgets import can_manage, scope_name
    for i in db.scalars(select(BudgetIncident).where(BudgetIncident.status == "open").order_by(BudgetIncident.id.desc()).limit(50)):
        p = db.get(BudgetPolicy, i.policy_id)
        if p and can_manage(db, acc, p):
            add(f"budget:{i.id}", "budget", scope_name(db, p), i.opened_at, "#/budgets", incident=i.id, budget_kind=i.kind,
                scope_type=p.scope_type, scope=scope_name(db, p), blocked=i.kind == "hard_stop")
    if acc.p.is_admin:  # modelo que sumiu do provedor e algum agente ainda usa
        from .llm_models import lost_in_use
        for x in lost_in_use(db):
            add(f"llm_model:{x['connection']}:{x['model']}", "llm_model",
                f"O modelo {x['model']} saiu do provedor ({x['connection']}) e é usado por {', '.join(x['agents'])}",
                x["at"], "#/providers", connection=x["connection"], model=x["model"], agents=x["agents"], blocked=True)
    if acc.p.is_admin:  # conector esperando um admin: ferramenta nova/alterada ou autenticação vencida
        from .connectors import attention
        for x in attention(db):
            title = (f"Conector {x['name']}: {len(x['tools'])} ferramenta(s) nova(s) ou alterada(s) esperando aprovação"
                     if x["kind"] == "tools" else f"Conector {x['name']} precisa de nova autenticação")
            add(f"connector:{x['name']}:{x['kind']}", "connector", title, x["at"], f"#/connectors/{x['name']}",
                connector=x["name"], blocked=True)
    if acc.p.is_admin:  # agente com um modelo bem melhor para o papel (sugestão; nada muda sozinho)
        from .model_selection import cached_suggestions
        for x in cached_suggestions(db):
            s = x["suggestion"]
            add(f"model_suggestion:{x['agent']}:{s['model']}", "model_suggestion",
                f"{x['name']}: {s['model']} é melhor para {x['role_label']} que {x['current']['model']}",
                x.get("at"), f"#/agents/{x['agent']}", agent=x["agent"], model=s["model"], connection=s["connection"])
    rows = list(out.values())
    counts = {"all": sum(not r["archived"] for r in rows), "unread": sum(r["unread"] and not r["archived"] for r in rows),
              "decisions": sum(r["kind"] == "decision" and not r["archived"] for r in rows),
              "blocked": sum(bool(r.get("blocked")) and not r["archived"] for r in rows)}
    pick = {"all": lambda r: not r["archived"], "unread": lambda r: r["unread"] and not r["archived"],
            "decisions": lambda r: r["kind"] == "decision" and not r["archived"],
            "blocked": lambda r: bool(r.get("blocked")) and not r["archived"],
            "mine": lambda r: (r.get("requested") or r.get("mine")) and not r["archived"],
            "archived": lambda r: r["archived"]}[view]
    rows = sorted((r for r in rows if pick(r)), key=lambda r: r["at"] or "", reverse=True)  # mais recentes primeiro
    rows.sort(key=lambda r: not r["unread"])
    return {"view": view, "counts": counts, "items": rows}


def mark(db: Session, acc: Access, keys: list[str], read: bool | None = None, archived: bool | None = None) -> dict:
    if not acc.p.user_id:
        raise PlatformError("a caixa de entrada é de uma pessoa")
    for k in keys[:200]:
        if not k or ":" not in k or len(k) > 40:
            raise PlatformError(f"item inválido: {k}")
        s = db.scalar(select(InboxState).where(InboxState.user_id == acc.p.user_id, InboxState.item == k))
        if s is None:
            s = InboxState(user_id=acc.p.user_id, item=k)
            db.add(s)
        if read is not None:
            s.read_at = now() if read else None
        if archived is not None:
            s.archived = bool(archived)
    db.commit()
    return {"ok": True, "updated": len(keys)}


def seen_task(db: Session, acc: Access, task_id: int):
    """Abrir a tarefa marca como lida na caixa de quem abriu."""
    if acc.p.user_id:
        mark(db, acc, [f"task:{task_id}"], read=True)


# ------------------------------------------------------------------ busca
def _like(col, q: str):
    from .. import shared
    return col.ilike(f"%{q}%") if shared.is_postgres() else col.like(f"%{q}%")


def _snip(text: str, q: str, n: int = 140) -> str:
    text = (text or "").replace("\n", " ")
    i = text.lower().find(q.lower())
    if i < 0:
        return text[:n]
    start = max(0, i - n // 3)
    return ("…" if start else "") + text[start:start + n] + ("…" if start + n < len(text) else "")


def search(db: Session, acc: Access, q: str, kinds: str = "", limit: int = 30) -> dict:
    q = (q or "").strip()
    if len(q) < 2:
        raise PlatformError("digite pelo menos 2 letras")
    q = q[:100].replace("%", "").replace("_", " ")
    want = set((kinds or "agent,employee,task,issue,project,goal,plugin,skill").split(","))
    limit = max(1, min(int(limit or 30), 100))
    out: list[dict] = []
    emp_by_agent = {e.agent_id: e for e in db.scalars(select(Employee))}
    if want & {"agent", "employee"}:
        for a in db.scalars(select(Agent).where(or_(_like(Agent.name, q), _like(Agent.slug, q), _like(Agent.objective, q)))
                            .limit(limit * 3)):
            if not acc.can("view", a):
                continue
            e = emp_by_agent.get(a.id)
            kind = "employee" if e else "agent"
            if kind in want:
                out.append({"kind": kind, "title": a.name, "sub": e.title if e else a.slug,
                            "url": f"#/employees/{a.slug}" if e else f"#/agents/{a.slug}",
                            "snippet": _snip(a.objective or "", q)})
    if "task" in want:
        from .employees import can_read_task
        rows = db.scalars(select(EmployeeTask).where(or_(_like(EmployeeTask.title, q), _like(EmployeeTask.body, q),
                                                         _like(EmployeeTask.result, q)))
                          .order_by(EmployeeTask.id.desc()).limit(limit * 3))
        for t in rows:
            e = db.get(Employee, t.employee_id)
            if e is not None and can_read_task(acc, t, e):
                src = t.title if q.lower() in t.title.lower() else t.body if q.lower() in (t.body or "").lower() else t.result
                out.append({"kind": "task", "title": f"#{t.id} {t.title}", "sub": t.status, "url": f"#/tasks/{t.id}",
                            "snippet": _snip(src, q)})
    if "issue" in want:
        from . import issues as im
        for i in db.scalars(select(im.Issue).where(or_(_like(im.Issue.title, q), _like(im.Issue.description, q)))
                            .order_by(im.Issue.id.desc()).limit(limit * 3)):
            if im.can_view(acc, i):
                k = im.key_of(db, i)
                out.append({"kind": "issue", "title": f"{k} {i.title}", "sub": i.status, "url": f"#/issues/{k}",
                            "snippet": _snip(i.description or i.title, q)})
    if "project" in want:
        from .goals import can_view_project
        for p in db.scalars(select(Project).where(or_(_like(Project.name, q), _like(Project.description, q),
                                                      _like(Project.slug, q))).limit(limit)):
            if can_view_project(acc, p):
                out.append({"kind": "project", "title": p.name, "sub": p.slug, "url": f"#/projects/{p.slug}",
                            "snippet": _snip(p.description, q)})
    if "goal" in want:
        for g in db.scalars(select(Goal).where(or_(_like(Goal.title, q), _like(Goal.description, q))).limit(limit)):
            out.append({"kind": "goal", "title": g.title, "sub": g.status, "url": "#/goals", "snippet": _snip(g.description, q)})
    if "plugin" in want:
        from .plugins import _can_edit, live
        for p in db.scalars(select(Plugin).where(or_(_like(Plugin.title, q), _like(Plugin.name, q),
                                                     _like(Plugin.description, q))).limit(limit)):
            if live(p) or _can_edit(acc, p) or acc.p.is_admin:
                out.append({"kind": "plugin", "title": p.title, "sub": p.name, "url": f"#/plugins/{p.name}",
                            "snippet": _snip(p.description, q)})
    if "skill" in want:
        for s in db.scalars(select(Skill).where(or_(_like(Skill.name, q), _like(Skill.description, q))).limit(limit)):
            if s.team_id is None or s.team_id in acc.teams or acc.p.is_auditor:
                out.append({"kind": "skill", "title": s.name, "sub": "skill", "url": "#/catalog", "snippet": _snip(s.description, q)})
    return {"q": q, "items": out[:limit], "more": len(out) > limit}
