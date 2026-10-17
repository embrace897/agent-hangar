"""AG-UI, fase 2: a resposta em stream (token a token) e o raciocínio, o estado compartilhado com o app (JSON Patch)
e a delegação a especialistas como sub-agentes. O provedor de mentira responde em SSE, como os de verdade."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from .test_agui import _run, _types
from .test_runtime import _fake_llm, rt  # noqa: F401  (fixture)


def _fake_stream(rt, monkeypatch, replies):  # noqa: F811
    """Provedor em stream (SSE), no formato OpenAI: cada resposta é uma lista de deltas; a última linha traz o uso."""
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        chunks = replies[min(len(seen) - 1, len(replies) - 1)]
        lines = [f"data: {json.dumps({'choices': [{'index': 0, 'delta': d, 'finish_reason': None}]})}\n\n" for d in chunks]
        usage = {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}
        lines.append(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n")
        lines.append(": comentário do provedor\n\ndata: [DONE]\n\n")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(lines).encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(rt.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(rt, "MODEL", "fake-model")
    rt.MODE.set("full")
    return seen


def _call(name, args, cid="c1"):
    return {"tool_calls": [{"index": 0, "id": cid, "function": {"name": name, "arguments": json.dumps(args)}}]}


def _text(evs):
    return "".join(e["delta"] for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")


def test_answer_streams_token_by_token_with_reasoning(rt, monkeypatch):  # noqa: F811
    seen = _fake_stream(rt, monkeypatch, [[{"reasoning": "pensando "}, {"reasoning": "no pedido"},
                                           {"content": "Olá"}, {"content": ", tudo"}, {"content": " certo."}]])
    evs = _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "oi"}])
    assert _types(evs) == ["RUN_STARTED", "REASONING_START", "REASONING_MESSAGE_START", "REASONING_MESSAGE_CONTENT",
                           "REASONING_MESSAGE_CONTENT", "REASONING_MESSAGE_END", "REASONING_END", "TEXT_MESSAGE_START",
                           "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",
                           "CUSTOM", "RUN_FINISHED"]
    assert _text(evs) == "Olá, tudo certo."
    assert evs[2]["role"] == "reasoning" and evs[3]["delta"] == "pensando "
    assert evs[-2]["value"]["total_tokens"] == 12  # o uso do último pedaço do stream chega à medição
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}


def test_streamed_tool_calls_are_assembled(rt, monkeypatch):  # noqa: F811
    """As tool_calls chegam em pedaços (argumentos fatiados): o runtime junta, roda, e o texto final segue em stream
    sem sair de novo no fim."""
    _fake_stream(rt, monkeypatch, [
        [{"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "memory__recall", "arguments": "{\"qu"}}]},
         {"tool_calls": [{"index": 0, "function": {"arguments": "ery\": \"x\"}"}}]}],
        [{"content": "pron"}, {"content": "to"}]])

    async def recall(args):
        assert args == {"query": "x"}
        return "fato"

    async def tools():
        return [rt.Tool("memory__recall", "", {"type": "object"}, recall)]
    monkeypatch.setattr(rt, "get_tools", tools)
    evs = _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "lembre"}])
    kinds = _types(evs)
    assert kinds[1:5] == ["TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"]
    assert json.loads(evs[2]["delta"]) == {"query": "x"} and evs[4]["content"] == "fato"
    assert kinds.count("TEXT_MESSAGE_START") == 1 and _text(evs) == "pronto"


def test_provider_that_ignores_stream_still_works(rt, monkeypatch):  # noqa: F811
    _fake_llm(rt, monkeypatch, [{"choices": [{"message": {"role": "assistant", "content": "inteiro"}}],
                                 "usage": {"total_tokens": 4}}])
    rt.MODE.set("full")
    evs = _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "oi"}])
    assert [e["delta"] for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT"] == ["inteiro"]


def test_provider_that_refuses_stream_options_gets_a_second_try(rt, monkeypatch):  # noqa: F811
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": "stream_options não suportado"})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b'data: {"choices": [{"index": 0, "delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n')
    real = httpx.AsyncClient
    monkeypatch.setattr(rt.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(rt, "MODEL", "fake-model")
    rt.MODE.set("full")
    evs = _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "oi"}])
    assert _text(evs) == "ok" and "stream_options" not in bodies[-1] and bodies[-1]["stream"] is True


def test_shared_state_the_agent_changes_with_json_patch(rt, monkeypatch):  # noqa: F811
    patch = [{"op": "replace", "path": "/pedido/status", "value": "pago"}, {"op": "add", "path": "/itens/-", "value": "frete"}]
    seen = _fake_stream(rt, monkeypatch, [[_call("app_state_update", {"patch": patch}, "s1")],
                                          [{"content": "Marquei como pago."}]])
    state = {"pedido": {"id": "4471", "status": "aberto"}, "itens": ["camiseta"]}
    evs = _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "marque como pago"}], state=state)
    kinds = _types(evs)
    assert next(e for e in evs if e["type"] == "STATE_DELTA")["delta"] == patch
    assert next(e for e in evs if e["type"] == "STATE_SNAPSHOT")["snapshot"] == {
        "pedido": {"id": "4471", "status": "pago"}, "itens": ["camiseta", "frete"]}
    assert kinds.index("STATE_DELTA") < kinds.index("TEXT_MESSAGE_START") < kinds.index("STATE_SNAPSHOT")
    assert "app_state_update" in {t["function"]["name"] for t in seen[0]["tools"]}
    assert '"status": "aberto"' in seen[0]["messages"][0]["content"]  # o agente vê a tela


def test_invalid_patch_changes_nothing(rt, monkeypatch):  # noqa: F811
    seen = _fake_stream(rt, monkeypatch, [
        [_call("app_state_update", {"patch": [{"op": "replace", "path": "/nao/existe", "value": 1}]}, "s1")],
        [{"content": "não deu"}]])
    evs = _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "mude"}], state={"a": 1})
    assert "STATE_DELTA" not in _types(evs) and "STATE_SNAPSHOT" not in _types(evs)
    assert next(e for e in evs if e["type"] == "TOOL_CALL_RESULT")["content"].startswith("erro: patch inválido")
    assert seen[1]["messages"][-1]["content"].startswith("erro: patch inválido")  # o modelo vê o erro


def test_no_state_no_state_tool(rt, monkeypatch):  # noqa: F811
    seen = _fake_stream(rt, monkeypatch, [[{"content": "ok"}]])
    _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "oi"}])
    assert "app_state_update" not in {t["function"]["name"] for t in seen[0].get("tools") or []}


def test_apply_patch(rt):  # noqa: F811
    doc = {"a": {"b": [1, 2]}, "x~y": 1, "p/q": 2}
    out = rt.apply_patch(doc, [{"op": "add", "path": "/a/b/0", "value": 0}, {"op": "remove", "path": "/x~0y"},
                               {"op": "replace", "path": "/p~1q", "value": 3}, {"op": "add", "path": "/novo", "value": {}}])
    assert out == {"a": {"b": [0, 1, 2]}, "p/q": 3, "novo": {}} and doc["a"]["b"] == [1, 2]  # o original fica igual
    for bad in ([], [{"op": "move", "path": "/a", "from": "/x"}], [{"op": "remove", "path": ""}],
                [{"op": "replace", "path": "/zz", "value": 1}], [{"op": "add", "path": "/a/b/9", "value": 1}],
                [{"op": "add", "path": "sem-barra", "value": 1}]):
        with pytest.raises(ValueError):
            rt.apply_patch(doc, bad)


def test_delegating_to_a_specialist_is_a_subagent(rt, monkeypatch):  # noqa: F811
    _fake_stream(rt, monkeypatch, [[_call("ask_juridico", {"message": "cláusula 3"}, "d1")],
                                   [{"content": "O jurídico aprovou."}]])

    async def ask(args):
        return "cláusula ok"

    async def tools():
        return [rt.Tool("ask_juridico", "Especialista jurídico", {"type": "object"}, ask, ref="agent:juridico")]
    monkeypatch.setattr(rt, "get_tools", tools)
    evs = _run(TestClient(rt.app), [{"id": "1", "role": "user", "content": "revise"}])
    assert next(e for e in evs if e["type"] == "SUBAGENT_STARTED") == {
        "type": "SUBAGENT_STARTED", "subagentRunId": "sub-d1", "name": "juridico", "parentToolCallId": "d1",
        "description": "Especialista jurídico"}
    assert next(e for e in evs if e["type"] == "SUBAGENT_FINISHED") == {
        "type": "SUBAGENT_FINISHED", "subagentRunId": "sub-d1", "result": "cláusula ok"}
    kinds = _types(evs)
    assert kinds.index("TOOL_CALL_START") < kinds.index("SUBAGENT_STARTED") < kinds.index("SUBAGENT_FINISHED")


def test_other_protocols_do_not_stream(rt, monkeypatch):  # noqa: F811
    """Fora do AG-UI, a chamada ao provedor continua sem stream (OpenAI-compatible, A2A, MCP, tarefas)."""
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {}})
    real = httpx.AsyncClient
    monkeypatch.setattr(rt.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(rt, "MODEL", "fake-model")
    rt.MODE.set("full")
    r = TestClient(rt.app).post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "oi"}]})
    assert r.json()["choices"][0]["message"]["content"] == "ok" and "stream" not in seen[0]
