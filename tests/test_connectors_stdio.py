"""Conectores stdio (npx/uvx) em container isolado: validação com versão fixa, comando sem shell, segredos no cofre,
o container da ponte (variáveis, proxy de saída, token), teste em container temporário, status e importação."""
import base64
import json

import pytest

from app import egress
from app.db import SessionLocal
from app.models import McpServer
from app.services import connectors as cn

from .conftest import ADMIN
from .test_access import org_setup  # noqa: F401  (fixture)

FS = {"runner": "npx", "package": "@modelcontextprotocol/server-filesystem@2025.8.21", "args": ["/tmp"]}


def test_spec_needs_a_pinned_version_and_no_shell():
    s = cn.stdio_spec({**FS, "env": [{"name": "API_TOKEN", "value": "x", "secret": True}], "egress": ["API.example.com"]})
    assert cn.stdio_command(s) == ["npx", "-y", "@modelcontextprotocol/server-filesystem@2025.8.21", "/tmp"]
    assert s["egress"] == ["api.example.com"]
    py = cn.stdio_spec({"runner": "uvx", "package": "mcp-server-fetch@2025.4.7", "args": ["--ignore-robots-txt"]})
    assert py["package"] == "mcp-server-fetch==2025.4.7" and py["entry"] == "mcp-server-fetch"
    assert cn.stdio_command(py) == ["uvx", "--from", "mcp-server-fetch==2025.4.7", "mcp-server-fetch", "--ignore-robots-txt"]
    for bad in ({"runner": "npx", "package": "@modelcontextprotocol/server-filesystem"},  # sem versão
                {"runner": "npx", "package": "pkg@latest"}, {"runner": "uvx", "package": "mcp-server-fetch"},
                {"runner": "bash", "package": "x@1.0.0"}, {**FS, "env": [{"name": "PATH", "value": "/x"}]},
                {**FS, "env": [{"name": "HTTPS_PROXY", "value": "x"}]}, {**FS, "egress": ["http://evil"]},
                {**FS, "args": ["a\nb"]}):
        with pytest.raises(cn.PlatformError):
            cn.stdio_spec(bad)


def test_import_turns_npx_and_uvx_into_local_servers():
    text = json.dumps({"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@2025.8.21", "/data"]},
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch==2025.4.7"], "env": {"FETCH_API_KEY": "abc", "LEVEL": "1"}},
        "solto": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
        "node": {"command": "node", "args": ["server.js"]}}})
    out = {x["name"]: x for x in cn.import_preview(text)}
    assert out["files"]["importable"] and out["files"]["stdio"]["args"] == ["/data"]
    assert out["fetch"]["importable"] and [e["secret"] for e in out["fetch"]["stdio"]["env"]] == [True, False]
    assert out["solto"]["importable"] is False and "versão fixa" in out["solto"]["why"] and out["solto"]["fixable"]
    assert out["node"]["importable"] is False and "npx e uvx" in out["node"]["why"]


def test_egress_proxy_knows_the_connector_and_the_test_container(client, uniq):
    with SessionLocal() as db:
        m = McpServer(name=uniq("st"), url="x", stdio=cn.stdio_spec({**FS, "egress": ["api.example.com"]}))
        db.add(m)
        db.commit()
        cid = m.id
    key = f"c{cid}"
    auth = "Basic " + base64.b64encode(f"{key}:{egress.token(key)}".encode()).decode()
    assert egress._who({"proxy-authorization": auth}) == key
    assert egress._who({"proxy-authorization": "Basic " + base64.b64encode(f"{key}:errado".encode()).decode()}) is None
    pats = egress._patterns_from_db(key)
    assert egress.allowed(pats, "registry.npmjs.org", 443) and egress.allowed(pats, "api.example.com", 443)
    assert not egress.allowed(pats, "evil.example.com", 443)
    assert egress.proxy_url(key).startswith(f"http://{key}:")
    from app import shared
    shared.put("egress-test:tab12cd", {"patterns": ["pypi.org"]}, 60)
    t = "Basic " + base64.b64encode(f"tab12cd:{egress.token('tab12cd')}".encode()).decode()
    assert egress._who({"proxy-authorization": t}) == "tab12cd" and egress._patterns_from_db("tab12cd") == ["pypi.org"]
    # o plugin continua como antes (i<id>)
    assert egress._who({"proxy-authorization": "Basic " + base64.b64encode(f"i7:{egress.token(7)}".encode()).decode()}) == 7


