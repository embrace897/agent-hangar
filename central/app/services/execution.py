"""Perfil de execução: como o modelo trabalha em cada chamada — esforço de raciocínio, limite de saída, ferramentas
em paralelo, cache do prompt, rodadas do harness, compactação do contexto e o modelo por resposta.

A spec do agente diz o que quer (`execution`, tudo opcional). A central junta isso com o papel do agente e com o que
o modelo suporta (recursos da lista do provedor) e resolve um perfil. Cada destino recebe o perfil na sua língua:

- runtime de chat: parâmetros da API (`reasoning_effort`, `reasoning.effort` na OpenRouter, `max_tokens`…);
- claude-code: `--effort`, `--max-turns`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `DISABLE_PROMPT_CACHING`;
- codex: `model_reasoning_effort`;
- hermes: `--reasoning`.

O que o modelo, o provedor ou o harness não aceita não é enviado e aparece como aviso, para ninguém achar que está
valendo. O runtime ainda tem uma rede de segurança: se o provedor recusar um parâmetro opcional, repete a chamada sem
ele.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Agent, LlmConnection, LlmModel, ModelProfile

LEVELS = ("minimal", "low", "medium", "high", "max")
LEVEL_LABEL = {"minimal": "mínimo", "low": "baixo", "medium": "médio", "high": "alto", "max": "máximo"}
# o papel decide o esforço automático: quem planeja e programa pensa mais; quem tria e extrai responde rápido
ROLE_EFFORT = {"planner": "high", "coder": "high", "analyst": "medium", "general": "medium", "writer": "low",
               "support": "low", "extractor": "low", "classifier": "minimal"}
# provedores (da conexão) que aceitam `reasoning_effort` no /chat/completions; a OpenRouter usa `reasoning.effort`
EFFORT_PROVIDERS = {"openai", "litellm", "gemini", "groq", "xai", "fireworks", "together", "nebius", "custom", "ollama"}
# cache explícito (cache_control) só faz diferença em modelos Claude atrás de um gateway; os outros fazem sozinhos
CACHE_GATEWAYS = {"litellm", "openrouter"}
AUTO_CACHE = {"openai", "deepseek", "gemini", "xai", "groq"}
CLAUDE = re.compile(r"claude|anthropic", re.I)
# o que cada harness entende como esforço (o nível pedido vira o mais próximo que ele aceita)
HARNESS_EFFORT = {
    "claude-code": {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "max": "max"},
    "codex": {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "max": "high"},
    "hermes": {"minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "max": "max"},
}
# contexto: compacta quando a conversa passa desta fração da janela do modelo (o resto fica para a resposta)
CONTEXT_SHARE = 0.6
CONTEXT_DEFAULT = 48_000  # conexão sem lista: não dá para saber a janela
# modelo por resposta, "auto": o leve custa no máximo isto do preço do modelo do agente
LIGHT_MAX_SHARE = 0.4
HARNESS_PARAMS = {
    "claude-code": {"effort": "--effort", "output": "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "turns": "--max-turns",
                    "cache": "DISABLE_PROMPT_CACHING"},
    "codex": {"effort": "model_reasoning_effort"},  # o Codex 0.157 ignora model_max_output_tokens
    "hermes": {"effort": "--reasoning"},
}


def _model_row(db: Session, conn: LlmConnection | None, model: str) -> LlmModel | None:
    if conn is None or not model:
        return None
    return db.scalar(select(LlmModel).where(LlmModel.connection_id == conn.id, LlmModel.model_id == model))


def _caps(db: Session, conn: LlmConnection | None, model: str) -> tuple[list[str] | None, bool]:
    """(recursos do modelo pela lista do provedor, se a lista diz algo sobre ferramentas). None quando não há como
    saber (conexão sem lista). Recursos deduzidos só do nome nunca incluem ferramentas: aí a falta delas não é aviso."""
    row = _model_row(db, conn, model)
    if row is None:
        return None, False
    return list(row.caps or []), row.kind_source in ("provider", "catalog", "admin")


def _uses_tools(spec: dict) -> bool:
    return bool(spec.get("mcps") or spec.get("tools") or spec.get("plugins") or spec.get("sub_agents")
                or spec.get("memory"))


def resolve(db: Session, spec: dict, target: str, conn_name: str, model: str, name: str = "",
            objective: str = "") -> dict:
    """target: "runtime" (agente de chat) ou o id do harness. Devolve o perfil resolvido, com o que vai ser enviado,
    de onde veio cada valor e os avisos."""
    from .model_selection import ROLES, infer_role

    want = spec.get("execution") or {}
    conn = db.scalar(select(LlmConnection).where(LlmConnection.name == conn_name)) if conn_name else None
    provider = conn.provider if conn else ""
    model = model or (conn.model_name if conn else "")
    caps, caps_know_tools = _caps(db, conn, model)
    warnings: list[str] = []
    role = infer_role(spec, name, objective)
    out = {"target": target, "connection": conn_name, "provider": provider, "model": model, "role": role,
           "caps": caps, "effort": None, "effort_level": None, "effort_source": "", "effort_param": "",
           "max_output_tokens": want.get("max_output_tokens"), "output_param": "",
           "parallel_tools": want.get("parallel_tools") is not False, "prompt_cache": "",
           "max_turns": want.get("max_turns"), "warnings": warnings,
           "compact": None, "context_budget": None, "context_source": "",
           "light_model": None, "light_prices": None, "light_source": "", "light_candidates": []}
    if target == "runtime":
        _context(db, conn, model, want, out)
    if not conn:
        out["effort_source"] = "sem conexão (modo mock)"
        out["prompt_cache"] = "off"
        if target == "runtime" and want.get("light_model"):
            warnings.append("modelo por resposta precisa de uma conexão: o agente está em modo mock")
        return out

    # --- esforço de raciocínio
    asked = want.get("effort") or "auto"
    level = asked if asked != "auto" else ROLE_EFFORT.get(role, "medium")
    reasons = caps is None or "reasoning" in caps
    source = "definido no agente" if asked != "auto" else f"automático pelo papel ({ROLES.get(role, {}).get('label', role)})"
    if target == "runtime":
        if provider == "openrouter":
            param = "reasoning.effort"
        elif provider in EFFORT_PROVIDERS:
            param = "reasoning_effort"
        else:
            param = ""
        value = {"minimal": "minimal" if provider == "openai" else "low", "max": "high"}.get(level, level)
    else:
        param = HARNESS_PARAMS.get(target, {}).get("effort", "")
        value = HARNESS_EFFORT.get(target, {}).get(level)
    if not param:
        source = "não enviado"
        if asked != "auto":
            warnings.append(f"o esforço pedido não é enviado: {'o harness ' + target if target != 'runtime' else 'o provedor ' + provider} "
                            "não tem esse controle")
    elif caps is not None and not reasons:
        source = "não enviado: o modelo não tem modo de raciocínio"
        if asked != "auto":
            warnings.append(f"o modelo '{model}' não declara modo de raciocínio: o esforço pedido não é enviado")
    elif caps is None and asked == "auto":
        # sem lista não dá para saber se o modelo raciocina; o automático não arrisca
        source = "não enviado: a conexão não tem lista de modelos para confirmar o modo de raciocínio"
    else:
        out.update(effort=value, effort_level=level, effort_param=param)
        if caps is None:
            warnings.append(f"não dá para confirmar se '{model}' tem modo de raciocínio (a conexão não tem lista); "
                            "se o provedor recusar, o esforço deixa de ser enviado")
    out["effort_source"] = source

    # --- limite de saída
    if out["max_output_tokens"]:
        if target == "runtime":
            out["output_param"] = "max_completion_tokens" if provider == "openai" else "max_tokens"
        else:
            out["output_param"] = HARNESS_PARAMS.get(target, {}).get("output", "")
            if not out["output_param"]:
                warnings.append(f"o harness {target} não tem limite de saída configurável: o valor é ignorado")
                out["max_output_tokens"] = None

    # --- rodadas (harness) e ferramentas em paralelo (chat)
    if target == "runtime":
        out["max_turns"] = None
    elif out["max_turns"] and "turns" not in HARNESS_PARAMS.get(target, {}):
        warnings.append(f"o harness {target} não aceita limite de rodadas: o valor é ignorado")
        out["max_turns"] = None
    if target != "runtime":
        out["parallel_tools"] = None  # o CLI decide

    # --- cache do prompt
    if want.get("prompt_cache") is False:
        out["prompt_cache"] = "off"
    elif target == "claude-code":
        out["prompt_cache"] = "auto"
    elif target == "runtime" and provider in CACHE_GATEWAYS and CLAUDE.search(model):
        out["prompt_cache"] = "explicit"
    elif target == "runtime" and (provider in AUTO_CACHE or (provider in CACHE_GATEWAYS and not CLAUDE.search(model))):
        out["prompt_cache"] = "auto"
    else:
        out["prompt_cache"] = "provider"

    # --- chamadas de ferramenta
    needs_tools = target != "runtime" or _uses_tools(spec)
    if needs_tools and caps is not None and caps_know_tools and "tools" not in caps:
        warnings.append(f"o modelo '{model}' não declara suporte a chamadas de ferramenta, e "
                        f"{'o harness depende delas' if target != 'runtime' else 'o agente usa ferramentas'}: "
                        "prefira um modelo com ferramentas")
    if target == "runtime":
        _light(db, conn, model, needs_tools, want.get("light_model"), out)
    return out


def _context(db: Session, conn: LlmConnection | None, model: str, want: dict, out: dict):
    """Compactação: ligada por padrão (só age quando a conversa passa do orçamento). O orçamento vem da spec ou de
    60% da janela do modelo, pela lista do provedor."""
    out["compact"] = want.get("compact") is not False
    row = _model_row(db, conn, model)
    window = row.context_length if row is not None else None
    if want.get("context_budget"):
        out["context_budget"], out["context_source"] = want["context_budget"], "definido no agente"
        if window and want["context_budget"] > window * 0.9:
            out["warnings"].append(f"o orçamento de contexto ({want['context_budget']}) passa de 90% da janela do "
                                   f"modelo ({window}): sobra pouco para a resposta")
    elif window:
        out["context_budget"] = int(window * CONTEXT_SHARE)
        out["context_source"] = f"60% da janela do modelo ({window} tokens)"
    else:
        out["context_budget"] = CONTEXT_DEFAULT
        out["context_source"] = "padrão: a conexão não informa a janela do modelo"
    if not out["compact"]:
        out["context_source"] = "desligada no agente"


def _blended(row: LlmModel) -> float:
    return (row.price_in_per_mtok or 0) + (row.price_out_per_mtok or 0)


def _light(db: Session, conn: LlmConnection, model: str, needs_tools: bool, asked: str | None, out: dict):
    """Modelo por resposta: um modelo mais barato DA MESMA CONEXÃO (o container já tem a chave dela) atende os pedidos
    simples. 'auto' escolhe entre os que custam até 40% do principal: o de melhor nota de raciocínio nas fichas ou,
    sem fichas, o mais caro deles (o mais capaz entre os baratos)."""
    main = _model_row(db, conn, model)
    rows = [r for r in db.scalars(select(LlmModel).where(LlmModel.connection_id == conn.id, LlmModel.available.is_(True),
                                                          LlmModel.kind == "text"))
            if r.model_id != model and (not needs_tools or "tools" in (r.caps or []))]
    # gratuitos (preço 0) ficam de fora do automático: limites de uso apertados e termos próprios de uso dos dados
    cheaper = [r for r in rows if main is not None and _blended(main) > 0 and r.price_in_per_mtok is not None
               and 0 < _blended(r) <= _blended(main) * LIGHT_MAX_SHARE]
    out["light_candidates"] = [{"model": r.model_id, "price_in": r.price_in_per_mtok, "price_out": r.price_out_per_mtok}
                               for r in sorted(cheaper, key=_blended)][:30]
    if not asked:
        out["light_source"] = "desligado"
        return
    if asked == "auto":
        if main is None or not _blended(main):
            out["warnings"].append("modelo por resposta 'auto' precisa do preço do modelo do agente (lista da "
                                   "conexão): desligado")
            out["light_source"] = "desligado: sem preço para comparar"
            return
        if not cheaper:
            out["warnings"].append(f"nenhum modelo da conexão '{conn.name}' custa até 40% de '{model}'"
                                   f"{' e tem ferramentas' if needs_tools else ''}: modelo por resposta desligado")
            out["light_source"] = "desligado: nenhum modelo mais barato na conexão"
            return
        from .model_profiles import key_of
        keys = {key_of(r.model_id) for r in cheaper}
        score = {p.key: ((p.scores or {}).get("reasoning") or {}).get("value")
                 for p in db.scalars(select(ModelProfile).where(ModelProfile.key.in_(list(keys))))}
        rated = [r for r in cheaper if isinstance(score.get(key_of(r.model_id)), (int, float))]
        if rated:
            pick = max(rated, key=lambda r: score[key_of(r.model_id)])
            source = "automático: melhor nota de raciocínio entre os que custam até 40%"
        else:
            pick = max(cheaper, key=_blended)
            source = "automático: o mais capaz entre os que custam até 40% (sem fichas com nota)"
    else:
        pick = next((r for r in rows if r.model_id == asked), None)
        if pick is None and db.scalar(select(LlmModel.id).where(LlmModel.connection_id == conn.id)) is not None:
            out["warnings"].append(f"o modelo '{asked}' não está na lista da conexão '{conn.name}'"
                                   f"{' com ferramentas' if needs_tools else ''}: modelo por resposta desligado")
            out["light_source"] = "desligado: modelo fora da lista da conexão"
            return
        source = "definido no agente"
        if pick is None:  # conexão sem lista: confia no id, sem preço
            out.update(light_model=asked, light_prices=[None, None], light_source=source + " (sem preço na lista)")
            return
    out.update(light_model=pick.model_id, light_prices=[pick.price_in_per_mtok, pick.price_out_per_mtok],
               light_source=source)


def runtime_options(profile: dict) -> dict:
    """O que o runtime de chat aplica em cada chamada (vai na spec resolvida, em `execution_resolved`)."""
    api: dict = {}
    if profile.get("effort"):
        if profile["effort_param"] == "reasoning.effort":
            api["reasoning"] = {"effort": profile["effort"]}
        else:
            api["reasoning_effort"] = profile["effort"]
    if profile.get("max_output_tokens") and profile.get("output_param"):
        api[profile["output_param"]] = profile["max_output_tokens"]
    lite: dict = {}
    if profile.get("effort"):  # títulos e respostas triviais: o mínimo de raciocínio
        low = "minimal" if profile["effort"] == "minimal" else "low"
        lite = {"reasoning": {"effort": low}} if "reasoning" in api else {"reasoning_effort": low}
    return {"api": api, "lite_api": lite, "parallel_tools": bool(profile.get("parallel_tools")),
            "cache": profile.get("prompt_cache") == "explicit",
            "compact": profile.get("compact") is not False, "context_budget": profile.get("context_budget"),
            "light": ({"model": profile["light_model"], "prices": profile.get("light_prices") or [None, None]}
                      if profile.get("light_model") else None)}


def harness_options(profile: dict) -> dict:
    """O que o entrypoint do harness traduz em flags e variáveis do CLI (vai em LLM_EXEC_JSON)."""
    return {k: v for k, v in (("effort", profile.get("effort")), ("max_output_tokens", profile.get("max_output_tokens")),
                              ("max_turns", profile.get("max_turns")),
                              ("prompt_cache", profile.get("prompt_cache") != "off")) if v is not None}


def describe(db: Session, agent: Agent, spec: dict, env: str) -> dict:
    """Para a tela do agente e o MCP: o perfil resolvido no ambiente, o que a spec pede e as opções."""
    h = spec.get("harness") or {}
    if h.get("id"):
        o = h.get(env) if isinstance(h.get(env), dict) else {}
        target = o.get("id") or h["id"]
        conn, model = o.get("connection") or h.get("connection") or "", o.get("model") or h.get("model") or ""
    else:
        llm = spec.get("llm") or {}
        o = llm.get(env) if isinstance(llm.get(env), dict) else {}
        target = "runtime"
        conn, model = o.get("connection") or llm.get("connection") or "", o.get("model") or llm.get("model") or ""
    prof = resolve(db, spec, target, conn, model, agent.name, agent.objective)
    return {"env": env, "spec": spec.get("execution") or {}, "profile": prof,
            "levels": ["auto", *LEVELS], "level_labels": LEVEL_LABEL,
            "sends": runtime_options(prof) if target == "runtime" else harness_options(prof)}


_TAGS: dict[tuple, tuple[float, dict]] = {}
TAGS_TTL_S = 300


def profile_for_version(db: Session, agent: Agent, version: int | None, env: str) -> dict:
    """O perfil resolvido de uma versão do agente num ambiente (a medição agrupa por modelo e esforço)."""
    from .common import spec_of
    spec = spec_of(agent, version)
    h = spec.get("harness") or {}
    blk = h if h.get("id") else (spec.get("llm") or {})
    o = blk.get(env) if isinstance(blk.get(env), dict) else {}
    target = (o.get("id") or h["id"]) if h.get("id") else "runtime"
    conn, model = o.get("connection") or blk.get("connection") or "", o.get("model") or blk.get("model") or ""
    return resolve(db, spec, target, conn, model, agent.name, agent.objective)


def usage_tags(db: Session, agent_id: int, env: str) -> dict:
    """{version, model, effort} do agente que está no ar neste ambiente, para marcar cada chamada. Cache curto: é
    chamado a cada chamada ao agente. Nunca derruba o registro de uso."""
    import time

    from sqlalchemy import select as _select

    from ..models import Deployment
    from ..tenancy import current_org
    try:
        version = db.scalar(_select(Deployment.version).where(
            Deployment.agent_id == agent_id, Deployment.env == env, Deployment.status == "running")
            .order_by(Deployment.id.desc()).limit(1))
        a = db.get(Agent, agent_id)
        if a is None:
            return {}
        version = version or a.current_version
        key = (current_org(), agent_id, env, version)
        hit = _TAGS.get(key)
        if hit and time.time() - hit[0] < TAGS_TTL_S:
            return hit[1]
        p = profile_for_version(db, a, version, env)
        tags = {"version": version, "model": p.get("model") or "", "effort": p.get("effort_level") or ""}
        if len(_TAGS) > 5000:
            _TAGS.clear()
        _TAGS[key] = (time.time(), tags)
        return tags
    except Exception:  # noqa: BLE001 — marcar o perfil é extra; o uso sempre é gravado
        return {}
