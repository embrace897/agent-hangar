# ruff: noqa: F811  (env é fixture importada de test_employees)
"""Plugins (P1): manifesto declarativo validado, importação de OpenAPI, revisão de quatro olhos, instalação por time
com segredos criptografados e o servidor MCP interno que injeta a credencial (que nunca chega ao agente)."""
import json

import httpx
import pytest

from app import auth, config
from app import services as svc
from app.db import SessionLocal
from app.models import ActionCatalog, Plugin, PluginInstall
from app.services import plugins as plugmod

from .conftest import ADMIN
from .test_employees import env  # noqa: F401

MANIFEST = """
name: {name}
title: ACME ERP
version: 1.0.0
description: Faturas e clientes do ERP.
publisher: TI Financeiro
base_url: https://{{{{settings.tenant}}}}.erp.example.com/api
auth: {{type: api_key, in: header, name: X-ERP-Key, label: Chave da API do ERP}}
settings:
  - {{key: tenant, title: Tenant, required: true}}
  - {{key: company_id, title: Empresa, required: true}}
tools:
  - name: get_invoice
    description: Uma fatura pelo número.
    method: GET
    path: /companies/{{{{settings.company_id}}}}/invoices/{{number}}
    parameters: {{type: object, properties: {{number: {{type: string}}}}, required: [number]}}
  - name: send_invoice
    description: Envia a fatura ao cliente.
    method: POST
    path: /invoices/{{number}}/send
    action: send_external
    parameters: {{type: object, properties: {{number: {{type: string}}, email: {{type: string}}}}}}
skills:
  - {{name: glossario, content: "Fatura vencida = mais de 30 dias."}}
test: {{tool: get_invoice, args: {{number: "1"}}}}
"""


class FakeERP:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, headers=None, params=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "json": json})
        if "/echo" in url:
            return httpx.Response(200, json={"headers": headers}, request=httpx.Request(method, url))
        if "/send" in url:
            return httpx.Response(202, json={"queued": True}, request=httpx.Request(method, url))
        return httpx.Response(200, json={"number": url.rsplit("/", 1)[-1], "total": 1500}, request=httpx.Request(method, url))


@pytest.fixture()
def erp(monkeypatch):
    fake = FakeERP()
    monkeypatch.setattr(plugmod, "HTTP", lambda: fake)
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", True)  # o host de teste não resolve
    return fake


def _approved(client, env, name):
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": MANIFEST.format(name=name), "team": env["team"]})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"]).json()["status"] == "pending"
    d = client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"}).json()
    assert d["status"] == "approved" and d["approved_version"] == "1.0.0"
    return d


def test_manifest_is_validated_with_clear_errors(client, env):
    bad = MANIFEST.format(name="erp-bad").replace("/invoices/{number}/send", "https://evil.com/x")
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": bad})
    assert r.status_code == 400 and "path" in r.json()["detail"]
    r = client.post("/api/plugins", headers=env["h"]["own"],
                    json={"manifest": MANIFEST.format(name="erp-bad2").replace("action: send_external", "action: teleport")})
    assert r.status_code == 400 and "teleport" in r.json()["detail"]
    r = client.post("/api/plugins", headers=env["h"]["own"],
                    json={"manifest": MANIFEST.format(name="erp-bad3").replace("  - {key: company_id, title: Empresa, required: true}\n", "")})
    assert r.status_code == 400 and "company_id" in r.json()["detail"]
    assert client.post("/api/plugins", headers=env["h"]["req"], json={"manifest": "nome: x"}).status_code == 400


