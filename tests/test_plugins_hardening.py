# ruff: noqa: F811  (env, erp e fake são fixtures importadas)
"""Plugins, o que faltava para produção: saída de rede só para os hosts declarados (proxy da central), assinatura da
imagem (cosign), limite de chamadas por instalação, auditoria por chamada, OAuth2 nos plugins com código e gatilhos
testáveis em stage."""
import asyncio
import base64
import json

import pytest

from app import auth, config, egress
from app.db import SessionLocal
from app.models import Plugin, PluginInstall
from app.services import plugins as plugmod

from .conftest import ADMIN
from .test_employees import env  # noqa: F401
from .test_plugins import MANIFEST as HTTP_MANIFEST
from .test_plugins import _approved, erp  # noqa: F401
from .test_plugins_server import DIGEST, fake  # noqa: F401
from .test_plugins_server import MANIFEST as SERVER_MANIFEST


def _agent(client, env, plugin):
    slug = client.post("/api/agents", headers=env["h"]["own"], json={"name": f"Usa {plugin}", "objective": "o",
                                                                     "final_output": "f"}).json()["slug"]
    assert client.patch(f"/api/agents/{slug}", headers=env["h"]["own"], json={"plugins": [plugin]}).status_code == 200
    return slug, {"X-Agent-Slug": slug, "Authorization": f"Bearer {auth.agent_token(slug)}"}


