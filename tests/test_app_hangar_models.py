"""Modelos de token sincronizados com o app Hangar e o proxy de LLM (contrato: docs/app-hangar-models.md): o botão do
admin e as recusas dele, GET /api/app/models, a chave do dispositivo (rotação e revogação), o proxy com um upstream
falso (autorização, credencial do cofre, streaming SSE com tokens de message_start/message_delta, custo, orçamento e
política de código)."""
import json

import httpx
import pytest
from sqlalchemy import select

from app import auth
from app.db import SessionLocal
from app.models import AppDevice, AuditLog, BudgetPolicy, Team, TeamMember, UsageEvent
from app.routers import app_llm as proxy
from app.services import budgets, llm_models
from app.services import org as orgmod
from app.services.catalog import upsert_llm_connection

from .conftest import ADMIN
from .test_app_hangar import _bearer, _connect, _device, _many_registrations, _register, person  # noqa: F401

UPSTREAM = "http://upstream.test"
VAULT_KEY = "sk-ant-cofre-123"


class Upstream:
    """O provedor falso: guarda o que recebeu e responde como a API de Mensagens (JSON ou SSE em pedaços)."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.usage = {"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 3000,
                      "output_tokens": 1}
        self.out_tokens = 500

    def sse(self, model):
        start = {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                                      "model": model, "content": [], "usage": self.usage}}
        delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": self.out_tokens}}
        text = (f"event: message_start\ndata: {json.dumps(start)}\n\n"
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"oi"}}\n\n'
                f"event: message_delta\ndata: {json.dumps(delta)}\n\n"
                'event: message_stop\ndata: {"type":"message_stop"}\n\n').encode()
        return [text[i:i + 37] for i in range(0, len(text), 37)]  # pedaços que cortam linhas no meio

    async def handler(self, request: httpx.Request):
        self.calls.append(request)
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 42})
        body = json.loads(request.content)
        if body.get("stream"):
            chunks = self.sse(body["model"])

            async def gen():
                for c in chunks:
                    yield c
            return httpx.Response(200, headers={"content-type": "text/event-stream", "request-id": "req_1"}, content=gen())
        return httpx.Response(200, json={"id": "msg_2", "type": "message", "model": body["model"], "role": "assistant",
                                         "content": [{"type": "text", "text": "oi"}],
                                         "usage": {**self.usage, "output_tokens": self.out_tokens}})


@pytest.fixture()
def upstream(monkeypatch):
    up = Upstream()
    monkeypatch.setattr(proxy, "HTTP", lambda: httpx.AsyncClient(transport=httpx.MockTransport(up.handler)))
    return up


def _conn(db, uniq, protocol="anthropic", sync=True):
    name = uniq("conn-app")
    c = upsert_llm_connection(db, name, UPSTREAM, "claude-x", VAULT_KEY, "", "t", protocol, None, None,
                              "custom-anthropic" if protocol == "anthropic" else "custom")
    llm_models.add_manual(db, c, "claude-x", "text", 3.0, 15.0, 200000)
    llm_models.add_manual(db, c, "claude-y", "text", 1.0, 5.0)
    llm_models.add_manual(db, c, "embed-z", "embedding")
    if sync:
        r = _client_patch(c.name, {"sync": True})
        assert r.status_code == 200, r.text
    db.refresh(c)
    return c


_CLIENT = {}
_REG: dict = {}


def _app_client(client) -> str:
    """Um app registrado para o módulo inteiro: o registro dinâmico tem limite por hora e por endereço, e os testes de
    OAuth que rodam depois também registram."""
    if "id" not in _REG:
        _REG["id"] = _register(client)["client_id"]
    return _REG["id"]


def _client_patch(name, body, headers=ADMIN):
    return _CLIENT["c"].patch(f"/api/catalog/llm/{name}/desktop", headers=headers, json=body)


@pytest.fixture()
def app_device(client, person):  # noqa: F811
    """Uma pessoa conectada pelo app, com o dispositivo registrado e a chave do proxy."""
    _CLIENT["c"] = client
    t, _ = _connect(client, person, _app_client(client))
    body = _device()
    assert client.post("/api/app/devices", headers=_bearer(t), json=body).status_code == 200
    r = client.post(f"/api/app/devices/{body['device_id']}/llm-key", headers=_bearer(t))
    assert r.status_code == 200, r.text
    return {"token": t, "device_id": body["device_id"], "key": r.json()["key"], "person": person}


@pytest.fixture()
def code_policy(client):
    def set_(v):
        assert client.patch("/api/org", headers=ADMIN, json={"code_policy": v}).status_code == 200
    yield set_
    set_("any")


def _msg(model="claude-x", **kw):
    return {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "oi"}], **kw}


def _usage(device_id):
    with SessionLocal() as s:
        d = s.scalar(select(AppDevice).where(AppDevice.device_id == device_id))
        return list(s.scalars(select(UsageEvent).where(UsageEvent.device_id == d.id).order_by(UsageEvent.id)))


# ------------------------------------------------------------------ o botão do console
def test_sync_toggle_refusals_hide_and_audit(client, db, uniq, code_policy):
    _CLIENT["c"] = client
    oa = _conn(db, uniq, protocol="openai", sync=False)
    r = _client_patch(oa.name, {"sync": True})
    assert r.status_code == 400 and "anthropic" in r.json()["detail"]
    an = _conn(db, uniq, sync=False)
    code_policy("approved")
    r = _client_patch(an.name, {"sync": True})
    assert r.status_code == 400 and "aprove esta conexão para código" in r.json()["detail"]
    st = [x for x in client.get("/api/catalog", headers=ADMIN).json()["llm_connections"] if x["name"] == an.name][0]
    assert st["desktop"]["sync"] is False and "aprove" in st["desktop"]["reason"] and st["id"] == an.id
    assert client.patch(f"/api/catalog/llm/{an.name}/code", headers=ADMIN, json={"allow_code": True}).status_code == 200
    r = _client_patch(an.name, {"sync": True})
    assert r.status_code == 200 and r.json() == {"sync": True, "reason": "", "active": True, "models": 2,
                                                 "models_total": 2, "hidden": []}
    r = _client_patch(an.name, {"model_id": "claude-y", "hidden": True})
    assert r.json()["models"] == 1 and r.json()["hidden"] == ["claude-y"]
    assert _client_patch(an.name, {"model_id": "nao-existe", "hidden": True}).status_code == 400
    r = _client_patch(an.name, {"model_id": "claude-y", "hidden": False})
    assert r.json()["models"] == 2 and r.json()["hidden"] == []
    with SessionLocal() as s:
        details = [a.detail for a in s.scalars(select(AuditLog).where(AuditLog.action == "llm_connection.desktop",
                                                                      AuditLog.target == an.name))]
    assert any("sincronizada" in d for d in details) and any("claude-y escondido" in d for d in details)


def test_sync_endpoint_is_admin_only(client, person, db, uniq):  # noqa: F811
    _CLIENT["c"] = client
    c = _conn(db, uniq, sync=False)
    client.cookies.clear()
    assert client.post("/api/auth/password", json={"username": person[0], "password": person[1]}).status_code == 200
    csrf = {"X-CSRF-Token": client.cookies.get(auth.CSRF_COOKIE)}
    assert client.patch(f"/api/catalog/llm/{c.name}/desktop", headers=csrf, json={"sync": True}).status_code == 403
    client.cookies.clear()


# ------------------------------------------------------------------ GET /api/app/models
def test_models_lists_only_synced_visible_text_models(client, db, uniq, app_device):
    on = _conn(db, uniq)
    off = _conn(db, uniq, sync=False)
    h = _bearer(app_device["token"])
    r = client.get("/api/app/models", headers=h)
    assert r.status_code == 200, r.text
    got = {c["id"]: c for c in r.json()["connections"]}
    assert off.id not in got
    mine = got[on.id]
    assert mine["base_path"] == f"/api/app/llm/{on.id}" and mine["name"] == on.name and mine["provider"] == "custom-anthropic"
    assert [m["model_id"] for m in mine["models"]] == ["claude-x", "claude-y"]
    x = mine["models"][0]
    assert set(x) == {"model_id", "name", "context_length", "caps", "price_in_per_mtok", "price_out_per_mtok"}
    assert x["context_length"] == 200000 and (x["price_in_per_mtok"], x["price_out_per_mtok"]) == (3.0, 15.0)
    v1 = r.json()["version"]
    _client_patch(on.name, {"model_id": "claude-y", "hidden": True})
    r2 = client.get("/api/app/models", headers=h).json()
    assert r2["version"] != v1 and [m["model_id"] for m in {c["id"]: c for c in r2["connections"]}[on.id]["models"]] == ["claude-x"]
    assert client.get("/api/app/models", headers=h).json()["version"] == r2["version"]  # estável sem mudança
    assert client.get("/api/app/models").status_code == 401


# ------------------------------------------------------------------ chave do dispositivo
def test_llm_key_rotation_invalidates_previous(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    old = app_device["key"]
    assert old.startswith("ahl_")
    url = f"/api/app/llm/{c.id}/v1/models"
    r = client.get(url, headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 200 and [m["id"] for m in r.json()["data"]] == ["claude-x", "claude-y"]
    assert client.get(f"{url}/claude-x", headers={"x-api-key": old}).json()["type"] == "model"
    assert client.get(f"{url}/embed-z", headers={"x-api-key": old}).status_code == 404
    r = client.post(f"/api/app/devices/{app_device['device_id']}/llm-key", headers=_bearer(app_device["token"]))
    new = r.json()
    assert new["key"] != old and new["prefix"] == new["key"][:12] and new["created_at"]
    assert client.get(url, headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert client.get(url, headers={"x-api-key": new["key"]}).status_code == 200
    with SessionLocal() as s:
        d = s.scalar(select(AppDevice).where(AppDevice.device_id == app_device["device_id"]))
        assert d.llm_key_hash == auth.hash_key(new["key"]) and new["key"] not in (d.llm_key_prefix or "")
        assert any(a.action == "app.device.llm_key" and "trocada" in a.detail for a in s.scalars(select(AuditLog)))
    assert not upstream.calls  # /v1/models não chama o provedor


def test_llm_key_needs_app_token_and_active_device(client, person, app_device):  # noqa: F811
    dev = app_device["device_id"]
    assert client.post(f"/api/app/devices/{dev}/llm-key", headers=ADMIN).status_code == 403
    assert client.post("/api/app/devices/nao-existe-123/llm-key", headers=_bearer(app_device["token"])).status_code == 404


# ------------------------------------------------------------------ proxy: autorização
def test_proxy_authorization_matrix(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    off = _conn(db, uniq, sync=False)
    key = {"x-api-key": app_device["key"]}
    url = f"/api/app/llm/{c.id}/v1/messages"
    r = client.post(url, json=_msg())
    assert r.status_code == 401 and r.json()["error"]["type"] == "authentication_error"
    assert client.post(url, headers={"x-api-key": "ahl_nao-existe"}, json=_msg()).status_code == 401
    assert client.post(url, headers=_bearer(app_device["token"]), json=_msg()).status_code == 401  # token OAuth não serve
    assert client.post(url, headers=ADMIN, json=_msg()).status_code == 401
    assert client.post(url, headers=key, json=_msg()).status_code == 200
    assert client.post(url, headers={"Authorization": f"Bearer {app_device['key']}"}, json=_msg()).status_code == 200
    r = client.post(f"/api/app/llm/{off.id}/v1/messages", headers=key, json=_msg())
    assert r.status_code == 403 and "não está sincronizada" in r.json()["error"]["message"]
    assert client.post("/api/app/llm/999999/v1/messages", headers=key, json=_msg()).status_code == 404
    _client_patch(c.name, {"model_id": "claude-y", "hidden": True})
    r = client.post(url, headers=key, json=_msg("claude-y"))
    assert r.status_code == 400 and "claude-x" in r.json()["error"]["message"] and "detail" in r.json()
    assert client.post(url, headers=key, json=_msg("embed-z")).status_code == 400  # não é modelo de texto
    assert client.post(url, headers=key, json={"messages": []}).status_code == 400
    assert client.post(url, headers=key, content=b"nao-json").status_code == 400
    assert len(upstream.calls) == 2  # só as duas chamadas autorizadas chegaram ao provedor


def test_proxy_injects_vault_key_and_forwards_headers(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    r = client.post(f"/api/app/llm/{c.id}/v1/messages?beta=true", json=_msg(),
                    headers={"x-api-key": app_device["key"], "anthropic-version": "2023-06-01",
                             "anthropic-beta": "prompt-caching-2024-07-31", "cookie": "hangar_session=nao"})
    assert r.status_code == 200 and r.json()["content"][0]["text"] == "oi"
    req = upstream.calls[-1]
    assert str(req.url) == f"{UPSTREAM}/v1/messages?beta=true"
    assert req.headers["x-api-key"] == VAULT_KEY and req.headers["authorization"] == f"Bearer {VAULT_KEY}"
    assert req.headers["anthropic-version"] == "2023-06-01" and req.headers["anthropic-beta"] == "prompt-caching-2024-07-31"
    assert app_device["key"] not in str(req.headers) and "cookie" not in req.headers
    assert json.loads(req.content) == _msg()
    ev = _usage(app_device["device_id"])[-1]
    assert (ev.tokens_in, ev.tokens_out, ev.tokens_cache_write, ev.tokens_cache_read) == (1000, 500, 200, 3000)
    # 1000·3 + 200·3·1,25 + 3000·3·0,1 + 500·15 = 3000 + 750 + 900 + 7500 por 1M
    assert ev.cost_usd == pytest.approx(0.01215) and ev.client == "hangar-desktop" and ev.agent_id is None
    assert ev.connection_id == c.id and ev.model == "claude-x" and ev.ok and ev.user_id == app_device["person"][2]["id"]


def test_count_tokens_is_forwarded_without_usage(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    before = len(_usage(app_device["device_id"]))
    r = client.post(f"/api/app/llm/{c.id}/v1/messages/count_tokens", headers={"x-api-key": app_device["key"]},
                    json=_msg())
    assert r.status_code == 200 and r.json() == {"input_tokens": 42}
    assert str(upstream.calls[-1].url) == f"{UPSTREAM}/v1/messages/count_tokens"
    assert len(_usage(app_device["device_id"])) == before


# ------------------------------------------------------------------ proxy: streaming
def test_streaming_passthrough_counts_sse_tokens(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    upstream.out_tokens = 777
    with client.stream("POST", f"/api/app/llm/{c.id}/v1/messages", headers={"x-api-key": app_device["key"]},
                       json=_msg(stream=True)) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["request-id"] == "req_1"
        body = b"".join(r.iter_bytes())
    assert body == b"".join(upstream.sse("claude-x"))  # repassado intacto
    assert upstream.calls[-1].headers["accept"] == "text/event-stream"
    ev = _usage(app_device["device_id"])[-1]
    assert (ev.tokens_in, ev.tokens_out, ev.tokens_cache_write, ev.tokens_cache_read) == (1000, 777, 200, 3000)
    assert ev.cost_usd == pytest.approx((3000 + 750 + 900 + 777 * 15) / 1e6)
    with SessionLocal() as s:  # nada do conteúdo vai para a auditoria
        assert not any("oi" == a.detail or "messages" in (a.detail or "") for a in s.scalars(select(AuditLog)))


# ------------------------------------------------------------------ proxy: revogação
def test_admin_revoke_and_logout_kill_the_key(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    key = {"x-api-key": app_device["key"]}
    url = f"/api/app/llm/{c.id}/v1/models"
    assert client.get(url, headers=key).status_code == 200
    dev = [x for x in client.get("/api/app/admin/devices", headers=ADMIN).json() if x["device_id"] == app_device["device_id"]][0]
    assert client.post(f"/api/app/admin/devices/{dev['id']}/revoke", headers=ADMIN).status_code == 200
    assert client.get(url, headers=key).status_code == 401
    with SessionLocal() as s:
        assert s.scalar(select(AppDevice).where(AppDevice.device_id == app_device["device_id"])).llm_key_hash is None


def test_grant_revoked_or_person_deactivated_kills_the_key(client, db, uniq, person, upstream):  # noqa: F811
    _CLIENT["c"] = client
    c = _conn(db, uniq)
    t, _ = _connect(client, person, _app_client(client))
    body = _device()
    client.post("/api/app/devices", headers=_bearer(t), json=body)
    key = client.post(f"/api/app/devices/{body['device_id']}/llm-key", headers=_bearer(t)).json()["key"]
    url = f"/api/app/llm/{c.id}/v1/models"
    assert client.get(url, headers={"x-api-key": key}).status_code == 200
    client.post("/oauth/revoke", data={"token": t["access_token"]})  # saída pelo app
    r = client.get(url, headers={"x-api-key": key})
    assert r.status_code == 401 and "conecte o app de novo" in r.json()["error"]["message"]
    # login novo no mesmo computador: o dispositivo volta, mas a chave antiga não
    t2, _ = _connect(client, person, _app_client(client))
    assert client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t2), json={}).json() == {"status": "active"}
    assert client.get(url, headers={"x-api-key": key}).status_code == 401
    key2 = client.post(f"/api/app/devices/{body['device_id']}/llm-key", headers=_bearer(t2)).json()["key"]
    assert client.get(url, headers={"x-api-key": key2}).status_code == 200
    client.patch(f"/api/users/{person[2]['id']}", headers=ADMIN, json={"active": False})
    assert client.get(url, headers={"x-api-key": key2}).status_code == 401


# ------------------------------------------------------------------ proxy: política de código e orçamento
def test_code_policy_blocks_proxy_and_models(client, db, uniq, app_device, upstream, code_policy):
    c = _conn(db, uniq)
    code_policy("approved")
    r = client.post(f"/api/app/llm/{c.id}/v1/messages", headers={"x-api-key": app_device["key"]}, json=_msg())
    assert r.status_code == 403 and r.json()["error"]["type"] == "permission_error" and "código" in r.json()["error"]["message"]
    assert c.id not in {x["id"] for x in client.get("/api/app/models", headers=_bearer(app_device["token"])).json()["connections"]}
    client.patch(f"/api/catalog/llm/{c.name}/code", headers=ADMIN, json={"allow_code": True})
    assert client.post(f"/api/app/llm/{c.id}/v1/messages", headers={"x-api-key": app_device["key"]}, json=_msg()).status_code == 200


def _team(db, uniq, user_id, **kw):
    t = Team(slug=uniq("time-app"), name="Time App", **kw)
    db.add(t)
    db.commit()
    db.add(TeamMember(team_id=t.id, user_id=user_id, role="developer", org_id=1))
    db.commit()
    return t


def test_team_budget_enforce_returns_429_and_usage_counts(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    team = _team(db, uniq, app_device["person"][2]["id"], budget_usd_month=0.02, budget_enforce=True)
    url, key = f"/api/app/llm/{c.id}/v1/messages", {"x-api-key": app_device["key"]}
    assert client.post(url, headers=key, json=_msg()).status_code == 200  # US$ 0,01215
    ev = _usage(app_device["device_id"])[-1]
    assert ev.team_id == team.id
    assert orgmod.month_spend(db, team.id) == pytest.approx(0.01215)
    orgmod._BUDGET_CACHE.clear()
    assert client.post(url, headers=key, json=_msg()).status_code == 200  # 0,0243: passou do limite
    orgmod._BUDGET_CACHE.clear()
    r = client.post(url, headers=key, json=_msg())
    assert r.status_code == 429 and r.json()["error"]["type"] == "rate_limit_error" and "orçamento" in r.json()["error"]["message"]
    assert client.post(f"{url}/count_tokens", headers=key, json=_msg()).status_code == 200  # contar não custa


def test_team_budget_policy_hard_stop(client, db, uniq, app_device, upstream):
    c = _conn(db, uniq)
    team = _team(db, uniq, app_device["person"][2]["id"])
    db.add(BudgetPolicy(scope_type="team", scope_id=team.id, window="month", amount_usd=0.01, hard_stop=True, org_id=1))
    db.commit()
    budgets._cache.clear()
    url, key = f"/api/app/llm/{c.id}/v1/messages", {"x-api-key": app_device["key"]}
    assert client.post(url, headers=key, json=_msg()).status_code == 200
    budgets._cache.clear()
    r = client.post(url, headers=key, json=_msg())
    assert r.status_code == 429 and "orçamento parado" in r.json()["error"]["message"]
    budgets._cache.clear()


# ------------------------------------------------------------------ console
def test_overview_summarizes_synced_models(client, db, uniq):
    _CLIENT["c"] = client
    before = client.get("/api/app/admin/overview", headers=ADMIN).json()["models_synced"]
    _conn(db, uniq)
    after = client.get("/api/app/admin/overview", headers=ADMIN).json()["models_synced"]
    assert after == {"connections": before["connections"] + 1, "models": before["models"] + 2}


def test_migration_adds_columns(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from app import db as dbmod
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    cfg = dbmod._alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    try:
        cols = lambda t: {c["name"]: c for c in inspect(eng).get_columns(t)}  # noqa: E731
        assert {"sync_desktop", "desktop_hidden_models"} <= set(cols("llm_connections"))
        assert {"llm_key_hash", "llm_key_prefix", "llm_key_created_at"} <= set(cols("app_devices"))
        ue = cols("usage_events")
        assert {"team_id", "device_id", "connection_id", "client", "tokens_cache_read", "tokens_cache_write"} <= set(ue)
        assert ue["agent_id"]["nullable"]
        command.downgrade(cfg, "0052_app_hangar")
        assert "sync_desktop" not in cols("llm_connections") and not cols("usage_events")["agent_id"]["nullable"]
        command.upgrade(cfg, "head")
    finally:
        eng.dispose()
