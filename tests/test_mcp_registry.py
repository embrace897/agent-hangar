"""Registro MCP do GitHub como origem do catálogo de conectores: cada servidor vira um item com o jeito mais simples de
conectar (OAuth com registro dinâmico, aberto, token, cabeçalho, endereço da conta ou pacote npm/PyPI), o que não dá
para conectar assim fica de fora, o que já está no catálogo não se repete, e a sondagem nunca fala com a rede interna."""
import asyncio

import pytest

from app import config, shared
from app.services import connectors as cn
from app.services import mcp_gateway, mcp_registry, remote_mcp

from .conftest import ADMIN


def _gh(display, stars=10, org=True, topics=()):
    return {"io.modelcontextprotocol.registry/publisher-provided": {"github": {
        "display_name": display, "stargazer_count": stars, "is_in_organization": org, "license": "MIT License",
        "owner_avatar_url": "https://avatars.githubusercontent.com/u/1?v=4", "topics": list(topics)}}}


ENTRIES = [
    {"name": "com.acme/crm", "description": "CRM da Acme", "status": "active", "_meta": _gh("Acme", topics=["crm"]),
     "remotes": [{"transport_type": "streamable-http", "url": "https://mcp.acme.com/mcp"}]},
    {"name": "io.github.someone/docs", "description": "Documentação aberta", "_meta": _gh("someone", org=False),
     "remotes": [{"transport_type": "streamable-http", "url": "https://docs.someone.dev/mcp"}]},
    {"name": "io.github.netdata/mcp-server", "description": "Monitoramento", "_meta": _gh("Netdata"),
     "remotes": [{"transport_type": "streamable-http", "url": "https://app.netdata.cloud/api/v1/mcp", "headers": [
         {"name": "Authorization", "value": "Bearer {NETDATA_TOKEN}", "is_required": True, "is_secret": True,
          "variables": {"NETDATA_TOKEN": {"description": "token com escopo mcp", "is_secret": True}}}]}]},
    {"name": "com.zapier/mcp", "description": "Zapier", "_meta": _gh("Zapier"),
     "remotes": [{"transport_type": "sse", "url": "{zapier_mcp_url}",
                  "variables": {"zapier_mcp_url": {"description": "o endereço do seu MCP no Zapier"}}}]},
    {"name": "io.github.upstash/context7", "description": "Docs atualizadas", "_meta": _gh("Upstash"),
     "packages": [{"identifier": "@upstash/context7-mcp", "version": "4.4.4", "environment_variables": [
         {"name": "CONTEXT7_API_KEY", "description": "chave", "is_secret": True}]}]},
    {"name": "com.keyonly/api", "description": "Pede token", "_meta": _gh("KeyOnly"),
     "remotes": [{"transport_type": "streamable-http", "url": "https://api.keyonly.io/mcp"}]},
    {"name": "com.down/api", "description": "Fora do ar", "_meta": _gh("Down"),
     "remotes": [{"transport_type": "streamable-http", "url": "https://down.example.org/mcp"}]},
    {"name": "com.box/remote", "description": "OAuth sem registro dinâmico", "_meta": _gh("Box"),
     "remotes": [{"transport_type": "streamable-http", "url": "https://mcp.box.com"}]},
    {"name": "io.github.x/docker-only", "description": "Só imagem docker", "_meta": _gh("x"),
     "packages": [{"identifier": "ghcr.io/x/mcp", "version": "1.0.0", "runtime_hint": "docker"}]},
    {"name": "io.github.x/loose", "description": "Versão solta", "_meta": _gh("x"),
     "packages": [{"identifier": "loose-mcp", "runtime_hint": "npx"}]},
    {"name": "com.old/thing", "description": "Removido", "status": "deleted", "_meta": _gh("Old"),
     "remotes": [{"transport_type": "streamable-http", "url": "https://old.example.com/mcp"}]},
    # já no catálogo: mesmo host do Notion curado, e o mesmo nome do Linear curado
    {"name": "com.notion/mcp", "description": "Notion", "_meta": _gh("Notion"),
     "remotes": [{"transport_type": "streamable-http", "url": "https://mcp.notion.com/mcp"}]},
    {"name": "io.github.someone/linear", "description": "Linear da comunidade", "_meta": _gh("someone"),
     "remotes": [{"transport_type": "streamable-http", "url": "https://linear.someone.dev/mcp"}]},
]
PROBE = {"https://mcp.acme.com/mcp": "oauth", "https://docs.someone.dev/mcp": "open", "https://api.keyonly.io/mcp": "token",
         "https://down.example.org/mcp": "down", "https://mcp.box.com": "oauth_manual",
         "https://mcp.notion.com/mcp": "oauth", "https://linear.someone.dev/mcp": "open"}


