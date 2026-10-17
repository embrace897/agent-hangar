"""Serialização de agentes, deployments e testes para a API/MCP."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config
from ..models import Agent, Deployment, PromotionRequest, Team, TestRun, UsageEvent, now
from .common import iso, spec_of
from .jobs import job_dict
from .usage import agent_usage


def dep_dict(d: Deployment) -> dict:
    return {"id": d.id, "version": d.version, "env": d.env, "container": d.container_name, "status": d.status,
            "url": d.url, "error": d.error, "created_at": iso(d.created_at), "stopped_at": iso(d.stopped_at)}


def test_dict(t: TestRun) -> dict:
    return {"id": t.id, "version": t.version, "env": t.env, "status": t.status, "summary": t.summary,
            "results": t.results, "duration_ms": t.duration_ms, "created_at": iso(t.created_at)}


def endpoints(slug: str) -> dict:
    base = f"{config.PUBLIC_BASE_URL}/gw/{slug}"
    return {"openai": base + "/v1/chat/completions", "a2a": base + "/a2a",
            "agent_card": base + "/.well-known/agent.json", "acp": base + "/acp/runs", "mcp": base + "/mcp",
            "agui": base + "/agui"}


def agent_dict(db: Session, a: Agent, detail=False, acc=None) -> dict:
    """acc (services.access.Access): inclui o nível de acesso e as permissões de quem pede e esconde o que
    ele não pode ver (spec, versões, jobs, logs de deploy e resultados de teste de agentes de outros times)."""
    since = now() - timedelta(days=7)
    reqs, tok, cost = db.execute(
        select(func.count(UsageEvent.id), func.coalesce(func.sum(UsageEvent.tokens_in + UsageEvent.tokens_out), 0),
               func.coalesce(func.sum(UsageEvent.cost_usd), 0.0))
        .where(UsageEvent.agent_id == a.id, UsageEvent.created_at >= since, UsageEvent.channel != "test")
    ).one()
    spec = spec_of(a)
    llm, harness, remote = spec.get("llm") or {}, spec.get("harness"), spec.get("remote")
    if remote:  # o modelo é escolha do agente remoto; aqui só o protocolo e o host
        from urllib.parse import urlparse
        model_display = f"remoto (AG-UI) · {urlparse(remote.get('url', '')).hostname or '?'}"
    elif harness:
        model_display = f"harness:{harness.get('id')} · {harness.get('connection') or 'mock'}"
    else:
        model_display = llm.get("model") or (f"{llm['connection']} (padrão)" if llm.get("connection")
                                             else config.DEFAULT_MODEL)
    out = {"slug": a.slug, "name": a.name, "objective": a.objective, "final_output": a.final_output,
           "kind": a.kind, "owner": a.owner, "status": a.status, "version": a.current_version,
           "created_at": iso(a.created_at), "updated_at": iso(a.updated_at), "harness": harness,
           "remote": {"protocol": remote.get("protocol", "agui")} if remote else None,
           "model": model_display, "sub_agents": spec.get("sub_agents", []),
           "stage": next((dep_dict(d) for d in a.deployments if d.env == "stage" and d.status == "running"), None),
           "prod": next((dep_dict(d) for d in a.deployments if d.env == "prod" and d.status == "running"), None),
           "last_test": test_dict(a.tests[0]) if a.tests else None,
           "requests_7d": reqs, "tokens_7d": int(tok), "cost_7d": round(float(cost), 4),
           "visibility": a.visibility, "expose_spec": a.expose_spec, "team": _team(db, a.team_id)}
    perms = acc.permissions(a) if acc is not None else None
    if perms is not None:
        # pedido de promoção pendente é assunto interno do time
        out.update(access=acc.level(a), permissions=sorted(perms), pending_promotion=bool(
            perms & {"edit", "approve"} and db.scalar(select(func.count(PromotionRequest.id)).where(
                PromotionRequest.agent_id == a.id, PromotionRequest.status == "pending"))))
        if "usage" not in perms:
            out.update(requests_7d=None, tokens_7d=None, cost_7d=None)
    if detail and perms is not None and "view_spec" not in perms:
        out.update(spec=None, versions=[], jobs=[], deployments=[], endpoints=endpoints(a.slug),
                   tests=[{k: v for k, v in test_dict(t).items() if k != "results"} for t in a.tests[:5]],
                   usage=agent_usage(db, a.id) if "usage" in perms else None)
        if out["last_test"]:
            out["last_test"].pop("results", None)
        return out
    if detail:
        out.update(
            spec=spec,
            versions=[{"version": v.version, "spec": v.spec, "created_by": v.created_by,
                       "created_at": iso(v.created_at)} for v in a.versions],
            tests=[test_dict(t) for t in a.tests],
            deployments=[dep_dict(d) for d in a.deployments],
            jobs=[job_dict(j) for j in a.jobs[:30]],
            endpoints=endpoints(a.slug),
            usage=agent_usage(db, a.id),
        )
    return out


def _team(db: Session, team_id: int | None) -> dict | None:
    t = db.get(Team, team_id) if team_id else None
    return {"id": t.id, "slug": t.slug, "name": t.name} if t else None


def list_agents(db: Session, acc=None) -> list[dict]:
    from .runtime import refresh_deployments

    refresh_deployments(db)
    agents = db.scalars(select(Agent).order_by(Agent.updated_at.desc())).all()
    if acc is not None:
        agents = acc.visible(agents)
    return [agent_dict(db, a, acc=acc) for a in agents]
