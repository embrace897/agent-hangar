"""AG-UI: o agente dentro do app de alguém. No runtime, uma rodada vira eventos (texto, ferramentas do agente com
resultado, ferramentas do app que voltam para ele executar). Na central, um Digital employee recebe a conversa como
tarefa: o que precisa de aprovação termina a rodada com um interrupt, a decisão acontece no hangar e o app retoma."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import services as svc
from app.db import SessionLocal
from app.models import EmployeeTask
from app.services import agui

from .test_employees import FakeRuntime, env, hire, make_active, open_request, tasksmod, work  # noqa: F401  (fixtures)
from .test_runtime import _fake_llm, rt  # noqa: F401  (fixture)


@pytest.fixture()
def emp_rt(monkeypatch):
    """O runtime de mentira dos Digital employees (o mesmo dos testes de tarefas)."""
    fake = FakeRuntime()
    monkeypatch.setattr(tasksmod, "HTTP", lambda: fake)
    return fake

PING = {"name": "hangar_ping", "description": "teste",
        "parameters": {"type": "object", "properties": {"nonce": {"type": "string"}}}}


try:  # com o SDK oficial instalado (pip install ag-ui-protocol, como no CI), cada evento e cada corpo é validado
    from ag_ui.core import Event as _SdkEvent
    from ag_ui.core import RunAgentInput as _SdkInput
    from pydantic import TypeAdapter
    _SDK = (TypeAdapter(_SdkEvent), _SdkInput)
except ImportError:
    _SDK = None


def _events(text: str) -> list[dict]:
    evs = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
    if _SDK is not None:
        for e in evs:
            _SDK[0].validate_python(e)
    return evs


def _types(evs: list[dict]) -> list[str]:
    return [e["type"] for e in evs]


@pytest.fixture()
def api(rt):  # noqa: F811
    rt.MODE.set("full")
    yield TestClient(rt.app)


def _run(api, messages, tools=None, **extra):
    body = {"threadId": "t-1", "runId": "r-1", "messages": messages, "tools": tools or [], "context": [], "state": {},
            "forwardedProps": {}, **extra}
    if _SDK is not None:
        _SDK[1].model_validate(body)
    r = api.post("/agui", json=body, headers={"accept": "text/event-stream"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), r.text
    return _events(r.text)


# ------------------------------------------------------------------ runtime
def test_text_run(api):
    evs = _run(api, [{"id": "m1", "role": "user", "content": "olá"}])
    assert _types(evs) == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM",
                           "RUN_FINISHED"]
    assert evs[0] == {"type": "RUN_STARTED", "threadId": "t-1", "runId": "r-1"}
    assert "olá" in evs[2]["delta"] and evs[1]["messageId"] == evs[2]["messageId"] == evs[3]["messageId"]
    assert evs[4]["name"] == "hangar.usage" and evs[4]["value"]["total_tokens"] > 0
    assert evs[-1] == {"type": "RUN_FINISHED", "threadId": "t-1", "runId": "r-1"}


def test_app_tool_round_trip(api):
    """A ferramenta do app volta para ele executar; o resultado vem numa rodada nova e o agente usa."""
    user = {"id": "m1", "role": "user", "content": "chame hangar_ping nonce=abc"}
    evs = _run(api, [user], [PING])
    assert _types(evs) == ["RUN_STARTED", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "CUSTOM", "RUN_FINISHED"]
    call_id = evs[1]["toolCallId"]
    assert evs[1]["toolCallName"] == "hangar_ping" and json.loads(evs[2]["delta"]) == {"nonce": "abc"}
    evs2 = _run(api, [user, {"id": "m2", "role": "assistant", "toolCalls": [
        {"id": call_id, "type": "function", "function": {"name": "hangar_ping", "arguments": evs[2]["delta"]}}]},
        {"id": "m3", "role": "tool", "toolCallId": call_id, "content": "pong abc"}], [PING])
    text = next(e["delta"] for e in evs2 if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "pong abc" in text and evs2[-1]["type"] == "RUN_FINISHED"


def test_server_tools_show_up_with_their_result(rt, monkeypatch):  # noqa: F811
    call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "memory__recall", "arguments": "{\"query\": \"x\"}"}}]}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}}
    final = {"choices": [{"message": {"role": "assistant", "content": "pronto"}}], "usage": {}}
    _fake_llm(rt, monkeypatch, [call, final])

    async def recall(args):
        return "fato da memória"

    async def tools():
        return [rt.Tool("memory__recall", "", {"type": "object"}, recall)]
    monkeypatch.setattr(rt, "get_tools", tools)
    rt.MODE.set("full")
    evs = _run(TestClient(rt.app), [{"id": "m1", "role": "user", "content": "lembre"}])
    assert _types(evs)[:5] == ["RUN_STARTED", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"]
    assert evs[1]["toolCallName"] == "memory__recall" and evs[4]["toolCallId"] == "c1"
    assert evs[4]["content"] == "fato da memória" and evs[4]["role"] == "tool"
    assert next(e["delta"] for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT") == "pronto"


def test_messages_tools_and_context_are_translated(rt):  # noqa: F811
    body = {"context": [{"description": "tela", "value": "pedido #4471"}], "messages": [
        {"id": "s", "role": "developer", "content": "seja breve"},
        {"id": "u", "role": "user", "content": [{"type": "text", "text": "veja"},
                                                {"type": "binary", "mimeType": "image/png", "data": "QUJD"}]},
        {"id": "a", "role": "assistant", "content": "", "toolCalls": [
            {"id": "c1", "type": "function", "function": {"name": "abrir", "arguments": "{}"}}]},
        {"id": "t", "role": "tool", "toolCallId": "c1", "content": "", "error": "sem permissão"}],
        "tools": [PING]}
    msgs = rt.agui_messages(body)
    assert msgs[0] == {"role": "system", "content": "Contexto do app:\n- tela: pedido #4471"}
    assert msgs[1] == {"role": "system", "content": "seja breve"}
    assert msgs[2]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert msgs[3]["tool_calls"][0]["id"] == "c1" and msgs[4] == {"role": "tool", "tool_call_id": "c1",
                                                                   "content": "erro: sem permissão"}
    assert rt.agui_tools(body)[0]["function"]["name"] == "hangar_ping"


def test_invalid_body_is_400(api):
    assert api.post("/agui", content=b"[1, 2]", headers={"content-type": "application/json"}).status_code == 400


# ------------------------------------------------------------------ agente com harness
def test_harness_run_becomes_a_job():
    async def invoke(text):
        assert text == "corrija o frete"
        return {"reply": "feito", "job_id": 7, "status": "passed", "diff": "+1 -0", "usage": {"total_tokens": 3}}

    async def collect():
        body = {"threadId": "t", "runId": "r", "messages": [{"id": "1", "role": "user", "content": "corrija o frete"}]}
        return _events("".join([x async for x in agui.harness_stream(invoke, body)]))
    evs = asyncio.run(collect())
    assert _types(evs) == ["RUN_STARTED", "STEP_STARTED", "STEP_FINISHED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
                           "TEXT_MESSAGE_END", "CUSTOM", "CUSTOM", "RUN_FINISHED"]
    assert evs[6]["value"] == {"jobId": 7, "status": "passed", "diff": "+1 -0"}


# ------------------------------------------------------------------ Digital employee: tarefa, alçada e interrupt
def _gw(client, slug, headers, body):
    r = client.post(f"/gw/{slug}/agui", headers=headers, json={"threadId": "th", "runId": "r", "tools": [], "context": [],
                                                               "state": {}, "forwardedProps": {}, **body})
    assert r.status_code == 200, r.text
    return _events(r.text)


def test_employee_conversation_becomes_a_task_and_approval_is_an_interrupt(client, env, emp_rt, monkeypatch):  # noqa: F811
    slug = hire(client, env)
    make_active(client, env, slug, emp_rt)
    monkeypatch.setattr(svc, "active_deployment", lambda a, e: True)
    monkeypatch.setattr(agui, "WAIT_S", 0.2)
    monkeypatch.setattr(agui, "POLL_S", 0.05)
    req = env["h"]["req"]

    # 1. a conversa vira tarefa; ainda na fila, a rodada devolve "ainda trabalhando"
    evs = _gw(client, slug, req, {"messages": [
        {"id": "1", "role": "user", "content": "Mande um e-mail para a Ana"},
        {"id": "2", "role": "assistant", "content": "Qual o texto?"},
        {"id": "3", "role": "user", "content": 'use send_email {"to": "ana@cliente.com", "body": "Olá"}'}]})
    task_id = evs[1]["value"]["taskId"]
    first = evs[-1]["outcome"]["interrupts"][0]
    assert evs[-1]["type"] == "RUN_FINISHED" and first == {
        "id": first["id"], "reason": "hangar.still_running",
        "message": "A tarefa continua no Agent Hangar. Retome para acompanhar.",
        "metadata": {"taskId": task_id, "status": "new"}}
    assert first["id"].startswith(f"hangar-task-{task_id}.")
    with SessionLocal() as db:
        t = db.get(EmployeeTask, task_id)
        assert t.source == "agui" and t.title.startswith("use send_email") and "Qual o texto?" in t.body

    # 2. o funcionário trabalha; a ação precisa de aprovação: interrupt, sem os argumentos da ação
    work()
    evs = _gw(client, slug, req, {"messages": [], "resume": [{"interruptId": first["id"], "status": "resolved"}]})
    it = evs[-1]["outcome"]["interrupts"][0]
    r = open_request(client, env, task_id)
    board = [e for e in evs if e["type"] == "STATE_DELTA"][-1]["delta"][0]  # a tarefa no estado do app, numa chave própria
    assert board["op"] == "add" and board["path"] == "/hangarTask" and board["value"]["status"] == "waiting_human"
    assert it["id"].startswith(f"hangar-decision-{r['id']}.") and it["reason"] == "hangar.approval"
    assert it["metadata"]["tool"] == "send_email" and "ana@cliente.com" not in json.dumps(it)
    assert any(e["type"] == "CUSTOM" and e["name"] == "hangar.task_event" and e["value"]["kind"] == "human_request" for e in evs)

    # 3. só quem pediu acompanha; e o app não decide: retomar sem decisão devolve o mesmo pedido
    out = client.post(f"/gw/{slug}/agui", headers=env["h"]["out"], json={"messages": [], "resume": [
        {"interruptId": it["id"], "status": "resolved", "payload": {"approved": True}}]})
    assert out.status_code in (400, 403)
    again = _gw(client, slug, req, {"messages": [], "resume": [
        {"interruptId": it["id"], "status": "resolved", "payload": {"approved": True}}]})
    assert again[-1]["outcome"]["interrupts"][0]["id"] == it["id"]

    # 4. a decisão é no hangar, por quem a alçada indica; retomando, a resposta chega
    assert client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 200
    work()
    evs = _gw(client, slug, req, {"messages": [], "resume": [{"interruptId": it["id"], "status": "resolved"}]})
    text = next(e["delta"] for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert text.startswith("feito: send_email") and evs[-1]["type"] == "RUN_FINISHED"
    # os eventos da tarefa dizem o que aconteceu, sem argumentos, resultados ou nomes de quem decidiu
    task_events = [e["value"] for e in evs if e["type"] == "CUSTOM" and e["name"] == "hangar.task_event"]
    assert any(v["kind"] == "tool" and v["payload"].get("tool") == "send_email" for v in task_events)
    assert all(not ({"args", "result", "by", "approved_by"} & set(v["payload"])) for v in task_events)
    assert "ana@cliente.com" not in json.dumps(task_events)
    assert evs[-1]["result"] == {"taskId": task_id}


def test_employee_resume_cancelled_cancels_the_task(client, env, emp_rt, monkeypatch):  # noqa: F811
    slug = hire(client, env)
    make_active(client, env, slug, emp_rt)
    monkeypatch.setattr(svc, "active_deployment", lambda a, e: True)
    monkeypatch.setattr(agui, "WAIT_S", 0.1)
    monkeypatch.setattr(agui, "POLL_S", 0.05)
    evs = _gw(client, slug, env["h"]["req"], {"messages": [{"id": "1", "role": "user", "content": "relatório"}]})
    task_id = evs[1]["value"]["taskId"]
    evs = _gw(client, slug, env["h"]["req"], {"messages": [], "resume": [
        {"interruptId": f"hangar-task-{task_id}", "status": "cancelled"}]})
    assert evs[-1]["type"] == "RUN_ERROR" and evs[-1]["code"] == "task_cancelled"


def test_agui_snippets():
    s = svc.connect.snippet("copilotkit", "agui", "dev-bot", "Dev Bot", key="ah_x")
    assert "/gw/dev-bot/agui" in s["content"] and "ah_x" in s["content"] and "HttpAgent" in s["content"]
    s = svc.connect.snippet("agui", "agui", "dev-bot", "Dev Bot", key="ah_x")
    assert s["content"].startswith("curl -N") and '"messages"' in s["content"]


def test_gateway_reads_usage_from_openai_and_agui_streams():
    """A medição do gateway acha os tokens nos dois formatos (no AG-UI, o evento CUSTOM hangar.usage)."""
    from app.routers.gateway import USAGE_MARK, usage_of
    agui_line = b'data: {"type": "CUSTOM", "name": "hangar.usage", "value": {"prompt_tokens": 12, "total_tokens": 18}}'
    openai_line = b'data: {"id": "x", "choices": [], "usage": {"total_tokens": 7}}'
    assert USAGE_MARK in agui_line and usage_of(agui_line) == {"prompt_tokens": 12, "total_tokens": 18}
    assert usage_of(openai_line) == {"total_tokens": 7}
    assert usage_of(b'data: {"type": "TEXT_MESSAGE_CONTENT", "delta": "usage"}') is None
    assert usage_of(b": keepalive") is None and usage_of(b"data: [DONE]") is None
