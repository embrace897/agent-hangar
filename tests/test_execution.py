"""Perfil de execução: esforço de raciocínio pelo papel e pelo que o modelo suporta, a língua de cada provedor e de cada
harness, avisos do que não vale, e o runtime aplicando (com repetição sem os opcionais e ferramentas em paralelo)."""
import asyncio
import json
import time

import httpx
import pytest

from app import services as svc
from app.models import LlmModel
from app.services import execution

from .conftest import ADMIN
from .test_runtime import rt  # noqa: F401  (fixture do runtime carregado pelo caminho)


def _conn(db, uniq, provider, models: dict, protocol="openai", base="https://example.test/v1", kind_source="provider"):
    """Conexão com modelos de recursos conhecidos: {id: [caps]}. O primeiro é o padrão."""
    name = uniq(provider)
    first = next(iter(models), "m")
    c = svc.upsert_llm_connection(db, name, base, first, "k", "", "t", protocol, provider=provider)
    for mid, caps in models.items():
        db.add(LlmModel(connection_id=c.id, model_id=mid, name=mid, kind="text", kind_source=kind_source, caps=caps))
    db.commit()
    return name


def _chat_agent(db, uniq, conn, model, name="Revisor de código", **extra):
    a = svc.create_agent(db, uniq(name), "revisa código e aponta bugs", "parecer")
    svc.design_agent(db, a.slug, {"llm": {"connection": conn, "model": model}, **extra})
    return a


