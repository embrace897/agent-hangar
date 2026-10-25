"""Catálogo reutilizável: skills, MCP servers e conexões de LLM (e a resolução delas para um agente)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, crypto
from ..models import Agent, LlmConnection, McpServer, Skill
from ..tenancy import current_org
from .common import PlatformError, audit, iso, spec_of

PROTOCOLS = ("openai", "anthropic", "deepseek")

# Cada harness exige uma conexão do protocolo que ele fala nativamente. claude-code fala a API de Mensagens
# da Anthropic; codex e hermes falam o formato da OpenAI (provedor real ou gateway: LiteLLM, OpenRouter…);
# deepseek-harness só tem caminho verificado contra a API oficial da DeepSeek.
HARNESS_PROTOCOL = {"claude-code": "anthropic", "codex": "openai", "hermes": "openai",
                    "deepseek-harness": "deepseek"}


# ------------------------------------------------------------------ skills e MCPs
def upsert_skill(db: Session, name, description, content, actor="admin", examples: str | None = None,
                 test: dict | None = None, team_id: int | None = None, create_only: bool = False):
    """Cria ou (admin) atualiza uma skill. `create_only`: nunca altera uma existente — quem usa a skill não muda sem
    querer. Cada alteração sobe a versão; o teste da skill entra nos testes dos agentes montados com ela."""
    from .. import spec as specmod
    s = db.scalar(select(Skill).where(Skill.name == name))
    if s and create_only:
        raise PlatformError(f"Já existe a skill '{name}' (v{s.version}) — use-a como está ou escolha outro nome")
    if test:
        try:
            test = specmod.TestCase(**test).model_dump(exclude_none=True)
        except Exception as e:
            raise PlatformError(f"teste da skill inválido: {e}") from None
    if s is None:
        s = Skill(name=name, version=1, team_id=team_id, created_by=actor)
    else:
        changed = (s.description, s.content, s.examples or "", s.test) != (
            description or "", content or "", examples if examples is not None else (s.examples or ""),
            test if test is not None else s.test)
        s.version = (s.version or 1) + (1 if changed else 0)
    s.description, s.content = description or "", content or ""
    if examples is not None:
        s.examples = examples
    if test is not None:
        s.test = test
    db.add(s)
    db.commit()
    audit(db, actor, "skill.upsert", name, f"v{s.version}")
    return s


def upsert_mcp(db: Session, name, url, description="", actor="admin", tool_prefix: str | None = None):
    if not url.startswith(("http://", "https://")):
        raise PlatformError("url do MCP deve ser http(s) (Streamable HTTP)")
    m = db.scalar(select(McpServer).where(McpServer.name == name)) or McpServer(name=name)
    m.url, m.description = url, description or ""
    if tool_prefix is not None:
        m.tool_prefix = tool_prefix
    db.add(m)
    db.commit()
    audit(db, actor, "mcp.upsert", name, url)
    return m


# ------------------------------------------------------------------ conexões de LLM
def upsert_llm_connection(db: Session, name, base_url, model_name, api_key="", description="", actor="admin",
                          protocol="openai", price_in_per_mtok=None, price_out_per_mtok=None, provider: str | None = None,
                          require_model: bool = True) -> LlmConnection:
    """Cria ou atualiza a conexão. `require_model=False`: o modelo padrão pode vir da lista do provedor (quem chama
    sincroniza em seguida, ver llm_models.sync_connection)."""
    from . import llm_models
    if not (name and base_url and (model_name or not require_model)):
        raise PlatformError("name, base_url e model_name são obrigatórios")
    if provider is not None and provider not in llm_models.BY_ID:
        raise PlatformError(f"provider desconhecido: {provider}. Opções: {', '.join(llm_models.BY_ID)}")
    if not base_url.startswith(("http://", "https://")):
        raise PlatformError("base_url deve ser uma URL http(s), ex.: https://openrouter.ai/api/v1")
    if protocol not in PROTOCOLS:
        raise PlatformError("protocol deve ser 'openai' (chat, codex, hermes), 'anthropic' (harness claude-code) "
                            "ou 'deepseek' (harness deepseek-harness, API nativa da DeepSeek)")
    c = db.scalar(select(LlmConnection).where(LlmConnection.name == name)) or LlmConnection(name=name)
    if c.id is None and provider is None:  # conexão nova sem provedor: o endereço diz qual é (senão "custom")
        provider = next((p["id"] for p in llm_models.PROVIDERS if p["base_url"] and p["group"] != "custom"
                         and p["base_url"].split("//")[-1].split("/")[0] in base_url), "custom")
    if provider is not None:
        c.provider = provider
    c.base_url, c.description, c.protocol = base_url.rstrip("/"), description or "", protocol
    if model_name or c.model_name is None:
        c.model_name = model_name or ""
    c.price_in_per_mtok, c.price_out_per_mtok = price_in_per_mtok, price_out_per_mtok
    if api_key:  # reenviar vazio preserva a chave atual (útil para só trocar a URL/modelo)
        c.api_key = crypto.encrypt(api_key)
    db.add(c)
    db.commit()
    audit(db, actor, "llm_connection.upsert", name, f"{base_url} · {model_name} · {protocol}")
    return c


def delete_llm_connection(db: Session, name, actor="admin"):
    c = db.scalar(select(LlmConnection).where(LlmConnection.name == name))
    if not c:
        raise PlatformError(f"Conexão '{name}' não encontrada")
    in_use = [a.slug for a in db.scalars(select(Agent)) if uses_connection(spec_of(a), name)]
    if in_use:
        raise PlatformError(f"Conexão em uso por: {', '.join(in_use)}. Troque o LLM desses agentes antes.")
    from sqlalchemy import delete

    from ..models import LlmModel
    db.execute(delete(LlmModel).where(LlmModel.connection_id == c.id))
    db.delete(c)
    db.commit()
    audit(db, actor, "llm_connection.delete", name)


def uses_connection(spec: dict, name: str) -> bool:
    names = set()
    for block_key in ("llm", "harness", "judge"):
        block = spec.get(block_key) or {}
        names.add(block.get("connection"))
        for e in ("stage", "prod"):
            if isinstance(block.get(e), dict):
                names.add(block[e].get("connection"))
    return name in names


def mask_key(k: str) -> str:
    try:
        plain = crypto.decrypt(k)
    except Exception:
        return "····"
    return f"····{plain[-4:]}" if len(plain) > 4 else ("····" if plain else "")


def llm_connection_dict(c: LlmConnection) -> dict:
    """Nunca devolve a chave — só os 4 últimos caracteres, para o admin reconhecer qual é."""
    from sqlalchemy.orm import object_session

    from . import app_llm, llm_models
    db = object_session(c)
    kinds = llm_models.kinds_summary(db, c) if db is not None and c.id else {}
    return {"id": c.id, "name": c.name, "base_url": c.base_url, "model_name": c.model_name, "description": c.description,
            "protocol": c.protocol, "api_key": mask_key(c.api_key), "has_key": bool(c.api_key),
            "price_in_per_mtok": c.price_in_per_mtok, "price_out_per_mtok": c.price_out_per_mtok,
            "allow_code": bool(c.allow_code), "created_at": iso(c.created_at), "provider": c.provider or "custom",
            "models": kinds, "models_total": sum(kinds.values()), "models_synced_at": iso(c.models_synced_at),
            "models_error": c.models_error or "",
            "desktop": app_llm.desktop_state(db, c) if db is not None and c.id else None}


def test_connection(db: Session, name: str) -> dict:
    """Chamada real e mínima ao LLM da conexão (uma frase, poucos tokens): confere URL, chave e modelo. Usada pelo
    `hangar doctor`, pelo instalador e pelo botão Testar do catálogo."""
    import time

    import httpx

    c = get_connection(db, name)
    key = crypto.decrypt(c.api_key) if c.api_key else ""
    t0 = time.monotonic()
    try:
        if c.protocol == "anthropic":
            r = httpx.post(f"{c.base_url}/v1/messages", timeout=30,
                           headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                           json={"model": c.model_name, "max_tokens": 16,
                                 "messages": [{"role": "user", "content": "Responda só: ok"}]})
        else:
            r = httpx.post(f"{c.base_url}/chat/completions", timeout=30,
                           headers={"Authorization": f"Bearer {key}"} if key else {},
                           json={"model": c.model_name, "max_tokens": 16,
                                 "messages": [{"role": "user", "content": "Responda só: ok"}]})
    except httpx.HTTPError as e:
        return {"name": name, "ok": False, "latency_ms": int((time.monotonic() - t0) * 1000),
                "detail": f"não consegui conectar em {c.base_url}: {type(e).__name__}"}
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code >= 400:
        hint = {401: "chave inválida", 403: "chave sem permissão", 404: "URL ou modelo inexistente",
                429: "limite/créditos do provedor"}.get(r.status_code, "")
        return {"name": name, "ok": False, "latency_ms": ms,
                "detail": f"HTTP {r.status_code}{' (' + hint + ')' if hint else ''}: {r.text[:200]}"}
    return {"name": name, "ok": True, "latency_ms": ms, "detail": f"{c.model_name} respondeu em {ms} ms"}


def set_code_approval(db: Session, name: str, allow: bool, actor="admin") -> LlmConnection:
    c = get_connection(db, name)
    c.allow_code = bool(allow)
    db.commit()
    audit(db, actor, "llm_connection.code", name, "aprovada para código" if allow else "não aprovada para código")
    return c


def code_allowed(db: Session, spec: dict, env: str) -> tuple[bool, str]:
    """O agente pode receber código (ferramentas do cliente de código, avaliações de código) neste ambiente?
    Com a política da empresa em "approved", só se a conexão de LLM efetiva estiver aprovada para código."""
    from ..models import Organization

    org = db.get(Organization, current_org())
    if not org or (org.code_policy or "any") != "approved":
        return True, ""
    llm = spec.get("llm") or {}
    override = llm.get(env) if isinstance(llm.get(env), dict) else {}
    name = override.get("connection") or llm.get("connection") or ""
    if not name:
        return True, ""  # mock: nada sai para um LLM
    conn = db.scalar(select(LlmConnection).where(LlmConnection.name == name))
    if conn and conn.allow_code:
        return True, ""
    return False, (f"a conexão de LLM '{name}' não está aprovada para receber código (política da empresa). "
                   "Peça a um admin para aprová-la em Catálogo → Conexões de LLM, ou use uma conexão aprovada.")


def get_connection(db: Session, name: str) -> LlmConnection:
    conn = db.scalar(select(LlmConnection).where(LlmConnection.name == name))
    if not conn:
        raise PlatformError(f"Conexão de LLM '{name}' não está no catálogo. Use register_llm_connection.")
    return conn


def resolve_llm(db: Session, spec: dict, env: str) -> dict:
    """LlmConnection do catálogo (com override por ambiente) -> {connection, base_url, api_key, model,
    temperature} prontos para o container do agente de chat."""
    llm = spec.get("llm") or {}
    override = llm.get(env) if isinstance(llm.get(env), dict) else {}
    conn_name = override.get("connection") or llm.get("connection") or ""
    model = override.get("model") or llm.get("model") or ""
    temperature = override.get("temperature", llm.get("temperature", 0.2))
    if not conn_name:
        return {"connection": "", "base_url": "", "api_key": "", "model": model or config.DEFAULT_MODEL,
                "temperature": temperature, "prices": (None, None)}
    conn = get_connection(db, conn_name)
    if conn.protocol != "openai":
        raise PlatformError(
            f"Conexão '{conn_name}' é protocol='{conn.protocol}'; agentes de chat precisam de uma conexão "
            "protocol='openai' (endpoint /v1/chat/completions).")
    from . import llm_models
    return {"connection": conn.name, "base_url": conn.base_url, "api_key": crypto.decrypt(conn.api_key),
            "model": model or conn.model_name, "temperature": temperature,
            "prices": llm_models.prices_for(db, conn, model or conn.model_name)}


def resolve_harness(db: Session, spec: dict, env: str) -> dict:
    """Harness (claude-code | codex | hermes | deepseek-harness) + a conexão que o alimenta, com override
    por ambiente. Sem `connection`, o job roda em modo mock (sem chamar LLM nenhum)."""
    h = spec.get("harness") or {}
    if not h.get("id"):
        raise PlatformError("Agente sem harness configurado. Use design_agent(harness={'id':"
                            "'claude-code'|'codex'|'hermes'|'deepseek-harness','connection':'<nome>'}).")
    override = h.get(env) if isinstance(h.get(env), dict) else {}
    harness_id = override.get("id") or h.get("id")
    if harness_id not in HARNESS_PROTOCOL:
        raise PlatformError(f"Harness '{harness_id}' desconhecido. Opções: {sorted(HARNESS_PROTOCOL)}")
    conn_name = override.get("connection") or h.get("connection") or ""
    if not conn_name:
        return {"harness_id": harness_id, "connection": "", "base_url": "", "api_key": "", "model": "",
                "mock": True, "prices": (None, None)}
    from . import llm_models
    conn = get_connection(db, conn_name)
    wanted = HARNESS_PROTOCOL[harness_id]
    if conn.protocol != wanted:
        raise PlatformError(f"Conexão '{conn_name}' é protocol='{conn.protocol}'; o harness '{harness_id}' "
                            f"precisa de uma conexão protocol='{wanted}'.")
    model = override.get("model") or h.get("model") or conn.model_name
    return {"harness_id": harness_id, "connection": conn.name, "base_url": conn.base_url,
            "api_key": crypto.decrypt(conn.api_key), "model": model, "mock": False,
            "prices": llm_models.prices_for(db, conn, model)}


def cost_usd(tokens_in: int, tokens_out: int, prices: tuple) -> float:
    p_in, p_out = prices or (None, None)
    return round(((tokens_in or 0) * (p_in or 0) + (tokens_out or 0) * (p_out or 0)) / 1_000_000, 6)
