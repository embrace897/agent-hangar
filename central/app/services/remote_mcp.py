"""MCPs remotos protegidos por OAuth (padrão de autorização do MCP): Activepieces, Notion, Linear, Atlassian…

Conectar (admin, uma vez): descobre o servidor de autorização pelo 401 do MCP (RFC 9728 → RFC 8414), registra a
central como cliente (DCR, RFC 7591), manda o navegador autorizar (authorization code + PKCE, `resource` da RFC
8707) e troca o código por tokens. Os tokens ficam criptografados no banco e são renovados sozinhos (refresh token,
com lock para dois agentes não gastarem o mesmo refresh ao mesmo tempo).

Uso: os agentes chamam `/internal/mcp-remote/<nome>` na central (token interno do agente), e a central repassa ao
MCP remoto com o access token válido — o agente nunca vê a credencial, e só agentes com esse MCP na spec passam."""
import base64
import hashlib
import re
import secrets
import time
import urllib.parse
from datetime import UTC, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, crypto, shared
from ..models import McpServer, RemoteMcp, now
from .access import Access, Forbidden
from .catalog import upsert_mcp
from .common import PlatformError, audit, iso

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,60}$")
HTTP = lambda: httpx.Client(timeout=20, follow_redirects=False)  # noqa: E731  (os testes trocam)
REFRESH_MARGIN_S = 90


def _admin(acc: Access):
    if not acc.p.is_admin:
        raise Forbidden("só admins conectam MCPs remotos")


def proxy_url(name: str) -> str:
    return f"{config.INTERNAL_BASE_URL}/internal/mcp-remote/{name}"


def redirect_uri() -> str:
    return f"{config.PUBLIC_BASE_URL}/api/remote-mcps/callback"


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def remote_dict(r: RemoteMcp) -> dict:
    return {"name": r.name, "url": r.url, "browser_base": r.browser_base, "description": r.description,
            "status": r.status, "expires_at": iso(_aware(r.expires_at)), "last_error": r.last_error,
            "connected_by": r.connected_by, "updated_at": iso(_aware(r.updated_at)), "catalog_mcp": r.name,
            "proxy_url": proxy_url(r.name), "scope": (r.oauth or {}).get("scope", ""),
            "has_refresh_token": bool(r.refresh_token)}


# ------------------------------------------------------------------ descoberta e registro
def _json(resp: httpx.Response, what: str) -> dict:
    if resp.status_code >= 400:
        raise PlatformError(f"{what}: HTTP {resp.status_code} {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError:
        raise PlatformError(f"{what}: resposta não é JSON") from None


def _same_origin(url: str, base: str) -> str:
    """Troca a origem de `url` pela de `base` (a central alcança o MCP por um endereço, o navegador por outro)."""
    if not base:
        return url
    u, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    return urllib.parse.urlunsplit((b.scheme, b.netloc, u.path, u.query, u.fragment))


def discover(url: str) -> dict:
    """401 do MCP -> metadados do recurso protegido -> metadados do servidor de autorização."""
    with HTTP() as http:
        r = http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                      headers={"Accept": "application/json, text/event-stream"})
        if r.status_code != 401:
            raise PlatformError(f"o MCP não pediu autenticação (HTTP {r.status_code}): cadastre-o como MCP comum")
        m = re.search(r'resource_metadata="([^"]+)"', r.headers.get("www-authenticate", ""))
        u = urllib.parse.urlsplit(url)
        origin = f"{u.scheme}://{u.netloc}"
        prm_url = m.group(1) if m else f"{origin}/.well-known/oauth-protected-resource{u.path}"
        # o servidor pode anunciar a própria origem sem a porta/host que a central usa: busca pelo host do MCP
        prm = _json(http.get(_same_origin(prm_url, origin)), "metadados do recurso")
        as_url = (prm.get("authorization_servers") or [origin])[0]
        meta = None
        for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            resp = http.get(_same_origin(as_url.rstrip("/") + path, origin))
            if resp.status_code == 200:
                meta = resp.json()
                break
        if not meta:
            raise PlatformError("servidor de autorização sem metadados (.well-known)")
    if "S256" not in (meta.get("code_challenge_methods_supported") or ["S256"]):
        raise PlatformError("o servidor de autorização não suporta PKCE S256")
    scopes = prm.get("scopes_supported") or meta.get("scopes_supported") or []
    return {"resource": prm.get("resource") or url, "authorization_endpoint": meta["authorization_endpoint"],
            "token_endpoint": meta["token_endpoint"], "registration_endpoint": meta.get("registration_endpoint", ""),
            "revocation_endpoint": meta.get("revocation_endpoint", ""),
            "scope": " ".join(s for s in scopes if s in ("mcp", "offline_access")) or " ".join(scopes[:4]),
            "origin": origin}


