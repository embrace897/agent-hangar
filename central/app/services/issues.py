"""Issues: o quadro kanban do trabalho (como o Multica), com a governança dos Digital employees.

- **Issue** (HGR-123) é o registro persistente: título, descrição (markdown), status, prioridade, responsável (uma
  pessoa ou um Digital employee), etiquetas, projeto, time, datas, estimativa, sub-issues com etapas, relações.
- **Rodada** é uma execução de um Digital employee sobre a issue: uma tarefa (EmployeeTask com issue_id), com a
  alçada, o orçamento, o modo sombra e a auditoria de sempre. Uma issue pode ter várias rodadas; o status da issue
  é que diz se acabou, não a rodada.
- Atribuir a um Digital employee (fora do backlog) começa uma rodada; @mencioná-lo num comentário também (sem trocar o
  responsável). Comentário de uma pessoa entra na conversa da rodada em andamento.
- O Digital employee comenta o andamento, muda o status (menos concluir ou cancelar: quem revisa conclui) e divide
  em sub-issues — repassar para outro funcionário passa pela alçada (delegar). Terminada a rodada, o resultado vira
  comentário e a issue vai para revisão; se falhar, volta para "a fazer" com o erro.
- Sub-issues em etapas: quando todas as da etapa mais antiga terminam, a issue mãe é avisada (e, se for de um Digital
  employee, ele acorda para decidir a próxima etapa).
- Quem vê: o time da issue (e admin/auditor); sem time, a empresa toda. Apagar: mantenedor, admin ou quem criou.
- Inscritos recebem a atividade dos outros na caixa de entrada; comentar ou ser mencionado inscreve.
"""
import re
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import tenancy
from ..models import (
    Agent,
    Employee,
    EmployeeTask,
    HumanRequest,
    Issue,
    IssueActivity,
    IssueComment,
    IssueLabel,
    IssueRelation,
    IssueStatus,
    IssueSubscription,
    Organization,
    Project,
    Team,
    TeamMember,
    User,
    now,
)
from . import employee_tasks as tasksmod
from .access import RANK, Access, Forbidden, find_team, principal_for
from .common import PlatformError, audit, iso

BUILTIN = [("backlog", "Backlog", "unstarted", "#8793A1"), ("todo", "A fazer", "unstarted", "#6B7785"),
           ("in_progress", "Em andamento", "started", "#D4A017"), ("in_review", "Em revisão", "started", "#2E9E5B"),
           ("blocked", "Bloqueada", "started", "#D9534F"), ("done", "Concluída", "done", "#3B82F6"),
           ("cancelled", "Cancelada", "closed", "#9AA6B3")]
CATEGORIES = ("unstarted", "started", "done", "closed")
PRIORITIES = ("urgent", "high", "medium", "low", "none")
TASK_PRIORITY = {"urgent": 1, "high": 1, "medium": 2, "none": 2, "low": 3}
RELATIONS = ("blocks", "related", "duplicate")
REACTIONS = ("👍", "❤️", "🎉", "👀", "🚀", "😕")
MENTION = re.compile(r"@\[([^\]]{1,120})\]\((employee|user):([A-Za-z0-9._@+-]{1,254})\)")
AGENT_STATUSES = ("unstarted", "started")  # o Digital employee não conclui nem cancela: quem revisa decide
MAX_BODY = 20000


# ------------------------------------------------------------------ configuração (status, etiquetas, prefixo)
def ensure_statuses(db: Session) -> None:
    have = set(db.scalars(select(IssueStatus.key)))
    added = False
    for pos, (key, name, cat, color) in enumerate(BUILTIN):
        if key not in have:
            db.add(IssueStatus(key=key, name=name, category=cat, color=color, position=pos * 10, builtin=True))
            added = True
    if added:
        try:
            db.commit()
        except IntegrityError:  # outra réplica criou ao mesmo tempo
            db.rollback()


def statuses(db: Session, include_archived: bool = False) -> list[dict]:
    ensure_statuses(db)
    q = select(IssueStatus).order_by(IssueStatus.position, IssueStatus.id)
    return [{"key": s.key, "name": s.name, "category": s.category, "color": s.color, "position": s.position,
             "builtin": s.builtin, "archived": s.archived} for s in db.scalars(q) if include_archived or not s.archived]


def _status(db: Session, key: str) -> IssueStatus:
    ensure_statuses(db)
    s = db.scalar(select(IssueStatus).where(IssueStatus.key == key))
    if s is None:
        raise PlatformError(f"status '{key}' não existe (veja os status do quadro)")
    return s


def category(db: Session, key: str) -> str:
    s = db.scalar(select(IssueStatus).where(IssueStatus.key == key))
    return s.category if s else "unstarted"


def _require_admin(acc: Access):
    if not acc.p.is_admin:
        raise Forbidden("só o admin configura o quadro (status e prefixo)")


def create_status(db: Session, acc: Access, name: str, cat: str, color: str = "#8793A1") -> dict:
    _require_admin(acc)
    ensure_statuses(db)
    name = (name or "").strip()[:80]
    if not name or cat not in CATEGORIES:
        raise PlatformError(f"informe o nome e a categoria ({' | '.join(CATEGORIES)})")
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "status"
    if db.scalar(select(IssueStatus).where(IssueStatus.key == key)):
        raise PlatformError(f"já existe o status '{key}'")
    last = db.scalar(select(func.max(IssueStatus.position))) or 0
    db.add(IssueStatus(key=key, name=name, category=cat, color=_color(color), position=last + 10))
    db.commit()
    audit(db, acc.p.name, "issue.status.create", key, cat)
    return {"key": key}


def update_status(db: Session, acc: Access, key: str, changes: dict) -> dict:
    _require_admin(acc)
    s = _status(db, key)
    if "position" in changes:
        s.position = int(changes["position"])
    if s.builtin and any(k in changes for k in ("name", "color", "archived")):
        raise PlatformError("os status embutidos não mudam de nome, cor nem são arquivados")
    if "name" in changes:
        s.name = str(changes["name"]).strip()[:80] or s.name
    if "color" in changes:
        s.color = _color(changes["color"])
    if "archived" in changes:
        s.archived = bool(changes["archived"])
    db.commit()
    audit(db, acc.p.name, "issue.status.update", key, ", ".join(sorted(changes)))
    return {"key": key}


def _color(c) -> str:
    c = str(c or "").strip()
    return c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else "#8793A1"


def labels(db: Session) -> list[dict]:
    return [{"id": x.id, "name": x.name, "color": x.color} for x in db.scalars(select(IssueLabel).order_by(IssueLabel.name))]


def create_label(db: Session, acc: Access, name: str, color: str = "") -> dict:
    if not acc.p.user_id and not acc.p.is_admin:
        raise Forbidden("etiquetas são criadas por pessoas")
    name = (name or "").strip()[:60]
    if not name:
        raise PlatformError("informe o nome da etiqueta")
    got = db.scalar(select(IssueLabel).where(func.lower(IssueLabel.name) == name.lower()))
    if got:
        return {"id": got.id, "name": got.name, "color": got.color}
    x = IssueLabel(name=name, color=_color(color or "#5B8DEF"), created_by=acc.p.name)
    db.add(x)
    db.commit()
    return {"id": x.id, "name": x.name, "color": x.color}


