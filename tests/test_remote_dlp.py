"""Agente remoto: o DLP da empresa na ponte (o agente vê [CPF_…], o app recebe o valor real) e a medição dos tokens e
do custo que o agente informa (evento CUSTOM hangar.usage)."""
import json

import httpx
import pytest

from app import config
from app import services as svc
from app.db import SessionLocal
from app.models import UsageEvent
from app.services import remote_agui
from app.services.remote_dlp import Masker, Stream

from .conftest import ADMIN
from .test_agui_remote import URL, FakeRemote

CPF = "529.982.247-25"
EMAIL = "ana@cliente.com.br"
RUN = {"threadId": "t", "runId": "r", "tools": [], "context": [], "forwardedProps": {}}


class MeteredRemote(FakeRemote):
    """Repete o pedido em pedaços de 3 letras (uma marca do DLP chega partida) e informa o uso em dois eventos."""

    def body(self, request: httpx.Request) -> bytes:
        inp = json.loads(request.content)
        self.calls.append({"headers": dict(request.headers), "input": inp})
        last = next((m["content"] for m in reversed(inp["messages"]) if m["role"] == "user"), "")
        text = f"remoto: {last}"
        evs = [{"type": "RUN_STARTED", "threadId": inp["threadId"], "runId": inp["runId"]},
               {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"}]
        evs += [{"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": text[i:i + 3]} for i in range(0, len(text), 3)]
        evs += [{"type": "TEXT_MESSAGE_END", "messageId": "m"},
                {"type": "CUSTOM", "name": "hangar.usage", "value": {"prompt_tokens": 1000, "completion_tokens": 200}},
                {"type": "CUSTOM", "name": "usage", "value": {"input_tokens": 500, "output_tokens": 100}},
                {"type": "RUN_FINISHED", "threadId": inp["threadId"], "runId": inp["runId"]}]
        return "".join(f"data: {json.dumps(e)}\n\n" for e in evs).encode()


@pytest.fixture()
def remote(monkeypatch, client):
    fake = MeteredRemote()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(remote_agui, "HTTP", lambda timeout: httpx.Client(transport=transport))
    monkeypatch.setattr(remote_agui, "AHTTP", lambda timeout: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", True)
    assert client.put("/api/governance", headers=ADMIN, json={"dlp": {"mask": ["cpf", "email"]}}).status_code == 200
    yield fake
    client.put("/api/governance", headers=ADMIN, json={"dlp": {"mask": []}})


def _agent(client, uniq, **remote) -> str:
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("Remoto DLP"), "objective": "x",
                                                           "final_output": "y"}).json()["slug"]
    r = client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"remote": {"url": URL, **remote}})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/agents/{slug}/deploy", headers=ADMIN, json={"env": "stage"}).status_code == 200
    return slug


def _usage(slug, protocol):
    with SessionLocal() as db:
        aid = svc.get_agent(db, slug).id
        return [(u.tokens_in, u.tokens_out, round(u.cost_usd, 6)) for u in
                db.query(UsageEvent).filter(UsageEvent.agent_id == aid, UsageEvent.protocol == protocol)]


def test_masker_tokens_are_stable_and_split_tokens_come_back_whole():
    m = Masker(["cpf"])
    a, b = m.mask(f"cliente {CPF}"), Masker(["cpf"]).mask(f"de novo: {CPF}")
    tok = a.split()[-1]
    assert CPF not in a and tok in b and tok.startswith("[CPF_")  # a mesma marca em outra rodada, sem guardar nada
    st = Stream(m, {"input_per_mtok": 2, "output_per_mtok": 10})
    out = []
    for piece in ["ok, ", tok[:4], tok[4:9], tok[9:] + " confirmado"]:
        out += st.events({"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": piece})
    out += st.events({"type": "TEXT_MESSAGE_END", "messageId": "m"})
    assert "".join(e.get("delta", "") for e in out) == f"ok, {CPF} confirmado" and out[-1]["type"] == "TEXT_MESSAGE_END"
    st.events({"type": "CUSTOM", "name": "hangar.usage", "value": {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}})
    assert st.result() == {"prompt_tokens": 1_000_000, "completion_tokens": 100_000, "total_tokens": 1_100_000, "cost_usd": 3.0}
    st.events({"type": "CUSTOM", "name": "usage", "value": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.5}})
    assert st.result()["cost_usd"] == 3.5 and st.result()["total_tokens"] == 1_100_015  # o custo informado vale
    assert Stream(Masker([])).result() is None  # o agente não informou nada


def test_agui_remote_sees_masks_app_gets_real_values_and_usage_is_metered(client, uniq, remote):
    slug = _agent(client, uniq, prices={"input_per_mtok": 3, "output_per_mtok": 15})
    msg = f"cadastre {CPF} com {EMAIL}"
    r = client.post(f"/gw-stage/{slug}/agui", headers=ADMIN, json={**RUN, "messages": [
        {"id": "1", "role": "user", "content": msg}], "state": {"cliente": {"cpf": CPF}}})
    assert r.status_code == 200, r.text
    sent = json.dumps(remote.calls[-1]["input"])
    assert CPF not in sent and EMAIL not in sent and "[CPF_" in sent and "[EMAIL_" in sent  # nem no estado do app
    evs = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    text = "".join(e.get("delta", "") for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert text == f"remoto: {msg}"  # o app recebe o valor real, mesmo com a marca partida em pedaços
    assert _usage(slug, "agui") == [(1500, 300, round((1500 * 3 + 300 * 15) / 1e6, 6))]


def test_other_protocols_get_the_same_dlp_and_metering(client, uniq, remote):
    slug = _agent(client, uniq, prices={"input_per_mtok": 3, "output_per_mtok": 15})
    r = client.post(f"/gw-stage/{slug}/v1/chat/completions", headers=ADMIN, json={"messages": [
        {"role": "user", "content": f"cpf {CPF}"}]})
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == f"remoto: cpf {CPF}" and CPF not in json.dumps(remote.calls[-1])
    assert r.json()["usage"]["total_tokens"] == 1800
    a2a = client.post(f"/gw-stage/{slug}/a2a", headers=ADMIN, json={"jsonrpc": "2.0", "id": "1", "method": "message/send",
                                                                    "params": {"message": {"kind": "message", "role": "user", "messageId": "m",
                                                                                           "parts": [{"kind": "text", "text": f"via a2a {EMAIL}"}]}}})
    assert a2a.json()["result"]["parts"][0]["text"] == f"remoto: via a2a {EMAIL}" and EMAIL not in json.dumps(remote.calls[-1])
    assert _usage(slug, "openai")[0][:2] == (1500, 300) and _usage(slug, "a2a")[0][:2] == (1500, 300)


def test_dlp_off_sends_real_values(client, uniq, remote):
    slug = _agent(client, uniq, dlp="off")
    client.post(f"/gw-stage/{slug}/agui", headers=ADMIN, json={**RUN, "state": {}, "messages": [
        {"id": "1", "role": "user", "content": f"cpf {CPF}"}]})
    assert CPF in json.dumps(remote.calls[-1]["input"])