@pytest.fixture()
def registry(monkeypatch):
    monkeypatch.setattr(config, "MCP_REGISTRY_URL", "https://registry.test/v0/servers")
    pages = [ENTRIES[:6], ENTRIES[6:]]

    def fetch(url, params):
        i = 1 if params.get("cursor") else 0
        return {"servers": [{"server": e} for e in pages[i]], "metadata": {"next_cursor": "p2" if i == 0 else None}}
    monkeypatch.setattr(mcp_registry, "FETCH", fetch)

    async def probe(c, url, transport="http"):
        return PROBE[url]
    monkeypatch.setattr(mcp_registry, "_probe", probe)
    monkeypatch.setattr(mcp_gateway, "catalog", lambda force=False: {})
    monkeypatch.setattr(mcp_gateway, "remotes", lambda: {})
    shared.pop(mcp_registry.KEY)
    shared.pop("mcp_registry:sync")
    mcp_registry._MEM.update(at=0.0, snap=None)
    yield
    shared.pop(mcp_registry.KEY)
    mcp_registry._MEM.update(at=0.0, snap=None)


def test_each_server_gets_the_simplest_way_to_connect(registry):
    snap = mcp_registry.sync()
    by = {it["registry_name"]: it for it in snap["items"]}
    assert by["com.acme/crm"]["auth"] == "oauth" and by["com.acme/crm"]["section"] == "sales"
    assert by["io.github.someone/docs"]["auth"] == "none"
    assert by["com.keyonly/api"]["auth"] == "bearer"
    nd = by["io.github.netdata/mcp-server"]
    assert nd["auth"] == "headers" and nd["id"] == "gh-netdata-mcp-server" and nd["name"] == "Netdata"
    assert nd["remote"]["headers"] == [{"name": "Authorization", "value": "Bearer {NETDATA_TOKEN}", "secret": True,
                                        "description": "", "variables": [{"name": "NETDATA_TOKEN",
                                                                          "description": "token com escopo mcp", "secret": True}]}]
    zp = by["com.zapier/mcp"]
    assert zp["auth"] == "url" and zp["remote"]["url_vars"][0]["name"] == "zapier_mcp_url" and zp["remote"]["transport"] == "sse"
    c7 = by["io.github.upstash/context7"]
    assert c7["auth"] == "stdio" and c7["package"] == {"runner": "npx", "package": "@upstash/context7-mcp@4.4.4", "env": [
        {"name": "CONTEXT7_API_KEY", "description": "chave", "secret": True, "required": False}]}
    # fora: fora do ar, OAuth sem registro dinâmico, só docker, versão solta, removido do registro
    for gone in ("com.down/api", "com.box/remote", "io.github.x/docker-only", "io.github.x/loose", "com.old/thing"):
        assert gone not in by
    assert mcp_registry.items() == snap["items"]  # retrato novo: nada de renovar


