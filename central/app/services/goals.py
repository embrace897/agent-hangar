"""Metas e projetos: o porquê do trabalho.

- Meta: da empresa (sem time; cria um admin) ou de um time (cria o mantenedor), em árvore (meta-pai), com dono, status e
  data alvo.
- Projeto: de um time; agrupa tarefas em torno de um resultado, aponta para uma meta e traz instruções e o Digital
  employee padrão.
- A tarefa aponta para um projeto (e, opcionalmente, para uma meta própria). O agente recebe no prompt o projeto, as
  instruções e a cadeia de metas até a da empresa: sabe por que a tarefa existe, não só o título.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Agent, Employee, EmployeeTask, Goal, Project, Team, User, now
from ..tenancy import unscoped
from .access import RANK, Access, Forbidden, find_team
from .common import PlatformError, audit, iso, slugify

GOAL_STATUSES = ("planned", "active", "achieved", "dropped")
MAX_DEPTH = 6


def _names(db: Session, ids) -> dict[int, str]:
    ids = [i for i in ids if i]
    return {u.id: (u.name or u.email) for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def _team(db: Session, team_id) -> Team | None:
    return db.get(Team, team_id) if team_id else None


def _role(acc: Access, team_id: int | None) -> int:
    return 99 if acc.p.is_admin else RANK.get(acc.teams.get(team_id, ""), 0) if team_id else 0


# ------------------------------------------------------------------ metas
def chain(db: Session, goal_id: int | None) -> list[Goal]:
    """A meta e as metas-pai, da mais próxima até a raiz."""
    out, seen = [], set()
    while goal_id and goal_id not in seen and len(out) < MAX_DEPTH:
        seen.add(goal_id)
        g = db.get(Goal, goal_id)
        if g is None:
            break
        out.append(g)
        goal_id = g.parent_id
    return out


def can_edit_goal(acc: Access, g: Goal) -> bool:
    if acc.p.is_admin:
        return True
    if g.team_id is None:
        return False
    return _role(acc, g.team_id) >= RANK["maintainer"] or (acc.p.user_id and g.owner_user_id == acc.p.user_id)


def goal_dict(db: Session, g: Goal, acc: Access | None = None, names: dict | None = None) -> dict:
    t = _team(db, g.team_id)
    names = names if names is not None else _names(db, [g.owner_user_id])
    projects = db.scalar(select(func.count()).select_from(Project).where(Project.goal_id == g.id)) or 0
    return {"id": g.id, "title": g.title, "description": g.description, "status": g.status, "target_date": g.target_date,
            "team": t.slug if t else None, "team_name": t.name if t else None, "parent_id": g.parent_id,
            "owner": names.get(g.owner_user_id, ""), "owner_user_id": g.owner_user_id, "projects": projects,
            "can_edit": bool(acc and can_edit_goal(acc, g)), "updated_at": iso(g.updated_at)}


def list_goals(db: Session, acc: Access) -> list[dict]:
    """Todas as metas da empresa: ver o porquê do trabalho é aberto a quem está na plataforma."""
    rows = list(db.scalars(select(Goal).order_by(Goal.parent_id.is_not(None), Goal.id)))
    names = _names(db, [g.owner_user_id for g in rows])
    return [goal_dict(db, g, acc, names) for g in rows]


def _owner(db: Session, email: str | None) -> int | None:
    if not email:
        return None
    u = db.scalar(select(User).where(User.email == email.strip().lower()))
    if u is None:
        raise PlatformError(f"'{email}' não é uma pessoa da plataforma")
    return u.id


def _check_parent(db: Session, g_id: int | None, parent_id: int | None, team_id: int | None):
    if not parent_id:
        return
    p = db.get(Goal, int(parent_id))
    if p is None:
        raise PlatformError("meta-pai não encontrada")
    if p.team_id is not None and p.team_id != team_id:
        raise PlatformError("a meta-pai é de outro time: aponte para uma meta da empresa ou do mesmo time")
    if g_id and any(x.id == g_id for x in chain(db, parent_id)):
        raise PlatformError("isso criaria um ciclo entre as metas")


def create_goal(db: Session, acc: Access, title: str, team: str | None = None, parent_id: int | None = None,
                description: str = "", owner: str | None = None, status: str = "active", target_date: str = "") -> dict:
    team_id = find_team(db, team).id if team else None
    if team_id is None and not acc.p.is_admin:
        raise Forbidden("metas da empresa são criadas por um admin; metas do time, pelo mantenedor")
    if team_id is not None and _role(acc, team_id) < RANK["maintainer"]:
        raise Forbidden("só o mantenedor do time (ou um admin) cria metas do time")
    if not (title or "").strip():
        raise PlatformError("a meta precisa de um título")
    if status not in GOAL_STATUSES:
        raise PlatformError(f"status: {' | '.join(GOAL_STATUSES)}")
    _check_parent(db, None, parent_id, team_id)
    g = Goal(team_id=team_id, parent_id=parent_id or None, title=title.strip()[:300], description=description or "",
             owner_user_id=_owner(db, owner) or acc.p.user_id, status=status, target_date=(target_date or "")[:10],
             created_by=acc.p.name, created_at=now(), updated_at=now())
    db.add(g)
    db.commit()
    audit(db, acc.p.name, "goal.create", g.title[:120], f"time {team or 'empresa'}")
    return goal_dict(db, g, acc)


def update_goal(db: Session, acc: Access, goal_id: int, changes: dict) -> dict:
    g = db.get(Goal, goal_id)
    if g is None:
        raise PlatformError("meta não encontrada")
    if not can_edit_goal(acc, g):
        raise Forbidden("edita a meta o dono, o mantenedor do time ou um admin")
    if changes.get("title") is not None:
        if not changes["title"].strip():
            raise PlatformError("a meta precisa de um título")
        g.title = changes["title"].strip()[:300]
    if changes.get("description") is not None:
        g.description = changes["description"]
    if changes.get("status") is not None:
        if changes["status"] not in GOAL_STATUSES:
            raise PlatformError(f"status: {' | '.join(GOAL_STATUSES)}")
        g.status = changes["status"]
    if changes.get("target_date") is not None:
        g.target_date = changes["target_date"][:10]
    if changes.get("owner") is not None:
        g.owner_user_id = _owner(db, changes["owner"]) if changes["owner"] else None
    if "parent_id" in changes:
        _check_parent(db, g.id, changes["parent_id"], g.team_id)
        g.parent_id = changes["parent_id"] or None
    g.updated_at = now()
    db.commit()
    audit(db, acc.p.name, "goal.update", g.title[:120], ", ".join(sorted(k for k, v in changes.items() if v is not None)))
    return goal_dict(db, g, acc)


def delete_goal(db: Session, acc: Access, goal_id: int):
    g = db.get(Goal, goal_id)
    if g is None:
        raise PlatformError("meta não encontrada")
    if not can_edit_goal(acc, g):
        raise Forbidden("apaga a meta o mantenedor do time ou um admin")
    if db.scalar(select(func.count()).select_from(Goal).where(Goal.parent_id == g.id)):
        raise PlatformError("a meta tem metas-filhas: mova ou apague antes")
    for p in db.scalars(select(Project).where(Project.goal_id == g.id)):
        p.goal_id = None
    db.delete(g)
    db.commit()
    audit(db, acc.p.name, "goal.delete", g.title[:120])


# ------------------------------------------------------------------ projetos
def can_view_project(acc: Access, p: Project) -> bool:
    return acc.p.is_auditor or p.team_id is None or p.team_id in acc.teams


def can_edit_project(acc: Access, p: Project) -> bool:
    return acc.p.is_admin or (p.team_id is not None and _role(acc, p.team_id) >= RANK["developer"])


def find_project(db: Session, acc: Access, ref) -> Project:
    p = db.get(Project, int(ref)) if str(ref).isdigit() else db.scalar(select(Project).where(Project.slug == str(ref)))
    if p is None or not can_view_project(acc, p):
        raise Forbidden(f"projeto '{ref}' não encontrado ou sem acesso")
    return p


def _employee(db: Session, slug: str | None, team_id: int | None) -> int | None:
    if not slug:
        return None
    a = db.scalar(select(Agent).where(Agent.slug == slug))
    e = db.scalar(select(Employee).where(Employee.agent_id == a.id)) if a else None
    if e is None:
        raise PlatformError(f"'{slug}' não é um Digital employee")
    if team_id and e.team_id and e.team_id != team_id:
        raise PlatformError(f"'{slug}' é de outro time")
    return e.id


def project_dict(db: Session, p: Project, acc: Access | None = None, detail: bool = False) -> dict:
    t = _team(db, p.team_id)
    counts = dict(db.execute(select(EmployeeTask.status, func.count()).where(EmployeeTask.project_id == p.id)
                             .group_by(EmployeeTask.status)).all())
    cost = db.scalar(select(func.coalesce(func.sum(EmployeeTask.cost_usd), 0)).where(EmployeeTask.project_id == p.id)) or 0
    de = db.get(Employee, p.default_employee_id) if p.default_employee_id else None
    da = db.get(Agent, de.agent_id) if de else None
    g = db.get(Goal, p.goal_id) if p.goal_id else None
    open_n = sum(v for k, v in counts.items() if k not in ("done", "failed", "cancelled", "expired"))
    d = {"slug": p.slug, "name": p.name, "description": p.description, "team": t.slug if t else None,
         "team_name": t.name if t else None, "status": p.status, "goal": {"id": g.id, "title": g.title} if g else None,
         "default_employee": da.slug if da else None, "default_employee_name": da.name if da else None,
         "tasks": sum(counts.values()), "open": open_n, "done": counts.get("done", 0), "cost_usd": round(float(cost), 4),
         "can_edit": bool(acc and can_edit_project(acc, p)), "updated_at": iso(p.updated_at)}
    if detail:
        from .employees import task_dict
        d["instructions"] = p.instructions
        from . import maximizer
        d["autonomy"] = maximizer.settings(p) or {"mode": "normal"}
        d["goal_chain"] = [{"id": x.id, "title": x.title, "status": x.status} for x in chain(db, p.goal_id)]
        rows = db.scalars(select(EmployeeTask).where(EmployeeTask.project_id == p.id).order_by(EmployeeTask.id.desc()).limit(200))
        d["task_list"] = []
        for x in rows:
            e = db.get(Employee, x.employee_id)
            a = db.get(Agent, e.agent_id) if e else None
            d["task_list"].append({**task_dict(db, x), "employee": a.slug if a else "", "employee_name": a.name if a else ""})
    return d


def list_projects(db: Session, acc: Access, include_archived: bool = False) -> list[dict]:
    q = select(Project).order_by(Project.status, Project.name)
    if not include_archived:
        q = q.where(Project.status == "active")
    return [project_dict(db, p, acc) for p in db.scalars(q) if can_view_project(acc, p)]


def create_project(db: Session, acc: Access, name: str, team: str, description: str = "", instructions: str = "",
                   goal_id: int | None = None, default_employee: str | None = None, slug: str = "") -> dict:
    t = find_team(db, team)
    if _role(acc, t.id) < RANK["developer"]:
        raise Forbidden("cria projetos quem é developer ou mantenedor do time (ou um admin)")
    if not (name or "").strip():
        raise PlatformError("o projeto precisa de um nome")
    base = slugify(slug or name)
    s, n = base, 2
    while db.scalar(unscoped(select(Project.id).where(Project.slug == s))):  # slug único na instalação
        s, n = f"{base}-{n}", n + 1
    if goal_id:
        g = db.get(Goal, int(goal_id))
        if g is None or (g.team_id is not None and g.team_id != t.id):
            raise PlatformError("a meta precisa ser da empresa ou deste time")
    p = Project(team_id=t.id, slug=s, name=name.strip()[:200], description=description or "", instructions=instructions or "",
                goal_id=goal_id or None, default_employee_id=_employee(db, default_employee, t.id), created_by=acc.p.name,
                created_at=now(), updated_at=now())
    db.add(p)
    db.commit()
    audit(db, acc.p.name, "project.create", p.slug, f"time {t.slug}")
    return project_dict(db, p, acc, True)


def update_project(db: Session, acc: Access, ref, changes: dict) -> dict:
    p = find_project(db, acc, ref)
    if not can_edit_project(acc, p):
        raise Forbidden("edita o projeto quem é developer ou mantenedor do time")
    for k in ("name", "description", "instructions"):
        if changes.get(k) is not None:
            setattr(p, k, changes[k].strip()[:200] if k == "name" else changes[k])
    if "goal_id" in changes:
        gid = changes["goal_id"]
        if gid:
            g = db.get(Goal, int(gid))
            if g is None or (g.team_id is not None and g.team_id != p.team_id):
                raise PlatformError("a meta precisa ser da empresa ou deste time")
        p.goal_id = gid or None
    if "default_employee" in changes:
        p.default_employee_id = _employee(db, changes["default_employee"], p.team_id)
    if changes.get("status") is not None:
        if changes["status"] not in ("active", "archived"):
            raise PlatformError("status: active | archived")
        p.status = changes["status"]
    p.updated_at = now()
    db.commit()
    audit(db, acc.p.name, "project.update", p.slug, ", ".join(sorted(k for k, v in changes.items() if v is not None)))
    return project_dict(db, p, acc, True)


def project_for_task(db: Session, acc: Access, ref, e: Employee) -> Project | None:
    """Valida o projeto de uma tarefa nova: visível para quem pede, ativo e do mesmo time do funcionário."""
    if not ref:
        return None
    p = find_project(db, acc, ref)
    if p.status != "active":
        raise PlatformError("o projeto está arquivado")
    if p.team_id and e.team_id and p.team_id != e.team_id:
        raise PlatformError("o projeto é de outro time")
    return p


# ------------------------------------------------------------------ o contexto que vai para o agente
def task_context(db: Session, task: EmployeeTask, en: bool = False) -> str:
    p = db.get(Project, task.project_id) if task.project_id else None
    goals = chain(db, task.goal_id or (p.goal_id if p else None))
    if not p and not goals:
        return ""
    lines = []
    if p:
        lines.append(f"{'Project' if en else 'Projeto'}: {p.name}" + (f" — {p.description}" if p.description else ""))
        if p.instructions.strip():
            lines.append(("Project instructions:\n" if en else "Instruções do projeto:\n") + p.instructions.strip()[:4000])
        from . import maximizer
        mx = maximizer.prompt(db, task, en)
        if mx:
            lines.append(mx)
    if goals:
        head = "This work serves these goals (closest first):" if en else "Este trabalho serve a estas metas (da mais próxima à da empresa):"
        lines.append(head + "\n" + "\n".join(f"- {g.title}" + (f": {g.description[:300]}" if g.description else "")
                                              for g in goals))
    return "\n".join(lines)
