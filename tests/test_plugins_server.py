# ruff: noqa: F811  (env é fixture importada de test_employees)
"""Plugins com código (P2b): um servidor MCP num container isolado por instalação. Imagem fixada por digest, o
container recebe só as configurações e a credencial da instalação, a central é o cliente MCP dele e o agente só vê as
ferramentas declaradas (e classificadas) no manifesto."""
import json

import pytest

from app import auth, config, deploy
from app.db import SessionLocal
from app.models import ActionCatalog
from app.services import plugins as plugmod

from .conftest import ADMIN
from .test_employees import env  # noqa: F401

DIGEST = "ghcr.io/acme/erp-mcp@sha256:" + "a" * 64
MANIFEST = f"""
name: {{name}}
title: ERP (servidor)
version: 1.0.0
runtime: server
auth: {{{{type: api_key, label: Chave do ERP}}}}
settings:
  - {{{{key: tenant, title: Tenant, required: true}}}}
  - {{{{key: region, title: Região, default: br}}}}
server:
  image: {DIGEST}
  port: 9000
  path: /mcp
  env: {{{{LOG_LEVEL: info}}}}
  settings_env: {{{{ERP_TENANT: tenant, ERP_REGION: region}}}}
  credential_env: ERP_KEY
  egress: [erp.example.com]
tools:
  - {{{{name: get_invoice, description: Uma fatura, action: read}}}}
  - {{{{name: pay_invoice, description: Paga uma fatura, action: financial}}}}
test: {{{{tool: get_invoice, args: {{{{number: "1"}}}}}}}}
"""


class FakeRuntime:
    """Containers e o servidor MCP do plugin, sem Docker."""

    def __init__(self):
        self.runs, self.stopped, self.states, self.calls = [], [], {}, []

    def run(self, name, image, environment, mem_limit, cpus, command=None, labels=None, port=8000):
        self.runs.append({"name": name, "image": image, "env": environment, "mem": mem_limit, "port": port})
        self.states[name] = "running"

    def state(self, name):
        return self.states.get(name, "missing")

    def stop(self, name):
        self.stopped.append(name)
        self.states.pop(name, None)

    def server(self, url, op, tool="", args=None, headers=None):
        self.calls.append((url, op, tool, args))
        self.headers = headers
        if op == "list":
            return [{"name": "get_invoice", "description": "x", "inputSchema": {"type": "object", "properties": {"number": {"type": "string"}}}},
                    {"name": "pay_invoice", "inputSchema": {"type": "object"}},
                    {"name": "drop_database", "inputSchema": {"type": "object"}}]
        env = next(r["env"] for r in reversed(self.runs) if r["name"] in url)
        return f"fatura {args.get('number')} (tenant {env['ERP_TENANT']}, chave {env['ERP_KEY']})", False


@pytest.fixture()
def fake(monkeypatch):
    f = FakeRuntime()
    monkeypatch.setattr(deploy, "run_plugin", f.run)
    monkeypatch.setattr(deploy, "plugin_state", f.state)
    monkeypatch.setattr(deploy, "stop_plugin", f.stop)
    monkeypatch.setattr(deploy, "plugin_logs", lambda name, tail=100: "boot ok key=k-123-secret\n")
    monkeypatch.setattr(plugmod, "SERVER", f.server)
    monkeypatch.setattr(plugmod, "_wait_ready", lambda url, timeout=30: True)
    return f


def _approved(client, env, name):
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": MANIFEST.format(name=name), "team": env["team"]})
    assert r.status_code == 200, r.text
    client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"])
    return client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"}).json()


def test_server_manifest_requires_a_pinned_image_and_known_settings(client, env, monkeypatch):
    monkeypatch.setattr(config, "PLUGIN_ALLOW_UNPINNED", False)
    tag = MANIFEST.format(name="srv-tag").replace(DIGEST, "ghcr.io/acme/erp-mcp:latest")
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": tag})
    assert r.status_code == 400 and "digest" in r.json()["detail"]
    bad = MANIFEST.format(name="srv-bad").replace("ERP_REGION: region", "ERP_REGION: nao_existe")
    assert "settings_env" in client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": bad}).json()["detail"]
    nocred = MANIFEST.format(name="srv-nocred").replace("  credential_env: ERP_KEY\n", "")
    assert "credential_env" in client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": nocred}).json()["detail"]
    monkeypatch.setattr(config, "PLUGIN_ALLOW_UNPINNED", True)
    assert client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": tag}).status_code == 200


