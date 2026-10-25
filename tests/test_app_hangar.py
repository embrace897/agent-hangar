"""App Hangar (desktop) na plataforma: OAuth com loopback de porta livre e escopos (`app`, `mcp`), a API /api/app
(perfil, dispositivos, heartbeat, design tokens), o admin (visão geral, lista, revogação) e o que revoga um
dispositivo (admin, saída do app, desligamento). Contrato: docs/app-hangar.md."""
import base64
import hashlib
import json
import os
import re
import secrets
import urllib.parse
import uuid

import pytest

from app import auth, config
from app.services import mcp_oauth as oa

from .conftest import ADMIN

MCP_HDR = {"Accept": "application/json, text/event-stream"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
LOOPBACK = "http://127.0.0.1/callback"
STATIC = os.path.join(os.path.dirname(__file__), "..", "central", "app", "static")


@pytest.fixture(autouse=True)
def _many_registrations(monkeypatch):
    monkeypatch.setattr(oa, "MAX_REGISTRATIONS_PER_HOUR", 10 ** 6)  # cada teste registra os seus apps


@pytest.fixture()
def person(client, uniq):
    name = uniq("app").lower()
    pw = "-".join(["Senha", "Boa", "123"])
    u = client.post("/api/users", headers=ADMIN, json={"username": name, "password": pw, "name": "Pessoa App"}).json()
    yield name, pw, u
    client.cookies.clear()


def _pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _register(client, uris=(LOOPBACK,), scope="app mcp", name="Hangar Desktop"):
    body = {"client_name": name, "redirect_uris": list(uris), "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none"}
    if scope is not None:
        body.update(scope=scope, software_id="hangar-desktop", software_version="0.1.0")
    r = client.post("/oauth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _params(client_id, redirect, scope, challenge):
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": "st-app",
         "code_challenge": challenge, "code_challenge_method": "S256", "resource": config.PUBLIC_BASE_URL}
    if scope is not None:
        q["scope"] = scope
    return q


def _connect(client, person, client_id, scope="app mcp", port=53123, path="/callback"):
    """O fluxo do app: /oauth/authorize com a porta efêmera -> portal (login e consentimento) -> troca do código."""
    redirect = f"http://127.0.0.1:{port}{path}"
    verifier, challenge = _pkce()
    r = client.get("/oauth/authorize", params=_params(client_id, redirect, scope, challenge), follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/app/#/oauth?"), r.text
    params = dict(urllib.parse.parse_qsl(r.headers["location"].split("?", 1)[1]))
    client.cookies.clear()
    assert client.post("/api/auth/password", json={"username": person[0], "password": person[1]}).status_code == 200
    csrf = {"X-CSRF-Token": client.cookies.get(auth.CSRF_COOKIE)}
    info = client.get("/api/oauth/consent", params=params).json()
    r = client.post("/api/oauth/consent", headers=csrf, json={"params": params, "approve": True})
    assert r.status_code == 200, r.text
    back = urllib.parse.urlsplit(r.json()["redirect"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == redirect
    code = dict(urllib.parse.parse_qsl(back.query))["code"]
    client.cookies.clear()
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                            "client_id": client_id, "redirect_uri": redirect})
    assert tok.status_code == 200, tok.text
    return tok.json(), info


def _bearer(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _device(**kw):
    return {"device_id": str(uuid.uuid4()), "name": "NOTE-ANA", "os": "windows", "os_version": "11 Pro 24H2",
            "app_version": "0.1.0", "channel": "stable", **kw}


# ------------------------------------------------------------------ OAuth: loopback e escopos
def test_redirect_matches_loopback_any_port():
    m = oa.redirect_matches
    assert m("http://127.0.0.1:53123/callback", "http://127.0.0.1/callback")
    assert m("http://127.0.0.1:1/callback", "http://127.0.0.1:9999/callback")
    assert m("http://[::1]:4000/cb", "http://[::1]/cb") and m("http://localhost:4000/cb", "http://localhost/cb")
    assert not m("http://127.0.0.1:53123/other", "http://127.0.0.1/callback")      # caminho exato
    assert not m("https://127.0.0.1:53123/callback", "http://127.0.0.1/callback")  # esquema exato
    assert not m("http://localhost:53123/callback", "http://127.0.0.1/callback")   # host exato
    assert not m("http://127.0.0.1:53123/callback?x=1", "http://127.0.0.1/callback")
    assert not m("http://127.0.0.1:abc/callback", "http://127.0.0.1/callback")
    assert not m("http://evil@127.0.0.1:5/callback", "http://127.0.0.1/callback")
    assert not m("https://claude.ai:444/api/mcp/auth_callback", "https://claude.ai/api/mcp/auth_callback")  # fora do loopback, exato


def test_register_keeps_scope_and_software(client):
    c = _register(client)
    assert c["scope"] == "app mcp" and c["software_id"] == "hangar-desktop" and c["software_version"] == "0.1.0"
    assert _register(client, scope=None)["scope"] == "mcp"  # sem escopo: o MCP, como sempre
    assert _register(client, scope="claudeai mcp")["scope"] == "mcp"  # escopos desconhecidos são ignorados
    listed = {x["id"]: x for x in client.get("/api/oauth/clients", headers=ADMIN).json()}
    assert listed[c["client_id"]]["software_id"] == "hangar-desktop" and listed[c["client_id"]]["scope"] == "app mcp"
    md = client.get("/.well-known/oauth-authorization-server").json()
    assert md["scopes_supported"] == ["app", "mcp"]
    assert client.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"] == ["mcp"]


def test_app_flow_free_port_scopes_and_consent(client, person):
    c = _register(client)
    t, info = _connect(client, person, c["client_id"], port=61234)
    assert t["scope"] == "app mcp" and t["access_token"].startswith("aho_")
    assert [s["scope"] for s in info["scopes"]] == ["app", "mcp"] and all(s["description"] for s in info["scopes"])
    assert info["software_id"] == "hangar-desktop" and info["redirect_host"] == "127.0.0.1"
    assert client.get("/api/app/me", headers=_bearer(t)).status_code == 200
    assert client.post("/mcp", json=LIST, headers={**MCP_HDR, **_bearer(t)}).status_code == 200
    assert client.get("/api/agents", headers=_bearer(t)).status_code == 403  # fora de /mcp e /api/app, nada
    # o refresh mantém os escopos
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"],
                                          "client_id": c["client_id"]}).json()
    assert r["scope"] == "app mcp"
    assert client.get("/api/app/me", headers=_bearer(r)).status_code == 200
    audit = client.get("/api/audit", headers=ADMIN).json()
    assert any(a["action"] == "app.login" and a["actor"] == person[2]["email"] for a in audit)


def test_another_port_or_path_is_refused(client, person):
    c = _register(client)
    _, challenge = _pkce()
    r = client.get("/oauth/authorize", params=_params(c["client_id"], "http://127.0.0.1:5000/outro", "app", challenge),
                   follow_redirects=False)
    assert r.status_code == 400 and "text/html" in r.headers["content-type"]  # nunca redireciona para lá
    # a troca do código exige o mesmo redirect do pedido (com a porta)
    t, _ = _connect(client, person, c["client_id"], port=40001)
    assert t["access_token"]


def test_without_scope_is_mcp_and_mcp_token_cannot_use_app_api(client, person):
    c = _register(client)
    t, info = _connect(client, person, c["client_id"], scope=None)
    assert t["scope"] == "mcp" and [s["scope"] for s in info["scopes"]] == ["mcp"]
    assert client.post("/mcp", json=LIST, headers={**MCP_HDR, **_bearer(t)}).status_code == 200
    r = client.get("/api/app/me", headers=_bearer(t))
    assert r.status_code == 403 and "escopo app" in r.json()["detail"]
    assert client.post("/api/app/devices", headers=_bearer(t), json=_device()).status_code == 403


def test_old_style_client_only_gets_its_registered_scopes(client, person):
    cb = "https://claude.ai/api/mcp/auth_callback"
    c = _register(client, uris=(cb,), scope=None, name="Claude")
    _, challenge = _pkce()
    r = client.get("/oauth/authorize", params=_params(c["client_id"], cb, "app", challenge), follow_redirects=False)
    assert r.status_code == 302 and "error=invalid_scope" in r.headers["location"] and r.headers["location"].startswith(cb)
    # pedir tudo o que os metadados anunciam ("app mcp") não quebra o cliente: ele recebe só o mcp
    params = dict(urllib.parse.parse_qsl(client.get("/oauth/authorize", params=_params(c["client_id"], cb, "app mcp", challenge),
                                                    follow_redirects=False).headers["location"].split("?", 1)[1]))
    client.cookies.clear()
    client.post("/api/auth/password", json={"username": person[0], "password": person[1]})
    assert [x["scope"] for x in client.get("/api/oauth/consent", params=params).json()["scopes"]] == ["mcp"]
    client.cookies.clear()


def test_app_only_token_is_refused_on_mcp_and_admin(client, person):
    client.patch(f"/api/users/{person[2]['id']}", headers=ADMIN, json={"org_role": "admin"})  # nem sendo admin
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"], scope="app")
    assert t["scope"] == "app"
    assert client.get("/api/app/me", headers=_bearer(t)).json()["user"]["org_role"] == "admin"
    r = client.post("/mcp", json=LIST, headers={**MCP_HDR, **_bearer(t)})
    assert r.status_code == 403
    for path in ("/api/app/admin/overview", "/api/app/admin/devices"):
        r = client.get(path, headers=_bearer(t))
        assert r.status_code == 403 and "detail" in r.json()
    assert client.post("/api/app/admin/devices/1/revoke", headers=_bearer(t)).status_code == 403


# ------------------------------------------------------------------ /api/app
def test_me_and_design_tokens(client, person, db):
    team = client.post("/api/teams", headers=ADMIN, json={"slug": f"t-{person[0]}", "name": "Time App"}).json()
    client.post(f"/api/teams/{team['slug']}/members", headers=ADMIN, json={"email": person[2]["email"], "role": "consumer"})
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"])
    me = client.get("/api/app/me", headers=_bearer(t)).json()
    assert me["user"]["email"] == person[2]["email"] and me["user"]["name"] == "Pessoa App"
    assert set(me["user"]) == {"id", "email", "name", "avatar_url", "org_role", "teams"}
    assert [x for x in me["user"]["teams"] if x["slug"] == team["slug"]] == [{"id": team["id"], "slug": team["slug"],
                                                                            "name": "Time App"}]
    assert set(me["org"]) == {"id", "name", "slug", "brand"} and me["platform"] == {"version": config.VERSION, "features": {}}
    dt = client.get("/api/app/design-tokens", headers=_bearer(t)).json()
    assert me["design_tokens"]["version"] == dt["version"] and len(dt["version"]) == 12
    assert set(dt) == {"version", "light", "dark", "fonts", "radius"}
    assert set(dt["fonts"]) == {"ui", "sign", "mono"} and set(dt["radius"]) == {"r", "r_lg"}
    assert dt["light"]["mark"] == "#F4B400" and set(dt["light"]) == set(dt["dark"])
    assert client.get("/api/app/me").status_code == 401  # sem token
    assert client.get("/api/app/me").json() == {"detail": "unauthorized"}


def test_devices_register_heartbeat_and_admin(client, person):
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"])
    body = _device()
    r = client.post("/api/app/devices", headers=_bearer(t), json=body)
    assert r.status_code == 200, r.text
    d = r.json()["device"]
    assert set(d) == {"id", "device_id", "name", "os", "app_version", "status", "created_at", "last_seen_at"}
    assert d["status"] == "active" and d["device_id"] == body["device_id"]
    again = client.post("/api/app/devices", headers=_bearer(t), json={**body, "app_version": "0.1.1"}).json()["device"]
    assert again["id"] == d["id"] and again["app_version"] == "0.1.1"  # idempotente por pessoa + device_id
    hb = client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t), json={"app_version": "0.2.0"})
    assert hb.status_code == 200 and hb.json() == {"status": "active"}
    assert client.post("/api/app/devices/nao-existe-123/heartbeat", headers=_bearer(t), json={}).status_code == 404
    assert client.post("/api/app/devices", headers=_bearer(t), json=_device(device_id="x")).status_code == 400

    listed = [x for x in client.get("/api/app/admin/devices", headers=ADMIN).json() if x["id"] == d["id"]][0]
    assert listed["app_version"] == "0.2.0" and listed["user"]["email"] == person[2]["email"]
    assert listed["os_version"] == "11 Pro 24H2" and listed["channel"] == "stable" and listed["status"] == "active"
    assert {"id", "device_id", "user", "name", "os", "os_version", "app_version", "channel", "status", "created_at",
            "last_seen_at"} <= set(listed)
    ov = client.get("/api/app/admin/overview", headers=ADMIN).json()
    assert ov["devices_active"] >= 1 and ov["members_connected"] >= 1 and ov["platform_url"] == config.PUBLIC_BASE_URL
    assert any(v["version"] == "0.2.0" for v in ov["versions"]) and ov["last_seen_at"]
    audit = client.get("/api/audit", headers=ADMIN).json()
    assert sum(1 for a in audit if a["action"] == "app.device.register" and body["device_id"] in a["detail"]) == 1


