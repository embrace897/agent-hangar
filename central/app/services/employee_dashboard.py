"""Painel do gestor: custo, tempo de decisão, % no prazo, qualidade e gargalos por Digital employee e por time, num
lugar só. Entra quem a pessoa gerencia (gestor, dono, substituto ou mantenedor do time); admins e auditores veem todos.
"""
from datetime import timedelta

from sqlalchemy import select

from ..models import Agent, Employee, EmployeeTask, HumanRequest, Team, User, now
from . import employee_tasks as tasksmod
from .access import Access, Forbidden
from .common import PlatformError, iso

OPEN = ("new", "in_progress", "waiting_human", "waiting_task", "waiting_budget")
FAIL_ALERT = 25  # % de falha (com 4+ tarefas terminadas) que aparece como gargalo


def _mine(db, acc: Access, team_id: int | None) -> list[tuple[Employee, Agent]]:
    from .employees import find
    out = []
    q = select(Employee).where(Employee.status != "offboarded")
    if team_id:
        q = q.where(Employee.team_id == team_id)
    for e in db.scalars(q):
        a = db.get(Agent, e.agent_id)
        if a is None:
            continue
        if not acc.p.is_auditor and not (acc.p.user_id and acc.p.user_id in (e.backup_user_ids or [])):
            try:
                find(db, acc, a.slug, "manage")
            except (Forbidden, PlatformError):
                continue
        out.append((e, a))
    return out


def _mins(a, b) -> float:
    return (tasksmod._aware(b) - tasksmod._aware(a)).total_seconds() / 60


def dashboard(db, acc: Access, days: int = 30, team: str | None = None) -> dict:
    from .access import find_team
    from .employee_quality import stats as quality_stats
    from .employee_work import on_time_counts
    from .employees import metrics
    days = max(1, min(int(days or 30), 180))
    team_id = find_team(db, team).id if team else None
    t0, since = now(), now() - timedelta(days=days)
    people = {u.id: (u.name or u.email) for u in db.scalars(select(User))}
    teams = {t.id: t.slug for t in db.scalars(select(Team))}
    rows, decisions, overdue, stuck = [], [], [], []
    holders: dict[int, dict] = {}
    for e, a in _mine(db, acc, team_id):
        m = metrics(db, e, days)
        on_time, timed = on_time_counts(db, e, since)
        q = quality_stats(db, e, days)
        open_tasks = list(db.scalars(select(EmployeeTask).where(EmployeeTask.employee_id == e.id,
                                                                EmployeeTask.status.in_(OPEN))))
        reqs = list(db.scalars(select(HumanRequest).where(HumanRequest.employee_id == e.id, HumanRequest.status == "open",
                                                          HumanRequest.kind.not_in(tasksmod.QUIET))))
        finished = m["done"] + m["failed"]
        fail = round(100 * m["failed"] / finished, 1) if finished else None
        late = [t for t in open_tasks if t.due_state in ("overdue", "escalated")]
        rows.append({"slug": a.slug, "name": a.name, "title": e.title, "team": teams.get(e.team_id), "level": e.autonomy_level,
                     "status": e.status, "manager": people.get(e.manager_user_id, ""), "done": m["done"],
                     "failed": m["failed"], "fail_rate": fail, "cost_usd": m["cost_usd"], "cost_per_task": m["cost_per_task"],
                     "avg_decision_min": m["avg_decision_min"], "on_time": round(100 * on_time / timed, 1) if timed else None,
                     "timed": timed, "quality": q["avg"], "rated": q["rated"], "open": len(open_tasks),
                     "waiting": sum(t.status == "waiting_human" for t in open_tasks), "overdue": len(late),
                     "pending_decisions": len(reqs),
                     "oldest_decision_min": round(max((_mins(r.created_at, t0) for r in reqs), default=0)) or None})
        for r in reqs:
            age = _mins(r.created_at, t0)
            decisions.append({"id": r.id, "kind": r.kind, "employee": a.slug, "employee_name": a.name,
                              "task_id": r.task_id, "holder": people.get(r.assigned_user_id, "") or "—",
                              "age_min": round(age), "action": r.action_type or r.kind,
                              "expires_at": iso(tasksmod._aware(r.expires_at)) if r.expires_at else None})
            h = holders.setdefault(r.assigned_user_id or 0, {"who": people.get(r.assigned_user_id, "") or "—",
                                                               "pending": 0, "oldest_min": 0})
            h["pending"] += 1
            h["oldest_min"] = max(h["oldest_min"], round(age))
        for t in late:
            overdue.append({"id": t.id, "title": t.title, "employee": a.slug, "employee_name": a.name,
                            "due_at": iso(tasksmod._aware(t.due_at)) if t.due_at else None, "status": t.status,
                            "due_state": t.due_state})
        for t in open_tasks:
            if t.status == "waiting_human" and _mins(t.updated_at or t.created_at, t0) > 24 * 60:
                stuck.append({"id": t.id, "title": t.title, "employee": a.slug, "employee_name": a.name,
                              "hours": round(_mins(t.updated_at or t.created_at, t0) / 60)})
    by_team: dict[str, dict] = {}
    for r in rows:
        g = by_team.setdefault(r["team"] or "—", {"team": r["team"] or "—", "employees": 0, "done": 0, "failed": 0,
                                                  "cost_usd": 0.0, "pending_decisions": 0, "overdue": 0, "_q": [], "_d": [],
                                                  "_on": [0, 0]})
        g["employees"] += 1
        g["done"] += r["done"]
        g["failed"] += r["failed"]
        g["cost_usd"] = round(g["cost_usd"] + r["cost_usd"], 4)
        g["pending_decisions"] += r["pending_decisions"]
        g["overdue"] += r["overdue"]
        if r["quality"] is not None:
            g["_q"] += [r["quality"]] * r["rated"]
        if r["avg_decision_min"] is not None:
            g["_d"].append(r["avg_decision_min"])
        if r["on_time"] is not None:
            g["_on"][0] += r["on_time"] * r["timed"] / 100
            g["_on"][1] += r["timed"]
    teams_out = []
    for g in by_team.values():
        q, d, (ok, n) = g.pop("_q"), g.pop("_d"), g.pop("_on")
        teams_out.append({**g, "quality": round(sum(q) / len(q), 2) if q else None,
                          "avg_decision_min": round(sum(d) / len(d), 1) if d else None,
                          "on_time": round(100 * ok / n, 1) if n else None})
    failing = [r for r in rows if r["fail_rate"] is not None and r["done"] + r["failed"] >= 4 and r["fail_rate"] >= FAIL_ALERT]
    total = {"employees": len(rows), "done": sum(r["done"] for r in rows), "failed": sum(r["failed"] for r in rows),
             "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
             "pending_decisions": len(decisions), "overdue": len(overdue)}
    return {"days": days, "team": team, "total": total, "employees": sorted(rows, key=lambda r: (-r["pending_decisions"], r["name"])),
            "teams": sorted(teams_out, key=lambda g: g["team"]),
            "bottlenecks": {"decisions": sorted(decisions, key=lambda x: -x["age_min"])[:15],
                            "holders": sorted(holders.values(), key=lambda h: (-h["pending"], -h["oldest_min"]))[:10],
                            "overdue": overdue[:15], "stuck": sorted(stuck, key=lambda x: -x["hours"])[:10],
                            "failing": [{"slug": r["slug"], "name": r["name"], "fail_rate": r["fail_rate"]} for r in failing]}}
