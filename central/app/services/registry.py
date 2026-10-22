"""Registro e versionamento de agentes: criar, desenhar (merge patch), substituir spec, rollback, apply."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import deploy, tenancy
from .. import spec as specmod
from ..models import (
    AccessRequest,
    Agent,
    AgentVersion,
    Organization,
    PromotionRequest,
    Schedule,
    ScheduleRun,
    Team,
    UsageEvent,
)
from ..tenancy import current_org, unscoped
from . import avatars
from .common import PlatformError, audit, find_agent, get_agent, slugify, spec_of

META_KEYS = ("name", "objective", "final_output", "owner")


def create_agent(db: Session, name: str, objective: str, final_output: str, kind="single", owner="",
                 actor="admin", slug: str | None = None, team_id: int | None = None,
                 visibility: str | None = None, avatar: str | None = None) -> Agent:
    if not (name and objective and final_output):
        raise PlatformError("name, objective e final_output são obrigatórios")
    def taken(s: str) -> bool:  # slug é único na instalação (nome do container): confere em todas as empresas
        return db.scalar(unscoped(select(Agent.id).where(Agent.slug == s))) is not None
    if slug:
        slug = slugify(slug)
        if taken(slug):
            raise PlatformError(f"slug '{slug}' já existe")
    else:
        base, i = slugify(name), 2
        slug = base
        while taken(slug):
            slug, i = f"{base}-{i}", i + 1
    if not team_id:  # o time padrão da empresa (o primeiro criado nela)
        team_id = db.scalar(select(Team.id).order_by(Team.id))
    if visibility and visibility not in ("private", "org", "open"):
        raise PlatformError("visibility deve ser private, org ou open")
    org = db.get(Organization, current_org())
    a = Agent(slug=slug, name=name, objective=objective, final_output=final_output, kind=kind or "single",
              owner=owner or "", status="draft", current_version=1, team_id=team_id or 1,
              visibility=visibility or (org.default_visibility if org else "org"),
              avatar=avatars.check(avatar) if avatar else avatars.pick(name, objective))  # sem escolha: pelo papel
    a.versions.append(AgentVersion(version=1, spec=specmod.default_spec(), created_by=actor, created_user_id=tenancy.current_user()))
    db.add(a)
    db.commit()
    audit(db, actor, "agent.register", slug, f"{name} | time #{a.team_id} | objetivo: {objective}")
    return a


def _check_refs(db: Session, slug: str, spec: dict):
    for s in spec.get("sub_agents", []):
        if s == slug:
            raise PlatformError("Agente não pode ser sub-agente de si mesmo")
        get_agent(db, s)


def _check_models(db: Session, old: dict, new: dict):
    """Um modelo novo na spec precisa estar na lista da conexão (quando o provedor tem lista). Só os pares que mudaram:
    um agente que já usa um modelo que sumiu continua editável (a caixa de entrada avisa o admin)."""
    from .llm_models import _effective, check_model
    before = {p for env in ("stage", "prod") for p in _effective(old, env)}
    for env in ("stage", "prod"):
        for conn, model in _effective(new, env):
            if model and (conn, model) not in before:
                err = check_model(db, conn, model)
                if err:
                    raise PlatformError(err)


def _commit_version(db: Session, a: Agent, spec: dict, actor: str, action: str, detail: str, acc=None) -> Agent:
    """Valida, e só cria uma versão nova se a spec realmente mudou. Com `acc`, um sub_agent novo exige permissão de
    uso sobre ele (agente de outro time: pedido de acesso aprovado)."""
    try:
        spec = specmod.validate(spec)
    except specmod.SpecError as e:
        raise PlatformError(str(e)) from None
    _check_refs(db, a.slug, spec)
    _check_models(db, specmod.normalize_legacy(spec_of(a)), spec)
    if acc is not None:
        from .composer import check_sub_agents
        check_sub_agents(db, acc, specmod.normalize_legacy(spec_of(a)), spec)
    a.kind = "multi" if spec.get("sub_agents") else "single"
    if spec == specmod.normalize_legacy(spec_of(a)):
        db.commit()
        return a
    a.current_version += 1
    a.status = "draft"
    a.versions.append(AgentVersion(version=a.current_version, spec=spec, created_by=actor, created_user_id=tenancy.current_user()))
    db.commit()
    audit(db, actor, action, a.slug, f"v{a.current_version}: {detail}")
    return a


def design_agent(db: Session, slug: str, patch: dict, actor="admin", acc=None) -> Agent:
    """Aplica `patch` como JSON Merge Patch (RFC 7396) sobre a spec atual: objetos mesclam, listas e valores
    substituem, e null apaga (ex.: {"llm": {"model": null}} volta ao modelo padrão da conexão)."""
    a = get_agent(db, slug)
    unknown = set(patch) - specmod.SPEC_KEYS - set(META_KEYS)
    if unknown:
        raise PlatformError(f"Campos desconhecidos: {sorted(unknown)}. Válidos: {sorted(specmod.SPEC_KEYS)}")
    for k in META_KEYS:
        if patch.get(k):
            setattr(a, k, patch[k])
    spec_patch = {k: v for k, v in patch.items() if k in specmod.SPEC_KEYS}
    merged = specmod.merge_patch(specmod.normalize_legacy(spec_of(a)), spec_patch)
    return _commit_version(db, a, merged, actor, "agent.design", f"campos {sorted(patch)}", acc)


def replace_spec(db: Session, slug: str, spec: dict, actor="admin", acc=None) -> Agent:
    """Substitui a spec inteira (editor da UI e GitOps/apply)."""
    return _commit_version(db, get_agent(db, slug), spec or {}, actor, "agent.spec", "spec substituída", acc)


def set_avatar(db: Session, slug: str, avatar: str, actor="admin") -> Agent:
    """O dono troca o avatar (o escolhido na criação, pela IA ou pelo papel). Não cria versão: não muda o comportamento."""
    a = get_agent(db, slug)
    a.avatar = avatars.check(avatar)
    db.commit()
    audit(db, actor, "agent.avatar", slug, avatar)
    return a


def rollback_agent(db: Session, slug: str, version: int, actor="admin") -> Agent:
    """Cria uma versão nova com a spec de uma versão anterior (o histórico nunca é reescrito)."""
    a = get_agent(db, slug)
    old = spec_of(a, version)
    return _commit_version(db, a, old, actor, "agent.rollback", f"restaurada a v{version}")


def delete_agent(db: Session, slug: str, actor="admin"):
    a = get_agent(db, slug)
    for env in ("stage", "prod"):
        deploy.stop_agent(slug, env, remove_data=True)  # excluído: o volume de dados sai junto
    db.query(UsageEvent).filter(UsageEvent.agent_id == a.id).delete()
    # tudo o que aponta para o agente sai junto (no Postgres, as chaves estrangeiras barrariam a exclusão)
    sids = [x.id for x in db.query(Schedule).filter(Schedule.agent_id == a.id)]
    if sids:
        db.query(ScheduleRun).filter(ScheduleRun.schedule_id.in_(sids)).delete(synchronize_session=False)
        db.query(Schedule).filter(Schedule.id.in_(sids)).delete(synchronize_session=False)
    db.query(AccessRequest).filter(AccessRequest.agent_id == a.id).delete()
    db.query(PromotionRequest).filter(PromotionRequest.agent_id == a.id).delete()
    from ..models import AgentGuide, AgentLineage, ExecutionTrial
    db.query(ExecutionTrial).filter(ExecutionTrial.agent_id == a.id).delete()  # sem CASCADE: barraria a exclusão
    # AgentLineage e AgentGuide têm ON DELETE CASCADE no Postgres; no SQLite, apagamos aqui
    db.query(AgentLineage).filter(AgentLineage.agent_id == a.id).delete()
    db.query(AgentGuide).filter(AgentGuide.agent_id == a.id).delete()
    db.delete(a)
    db.commit()
    audit(db, actor, "agent.delete", slug)
    from . import memory
    memory.forget_agent(slug)  # memória própria do agente (a de time/empresa continua com os outros)


def apply_document(db: Session, doc: dict, actor="admin", acc=None, trusted_catalog=False) -> list[dict]:
    """GitOps: aplica o estado desejado de um documento {skills, mcp_servers, agents}. Idempotente — só
    cria versão quando a spec muda. Agentes são aplicados na ordem do documento (membros antes do
    orquestrador). `acc` (services.access.Access) aplica as permissões de quem chama: catálogo só admin,
    agente novo num time onde a pessoa é developer+ (campo `team`), agente existente exige poder editar."""
    from .access import Forbidden
    from .catalog import upsert_mcp, upsert_skill

    # templates vêm da imagem da central (curados): as skills/MCPs deles entram mesmo por quem não é admin
    if acc is not None and not acc.p.is_admin and not trusted_catalog and (doc.get("skills") or doc.get("mcp_servers")):
        raise Forbidden("Só admins registram skills e MCP servers no catálogo")
    out = []
    for s in doc.get("skills", []) or []:
        upsert_skill(db, s["name"], s.get("description", ""), s.get("content", ""), actor)
        out.append({"kind": "skill", "name": s["name"], "action": "upserted"})
    for m in doc.get("mcp_servers", []) or []:
        upsert_mcp(db, m["name"], m["url"], m.get("description", ""), actor)
        out.append({"kind": "mcp_server", "name": m["name"], "action": "upserted"})
    for item in doc.get("agents", []) or []:
        slug = slugify(item.get("slug") or item["name"])
        a = find_agent(db, slug)
        action = "unchanged"
        if not a:
            team_id = acc.team_for_new_agent(item.get("team")).id if acc is not None else None
            if acc is None and item.get("team"):
                from .access import find_team
                team_id = find_team(db, item["team"]).id
            a = create_agent(db, item["name"], item["objective"], item["final_output"], owner=item.get("owner", ""),
                             actor=actor, slug=slug, team_id=team_id, visibility=item.get("visibility"))
            action = "created"
        else:
            if acc is not None:
                acc.require("edit", a)
            for k in META_KEYS:
                if item.get(k):
                    setattr(a, k, item[k])
        before = a.current_version
        replace_spec(db, slug, item.get("spec") or {}, actor)
        if action == "unchanged" and a.current_version != before:
            action = "updated"
        out.append({"kind": "agent", "slug": slug, "action": action, "version": a.current_version})
    return out


def _short(v, n=300):
    import json
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"… (+{len(s) - n} caracteres)"


def spec_diff(old, new, path: str = "") -> list[dict]:
    """O que mudou entre duas specs, campo a campo (listas comparadas inteiras)."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in sorted(set(old) | set(new)):
            p = f"{path}.{k}" if path else k
            if k not in old:
                out.append({"path": p, "change": "added", "to": _short(new[k])})
            elif k not in new:
                out.append({"path": p, "change": "removed", "from": _short(old[k])})
            else:
                out += spec_diff(old[k], new[k], p)
        return out
    return [] if old == new else [{"path": path, "change": "changed", "from": _short(old), "to": _short(new)}]


def diff_versions(db: Session, slug: str, from_version: int, to_version: int | None = None) -> dict:
    a = get_agent(db, slug)
    to_version = to_version or a.current_version
    old = specmod.normalize_legacy(spec_of(a, from_version))
    new = specmod.normalize_legacy(spec_of(a, to_version))
    return {"slug": a.slug, "from_version": from_version, "to_version": to_version, "changes": spec_diff(old, new)}
