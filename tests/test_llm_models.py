"""Provedores de LLM e a lista de modelos: coleta por provedor (formatos de OpenRouter, LiteLLM, Anthropic, OpenAI),
tipo de saída de cada modelo, preço, modelo que some, correções do admin, escolha do modelo do agente e a ferramenta
do construtor."""
import pytest

from app import services as svc
from app.db import SessionLocal
from app.services import llm_models as lm

from .conftest import ADMIN

OPENROUTER = {"data": [
    {"id": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "context_length": 128000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.00000004", "completion": "0.00000029"}, "supported_parameters": ["tools", "reasoning"]},
    {"id": "openai/gpt-5-image", "name": "GPT-5 Image",
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]}, "pricing": {}},
    {"id": "google/gemini-3-pro", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.000002", "completion": "0.000012"}},
]}
OPENROUTER_ALL = [{"id": i, "architecture": {"input_modalities": ["text"], "output_modalities": o}} for i, o in (
    ("openai/text-embedding-3-large", ["embeddings"]), ("cohere/rerank-v3.5", ["rerank"]), ("openai/whisper-1", ["transcription"]),
    ("openai/tts-1", ["speech"]), ("google/veo-3", ["video"]), ("google/gemini-3-pro-image", ["image", "text"]))]
LITELLM = {"data": [
    {"model_name": "chat-fast", "model_info": {"mode": "chat", "max_input_tokens": 64000, "supports_function_calling": True,
                                               "input_cost_per_token": 1e-7, "output_cost_per_token": 4e-7}},
    {"model_name": "embed-pt", "model_info": {"mode": "embedding", "input_cost_per_token": 2e-8}},
    {"model_name": "rerank-v3", "model_info": {"mode": "rerank"}},
    {"model_name": "voz", "model_info": {"mode": "audio_speech"}},
    {"model_name": "ouvido", "model_info": {"mode": "audio_transcription"}},
]}
OPENAI = {"data": [{"id": x} for x in ("gpt-4.1-mini", "text-embedding-3-small", "whisper-1", "tts-1", "dall-e-3",
                                        "omni-moderation-latest")]}


@pytest.fixture()
def api(monkeypatch):
    """Provedores falsos: devolve a resposta pela URL pedida."""
    routes = {}

    def get(url, headers, params=None):
        for frag, body in routes.items():
            if frag in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise lm.ModelsError(f"HTTP 404 em {url}")
    monkeypatch.setattr(lm, "_get", get)
    return routes


def _add(client, name, provider, url, model="", key="k"):
    return client.post("/api/catalog/llm", headers=ADMIN, json={"name": name, "provider": provider, "base_url": url,
                                                                 "api_key": key, "model_name": model})


def test_kind_from_name_covers_the_usual_families():
    assert [lm.kind_from_name(x) for x in ("text-embedding-3-large", "bge-m3", "rerank-2.5", "whisper-large-v3",
                                            "gpt-4o-mini-tts", "dall-e-3", "flux-1.1-pro", "veo-3", "llama-guard-4",
                                            "gpt-4.1")] == [
        "embedding", "embedding", "rerank", "stt", "tts", "image", "image", "video", "moderation", "text"]
    assert "vision" in lm.caps_from_name("qwen2.5-vl-72b", "text") and lm.caps_from_name("bge-m3", "embedding") == []


def test_openrouter_list_brings_kind_price_context_and_caps(client, uniq, api):
    api["openrouter.ai/api/v1/models"] = OPENROUTER
    name = uniq("or")
    r = _add(client, name, "openrouter", "https://openrouter.ai/api/v1")
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["sync"]["ok"] and c["sync"]["total"] == 3 and c["provider"] == "openrouter"
    assert c["model_name"] == "deepseek/deepseek-v4.1-flash"  # sem modelo escolhido: o primeiro de texto com ferramentas
    assert c["models"] == {"text": 2, "image": 1}
    ms = {m["id"]: m for m in client.get(f"/api/catalog/models?connection={name}", headers=ADMIN).json()}
    ds = ms["deepseek/deepseek-v4.1-flash"]
    assert (ds["price_in_per_mtok"], ds["price_out_per_mtok"], ds["context_length"]) == (0.04, 0.29, 128000)
    assert set(ds["caps"]) == {"tools", "reasoning"} and ds["kind_source"] == "provider" and ds["default"]
    assert ms["openai/gpt-5-image"]["kind"] == "image" and "vision" in ms["google/gemini-3-pro"]["caps"]


