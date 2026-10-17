"""AG-UI, fase 3: agente remoto. O agente roda fora do hangar (LangGraph, Google ADK, Mastra…) e fala AG-UI; o hangar
governa o ciclo de vida (spec versionada, testes em stage pela ponte, promoção, medição) e o expõe em todos os
protocolos dele: OpenAI, A2A, ACP, MCP e o próprio AG-UI, evento por evento."""
import json

import httpx
import pytest

from app import config
from app import services as svc
from app.db import SessionLocal
from app.models import UsageEvent
from app.services import remote_agui

from .conftest import ADMIN

URL = "https://agente.example.com/agui"


class FakeRemote:
    """Um agente AG-UI de mentira: guarda o que recebe e responde com eventos (texto, ferramenta, ou interrupt)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.mode = "text"

    def body(self, request: httpx.Request) -> bytes:
        inp = json.loads(request.content)
        self.calls.append({"headers": dict(request.headers), "input": inp})
        last = next((m["content"] for m in reversed(inp["messages"]) if m["role"] == "user"), "")
        t, r = inp["threadId"], inp["runId"]
        evs = [{"type": "RUN_STARTED", "threadId": t, "runId": r}]
        if self.mode == "interrupt":
            evs.append({"type": "RUN_FINISHED", "threadId": t, "runId": r, "outcome": {"type": "interrupt", "interrupts": [
                {"id": "i1", "reason": "approval", "message": "aprovar o reembolso de R$ 900?"}]}})
        else:
            evs += [{"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "buscar_pedido"},
                    {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": "{\"id\": 1}"},
                    {"type": "TOOL_CALL_END", "toolCallId": "c1"},
                    {"type": "TOOL_CALL_RESULT", "messageId": "r1", "toolCallId": "c1", "content": "pedido 1: pago"},
                    {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"},
                    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": "remoto: "},
                    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": last},
                    {"type": "TEXT_MESSAGE_END", "messageId": "m"},
                    {"type": "RUN_FINISHED", "threadId": t, "runId": r}]
        return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in evs).encode()

    def handler(self, request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self.body(request))


@pytest.fixture()
def remote(monkeypatch):
    fake = FakeRemote()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(remote_agui, "HTTP", lambda timeout: httpx.Client(transport=transport))
    monkeypatch.setattr(remote_agui, "AHTTP", lambda timeout: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", True)  # example.com não resolve dentro do teste
    return fake


def _agent(client, uniq, **remote_over) -> str:
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("Remoto"), "objective": "atende pedidos",
                                                           "final_output": "resposta"}).json()["slug"]
    secret = uniq("chave-remota")
    assert client.put("/api/secrets", headers=ADMIN, json={"name": secret, "value": "tok-123"}).status_code == 200
    r = client.patch(f"/api/agents/{slug}", headers=ADMIN, json={
        "remote": {"url": URL, "headers": {"Authorization": f"secret://{secret}", "X-Tenant": "acme"}, **remote_over},
        "tests": [{"name": "eco", "input": "pedido 7", "expect_contains": "pedido 7"}]})
    assert r.status_code == 200, r.text
    return slug


def _events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


# ------------------------------------------------------------------ spec
@pytest.mark.parametrize("bad,msg", [
    ({"remote": {"url": "ftp://x"}}, "http"),
    ({"remote": {"url": URL, "headers": {"Authorization": "Bearer abc"}}}, "cofre"),
    ({"remote": {"url": URL}, "skills": ["x"]}, "fora do hangar"),
    ({"remote": {"url": URL}, "llm": {"connection": "c"}}, "fora do hangar"),
])
def test_spec_rules(bad, msg):
    from app.spec import SpecError, validate
    with pytest.raises(SpecError, match=msg):
        validate(bad)
    assert validate({"remote": {"url": URL, "headers": {"Authorization": "secret://k", "X-Tenant": "acme"}}})["remote"]["url"] == URL


# ------------------------------------------------------------------ ciclo de vida e protocolos
def test_remote_agent_is_tested_in_stage_and_bridged_to_every_protocol(client, uniq, remote):
    slug = _agent(client, uniq)
    t = client.post(f"/api/agents/{slug}/test", headers=ADMIN)
    assert t.status_code == 200, t.text
    run = t.json()
    assert run["status"] == "passed" and "(remoto)" in run["summary"] and "mock" not in run["summary"]
    assert [x["name"] for x in run["results"]] == ["smoke: rodada AG-UI no agente remoto", "caso: eco"]
    # o segredo sai do cofre só na hora da chamada; a spec guarda só a referência
    assert remote.calls[0]["headers"]["authorization"] == "tok-123" and remote.calls[0]["headers"]["x-tenant"] == "acme"
    a = client.get(f"/api/agents/{slug}", headers=ADMIN).json()
    assert "tok-123" not in json.dumps(a) and a["model"].startswith("remoto (AG-UI)")

    gw = f"/gw-stage/{slug}"
    # OpenAI-compatible: a conversa inteira vai ao agente
    r = client.post(f"{gw}/v1/chat/completions", headers=ADMIN, json={"messages": [
        {"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}, {"role": "user", "content": "pedido 9"}]})
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "remoto: pedido 9"
    assert [m["role"] for m in remote.calls[-1]["input"]["messages"]] == ["user", "assistant", "user"]
    assert r.json()["x_trace"][0]["tool"] == "remoto:buscar_pedido"
    s = client.post(f"{gw}/v1/chat/completions", headers=ADMIN, json={"stream": True, "messages": [{"role": "user", "content": "x"}]})
    assert s.headers["content-type"].startswith("text/event-stream") and "remoto: x" in s.text and s.text.rstrip().endswith("[DONE]")
    # A2A, ACP e MCP pela mesma ponte
    a2a = client.post(f"{gw}/a2a", headers=ADMIN, json={"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {
        "message": {"kind": "message", "role": "user", "messageId": "m", "parts": [{"kind": "text", "text": "via a2a"}]}}})
    assert a2a.json()["result"]["parts"][0]["text"] == "remoto: via a2a"
    acp = client.post(f"{gw}/acp/runs", headers=ADMIN, json={"agent_name": slug, "input": [
        {"role": "user", "parts": [{"content_type": "text/plain", "content": "via acp"}]}]})
    assert acp.json()["output"][0]["parts"][0]["content"] == "remoto: via acp"
    tool = client.post(f"{gw}/mcp", headers=ADMIN, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
    name = tool["result"]["tools"][0]["name"]
    call = client.post(f"{gw}/mcp", headers=ADMIN, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": name, "arguments": {"message": "via mcp"}}}).json()
    assert call["result"]["content"][0]["text"] == "remoto: via mcp" and call["result"]["isError"] is False
    # AG-UI: direto, evento por evento (o mesmo que o agente mandou)
    g = client.post(f"{gw}/agui", headers=ADMIN, json={"threadId": "t", "runId": "r", "messages": [
        {"id": "1", "role": "user", "content": "via agui"}], "tools": [], "context": [], "state": {}, "forwardedProps": {}})
    evs = _events(g.text)
    assert [e["type"] for e in evs][:2] == ["RUN_STARTED", "TOOL_CALL_START"] and evs[-1]["type"] == "RUN_FINISHED"
    assert remote.calls[-1]["input"]["threadId"] == "t"  # o RunAgentInput chega como o app mandou
    with SessionLocal() as db:
        protos = {u.protocol for u in db.query(UsageEvent).filter(UsageEvent.agent_id == svc.get_agent(db, slug).id)}
    assert {"openai", "a2a", "acp", "mcp", "agui"} <= protos


def test_interrupt_from_the_remote_agent_reaches_other_protocols_as_text(client, uniq, remote):
    slug = _agent(client, uniq)
    assert client.post(f"/api/agents/{slug}/deploy", headers=ADMIN, json={"env": "stage"}).status_code == 200
    remote.mode = "interrupt"
    r = client.post(f"/gw-stage/{slug}/v1/chat/completions", headers=ADMIN, json={"messages": [{"role": "user", "content": "x"}]})
    body = r.json()
    assert "aprovar o reembolso de R$ 900?" in body["choices"][0]["message"]["content"] and body["x_status"] == "interrupted"


def test_connection_probe_and_private_network_rule(client, uniq, remote, monkeypatch):
    slug = _agent(client, uniq)
    p = client.post(f"/api/agents/{slug}/remote/test", headers=ADMIN, json={"env": "stage"}).json()
    assert p["ok"] and [s["ok"] for s in p["steps"]] == [True, True, True] and "remoto:" in p["reply"]
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", False)
    client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"remote": {"url": "http://127.0.0.1:9/agui"}})
    p = client.post(f"/api/agents/{slug}/remote/test", headers=ADMIN, json={"env": "stage"}).json()
    assert not p["ok"] and "rede interna" in p["steps"][0]["detail"]


def test_missing_secret_fails_clearly(client, uniq, remote):
    slug = _agent(client, uniq)
    client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"remote": {"headers": {"Authorization": "secret://nao-existe"}}})
    p = client.post(f"/api/agents/{slug}/remote/test", headers=ADMIN, json={"env": "stage"}).json()
    assert not p["ok"] and "cofre" in p["steps"][1]["detail"]


def test_collect_reads_chunks_and_errors():
    out = remote_agui.collect([{"type": "RUN_STARTED"}, {"type": "TEXT_MESSAGE_CHUNK", "messageId": "m", "delta": "a"},
                               {"type": "TEXT_MESSAGE_CHUNK", "delta": "b"}, {"type": "RUN_FINISHED"}])
    assert out["reply"] == "ab" and out["status"] == "passed"
    bad = remote_agui.collect([{"type": "RUN_STARTED"}, {"type": "RUN_ERROR", "message": "quebrou"}])
    assert bad["status"] == "failed" and "quebrou" in bad["reply"]
    cut = remote_agui.collect([{"type": "RUN_STARTED"}, {"type": "TEXT_MESSAGE_CONTENT", "delta": "x"}])
    assert cut["status"] == "failed" and "RUN_FINISHED" in cut["error"]
