"""Organograma dos Digital employees: cada um pode ter um chefe (outro Digital employee do mesmo time). O gestor continua
sendo uma pessoa — o chefe é a linha de trabalho entre agentes.

- O chefe pode repassar trabalho para quem reporta a ele (delegação para baixo) e quem reporta pode repassar para o
  chefe (subir o que não é dele), além dos colegas liberados pelo gestor. Os repasses continuam passando pela alçada
  (`delegate`).
- O prompt de cada tarefa diz quem é o chefe e quem reporta a ele.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Agent, Employee, Team, User
from .access import Access
from .common import PlatformError, audit

MAX_DEPTH = 8


def _slug(db: Session, e: Employee | None) -> str | None:
    a = db.get(Agent, e.agent_id) if e else None
    return a.slug if a else None


def chain_up(db: Session, e: Employee) -> list[Employee]:
    out, seen, cur = [], {e.id}, e
    while cur.reports_to_id and len(out) < MAX_DEPTH:
        nxt = db.get(Employee, cur.reports_to_id)
        if nxt is None or nxt.id in seen:
            break
        out.append(nxt)
        seen.add(nxt.id)
        cur = nxt
    return out


def direct_reports(db: Session, e: Employee) -> list[Employee]:
    return list(db.scalars(select(Employee).where(Employee.reports_to_id == e.id, Employee.status != "offboarded")))


def handoff_targets(db: Session, e: Employee) -> list[str]:
    """Para quem ele pode repassar: os colegas liberados, o chefe e quem reporta a ele."""
    out = list(e.colleagues or [])
    chief = db.get(Employee, e.reports_to_id) if e.reports_to_id else None
    for x in ([chief] if chief else []) + direct_reports(db, e):
        s = _slug(db, x)
        if s and s not in out:
            out.append(s)
    return out


def set_chief(db: Session, e: Employee, chief_slug: str | None, who: str):
    if not chief_slug:
        e.reports_to_id = None
        return
    a = db.scalar(select(Agent).where(Agent.slug == chief_slug))
    chief = db.scalar(select(Employee).where(Employee.agent_id == a.id)) if a else None
    if chief is None:
        raise PlatformError(f"'{chief_slug}' não é um Digital employee")
    if chief.id == e.id:
        raise PlatformError("ele não reporta a si mesmo")
    if chief.team_id and e.team_id and chief.team_id != e.team_id:
        raise PlatformError("o chefe precisa ser do mesmo time")
    if any(x.id == e.id for x in chain_up(db, chief)):
        raise PlatformError("isso criaria um ciclo no organograma")
    e.reports_to_id = chief.id
    audit(db, who, "employee.reports_to", _slug(db, e) or "", chief_slug)


def prompt_lines(db: Session, e: Employee, en: bool = False) -> str:
    chief = db.get(Employee, e.reports_to_id) if e.reports_to_id else None
    reps = direct_reports(db, e)
    if not chief and not reps:
        return ""
    lines = []
    if chief:
        lines.append(f"You report to {_slug(db, chief)} ({chief.title}): hand work up to them with handoff_task when it "
                      "is not yours." if en else
                      f"Você reporta a {_slug(db, chief)} ({chief.title}): repasse para ele com handoff_task o que não é seu.")
    if reps:
        names = ", ".join(f"{_slug(db, r)} ({r.title})" for r in reps)
        lines.append(f"Reporting to you: {names}. Delegate to them with handoff_task." if en else
                     f"Reportam a você: {names}. Delegue para eles com handoff_task.")
    return "\n".join(lines)


def chart(db: Session, acc: Access, team: str | None = None, mine: bool = False) -> dict:
    """Os Digital employees que a pessoa vê, com o chefe (agente) e o gestor (pessoa) de cada um. mine=True (o
    organograma do portal): só os agentes da pessoa (pessoais, que ela gerencia ou de que é dona), com ela no topo."""
    from .employees import find
    q = select(Employee).where(Employee.status != "offboarded")
    if team:
        t = db.scalar(select(Team).where(Team.slug == team))
        q = q.where(Employee.team_id == (t.id if t else -1))
    people = {u.id: (u.name or u.email) for u in db.scalars(select(User))}
    teams = {t.id: t.slug for t in db.scalars(select(Team))}
    nodes = []
    for e in db.scalars(q):
        a = db.get(Agent, e.agent_id)
        if a is None:
            continue
        uid = acc.p.user_id
        if mine and not (uid and (a.personal_owner_id == uid or uid in (e.manager_user_id, e.owner_user_id))):
            continue
        try:
            find(db, acc, a.slug)
        except Exception:  # noqa: BLE001
            continue
        chief = db.get(Employee, e.reports_to_id) if e.reports_to_id else None
        nodes.append({"slug": a.slug, "name": a.name, "title": e.title, "status": e.status, "level": e.autonomy_level,
                      "team": teams.get(e.team_id), "manager": people.get(e.manager_user_id, ""),
                      "reports_to": _slug(db, chief), **card(db, acc, e, a, people)})
    if not mine:
        return {"nodes": nodes}
    u = db.get(User, acc.p.user_id) if acc.p.user_id else None
    slugs = {n["slug"] for n in nodes}
    for n in nodes:  # o chefe fora do meu organograma: o agente fica direto abaixo de mim
        if n["reports_to"] not in slugs:
            n["reports_to"] = None
    from .workspace import built_agents  # os agentes comuns dela também ficam abaixo dela (não são arrastáveis)
    return {"nodes": nodes, "agents": built_agents(db, acc) if acc.p.user_id else [], "me": {"name": (u.name or u.email) if u else acc.p.name, "email": u.email if u else "",
                                   "avatar_url": u.avatar_url if u else "", "role": "CEO" if acc.p.is_admin else "Staff"}}


def card(db: Session, acc: Access, e: Employee, a: Agent, people: dict) -> dict:
    """O que o cartão do organograma mostra: o modelo, o harness dos especialistas, se é pessoal, o que faz agora."""
    from ..models import EmployeeTask
    from .common import spec_of
    from .employees import find
    spec = spec_of(a) or {}
    llm = spec.get("llm") or {}
    specialists = []
    for s in spec.get("sub_agents") or []:
        sa = db.scalar(select(Agent).where(Agent.slug == s))
        if sa is not None:
            h = (spec_of(sa) or {}).get("harness") or {}
            specialists.append({"slug": sa.slug, "name": sa.name, "harness": h.get("id") or h.get("name") or ""})
    running = db.scalar(select(EmployeeTask).where(EmployeeTask.employee_id == e.id, EmployeeTask.status == "in_progress")
                        .order_by(EmployeeTask.id.desc()))
    try:
        find(db, acc, a.slug, "manage")
        manage = True
    except Exception:  # noqa: BLE001
        manage = False
    model = llm.get("model") or ""
    if not model and llm.get("connection"):  # o modelo da conexão (o mesmo que o deploy usa)
        from ..models import LlmConnection
        conn = db.scalar(select(LlmConnection).where(LlmConnection.name == llm["connection"]))
        model = conn.model_name if conn else ""
    from .. import config
    return {"model": model or config.DEFAULT_MODEL, "connection": llm.get("connection") or "",
            "harness": ((spec.get("harness") or {}).get("id") or "hangar"), "specialists": specialists,
            "personal": bool(a.personal_owner_id), "owner": people.get(a.personal_owner_id, "") if a.personal_owner_id else "",
            "can_manage": manage, "working_on": {"id": running.id, "title": running.title} if running else None}