def test_openrouter_asks_for_every_output_type(client, uniq, monkeypatch):
    seen = {}

    def get(url, headers, params=None):
        seen["params"] = params
        return {"data": OPENROUTER["data"] + OPENROUTER_ALL}
    monkeypatch.setattr(lm, "_get", get)
    c = _add(client, uniq("or"), "openrouter", "https://openrouter.ai/api/v1").json()
    assert seen["params"] == {"output_modalities": "all"}  # sem isso o OpenRouter só lista os de texto
    assert c["models"] == {"text": 2, "image": 2, "embedding": 1, "rerank": 1, "stt": 1, "tts": 1, "video": 1}


def test_litellm_with_a_virtual_key_falls_back_to_the_plain_list(client, uniq, api):
    api["/model/info"] = lm.ModelsError("HTTP 403 (chave sem permissão)")
    api["host.docker.internal:4000/v1/models"] = {"data": [{"id": "gpt-4.1-mini"}, {"id": "text-embedding-3-small"}]}
    c = _add(client, uniq("ll"), "litellm", "http://host.docker.internal:4000/v1").json()
    assert c["sync"]["ok"] and c["models"] == {"text": 1, "embedding": 1}


def test_litellm_model_info_gives_every_kind(client, uniq, api):
    api["/model/info"] = LITELLM
    name = uniq("ll")
    c = _add(client, name, "litellm", "http://host.docker.internal:4000/v1").json()
    assert c["models"] == {"text": 1, "embedding": 1, "rerank": 1, "tts": 1, "stt": 1}
    emb = client.get(f"/api/catalog/models?kind=embedding&connection={name}", headers=ADMIN).json()
    assert [m["id"] for m in emb] == ["embed-pt"] and emb[0]["price_in_per_mtok"] == 0.02


def test_openai_ids_only_fall_back_to_the_public_catalog_then_the_name(client, uniq, api, monkeypatch):
    api["api.openai.com/v1/models"] = OPENAI
    monkeypatch.setattr(lm, "public_catalog", lambda: {
        "gpt-4.1-mini": {"mode": "chat", "input_cost_per_token": 4e-7, "output_cost_per_token": 1.6e-6,
                         "max_input_tokens": 1047576, "supports_vision": True, "supports_function_calling": True},
        "openrouter/whisper-1": {"mode": "chat", "input_cost_per_token": 9.0}})  # outro provedor: não vale
    name = uniq("oa")
    c = _add(client, name, "openai", "https://api.openai.com/v1", model="gpt-4.1-mini").json()
    ms = {m["id"]: m for m in client.get(f"/api/catalog/models?connection={name}", headers=ADMIN).json()}
    assert ms["gpt-4.1-mini"]["kind_source"] == "catalog" and ms["gpt-4.1-mini"]["price_in_per_mtok"] == 0.4
    assert set(ms["gpt-4.1-mini"]["caps"]) == {"vision", "tools"}
    assert {k: ms[k]["kind"] for k in ("text-embedding-3-small", "whisper-1", "tts-1", "dall-e-3", "omni-moderation-latest")} == {
        "text-embedding-3-small": "embedding", "whisper-1": "stt", "tts-1": "tts", "dall-e-3": "image",
        "omni-moderation-latest": "moderation"}
    assert ms["whisper-1"]["price_in_per_mtok"] is None  # o preço de outro provedor não é usado
    assert c["models"]["text"] == 1


def test_anthropic_list_uses_its_own_headers_and_protocol(client, uniq, monkeypatch):
    seen = {}

    def anth(url, headers, params=None):
        seen.update(url=url, headers=headers)
        return {"data": [{"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"}]}
    monkeypatch.setattr(lm, "_get", anth)
    c = _add(client, uniq("an"), "anthropic", "https://api.anthropic.com").json()
    assert seen["url"] == "https://api.anthropic.com/v1/models" and "x-api-key" in seen["headers"]
    assert (c["protocol"], c["model_name"]) == ("anthropic", "claude-sonnet-5")  # o protocolo vem do provedor