def delete_label(db: Session, acc: Access, label_id: int) -> dict:
    _require_admin(acc)
    x = db.get(IssueLabel, label_id)
    if x is None:
        raise PlatformError("etiqueta não encontrada")
    for i in db.scalars(select(Issue)):
        if label_id in (i.labels or []):
            i.labels = [lid for lid in i.labels if lid != label_id]
    db.delete(x)
    db.commit()
    return {"ok": True}


def prefix(db: Session) -> str:
    o = db.get(Organization, tenancy.current_org())
    return (o.issue_prefix if o and o.issue_prefix else "HGR")


def set_prefix(db: Session, acc: Access, value: str) -> dict:
    _require_admin(acc)
    v = (value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", v):
        raise PlatformError("prefixo: 2 a 10 letras/números, começando por letra (ex.: OPS)")
    db.get(Organization, tenancy.current_org()).issue_prefix = v
    db.commit()
    audit(db, acc.p.name, "issue.prefix", v, "")
    return {"prefix": v}


def settings(db: Session, acc: Access) -> dict:
    return {"prefix": prefix(db), "statuses": statuses(db, include_archived=True), "labels": labels(db),
            "priorities": list(PRIORITIES), "can_admin": acc.p.is_admin}


# ------------------------------------------------------------------ acesso
def can_view(acc: Access, i: Issue) -> bool:
    if acc.p.is_admin or acc.p.is_auditor or i.team_id is None:
        return True
    uid = acc.p.user_id
    return bool(acc.team_role(i.team_id)) or (uid is not None and uid in (i.creator_user_id, i.assignee_user_id))


def can_edit(acc: Access, i: Issue) -> bool:
    if acc.p.is_admin:
        return True
    if not acc.p.user_id:
        return False
    if i.team_id is None:
        return True
    return bool(acc.team_role(i.team_id)) or acc.p.user_id in (i.creator_user_id, i.assignee_user_id)


def can_delete(acc: Access, i: Issue) -> bool:
    if acc.p.is_admin or (acc.p.user_id and acc.p.user_id == i.creator_user_id):
        return True
    role = acc.team_role(i.team_id) if i.team_id else None
    return bool(role) and RANK[role] >= RANK["maintainer"]


def find_issue(db: Session, acc: Access, ref, write: bool = False) -> Issue:
    i = _by_ref(db, ref)
    if i is None or not can_view(acc, i):
        raise Forbidden(f"issue '{ref}' não encontrada ou sem acesso")
    if write and not can_edit(acc, i):
        raise Forbidden(f"sem permissão para mudar {key_of(db, i)}")
    return i


def _by_ref(db: Session, ref) -> Issue | None:
    s = str(ref or "").strip()
    m = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)-(\d+)", s)
    if m:
        return db.scalar(select(Issue).where(Issue.number == int(m.group(2))))
    if s.isdigit():
        return db.scalar(select(Issue).where(Issue.number == int(s)))
    return None


def key_of(db: Session, i: Issue, pfx: str | None = None) -> str:
    return f"{pfx or prefix(db)}-{i.number}"


# ------------------------------------------------------------------ leitura
def _people(db: Session, ids) -> dict[int, User]:
    ids = {x for x in ids if x}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def _employees(db: Session, ids) -> dict[int, tuple[Employee, Agent]]:
    ids = {x for x in ids if x}
    if not ids:
        return {}
    return {e.id: (e, a) for e, a in db.execute(select(Employee, Agent).join(Agent, Agent.id == Employee.agent_id)
                                                 .where(Employee.id.in_(ids))).all()}


def _assignee(i: Issue, people: dict, emps: dict) -> dict | None:
    if i.assignee_employee_id and i.assignee_employee_id in emps:
        e, a = emps[i.assignee_employee_id]
        return {"kind": "employee", "ref": f"employee:{a.slug}", "slug": a.slug, "name": a.name, "title": e.title}
    if i.assignee_user_id and i.assignee_user_id in people:
        u = people[i.assignee_user_id]
        return {"kind": "user", "ref": f"user:{u.id}", "id": u.id, "name": u.name or u.email, "email": u.email,
                "avatar_url": u.avatar_url}
    return None


def _runs(db: Session, issue_ids: list[int]) -> dict[int, list[EmployeeTask]]:
    out: dict[int, list[EmployeeTask]] = {}
    if issue_ids:
        for t in db.scalars(select(EmployeeTask).where(EmployeeTask.issue_id.in_(issue_ids)).order_by(EmployeeTask.id)):
            out.setdefault(t.issue_id, []).append(t)
    return out


def _run_state(runs: list[EmployeeTask]) -> dict | None:
    if not runs:
        return None
    active = [t for t in runs if t.status not in tasksmod.TERMINAL]
    last = runs[-1]
    cur = active[-1] if active else last
    return {"count": len(runs), "active": bool(active), "task_id": cur.id, "status": cur.status,
            "waiting_decision": any(t.status == "waiting_human" for t in active),
            "cost_usd": round(sum(t.cost_usd or 0 for t in runs), 4)}