def test_session_cannot_register_device(client, person):
    client.cookies.clear()
    client.post("/api/auth/password", json={"username": person[0], "password": person[1]})
    csrf = {"X-CSRF-Token": client.cookies.get(auth.CSRF_COOKIE)}
    assert client.get("/api/app/me").status_code == 200  # a sessão do portal lê o perfil
    assert client.post("/api/app/devices", headers=csrf, json=_device()).status_code == 403  # o dispositivo, só o app
    assert client.get("/api/app/admin/overview").status_code == 403  # e não é admin
    client.cookies.clear()


# ------------------------------------------------------------------ revogação
def test_admin_revoke_kills_grant_and_blocks_device(client, person):
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"])
    body = _device()
    d = client.post("/api/app/devices", headers=_bearer(t), json=body).json()["device"]
    r = client.post(f"/api/app/admin/devices/{d['id']}/revoke", headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"ok": True}
    hb = client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t), json={})
    assert hb.status_code == 200 and hb.json() == {"status": "revoked"}
    assert client.get("/api/app/me", headers=_bearer(t)).status_code == 401  # a autorização caiu junto
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"]}).status_code == 400
    listed = [x for x in client.get("/api/app/admin/devices", headers=ADMIN).json() if x["id"] == d["id"]][0]
    assert listed["status"] == "revoked" and listed["revoked_by"] == "admin-token"
    # um login novo não traz de volta um dispositivo revogado pelo admin
    t2, _ = _connect(client, person, c["client_id"])
    r = client.post("/api/app/devices", headers=_bearer(t2), json=body)
    assert r.status_code == 403 and "revogado" in r.json()["detail"]
    assert client.post("/api/app/devices", headers=_bearer(t2), json=_device()).status_code == 200  # outro computador, sim
    audit = client.get("/api/audit", headers=ADMIN).json()
    assert any(a["action"] == "app.device.revoke" and body["device_id"] in a["detail"] for a in audit)
    assert client.post("/api/app/admin/devices/999999/revoke", headers=ADMIN).status_code == 404