def test_new_connection_without_list_and_without_model_is_not_saved(client, uniq, api):
    name = uniq("bad")
    r = _add(client, name, "openai", "https://api.openai.com/v1")
    assert r.status_code == 400 and "HTTP 404" in r.json()["detail"]
    assert name not in {c["name"] for c in client.get("/api/catalog", headers=ADMIN).json()["llm_connections"]}
    # com o modelo informado, salva e guarda o erro da lista para o admin ver
    c = _add(client, name, "openai", "https://api.openai.com/v1", model="gpt-x").json()
    assert c["model_name"] == "gpt-x" and "HTTP 404" in c["models_error"] and c["models_total"] == 0


def test_model_that_disappears_is_kept_unavailable_and_warns_admins(client, uniq, api):
    api["openrouter.ai/api/v1/models"] = OPENROUTER
    name, slug = uniq("or"), uniq("ag")
    _add(client, name, "openrouter", "https://openrouter.ai/api/v1")
    agent = client.post("/api/agents", headers=ADMIN, json={"name": slug, "objective": "o", "final_output": "f"}).json()["slug"]
    r = client.patch(f"/api/agents/{agent}", headers=ADMIN, json={"llm": {"connection": name, "model": "google/gemini-3-pro"}})
    assert r.status_code == 200, r.text
    # modelo que não está na lista da conexão é recusado
    bad = client.patch(f"/api/agents/{agent}", headers=ADMIN, json={"llm": {"connection": name, "model": "nao/existe"}})
    assert bad.status_code == 400 and "não está na lista" in bad.json()["detail"]
    api["openrouter.ai/api/v1/models"] = {"data": OPENROUTER["data"][:2]}
    s = client.post(f"/api/catalog/llm/{name}/models/sync", headers=ADMIN).json()
    assert s["lost"] == ["google/gemini-3-pro"]
    all_ = {m["id"]: m for m in client.get(f"/api/catalog/models?connection={name}&all=true", headers=ADMIN).json()}
    assert all_["google/gemini-3-pro"]["available"] is False
    assert "google/gemini-3-pro" not in {m["id"] for m in client.get(f"/api/catalog/models?connection={name}", headers=ADMIN).json()}
    with SessionLocal() as db:  # a caixa de entrada é de uma pessoa; a regra é conferida direto
        lost = [x for x in lm.lost_in_use(db) if x["connection"] == name]
    assert lost and lost[0]["agents"] == [agent]
    # o agente continua editável (o par que já usava não é conferido de novo)
    assert client.patch(f"/api/agents/{agent}", headers=ADMIN, json={"instructions": "x"}).status_code == 200


def test_admin_fixes_kind_and_price_and_sync_keeps_it(client, uniq, api):
    api["api.openai.com/v1/models"] = OPENAI
    name = uniq("oa")
    _add(client, name, "openai", "https://api.openai.com/v1", model="gpt-4.1-mini")
    r = client.patch(f"/api/catalog/llm/{name}/models/tts-1", headers=ADMIN,
                     json={"kind": "other", "set_price": True, "price_in_per_mtok": 15, "price_out_per_mtok": 0})
    assert r.json()["kind"] == "other" and r.json()["price_source"] == "admin"
    client.post(f"/api/catalog/llm/{name}/models/sync", headers=ADMIN)
    m = {x["id"]: x for x in client.get(f"/api/catalog/models?connection={name}", headers=ADMIN).json()}["tts-1"]
    assert (m["kind"], m["price_in_per_mtok"]) == ("other", 15)
    assert client.patch(f"/api/catalog/llm/{name}/models/whisper-1", headers=ADMIN, json={"kind": "banana"}).status_code == 400
    d = client.patch(f"/api/catalog/llm/{name}/models/text-embedding-3-small", headers=ADMIN, json={"default": True})
    assert d.json()["default"] is True