def test_catalog_shows_them_without_repeating_what_is_already_there(client, registry):
    mcp_registry.sync()
    items = client.get("/api/connectors", headers=ADMIN).json()["items"]
    gh = {x["registry_name"]: x for x in items if x["kind"] == "github"}
    assert "com.notion/mcp" not in gh and "io.github.someone/linear" not in gh  # o curado vale mais
    acme = gh["com.acme/crm"]
    assert acme["key"] == "github:gh-com-acme-crm" and acme["auth"] == "oauth" and acme["url"] == "https://mcp.acme.com/mcp"
    assert acme["transport"] == "auto"  # o registro às vezes erra o transporte: o teste de conexão decide
    assert acme["publisher"] == "com.acme" and acme["stars"] == 10 and acme["org"] and acme["license"] == "MIT License"
    assert gh["io.github.upstash/context7"]["stdio"]["package"] == "@upstash/context7-mcp@4.4.4"
    assert cn.icon_source("github:gh-com-acme-crm") == "https://avatars.githubusercontent.com/u/1?v=4"


def test_installing_a_registry_server_goes_through_the_bridge(client, registry, monkeypatch):
    mcp_registry.sync()
    seen = {}

    def probe(url, transport, headers, timeout=30):
        seen.update(url=url, headers=headers)
        return {"ok": True, "status": "ok", "transport": "http", "tools": [{"name": "status", "description": "x",
                                                                             "input_schema": {}, "read_only": True, "hash": "h1"}]}
    monkeypatch.setattr(cn, "probe", probe)
    r = client.post("/api/connectors", headers=ADMIN, json={
        "name": "gh-netdata-mcp-server", "url": "https://app.netdata.cloud/api/v1/mcp", "auth": "headers",
        "headers": [{"name": "Authorization", "value": "Bearer abc123", "secret": True}], "source": "github",
        "icon": "github:gh-netdata-mcp-server", "description": "Monitoramento", "accept_risk": True, "scope": "org"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["source"] == "github" and c["status"] == "ok" and c["bridged"] and c["headers"][0]["secret"]
    assert seen["headers"] == {"Authorization": "Bearer abc123"}  # o cofre devolve o valor só para a central
    items = client.get("/api/connectors", headers=ADMIN).json()["items"]
    assert next(x for x in items if x["id"] == "gh-netdata-mcp-server")["installed"]
    client.delete("/api/connectors/gh-netdata-mcp-server", headers=ADMIN)


def test_the_probe_never_talks_to_the_internal_network():
    async def run():
        import httpx
        async with httpx.AsyncClient() as c:
            return [await mcp_registry._probe(c, u) for u in
                    ("https://10.0.0.5/mcp", "https://127.0.0.1/mcp", "http://example.com/mcp", "https://169.254.169.254/x")]
    assert asyncio.run(run()) == ["down"] * 4


def test_oauth_server_on_another_domain_is_used_as_announced(monkeypatch):
    """O servidor de autorização em outro domínio (auth.acme.com, WorkOS…) é usado como anunciado; o local
    (Activepieces anuncia localhost) e o do próprio host do MCP seguem pela origem do MCP."""
    monkeypatch.setattr(remote_mcp, "RESOLVES", lambda host: host != "nao-resolve.internal")
    origin = "http://activepieces:80"
    assert remote_mcp._reach("https://auth.acme.com/oauth/token", "https://mcp.acme.com") == "https://auth.acme.com/oauth/token"
    assert remote_mcp._reach("http://localhost:8080/oauth/token", origin) == "http://activepieces:80/oauth/token"
    assert remote_mcp._reach("https://mcp.acme.com:8443/token", "https://mcp.acme.com") == "https://mcp.acme.com/token"
    assert remote_mcp._reach("http://nao-resolve.internal/token", origin) == "http://activepieces:80/token"


def test_without_a_registry_url_nothing_is_fetched(monkeypatch):
    monkeypatch.setattr(config, "MCP_REGISTRY_URL", "")
    monkeypatch.setattr(mcp_registry, "FETCH", lambda *a: (_ for _ in ()).throw(AssertionError("buscou o registro")))
    assert mcp_registry.items() == [] and mcp_registry.sync()["disabled"]


def test_section_ignores_the_github_namespace():
    e = {"name": "io.github.someone/notes", "description": "Notas e tarefas do dia", "_meta": _gh("someone", topics=[])}
    assert mcp_registry._section(e, mcp_registry._github(e)) == "productivity"