def _prof(client, slug, env="prod"):
    r = client.get(f"/api/agents/{slug}/execution?env={env}", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def test_auto_effort_follows_the_role_and_only_goes_to_reasoning_models(client, db, uniq):
    conn = _conn(db, uniq, "openrouter", {"x/thinker": ["tools", "reasoning"], "x/plain": ["tools"]})
    a = _chat_agent(db, uniq, conn, "x/thinker")
    r = _prof(client, a.slug)
    p = r["profile"]
    assert p["role"] == "coder" and p["effort"] == "high" and p["effort_param"] == "reasoning.effort"
    assert "papel" in p["effort_source"] and r["sends"]["api"] == {"reasoning": {"effort": "high"}}
    assert r["sends"]["lite_api"] == {"reasoning": {"effort": "low"}} and p["warnings"] == []
    # triagem pensa o mínimo (que vira "low" fora da OpenAI)
    t = _chat_agent(db, uniq, conn, "x/thinker", name="Triagem de chamados")
    assert _prof(client, t.slug)["profile"]["effort"] == "low"
    # modelo sem modo de raciocínio: nada é enviado, e o automático não vira aviso
    svc.design_agent(db, a.slug, {"llm": {"model": "x/plain"}})
    p = _prof(client, a.slug)["profile"]
    assert p["effort"] is None and "não tem modo de raciocínio" in p["effort_source"] and p["warnings"] == []


def test_explicit_settings_in_each_provider_dialect(client, db, uniq):
    lite = _conn(db, uniq, "litellm", {"claude-sonnet": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, lite, "claude-sonnet",
                    execution={"effort": "max", "max_output_tokens": 4096, "parallel_tools": False})
    r = _prof(client, a.slug)
    assert r["sends"]["api"] == {"reasoning_effort": "high", "max_tokens": 4096}
    assert r["sends"]["parallel_tools"] is False and r["sends"]["cache"] is True  # Claude atrás do gateway: cache explícito
    assert r["profile"]["prompt_cache"] == "explicit" and r["spec"]["effort"] == "max"
    oa = _conn(db, uniq, "openai", {"gpt-5-mini": ["tools", "reasoning"]})
    b = _chat_agent(db, uniq, oa, "gpt-5-mini", execution={"effort": "minimal", "max_output_tokens": 2048})
    r = _prof(client, b.slug)
    assert r["sends"]["api"] == {"reasoning_effort": "minimal", "max_completion_tokens": 2048}
    assert r["profile"]["prompt_cache"] == "auto" and r["sends"]["cache"] is False
    # pedido explícito em provedor sem esse controle: aviso, nada enviado
    ds = _conn(db, uniq, "deepseek", {"deepseek-chat": ["tools"]})
    c = _chat_agent(db, uniq, ds, "deepseek-chat", execution={"effort": "high"})
    p = _prof(client, c.slug)["profile"]
    assert p["effort"] is None and any("provedor deepseek" in w for w in p["warnings"])


def test_warns_when_the_model_has_no_tool_calling(client, db, uniq):
    conn = _conn(db, uniq, "litellm", {"no-tools": ["reasoning"]})
    a = _chat_agent(db, uniq, conn, "no-tools", tools=[{"type": "builtin", "name": "calculator"}])
    assert any("ferramenta" in w for w in _prof(client, a.slug)["profile"]["warnings"])
    # recursos deduzidos só do nome nunca falam de ferramentas: aí não é aviso
    guess = _conn(db, uniq, "custom", {"guess-r1": ["reasoning"]}, kind_source="name")
    b = _chat_agent(db, uniq, guess, "guess-r1", tools=[{"type": "builtin", "name": "calculator"}])
    assert not any("ferramenta" in w for w in _prof(client, b.slug)["profile"]["warnings"])


def test_without_model_list_auto_does_not_risk_but_explicit_goes_with_a_warning(client, db, uniq):
    conn = _conn(db, uniq, "custom", {})
    a = _chat_agent(db, uniq, conn, "")
    p = _prof(client, a.slug)["profile"]
    assert p["effort"] is None and "lista de modelos" in p["effort_source"]
    svc.design_agent(db, a.slug, {"execution": {"effort": "high"}})
    p = _prof(client, a.slug)["profile"]
    assert p["effort"] == "high" and any("não dá para confirmar" in w for w in p["warnings"])


def test_deploy_hands_the_resolved_profile_to_the_runtime(client, db, uniq, fake_docker, monkeypatch):
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, conn, "think", execution={"max_output_tokens": 1024})
    seen = {}
    real = fake_docker.run_agent

    def run_agent(slug, env, spec, llm_conf):
        seen.update(spec)
        return real(slug, env, spec, llm_conf)
    monkeypatch.setattr(svc.runtime.deploy, "run_agent", run_agent)
    svc.deploy_env(db, a.slug, "stage", "t")
    assert seen["execution_resolved"] == {"api": {"reasoning_effort": "high", "max_tokens": 1024},
                                          "lite_api": {"reasoning_effort": "low"}, "parallel_tools": True, "cache": False,
                                          "compact": True, "context_budget": 48_000, "light": None}


def test_harness_jobs_get_the_profile_in_their_cli_vocabulary(db, uniq, fake_docker):
    ant = _conn(db, uniq, "anthropic", {"claude-opus": ["tools", "reasoning", "vision"], "claude-haiku": ["tools"]},
                protocol="anthropic", base="https://api.anthropic.com")
    a = svc.create_agent(db, uniq("Dev de código"), "programa", "diff")
    svc.design_agent(db, a.slug, {"harness": {"id": "claude-code", "connection": ant, "model": "claude-opus"},
                                  "execution": {"effort": "max", "max_turns": 30, "max_output_tokens": 8000,
                                                "prompt_cache": False}})
    svc.run_harness_job(db, a.slug, "x", "stage", 30, "t", "api")
    env = fake_docker.jobs[-1]["env"]
    assert env["LLM_MODEL"] == "claude-opus"  # o modelo escolhido no harness, não só o padrão da conexão
    assert json.loads(env["LLM_EXEC_JSON"]) == {"effort": "max", "max_output_tokens": 8000, "max_turns": 30,
                                                "prompt_cache": False}
    # automático num modelo sem raciocínio: só o cache segue
    svc.design_agent(db, a.slug, {"harness": {"model": "claude-haiku"}, "execution": None})
    svc.run_harness_job(db, a.slug, "x", "stage", 30, "t", "api")
    assert json.loads(fake_docker.jobs[-1]["env"]["LLM_EXEC_JSON"]) == {"prompt_cache": True}


def test_each_harness_maps_effort_and_drops_what_it_cannot_do(db, uniq):
    oa = _conn(db, uniq, "openrouter", {"openai/gpt-5": ["tools", "reasoning"]})
    spec = {"execution": {"effort": "max", "max_turns": 12, "max_output_tokens": 3000}}
    codex = execution.resolve(db, spec, "codex", oa, "openai/gpt-5")
    assert codex["effort"] == "high" and codex["effort_param"] == "model_reasoning_effort"
    assert codex["max_turns"] is None and any("rodadas" in w for w in codex["warnings"])
    assert any("limite de saída" in w for w in codex["warnings"])  # o Codex ignora model_max_output_tokens
    assert execution.harness_options(codex) == {"effort": "high", "prompt_cache": True}
    hermes = execution.resolve(db, {"execution": {"effort": "minimal"}}, "hermes", oa, "openai/gpt-5")
    assert hermes["effort"] == "minimal" and hermes["effort_param"] == "--reasoning"
    ds = execution.resolve(db, {"execution": {"effort": "high"}}, "deepseek-harness", oa, "openai/gpt-5")
    assert ds["effort"] is None and any("harness deepseek-harness" in w for w in ds["warnings"])


def test_spec_rejects_unknown_effort(db, uniq):
    a = svc.create_agent(db, uniq("Ag"), "x", "y")
    with pytest.raises(svc.PlatformError, match="execution.effort"):
        svc.design_agent(db, a.slug, {"execution": {"effort": "turbo"}})


def test_mcp_reads_the_profile(client, db, uniq):
    from .test_edit_agent import _mcp
    conn = _conn(db, uniq, "litellm", {"think": ["tools", "reasoning"]})
    a = _chat_agent(db, uniq, conn, "think")
    err, out = _mcp(client, ADMIN, "get_execution_profile", {"slug": a.slug})
    assert not err, out
    assert out["profile"]["effort"] == "high" and out["sends"]["api"] == {"reasoning_effort": "high"}
    err, _ = _mcp(client, ADMIN, "design_agent", {"slug": a.slug, "execution": {"effort": "low"}})
    assert not err
    _, out = _mcp(client, ADMIN, "get_execution_profile", {"slug": a.slug})
    assert out["sends"]["api"] == {"reasoning_effort": "low"} and out["spec"] == {"effort": "low"}


# ---------------------------------------------------------------- runtime

def _llm(runtime, monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(runtime, "MODEL", "fake-model")
    monkeypatch.setattr(runtime, "_OPTIONAL_OFF", False)


def test_runtime_sends_the_profile_and_retries_without_it_when_refused(rt, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rt, "EXEC", {"api": {"reasoning_effort": "high", "max_tokens": 900}})
    monkeypatch.setattr(rt, "PROMPT_CACHE", True)
    seen, refuse = [], []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if refuse and "reasoning_effort" in body:
            return httpx.Response(400, json={"error": "Unrecognized request argument: reasoning_effort"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})
    _llm(rt, monkeypatch, handler)

    async def ask():
        rt.MODE.set("full")
        return await rt.chat([{"role": "user", "content": "oi"}])
    assert asyncio.run(ask())[0] == "ok"
    first = seen[0]
    assert first["reasoning_effort"] == "high" and first["max_tokens"] == 900
    assert first["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    # o provedor recusa: a mesma chamada sem os opcionais, e o container segue só com o essencial
    seen.clear()
    refuse.append(True)
    assert asyncio.run(ask())[0] == "ok"
    assert "reasoning_effort" in seen[0] and "reasoning_effort" not in seen[1]
    assert isinstance(seen[1]["messages"][0]["content"], str) and rt._OPTIONAL_OFF is True
    seen.clear()
    refuse.clear()
    asyncio.run(ask())
    assert len(seen) == 1 and "reasoning_effort" not in seen[0]


def test_runtime_runs_tools_of_different_servers_together_in_model_order(rt, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rt, "EXEC", {})
    monkeypatch.setattr(rt, "PROMPT_CACHE", False)
    monkeypatch.setattr(rt, "PARALLEL_TOOLS", True)
    calls = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": "{}"}}
             for i, n in enumerate(("a__slow", "b__slow", "a__fast"))]
    replies = [{"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}}], "usage": {}},
               {"choices": [{"message": {"content": "fim"}}], "usage": {}}]
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=replies[min(len(seen) - 1, 1)])
    _llm(rt, monkeypatch, handler)
    log = []

    def tool(name, delay):
        async def fn(args):
            log.append(("start", name))
            await asyncio.sleep(delay)
            log.append(("end", name))
            return name
        return rt.Tool(name, "", {"type": "object"}, fn, ref=f"mcp:{name.split('__')[0]}:{name}")

    async def tools():
        return [tool("a__slow", 0.3), tool("b__slow", 0.3), tool("a__fast", 0.01)]
    monkeypatch.setattr(rt, "get_tools", tools)

    async def run():
        rt.MODE.set("full")
        t0 = time.monotonic()
        out = await rt.chat([{"role": "user", "content": "vai"}])
        return out, time.monotonic() - t0
    (text, _, trace), took = asyncio.run(run())
    assert text == "fim" and took < 0.55  # os dois lentos de servidores diferentes rodaram juntos
    # no mesmo servidor, em ordem: a__fast só começa depois que a__slow terminou
    assert log.index(("end", "a__slow")) < log.index(("start", "a__fast"))
    # o que volta ao modelo segue a ordem das chamadas
    tool_msgs = [m for m in seen[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c0", "c1", "c2"] and [t["tool"] for t in trace] == ["a__slow", "b__slow", "a__fast"]
    # com parallel_tools=false, uma por vez (e o provedor é avisado)
    monkeypatch.setattr(rt, "PARALLEL_TOOLS", False)
    seen.clear()
    (_, _, _), took = asyncio.run(run())
    assert took >= 0.55 and seen[0]["parallel_tool_calls"] is False