def issue_dicts(db: Session, acc: Access, rows: list[Issue], detail: bool = False) -> list[dict]:
    pfx = prefix(db)
    people = _people(db, [x for i in rows for x in (i.assignee_user_id, i.creator_user_id)])
    emps = _employees(db, [x for i in rows for x in (i.assignee_employee_id, i.creator_employee_id)])
    lab = {x["id"]: x for x in labels(db)}
    teams = {t.id: t for t in db.scalars(select(Team).where(Team.id.in_({i.team_id for i in rows if i.team_id} or {-1})))}
    projects = {p.id: p for p in db.scalars(select(Project).where(Project.id.in_({i.project_id for i in rows if i.project_id} or {-1})))}
    ids = [i.id for i in rows]
    runs = _runs(db, ids)
    subs: dict[int, list[Issue]] = {}
    for c in db.scalars(select(Issue).where(Issue.parent_id.in_(ids or [-1]))):
        subs.setdefault(c.parent_id, []).append(c)
    comments = dict(db.execute(select(IssueComment.issue_id, func.count()).where(
        IssueComment.issue_id.in_(ids or [-1]), IssueComment.deleted.is_(False)).group_by(IssueComment.issue_id)).all())
    cats = {s["key"]: s["category"] for s in statuses(db, include_archived=True)}
    parents = {p.id: p for p in db.scalars(select(Issue).where(Issue.id.in_({i.parent_id for i in rows if i.parent_id} or {-1})))}
    out = []
    for i in rows:
        kids = subs.get(i.id, [])
        creator = (people.get(i.creator_user_id).email if i.creator_user_id in people else
                   (f"employee:{emps[i.creator_employee_id][1].slug}" if i.creator_employee_id in emps else ""))
        d = {"id": i.id, "key": f"{pfx}-{i.number}", "number": i.number, "title": i.title,
             "snippet": re.sub(r"\s+", " ", re.sub(r"[#*_`>\[\]()-]", " ", i.description or "")).strip()[:140],
             "status": i.status, "category": cats.get(i.status, "unstarted"), "priority": i.priority,
             "assignee": _assignee(i, people, emps), "creator": creator,
             "labels": [lab[x] for x in (i.labels or []) if x in lab],
             "team": {"id": i.team_id, "slug": teams[i.team_id].slug, "name": teams[i.team_id].name} if i.team_id in teams else None,
             "project": {"id": i.project_id, "slug": projects[i.project_id].slug, "name": projects[i.project_id].name}
             if i.project_id in projects else None,
             "parent": {"key": f"{pfx}-{parents[i.parent_id].number}", "title": parents[i.parent_id].title}
             if i.parent_id in parents else None,
             "stage": i.stage, "start_date": iso(tasksmod._aware(i.start_date)) if i.start_date else None,
             "due_date": iso(tasksmod._aware(i.due_date)) if i.due_date else None, "estimate": i.estimate,
             "position": i.position, "sub": {"total": len(kids), "done": sum(cats.get(k.status) in ("done", "closed") for k in kids)},
             "run": _run_state(runs.get(i.id, [])), "comments": comments.get(i.id, 0),
             "created_at": iso(tasksmod._aware(i.created_at)), "updated_at": iso(tasksmod._aware(i.updated_at)),
             "completed_at": iso(tasksmod._aware(i.completed_at)) if i.completed_at else None,
             "can_edit": can_edit(acc, i)}
        if detail:
            d.update(description=i.description, can_delete=can_delete(acc, i), subscribed=bool(acc.p.user_id and db.scalar(
                select(IssueSubscription.id).where(IssueSubscription.issue_id == i.id, IssueSubscription.user_id == acc.p.user_id))),
                sub_issues=issue_dicts(db, acc, sorted(kids, key=lambda k: (k.stage or 0, k.number))),
                relations=_relations(db, i, pfx), runs=[_run_dict(db, t) for t in runs.get(i.id, [])],
                subscribers=[u.name or u.email for u in _people(db, db.scalars(select(IssueSubscription.user_id).where(
                    IssueSubscription.issue_id == i.id))).values()],
                timeline=timeline(db, acc, i))
        out.append(d)
    return out


def _run_dict(db: Session, t: EmployeeTask) -> dict:
    e = db.get(Employee, t.employee_id)
    a = db.get(Agent, e.agent_id) if e else None
    return {"task_id": t.id, "employee": a.slug if a else "", "employee_name": a.name if a else "", "status": t.status,
            "title": t.title, "cost_usd": round(t.cost_usd or 0, 4), "requester": t.requester,
            "created_at": iso(tasksmod._aware(t.created_at)),
            "finished_at": iso(tasksmod._aware(t.finished_at)) if t.finished_at else None}


def _relations(db: Session, i: Issue, pfx: str) -> list[dict]:
    out = []
    for r in db.scalars(select(IssueRelation).where(or_(IssueRelation.issue_id == i.id, IssueRelation.other_id == i.id))):
        mine = r.issue_id == i.id
        other = db.get(Issue, r.other_id if mine else r.issue_id)
        if other is None:
            continue
        kind = r.kind if mine or r.kind != "blocks" else "blocked_by"
        out.append({"id": r.id, "kind": kind, "key": f"{pfx}-{other.number}", "title": other.title, "status": other.status})
    return out


def list_issues(db: Session, acc: Access, team: str = "", project: str = "", assignee: str = "", status: str = "",
                priority: str = "", label: str = "", q: str = "", parent: str = "", limit: int = 1000) -> list[dict]:
    query = select(Issue)
    if team:
        query = query.where(Issue.team_id == find_team(db, team).id)
    if project:
        from .goals import find_project
        query = query.where(Issue.project_id == find_project(db, acc, project).id)
    if assignee == "me":
        query = query.where(Issue.assignee_user_id == (acc.p.user_id or -1))
    elif assignee == "none":
        query = query.where(Issue.assignee_user_id.is_(None), Issue.assignee_employee_id.is_(None))
    elif assignee.startswith("employee:"):
        e = _employee_by_slug(db, assignee[9:])
        query = query.where(Issue.assignee_employee_id == (e.id if e else -1))
    elif assignee.startswith("user:"):
        query = query.where(Issue.assignee_user_id == int(assignee[5:] or -1))
    if status:
        query = query.where(Issue.status.in_([s for s in status.split(",") if s]))
    if priority:
        query = query.where(Issue.priority.in_([s for s in priority.split(",") if s]))
    if parent:
        p = _by_ref(db, parent)
        query = query.where(Issue.parent_id == (p.id if p else -1))
    if q:
        like = f"%{q.strip()}%"
        m = re.fullmatch(r"(?:[A-Za-z][A-Za-z0-9]*-)?(\d+)", q.strip())
        query = query.where(or_(Issue.title.ilike(like), Issue.description.ilike(like),
                                *( [Issue.number == int(m.group(1))] if m else [])))
    rows = [i for i in db.scalars(query.order_by(Issue.position, Issue.number.desc()).limit(limit * 2)) if can_view(acc, i)]
    if label:
        want = {int(x) for x in label.split(",") if x.isdigit()}
        rows = [i for i in rows if want & set(i.labels or [])]
    return issue_dicts(db, acc, rows[:limit])


def get_issue(db: Session, acc: Access, ref) -> dict:
    i = find_issue(db, acc, ref)
    return issue_dicts(db, acc, [i], detail=True)[0]


def assignees(db: Session, acc: Access, team: str = "") -> dict:
    """Quem pode receber uma issue: as pessoas do time (sem time: as dos meus times) e os Digital employees a quem
    eu posso entregar trabalho (do time, quando há time). Também serve para as @menções."""
    from .employees import find
    tid = find_team(db, team).id if team else None
    team_ids = [tid] if tid else (list(acc.teams) if not acc.p.is_admin else [t.id for t in db.scalars(select(Team))])
    uids = set(db.scalars(select(TeamMember.user_id).where(TeamMember.team_id.in_(team_ids or [-1]))))
    if acc.p.user_id:
        uids.add(acc.p.user_id)
    people = [{"ref": f"user:{u.id}", "id": u.id, "name": u.name or u.email, "email": u.email, "avatar_url": u.avatar_url}
              for u in sorted(_people(db, uids).values(), key=lambda u: (u.name or u.email).lower()) if u.active]
    emps = []
    for e, a in db.execute(select(Employee, Agent).join(Agent, Agent.id == Employee.agent_id).where(
            Employee.status.in_(("probation", "active", "paused")))).all():
        if tid and e.team_id and e.team_id != tid:
            continue
        try:
            find(db, acc, a.slug, "assign")
        except (Forbidden, PlatformError):
            continue
        emps.append({"ref": f"employee:{a.slug}", "slug": a.slug, "name": a.name, "title": e.title, "status": e.status,
                     "personal": bool(a.personal_owner_id), "team_id": e.team_id})
    return {"people": people, "employees": sorted(emps, key=lambda x: x["name"].lower())}