def test_logout_revokes_device_and_new_login_reactivates(client, person):
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"])
    body = _device()
    client.post("/api/app/devices", headers=_bearer(t), json=body)
    assert client.post("/oauth/revoke", data={"token": t["access_token"]}).status_code == 200  # "Desconectar" no app
    auth.clear_cache()
    hb = client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t), json={})
    assert hb.json() == {"status": "revoked"}
    listed = [x for x in client.get("/api/app/admin/devices", headers=ADMIN).json() if x["device_id"] == body["device_id"]][0]
    assert listed["status"] == "revoked" and listed["revoked_by"].startswith("sistema:")
    t2, _ = _connect(client, person, c["client_id"])
    d = client.post("/api/app/devices", headers=_bearer(t2), json=body).json()["device"]
    assert d["status"] == "active" and d["id"] == listed["id"]
    assert client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t2), json={}).json() == {"status": "active"}


def test_portal_revoke_of_grant_revokes_device(client, person):
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"])
    body = _device()
    client.post("/api/app/devices", headers=_bearer(t), json=body)
    grant = [g for g in client.get("/api/me/oauth", headers=ADMIN).json() if g["client_id"] == c["client_id"]][0]
    assert grant["scope"] == "app mcp"
    assert client.delete(f"/api/me/oauth/{grant['id']}", headers=ADMIN).status_code == 200
    ov_devices = {x["device_id"]: x for x in client.get("/api/app/admin/devices", headers=ADMIN).json()}
    assert ov_devices[body["device_id"]]["status"] == "revoked"
    assert client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t), json={}).json() == {"status": "revoked"}