@pytest.fixture()
def runner(monkeypatch):
    """Containers e servidor falsos: guarda o que a central mandou subir."""
    from app import deploy
    st = {"runs": [], "state": "missing", "stopped": []}

    def run(name, image, environment, mem_limit, cpus, command=None, labels=None, port=8000, tmp="size=64m"):
        st["runs"].append({"name": name, "image": image, "env": environment, "tmp": tmp, "mem": mem_limit})
        st["state"] = "running"
    monkeypatch.setattr(deploy, "run_plugin", run)
    monkeypatch.setattr(deploy, "plugin_state", lambda name: st["state"])
    monkeypatch.setattr(deploy, "stop_plugin", lambda name: st["stopped"].append(name))
    monkeypatch.setattr(cn, "HEALTH_GET", lambda url: type("R", (), {"status_code": 200, "headers": {"content-type": "application/json"},
                                                                     "json": lambda self: {"ok": True}})())
    seen = {}

    def probe(url, transport, headers, timeout=30):
        seen.update(url=url, headers=headers)
        return {"ok": True, "transport": "http", "protocol_version": "2025-06-18", "server": {"name": "fs", "version": "1"},
                "capabilities": {"tools": True}, "resources": 0, "resource_templates": 0, "prompts": 0,
                "tools": [{"name": "read_file", "description": "Lê um arquivo", "hash": "h1", "read_only": True},
                          {"name": "write_file", "description": "Escreve", "hash": "h2", "destructive": True}]}
    monkeypatch.setattr(cn, "probe", probe)
    st["probe"] = seen
    return st


def test_create_runs_an_isolated_container_with_secrets_from_the_vault(client, uniq, runner):
    name = uniq("fs")
    r = client.post("/api/connectors", headers=ADMIN, json={
        "name": name, "accept_risk": True, "stdio": {**FS, "env": [{"name": "FS_TOKEN", "value": "s3cr3t-valor", "secret": True},
                                                                  {"name": "LEVEL", "value": "debug"}]}})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == "ok" and c["stdio"]["runner"] == "npx" and c["stdio"]["registry"] == ["registry.npmjs.org"]
    assert c["stdio"]["env"][0]["value"].startswith("secret://") and c["stdio"]["env"][1]["value"] == "····"
    run = runner["runs"][0]
    assert run["name"] == f"mcp-stdio-{name}" and run["image"].endswith("/mcp-stdio-runner:latest") and "exec" in run["tmp"]
    assert json.loads(run["env"]["MCP_COMMAND"])[:3] == ["npx", "-y", FS["package"]]
    assert json.loads(run["env"]["MCP_SERVER_ENV"]) == {"FS_TOKEN": "s3cr3t-valor", "LEVEL": "debug"}  # só no container
    assert run["env"]["RUNNER_TOKEN"] == cn.runner_token(name) and "c" in run["env"].get("HTTPS_PROXY", "c")
    with SessionLocal() as db:
        m = db.query(McpServer).filter(McpServer.name == name).one()
        assert "s3cr3t" not in json.dumps(m.stdio) and m.upstream_url.endswith(f"mcp-stdio-{name}:8000/mcp")
    assert runner["probe"]["headers"]["X-Runner-Token"] == cn.runner_token(name)
    # status: o container caiu -> sobe de novo
    runner["state"] = "exited"
    assert client.post(f"/api/connectors/{name}/check", headers=ADMIN).json()["status"] == "ok" and len(runner["runs"]) == 2
    # remover derruba o container
    assert client.delete(f"/api/connectors/{name}", headers=ADMIN).json()["ok"] and runner["stopped"] == [f"mcp-stdio-{name}"]


def test_only_admins_add_local_servers_and_test_runs_in_a_temporary_container(client, uniq, runner, org_setup):  # noqa: F811
    r = client.post("/api/connectors", headers=org_setup["h"]["maint"], json={
        "name": uniq("x"), "accept_risk": True, "scope": "team", "team": org_setup["ta"], "stdio": FS})
    assert r.status_code == 403 and "só admins" in r.json()["detail"]
    t = client.post("/api/connectors/test", headers=ADMIN, json={"url": "", "stdio": FS}).json()
    assert t["ok"] and t["transport"] == "stdio" and {x["name"] for x in t["tools"]} == {"read_file", "write_file"}
    assert runner["runs"][-1]["name"].startswith("mcp-stdio-test-") and runner["stopped"][-1] == runner["runs"][-1]["name"]
    from app import shared
    assert shared.get("egress-test:t" + runner["runs"][-1]["name"].removeprefix("mcp-stdio-test-")) is None  # liberação some


def test_a_server_that_does_not_start_reports_why(client, uniq, runner, monkeypatch):
    monkeypatch.setattr(cn, "HEALTH_GET", lambda url: type("R", (), {"status_code": 503, "headers": {"content-type": "application/json"},
                                                                     "json": lambda self: {"ok": False, "error": "npm ERR! 404 Not Found"}})())
    c = client.post("/api/connectors", headers=ADMIN, json={"name": uniq("bad"), "accept_risk": True, "stdio": FS}).json()
    assert c["status"] == "error" and "npm ERR! 404" in c["status_detail"]