# ------------------------------------------------------------------ escrita
def _employee_by_slug(db: Session, slug: str) -> Employee | None:
    a = db.scalar(select(Agent).where(Agent.slug == slug))
    return db.scalar(select(Employee).where(Employee.agent_id == a.id)) if a else None


def _actor(acc: Access | None, e: Employee | None = None, db: Session | None = None) -> tuple[str, int | None]:
    if e is not None and db is not None:
        a = db.get(Agent, e.agent_id)
        return f"employee:{a.slug if a else e.id}", None
    return (acc.p.name if acc else "sistema"), (acc.p.user_id if acc else None)


def activity(db: Session, i: Issue, actor: str, actor_user_id: int | None, kind: str, data: dict | None = None):
    db.add(IssueActivity(issue_id=i.id, actor=actor, actor_user_id=actor_user_id, kind=kind, data=data or {},
                         created_at=now()))
    i.updated_at = now()


def subscribe(db: Session, i: Issue, user_id: int | None):
    if user_id and not db.scalar(select(IssueSubscription.id).where(IssueSubscription.issue_id == i.id,
                                                                     IssueSubscription.user_id == user_id)):
        db.add(IssueSubscription(issue_id=i.id, user_id=user_id, created_at=now()))


def set_subscription(db: Session, acc: Access, ref, on: bool) -> dict:
    i = find_issue(db, acc, ref)
    if not acc.p.user_id:
        raise PlatformError("inscrição é de uma pessoa")
    s = db.scalar(select(IssueSubscription).where(IssueSubscription.issue_id == i.id, IssueSubscription.user_id == acc.p.user_id))
    if on:
        subscribe(db, i, acc.p.user_id)
    elif s:
        db.delete(s)
    db.commit()
    return {"subscribed": on}


def _date(v) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise PlatformError(f"data inválida: {v} (AAAA-MM-DD)") from None
    return d if d.tzinfo else d.replace(tzinfo=tasksmod._aware(now()).tzinfo)


def _resolve_assignee(db: Session, acc: Access | None, ref: str, team_id: int | None) -> tuple[int | None, int | None]:
    """(user_id, employee_id). ref: "" | "me" | user:<id ou e-mail> | employee:<slug> | <e-mail> | <slug>."""
    ref = (ref or "").strip()
    if not ref:
        return None, None
    if ref == "me":
        if not acc or not acc.p.user_id:
            raise PlatformError("'me' só para uma pessoa logada")
        return acc.p.user_id, None
    if ref.startswith("user:") or "@" in ref:
        v = ref[5:] if ref.startswith("user:") else ref
        u = db.get(User, int(v)) if v.isdigit() else db.scalar(select(User).where(User.email == v.lower()))
        if u is None or not u.active:
            raise PlatformError(f"pessoa '{v}' não encontrada")
        return u.id, None
    slug = ref[9:] if ref.startswith("employee:") else ref
    e = _employee_by_slug(db, slug)
    if e is None:
        raise PlatformError(f"'{slug}' não é um Digital employee")
    if acc is not None:
        from .employees import find
        find(db, acc, slug, "assign")  # quem atribui precisa poder entregar trabalho a ele
    if e.status not in ("probation", "active", "paused"):
        raise PlatformError(f"'{slug}' ainda não trabalha (status {e.status})")
    if team_id and e.team_id and e.team_id != team_id:
        raise PlatformError("o Digital employee é de outro time: mova a issue para o time dele ou escolha outro")
    return None, e.id


def _next_number(db: Session) -> int:
    return (db.scalar(select(func.max(Issue.number))) or 0) + 1


def create(db: Session, acc: Access | None, title: str, description: str = "", team: str = "", project: str = "",
           status: str = "", priority: str = "none", assignee: str = "", labels: list | None = None, parent: str = "",
           stage: int | None = None, start_date=None, due_date=None, estimate: int | None = None, start: bool = True,
           by_employee: Employee | None = None, routine_id: int | None = None) -> dict:
    title = (title or "").strip()
    if not title:
        raise PlatformError("a issue precisa de um título")
    ensure_statuses(db)
    par = None
    if parent:
        par = _by_ref(db, parent)
        if par is None or (acc is not None and not can_view(acc, par)):
            raise PlatformError(f"issue mãe '{parent}' não encontrada")
    team_id = None
    if team:
        t = find_team(db, team)
        if acc is not None and not (acc.p.is_admin or acc.team_role(t.id)):
            raise Forbidden(f"você não é do time '{t.slug}'")
        team_id = t.id
    elif par is not None:
        team_id = par.team_id
    elif acc is not None and len(acc.teams) == 1:
        team_id = next(iter(acc.teams))
    proj = None
    if project:
        from .goals import find_project
        proj = find_project(db, acc, project) if acc else db.scalar(select(Project).where(Project.slug == project))
        if proj and proj.team_id and team_id and proj.team_id != team_id:
            raise PlatformError("o projeto é de outro time")
    elif par is not None:
        proj = db.get(Project, par.project_id) if par.project_id else None
    if priority not in PRIORITIES:
        raise PlatformError(f"prioridade: {' | '.join(PRIORITIES)}")
    uid, eid = _resolve_assignee(db, acc, assignee, team_id)
    status = status or ("todo" if eid else "backlog")
    _status(db, status)
    actor, actor_uid = _actor(acc, by_employee, db)
    first = db.scalar(select(func.min(Issue.position)).where(Issue.status == status))
    for attempt in range(5):
        i = Issue(number=_next_number(db), team_id=team_id, project_id=proj.id if proj else None,
                  parent_id=par.id if par else None, stage=int(stage) if stage else None, title=title[:300],
                  description=(description or "")[:MAX_BODY], status=status, priority=priority,
                  assignee_user_id=uid, assignee_employee_id=eid, creator_user_id=acc.p.user_id if acc and not by_employee else None,
                  creator_employee_id=by_employee.id if by_employee else None, labels=_label_ids(db, labels),
                  start_date=_date(start_date), due_date=_date(due_date), estimate=int(estimate) if estimate not in (None, "") else None,
                  position=(first - 1) if first is not None else 0, routine_id=routine_id, created_at=now(), updated_at=now())
        db.add(i)
        try:
            db.flush()
            break
        except IntegrityError:  # número disputado com outra criação: tenta o próximo
            db.rollback()
            if attempt == 4:
                raise
    activity(db, i, actor, actor_uid, "created", {"status": status})
    subscribe(db, i, actor_uid)
    subscribe(db, i, uid)
    if category(db, status) in ("done", "closed"):
        i.completed_at = now()
    db.commit()
    audit(db, actor, "issue.create", key_of(db, i), title[:120])
    run = None
    if eid and start and status != "backlog":
        run = start_run(db, acc, i, by_employee=by_employee)
    return {**issue_dicts(db, acc or _system_acc(db), [i])[0], "run_started": bool(run)}


