"""Catálogo Docker MCP: leitura do catálogo, ativação (segredos criptografados, arquivos do gateway), item do
catálogo do Hangar com prefixo de ferramentas e permissões."""
import httpx
import pytest
import yaml

from app import config
from app import services as svc
from app.db import SessionLocal
from app.models import GatewayServer

from .conftest import ADMIN
from .test_access import _session

CATALOG = {"registry": {
    "fetch": {"type": "server", "image": "mcp/fetch@sha256:1", "title": "Fetch", "description": "Lê URLs",
              "metadata": {"category": "web", "tags": ["web"]}},
    "github-official": {"type": "server", "image": "ghcr.io/github/github-mcp-server@sha256:2", "title": "GitHub",
                        "description": "API do GitHub",
                        "secrets": [{"name": "github.personal_access_token", "env": "GITHUB_PERSONAL_ACCESS_TOKEN"}]},
    "postgres": {"type": "server", "image": "mcp/postgres@sha256:3", "title": "Postgres", "description": "SQL",
                 "config": [{"properties": {"url": {"type": "string", "description": "postgresql://…"}}}]},
    "notion-remote": {"type": "remote", "title": "Notion", "remote": {"url": "https://x"}},
}}


@pytest.fixture()
def gw(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MCP_GATEWAY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(svc.mcp_gateway, "HTTP", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=yaml.safe_dump(CATALOG)))))
    svc.mcp_gateway._CACHE.update(at=0.0, servers={})
    yield tmp_path
    with SessionLocal() as db:
        db.query(GatewayServer).delete()
        db.commit()


def test_catalog_lists_container_servers_only(client, gw):
    r = client.get("/api/mcp-gateway/catalog", headers=ADMIN, params={"q": ""}).json()
    names = {s["name"] for s in r["servers"]}
    assert r["total"] == 3 and "notion-remote" not in names  # remotos/OAuth ficam de fora
    gh = next(s for s in r["servers"] if s["name"] == "github-official")
    assert gh["secrets"][0]["name"] == "github.personal_access_token"
    assert client.get("/api/mcp-gateway/catalog", headers=ADMIN, params={"q": "sql"}).json()["matches"] == 1


def test_enable_writes_gateway_files_and_catalog_item(client, gw):
    r = client.put("/api/mcp-gateway/servers/fetch", headers=ADMIN, json={})
    assert r.status_code == 200 and r.json()["catalog_mcp"] == "docker:fetch"
    # segredo obrigatório
    r = client.put("/api/mcp-gateway/servers/github-official", headers=ADMIN, json={})
    assert r.status_code == 400 and "github.personal_access_token" in r.json()["detail"]
    client.put("/api/mcp-gateway/servers/github-official", headers=ADMIN,
               json={"secrets": {"github.personal_access_token": "ghp_teste123"}})
    client.put("/api/mcp-gateway/servers/postgres", headers=ADMIN, json={"config": {"url": "postgresql://db/x"}})
    reg = yaml.safe_load((gw / "registry.yaml").read_text())
    assert set(reg["registry"]) == {"fetch", "github-official", "postgres"}
    assert yaml.safe_load((gw / "config.yaml").read_text()) == {"postgres": {"url": "postgresql://db/x"}}
    assert (gw / "secrets.env").read_text() == "github.personal_access_token=ghp_teste123\n"
    with SessionLocal() as db:  # no banco, o segredo fica criptografado
        row = db.query(GatewayServer).filter_by(name="github-official").one()
        assert row.secrets.startswith("enc:") and "ghp_teste123" not in row.secrets
    # item do catálogo do Hangar com prefixo de ferramentas; o agente recebe o prefixo na spec resolvida
    cat = client.get("/api/catalog", headers=ADMIN).json()["mcp_servers"]
    item = next(m for m in cat if m["name"] == "docker:fetch")
    assert item["tool_prefix"] == "fetch__" and item["url"] == config.MCP_GATEWAY_URL
    # atualizar sem repetir o segredo mantém o salvo
    client.put("/api/mcp-gateway/servers/github-official", headers=ADMIN, json={"secrets": {}})
    assert "ghp_teste123" in (gw / "secrets.env").read_text()
    # desativar tira do gateway e do catálogo
    assert client.delete("/api/mcp-gateway/servers/fetch", headers=ADMIN).status_code == 200
    assert "fetch" not in yaml.safe_load((gw / "registry.yaml").read_text())["registry"]
    assert all(m["name"] != "docker:fetch" for m in client.get("/api/catalog", headers=ADMIN).json()["mcp_servers"])