def _call(client, plugin, hdr, tool, args, task=None):
    h = {**hdr, **({"X-Hangar-Task": str(task)} if task else {})}
    return client.post(f"/internal/plugins/{plugin}/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).json()["result"]


# ------------------------------------------------------------------ limite de chamadas e auditoria por chamada
def test_rate_limit_per_install_and_every_call_is_audited(client, env, erp):
    name = f"erp-rl-{env['n']}"
    _approved(client, env, name)
    url = f"/api/plugins/{name}/install"
    body = {"team": env["team"], "settings": {"tenant": "acme", "company_id": "42"}, "credential": "erp-secret-123",
            "rate_limit": 2}
    r = client.put(url, headers=env["h"]["mgr"], json=body)
    assert r.status_code == 200 and r.json()["rate_limit"] == 2 and r.json()["rate_limit_effective"] == 2
    slug, hdr = _agent(client, env, name)
    assert _call(client, name, hdr, "get_invoice", {"number": "1"})["isError"] is False
    assert _call(client, name, hdr, "send_invoice", {"number": "1", "email": "erp-secret-123@x.com"})["isError"] is False
    third = _call(client, name, hdr, "get_invoice", {"number": "2"})
    assert third["isError"] is True and "2 chamadas por minuto" in third["content"][0]["text"]
    assert len(erp.calls) == 2  # a terceira nem chegou ao sistema
    calls = client.get(f"/api/plugins/{name}/calls", headers=env["h"]["mgr"], params={"team": env["team"]}).json()
    items = calls["items"]
    assert [c["status"] for c in items] == ["limited", "ok", "ok"] and calls["retention_days"] == config.PLUGIN_CALLS_DAYS
    assert {c["caller"] for c in items} == {slug} and items[1]["tool"] == "send_invoice"
    assert items[1]["action"] == "send_external" and items[2]["http_status"] == 200
    assert "erp-secret-123" not in json.dumps(items) and "[segredo]" in items[1]["args"]  # segredo mascarado
    only = client.get(f"/api/plugins/{name}/calls", headers=env["h"]["mgr"],
                      params={"team": env["team"], "status": "limited"}).json()["items"]
    assert len(only) == 1
    # quem vê: mantenedor do time, admin e auditor; developer do time não
    assert client.get(f"/api/plugins/{name}/calls", headers=env["h"]["own"], params={"team": env["team"]}).status_code == 403
    assert client.get(f"/api/plugins/{name}/calls", headers=ADMIN, params={"team": env["team"]}).status_code == 200
    # 0 volta ao padrão da empresa
    r = client.put(url, headers=env["h"]["mgr"], json={"team": env["team"], "rate_limit": 0})
    assert r.json()["rate_limit"] is None and r.json()["rate_limit_effective"] == config.PLUGIN_RATE_LIMIT
    with SessionLocal() as db:
        assert plugmod.prune_calls(db) == 0  # nada antigo para apagar


def test_manifest_rate_limit_is_the_default_of_each_install(client, env, erp):
    name = f"erp-rlm-{env['n']}"
    m = HTTP_MANIFEST.format(name=name).replace("publisher: TI Financeiro", "publisher: TI Financeiro\nrate_limit: 7")
    assert client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": m, "team": env["team"]}).status_code == 200
    client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"])
    client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"})
    r = client.put(f"/api/plugins/{name}/install", headers=env["h"]["mgr"], json={
        "team": env["team"], "settings": {"tenant": "a", "company_id": "1"}, "credential": "k"})
    assert r.json()["rate_limit_effective"] == 7


# ------------------------------------------------------------------ saída de rede: só os hosts declarados
def test_egress_patterns_and_manifest_normalization(client, env):
    assert egress.allowed(["api.acme.com"], "api.acme.com", 443) and egress.allowed(["api.acme.com"], "API.acme.com.", 80)
    assert not egress.allowed(["api.acme.com"], "api.acme.com", 8443)  # outra porta só se declarada
    assert egress.allowed(["api.acme.com:8443"], "api.acme.com", 8443)
    assert not egress.allowed(["api.acme.com:8443"], "api.acme.com", 443)
    assert egress.allowed(["*.acme.com"], "eu.api.acme.com", 443) and not egress.allowed(["*.acme.com"], "acme.com", 443)
    assert not egress.allowed(["*.acme.com"], "evil-acme.com", 443) and not egress.allowed([], "acme.com", 443)
    m = SERVER_MANIFEST.format(name=f"srv-eg-{env['n']}").replace(
        "egress: [erp.example.com]", "egress: ['https://erp.example.com/api', '*.files.example.com', 'x.example.com:8443']")
    d = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": m}).json()
    assert d["manifest"]["server"]["egress"] == ["erp.example.com", "*.files.example.com", "x.example.com:8443"]
    bad = SERVER_MANIFEST.format(name=f"srv-eg2-{env['n']}").replace("egress: [erp.example.com]", "egress: ['a b']")
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": bad})
    assert r.status_code == 400 and "egress" in r.json()["detail"]


def test_egress_proxy_lets_declared_hosts_through_and_refuses_the_rest(monkeypatch):
    denied = []
    monkeypatch.setattr(config, "PLUGIN_EGRESS", "proxy")
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", True)  # o "sistema" do teste roda no localhost
    monkeypatch.setattr(egress, "_note_denied", lambda iid, host, port, why: denied.append((iid, host, port)))
    egress._cache.clear()

    async def scenario():
        async def upstream(r, w):  # o sistema: devolve o que recebe
            data = await r.read(100)
            w.write(b"echo:" + data)
            await w.drain()
            w.close()
        up = await asyncio.start_server(upstream, "127.0.0.1", 0)
        up_port = up.sockets[0].getsockname()[1]
        monkeypatch.setattr(egress, "_patterns_from_db", lambda iid: [f"127.0.0.1:{up_port}"] if iid == 7 else None)
        proxy = await asyncio.start_server(egress._handle, "127.0.0.1", 0)
        port = proxy.sockets[0].getsockname()[1]

        async def connect(target, iid=7, password=None):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            cred = base64.b64encode(f"i{iid}:{password or egress.token(iid)}".encode()).decode()
            w.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {cred}\r\n\r\n".encode())
            await w.drain()
            head = (await r.readuntil(b"\r\n\r\n")).decode()
            return head, r, w

        head, r, w = await connect(f"127.0.0.1:{up_port}")
        assert head.startswith("HTTP/1.1 200")
        w.write(b"ping")
        await w.drain()
        assert await r.read(100) == b"echo:ping"
        w.close()
        head, _, w = await connect("127.0.0.1:9")  # porta não declarada
        assert head.startswith("HTTP/1.1 403") and denied == [(7, "127.0.0.1", 9)]
        w.close()
        head, _, w = await connect(f"127.0.0.1:{up_port}", password="errada")  # senha de outra instalação
        assert head.startswith("HTTP/1.1 407")
        w.close()
        head, _, w = await connect(f"127.0.0.1:{up_port}", iid=8)  # instalação removida
        assert head.startswith("HTTP/1.1 403")
        w.close()
        proxy.close()
        up.close()
    asyncio.run(scenario())


def test_private_networks_stay_closed_through_the_proxy(monkeypatch):
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", False)
    with pytest.raises(PermissionError):
        asyncio.run(egress._resolve("127.0.0.1", 443))


# ------------------------------------------------------------------ OAuth2 nos plugins com código
OAUTH_SERVER = SERVER_MANIFEST.replace(
    "auth: {{type: api_key, label: Chave do ERP}}",
    "auth: {{type: oauth2, authorize_url: 'https://login.example.com/authorize', token_url: 'https://login.example.com/token'}}",
).replace("  credential_env: ERP_KEY\n", "")


def test_oauth2_code_plugin_gets_a_fresh_token_per_call_never_in_the_container(client, env, fake, monkeypatch):
    from datetime import timedelta

    from app import crypto
    from app.models import now
    from app.services.common import iso
    name = f"srv-oa-{env['n']}"
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": OAUTH_SERVER.format(name=name), "team": env["team"]})
    assert r.status_code == 200, r.text
    assert r.json()["manifest"]["server"]["credential_header"] == "Authorization"
    client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"])
    assert client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"}).status_code == 200
    r = client.put(f"/api/plugins/{name}/install", headers=env["h"]["mgr"], json={
        "team": env["team"], "settings": {"tenant": "acme"}, "oauth_client_id": "cid", "oauth_client_secret": "csec"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:  # conta conectada (o fluxo do navegador é testado em test_plugins_p2)
        i = db.query(PluginInstall).filter_by(plugin_id=db.query(Plugin).filter_by(name=name).one().id).one()
        sec = plugmod._secrets(i)
        sec["oauth"].update(access_token="tok-abc-123", refresh_token="ref-1", expires_at=iso(now() + timedelta(hours=1)))
        i.secret = crypto.encrypt(json.dumps(sec))
        db.commit()
    run = fake.runs[-1]
    assert "tok-abc-123" not in json.dumps(run["env"]) and "csec" not in json.dumps(run["env"])
    seen = []

    def server(url, op, tool="", args=None, headers=None):  # o servidor ecoa o token que recebeu
        seen.append(headers)
        if op == "list":
            return [{"name": "get_invoice", "inputSchema": {"type": "object"}}]
        return f"fatura {args['number']} com {headers['Authorization']}", False
    monkeypatch.setattr(plugmod, "SERVER", server)
    _, hdr = _agent(client, env, name)
    out = _call(client, name, hdr, "get_invoice", {"number": "9"})
    assert out["isError"] is False and seen[-1] == {"Authorization": "Bearer tok-abc-123"}
    assert "tok-abc-123" not in out["content"][0]["text"] and "[segredo]" in out["content"][0]["text"]


# ------------------------------------------------------------------ gatilhos testáveis em stage
TRIGGER = """triggers:
  - name: paid
    title: Fatura paga
    task_title: "Conciliar a fatura {{{{event.number}}}}"
    task_body: "Valor {{{{event.total}}}}"
    dedupe: number
    example: {{number: "NF-1", total: 1500}}
tests:
  - {{name: lê, tool: get_invoice, args: {{number: "1"}}, expect_contains: "1500"}}
  - {{name: gatilho, trigger: paid, event: {{number: "NF-9", total: 10}}, expect_contains: "Conciliar a fatura NF-9"}}
"""


def test_triggers_preview_in_stage_and_stage_webhooks_create_nothing(client, env, erp):
    name = f"erp-tg-{env['n']}"
    m = HTTP_MANIFEST.format(name=name).replace('test: {tool: get_invoice, args: {number: "1"}}\n', TRIGGER.format())
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": m, "team": env["team"]})
    assert r.status_code == 200, r.text
    pv = client.post(f"/api/plugins/{name}/triggers/paid/test", headers=env["h"]["own"], json={}).json()
    assert pv["title"] == "Conciliar a fatura NF-1" and "Valor 1500" in pv["body"] and pv["dedupe_key"].endswith(":NF-1")
    pv = client.post(f"/api/plugins/{name}/triggers/paid/test", headers=env["h"]["own"],
                     json={"event": {"number": "X"}}).json()
    assert pv["title"] == "Conciliar a fatura X"
    assert client.post(f"/api/plugins/{name}/triggers/paid/test", headers=env["h"]["out"], json={}).status_code == 403
    # instalação de stage + token: o webhook de teste do sistema confere a autenticação e só devolve a prévia
    r = client.put(f"/api/plugins/{name}/install", headers=env["h"]["own"], json={
        "team": env["team"], "stage": True, "settings": {"tenant": "t", "company_id": "1"}, "credential": "test-key"})
    assert r.status_code == 200, r.text
    tok = client.post(f"/api/plugins/{name}/install/hook-token", headers=env["h"]["own"],
                      json={"team": env["team"], "stage": True}).json()
    hook = "/" + tok["urls"]["paid"].split("/", 3)[3]  # o caminho de PUBLIC_BASE_URL/hooks/plugins/<id>/paid
    assert client.post(hook, json={"number": "NF-2"}).status_code == 401
    out = client.post(hook, json={"number": "NF-2", "total": 3}, headers={"Authorization": f"Bearer {tok['token']}"}).json()
    assert out["stage"] is True and out["preview"]["title"] == "Conciliar a fatura NF-2"
    # os testes em stage incluem o caso do gatilho
    rep = client.post(f"/api/plugins/{name}/tests", headers=env["h"]["own"], json={"team": env["team"]}).json()
    assert rep["passed"] is True and [x.get("trigger") for x in rep["results"]] == [None, "paid"]
    stage_calls = client.get(f"/api/plugins/{name}/calls", headers=env["h"]["own"],
                             params={"team": env["team"], "stage": True}).json()["items"]
    assert stage_calls and stage_calls[0]["caller"].startswith("stage-tests:")


# ------------------------------------------------------------------ assinatura da imagem (cosign)
def test_cosign_blocks_approval_and_start_of_unsigned_images(client, env, fake, monkeypatch):
    seen = []
    verdict = {"code": 1, "out": "", "err": "Error: no matching signatures"}

    def cosign(cmd):
        seen.append(cmd)
        return verdict["code"], verdict["out"], verdict["err"]
    monkeypatch.setattr(plugmod, "COSIGN", cosign)
    monkeypatch.setattr(config, "PLUGIN_COSIGN_KEY", "-----BEGIN PUBLIC KEY-----\\nMFkw\\n-----END PUBLIC KEY-----")
    monkeypatch.setattr(config, "PLUGIN_REQUIRE_SIGNED", True)
    name = f"srv-sig-{env['n']}"
    client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": SERVER_MANIFEST.format(name=name), "team": env["team"]})
    client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"])
    r = client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"})
    assert r.status_code == 400 and "no matching signatures" in r.json()["detail"]
    assert seen[-1][:4] == ["cosign", "verify", "--output", "json"] and seen[-1][-1] == DIGEST and "--key" in seen[-1]
    d = client.get(f"/api/plugins/{name}", headers=ADMIN).json()
    assert d["status"] == "pending" and d["signature"]["ok"] is False and d["signing"] == {"configured": True, "required": True}
    verdict.update(code=0, out=json.dumps([{"optional": {"Subject": "ci@acme"}}]), err="")
    d = client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"}).json()
    assert d["status"] == "approved" and d["signature"]["ok"] is True and "ci@acme" in d["signature"]["detail"]
    r = client.put(f"/api/plugins/{name}/install", headers=env["h"]["mgr"], json={
        "team": env["team"], "settings": {"tenant": "acme"}, "credential": "k-1"})
    assert r.status_code == 200 and fake.runs[-1]["image"] == DIGEST
    # keyless: identidade e emissor do certificado
    monkeypatch.setattr(config, "PLUGIN_COSIGN_KEY", "")
    monkeypatch.setattr(config, "PLUGIN_COSIGN_IDENTITY", "^https://github.com/acme/")
    assert plugmod.verify_image(DIGEST)["ok"] is True
    assert "--certificate-identity-regexp" in seen[-1] and "--certificate-oidc-issuer" in seen[-1]
    assert plugmod.verify_image("ghcr.io/acme/x:latest")["ok"] is False  # só por digest
