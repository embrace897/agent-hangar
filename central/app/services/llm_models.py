"""Provedores de LLM e os modelos que cada um oferece.

Cada conexão é de um provedor do catálogo (OpenAI, OpenRouter, LiteLLM…) ou "custom" (compatível com OpenAI ou
Anthropic, informado à mão). A central pergunta ao provedor quais modelos ele tem — na hora em que a conexão é salva e
depois a cada hora — e guarda cada modelo com o tipo de saída, o contexto e o preço:

- tipo: o que o provedor informa (OpenRouter, LiteLLM, Together, Mistral, Cohere), senão a tabela pública de modelos do
  LiteLLM, senão o nome (`*embed*`, `whisper`, `tts`…); o admin corrige e a coleta não desfaz;
- preço: o do provedor, senão a tabela pública (só quando o nome bate com o provedor, para não pegar o preço de outro);
- modelo que sumiu da lista fica indisponível (não é apagado); se algum agente usa, aparece na caixa de entrada.

O construtor de agentes (MCP) lê essa lista para saber se a empresa tem o tipo de modelo que o agente precisa.
"""
import logging
import re
import threading
import time
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, crypto, tenancy
from ..models import Agent, LlmConnection, LlmModel, now
from .common import PlatformError, audit, iso, spec_of

log = logging.getLogger("hangar.models")

KINDS = ("text", "embedding", "rerank", "image", "video", "tts", "stt", "moderation", "other")
MAX_MODELS = 3000

