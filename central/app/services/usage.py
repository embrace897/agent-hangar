"""Uso, tokens e custo por chamada — base do dashboard e das métricas por agente/canal/protocolo."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import observability
from ..models import Agent, TestRun, UsageEvent, now
from .common import iso


def find_usage(obj) -> dict:
    if isinstance(obj, dict):
        if isinstance(obj.get("usage"), dict):
            return obj["usage"]
        for v in obj.values():
            u = find_usage(v)
            if u:
                return u
    elif isinstance(obj, list):
        for v in obj:
            u = find_usage(v)
            if u:
                return u
    return {}


def record_usage(db: Session, agent_id: int, env: str, channel: str, protocol: str, latency_ms: int, ok: bool,
                 payload=None, tokens_in=None, tokens_out=None, cost=None, user_id: int | None = None,
                 tags: dict | None = None):
    u = find_usage(payload) if payload else {}
    if tags is None:  # o perfil que está no ar: a medição por perfil (esforço, modelo) sai daqui
        from .execution import usage_tags
        tags = usage_tags(db, agent_id, env)
    ev = UsageEvent(
        agent_id=agent_id, env=env, channel=(channel or "api")[:50], protocol=protocol,
        tokens_in=int(tokens_in if tokens_in is not None else u.get("prompt_tokens", 0) or 0),
        tokens_out=int(tokens_out if tokens_out is not None else u.get("completion_tokens", 0) or 0),
        cost_usd=float(cost if cost is not None else u.get("cost_usd", 0) or 0),
        latency_ms=latency_ms, ok=ok, user_id=user_id, version=tags.get("version"),
        # modelo por resposta: o runtime diz qual modelo atendeu (o leve ou o do agente)
        model=str(u.get("model_used") or tags.get("model") or "")[:200], effort=str(tags.get("effort") or "")[:12])
    db.add(ev)
    db.commit()
    a = db.get(Agent, agent_id)
    observability.record_invocation(a.slug if a else str(agent_id), env, ev.channel, protocol, ok, latency_ms,
                                    ev.tokens_in, ev.tokens_out, ev.cost_usd)


def _series(rows, days):
    buckets = {}
    for i in range(days):
        d = (now() - timedelta(days=days - 1 - i)).date().isoformat()
        buckets[d] = {"day": d, "requests": 0, "tokens": 0, "errors": 0, "cost_usd": 0.0}
    for r in rows:
        b = buckets.get(r.created_at.date().isoformat())
        if b:
            b["requests"] += 1
            b["tokens"] += r.tokens_in + r.tokens_out
            b["errors"] += 0 if r.ok else 1
            b["cost_usd"] = round(b["cost_usd"] + (r.cost_usd or 0), 6)
    return list(buckets.values())


def usage_row(r: UsageEvent, slug: str | None = None) -> dict:
    d = {"at": iso(r.created_at), "env": r.env, "channel": r.channel, "protocol": r.protocol,
         "tokens": r.tokens_in + r.tokens_out, "cost_usd": r.cost_usd or 0, "latency_ms": r.latency_ms, "ok": r.ok}
    if slug:
        d["agent"] = slug
    return d


def agent_usage(db: Session, agent_id: int, days=14):
    since = now() - timedelta(days=days)
    rows = db.scalars(select(UsageEvent).where(UsageEvent.agent_id == agent_id, UsageEvent.created_at >= since)
                      .order_by(UsageEvent.id.desc())).all()
    return {"series": _series(rows, days), "recent": [usage_row(r) for r in rows[:30]]}


def overview(db: Session, acc=None):
    """acc: quem não é admin/auditor vê só os agentes cujo uso pode ver (os dos seus times)."""
    from .runtime import refresh_deployments

    refresh_deployments(db)
    agents = db.scalars(select(Agent)).all()
    if acc is not None and not acc.p.is_auditor:
        agents = [a for a in agents if acc.can("usage", a)]
    ids = {a.id for a in agents}
    status = {}
    for a in agents:
        status[a.status] = status.get(a.status, 0) + 1
    since = now() - timedelta(days=14)
    rows = [r for r in db.scalars(select(UsageEvent).where(UsageEvent.created_at >= since,
                                                           UsageEvent.channel != "test")).all() if r.agent_id in ids]
    day1 = now() - timedelta(days=1)
    r24 = [r for r in rows if r.created_at >= day1]
    by_agent, by_channel, by_protocol = {}, {}, {}
    names = {a.id: a for a in agents}
    for r in rows:
        x = by_agent.setdefault(r.agent_id, {"requests": 0, "tokens": 0, "errors": 0, "lat": 0, "cost_usd": 0.0})
        x["requests"] += 1
        x["tokens"] += r.tokens_in + r.tokens_out
        x["errors"] += 0 if r.ok else 1
        x["lat"] += r.latency_ms
        x["cost_usd"] = round(x["cost_usd"] + (r.cost_usd or 0), 6)
        by_channel[r.channel] = by_channel.get(r.channel, 0) + 1
        by_protocol[r.protocol] = by_protocol.get(r.protocol, 0) + 1
    tests = [t for t in db.scalars(select(TestRun)).all() if t.agent_id in ids]
    running = [d for a in agents for d in a.deployments if d.status == "running"]
    return {
        "agents_total": len(agents), "status": status,
        "multi_agents": sum(1 for a in agents if a.kind == "multi"),
        "running_prod": sum(1 for d in running if d.env == "prod"),
        "running_stage": sum(1 for d in running if d.env == "stage"),
        "tests_total": len(tests),
        "tests_pass_rate": round(100 * sum(t.status == "passed" for t in tests) / len(tests)) if tests else None,
        "requests_24h": len(r24), "tokens_24h": sum(r.tokens_in + r.tokens_out for r in r24),
        "cost_24h": round(sum(r.cost_usd or 0 for r in r24), 4),
        "cost_14d": round(sum(r.cost_usd or 0 for r in rows), 4),
        "avg_latency_ms": int(sum(r.latency_ms for r in r24) / len(r24)) if r24 else 0,
        "error_rate": round(100 * sum(not r.ok for r in r24) / len(r24), 1) if r24 else 0,
        "series": _series(rows, 14),
        "top_agents": sorted(
            [{"slug": names[i].slug, "name": names[i].name, **{k: v for k, v in x.items() if k != "lat"},
              "avg_latency_ms": int(x["lat"] / x["requests"])} for i, x in by_agent.items() if i in names],
            key=lambda x: -x["requests"])[:8],
        "by_channel": by_channel, "by_protocol": by_protocol,
    }