def _label_ids(db: Session, values) -> list[int]:
    out = []
    for v in values or []:
        if isinstance(v, int) or str(v).isdigit():
            x = db.get(IssueLabel, int(v))
        else:
            x = db.scalar(select(IssueLabel).where(func.lower(IssueLabel.name) == str(v).strip().lower()))
        if x is None:
            raise PlatformError(f"etiqueta '{v}' não existe")
        if x.id not in out:
            out.append(x.id)
    return out


def update(db: Session, acc: Access | None, ref, changes: dict, start: bool | None = None,
           by_employee: Employee | None = None) -> dict:
    i = find_issue(db, acc, ref, write=True) if acc is not None and by_employee is None else _by_ref(db, ref)
    if i is None:
        raise PlatformError(f"issue '{ref}' não encontrada")
    actor, actor_uid = _actor(acc, by_employee, db)
    was_status, was_emp = i.status, i.assignee_employee_id
    log = []
    if "title" in changes:
        t = str(changes["title"] or "").strip()
        if not t:
            raise PlatformError("a issue precisa de um título")
        if t != i.title:
            log.append(("title", {"from": i.title, "to": t[:300]}))
            i.title = t[:300]
    if "description" in changes and (changes["description"] or "") != i.description:
        i.description = str(changes["description"] or "")[:MAX_BODY]
        log.append(("description", {}))
    if "status" in changes and changes["status"] != i.status:
        st = _status(db, str(changes["status"]))
        if by_employee is not None and st.category not in AGENT_STATUSES:
            raise PlatformError("o Digital employee não conclui nem cancela a issue: deixe em revisão (in_review) "
                                "e quem revisa decide")
        if st.archived:
            raise PlatformError(f"o status '{st.key}' está arquivado")
        log.append(("status", {"from": i.status, "to": st.key}))
        i.status = st.key
        i.completed_at = now() if st.category in ("done", "closed") else None
    if "priority" in changes and changes["priority"] != i.priority:
        if changes["priority"] not in PRIORITIES:
            raise PlatformError(f"prioridade: {' | '.join(PRIORITIES)}")
        log.append(("priority", {"from": i.priority, "to": changes["priority"]}))
        i.priority = changes["priority"]
    if "team" in changes:
        tid = find_team(db, changes["team"]).id if changes["team"] else None
        if tid != i.team_id:
            if acc is not None and tid and not (acc.p.is_admin or acc.team_role(tid)):
                raise Forbidden("você não é desse time")
            log.append(("team", {"to": changes["team"] or None}))
            i.team_id = tid
    if "assignee" in changes:
        uid, eid = _resolve_assignee(db, acc if by_employee is None else None, str(changes["assignee"] or ""), i.team_id)
        if (uid, eid) != (i.assignee_user_id, i.assignee_employee_id):
            log.append(("assignee", {"to": str(changes["assignee"] or "") or None}))
            i.assignee_user_id, i.assignee_employee_id = uid, eid
            subscribe(db, i, uid)
    if "labels" in changes:
        ids = _label_ids(db, changes["labels"])
        if ids != (i.labels or []):
            log.append(("labels", {"to": ids}))
            i.labels = ids
    if "project" in changes:
        pid = None
        if changes["project"]:
            from .goals import find_project
            p = find_project(db, acc, changes["project"]) if acc else db.scalar(select(Project).where(Project.slug == changes["project"]))
            if p and p.team_id and i.team_id and p.team_id != i.team_id:
                raise PlatformError("o projeto é de outro time")
            pid = p.id if p else None
        if pid != i.project_id:
            log.append(("project", {"to": changes["project"] or None}))
            i.project_id = pid
    if "parent" in changes:
        pid = None
        if changes["parent"]:
            p = _by_ref(db, changes["parent"])
            if p is None:
                raise PlatformError(f"issue '{changes['parent']}' não encontrada")
            cur = p
            while cur is not None:  # sem ciclo: a mãe não pode ser a própria issue nem uma filha dela
                if cur.id == i.id:
                    raise PlatformError("isso criaria um ciclo de sub-issues")
                cur = db.get(Issue, cur.parent_id) if cur.parent_id else None
            pid = p.id
        if pid != i.parent_id:
            log.append(("parent", {"to": changes["parent"] or None}))
            i.parent_id = pid
    for k in ("stage", "estimate"):
        if k in changes:
            v = int(changes[k]) if changes[k] not in (None, "") else None
            if v != getattr(i, k):
                log.append((k, {"to": v}))
                setattr(i, k, v)
    for k in ("start_date", "due_date"):
        if k in changes:
            v = _date(changes[k])
            if (tasksmod._aware(v) if v else None) != (tasksmod._aware(getattr(i, k)) if getattr(i, k) else None):
                log.append((k, {"to": changes[k] or None}))
                setattr(i, k, v)
    if "position" in changes:
        i.position = float(changes["position"])
        i.updated_at = now()
    for kind, data in log:
        activity(db, i, actor, actor_uid, kind, data)
    db.commit()
    if log:
        audit(db, actor, "issue.update", key_of(db, i), ", ".join(k for k, _ in log))
    run = None
    out_of_backlog = was_status == "backlog" and i.status != "backlog"
    newly_assigned = i.assignee_employee_id and i.assignee_employee_id != was_emp
    unstarted = category(db, i.status) == "unstarted" and i.status != "backlog"
    if i.assignee_employee_id and start is not False and by_employee is None and i.status != "backlog" and (
            newly_assigned or out_of_backlog or (start is True and unstarted)):
        run = start_run(db, acc, i)
    if "status" in changes and category(db, i.status) in ("done", "closed") and i.parent_id:
        stage_check(db, db.get(Issue, i.parent_id))
    return {**issue_dicts(db, acc or _system_acc(db), [i])[0], "run_started": bool(run)}


def delete(db: Session, acc: Access, ref) -> dict:
    i = find_issue(db, acc, ref)
    if not can_delete(acc, i):
        raise Forbidden("apagam a issue: quem criou, um mantenedor do time ou o admin (para manter o histórico, cancele)")
    k = key_of(db, i)
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.issue_id == i.id)):
        if t.status not in tasksmod.TERMINAL:
            t.status, t.finished_at, t.error = "cancelled", now(), f"issue {k} apagada por {acc.p.name}"
            for r in db.scalars(select(HumanRequest).where(HumanRequest.task_id == t.id, HumanRequest.status == "open")):
                r.status, r.decided_at, r.decided_by = "cancelled", now(), acc.p.name
        t.issue_id = None
    for model in (IssueComment, IssueActivity, IssueSubscription):
        for x in db.scalars(select(model).where(model.issue_id == i.id)):
            db.delete(x)
    for r in db.scalars(select(IssueRelation).where(or_(IssueRelation.issue_id == i.id, IssueRelation.other_id == i.id))):
        db.delete(r)
    for c in db.scalars(select(Issue).where(Issue.parent_id == i.id)):
        c.parent_id = None
    db.delete(i)
    db.commit()
    audit(db, acc.p.name, "issue.delete", k, "")
    return {"ok": True, "key": k}