def test_deactivated_user_revokes_devices(client, person):
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"])
    body = _device()
    client.post("/api/app/devices", headers=_bearer(t), json=body)
    client.patch(f"/api/users/{person[2]['id']}", headers=ADMIN, json={"active": False})
    assert client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t), json={}).json() == {"status": "revoked"}
    listed = {x["device_id"]: x for x in client.get("/api/app/admin/devices", headers=ADMIN).json()}
    assert listed[body["device_id"]]["status"] == "revoked"


def test_heartbeat_unknown_or_expired_token_is_401(client, person):
    r = client.post("/api/app/devices/abcdefgh-1234/heartbeat", headers={"Authorization": "Bearer aho_desconhecido"}, json={})
    assert r.status_code == 401 and "detail" in r.json()
    c = _register(client)
    t, _ = _connect(client, person, c["client_id"])
    body = _device()
    client.post("/api/app/devices", headers=_bearer(t), json=body)
    from app.db import SessionLocal
    from app.models import OAuthGrant
    from app.models import now as _now
    with SessionLocal() as s:  # o token de acesso venceu, mas a autorização vale: o app faz refresh
        g = s.query(OAuthGrant).filter(OAuthGrant.access_hash == auth.hash_key(t["access_token"])).one()
        g.access_expires_at = _now().replace(year=2000)
        s.commit()
    auth.clear_cache()
    assert client.post(f"/api/app/devices/{body['device_id']}/heartbeat", headers=_bearer(t), json={}).status_code == 401


