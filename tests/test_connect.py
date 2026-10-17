"""Conexões plug and play: um agente por ferramenta, em modo MCP (todas) ou modelo (chats)."""
import json

import pytest

from app import services as svc

from .conftest import ADMIN


@pytest.mark.parametrize("client", list(svc.connect.CLIENTS))
def test_every_supported_combination_renders(client):
    for mode in svc.connect.CLIENTS[client][1]:
        r = svc.connect.snippet(client, mode, "meu-agente", "Meu Agente", key="ah_TESTE")
        assert "ah_TESTE" in r["content"] and "/gw/meu-agente/" in r["content"] and r["steps"]
        if r["language"] == "json":
            json.loads(r["content"])  # JSON pronto para colar, válido
        if mode == "mcp":
            assert "/gw/meu-agente/mcp" in r["content"]
        elif mode == "agui":
            assert "/gw/meu-agente/agui" in r["content"]
        else:
            assert "/gw/meu-agente/v1" in r["content"]


def test_mcp_is_offered_to_every_tool_platform_and_model_to_chats():
    modes = {k: v[1] for k, v in svc.connect.CLIENTS.items()}
    for tool in ("claude-code", "claude-desktop", "codex", "opencode", "cursor", "vscode", "librechat", "open-webui"):
        assert "mcp" in modes[tool]
    for chat in ("librechat", "open-webui", "opencode", "openai-sdk"):
        assert "model" in modes[chat]


def test_unsupported_mode_is_rejected():
    with pytest.raises(svc.PlatformError, match="não suporta"):
        svc.connect.snippet("claude-code", "model", "x", "X")
    with pytest.raises(svc.PlatformError, match="desconhecido"):
        svc.connect.snippet("netscape", "mcp", "x", "X")


def test_connection_api_creates_scoped_key_lists_and_revokes(client, uniq):
    slug = client.post("/api/agents", headers=ADMIN,
                       json={"name": uniq("Conn"), "objective": "o", "final_output": "f"}).json()["slug"]
    other = client.post("/api/agents", headers=ADMIN,
                        json={"name": uniq("Outro"), "objective": "o", "final_output": "f"}).json()["slug"]
    assert {c["id"] for c in client.get("/api/connect/clients", headers=ADMIN).json()} >= {"claude-code", "open-webui"}

    prev = client.get(f"/api/agents/{slug}/connections/snippet", headers=ADMIN,
                      params={"client": "codex", "mode": "mcp"}).json()
    assert "<SUA_CHAVE>" in prev["content"]  # prévia não cria chave
    assert client.get(f"/api/agents/{slug}/connections", headers=ADMIN).json() == []

    r = client.post(f"/api/agents/{slug}/connections", headers=ADMIN, json={"client": "claude-code", "mode": "mcp"})
    assert r.status_code == 200
    body = r.json()
    key = body["key"]
    assert key in body["content"] and body["connection"]["client"] == "claude-code"
    assert client.post(f"/api/agents/{slug}/connections", headers=ADMIN,
                       json={"client": "claude-code", "mode": "model"}).status_code == 400

    h = {"Authorization": f"Bearer {key}"}
    assert client.post(f"/gw/{slug}/mcp", headers=h, json={}).status_code == 503      # autorizado (não está no ar)
    assert client.post(f"/gw/{other}/mcp", headers=h, json={}).status_code == 403     # só este agente
    assert client.get("/api/agents", headers=h).status_code == 403                    # não administra

    conns = client.get(f"/api/agents/{slug}/connections", headers=ADMIN).json()
    assert [c["client_label"] for c in conns] == ["Claude Code"]
    assert client.delete(f"/api/keys/{conns[0]['id']}", headers=ADMIN).status_code == 200
    assert client.get(f"/api/agents/{slug}/connections", headers=ADMIN).json() == []
    assert client.post(f"/gw/{slug}/mcp", headers=h, json={}).status_code == 401


def test_channel_defaults_to_the_key_client():
    from starlette.requests import Request

    from app import auth
    from app.routers.gateway import channel_of

    def req(headers, principal):
        return Request({"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
                        "state": {"principal": principal}})
    p = auth.Principal("key:x", {"invoke"}, ["a"], "open-webui")
    assert channel_of(req({}, p)) == "open-webui"
    assert channel_of(req({"x-channel": "slack"}, p)) == "slack"
    assert channel_of(req({}, auth.Principal("admin-token", {"admin"}))) == "api"


def test_only_mcp_tool_calls_count_as_usage():
    from app.routers.gateway import _is_tool_call
    assert _is_tool_call(b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{}}')
    assert not _is_tool_call(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}')
    assert not _is_tool_call(b'{"jsonrpc":"2.0","method":"notifications/initialized"}')
    assert not _is_tool_call(b"")