def _register(meta: dict) -> tuple[str, str]:
    if not meta.get("registration_endpoint"):
        raise PlatformError("o servidor não oferece registro dinâmico de cliente (DCR)")
    with HTTP() as http:
        body = _json(http.post(_same_origin(meta["registration_endpoint"], meta["origin"]), json={
            "client_name": "Agent Hangar", "redirect_uris": [redirect_uri()],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}), "registro do cliente (DCR)")
    return body["client_id"], body.get("client_secret", "")


# ------------------------------------------------------------------ conectar (admin)
def start(db: Session, acc: Access, name: str, url: str, description: str = "", browser_base: str = "") -> dict:
    """Cadastra (ou reconecta) e devolve a URL de autorização para o navegador do admin + o state assinado."""
    from .. import sso

    _admin(acc)
    name = (name or "").strip().lower()
    if not NAME.match(name):
        raise PlatformError("nome: 2 a 61 caracteres, minúsculas, números, ponto, _ ou -")
    if not re.match(r"^https?://", url or ""):
        raise PlatformError("url do MCP deve ser http(s)")
    other = db.scalar(select(McpServer).where(McpServer.name == name))
    if other and other.url != proxy_url(name) and other.auth != "oauth":
        raise PlatformError(f"já existe um MCP '{name}' no catálogo")
    meta = discover(url)
    r = db.scalar(select(RemoteMcp).where(RemoteMcp.name == name)) or RemoteMcp(name=name)
    reuse = r.id and r.url == url and (r.oauth or {}).get("client_id")
    client_id, client_secret = ((r.oauth["client_id"], crypto.decrypt(r.client_secret) if r.client_secret else "")
                                if reuse else _register(meta))
    r.url, r.description, r.browser_base = url, description or r.description or "", (browser_base or "").rstrip("/")
    r.oauth = {**meta, "client_id": client_id}
    r.client_secret = crypto.encrypt(client_secret) if client_secret else ""
    if r.status != "connected":
        r.status = "pending"
    db.add(r)
    db.commit()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri(), "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", "resource": meta["resource"]}
    if meta.get("scope"):
        params["scope"] = meta["scope"]
    authorize = _same_origin(meta["authorization_endpoint"], r.browser_base) + "?" + urllib.parse.urlencode(params)
    audit(db, acc.p.name, "remote_mcp.start", name, url)
    return {"authorize_url": authorize,
            "cookie": sso.sign_state({"s": state, "v": verifier, "n": name, "t": int(time.time())})}


def _save_tokens(r: RemoteMcp, body: dict):
    r.access_token = crypto.encrypt(body["access_token"])
    if body.get("refresh_token"):  # rotação: o servidor pode trocar o refresh a cada uso
        r.refresh_token = crypto.encrypt(body["refresh_token"])
    r.expires_at = now() + timedelta(seconds=int(body.get("expires_in") or 3600))
    r.status, r.last_error = "connected", ""


