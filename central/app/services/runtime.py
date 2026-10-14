"""Deploy em stage/produção, reconciliação com o Docker, promoção e ship."""
import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, deploy
from ..models import Agent, Deployment, McpServer, Skill, now
from .catalog import resolve_llm
from .common import PlatformError, audit, get_agent, spec_of


def resolve_spec(db: Session, agent: Agent, env: str, version: int | None = None) -> dict:
    """Spec com skills/MCPs do catálogo expandidos e sub-agentes apontando para a central."""
    spec = copy.deepcopy(spec_of(agent, version))
    skills = []
    for s in spec.get("skills", []):
        if isinstance(s, str):
            row = db.scalar(select(Skill).where(Skill.name == s))
            if not row:
                raise PlatformError(f"Skill '{s}' não está no catálogo")
            skills.append({"name": row.name, "description": row.description, "content": row.content})
        else:
            skills.append(s)
    mcps = []
    for m in spec.get("mcps", []):
        if isinstance(m, str):
            row = db.scalar(select(McpServer).where(McpServer.name == m))
            if not row:
                raise PlatformError(f"MCP '{m}' não está no catálogo")
            from .connectors import can_use
            if not can_use(agent, row):
                raise PlatformError(f"o conector '{m}' é de outro time ou pessoal: este agente não pode usá-lo")
            mcps.append({"name": row.name, "url": row.url, **({"tool_prefix": row.tool_prefix} if row.tool_prefix else {})})
        else:
            mcps.append(m)
    if spec.get("memory"):
        if not config.MEMORY_URL:
            raise PlatformError("a spec usa `memory`, mas a memória não está ligada nesta instalação "
                                "(docker compose --profile memory; ver docs/memory.md)")
        # sempre pela central, que confere o agente e impõe os grupos de memória (o agente não escolhe)
        mcps.append({"name": "memory", "url": f"{config.INTERNAL_BASE_URL}/internal/memory/mcp"})
    subs = []
    pieces = read_only_pieces(db, agent)
    for s in spec.get("sub_agents", []):
        sub = get_agent(db, s)
        # Via central, nunca direto no container do vizinho: é o único jeito de alcançar um sub-agente com
        # harness (sem container fixo), e a central confere o token interno de quem chama. Peças só de leitura
        # (especialistas de um agente montado) são sempre chamadas na versão de PRODUÇÃO, a aprovada pelo dono.
        gw = "gw" if env == "prod" or sub.slug in pieces else "gw-stage"
        harness = bool(spec_of(sub).get("harness"))
        subs.append({"slug": sub.slug, "name": sub.name, "objective": sub.objective,
                     "url": f"{config.INTERNAL_BASE_URL}/internal/{gw}/{sub.slug}",
                     **({"harness": True, "timeout_s": config.JOB_MAX_TIMEOUT_S + 60} if harness else {})})
    if spec.get("plugins"):  # servidos pela central como MCP interno: a credencial do time nunca entra no container
        from .plugins import resolve_for_spec
        p_mcps, p_skills = resolve_for_spec(db, agent, spec["plugins"])
        mcps += p_mcps
        skills += p_skills
    from .employees import runtime_profile  # Digital employee: o runtime passa toda ferramenta pelo gate da central
    profile = runtime_profile(db, agent)
    if profile:
        spec["employee"] = profile
    spec.update(skills_resolved=skills, mcps=mcps, sub_agents_resolved=subs, slug=agent.slug,
                name=agent.name, objective=agent.objective, final_output=agent.final_output,
                version=version or agent.current_version)
    return spec


def read_only_pieces(db: Session, agent: Agent) -> set[str]:
    """Especialistas que um agente montado (compose_agent) usa como peça: nunca são testados, publicados nem
    redeployados por causa dele — o agente novo chama a versão deles em produção, como está."""
    from ..models import AgentLineage
    row = db.scalar(select(AgentLineage).where(AgentLineage.agent_id == agent.id))
    return {x["slug"] for x in (row.specialists or [])} if row else set()


def active_deployment(agent: Agent, env: str) -> Deployment | None:
    for d in agent.deployments:
        if d.env == env and d.status == "running":
            return d
    return None


def refresh_deployments(db: Session, agents=None):
    """Reconcilia o registro com o Docker real (uma chamada à API do Docker para todos). Agente-com-harness
    não tem container ocioso — o deployment dele é só um marcador de ambiente aprovado."""
    live = deploy.states()
    if live is None:  # Docker indisponível agora: não dá para concluir nada
        return
    changed = False
    for a in agents if agents is not None else db.scalars(select(Agent)):
        if spec_of(a).get("harness"):
            continue
        for d in a.deployments:
            if d.status == "running":
                st = live.get(deploy.container_name(a.slug, d.env), "missing")
                if st not in ("running", "hibernated"):  # hibernado: o stage ocioso parado de propósito
                    d.status = "missing" if st == "missing" else "exited"
                    d.stopped_at = now()
                    changed = True
    if changed:
        db.commit()


def _deploy_harness_marker(db: Session, a: Agent, env: str, actor="admin") -> Deployment:
    for d in a.deployments:
        if d.env == env and d.status == "running":
            d.status, d.stopped_at = "replaced", now()
    dep = Deployment(agent_id=a.id, version=a.current_version, env=env, container_name="(sob demanda)",
                     status="running", url=deploy.public_url(a.slug, env), container_id="", error="")
    db.add(dep)
    a.status = "production" if env == "prod" else (a.status if a.status == "production" else "stage")
    db.commit()
    audit(db, actor, f"deploy.{env}", a.slug, f"v{a.current_version} (harness, sem container fixo)")
    return dep