# id, nome, grupo, protocolo, base_url, como listar, prefixo na tabela pública do LiteLLM, tipos que costuma ter
P = dict
PROVIDERS = [
    P(id="litellm", name="LiteLLM", group="gateway", protocol="openai", base_url="http://host.docker.internal:4000/v1",
      list="litellm", prefix="", kinds="text, embedding, rerank, image, tts, stt", initials="LL", color="#1F6FEB",
      note="O gateway da empresa (ou o seu). A lista vem de /model/info, com o tipo de cada modelo. De dentro do Docker, "
           "use host.docker.internal em vez de localhost."),
    P(id="openrouter", name="OpenRouter", group="gateway", protocol="openai", base_url="https://openrouter.ai/api/v1",
      list="openai", list_params={"output_modalities": "all"}, prefix="openrouter/",
      kinds="text, embedding, rerank, image, video, tts, stt", key_url="https://openrouter.ai/keys",
      note="Centenas de modelos numa chave só, com preço e contexto de cada um."),
    P(id="openai", name="OpenAI", group="api", protocol="openai", base_url="https://api.openai.com/v1", list="openai",
      prefix="", kinds="text, embedding, image, tts, stt", key_url="https://platform.openai.com/api-keys"),
    P(id="anthropic", name="Anthropic", group="api", protocol="anthropic", base_url="https://api.anthropic.com",
      list="anthropic", prefix="", kinds="text, vision", key_url="https://console.anthropic.com/settings/keys",
      note="Protocolo da Anthropic: serve o harness claude-code."),
    P(id="gemini", name="Google Gemini", group="api", protocol="openai",
      base_url="https://generativelanguage.googleapis.com/v1beta/openai", list="openai", prefix="gemini/",
      kinds="text, embedding, image", key_url="https://aistudio.google.com/apikey"),
    P(id="deepseek", name="DeepSeek", group="api", protocol="openai", base_url="https://api.deepseek.com/v1",
      list="openai", prefix="deepseek/", kinds="text", key_url="https://platform.deepseek.com/api_keys",
      note="Para o harness deepseek-harness, cadastre de novo com protocolo deepseek e base https://api.deepseek.com."),
    P(id="groq", name="Groq", group="api", protocol="openai", base_url="https://api.groq.com/openai/v1", list="openai",
      prefix="groq/", kinds="text, stt, tts", key_url="https://console.groq.com/keys"),
    P(id="mistral", name="Mistral", group="api", protocol="openai", base_url="https://api.mistral.ai/v1", list="openai",
      prefix="mistral/", kinds="text, embedding, vision", key_url="https://console.mistral.ai/api-keys"),
    P(id="xai", name="xAI (Grok)", group="api", protocol="openai", base_url="https://api.x.ai/v1", list="openai",
      prefix="xai/", kinds="text, image", key_url="https://console.x.ai"),
    P(id="together", name="Together AI", group="api", protocol="openai", base_url="https://api.together.xyz/v1",
      list="openai", prefix="together_ai/", kinds="text, embedding, rerank, image, tts, stt",
      key_url="https://api.together.ai/settings/api-keys"),
    P(id="fireworks", name="Fireworks AI", group="api", protocol="openai", base_url="https://api.fireworks.ai/inference/v1",
      list="openai", prefix="fireworks_ai/", kinds="text, embedding, image",
      key_url="https://fireworks.ai/account/api-keys"),
    P(id="cerebras", name="Cerebras", group="api", protocol="openai", base_url="https://api.cerebras.ai/v1",
      list="openai", prefix="cerebras/", kinds="text", key_url="https://cloud.cerebras.ai"),
    P(id="cohere", name="Cohere", group="api", protocol="openai", base_url="https://api.cohere.ai/compatibility/v1",
      list="cohere", prefix="cohere/", kinds="text, embedding, rerank", key_url="https://dashboard.cohere.com/api-keys"),
    P(id="perplexity", name="Perplexity", group="api", protocol="openai", base_url="https://api.perplexity.ai",
      list="none", prefix="perplexity/", kinds="text",
      key_url="https://www.perplexity.ai/settings/api",
      note="O Perplexity não publica a lista de modelos: inclua os que vai usar (ex.: sonar, sonar-pro)."),
    P(id="nvidia", name="NVIDIA NIM", group="api", protocol="openai", base_url="https://integrate.api.nvidia.com/v1",
      list="openai", prefix="nvidia_nim/", kinds="text, embedding, rerank", key_url="https://build.nvidia.com"),
    P(id="nebius", name="Nebius AI", group="api", protocol="openai", base_url="https://api.studio.nebius.com/v1",
      list="openai", prefix="nebius/", kinds="text, embedding, image", key_url="https://studio.nebius.com"),
    P(id="siliconflow", name="SiliconFlow", group="api", protocol="openai", base_url="https://api.siliconflow.com/v1",
      list="openai", prefix="", kinds="text, embedding, rerank, image", key_url="https://cloud.siliconflow.com"),
    P(id="hyperbolic", name="Hyperbolic", group="api", protocol="openai", base_url="https://api.hyperbolic.xyz/v1",
      list="openai", prefix="hyperbolic/", kinds="text, image", key_url="https://app.hyperbolic.xyz"),
    P(id="kimi", name="Kimi (Moonshot)", group="api", protocol="openai", base_url="https://api.moonshot.ai/v1",
      list="openai", prefix="moonshot/", kinds="text", key_url="https://platform.moonshot.ai"),
    P(id="glm", name="GLM (Z.ai)", group="api", protocol="openai", base_url="https://api.z.ai/api/paas/v4",
      list="openai", prefix="zai/", kinds="text, vision", key_url="https://z.ai/manage-apikey/apikey-list"),
    P(id="minimax", name="MiniMax", group="api", protocol="openai", base_url="https://api.minimax.io/v1", list="openai",
      prefix="minimax/", kinds="text, tts", key_url="https://www.minimax.io/platform"),
    P(id="qwen", name="Alibaba (Qwen)", group="api", protocol="openai",
      base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", list="openai", prefix="dashscope/",
      kinds="text, embedding, vision", key_url="https://modelstudio.console.alibabacloud.com"),
    P(id="sambanova", name="SambaNova", group="api", protocol="openai", base_url="https://api.sambanova.ai/v1",
      list="openai", prefix="sambanova/", kinds="text", key_url="https://cloud.sambanova.ai/apis"),
    P(id="novita", name="Novita AI", group="api", protocol="openai", base_url="https://api.novita.ai/v3/openai",
      list="openai", prefix="novita/", kinds="text, embedding", key_url="https://novita.ai/settings/key-management"),
    P(id="huggingface", name="Hugging Face", group="api", protocol="openai", base_url="https://router.huggingface.co/v1",
      list="openai", prefix="huggingface/", kinds="text", key_url="https://huggingface.co/settings/tokens"),
    P(id="voyage-ai", name="Voyage AI", group="api", protocol="openai", base_url="https://api.voyageai.com/v1",
      list="none", prefix="voyage/", kinds="embedding, rerank", key_url="https://dashboard.voyageai.com",
      note="Só embedding e rerank, sem lista pública: inclua os modelos (ex.: voyage-3.5, rerank-2.5)."),
    P(id="jina-ai", name="Jina AI", group="api", protocol="openai", base_url="https://api.jina.ai/v1", list="none",
      prefix="jina_ai/", kinds="embedding, rerank", key_url="https://jina.ai/api-dashboard",
      note="Só embedding e rerank, sem lista pública: inclua os modelos (ex.: jina-embeddings-v3, jina-reranker-v2-base-multilingual)."),
    P(id="ollama", name="Ollama", group="local", protocol="openai", base_url="http://host.docker.internal:11434/v1",
      list="openai", prefix="ollama/", kinds="text, embedding",
      note="Modelos na sua máquina. A chave não é conferida: qualquer valor serve (ex.: ollama)."),
    P(id="custom", name="Compatível com OpenAI", group="custom", protocol="openai", base_url="", list="openai", prefix="",
      kinds="", initials="AI", color="#6E7781",
      note="Qualquer endpoint no formato da OpenAI (/v1/chat/completions e /v1/models): vLLM, Azure, Bedrock via gateway…"),
    P(id="custom-anthropic", name="Compatível com Anthropic", group="custom", protocol="anthropic", base_url="",
      list="anthropic", prefix="", kinds="", initials="A\\", color="#B4532A",
      note="Qualquer endpoint no formato de Mensagens da Anthropic (/v1/messages e /v1/models)."),
]
BY_ID = {p["id"]: p for p in PROVIDERS}
LOGOS = {p["id"] for p in PROVIDERS if p["group"] != "custom" and p["id"] != "litellm"}


