"""Página "Início" do usuário (portal /app): tudo o que a pessoa precisa ver no dia a dia, numa chamada só,
já filtrado pelas permissões dela — o que precisa de atenção, os agentes dela, pedidos, uso e orçamento,
agendamentos, conexões (chaves) e memória."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth
from ..models import AccessRequest, Agent, ApiKey, Schedule, UsageEvent, now
from .access import Access
from .common import iso, spec_of
from .org import approvals, pending_for, team_dict
from .runtime import active_deployment, latest_test, refresh_deployments
from .schedules import schedule_dict

OWN_LEVELS = ("maintainer", "developer", "consumer")


def _own(acc: Access, a: Agent) -> bool:
    lv = acc.level(a)
    if lv == "admin":  # admin no portal: os agentes dos times dele (sem time = todos)
        return not acc.teams or a.team_id in acc.teams
    return lv in OWN_LEVELS or (lv == "granted" and a.visibility != "open")


def _usage_by_agent(db: Session, ids: list[int], since) -> dict[int, dict]:
    if not ids:
        return {}
    rows = db.execute(select(UsageEvent.agent_id, func.count(UsageEvent.id), func.coalesce(func.sum(UsageEvent.cost_usd), 0.0))
                      .where(UsageEvent.agent_id.in_(ids), UsageEvent.created_at >= since)
                      .group_by(UsageEvent.agent_id)).all()
    return {r[0]: {"requests": r[1], "cost_usd": round(float(r[2] or 0), 4)} for r in rows}


def _errors_by_agent(db: Session, ids: list[int], since) -> dict[int, int]:
    if not ids:
        return {}
    return dict(db.execute(select(UsageEvent.agent_id, func.count(UsageEvent.id))
                           .where(UsageEvent.agent_id.in_(ids), UsageEvent.created_at >= since, UsageEvent.ok.is_(False))
                           .group_by(UsageEvent.agent_id)).all())


def home(db: Session, acc: Access) -> dict:
    p = acc.p
    t = now()
    all_agents = acc.visible(db.scalars(select(Agent).order_by(Agent.updated_at.desc())).all())
    refresh_deployments(db, all_agents)
    own = [a for a in all_agents if _own(acc, a)]
    ids = [a.id for a in own]
    use7 = _usage_by_agent(db, ids, t - timedelta(days=7))
    err24 = _errors_by_agent(db, ids, t - timedelta(hours=24))
    attention: list[dict] = []

    agents = []
    for a in own:
        perms = acc.permissions(a)
        spec = spec_of(a)
        prod, stage = active_deployment(a, "prod"), active_deployment(a, "stage")
        lt = latest_test(a, a.current_version)
        errors = err24.get(a.id, 0) if "usage" in perms else None
        agents.append({"slug": a.slug, "name": a.name, "objective": a.objective, "status": a.status, "kind": a.kind,
                       "team": acc.team_rows[a.team_id].name if a.team_id in acc.team_rows else None,
                       "access": acc.level(a), "version": a.current_version, "prod": bool(prod), "stage": bool(stage),
                       "prod_version": prod.version if prod else None, "last_test": lt.status if lt else None,
                       "harness": bool(spec.get("harness")), "memory": (spec.get("memory") or {}).get("scope"),
                       "requests_7d": use7.get(a.id, {}).get("requests", 0) if "usage" in perms else None,
                       "cost_7d": use7.get(a.id, {}).get("cost_usd", 0.0) if "usage" in perms else None,
                       "errors_24h": errors, "can_edit": "edit" in perms, "can_consume": "consume" in perms})
        link = f"#/agents/{a.slug}"
        if "edit" in perms and lt and lt.status == "failed":
            attention.append({"level": "bad", "text": f"{a.name}: teste reprovado na v{a.current_version}",
                              "link": link + "/tests"})
        if a.status == "production" and not prod and not spec.get("harness") and not spec.get("remote"):
            attention.append({"level": "bad", "text": f"{a.name}: está em produção, mas o container não está no ar",
                              "link": link + "/deployments"})
        if errors:
            attention.append({"level": "warn", "text": f"{a.name}: {errors} erro(s) nas últimas 24 h", "link": link + "/usage"})

    # agendamentos dos meus agentes: próximos disparos e falhas
    own_ids = set(ids)
    scheds = [schedule_dict(db, s) for s in db.scalars(select(Schedule).where(Schedule.agent_id.in_(own_ids)))] if own_ids else []
    for s in scheds:
        if s["last_status"] == "failed":
            attention.append({"level": "bad", "text": f"{s['agent_name']}: o agendamento “{s['name'] or s['when']}” falhou",
                              "link": f"#/agents/{s['agent']}/schedules"})
    upcoming = sorted((s for s in scheds if s["enabled"] and s["next_run_at"]), key=lambda s: s["next_run_at"])[:6]

    # orçamento dos meus times
    teams = []
    for tid, role in acc.teams.items():
        team = acc.team_rows.get(tid)
        if not team:
            continue
        d = team_dict(db, team, acc)
        teams.append({"slug": d["slug"], "name": d["name"], "role": role, "budget_usd_month": d["budget_usd_month"],
                      "spent_month": d["spent_month"], "budget_state": d["budget_state"], "agents": d["agents"]})
        if d["budget_state"] in ("warn", "over"):
            pct = int(100 * d["spent_month"] / d["budget_usd_month"])
            attention.append({"level": "bad" if d["budget_state"] == "over" else "warn",
                              "text": f"Time {d['name']}: US$ {d['spent_month']:.2f} de US$ {d['budget_usd_month']:.2f} "
                                      f"no mês ({pct}%)" + (" — o gateway está recusando chamadas" if
                                                            d["budget_state"] == "over" and team.budget_enforce else ""),
                              "link": f"#/teams/{d['slug']}"})

    # pedidos: o que eu decido e o que eu pedi
    to_decide = pending_for(db, acc)
    if to_decide:
        attention.insert(0, {"level": "warn", "text": f"{len(to_decide)} pedido(s) aguardando a sua decisão",
                             "link": "#/approvals"})
    ap = approvals(db, acc)
    recent = t - timedelta(days=14)
    for r in ap["my_access"]:
        if r["status"] == "rejected" and r["decided_at"] and r["decided_at"] >= iso(recent):
            attention.append({"level": "info", "text": f"Seu pedido de acesso a {r['agent_name']} foi recusado",
                              "link": "#/approvals"})

    # meu uso (chaves e sessões minhas) nos últimos 30 dias
    my_usage = {"requests": 0, "cost_usd": 0.0, "tokens": 0, "by_agent": []}
    if p.is_user:
        since = t - timedelta(days=30)
        base = select(UsageEvent).where(UsageEvent.user_id == p.user_id, UsageEvent.created_at >= since).subquery()
        row = db.execute(select(func.count(base.c.id), func.coalesce(func.sum(base.c.cost_usd), 0.0),
                                func.coalesce(func.sum(base.c.tokens_in + base.c.tokens_out), 0))).one()
        names = {a.id: (a.slug, a.name) for a in all_agents}
        by = db.execute(select(base.c.agent_id, func.count(base.c.id), func.coalesce(func.sum(base.c.cost_usd), 0.0))
                        .group_by(base.c.agent_id).order_by(func.count(base.c.id).desc()).limit(5)).all()
        my_usage = {"requests": row[0], "cost_usd": round(float(row[1]), 4), "tokens": int(row[2]),
                    "by_agent": [{"slug": names[a][0], "name": names[a][1], "requests": n, "cost_usd": round(float(c), 4)}
                                 for a, n, c in by if a in names]}

    # conexões: minhas chaves ativas
    keys = []
    if p.user_id:
        keys = [auth.api_key_dict(k) for k in db.scalars(select(ApiKey).where(
            ApiKey.user_id == p.user_id, ApiKey.revoked_at.is_(None)).order_by(ApiKey.id.desc()))]
        stale = t - timedelta(days=60)
        for k in keys:
            k["stale"] = not k["last_used_at"] or k["last_used_at"] < iso(stale)

    # catálogo: novidades de outros times que eu posso encontrar
    news = [{"slug": a.slug, "name": a.name, "objective": a.objective,
             "team": acc.team_rows[a.team_id].name if a.team_id in acc.team_rows else None,
             "access": acc.level(a), "created_at": iso(a.created_at)}
            for a in all_agents if not _own(acc, a) and a.status == "production"][:5]

    pending_mine = [r for r in ap["my_access"] if r["status"] == "pending"] + \
        [r for r in ap["my_promotions"] if r["status"] == "pending"]
    return {
        "user": {"name": p.name, "is_admin": p.is_admin, "is_auditor": p.is_auditor},
        "attention": attention,
        "agents": agents,
        "counts": {"agents": len(agents), "in_production": sum(1 for a in agents if a["prod"]),
                   "to_decide": len(to_decide), "my_pending": len(pending_mine),
                   "access_requests_open": db.scalar(select(func.count(AccessRequest.id)).where(
                       AccessRequest.user_id == p.user_id, AccessRequest.status == "pending")) if p.user_id else 0},
        "to_decide": to_decide[:10],
        "my_requests": {"access": ap["my_access"][:10], "promotions": ap["my_promotions"][:10]},
        "teams": teams,
        "my_usage": my_usage,
        "schedules": upcoming,
        "keys": keys,
        "memory": [{"slug": a["slug"], "name": a["name"], "scope": a["memory"]} for a in agents if a["memory"]],
        "catalog_news": news,
    }