def test_install_runs_an_isolated_container_and_agents_see_only_declared_tools(client, env, fake):
    name = f"srv-{env['n']}"
    d = _approved(client, env, name)
    perm = d["permissions"]
    assert perm["runtime"] == "server" and perm["pinned"] is True and perm["host"] == "erp.example.com"
    assert perm["actions"] == {"read": 1, "financial": 1}
    url = f"/api/plugins/{name}/install"
    r = client.put(url, headers=env["h"]["mgr"], json={"team": env["team"], "settings": {"tenant": "acme"}, "credential": "k-123-secret"})
    assert r.status_code == 200, r.text
    run = fake.runs[-1]
    assert run["image"] == DIGEST and run["port"] == 9000 and run["mem"] == config.PLUGIN_MEM_LIMIT
    proxy = {k: v for k, v in run["env"].items() if "PROXY" in k.upper()}
    assert {k: v for k, v in run["env"].items() if k not in proxy} == {
        "LOG_LEVEL": "info", "ERP_TENANT": "acme", "ERP_REGION": "br", "ERP_KEY": "k-123-secret"}
    # a saída para a internet vai pelo proxy da central, com a senha própria da instalação
    assert proxy["HTTPS_PROXY"].endswith(f"@{config.PLUGIN_EGRESS_HOST}:{config.PLUGIN_EGRESS_PORT}")
    assert proxy["HTTPS_PROXY"].startswith("http://i") and proxy["NO_PROXY"] == ""
    srv = r.json()["server"]
    assert srv["state"] == "running" and srv["image"] == DIGEST and "k-123-secret" not in r.text
    # testar: o servidor oferece as declaradas; a extra fica escondida
    t = client.post(f"{url}/test", headers=env["h"]["mgr"], json={"team": env["team"]}).json()
    assert t["ok"] is True and t["tool"] == "get_invoice" and "k-123-secret" not in t["sample"] and "[segredo]" in t["sample"]
    logs = client.get(f"{url}/logs", headers=env["h"]["mgr"], params={"team": env["team"]}).json()["logs"]
    assert "[segredo]" in logs and "k-123-secret" not in logs
    assert client.get(f"{url}/logs", headers=env["h"]["own"], params={"team": env["team"]}).status_code == 403
    # um agente do time com o plugin
    slug = client.post("/api/agents", headers=env["h"]["own"], json={"name": f"Pagador {env['n']}", "objective": "o",
                                                                     "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{slug}", headers=env["h"]["own"], json={"plugins": [name]})
    hdr = {"X-Agent-Slug": slug, "Authorization": f"Bearer {auth.agent_token(slug)}"}
    ep = f"/internal/plugins/{name}/mcp"
    tools = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    assert [x["name"] for x in tools] == ["get_invoice", "pay_invoice"]  # drop_database não aparece
    assert tools[0]["inputSchema"]["properties"] == {"number": {"type": "string"}}
    hidden = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                "params": {"name": "drop_database", "arguments": {}}}).json()
    assert hidden["error"]["message"].startswith("ferramenta desconhecida")
    out = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                             "params": {"name": "get_invoice", "arguments": {"number": "77"}}}).json()["result"]
    assert out["isError"] is False and "fatura 77 (tenant acme" in out["content"][0]["text"]
    assert "k-123-secret" not in json.dumps(out)
    with SessionLocal() as db:
        row = db.query(ActionCatalog).filter_by(tool_ref=f"mcp:plugin-{name}:pay_invoice").one()
        assert row.action_type == "financial"
    # o container caiu: a próxima chamada sobe de novo
    fake.states.clear()
    client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                       "params": {"name": "get_invoice", "arguments": {"number": "1"}}})
    assert len(fake.runs) == 2
    # pausar e desinstalar param o container; desligar o plugin também
    client.put(url, headers=env["h"]["mgr"], json={"team": env["team"], "enabled": False})
    assert fake.stopped[-1] == run["name"]
    client.put(url, headers=env["h"]["mgr"], json={"team": env["team"], "enabled": True})
    client.post(f"/api/plugins/{name}/disable", headers=ADMIN)
    assert fake.stopped[-1] == run["name"]


def test_new_approved_version_restarts_running_servers(client, env, fake):
    name = f"srv-v-{env['n']}"
    _approved(client, env, name)
    client.put(f"/api/plugins/{name}/install", headers=env["h"]["mgr"],
               json={"team": env["team"], "settings": {"tenant": "acme"}, "credential": "k-1"})
    new_image = "ghcr.io/acme/erp-mcp@sha256:" + "b" * 64
    v2 = MANIFEST.format(name=name).replace("1.0.0", "1.1.0").replace(DIGEST, new_image)
    client.put(f"/api/plugins/{name}", headers=env["h"]["own"], json={"manifest": v2})
    assert fake.runs[-1]["image"] == DIGEST  # rascunho não muda nada no ar
    client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"])
    client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"})
    assert fake.runs[-1]["image"] == new_image