def provider(pid: str) -> dict:
    return BY_ID.get(pid) or BY_ID["custom"]


def providers_view() -> list[dict]:
    return [{**p, "logo": f"/ui/providers/{p['id']}.png" if p["id"] in LOGOS else ""} for p in PROVIDERS]


# ------------------------------------------------------------------ tipo de cada modelo
_NAME_KIND = [
    ("rerank", re.compile(r"rerank")),
    ("embedding", re.compile(r"embed|(^|[/-])(bge|e5|gte)-|text-similarity|voyage-(?!.*rerank)|retrieval")),
    ("stt", re.compile(r"whisper|transcri|speech-to-text|(^|[/-])(stt|asr)\b|nova-[23]|parakeet|scribe")),
    ("tts", re.compile(r"(^|[/-])tts|text-to-speech|playai|sonic|kokoro|orpheus|speech-\d|eleven|audio-preview-tts")),
    ("image", re.compile(r"dall-e|gpt-image|imagen|flux|stable-diffusion|sdxl|(^|[/-])sd3|seedream|recraft|ideogram"
                         r"|image-(gen|preview)|hidream|qwen-image|grok-2-image|playground-v")),
    ("video", re.compile(r"(^|[/-])(veo|sora|kling|hailuo|wan2|seedance)|video")),
    ("moderation", re.compile(r"moderation|(^|[/-])(llama-)?guard|shieldgemma")),
]
_VISION = re.compile(r"vision|(^|[-/])vl\b|-vl-|4o|gpt-4\.1|gpt-5|claude-(3|sonnet|opus|haiku)|gemini|pixtral|llava|"
                     r"llama-4|grok-4|glm-4\.\dv|qwen.*-vl|omni")
_REASON = re.compile(r"(^|[-/])o[134]\b|(^|[-/])o[134]-|r1\b|reason|thinking|qwq|deepseek-v[34]|gpt-5|claude-(sonnet|opus)-4")
_LITELLM_MODE = {"chat": "text", "completion": "text", "responses": "text", "embedding": "embedding", "rerank": "rerank",
                 "image_generation": "image", "image_edit": "image", "video_generation": "video", "audio_speech": "tts",
                 "audio_transcription": "stt", "moderation": "moderation", "realtime": "text"}
_TOGETHER_TYPE = {"chat": "text", "language": "text", "code": "text", "embedding": "embedding", "rerank": "rerank",
                  "image": "image", "video": "video", "audio": "tts", "transcribe": "stt", "moderation": "moderation"}


def kind_from_name(model_id: str) -> str:
    low = model_id.lower()
    for kind, rx in _NAME_KIND:
        if rx.search(low):
            return kind
    return "text"


def caps_from_name(model_id: str, kind: str) -> list[str]:
    if kind != "text":
        return []
    low = model_id.lower()
    return [c for c, rx in (("vision", _VISION), ("reasoning", _REASON)) if rx.search(low)]