def test_four_eyes_review_classifies_the_actions_and_versions_stay_pinned(client, env):
    name = f"erp-{env['n']}"
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": MANIFEST.format(name=name), "team": env["team"]})
    p = r.json()
    assert p["status"] == "draft" and p["permissions"]["host"] == "{setting}.erp.example.com"
    assert p["permissions"]["actions"] == {"read": 1, "send_external": 1} and p["permissions"]["risky"] == ["send_external"]
    client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"])
    assert client.post(f"/api/plugins/{name}/review", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 403
    assert client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "reject"}).status_code == 400  # diga por quê
    client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"})
    with SessionLocal() as db:
        row = db.query(ActionCatalog).filter_by(tool_ref=f"mcp:plugin-{name}:send_invoice").one()
        assert row.action_type == "send_external" and row.classified_by == f"plugin:{name}@1.0.0"
    # a mesma versão não muda depois de aprovada; uma versão nova vira rascunho e as instalações seguem na aprovada
    same = MANIFEST.format(name=name).replace("Uma fatura pelo número.", "Outra descrição")
    assert client.put(f"/api/plugins/{name}", headers=env["h"]["own"], json={"manifest": same}).status_code == 400
    d = client.put(f"/api/plugins/{name}", headers=env["h"]["own"], json={"manifest": same.replace("1.0.0", "1.1.0")}).json()
    assert d["status"] == "draft" and d["approved_version"] == "1.0.0" and d["approved_manifest"]["version"] == "1.0.0"
    assert client.put(f"/api/plugins/{name}", headers=env["h"]["out"], json={"manifest": same}).status_code == 403
    # o catálogo mostra aprovados a todos; rascunhos de outro time não aparecem
    assert name in [x["name"] for x in client.get("/api/plugins", headers=env["h"]["out"]).json()]


def test_openapi_import_builds_a_reviewable_draft():
    doc = {"openapi": "3.0.3", "info": {"title": "Billing API", "version": "2.1.0"},
           "servers": [{"url": "https://billing.example.com/v2"}],
           "components": {"securitySchemes": {"k": {"type": "apiKey", "in": "header", "name": "X-Billing-Key"}},
                          "schemas": {"Charge": {"type": "object", "required": ["amount"],
                                                 "properties": {"amount": {"type": "number"}, "customer": {"type": "string"}}}}},
           "paths": {"/customers/{id}": {"get": {"operationId": "getCustomer", "summary": "Um cliente",
                                                 "parameters": [{"name": "id", "in": "path", "schema": {"type": "string"}}]}},
                     "/charges": {"post": {"operationId": "createCharge", "summary": "Cobra",
                                           "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Charge"}}}}}},
                     "/customers/{id}/notify": {"post": {"operationId": "sendReminder"}}}}
    m = plugmod.from_openapi(json.dumps(doc))
    assert m["name"] == "billing-api" and m["base_url"] == "https://billing.example.com/v2" and m["version"] == "2.1.0"
    assert {k: m["auth"][k] for k in ("type", "in", "name")} == {"type": "api_key", "in": "header", "name": "X-Billing-Key"}
    tools = {t["name"]: t for t in m["tools"]}
    assert tools["get_customer"]["action"] == "read" and tools["get_customer"]["parameters"]["required"] == ["id"]
    assert tools["create_charge"]["action"] == "financial" and "amount" in tools["create_charge"]["parameters"]["properties"]
    assert tools["send_reminder"]["action"] == "send_external"