def test_provider_without_list_takes_models_by_hand(client, uniq, api):
    name = uniq("vo")
    r = _add(client, name, "voyage-ai", "https://api.voyageai.com/v1")
    assert r.status_code == 400  # sem lista e sem modelo padrão
    r = _add(client, name, "voyage-ai", "https://api.voyageai.com/v1", model="voyage-3.5")
    assert r.status_code == 200 and r.json()["sync"]["ok"] and r.json()["sync"]["total"] == 0
    m = client.post(f"/api/catalog/llm/{name}/models", headers=ADMIN, json={"model_id": "voyage-3.5"}).json()
    assert m["kind"] == "embedding" and m["manual"]
    client.post(f"/api/catalog/llm/{name}/models", headers=ADMIN, json={"model_id": "rerank-2.5", "price_in_per_mtok": 0.05})
    client.post(f"/api/catalog/llm/{name}/models/sync", headers=ADMIN)  # "none": a sincronização não apaga os manuais
    kinds = {x["id"]: x["kind"] for x in client.get(f"/api/catalog/models?connection={name}", headers=ADMIN).json()}
    assert kinds == {"voyage-3.5": "embedding", "rerank-2.5": "rerank"}
    assert client.delete(f"/api/catalog/llm/{name}/models/rerank-2.5", headers=ADMIN).json()["ok"]


def test_cost_uses_the_price_of_the_model_the_agent_uses(client, uniq, api):
    api["openrouter.ai/api/v1/models"] = OPENROUTER
    name = uniq("or")
    _add(client, name, "openrouter", "https://openrouter.ai/api/v1")
    with SessionLocal() as db:
        r = svc.resolve_llm(db, {"llm": {"connection": name, "model": "google/gemini-3-pro"}}, "prod")
        assert r["prices"] == (2.0, 12.0)
        assert svc.resolve_llm(db, {"llm": {"connection": name}}, "prod")["prices"] == (0.04, 0.29)
        c = svc.get_connection(db, name)
        c.price_in_per_mtok, c.price_out_per_mtok = 1.0, 1.0  # preço manual vale só para o modelo padrão
        db.commit()
        assert svc.resolve_llm(db, {"llm": {"connection": name}}, "prod")["prices"] == (1.0, 1.0)
        assert svc.resolve_llm(db, {"llm": {"connection": name, "model": "google/gemini-3-pro"}}, "prod")["prices"] == (2.0, 12.0)


def test_hourly_refresh_only_touches_stale_connections(client, uniq, api):
    api["openrouter.ai/api/v1/models"] = OPENROUTER
    name = uniq("or")
    _add(client, name, "openrouter", "https://openrouter.ai/api/v1")
    assert name not in lm.refresh_due()  # acabou de sincronizar
    assert name in lm.refresh_due(max_age_s=0)


def test_providers_catalog_and_deleting_a_connection_drops_its_models(client, uniq, api):
    p = client.get("/api/catalog/providers", headers=ADMIN).json()
    ids = {x["id"]: x for x in p["providers"]}
    assert {"openai", "anthropic", "openrouter", "litellm", "custom", "custom-anthropic"} <= set(ids)
    assert ids["openai"]["logo"] == "/ui/providers/openai.png" and ids["litellm"]["logo"] == ""
    assert "embedding" in p["kinds"]
    api["openrouter.ai/api/v1/models"] = OPENROUTER
    name = uniq("or")
    _add(client, name, "openrouter", "https://openrouter.ai/api/v1")
    assert client.delete(f"/api/catalog/llm/{name}", headers=ADMIN).json()["ok"]
    assert client.get(f"/api/catalog/models?connection={name}&all=true", headers=ADMIN).json() == []


def test_mcp_builder_sees_kinds_available_and_missing(client, uniq, api):
    import json

    from .test_access import _mcp
    api["/model/info"] = LITELLM
    name = uniq("ll")
    _add(client, name, "litellm", "http://host.docker.internal:4000/v1")
    r = _mcp(client, ADMIN, "list_llm_models", {"connection": name})
    out = json.loads(r["content"][0]["text"])
    assert set(out["kinds_available"]) == {"text", "embedding", "rerank", "tts", "stt"}
    assert {"image", "video", "moderation"} <= set(out["kinds_missing"]) and "other" not in out["kinds_missing"]
    emb = json.loads(_mcp(client, ADMIN, "list_llm_models", {"kind": "embedding", "connection": name})["content"][0]["text"])
    assert [m["id"] for m in emb["models"]] == ["embed-pt"]
    # registrar pelo MCP sem model_name: o padrão vem da lista do provedor
    reg = _mcp(client, ADMIN, "register_llm_connection", {"name": uniq("ll2"), "base_url": "http://host.docker.internal:4000/v1",
                                                         "api_key": "k", "provider": "litellm"})
    assert not reg["isError"] and json.loads(reg["content"][0]["text"])["model_name"] == "chat-fast"