def relate(db: Session, acc: Access, ref, kind: str, other: str) -> dict:
    i = find_issue(db, acc, ref, write=True)
    o = find_issue(db, acc, other)
    if kind not in RELATIONS and kind != "blocked_by":
        raise PlatformError("relação: blocks | blocked_by | related | duplicate")
    if o.id == i.id:
        raise PlatformError("a issue não se relaciona com ela mesma")
    a, b, k = (o, i, "blocks") if kind == "blocked_by" else (i, o, kind)
    if not db.scalar(select(IssueRelation.id).where(IssueRelation.issue_id == a.id, IssueRelation.other_id == b.id,
                                                    IssueRelation.kind == k)):
        db.add(IssueRelation(issue_id=a.id, kind=k, other_id=b.id, created_at=now()))
    activity(db, i, acc.p.name, acc.p.user_id, "relation", {"kind": kind, "other": key_of(db, o)})
    db.commit()
    return {"ok": True}


def unrelate(db: Session, acc: Access, ref, relation_id: int) -> dict:
    i = find_issue(db, acc, ref, write=True)
    r = db.get(IssueRelation, relation_id)
    if r is None or i.id not in (r.issue_id, r.other_id):
        raise PlatformError("relação não encontrada")
    db.delete(r)
    activity(db, i, acc.p.name, acc.p.user_id, "unrelation", {"kind": r.kind})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ comentários e menções
def _mentions(body: str) -> list[tuple[str, str]]:
    return [(m.group(2), m.group(3)) for m in MENTION.finditer(body or "")]


def comment_dict(db: Session, c: IssueComment, acc: Access, people: dict, emps: dict) -> dict:
    if c.author_employee_id and c.author_employee_id in emps:
        e, a = emps[c.author_employee_id]
        author = {"kind": "employee", "slug": a.slug, "name": a.name}
    else:
        u = people.get(c.author_user_id)
        author = {"kind": "user", "id": c.author_user_id, "name": (u.name or u.email) if u else "—",
                  "avatar_url": u.avatar_url if u else ""}
    mine = bool(acc.p.user_id and c.author_user_id == acc.p.user_id)
    return {"id": c.id, "parent_id": c.parent_id, "author": author, "body": "" if c.deleted else c.body,
            "deleted": c.deleted, "reactions": {k: len(v) for k, v in (c.reactions or {}).items() if v},
            "my_reactions": [k for k, v in (c.reactions or {}).items() if acc.p.user_id in v],
            "resolved": bool(c.resolved_at), "resolved_by": c.resolved_by, "edited": bool(c.edited_at),
            "task_id": c.task_id, "created_at": iso(tasksmod._aware(c.created_at)),
            "can_edit": not c.deleted and not c.author_employee_id and (mine or acc.p.is_admin),
            "can_delete": not c.deleted and (mine or acc.p.is_admin)}


def timeline(db: Session, acc: Access, i: Issue) -> list[dict]:
    cs = list(db.scalars(select(IssueComment).where(IssueComment.issue_id == i.id).order_by(IssueComment.id)))
    acts = list(db.scalars(select(IssueActivity).where(IssueActivity.issue_id == i.id).order_by(IssueActivity.id)))
    people = _people(db, [c.author_user_id for c in cs])
    emps = _employees(db, [c.author_employee_id for c in cs])
    out = [{"type": "comment", **comment_dict(db, c, acc, people, emps)} for c in cs]
    out += [{"type": "activity", "id": a.id, "actor": a.actor, "kind": a.kind, "data": a.data or {},
             "created_at": iso(tasksmod._aware(a.created_at))} for a in acts if a.kind != "comment"]
    return sorted(out, key=lambda x: x["created_at"] or "")


def add_comment(db: Session, acc: Access, ref, body: str, parent_id: int | None = None) -> dict:
    i = find_issue(db, acc, ref)
    if not acc.p.user_id:
        raise PlatformError("comentários são de pessoas (entre com a sua conta)")
    if not can_edit(acc, i):
        raise Forbidden("sem permissão para comentar nesta issue")
    body = (body or "").strip()
    if not body:
        raise PlatformError("escreva o comentário")
    if len(body) > MAX_BODY:
        raise PlatformError(f"comentário longo demais (máx. {MAX_BODY})")
    root = None
    if parent_id:
        root = db.get(IssueComment, parent_id)
        if root is None or root.issue_id != i.id:
            raise PlatformError("discussão não encontrada")
        while root.parent_id:
            root = db.get(IssueComment, root.parent_id)
        root.resolved_at, root.resolved_by = None, ""  # responder reabre a discussão
    c = IssueComment(issue_id=i.id, parent_id=root.id if root else None, author_user_id=acc.p.user_id, body=body,
                     created_at=now())
    db.add(c)
    db.flush()
    activity(db, i, acc.p.name, acc.p.user_id, "comment", {"comment": c.id})
    subscribe(db, i, acc.p.user_id)
    db.commit()
    started = _act_on_mentions(db, acc, i, c, set())
    if not started and i.assignee_employee_id:  # conversa com o responsável: vai para a rodada em andamento (ou começa uma)
        _follow_up(db, acc, i, c)
    return {"id": c.id, "runs_started": started}


def _act_on_mentions(db: Session, acc: Access, i: Issue, c: IssueComment, before: set) -> list[str]:
    started = []
    for kind, ref in _mentions(c.body):
        if (kind, ref) in before:
            continue
        if kind == "user":
            u = db.get(User, int(ref)) if ref.isdigit() else None
            if u is not None and u.active:
                subscribe(db, i, u.id)
                activity(db, i, acc.p.name, acc.p.user_id, "mention", {"user": u.email, "comment": c.id})
        elif kind == "employee":
            e = _employee_by_slug(db, ref)
            if e is None:
                continue
            t = start_run(db, acc, i, request=c.body, employee=e, comment=c)
            if t is not None:
                started.append(ref)
    db.commit()
    return started


def _follow_up(db: Session, acc: Access, i: Issue, c: IssueComment):
    active = db.scalar(select(EmployeeTask).where(EmployeeTask.issue_id == i.id, EmployeeTask.employee_id == i.assignee_employee_id,
                                                  EmployeeTask.status.not_in(tasksmod.TERMINAL)).order_by(EmployeeTask.id.desc()))
    if active is not None:
        from . import task_thread
        try:
            task_thread.post(db, acc, active.id, f"[{key_of(db, i)} · comentário de {acc.p.name}]\n{c.body}")
            c.task_id = active.id
            db.commit()
        except (PlatformError, Forbidden):
            pass
        return
    if i.status != "backlog" and category(db, i.status) not in ("done", "closed"):
        start_run(db, acc, i, request=c.body, comment=c)