# ------------------------------------------------------------------ design tokens x style.css
def _css_vars(block: str) -> dict:
    return dict(re.findall(r"--([\w-]+):\s*([^;]+?)\s*;", block + ";"))


def test_style_css_matches_design_tokens_json():
    css = open(os.path.join(STATIC, "style.css"), encoding="utf-8").read()
    tokens = json.load(open(os.path.join(STATIC, "design-tokens.json"), encoding="utf-8"))
    light = _css_vars(re.search(r"^:root\{(.*?)\}", css, re.S | re.M).group(1))
    dark = _css_vars(re.search(r"@media \(prefers-color-scheme:dark\)\{:root\{(.*?)\}\}", css, re.S).group(1))
    fonts = {k[2:]: v for k, v in light.items() if k.startswith("f-")}
    fonts = {k: re.sub(r"var\(--f-(\w+)\)", lambda m: fonts[m.group(1)], v) for k, v in fonts.items()}
    colors = {k: v for k, v in light.items() if not k.startswith("f-") and k not in ("r", "r-lg")}
    assert tokens["light"] == colors, "style.css (:root) e design-tokens.json (light) divergem"
    assert tokens["dark"] == {**colors, **dark}, "style.css (tema escuro) e design-tokens.json (dark) divergem"
    assert tokens["fonts"] == fonts
    assert tokens["radius"] == {"r": light["r"], "r_lg": light["r-lg"]}
    assert not set(dark) - set(colors), "o tema escuro tem um token que o claro não tem"


# ------------------------------------------------------------------ migração do zero
def test_migrations_apply_from_scratch(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from app import db as dbmod
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    cfg = dbmod._alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    try:
        ins = inspect(eng)
        assert {"id", "org_id", "user_id", "device_id", "grant_id", "status", "revoked_by"} <= \
            {c["name"] for c in ins.get_columns("app_devices")}
        assert {"scope", "software_id", "software_version"} <= {c["name"] for c in ins.get_columns("oauth_clients")}
        assert "scope" in {c["name"] for c in ins.get_columns("oauth_grants")}
        command.downgrade(cfg, "0051_agent_avatar")
        assert "app_devices" not in inspect(eng).get_table_names()
        command.upgrade(cfg, "head")
        assert "app_devices" in inspect(eng).get_table_names()
    finally:
        eng.dispose()