def test_rewrites_keep_the_same_file_for_the_gateway_watch(client, gw):
    """O --watch do gateway segue o arquivo: trocá-lo por outro (rename) fazia só a primeira mudança ser percebida."""
    import os
    client.put("/api/mcp-gateway/servers/fetch", headers=ADMIN, json={})
    before = {n: os.stat(gw / n).st_ino for n in ("registry.yaml", "config.yaml", "secrets.env")}
    client.put("/api/mcp-gateway/servers/github-official", headers=ADMIN,
               json={"secrets": {"github.personal_access_token": "ghp_teste123"}})
    client.put("/api/mcp-gateway/servers/postgres", headers=ADMIN, json={"config": {"url": "postgresql://db/x"}})
    assert {n: os.stat(gw / n).st_ino for n in before} == before
    assert set(yaml.safe_load((gw / "registry.yaml").read_text())["registry"]) == {"fetch", "github-official", "postgres"}
    assert (gw / "secrets.env").read_text() == "github.personal_access_token=ghp_teste123\n"
    client.delete("/api/mcp-gateway/servers/github-official", headers=ADMIN)
    assert (gw / "secrets.env").read_text() == ""  # encurtou: nada do conteúdo antigo sobra no fim do arquivo
    assert not list(gw.glob(".*.tmp"))


def test_reconnect_of_a_docker_connector_asks_the_gateway(client, gw, monkeypatch):
    """O conector `docker:<nome>` não tem endereço próprio: o "reconectar" pergunta ao gateway se ele subiu."""
    client.put("/api/mcp-gateway/servers/fetch", headers=ADMIN, json={})
    seen = []

    def probe(url, transport, headers, timeout=30):
        seen.append(url)
        return {"ok": True, "tools": [{"name": n, "hash": "h"} for n in tools]}
    monkeypatch.setattr(svc.connectors, "probe", probe)
    tools = ["duckduckgo__search"]  # o gateway no ar, mas sem o fetch
    r = client.post("/api/connectors/docker%3Afetch/check", headers=ADMIN).json()
    assert seen == [config.MCP_GATEWAY_URL] and r["status"] == "error" and "não subiu" in r["status_detail"]
    tools = ["fetch__fetch", "duckduckgo__search"]
    r = client.post("/api/connectors/docker%3Afetch/check", headers=ADMIN).json()
    assert r["status"] == "ok" and r["status_detail"] == ""


def test_agent_spec_gets_tool_prefix(client, gw, uniq):
    client.put("/api/mcp-gateway/servers/fetch", headers=ADMIN, json={})
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("Web"), "objective": "o", "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"mcps": ["docker:fetch"]})
    with SessionLocal() as db:
        spec = svc.resolve_spec(db, svc.get_agent(db, slug), "stage")
    assert spec["mcps"] == [{"name": "docker:fetch", "url": config.MCP_GATEWAY_URL, "tool_prefix": "fetch__"}]


def test_only_admins(client, gw, uniq):
    team = client.post("/api/teams", headers=ADMIN, json={"name": uniq("T")}).json()["slug"]
    email = f"{uniq('m')}@acme.com"
    client.post(f"/api/teams/{team}/members", headers=ADMIN, json={"email": email, "role": "maintainer"})
    h = _session(email)
    assert client.get("/api/mcp-gateway/catalog", headers=h).status_code == 403
    assert client.put("/api/mcp-gateway/servers/fetch", headers=h, json={}).status_code == 403


def test_unknown_or_remote_server_rejected(client, gw):
    assert client.put("/api/mcp-gateway/servers/notion-remote", headers=ADMIN, json={}).status_code == 400
    assert client.put("/api/mcp-gateway/servers/..%2Fetc", headers=ADMIN, json={}).status_code in (400, 404)