def edit_comment(db: Session, acc: Access, ref, comment_id: int, body: str) -> dict:
    i = find_issue(db, acc, ref)
    c = db.get(IssueComment, comment_id)
    if c is None or c.issue_id != i.id or c.deleted:
        raise PlatformError("comentário não encontrado")
    if c.author_employee_id:
        raise Forbidden("comentários de Digital employees não se editam (só se apagam)")
    if not (acc.p.is_admin or (acc.p.user_id and acc.p.user_id == c.author_user_id)):
        raise Forbidden("só quem escreveu (ou o admin) edita")
    body = (body or "").strip()
    if not body:
        raise PlatformError("o comentário ficou vazio: apague em vez de editar")
    before = set(_mentions(c.body))
    c.body, c.edited_at = body[:MAX_BODY], now()
    db.commit()
    started = _act_on_mentions(db, acc, i, c, before)  # menção nova numa edição também aciona
    return {"ok": True, "runs_started": started}


def delete_comment(db: Session, acc: Access, ref, comment_id: int) -> dict:
    i = find_issue(db, acc, ref)
    c = db.get(IssueComment, comment_id)
    if c is None or c.issue_id != i.id:
        raise PlatformError("comentário não encontrado")
    if not (acc.p.is_admin or (acc.p.user_id and acc.p.user_id == c.author_user_id)):
        raise Forbidden("só quem escreveu (ou o admin) apaga")
    has_replies = db.scalar(select(IssueComment.id).where(IssueComment.parent_id == c.id))
    if has_replies:
        c.deleted, c.body = True, ""  # a discussão fica, com "comentário apagado" no lugar
    else:
        db.delete(c)
    activity(db, i, acc.p.name, acc.p.user_id, "comment_deleted", {})
    db.commit()
    return {"ok": True}


def resolve_comment(db: Session, acc: Access, ref, comment_id: int, resolved: bool = True) -> dict:
    i = find_issue(db, acc, ref, write=True)
    c = db.get(IssueComment, comment_id)
    if c is None or c.issue_id != i.id or c.parent_id:
        raise PlatformError("só a discussão (o primeiro comentário) é resolvida")
    c.resolved_at, c.resolved_by = (now(), acc.p.name) if resolved else (None, "")
    db.commit()
    return {"resolved": resolved}


def react(db: Session, acc: Access, ref, comment_id: int, emoji: str) -> dict:
    i = find_issue(db, acc, ref)
    c = db.get(IssueComment, comment_id)
    if c is None or c.issue_id != i.id or c.deleted:
        raise PlatformError("comentário não encontrado")
    if emoji not in REACTIONS or not acc.p.user_id:
        raise PlatformError(f"reação: {' '.join(REACTIONS)}")
    r = {k: list(v) for k, v in (c.reactions or {}).items()}
    who = r.setdefault(emoji, [])
    if acc.p.user_id in who:
        who.remove(acc.p.user_id)
    else:
        who.append(acc.p.user_id)
    c.reactions = {k: v for k, v in r.items() if v}
    db.commit()
    return {"reactions": {k: len(v) for k, v in c.reactions.items()}}


# ------------------------------------------------------------------ rodadas (o Digital employee trabalhando na issue)
def _system_acc(db: Session) -> Access:
    from .. import auth
    return Access(db, auth.Principal("sistema", {"admin"}, via="internal"))


def _context(db: Session, i: Issue, request: str, en: bool) -> str:
    k = key_of(db, i)
    cs = list(db.scalars(select(IssueComment).where(IssueComment.issue_id == i.id, IssueComment.deleted.is_(False))
                         .order_by(IssueComment.id.desc()).limit(6)))[::-1]
    people = _people(db, [c.author_user_id for c in cs])
    emps = _employees(db, [c.author_employee_id for c in cs])
    lines = [f"Issue {k}: {i.title}",
             f"Status: {i.status} · prioridade: {i.priority}" + (f" · prazo: {tasksmod._aware(i.due_date).date()}" if i.due_date else "")]
    if i.description:
        lines += ["", "Descrição:", i.description[:6000]]
    kids = list(db.scalars(select(Issue).where(Issue.parent_id == i.id).order_by(Issue.stage, Issue.number)))
    if kids:
        lines += ["", "Sub-issues:"] + [f"- {key_of(db, x)} [{x.status}]{f' etapa {x.stage}' if x.stage else ''}: {x.title}" for x in kids]
    if i.parent_id:
        p = db.get(Issue, i.parent_id)
        if p:
            lines += ["", f"Issue mãe: {key_of(db, p)}: {p.title}"]
    if cs:
        lines += ["", "Últimos comentários:"]
        for c in cs:
            who = emps[c.author_employee_id][1].name if c.author_employee_id in emps else (
                (people[c.author_user_id].name or people[c.author_user_id].email) if c.author_user_id in people else "?")
            lines.append(f"- {who}: {MENTION.sub(lambda m: '@' + m.group(1), c.body)[:1200]}")
    if request:
        lines += ["", "Pedido agora:", MENTION.sub(lambda m: "@" + m.group(1), request)[:4000]]
    lines += ["", "Ferramentas desta issue: issue_comment (relatar o andamento e o que encontrou), issue_set_status "
                  "(in_progress, ou blocked com o motivo num comentário), create_sub_issue (dividir o trabalho; para outro "
                  "Digital employee passa pela alçada). Sua resposta final vira um comentário e a issue vai para "
                  "revisão: quem revisa conclui."]
    return "\n".join(lines)