def finish(db: Session, acc: Access, code: str, state: str, cookie: str) -> RemoteMcp:
    from .. import sso

    _admin(acc)
    data = sso.read_state(cookie)
    if not secrets.compare_digest(data.get("s", ""), state or ""):
        raise PlatformError("state inválido — tente conectar de novo")
    r = db.scalar(select(RemoteMcp).where(RemoteMcp.name == data.get("n")))
    if not r or not code:
        raise PlatformError("conexão não encontrada ou sem código de autorização")
    o = r.oauth
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
            "client_id": o["client_id"], "code_verifier": data["v"], "resource": o["resource"]}
    if r.client_secret:
        form["client_secret"] = crypto.decrypt(r.client_secret)
    with HTTP() as http:
        body = _json(http.post(_same_origin(o["token_endpoint"], o["origin"]), data=form,
                               headers={"Accept": "application/json"}), "troca do código")
    _save_tokens(r, body)
    r.connected_by = acc.p.name
    db.commit()
    conn = db.scalar(select(McpServer).where(McpServer.name == r.name))
    if conn is not None and conn.auth == "oauth":  # conector: segue pela ponte (ferramentas aprovadas, status)
        from . import connectors
        connectors.oauth_done(db, r.name)
    else:
        upsert_mcp(db, r.name, proxy_url(r.name), r.description or f"MCP remoto (OAuth): {r.url}", acc.p.name, tool_prefix="")
    audit(db, acc.p.name, "remote_mcp.connected", r.name, r.url)
    return r


# ------------------------------------------------------------------ token válido (para o proxy)
def token(db: Session, name: str, force: bool = False) -> tuple[RemoteMcp, str]:
    # refresh tokens costumam ser rotativos: duas réplicas renovando juntas invalidariam uma à outra
    with shared.cluster_lock(f"remote-mcp:{name}"):
        db.expire_all()
        r = db.scalar(select(RemoteMcp).where(RemoteMcp.name == name))
        if not r or r.status == "pending" or not r.access_token:
            raise PlatformError(f"MCP remoto '{name}' não está conectado")
        fresh = r.expires_at and _aware(r.expires_at) - now() > timedelta(seconds=REFRESH_MARGIN_S)
        if fresh and not force:
            return r, crypto.decrypt(r.access_token)
        if not r.refresh_token:
            r.status, r.last_error = "error", "token expirado e sem refresh token: reconecte"
            db.commit()
            raise PlatformError(r.last_error)
        o = r.oauth
        form = {"grant_type": "refresh_token", "refresh_token": crypto.decrypt(r.refresh_token),
                "client_id": o["client_id"], "resource": o["resource"]}
        if r.client_secret:
            form["client_secret"] = crypto.decrypt(r.client_secret)
        try:
            with HTTP() as http:
                body = _json(http.post(_same_origin(o["token_endpoint"], o["origin"]), data=form,
                                       headers={"Accept": "application/json"}), "renovação do token")
        except PlatformError as e:
            r.status, r.last_error = "error", f"{e} — reconecte o MCP"
            db.commit()
            raise PlatformError(r.last_error) from None
        _save_tokens(r, body)
        db.commit()
        return r, crypto.decrypt(r.access_token)


def list_all(db: Session, acc: Access) -> list[dict]:
    _admin(acc)
    return [remote_dict(r) for r in db.scalars(select(RemoteMcp).order_by(RemoteMcp.name))]


def remove(db: Session, acc: Access, name: str):
    _admin(acc)
    r = db.scalar(select(RemoteMcp).where(RemoteMcp.name == name))
    if not r:
        raise PlatformError(f"MCP remoto '{name}' não existe")
    if r.refresh_token and (r.oauth or {}).get("revocation_endpoint"):
        try:  # melhor esforço: revoga no provedor
            with HTTP() as http:
                http.post(_same_origin(r.oauth["revocation_endpoint"], r.oauth["origin"]),
                          data={"token": crypto.decrypt(r.refresh_token), "client_id": r.oauth["client_id"]})
        except Exception:
            pass
    m = db.scalar(select(McpServer).where(McpServer.name == name))
    if m and m.url == proxy_url(name):
        db.delete(m)
    db.delete(r)
    db.commit()
    audit(db, acc.p.name, "remote_mcp.remove", name)

