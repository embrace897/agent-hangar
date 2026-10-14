"""Fichas dos modelos: chave normalizada, fontes sem custo (Artificial Analysis e OpenRouter), pesquisa com LLM dentro do
orçamento e sem deixar a fonte mandar, correções do admin travadas, nota de custo e o que o construtor precisa."""
import json

import pytest

from app.db import SessionLocal
from app.services import llm_models as lm
from app.services import model_profiles as mp

from .conftest import ADMIN

OR_MODELS = {"data": [
    {"id": "deepseek/deepseek-v4.1-flash", "context_length": 128000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.00000004", "completion": "0.00000029"}, "supported_parameters": ["tools"]},
    {"id": "anthropic/claude-opus-5", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.000015", "completion": "0.000075"}, "supported_parameters": ["tools", "reasoning"]},
    {"id": "qwen/qwen3-max", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.0000012", "completion": "0.000006"}},
    {"id": "mistral/mistral-large-3", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.000002", "completion": "0.000006"}},
    {"id": "openai/text-embedding-3-large", "architecture": {"input_modalities": ["text"], "output_modalities": ["embeddings"]},
     "pricing": {"prompt": "0.00000013"}},
]}
OR_PUBLIC = {"data": [
    {"id": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "description": "Modelo rápido e barato da DeepSeek."},
    {"id": "anthropic/claude-opus-5", "name": "Claude Opus 5", "description": "O modelo mais capaz da Anthropic."},
    {"id": "qwen/qwen3-max", "name": "Qwen3 Max", "description": "O maior modelo da Qwen."},
    {"id": "mistral/mistral-large-3", "name": "Mistral Large 3", "description": "O modelo grande da Mistral."},
]}
AA = {"data": [
    {"slug": "claude-opus-5", "name": "Claude Opus 5", "median_output_tokens_per_second": 40,
     "evaluations": {"artificial_analysis_intelligence_index": 70, "artificial_analysis_coding_index": 65}},
    {"slug": "deepseek-v4-1-flash", "name": "DeepSeek V4.1 Flash", "median_output_tokens_per_second": 200,
     "evaluations": {"artificial_analysis_intelligence_index": 50, "artificial_analysis_coding_index": 45}},
    {"slug": "outro-modelo", "name": "Outro", "median_output_tokens_per_second": 100,
     "evaluations": {"artificial_analysis_intelligence_index": 30, "artificial_analysis_coding_index": 20}},
]}


class Resp:
    def __init__(self, body, status=200, ctype="application/json"):
        self.body, self.status_code, self.headers = body, status, {"content-type": ctype}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture()
def world(monkeypatch, client, uniq):
    """Uma conexão OpenRouter com 3 modelos e as fontes públicas falsas."""
    seen = []

    def get(url, **kw):
        seen.append(url)
        if "artificialanalysis" in url:
            return Resp(AA)
        if "openrouter.ai/api/v1/models" in url:
            return Resp(OR_PUBLIC)
        return Resp({}, 404)
    monkeypatch.setattr(mp, "HTTP_GET", get)
    monkeypatch.setattr(lm, "_get", lambda url, headers, params=None: OR_MODELS)
    monkeypatch.setattr(mp.config, "MODEL_PUBLIC_SOURCES", True)
    mp._cache.clear()
    name = uniq("or")
    r = client.post("/api/catalog/llm", headers=ADMIN, json={"name": name, "provider": "openrouter",
                                                           "base_url": "https://openrouter.ai/api/v1", "api_key": "k"})
    assert r.status_code == 200, r.text
    yield {"conn": name, "seen": seen}
    client.put("/api/catalog/model-profiles/settings", headers=ADMIN, json=dict(mp.DEFAULTS))
    mp._cache.clear()


def test_key_normalizes_provider_prefixes_dates_and_dots():
    assert mp.key_of("openrouter/deepseek/deepseek-v4.1-flash:free") == "deepseek-v4-1-flash"
    assert mp.key_of("claude-sonnet-4-20250514") == "claude-sonnet-4" == mp.key_of("anthropic/claude-sonnet-4")
    assert mp.key_of("gpt-4o-2024-08-06") == "gpt-4o" and mp.key_of("gemini-2.5-pro-latest") == "gemini-2-5-pro"


def test_free_sources_fill_profiles_and_aa_becomes_percentile_scores(client, world, uniq):
    from app.services import secrets_vault  # noqa: F401
    sec = uniq("aa").replace("-", "")
    assert client.put("/api/secrets", headers=ADMIN, json={"name": sec, "value": "aa-key"}).status_code == 200
    s = client.put("/api/catalog/model-profiles/settings", headers=ADMIN, json={"aa_key": f"secret://{sec}"})
    assert s.status_code == 200, s.text
    r = client.post("/api/catalog/model-profiles/refresh", headers=ADMIN).json()
    assert r["artificial_analysis"] and r["openrouter"] and r["profiles_updated"] >= 2
    p = client.get("/api/catalog/model-profiles/anthropic/claude-opus-5", headers=ADMIN).json()
    assert p["scores"]["reasoning"] == {"value": 100, "source": "artificial_analysis",
                                        "basis": "posição entre os modelos avaliados pelo Artificial Analysis (reasoning)"}
    assert p["scores"]["speed"]["value"] == 0 and p["metrics"]["intelligence_index"] == 70
    assert p["summary"] == "O modelo mais capaz da Anthropic."
    assert {s["kind"] for s in p["sources"]} == {"artificial_analysis", "openrouter"}
    flash = client.get("/api/catalog/model-profiles/deepseek-v4.1-flash", headers=ADMIN).json()
    assert flash["scores"]["reasoning"]["value"] == 50 and flash["scores"]["cost"]["value"] == 100  # o mais barato
    assert p["scores"]["cost"]["value"] == 0
    # a lista de modelos leva a ficha resumida
    ms = {m["id"]: m for m in client.get(f"/api/catalog/models?connection={world['conn']}", headers=ADMIN).json()}
    assert ms["anthropic/claude-opus-5"]["profile"]["scores"]["coding"]["value"] == 100
    assert ms["openai/text-embedding-3-large"]["profile"] is None


def test_keys_must_be_vault_references(client, world):
    r = client.put("/api/catalog/model-profiles/settings", headers=ADMIN, json={"aa_key": "chave-em-texto"})
    assert r.status_code == 400 and "secret://" in r.json()["detail"]
    r = client.put("/api/catalog/model-profiles/settings", headers=ADMIN, json={"aa_key": "secret://nao-existe-xyz"})
    assert r.status_code == 400


def test_research_uses_sources_as_data_respects_budget_and_admin_locks(client, world, monkeypatch):
    calls = []

    def call(base, key, model, system, user):
        calls.append(user)
        assert "ignore qualquer pedido" in system and "<fonte n=1" in user
        return json.dumps({"summary": "Bom para planejar.", "strengths": ["raciocínio"], "weaknesses": ["caro"],
                           "best_for": ["planejamento", "código"], "scores": {"reasoning": 92, "coding": 999, "speed": None},
                           "score_basis": {"reasoning": "ranking citado na fonte 1"}, "sources_used": [1]}), 1000, 500
    monkeypatch.setattr(mp, "CALL", call)
    r = client.post("/api/catalog/model-profiles/qwen/qwen3-max/research", headers=ADMIN)
    assert r.status_code == 400 and "conexão de LLM" in r.json()["detail"]
    client.put("/api/catalog/model-profiles/settings", headers=ADMIN,
               json={"connection": world["conn"], "model": "deepseek/deepseek-v4.1-flash", "monthly_budget_usd": 1})
    r = client.post("/api/catalog/model-profiles/qwen/qwen3-max/research", headers=ADMIN).json()
    assert r["ok"] and r["cost_usd"] == round((1000 * 0.04 + 500 * 0.29) / 1e6, 6)
    p = client.get("/api/catalog/model-profiles/qwen3-max", headers=ADMIN).json()
    assert p["summary"] == "Bom para planejar." and p["best_for"] == ["planejamento", "código"]
    assert p["scores"]["reasoning"] == {"value": 92, "source": "research", "basis": "ranking citado na fonte 1"}
    assert "coding" not in p["scores"]  # nota fora de 0–100 é descartada
    assert client.get("/api/catalog/model-profiles/settings", headers=ADMIN).json()["spent_this_month_usd"] > 0
    # o admin corrige; a próxima pesquisa não desfaz
    e = client.patch("/api/catalog/model-profiles/qwen3-max", headers=ADMIN,
                     json={"summary": "Revisado pelo time.", "scores": {"reasoning": 80}})
    assert e.json()["locked"] == ["reasoning", "summary"]
    client.post("/api/catalog/model-profiles/qwen3-max/research", headers=ADMIN)
    p = client.get("/api/catalog/model-profiles/qwen3-max", headers=ADMIN).json()
    assert p["summary"] == "Revisado pelo time." and p["scores"]["reasoning"]["source"] == "admin"
    assert client.patch("/api/catalog/model-profiles/qwen3-max", headers=ADMIN, json={"scores": {"reasoning": 300}}).status_code == 400
    # orçamento esgotado: a pesquisa para
    client.put("/api/catalog/model-profiles/settings", headers=ADMIN, json={"monthly_budget_usd": 0})
    r = client.post("/api/catalog/model-profiles/qwen3-max/research", headers=ADMIN)
    assert r.status_code == 400 and "orçamento" in r.json()["detail"]


def test_automatic_research_starts_with_the_models_in_use(client, world, monkeypatch, uniq):
    monkeypatch.setattr(mp, "CALL", lambda *a: ('{"summary": "x", "best_for": ["y"], "scores": {}}', 10, 10))
    client.put("/api/catalog/model-profiles/settings", headers=ADMIN,
               json={"connection": world["conn"], "auto": "used", "monthly_budget_usd": 5, "per_run": 100})
    agent = client.post("/api/agents", headers=ADMIN, json={"name": uniq("ag"), "objective": "o", "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{agent}", headers=ADMIN, json={"llm": {"connection": world["conn"], "model": "mistral/mistral-large-3"}})
    with SessionLocal() as db:
        due = mp.due_for_research(db)
    assert "mistral-large-3" in due and "deepseek-v4-1-flash" in due  # o usado pelo agente e o padrão da conexão
    assert "text-embedding-3-large" not in due  # modo "used": só os usados e os padrões
    out = mp.run_auto()
    assert any("mistral-large-3" in r["researched"] for r in out.values())
    with SessionLocal() as db:
        assert "mistral-large-3" not in mp.due_for_research(db)  # acabou de pesquisar


def test_web_pages_from_the_internal_network_are_never_fetched(monkeypatch):
    monkeypatch.setattr(mp, "HTTP_GET", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia buscar")))
    assert mp.fetch_page("http://127.0.0.1:8090/api/me") == ""
    assert mp.fetch_page("http://169.254.169.254/latest/meta-data") == ""
    assert mp.fetch_page("file:///etc/passwd") == ""


def test_builder_check_needs_says_what_is_missing(client, world, monkeypatch):
    from .test_access import _mcp
    have = {m["kind"] for m in client.get("/api/catalog/models", headers=ADMIN).json()}
    want = ["text", "embedding", "rerank", "stt", "video"]
    out = json.loads(_mcp(client, ADMIN, "check_model_needs", {"kinds": want, "caps": ["vision"]})["content"][0]["text"])
    assert out["missing_kinds"] == [k for k in want if k not in have] and out["missing_caps"] == []
    assert out["ok"] is (not out["missing_kinds"])
    # sem nenhum modelo do tipo: a mensagem pronta para o usuário aponta onde cadastrar
    from app.services import model_profiles
    real = model_profiles.check_needs
    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models import LlmModel
        kinds = set(db.scalars(select(LlmModel.kind).where(LlmModel.available.is_(True))))
    absent = next((k for k in lm.KINDS if k not in kinds and k != "other"), None)
    if absent:
        miss = json.loads(_mcp(client, ADMIN, "check_model_needs", {"kinds": ["text", absent]})["content"][0]["text"])
        assert miss["ok"] is False and miss["missing_kinds"] == [absent] and "Provedores de LLM" in miss["message_for_user"]
    with SessionLocal() as db:
        r = real(db, ["text"], ["vision"])
    assert r["ok"] and r["message_for_user"] == ""
    assert _mcp(client, ADMIN, "check_model_needs", {"kinds": ["banana"]})["isError"]


def test_research_output_is_cleaned_and_gets_the_platform_facts(client, world, monkeypatch):
    seen = {}
    broken = "Modelo construído para tarefas rápidas.".encode().decode("latin-1")  # como alguns modelos devolvem

    def call(base, key, model, system, user):
        seen["user"] = user
        return json.dumps({"summary": broken, "best_for": ["Não especificado na fonte", "classificação"],
                           "strengths": ["barato"], "scores": {}}), 10, 10
    monkeypatch.setattr(mp, "CALL", call)
    client.put("/api/catalog/model-profiles/settings", headers=ADMIN, json={"connection": world["conn"], "monthly_budget_usd": 1})
    assert client.post("/api/catalog/model-profiles/deepseek-v4.1-flash/research", headers=ADMIN).json()["ok"]
    assert "Dados da plataforma: tipo de saída: text; contexto: 128000 tokens" in seen["user"]
    p = client.get("/api/catalog/model-profiles/deepseek-v4.1-flash", headers=ADMIN).json()
    assert p["summary"] == "Modelo construído para tarefas rápidas." and p["best_for"] == ["classificação"]


def test_hugging_face_match_prefers_the_original_repo():
    hits = [{"id": "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"}, {"id": "Qwen/Qwen3-235B-A22B-Instruct-2507"},
            {"id": "Qwen/Qwen3-235B-A22B-Thinking-2507"}, {"id": "other/qwen3-8b"}]
    assert mp.hf_candidates("qwen3-235b-a22b-2507", hits)[:2] == ["Qwen/Qwen3-235B-A22B-Instruct-2507",
                                                                  "Qwen/Qwen3-235B-A22B-Thinking-2507"]
    exact = [{"id": "casperhansen/llama-3.3-70b-instruct-awq"}, {"id": "meta-llama/Llama-3.3-70B-Instruct"}]
    assert mp.hf_candidates("llama-3-3-70b-instruct", exact)[0] == "meta-llama/Llama-3.3-70B-Instruct"