def _per_mtok(v) -> float | None:
    """Preço por token (string ou número) -> US$ por 1M tokens."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f * 1_000_000, 6) if f >= 0 else None


def _num(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_item(m: dict) -> dict | None:
    """Um modelo da resposta do provedor -> {id, name, kind?, caps, context_length, price_in, price_out}. Lê os campos
    de OpenRouter, LiteLLM (/model/info), Together, Mistral, Cohere, Groq e Anthropic; o resto só tem o id."""
    mid = m.get("id") or m.get("model_name") or m.get("name") or ""
    if not isinstance(mid, str) or not mid:
        return None
    mid = mid.removeprefix("models/")  # Gemini
    out = {"id": mid, "name": m.get("display_name") or m.get("name") or mid, "kind": None, "caps": [],
           "context_length": None, "price_in": None, "price_out": None}
    if isinstance(m.get("architecture"), dict):  # OpenRouter
        a = m["architecture"]
        outs, ins = a.get("output_modalities") or [], a.get("input_modalities") or []
        out["kind"] = next((k for o, k in (("embeddings", "embedding"), ("rerank", "rerank"), ("transcription", "stt"),
                                            ("speech", "tts"), ("video", "video"), ("image", "image"),
                                            ("decisions", "other")) if o in outs), "text")
        out["caps"] += ["audio_out"] if "audio" in outs else []
        out["caps"] += ["vision"] if "image" in ins else []
        out["caps"] += ["audio_in"] if "audio" in ins else []
        sp = m.get("supported_parameters") or []
        out["caps"] += [c for c, p in (("tools", "tools"), ("reasoning", "reasoning")) if p in sp]
        out["context_length"] = _num(m.get("context_length"))
        pr = m.get("pricing") or {}
        out["price_in"], out["price_out"] = _per_mtok(pr.get("prompt")), _per_mtok(pr.get("completion"))
        out["name"] = m.get("name") or mid
    elif isinstance(m.get("model_info"), dict):  # LiteLLM /model/info
        i = m["model_info"]
        out["id"] = m.get("model_name") or mid
        out["name"] = out["id"]
        out["kind"] = _LITELLM_MODE.get(i.get("mode") or "", None)
        out["caps"] += [c for c, f in (("vision", "supports_vision"), ("tools", "supports_function_calling"),
                                       ("reasoning", "supports_reasoning")) if i.get(f)]
        out["context_length"] = _num(i.get("max_input_tokens") or i.get("max_tokens"))
        out["price_in"], out["price_out"] = _per_mtok(i.get("input_cost_per_token")), _per_mtok(i.get("output_cost_per_token"))
    elif isinstance(m.get("type"), str) and m["type"] in _TOGETHER_TYPE:  # Together
        out["kind"] = _TOGETHER_TYPE[m["type"]]
        out["context_length"] = _num(m.get("context_length"))
        pr = m.get("pricing") or {}
        if isinstance(pr, dict) and (pr.get("input") or pr.get("output")):
            out["price_in"], out["price_out"] = pr.get("input"), pr.get("output")
    elif isinstance(m.get("capabilities"), dict):  # Mistral
        c = m["capabilities"]
        out["caps"] += [k for k, f in (("vision", "vision"), ("tools", "function_calling")) if c.get(f)]
        out["context_length"] = _num(m.get("max_context_length"))
        if not c.get("completion_chat") and "embed" in mid:
            out["kind"] = "embedding"
    elif isinstance(m.get("endpoints"), list):  # Cohere
        eps = m["endpoints"]
        out["kind"] = "rerank" if "rerank" in eps else "embedding" if "embed" in eps else "text" if "chat" in eps else None
        out["context_length"] = _num(m.get("context_length"))
    else:
        out["context_length"] = _num(m.get("context_window") or m.get("context_length") or m.get("max_model_len"))
    return out


# ------------------------------------------------------------------ tabela pública de modelos (LiteLLM, MIT)
_catalog: dict = {"data": None, "at": 0.0}
_catalog_lock = threading.Lock()


def public_catalog() -> dict:
    """model_prices_and_context_window.json do LiteLLM: preço, contexto e tipo de ~2 mil modelos. Baixado no máximo
    uma vez por dia; vazio se MODEL_CATALOG_URL estiver vazio (ambiente sem internet) ou o download falhar."""
    if not config.MODEL_CATALOG_URL:
        return {}
    with _catalog_lock:
        if _catalog["data"] is not None and time.time() - _catalog["at"] < 86400:
            return _catalog["data"]
        try:
            r = httpx.get(config.MODEL_CATALOG_URL, timeout=30, follow_redirects=True)
            data = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            data = {}
        if isinstance(data, dict) and data:
            data.pop("sample_spec", None)
            _catalog.update(data=data, at=time.time())
        elif _catalog["data"] is None:
            _catalog.update(data={}, at=time.time() - 86400 + 600)  # tenta de novo em 10 minutos
        return _catalog["data"]


def catalog_entry(cat: dict, prov: dict, model_id: str) -> tuple[dict | None, bool]:
    """(entrada, exata). Exata = o nome bate com o provedor (o preço vale); só o fim do nome bate = só o tipo vale."""
    pre = prov.get("prefix") or ""
    tail = model_id.rsplit("/", 1)[-1]
    for key in ([pre + model_id] if pre else []) + [model_id]:
        if key in cat:
            return cat[key], True
    for key in (pre + tail if pre else None, tail):
        if key and key in cat:
            return cat[key], False
    return None, False


# ------------------------------------------------------------------ lista do provedor
class ModelsError(Exception):
    pass


def _get(url: str, headers: dict, params: dict | None = None):
    try:
        r = httpx.get(url, headers=headers, params=params, timeout=25, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ModelsError(f"não consegui conectar em {url}: {type(e).__name__}") from None
    if r.status_code >= 400:
        hint = {401: "chave inválida", 403: "chave sem permissão", 404: "o provedor não tem lista de modelos neste endereço"}
        raise ModelsError(f"HTTP {r.status_code} em {url}" + (f" ({hint[r.status_code]})" if r.status_code in hint else ""))
    try:
        return r.json()
    except ValueError:
        raise ModelsError(f"{url} não devolveu JSON") from None


def _items(j) -> list:
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("data", "models", "items"):
            if isinstance(j.get(k), list):
                return j[k]
    return []


def fetch_models(conn: LlmConnection, key: str) -> list[dict]:
    """Pergunta ao provedor quais modelos ele tem. Levanta ModelsError com uma mensagem para o admin."""
    prov = provider(conn.provider)
    style = prov["list"] if conn.provider in BY_ID else ("anthropic" if conn.protocol == "anthropic" else "openai")
    base = conn.base_url.rstrip("/")
    bearer = {"Authorization": f"Bearer {key}"} if key else {}
    if style == "none":
        return []
    if style == "anthropic":
        root = base.removesuffix("/v1")
        raw = _items(_get(f"{root}/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}, {"limit": 1000}))
    elif style == "litellm":  # /model/info traz o tipo de cada modelo, mas exige chave de admin do LiteLLM;
        raw = None             # com chave virtual, fica a lista comum (/models) e o tipo sai da tabela pública e do nome
        for url in (f"{base}/model/info", f"{base.removesuffix('/v1')}/model/info"):
            try:
                raw = _items(_get(url, bearer))
                break
            except ModelsError:
                continue
        if raw is None:
            raw = _items(_get(f"{base}/models", bearer))
    elif style == "cohere":
        raw = _items(_get("https://api.cohere.com/v1/models", bearer, {"page_size": 1000}))
    else:
        raw = _items(_get(f"{base}/models", bearer, prov.get("list_params") if conn.provider in BY_ID else None))
    out, seen = [], set()
    for m in raw[:MAX_MODELS]:
        it = parse_item(m) if isinstance(m, dict) else ({"id": m, "name": m, "kind": None, "caps": [], "context_length": None,
                                                          "price_in": None, "price_out": None} if isinstance(m, str) else None)
        if it and it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out


def enrich(items: list[dict], prov: dict, cat: dict | None = None) -> list[dict]:
    """Completa tipo, contexto e preço com a tabela pública e, por último, com o nome."""
    cat = public_catalog() if cat is None else cat
    for it in items:
        it["kind_source"] = "provider" if it.get("kind") else ""
        it["price_source"] = "provider" if it.get("price_in") is not None or it.get("price_out") is not None else ""
        e, exact = catalog_entry(cat, prov, it["id"]) if cat else (None, False)
        if e:
            if not it.get("kind") and _LITELLM_MODE.get(e.get("mode") or ""):
                it["kind"], it["kind_source"] = _LITELLM_MODE[e["mode"]], "catalog"
            it["context_length"] = it.get("context_length") or _num(e.get("max_input_tokens") or e.get("max_tokens"))
            if exact and not it["price_source"] and (e.get("input_cost_per_token") is not None):
                it["price_in"], it["price_out"] = _per_mtok(e.get("input_cost_per_token")), _per_mtok(e.get("output_cost_per_token"))
                it["price_source"] = "catalog"
            if exact or not it["caps"]:
                it["caps"] = sorted(set(it["caps"]) | {c for c, f in (("vision", "supports_vision"), ("tools", "supports_function_calling"),
                                                                     ("reasoning", "supports_reasoning")) if e.get(f)})
        if not it.get("kind"):
            it["kind"], it["kind_source"] = kind_from_name(it["id"]), "name"
        if not it["caps"]:
            it["caps"] = caps_from_name(it["id"], it["kind"])
    return items


# ------------------------------------------------------------------ guardar e atualizar
def sync_connection(db: Session, conn: LlmConnection, actor: str = "agendador") -> dict:
    """Atualiza a lista de modelos da conexão. Nunca levanta: um provedor fora do ar só marca o erro."""
    key = crypto.decrypt(conn.api_key) if conn.api_key else ""
    prov = provider(conn.provider)
    t = now()
    try:
        items = enrich(fetch_models(conn, key), prov)
    except ModelsError as e:
        conn.models_error, conn.models_synced_at = str(e)[:500], t
        db.commit()
        return {"ok": False, "error": conn.models_error, "total": 0, "added": [], "lost": []}
    have = {m.model_id: m for m in db.scalars(select(LlmModel).where(LlmModel.connection_id == conn.id))}
    added, lost, seen = [], [], set()
    for it in items:
        seen.add(it["id"])
        m = have.get(it["id"])
        if m is None:
            m = LlmModel(connection_id=conn.id, model_id=it["id"][:200], org_id=conn.org_id, first_seen=t)
            db.add(m)
            added.append(it["id"])
        m.name, m.available, m.last_seen = (it.get("name") or it["id"])[:200], True, t
        if m.kind_source != "admin":
            m.kind, m.kind_source = it["kind"], it["kind_source"]
        m.caps = it.get("caps") or []
        m.context_length = it.get("context_length") or m.context_length
        if m.price_source != "admin" and it.get("price_source"):
            m.price_in_per_mtok, m.price_out_per_mtok, m.price_source = it["price_in"], it["price_out"], it["price_source"]
    if prov["list"] != "none" or conn.provider not in BY_ID:
        for mid, m in have.items():
            if mid not in seen and not m.manual and m.available:
                m.available = False
                lost.append(mid)
    conn.models_error, conn.models_synced_at = "", t
    if not conn.model_name:
        conn.model_name = default_model(items)
    db.commit()
    if added or lost:
        audit(db, actor, "llm_models.sync", conn.name, f"+{len(added)} −{len(lost)} de {len(items)}"
              + (f" · sumiram: {', '.join(lost[:10])}" if lost else ""))
    return {"ok": True, "error": "", "total": len(items), "added": added, "lost": lost}


def default_model(items: list[dict]) -> str:
    """Modelo padrão de uma conexão nova sem modelo escolhido: o primeiro de texto com ferramentas, senão de texto."""
    text = [i for i in items if i.get("kind") == "text"]
    pick = next((i for i in text if "tools" in (i.get("caps") or [])), None) or (text[0] if text else None)
    return pick["id"] if pick else ""


def refresh_due(max_age_s: float | None = None) -> list[str]:
    """As conexões (de todas as empresas) com a lista mais velha que LLM_MODELS_REFRESH_MIN. O agendador chama."""
    from ..db import SessionLocal
    max_age = timedelta(seconds=max_age_s if max_age_s is not None else config.LLM_MODELS_REFRESH_MIN * 60)
    done = []
    with SessionLocal() as db:
        conns = list(db.scalars(select(LlmConnection)))
        for c in conns:
            last = c.models_synced_at
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=now().tzinfo)
            if last is not None and now() - last < max_age:
                continue
            with tenancy.using(c.org_id):
                try:
                    r = sync_connection(db, c)
                except Exception:
                    db.rollback()
                    log.exception("falha ao atualizar os modelos de %s", c.name)
                    continue
            done.append(c.name)
            if not r["ok"]:
                log.warning("modelos de %s: %s", c.name, r["error"])
    return done


# ------------------------------------------------------------------ leitura e ajustes do admin
def model_dict(m: LlmModel, conn: LlmConnection | None = None) -> dict:
    return {"id": m.model_id, "name": m.name, "kind": m.kind, "kind_source": m.kind_source, "caps": m.caps or [],
            "context_length": m.context_length, "price_in_per_mtok": m.price_in_per_mtok,
            "price_out_per_mtok": m.price_out_per_mtok, "price_source": m.price_source, "available": bool(m.available),
            "manual": bool(m.manual), "first_seen": iso(m.first_seen), "last_seen": iso(m.last_seen),
            **({"connection": conn.name, "provider": conn.provider, "protocol": conn.protocol,
                "default": conn.model_name == m.model_id} if conn else {})}


def list_models(db: Session, connection: str = "", kind: str = "", available_only: bool = True) -> list[dict]:
    q = select(LlmModel, LlmConnection).join(LlmConnection, LlmConnection.id == LlmModel.connection_id)
    if connection:
        q = q.where(LlmConnection.name == connection)
    if kind:
        if kind not in KINDS:
            raise PlatformError(f"kind: {' | '.join(KINDS)}")
        q = q.where(LlmModel.kind == kind)
    if available_only:
        q = q.where(LlmModel.available.is_(True))
    from . import model_profiles as mp
    profs, cost = mp.profiles_by_key(db), mp.cost_scores(db)
    out = []
    for m, c in db.execute(q.order_by(LlmConnection.name, LlmModel.model_id)):
        d = model_dict(m, c)
        d["profile"] = mp.profile_dict(profs.get(mp.key_of(m.model_id)), cost, full=False)
        out.append(d)
    return out


def kinds_summary(db: Session, conn: LlmConnection) -> dict:
    out: dict[str, int] = {}
    for k in db.scalars(select(LlmModel.kind).where(LlmModel.connection_id == conn.id, LlmModel.available.is_(True))):
        out[k] = out.get(k, 0) + 1
    return out


def _model(db: Session, conn: LlmConnection, model_id: str) -> LlmModel:
    m = db.scalar(select(LlmModel).where(LlmModel.connection_id == conn.id, LlmModel.model_id == model_id))
    if not m:
        raise PlatformError(f"O modelo '{model_id}' não está na lista da conexão '{conn.name}'")
    return m


def add_manual(db: Session, conn: LlmConnection, model_id: str, kind: str = "", price_in=None, price_out=None,
               context_length=None, actor="admin") -> LlmModel:
    """Provedor sem lista de modelos (Perplexity, Voyage, Jina…) ou um modelo que a lista não mostra."""
    model_id = (model_id or "").strip()
    if not model_id:
        raise PlatformError("informe o id do modelo")
    kind = kind or kind_from_name(model_id)
    if kind not in KINDS:
        raise PlatformError(f"kind: {' | '.join(KINDS)}")
    m = db.scalar(select(LlmModel).where(LlmModel.connection_id == conn.id, LlmModel.model_id == model_id))
    if m is None:
        m = LlmModel(connection_id=conn.id, model_id=model_id[:200], name=model_id[:200], org_id=conn.org_id)
        db.add(m)
    m.kind, m.kind_source, m.manual, m.available, m.last_seen = kind, "admin", True, True, now()
    m.caps = caps_from_name(model_id, kind)
    if context_length:
        m.context_length = int(context_length)
    if price_in is not None or price_out is not None:
        m.price_in_per_mtok, m.price_out_per_mtok, m.price_source = price_in, price_out, "admin"
    if not conn.model_name and kind == "text":
        conn.model_name = model_id
    db.commit()
    audit(db, actor, "llm_models.add", conn.name, f"{model_id} ({kind})")
    return m


def edit_model(db: Session, conn: LlmConnection, model_id: str, kind: str | None = None, price_in=None, price_out=None,
               set_price: bool = False, actor="admin") -> LlmModel:
    m = _model(db, conn, model_id)
    if kind is not None:
        if kind not in KINDS:
            raise PlatformError(f"kind: {' | '.join(KINDS)}")
        m.kind, m.kind_source = kind, "admin"
        m.caps = [c for c in (m.caps or []) if kind == "text"]
    if set_price:
        m.price_in_per_mtok, m.price_out_per_mtok, m.price_source = price_in, price_out, "admin" if (
            price_in is not None or price_out is not None) else ""
    db.commit()
    audit(db, actor, "llm_models.edit", conn.name, f"{model_id}: {m.kind}"
          + (f" · US$ {m.price_in_per_mtok} / {m.price_out_per_mtok}" if set_price else ""))
    return m


def remove_model(db: Session, conn: LlmConnection, model_id: str, actor="admin"):
    m = _model(db, conn, model_id)
    if m.available and not m.manual:
        raise PlatformError("o modelo ainda está na lista do provedor; só dá para tirar os incluídos à mão ou os que sumiram")
    users = agents_using(db, conn, model_id)
    if users:
        raise PlatformError(f"Modelo em uso por: {', '.join(users)}. Troque o modelo desses agentes antes.")
    db.delete(m)
    db.commit()
    audit(db, actor, "llm_models.remove", conn.name, model_id)


def set_default(db: Session, conn: LlmConnection, model_id: str, actor="admin") -> LlmConnection:
    m = _model(db, conn, model_id)
    if not m.available:
        raise PlatformError(f"o modelo '{model_id}' sumiu da lista do provedor")
    conn.model_name = model_id
    db.commit()
    audit(db, actor, "llm_connection.default_model", conn.name, model_id)
    return conn


# ------------------------------------------------------------------ uso e preço
def _effective(spec: dict, env: str) -> list[tuple[str, str]]:
    """(conexão, modelo) que o agente usa neste ambiente; modelo vazio = o padrão da conexão."""
    out = []
    for key in ("llm", "harness", "judge"):
        b = spec.get(key) or {}
        o = b.get(env) if isinstance(b.get(env), dict) else {}
        c = o.get("connection") or b.get("connection")
        if c:
            out.append((c, o.get("model") or b.get("model") or ""))
    return out


def agents_using(db: Session, conn: LlmConnection, model_id: str) -> list[str]:
    users = []
    for a in db.scalars(select(Agent)):
        spec = spec_of(a)
        for env in ("stage", "prod"):
            if any(c == conn.name and (m or conn.model_name) == model_id for c, m in _effective(spec, env)):
                users.append(a.slug)
                break
    return users


def lost_in_use(db: Session) -> list[dict]:
    """Modelos que sumiram do provedor e algum agente ainda usa (a caixa de entrada dos admins mostra)."""
    out = []
    q = select(LlmModel, LlmConnection).join(LlmConnection, LlmConnection.id == LlmModel.connection_id).where(
        LlmModel.available.is_(False))
    for m, c in db.execute(q):
        users = agents_using(db, c, m.model_id)
        if users:
            out.append({"connection": c.name, "model": m.model_id, "agents": users, "at": m.last_seen})
    return out


def prices_for(db: Session, conn: LlmConnection, model: str) -> tuple:
    """Preço por 1M tokens do modelo usado: o que o admin pôs na conexão vale para o modelo padrão; os outros modelos
    usam o preço da lista; sem preço na lista, o da conexão."""
    model = model or conn.model_name
    manual = (conn.price_in_per_mtok, conn.price_out_per_mtok)
    if model == conn.model_name and manual != (None, None):
        return manual
    m = db.scalar(select(LlmModel).where(LlmModel.connection_id == conn.id, LlmModel.model_id == model))
    if m and (m.price_in_per_mtok is not None or m.price_out_per_mtok is not None):
        return (m.price_in_per_mtok, m.price_out_per_mtok)
    return manual


def check_model(db: Session, conn_name: str, model: str) -> str:
    """Erro (texto) se o modelo pedido não está disponível na conexão; vazio se está ok ou se não há lista para conferir."""
    if not conn_name or not model:
        return ""
    conn = db.scalar(select(LlmConnection).where(LlmConnection.name == conn_name))
    if conn is None:
        return ""
    known = db.scalars(select(LlmModel).where(LlmModel.connection_id == conn.id)).all()
    if not known:
        return ""
    m = next((x for x in known if x.model_id == model), None)
    if m is None:
        return f"o modelo '{model}' não está na lista da conexão '{conn_name}' (veja list_llm_models)"
    if not m.available:
        return f"o modelo '{model}' sumiu da lista do provedor da conexão '{conn_name}'"
    return ""