def latest_test(agent: Agent, version: int):
    for t in agent.tests:
        if t.version == version:
            return t
    return None


def deploy_env(db: Session, slug: str, env: str, actor="admin", _stack=()) -> Deployment:
    if env not in ("stage", "prod"):
        raise PlatformError("env deve ser 'stage' ou 'prod'")
    a = get_agent(db, slug)
    if slug in _stack:
        raise PlatformError(f"Ciclo de sub-agentes: {' -> '.join(_stack + (slug,))}")
    if env == "prod":
        gate = latest_test(a, a.current_version)
        if not gate or gate.status != "passed":
            raise PlatformError(
                f"Promoção bloqueada: v{a.current_version} não tem teste aprovado em stage. Rode run_tests antes.")
    if spec_of(a).get("harness"):
        return _deploy_harness_marker(db, a, env, actor)
    pieces = read_only_pieces(db, a)
    for sub in spec_of(a).get("sub_agents", []):
        sa = get_agent(db, sub)
        refresh_deployments(db, [sa])
        if sub in pieces:  # peça só de leitura: precisa estar no ar em produção; nunca é (re)deployada daqui
            if not active_deployment(sa, "prod"):
                raise PlatformError(f"o especialista '{sub}' não está no ar em produção — ele é uma peça só de leitura "
                                    f"deste agente; o time dono precisa publicá-lo")
            continue
        dep = active_deployment(sa, env)
        if not dep or dep.version != sa.current_version:
            deploy_env(db, sub, env, actor, _stack + (slug,))
    spec = resolve_spec(db, a, env)
    llm_conf = resolve_llm(db, spec, env)
    spec["llm"]["model"] = llm_conf["model"]  # modelo efetivo, para o runtime e para exibição
    spec["llm"]["prices"] = list(llm_conf["prices"])
    for d in a.deployments:
        if d.env == env and d.status == "running":
            d.status, d.stopped_at = "replaced", now()
    dep = Deployment(agent_id=a.id, version=a.current_version, env=env, container_name=deploy.container_name(slug, env),
                     status="starting", url=deploy.public_url(slug, env), container_id="", error="")
    db.add(dep)
    db.commit()
    db.expire(a, ["deployments"])
    try:
        c = deploy.run_agent(slug, env, spec, llm_conf)
        dep.container_id = c.id[:12]
        err = deploy.wait_healthy(slug, env)
    except Exception as e:
        err = str(e)
    deploy.invalidate_states()
    if err:
        dep.status, dep.error, dep.stopped_at = "failed", err[:4000], now()
        db.commit()
        audit(db, actor, f"deploy.{env}.failed", slug, err[:500])
        raise PlatformError(f"Deploy {env} falhou: {err[:500]}")
    dep.status = "running"
    a.status = "production" if env == "prod" else (a.status if a.status == "production" else "stage")
    db.commit()
    audit(db, actor, f"deploy.{env}", slug, f"v{a.current_version} -> {dep.container_name}")
    return dep


def stop(db: Session, slug: str, env: str, actor="admin"):
    a = get_agent(db, slug)
    deploy.stop_agent(slug, env)
    for d in a.deployments:
        if d.env == env and d.status == "running":
            d.status, d.stopped_at = "stopped", now()
    if env == "prod" and a.status == "production":
        a.status = "stage" if active_deployment(a, "stage") else "tested"
    db.commit()
    audit(db, actor, f"stop.{env}", slug)


def promote(db: Session, slug: str, actor="admin") -> Deployment:
    dep = deploy_env(db, slug, "prod", actor)
    if dep.status == "running":
        from . import guides  # rascunho do guia da versão nova, em segundo plano (melhor esforço)
        guides.after_promote(db, get_agent(db, slug), dep.version)
    return dep


def ship(db: Session, slug: str, actor="admin", _seen=None, promote_prod: bool = True) -> list[dict]:
    """Sub-agentes primeiro -> stage -> testes -> produção (só se os testes passarem). promote_prod=False para
    depois dos testes: quem não pode promover direto gera um pedido de aprovação (services/org.py)."""
    from .testing import run_tests

    _seen = _seen if _seen is not None else set()
    if slug in _seen:
        return []
    _seen.add(slug)
    a = get_agent(db, slug)
    steps = []
    pieces = read_only_pieces(db, a)
    for sub in spec_of(a).get("sub_agents", []):
        if sub in pieces:  # especialista de um agente montado: usado como está (versão em produção), sem testes/deploy
            sa = get_agent(db, sub)
            dep = active_deployment(sa, "prod")
            steps.append({"agent": sub, "step": "piece", "status": f"em produção v{dep.version} (não alterado)"
                          if dep else "fora do ar em produção"})
            continue
        steps += ship(db, sub, actor, _seen, promote_prod)
    run = run_tests(db, slug, actor)
    steps.append({"agent": slug, "step": "tests", "status": run.status, "summary": run.summary})
    if run.status != "passed":
        raise PlatformError(f"Testes reprovados para '{slug}': {run.summary}. Veja os resultados e ajuste com "
                            "design_agent.")
    if not promote_prod:
        return steps
    dep = promote(db, slug, actor)
    steps.append({"agent": slug, "step": "prod", "status": dep.status, "url": dep.url})
    return steps