def start_run(db: Session, acc: Access | None, i: Issue, request: str = "", employee: Employee | None = None,
              comment: IssueComment | None = None, by_employee: Employee | None = None) -> EmployeeTask | None:
    """Uma rodada do Digital employee nesta issue: uma tarefa com a issue como contexto. Quem pede é a pessoa que
    atribuiu, mencionou ou comentou — as permissões e a alçada valem como em qualquer tarefa."""
    e = employee or (db.get(Employee, i.assignee_employee_id) if i.assignee_employee_id else None)
    if e is None:
        return None
    a = db.get(Agent, e.agent_id)
    if acc is not None and by_employee is None and acc.p.user_id:
        from .employees import find
        find(db, acc, a.slug, "assign")
    if e.status not in ("probation", "active", "paused"):
        raise PlatformError(f"'{a.slug}' ainda não trabalha (status {e.status})")
    active = db.scalar(select(EmployeeTask).where(EmployeeTask.issue_id == i.id, EmployeeTask.employee_id == e.id,
                                                  EmployeeTask.status.not_in(tasksmod.TERMINAL)))
    if active is not None:  # já trabalhando nela: o pedido entra na conversa da rodada
        if request and acc is not None and acc.p.user_id:
            from . import task_thread
            try:
                task_thread.post(db, acc, active.id, f"[{key_of(db, i)}]\n{request}")
            except (PlatformError, Forbidden):
                pass
        return None
    k = key_of(db, i)
    requester = acc.p.name if acc else "sistema"
    uid = acc.p.user_id if acc else None
    if by_employee is not None:
        ba = db.get(Agent, by_employee.agent_id)
        requester = f"{ba.name if ba else 'Digital employee'} ({k})"
    t = tasksmod.create_task(db, e, f"{k}: {i.title}"[:300], _context(db, i, request, tasksmod.lang_of(e) == "en"), "issue",
                             requester, uid, TASK_PRIORITY.get(i.priority, 2), i.due_date, project_id=i.project_id)
    t.issue_id = i.id
    if comment is not None:
        comment.task_id = t.id
    activity(db, i, requester, uid, "run_started", {"task": t.id, "employee": a.slug, "employee_name": a.name})
    if category(db, i.status) == "unstarted":
        activity(db, i, "sistema", None, "status", {"from": i.status, "to": "in_progress"})
        i.status = "in_progress"
    db.commit()
    audit(db, requester, "issue.run", k, f"{a.slug} #{t.id}")
    return t


def agent_comment(db: Session, i: Issue, e: Employee, body: str, task_id: int | None = None) -> IssueComment:
    c = IssueComment(issue_id=i.id, author_employee_id=e.id, body=(body or "").strip()[:MAX_BODY], task_id=task_id,
                     created_at=now())
    db.add(c)
    db.flush()
    activity(db, i, _actor(None, e, db)[0], None, "comment", {"comment": c.id})
    return c


def task_finished(db: Session, task: EmployeeTask):
    """A rodada terminou: o resultado vira comentário; concluída vai para revisão, falha volta para "a fazer"."""
    if not task.issue_id:
        return
    i = db.get(Issue, task.issue_id)
    e = db.get(Employee, task.employee_id)
    if i is None or e is None:
        return
    actor = _actor(None, e, db)[0]
    moved_by_agent = db.scalar(select(IssueActivity.id).where(
        IssueActivity.issue_id == i.id, IssueActivity.actor == actor, IssueActivity.kind == "status",
        IssueActivity.created_at >= (task.started_at or task.created_at)))
    others = db.scalar(select(func.count()).select_from(EmployeeTask).where(
        EmployeeTask.issue_id == i.id, EmployeeTask.id != task.id, EmployeeTask.status.not_in(tasksmod.TERMINAL))) or 0
    if task.status == "done":
        if (task.result or "").strip():
            agent_comment(db, i, e, task.result, task.id)
        activity(db, i, actor, None, "run_done", {"task": task.id})
        if not moved_by_agent and not others and category(db, i.status) in ("unstarted", "started") and i.status != "blocked":
            activity(db, i, actor, None, "status", {"from": i.status, "to": "in_review"})
            i.status = "in_review"
    elif task.status in ("failed", "expired"):
        agent_comment(db, i, e, f"A rodada #{task.id} não terminou: {task.error or task.status}", task.id)
        activity(db, i, actor, None, "run_failed", {"task": task.id, "error": (task.error or "")[:300]})
        if not others and i.status == "in_progress":
            activity(db, i, "sistema", None, "status", {"from": i.status, "to": "todo"})
            i.status = "todo"
    elif task.status == "cancelled":
        activity(db, i, actor, None, "run_cancelled", {"task": task.id})
    i.updated_at = now()
    db.commit()


def stage_check(db: Session, parent: Issue | None):
    """Sub-issues em etapas: terminou a etapa mais antiga em aberto? Avisa a mãe (uma vez) e, se ela for de um
    Digital employee, ele acorda para decidir a próxima etapa."""
    if parent is None:
        return
    kids = list(db.scalars(select(Issue).where(Issue.parent_id == parent.id)))
    if not kids:
        return
    finished = lambda x: category(db, x.status) in ("done", "closed")  # noqa: E731
    stages = sorted({x.stage or 0 for x in kids})
    for s in stages:
        group = [x for x in kids if (x.stage or 0) == s]
        if not all(finished(x) for x in group):
            return
        announced = {(a.data or {}).get("stage") for a in db.scalars(select(IssueActivity).where(
            IssueActivity.issue_id == parent.id, IssueActivity.kind == "stage_done"))}
        if s in announced:
            continue
        later = [x for x in kids if (x.stage or 0) > s]
        activity(db, parent, "sistema", None, "stage_done", {"stage": s, "next": bool(later)})
        db.commit()
        if parent.assignee_employee_id:
            who = db.get(User, parent.creator_user_id) if parent.creator_user_id else None
            acc = Access(db, principal_for(who)) if who and who.active else _system_acc(db)
            msg = (f"A etapa {s or 'única'} das sub-issues terminou. " +
                   ("Veja os resultados e comece a próxima etapa." if later else "Todas as sub-issues terminaram: "
                    "consolide o resultado na issue."))
            try:
                start_run(db, acc, parent, request=msg)
            except (PlatformError, Forbidden):
                pass
        return


# ------------------------------------------------------------------ caixa de entrada (inscrições)
def inbox_items(db: Session, acc: Access) -> list[dict]:
    """Issues em que estou inscrito com atividade de outra pessoa (ou de um Digital employee)."""
    if not acc.p.user_id:
        return []
    ids = list(db.scalars(select(IssueSubscription.issue_id).where(IssueSubscription.user_id == acc.p.user_id)))
    if not ids:
        return []
    pfx = prefix(db)
    out = []
    since = now() - timedelta(days=60)
    for i in db.scalars(select(Issue).where(Issue.id.in_(ids), Issue.updated_at >= since)):
        last = db.scalar(select(IssueActivity).where(IssueActivity.issue_id == i.id, or_(
            IssueActivity.actor_user_id.is_(None), IssueActivity.actor_user_id != acc.p.user_id)).order_by(IssueActivity.id.desc()))
        if last is None:
            continue
        out.append({"issue": i, "key": f"{pfx}-{i.number}", "at": last.created_at, "actor": last.actor, "kind": last.kind,
                    "data": last.data or {}, "mentioned": (last.data or {}).get("user") == acc.p.name})
    return out


# ------------------------------------------------------------------ rotina que abre issue
def from_routine(db: Session, e: Employee, routine) -> EmployeeTask | None:
    a = db.get(Agent, e.agent_id)
    out = create(db, None, routine.title, routine.body or "", team="", status="todo",
                 priority={1: "high", 2: "medium", 3: "low"}.get(routine.priority, "medium"), assignee="",
                 routine_id=routine.id)
    i = db.get(Issue, out["id"])
    i.team_id, i.assignee_employee_id = e.team_id, e.id
    activity(db, i, f"rotina #{routine.id}", None, "assignee", {"to": f"employee:{a.slug if a else e.id}"})
    db.commit()
    return start_run(db, None, i)
