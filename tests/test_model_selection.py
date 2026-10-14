"""Escolha do modelo por papel: pesos por papel sobre as notas da ficha, desempenho real que ajusta a nota, recursos
obrigatórios, política de código, sugestão para o agente (e o time de um multiagente) sem mudar nada sozinho."""
import json

import pytest

from app.db import SessionLocal
from app.models import Agent, TestRun
from app.services import llm_models as lm
from app.services import model_selection as ms

from .conftest import ADMIN


@pytest.fixture()
def lab(client, uniq, monkeypatch):
    """Uma conexão com três modelos de nomes únicos e notas controladas pelo admin."""
    tag = uniq("m").replace("-", "")
    ids = {"big": f"lab/{tag}-big", "cheap": f"lab/{tag}-cheap", "coder": f"lab/{tag}-coder", "plain": f"lab/{tag}-plain"}
    data = [{"id": ids["big"], "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
             "supported_parameters": ["tools"], "pricing": {"prompt": "0.00001", "completion": "0.00003"}},
            {"id": ids["cheap"], "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
             "supported_parameters": ["tools"], "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}},
            {"id": ids["coder"], "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
             "supported_parameters": ["tools"], "pricing": {"prompt": "0.000002", "completion": "0.000008"}},
            {"id": ids["plain"], "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
             "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}}]
    monkeypatch.setattr(lm, "_get", lambda url, headers, params=None: {"data": data})
    conn = uniq("lab")
    r = client.post("/api/catalog/llm", headers=ADMIN, json={"name": conn, "provider": "openrouter",
                                                           "base_url": "https://openrouter.ai/api/v1", "api_key": "k",
                                                           "model_name": ids["cheap"]})
    assert r.status_code == 200, r.text
    scores = {"big": {"reasoning": 100, "coding": 70, "speed": 100, "cost": 100},
              "cheap": {"reasoning": 30, "coding": 20, "speed": 100, "cost": 100},
              "coder": {"reasoning": 60, "coding": 100, "speed": 100, "cost": 100},
              "plain": {"reasoning": 100, "coding": 100, "speed": 100, "cost": 100}}
    for k, sc in scores.items():
        assert client.patch(f"/api/catalog/model-profiles/{ids[k]}", headers=ADMIN, json={"scores": sc}).status_code == 200
    ms._SUGGEST_CACHE.clear()
    return {"conn": conn, "ids": ids}


def _rec(client, lab, role, caps="tools"):
    return client.get(f"/api/catalog/models/recommend?role={role}&caps={caps}&connection={lab['conn']}", headers=ADMIN).json()


def test_each_role_gets_the_model_that_fits_it(client, lab):
    ids = lab["ids"]
    assert _rec(client, lab, "planner")["candidates"][0]["model"] == ids["big"]
    assert _rec(client, lab, "coder")["candidates"][0]["model"] == ids["coder"]
    cl = _rec(client, lab, "classifier")
    assert cl["candidates"][0]["model"] in (ids["big"], ids["cheap"], ids["coder"])  # custo e velocidade empatam em 100
    big = next(c for c in _rec(client, lab, "planner")["candidates"] if c["model"] == ids["big"])
    assert big["confidence"] == "alta" and big["reasons"][0] == "Raciocínio 100"
    # quem não tem ferramentas fica de fora quando o agente usa ferramentas
    assert ids["plain"] not in {c["model"] for c in _rec(client, lab, "planner")["candidates"]}
    assert ids["plain"] in {c["model"] for c in _rec(client, lab, "planner", caps="")["candidates"]}
    # ler imagens: só o que tem visão
    assert [c["model"] for c in _rec(client, lab, "general", caps="tools,vision")["candidates"]] == [ids["big"]]
    assert client.get("/api/catalog/models/recommend?role=chef", headers=ADMIN).status_code == 400


def test_real_results_here_pull_the_score(client, lab, uniq):
    ids = lab["ids"]
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("ag"), "objective": "o", "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"llm": {"connection": lab["conn"], "model": ids["coder"]}})
    before = next(c for c in _rec(client, lab, "coder")["candidates"] if c["model"] == ids["coder"])
    with SessionLocal() as db:
        a = db.query(Agent).filter(Agent.slug == slug).one()
        for _ in range(40):
            db.add(TestRun(agent_id=a.id, version=a.current_version, env="stage", status="failed", org_id=a.org_id))
        db.commit()
    after = next(c for c in _rec(client, lab, "coder")["candidates"] if c["model"] == ids["coder"])
    assert after["real"]["tests"] == 40 and after["real"]["value"] == 0
    assert after["score"] < before["score"] - 30  # 40 falhas reais pesam metade da nota
    assert any("desempenho real aqui 0" in r for r in after["reasons"])


def test_agent_gets_a_suggestion_and_applying_it_is_a_normal_version(client, lab, uniq):
    ids = lab["ids"]
    spec = lambda name: {"name": name, "objective": "Orquestrar a equipe de vendas", "final_output": "plano"}  # noqa: E731
    child = client.post("/api/agents", headers=ADMIN, json={**spec(uniq("dev")), "objective": "corrigir bugs no código"}).json()["slug"]
    client.patch(f"/api/agents/{child}", headers=ADMIN, json={"llm": {"connection": lab["conn"], "model": ids["cheap"]},
                                                             "mcps": [{"name": "x", "url": "https://mcp.example.com/mcp"}]})
    boss = client.post("/api/agents", headers=ADMIN, json=spec(uniq("boss"))).json()["slug"]
    client.patch(f"/api/agents/{boss}", headers=ADMIN, json={"llm": {"connection": lab["conn"], "model": ids["cheap"]},
                                                            "sub_agents": [child]})
    team = client.get(f"/api/agents/{boss}/model-recommendation", headers=ADMIN).json()
    assert [x["role"] for x in team] == ["planner", "coder"]
    assert team[1]["caps"] == ["tools"]
    boss_rec = team[0]
    assert boss_rec["current"]["model"] == ids["cheap"] and boss_rec["suggestion"] is not None
    assert boss_rec["suggestion"]["gain"] >= ms.MARGIN
    # aplicar = um patch comum (nova versão, passa pelos testes antes de produção)
    v = client.get(f"/api/agents/{boss}", headers=ADMIN).json()["version"]
    r = client.patch(f"/api/agents/{boss}", headers=ADMIN, json=boss_rec["suggestion"]["patch"])
    assert r.status_code == 200 and client.get(f"/api/agents/{boss}", headers=ADMIN).json()["version"] == v + 1
    # o papel pode ser informado
    forced = client.get(f"/api/agents/{boss}/model-recommendation?role=classifier", headers=ADMIN).json()
    assert forced[0]["role"] == "classifier"
    # a caixa de entrada dos admins mostra quem tem sugestão
    with SessionLocal() as db:
        assert child in {x["agent"] for x in ms.cached_suggestions(db)}


def test_code_role_respects_the_code_policy(client, lab):
    client.patch("/api/org", headers=ADMIN, json={"code_policy": "approved"})
    try:
        assert _rec(client, lab, "coder")["candidates"] == []  # a conexão do laboratório não é aprovada para código
        client.patch(f"/api/catalog/llm/{lab['conn']}/code", headers=ADMIN, json={"allow_code": True})
        assert _rec(client, lab, "coder")["candidates"][0]["model"] == lab["ids"]["coder"]
        assert _rec(client, lab, "planner")["candidates"]  # outros papéis não dependem da aprovação
    finally:
        client.patch("/api/org", headers=ADMIN, json={"code_policy": "any"})


def test_role_and_caps_are_inferred_from_the_spec():
    assert ms.infer_role({"sub_agents": ["a"]}) == "planner"
    assert ms.infer_role({"tests": [{"workspace": {"files": {}}}]}) == "coder"
    assert ms.infer_role({}, "Triagem de chamados", "") == "classifier"
    assert ms.infer_role({}, "", "extrair dados de notas fiscais") == "extractor"
    assert ms.infer_role({}, "Atendimento", "responder clientes") == "support"
    assert ms.infer_role({}, "Assistente", "ajudar") == "general"
    assert ms.infer_caps({"mcps": ["x"]}, "", "ler prints de tela") == ["tools", "vision"]


def test_harness_agents_and_mcp_tools(client, lab, uniq):
    from .test_access import _mcp
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("h"), "objective": "o", "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"harness": {"id": "codex"}})
    assert client.get(f"/api/agents/{slug}/model-recommendation", headers=ADMIN).json()[0]["supported"] is False
    r = json.loads(_mcp(client, ADMIN, "recommend_models", {"role": "planner", "caps": ["tools"],
                                                           "connection": lab["conn"]})["content"][0]["text"])
    assert r["candidates"][0]["model"] == lab["ids"]["big"] and r["role_label"] == "planejador / orquestrador"
    s = json.loads(_mcp(client, ADMIN, "suggest_agent_models", {"slug": slug})["content"][0]["text"])
    assert s["agents"][0]["supported"] is False


def test_no_suggestion_when_only_the_price_is_known(client, lab, uniq):
    ids = lab["ids"]
    for k in ids:  # tira as notas de raciocínio dos modelos do laboratório: só sobram custo e velocidade
        client.patch(f"/api/catalog/model-profiles/{ids[k]}", headers=ADMIN, json={"scores": {"reasoning": None}})
    rec = _rec(client, lab, "planner", caps="")
    assert rec["candidates"] and all(c["confidence"] == "baixa" for c in rec["candidates"])
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("ag"), "objective": "o", "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"llm": {"connection": lab["conn"], "model": ids["coder"]}})
    with SessionLocal() as db:  # só os modelos do laboratório (os de outros testes ficam de fora)
        ctx = ms.context(db)
        ctx["rows"] = [(m, c) for m, c in ctx["rows"] if c.name == lab["conn"]]
        r = ms.for_agent(db, db.query(Agent).filter(Agent.slug == slug).one(), role="planner", ctx=ctx)
    assert r["candidates"] and r["suggestion"] is None  # todos sem nota de raciocínio: o preço sozinho não sugere troca


def test_the_agent_name_weighs_more_than_the_text():
    assert ms.infer_role({}, "analista-dados", "extrair números das planilhas") == "analyst"
    assert ms.infer_role({}, "Redator", "extrair o tom de voz da marca") == "writer"



def test_a_current_model_without_quality_score_is_not_called_the_best(client, lab, uniq):
    ids = lab["ids"]
    client.patch(f"/api/catalog/model-profiles/{ids['cheap']}", headers=ADMIN, json={"scores": {"reasoning": None, "coding": None}})
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("ag"), "objective": "o", "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"llm": {"connection": lab["conn"], "model": ids["cheap"]}})
    r = client.get(f"/api/agents/{slug}/model-recommendation?role=planner", headers=ADMIN).json()[0]
    assert r["current"]["confidence"] == "baixa" and r["suggestion"] is None and "Pesquise a ficha" in r["note"]