def test_install_per_team_keeps_secrets_and_mcp_injects_the_credential(client, env, erp):
    name = f"erp-i-{env['n']}"
    _approved(client, env, name)
    url = f"/api/plugins/{name}/install"
    body = {"team": env["team"], "settings": {"tenant": "acme", "company_id": "42"}, "credential": "erp-secret-123"}
    assert client.put(url, headers=env["h"]["own"], json=body).status_code == 403  # developer não instala
    assert client.put(url, headers=env["h"]["mgr"], json={**body, "credential": None}).status_code == 400  # falta a chave
    r = client.put(url, headers=env["h"]["mgr"], json=body)
    assert r.status_code == 200 and r.json()["credential"] is True and "erp-secret-123" not in r.text
    with SessionLocal() as db:  # criptografado no banco
        pid = db.query(Plugin).filter_by(name=name).one().id
        row = db.query(PluginInstall).filter_by(plugin_id=pid).one()
        assert row.secret.startswith("enc:") and "erp-secret-123" not in row.secret
    t = client.post(f"{url}/test", headers=env["h"]["mgr"], json={"team": env["team"]}).json()
    assert t["ok"] is True and t["tool"] == "get_invoice"
    # um agente do time com o plugin na spec
    slug = client.post("/api/agents", headers=env["h"]["own"], json={"name": f"Cobrador {env['n']}", "objective": "o",
                                                                     "final_output": "f"}).json()["slug"]
    assert client.patch(f"/api/agents/{slug}", headers=env["h"]["own"], json={"plugins": [name]}).status_code == 200
    with SessionLocal() as db:
        spec = svc.runtime.resolve_spec(db, svc.get_agent(db, slug), "stage")
    mcp = next(m for m in spec["mcps"] if m["name"] == f"plugin-{name}")
    assert mcp["url"].endswith(f"/internal/plugins/{name}/mcp") and "erp-secret-123" not in json.dumps(spec)
    assert any(s["name"] == f"{name}:glossario" for s in spec["skills_resolved"])
    hdr = {"X-Agent-Slug": slug, "Authorization": f"Bearer {auth.agent_token(slug)}"}
    ep = f"/internal/plugins/{name}/mcp"
    init = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-06-18"}}).json()
    assert init["result"]["serverInfo"]["name"] == f"plugin-{name}"
    assert client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    tools = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_invoice", "send_invoice"]
    out = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                             "params": {"name": "get_invoice", "arguments": {"number": "NF 7/1"}}}).json()
    assert out["result"]["isError"] is False and '"total": 1500' in out["result"]["content"][0]["text"]
    call = erp.calls[-1]
    assert call["url"] == "https://acme.erp.example.com/api/companies/42/invoices/NF%207%2F1"  # parâmetro escapado
    assert call["headers"]["X-ERP-Key"] == "erp-secret-123"
    post = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "send_invoice", "arguments": {"number": "7", "email": "a@b.com"}}}).json()
    assert post["result"]["isError"] is False and erp.calls[-1]["json"] == {"email": "a@b.com"}
    detail = client.get(f"/api/plugins/{name}", headers=env["h"]["mgr"]).json()
    assert detail["installs"][0]["stats"]["calls"] == 2
    # sem o plugin na spec, ou com token de outro agente: não chama
    other = client.post("/api/agents", headers=env["h"]["own"], json={"name": f"Outro {env['n']}", "objective": "o",
                                                                      "final_output": "f"}).json()["slug"]
    oh = {"X-Agent-Slug": other, "Authorization": f"Bearer {auth.agent_token(other)}"}
    assert client.post(ep, headers=oh, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 403
    assert client.post(ep, headers={**hdr, "Authorization": f"Bearer {auth.agent_token(other)}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    # desligado pelo admin: o agente não consegue mais usar
    client.post(f"/api/plugins/{name}/disable", headers=ADMIN)
    err = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"}).json()
    assert "não está aprovado" in err["error"]["message"]


def test_private_hosts_are_blocked_unless_allowed(monkeypatch):
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", False)
    with pytest.raises(svc.PlatformError, match="rede interna"):
        plugmod._check_host("127.0.0.1")


def test_secrets_are_masked_in_what_the_system_returns(env, erp):
    m = plugmod.parse(MANIFEST.format(name="erp-echo"))
    m["tools"].append({"name": "echo", "method": "GET", "path": "/echo", "action": "read",
                       "parameters": {"type": "object", "properties": {}}})
    inst = PluginInstall(settings={"tenant": "acme", "company_id": "1"},
                         secret=svc.plugins.crypto.encrypt(json.dumps({"credential": "super-secret-key"})))
    text, err, _ = plugmod._execute(None, m, inst, m["tools"][-1], {})
    assert not err and "super-secret-key" not in text and "[segredo]" in text



def test_gmail_oauth_example_manifest_is_valid():
    """O exemplo examples/plugins/gmail.hangar-plugin.yaml continua passando na validação (e só lê)."""
    import pathlib

    import yaml

    from app.services import plugins
    doc = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / "examples/plugins/gmail.hangar-plugin.yaml").read_text(encoding="utf-8"))
    m = plugins.parse(doc)
    assert m["auth"]["type"] == "oauth2" and m["auth"]["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert {t["name"]: t["action"] for t in m["tools"]} == {"search_messages": "read", "get_message": "read",
                                                            "get_thread": "read", "list_labels": "read"}
