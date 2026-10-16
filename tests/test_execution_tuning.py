"""Medição por perfil de execução: cada chamada marcada com versão, modelo e esforço; sugestão pelo uso real;
comparação de perfis em stage (chat e harness) com o override que só a central assina; caixa de entrada e MCP."""
import json
from datetime import timedelta

import pytest

from app import services as svc
from app.models import ExecutionTrial, UsageEvent, now
from app.services import execution_tuning as tu

from .conftest import ADMIN
from .test_edit_agent import _mcp
from .test_execution import _chat_agent, _conn
from .test_runtime import rt  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    tu._CACHE.clear()
    svc.execution._TAGS.clear()
    monkeypatch.setattr(svc.runtime.deploy, "ensure_up", lambda slug, env, timeout=90: None)


def _usage(db, a, model, effort, n, cost, lat, ok=True, version=None):
    for _ in range(n):
        db.add(UsageEvent(agent_id=a.id, env="prod", channel="api", protocol="openai", tokens_in=100, tokens_out=50,
                          cost_usd=cost, latency_ms=lat, ok=ok, version=version or a.current_version, model=model,
                          effort=effort, created_at=now() - timedelta(hours=1)))
    db.commit()


def test_each_call_is_tagged_with_the_profile_that_served_it(db, uniq, fake_docker):
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, conn, "think")
    svc.deploy_env(db, a.slug, "stage", "t")
    svc.record_usage(db, a.id, "stage", "api", "openai", 900, True, {"usage": {"prompt_tokens": 3, "completion_tokens": 2}})
    ev = db.query(UsageEvent).filter_by(agent_id=a.id).order_by(UsageEvent.id.desc()).first()
    assert (ev.version, ev.model, ev.effort) == (a.current_version, "think", "high")


def test_usage_suggests_a_lower_effort_that_hits_the_same_for_less(client, db, uniq):
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, conn, "think")  # papel código: alto
    _usage(db, a, "think", "high", 25, 0.010, 4000)
    _usage(db, a, "think", "medium", 25, 0.005, 2000, version=1)
    r = client.get(f"/api/agents/{a.slug}/execution/tuning", headers=ADMIN).json()
    s = r["suggestion"]
    assert s["source"] == "usage" and s["effort"] == "medium" and s["from"] == "high" and s["direction"] == "down"
    assert s["cost_change_pct"] == -50 and s["latency_change_pct"] == -50 and s["patch"] == {"execution": {"effort": "medium"}}
    high = next(x for x in r["stats"] if x["effort"] == "high")
    assert high["calls"] == 25 and high["ok_rate"] == 1.0 and high["latency_ms_p50"] == 4000
    # na caixa de entrada dos admins
    from .test_access import _session
    email = f"adm-{uniq('x')}@acme.com"
    client.post("/api/users", headers=ADMIN, json={"email": email, "org_role": "admin"})
    items = client.get("/api/inbox", headers=_session(email)).json()["items"]
    assert any(i["key"] == f"execution_suggestion:{a.slug}:medium" for i in items)


def test_usage_does_not_suggest_with_few_calls_or_lower_accuracy(db, uniq):
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, conn, "think")
    _usage(db, a, "think", "high", 25, 0.010, 4000)
    _usage(db, a, "think", "medium", 5, 0.005, 2000)  # poucas chamadas
    assert tu.suggest(db, a)["suggestion"] is None
    _usage(db, a, "think", "medium", 20, 0.005, 2000, ok=False)  # mais barato, mas erra
    assert tu.suggest(db, a)["suggestion"] is None


def _fake_chat(monkeypatch, answers: dict):
    """answers: esforço enviado -> (texto, custo). Registra os perfis recebidos."""
    seen = []

    def ask(slug, text, opts):
        seen.append(opts)
        effort = (opts.get("api") or {}).get("reasoning_effort")
        out, cost = answers[effort]
        return out, {"completion_tokens": 10, "cost_usd": cost}
    monkeypatch.setattr(tu, "_ask_chat", ask)
    return seen


def test_trial_compares_neighbor_levels_and_suggests_the_cheaper_one(client, db, uniq, fake_docker, monkeypatch):
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, conn, "think", tests=[{"name": "soma", "input": "2+2?", "expect_contains": "4"},
                                                    {"name": "capital", "input": "capital da França?", "expect_contains": "Paris"}])
    seen = _fake_chat(monkeypatch, {"medium": ("4 Paris", 0.001), "high": ("4 Paris", 0.003)})
    t = tu.start_trial(db, a.slug, "t", wait=True)
    assert t.status == "done" and t.levels == ["medium", "high"] and t.baseline == "high"  # máx. envia o mesmo que alto
    assert {s["api"]["reasoning_effort"] for s in seen} == {"medium", "high"}
    med = next(r for r in t.results if r["level"] == "medium")
    assert med["passed"] == 2 and med["total"] == 2 and med["avg_cost_usd"] == 0.001 and t.cost_usd == 0.008
    s = tu.suggest(db, a)["suggestion"]
    assert s["source"] == "trial" and s["effort"] == "medium" and s["cost_change_pct"] == -67 and "2/2" in s["reason"]
    assert "medium" in t.summary or "médio" in t.summary
    # aplicada a sugestão (nova versão), a comparação vira "de uma versão anterior" e não sugere de novo
    client.patch(f"/api/agents/{a.slug}", headers=ADMIN, json=s["patch"])
    db.expire_all()
    r = tu.suggest(db, svc.get_agent(db, a.slug))
    assert r["trial_stale"] is True and r["suggestion"] is None and r["effort"] == "medium"
    # excluir o agente leva as comparações junto (a chave estrangeira barrava a exclusão no Postgres)
    assert client.delete(f"/api/agents/{a.slug}", headers=ADMIN).status_code == 200
    assert db.query(ExecutionTrial).filter(ExecutionTrial.id == t.id).count() == 0


def test_trial_suggests_more_effort_when_it_hits_more(db, uniq, fake_docker, monkeypatch):
    conn = _conn(db, uniq, "openai", {"gpt-5": ["tools", "reasoning"]})
    a = svc.create_agent(db, uniq("Assistente geral"), "ajuda o time no dia a dia", "resposta")  # papel geral: médio
    svc.design_agent(db, a.slug, {"llm": {"connection": conn, "model": "gpt-5"}, "tests": [
        {"input": "a", "expect_contains": "ok"}, {"input": "b", "expect_contains": "ok"}]})
    _fake_chat(monkeypatch, {"low": ("não", 0.001), "medium": ("não", 0.002), "high": ("ok", 0.004)})
    t = tu.start_trial(db, a.slug, "t", wait=True)
    assert t.levels == ["low", "medium", "high"] and t.baseline == "medium"
    s = tu.from_trial(t)
    assert s["effort"] == "high" and s["direction"] == "up" and s["cost_change_pct"] == 100


def test_harness_trial_runs_jobs_with_each_level(db, uniq, fake_docker):
    conn = _conn(db, uniq, "anthropic", {"claude-opus": ["tools", "reasoning"]}, protocol="anthropic",
                 base="https://api.anthropic.com")
    a = svc.create_agent(db, uniq("Dev de código"), "programa", "diff")
    svc.design_agent(db, a.slug, {"harness": {"id": "claude-code", "connection": conn},
                                  "tests": [{"input": "faça", "expect_contains": "done"}]})
    t = tu.start_trial(db, a.slug, "t", wait=True)
    assert t.status == "done" and t.levels == ["medium", "high", "max"]
    sent = [json.loads(j["env"]["LLM_EXEC_JSON"])["effort"] for j in fake_docker.jobs[-3:]]
    assert sent == ["medium", "high", "max"]
    efforts = [e.effort for e in db.query(UsageEvent).filter_by(agent_id=a.id, channel="profile-trial").order_by(UsageEvent.id)]
    assert efforts == ["medium", "high", "max"]


def test_trial_refuses_what_it_cannot_compare(db, uniq):
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"], "plain": ["tools"]})
    a = _chat_agent(db, uniq, conn, "think")
    with pytest.raises(svc.PlatformError, match="casos de teste"):
        tu.start_trial(db, a.slug, "t")
    svc.design_agent(db, a.slug, {"llm": {"model": "plain"}, "tests": [{"input": "x"}]})
    with pytest.raises(svc.PlatformError, match="raciocínio"):
        tu.start_trial(db, a.slug, "t")
    m = svc.create_agent(db, uniq("Mock"), "x", "y")
    with pytest.raises(svc.PlatformError, match="mock"):
        tu.start_trial(db, m.slug, "t")
    svc.design_agent(db, a.slug, {"llm": {"model": "think"}})
    db.add(ExecutionTrial(agent_id=a.id, version=a.current_version, status="running"))
    db.commit()
    with pytest.raises(svc.PlatformError, match="já há"):
        tu.start_trial(db, a.slug, "t")


def test_api_and_mcp(client, db, uniq, fake_docker, monkeypatch):
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, conn, "think", tests=[{"input": "2+2?", "expect_contains": "4"}])
    _fake_chat(monkeypatch, {"medium": ("4", 0.001), "high": ("4", 0.001)})
    monkeypatch.setattr(tu._pool, "submit", lambda fn, *args: fn(*args))  # sem thread no teste
    r = client.post(f"/api/agents/{a.slug}/execution/trials", headers=ADMIN, json={})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/agents/{a.slug}/execution/trials/{r.json()['id']}", headers=ADMIN).json()
    assert got["status"] == "done" and len(got["results"]) == 2
    err, out = _mcp(client, ADMIN, "get_execution_tuning", {"slug": a.slug})
    assert not err and out["trial"]["id"] == got["id"]
    err, out = _mcp(client, ADMIN, "compare_execution_profiles", {"slug": a.slug, "levels": ["low", "high"]})
    assert not err and out["levels"] == ["low", "high"]


def test_runtime_override_only_with_the_central_token(rt, monkeypatch):  # noqa: F811
    monkeypatch.setenv("INTERNAL_ENV_TOKEN", "env-tok")
    monkeypatch.setattr(rt, "EXEC", {"api": {"reasoning_effort": "high"}})
    monkeypatch.setattr(rt, "_OPTIONAL_OFF", False)

    class Req:
        def __init__(self, h):
            self.headers = h
    opts = json.dumps({"api": {"reasoning_effort": "low"}, "cache": False})
    assert rt._exec_override(Req({"x-hangar-exec": opts, "x-hangar-internal": "outro"})) is None
    assert rt._exec_override(Req({"x-hangar-exec": opts})) is None
    ov = rt._exec_override(Req({"x-hangar-exec": opts, "x-hangar-internal": "env-tok"}))
    assert ov["api"] == {"reasoning_effort": "low"}
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert rt._with_options(base)["reasoning_effort"] == "high"
    tok = rt.EXEC_OVERRIDE.set(ov)
    try:
        assert rt._with_options(base)["reasoning_effort"] == "low"
    finally:
        rt.EXEC_OVERRIDE.reset(tok)
