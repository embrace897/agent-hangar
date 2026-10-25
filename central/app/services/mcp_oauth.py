"""OAuth 2.1 do MCP da plataforma: Claude.ai, ChatGPT e outros clientes web conectam só com a URL `<base>/mcp`.

Segue a especificação de autorização do MCP:
1. O cliente chama /mcp sem token e recebe 401 com `WWW-Authenticate: Bearer resource_metadata=…` (RFC 9728).
2. Lê os metadados do servidor de autorização (RFC 8414) e se registra sozinho (registro dinâmico, RFC 7591).
3. Manda a pessoa para /oauth/authorize (PKCE S256 obrigatório). A pessoa entra no portal (SSO ou conta local) e
   autoriza na tela de consentimento; o portal devolve o navegador ao redirect do app com um código de uso único.
4. O app troca código + verifier por um token de acesso curto (1 h) e um refresh token rotativo (30 dias,
   com detecção de reuso: um refresh antigo reapresentado derruba a autorização inteira).

O token age como a pessoa (times e papéis dela) e vale onde os escopos da autorização deixam: `mcp` no /mcp e `app`
nas rotas do app Hangar (/api/app, menos /api/app/admin). Pedido sem escopo vira `mcp` (Claude.ai, ChatGPT); um app só
recebe, do que pediu, os escopos que declarou no registro. A pessoa vê e revoga os apps em "Minhas chaves"; o admin vê e bloqueia apps
na página de SSO. Redirects só para hosts permitidos (OAUTH_REDIRECT_HOSTS), além de localhost — um app registrado por
terceiros não consegue mandar o código para outro lugar. Em loopback (127.0.0.1, [::1], localhost) a porta é livre
(RFC 8252 §7.3): o app nativo sobe o servidor de retorno numa porta qualquer.
"""
import base64
import hashlib
import re
import secrets
import time
import urllib.parse
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, config, shared, sso
from ..models import OAuthClient, OAuthGrant, User, now
from .access import Access, Forbidden
from .common import PlatformError, audit, iso

ACCESS_PREFIX, REFRESH_PREFIX = "aho_", "ahr_"
CODE_TTL_S = 300
SCOPE = "mcp"  # o padrão: pedido sem escopo (Claude.ai, ChatGPT…)
SCOPES = ("app", "mcp")
SCOPE_INFO = {"mcp": "Usar o MCP da plataforma com os seus times e papéis: listar, criar, testar e publicar os agentes "
                     "que você pode mexer.",
              "app": "Conectar o app Hangar deste computador: ver o seu perfil, a empresa e os times, registrar o "
                     "dispositivo e receber o visual da plataforma."}
LOOPBACK = {"localhost", "127.0.0.1", "[::1]", "::1"}
MAX_REGISTRATIONS_PER_HOUR = 30


class OAuthError(Exception):
    """Erro no formato do RFC 6749 ({"error": …, "error_description": …})."""

    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(description or error)
        self.error, self.description, self.status = error, description, status

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.description}


def base() -> str:
    return config.PUBLIC_BASE_URL


def resource() -> str:
    return f"{base()}/mcp"


def resource_metadata_url() -> str:
    return f"{base()}/.well-known/oauth-protected-resource/mcp"


def protected_resource_metadata() -> dict:  # o recurso /mcp: só o escopo dele
    return {"resource": resource(), "authorization_servers": [base()], "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"], "resource_name": "Agent Hangar"}


def server_metadata() -> dict:
    b = base()
    return {"issuer": b, "authorization_endpoint": f"{b}/oauth/authorize", "token_endpoint": f"{b}/oauth/token",
            "registration_endpoint": f"{b}/oauth/register", "revocation_endpoint": f"{b}/oauth/revoke",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "scopes_supported": list(SCOPES),
            "token_endpoint_auth_methods_supported": ["none"], "revocation_endpoint_auth_methods_supported": ["none"],
            "service_documentation": "https://github.com/valteresj2/agent-hangar/blob/main/docs/clients.md"}


def parse_scope(raw, default: str = SCOPE) -> str:
    """Os escopos conhecidos de um pedido, em ordem fixa ("app mcp"). Valores desconhecidos são ignorados (clientes que
    mandam os seus próprios); sem nenhum conhecido, o padrão."""
    asked = set(str(raw or "").split())
    return " ".join(s for s in SCOPES if s in asked) or default


def scopes_of(scope: str | None) -> set[str]:
    return set((scope or SCOPE).split())


# ------------------------------------------------------------------ registro dinâmico (RFC 7591)
def redirect_allowed(uri: str) -> bool:
    try:
        u = urllib.parse.urlsplit(uri)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.fragment or not host or u.username or u.password:
        return False
    if host in LOOPBACK:
        return u.scheme in ("http", "https")
    if u.scheme != "https":
        return False
    hosts = config.OAUTH_REDIRECT_HOSTS
    return "*" in hosts or any(host == h or host.endswith("." + h) for h in hosts)


def redirect_matches(asked: str, registered: str) -> bool:
    """O redirect do pedido é o registrado. Em loopback a porta não conta (RFC 8252 §7.3): esquema, host, caminho e
    query continuam exatos."""
    if asked == registered:
        return True
    try:
        a, r = urllib.parse.urlsplit(asked), urllib.parse.urlsplit(registered)
        if a.port is not None and not 0 < a.port < 65536:
            return False
    except ValueError:  # porta que não é número
        return False
    host = (r.hostname or "").lower()
    if host not in LOOPBACK or (a.hostname or "").lower() != host or a.username or a.password or a.fragment:
        return False
    return a.scheme == r.scheme and a.path == r.path and a.query == r.query


def redirect_registered(c: OAuthClient, asked: str) -> bool:
    return any(redirect_matches(asked, r) for r in c.redirect_uris or [])


def register(db: Session, body: dict, ip: str = "") -> dict:
    if not config.OAUTH_ENABLED:
        raise OAuthError("access_denied", "OAuth do MCP desligado nesta instalação (OAUTH_ENABLED=0)", 403)
    if shared.hits(f"oauth-dcr:{ip}", 3600, add=True) > MAX_REGISTRATIONS_PER_HOUR:
        raise OAuthError("slow_down", "registros demais deste endereço — tente mais tarde", 429)
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > 5 or not all(isinstance(u, str) for u in uris):
        raise OAuthError("invalid_redirect_uri", "informe de 1 a 5 redirect_uris")
    bad = [u for u in uris if not redirect_allowed(u)]
    if bad:
        raise OAuthError("invalid_redirect_uri",
                         f"redirect não permitido nesta empresa: {bad[0][:200]} (o admin libera hosts em OAUTH_REDIRECT_HOSTS)")
    method = body.get("token_endpoint_auth_method") or "none"
    if method != "none":
        raise OAuthError("invalid_client_metadata", "só clientes públicos com PKCE (token_endpoint_auth_method=none)")
    for g in body.get("grant_types") or ["authorization_code"]:
        if g not in ("authorization_code", "refresh_token"):
            raise OAuthError("invalid_client_metadata", f"grant_type não suportado: {g}")
    name = _clean(body.get("client_name"), 120) or (urllib.parse.urlsplit(uris[0]).hostname or "app")
    scope = parse_scope(body.get("scope"))
    software_id, software_version = _clean(body.get("software_id"), 120), _clean(body.get("software_version"), 60)
    c = OAuthClient(id="mcp_" + secrets.token_urlsafe(18), name=name, redirect_uris=uris, scope=scope,
                    software_id=software_id, software_version=software_version)
    db.add(c)
    db.commit()
    soft = f" · {software_id} {software_version}".rstrip() if software_id else ""
    audit(db, f"oauth:{name}", "oauth.register", c.id, f"{', '.join(uris)} · {scope}{soft}"[:500])
    out = {"client_id": c.id, "client_id_issued_at": int(time.time()), "client_name": name, "redirect_uris": uris,
           "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
           "token_endpoint_auth_method": "none", "scope": scope}
    if software_id:
        out.update(software_id=software_id, software_version=software_version)
    return out


def _clean(value, size: int) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", str(value or ""))[:size].strip()


# ------------------------------------------------------------------ autorização (tela de consentimento no portal)
def _client(db: Session, client_id: str) -> OAuthClient:
    c = db.get(OAuthClient, client_id or "")
    if not c or c.revoked_at is not None:
        raise OAuthError("invalid_client", "app desconhecido ou bloqueado pelo admin — reconecte pelo app")
    return c


def check_request(db: Session, q: dict) -> tuple[OAuthClient, dict]:
    """Valida o pedido de /oauth/authorize. Erros de app/redirect nunca redirecionam (evita redirecionamento aberto)."""
    if not config.OAUTH_ENABLED:
        raise OAuthError("access_denied", "OAuth do MCP desligado nesta instalação", 403)
    c = _client(db, q.get("client_id", ""))
    redirect = q.get("redirect_uri") or (c.redirect_uris[0] if len(c.redirect_uris) == 1 else "")
    if not redirect_registered(c, redirect):
        raise OAuthError("invalid_request", "redirect_uri não registrado para este app")
    clean = {"client_id": c.id, "redirect_uri": redirect, "state": (q.get("state") or "")[:500],
             "code_challenge": q.get("code_challenge") or "", "resource": q.get("resource") or resource(),
             "scope": parse_scope(q.get("scope"))}
    if q.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "só response_type=code")
    if q.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", clean["code_challenge"]):
        raise OAuthError("invalid_request", "PKCE obrigatório (code_challenge com S256)")
    if clean["resource"].rstrip("/") not in (resource(), base()):
        raise OAuthError("invalid_target", f"resource inválido: use {resource()}")
    # só os escopos que o app declarou no registro (RFC 6749 §3.3: o servidor pode conceder menos que o pedido) —
    # um cliente MCP que pede tudo o que os metadados anunciam continua conectando, com `mcp`
    granted = scopes_of(clean["scope"]) & scopes_of(c.scope)
    if not granted:
        raise OAuthError("invalid_scope", f"escopo não registrado para este app: {clean['scope']}")
    clean["scope"] = " ".join(s for s in SCOPES if s in granted)
    return c, clean


def redirect_with(redirect_uri: str, params: dict) -> str:
    sep = "&" if urllib.parse.urlsplit(redirect_uri).query else "?"
    return redirect_uri + sep + urllib.parse.urlencode({k: v for k, v in params.items() if v})


def consent_info(db: Session, acc: Access, q: dict) -> dict:
    c, clean = check_request(db, q)
    who = db.get(User, acc.p.user_id) if acc.p.user_id else None
    return {"client_name": c.name, "client_id": c.id, "redirect_host": urllib.parse.urlsplit(clean["redirect_uri"]).hostname,
            "user": {"name": who.name, "email": who.email} if who else None, "resource": clean["resource"],
            "software_id": c.software_id or None, "software_version": c.software_version or None,
            "scopes": [{"scope": s, "description": SCOPE_INFO[s]} for s in SCOPES if s in scopes_of(clean["scope"])]}


def authorize(db: Session, acc: Access, q: dict, approve: bool) -> dict:
    c, clean = check_request(db, q)
    if not approve:
        return {"redirect": redirect_with(clean["redirect_uri"], {"error": "access_denied", "state": clean["state"],
                                                                   "iss": base()})}
    if not acc.p.is_user or not acc.p.user_id or acc.p.via != "session":
        raise Forbidden("entre com a sua conta (SSO ou usuário e senha) para autorizar o app")
    code = sso.sign_state({"k": "mcp-oauth", "u": acc.p.user_id, "cl": c.id, "r": clean["redirect_uri"],
                           "c": clean["code_challenge"], "s": clean["scope"], "j": secrets.token_urlsafe(12),
                           "t": int(time.time())})
    audit(db, acc.p.name, "oauth.authorize", c.name, f"{c.id} · {clean['scope']}")
    return {"redirect": redirect_with(clean["redirect_uri"], {"code": code, "state": clean["state"], "iss": base()})}


# ------------------------------------------------------------------ tokens
def _issue(db: Session, grant: OAuthGrant | None, client: OAuthClient, user_id: int, scope: str = SCOPE) -> dict:
    access, refresh = ACCESS_PREFIX + secrets.token_urlsafe(32), REFRESH_PREFIX + secrets.token_urlsafe(40)
    t = now()
    if grant is None:
        grant = OAuthGrant(client_id=client.id, user_id=user_id, previous_refresh_hash="", scope=scope)
        db.add(grant)
    else:
        grant.previous_refresh_hash = grant.refresh_hash
    grant.access_hash, grant.refresh_hash = auth.hash_key(access), auth.hash_key(refresh)
    grant.access_expires_at = t + timedelta(seconds=config.OAUTH_ACCESS_TTL_S)
    grant.refresh_expires_at = t + timedelta(days=config.OAUTH_REFRESH_TTL_DAYS)
    db.commit()
    return {"access_token": access, "token_type": "Bearer", "expires_in": config.OAUTH_ACCESS_TTL_S,
            "refresh_token": refresh, "scope": grant.scope or SCOPE}


def _active_user(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if not u or not u.active:
        raise OAuthError("invalid_grant", "usuário inativo")
    return u


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def token(db: Session, form: dict) -> dict:
    if not config.OAUTH_ENABLED:
        raise OAuthError("access_denied", "OAuth do MCP desligado nesta instalação", 403)
    grant_type = form.get("grant_type")
    if grant_type == "authorization_code":
        try:
            data = sso.read_state(form.get("code") or "", max_age=CODE_TTL_S)
        except sso.AuthError:
            raise OAuthError("invalid_grant", "código inválido ou expirado") from None
        if data.get("k") != "mcp-oauth":
            raise OAuthError("invalid_grant", "código inválido")
        c = _client(db, form.get("client_id") or data["cl"])
        if c.id != data["cl"] or (form.get("redirect_uri") or data["r"]) != data["r"]:  # o do pedido, com a porta
            raise OAuthError("invalid_grant", "o código não é deste app ou deste redirect_uri")
        verifier = form.get("code_verifier") or ""
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if not secrets.compare_digest(expected, data.get("c", "")):
            raise OAuthError("invalid_grant", "verificação PKCE falhou")
        if not shared.take_once(f"oauth-code:{data['j']}", CODE_TTL_S + 60):
            raise OAuthError("invalid_grant", "este código já foi usado")
        u = _active_user(db, data["u"])
        scope = data.get("s") or SCOPE  # códigos assinados antes dos escopos: mcp
        out = _issue(db, None, c, u.id, scope)
        audit(db, u.email, "oauth.token", c.name, f"{c.id} · {scope}")
        if "app" in scopes_of(scope):
            audit(db, u.email, "app.login", c.name, " ".join(x for x in (c.software_id, c.software_version) if x) or c.id)
        return out
    if grant_type == "refresh_token":
        h = auth.hash_key(form.get("refresh_token") or "")
        g = db.scalar(select(OAuthGrant).where(OAuthGrant.refresh_hash == h))
        if g is None:
            old = db.scalar(select(OAuthGrant).where(OAuthGrant.previous_refresh_hash == h,
                                                     OAuthGrant.revoked_at.is_(None))) if form.get("refresh_token") else None
            if old is not None:  # refresh já trocado sendo reapresentado: token vazou — derruba a autorização
                old.revoked_at = now()
                db.commit()
                auth.clear_cache()
                audit(db, f"oauth:{old.client_id}", "oauth.refresh_reuse", str(old.user_id), f"autorização #{old.id} revogada")
            raise OAuthError("invalid_grant", "refresh token inválido")
        if g.revoked_at is not None or _aware(g.refresh_expires_at) < now():
            raise OAuthError("invalid_grant", "autorização revogada ou expirada — conecte de novo")
        c = _client(db, g.client_id)
        if form.get("client_id") and form["client_id"] != c.id:
            raise OAuthError("invalid_grant", "o refresh token não é deste app")
        _active_user(db, g.user_id)
        auth.clear_cache()
        return _issue(db, g, c, g.user_id)
    raise OAuthError("unsupported_grant_type", "use authorization_code ou refresh_token")


def revoke_token(db: Session, raw: str):
    """RFC 7009: sempre responde 200; revoga a autorização dona do token (acesso ou refresh)."""
    h = auth.hash_key(raw or "")
    g = db.scalar(select(OAuthGrant).where((OAuthGrant.access_hash == h) | (OAuthGrant.refresh_hash == h)))
    if g and g.revoked_at is None:
        g.revoked_at = now()
        db.commit()
        auth.clear_cache()


# ------------------------------------------------------------------ gestão: a pessoa e o admin
def _grant_dict(db: Session, g: OAuthGrant) -> dict:
    c = db.get(OAuthClient, g.client_id)
    u = db.get(User, g.user_id)
    return {"id": g.id, "client_id": g.client_id, "client_name": c.name if c else g.client_id, "scope": g.scope or SCOPE,
            "user": u.email if u else str(g.user_id), "created_at": iso(g.created_at), "last_used_at": iso(g.last_used_at),
            "expires_at": iso(g.refresh_expires_at), "revoked": g.revoked_at is not None or _aware(g.refresh_expires_at) < now()}


def my_grants(db: Session, acc: Access) -> list[dict]:
    q = select(OAuthGrant).order_by(OAuthGrant.id.desc())
    if not acc.p.is_admin:
        if not acc.p.user_id:
            return []
        q = q.where(OAuthGrant.user_id == acc.p.user_id)
    return [_grant_dict(db, g) for g in db.scalars(q.limit(200))]


def revoke_grant(db: Session, acc: Access, grant_id: int):
    g = db.get(OAuthGrant, grant_id)
    if not g or (g.user_id != acc.p.user_id and not acc.p.is_admin):
        raise PlatformError("autorização não encontrada")
    if g.revoked_at is None:
        g.revoked_at = now()
        db.commit()
        auth.clear_cache()
    audit(db, acc.p.name, "oauth.revoke", g.client_id, f"autorização #{g.id}")


def clients(db: Session, acc: Access) -> list[dict]:
    if not acc.p.is_admin:
        raise Forbidden("só admins")
    out = []
    for c in db.scalars(select(OAuthClient).order_by(OAuthClient.created_at.desc()).limit(200)):
        active = db.scalars(select(OAuthGrant).where(OAuthGrant.client_id == c.id, OAuthGrant.revoked_at.is_(None))).all()
        out.append({"id": c.id, "name": c.name, "redirect_uris": c.redirect_uris, "created_at": iso(c.created_at),
                    "blocked": c.revoked_at is not None, "scope": c.scope or SCOPE,
                    "software_id": c.software_id or None, "software_version": c.software_version or None,
                    "active_grants": sum(1 for g in active if _aware(g.refresh_expires_at) > now())})
    return out


def block_client(db: Session, acc: Access, client_id: str):
    if not acc.p.is_admin:
        raise Forbidden("só admins")
    c = db.get(OAuthClient, client_id)
    if not c:
        raise PlatformError("app não encontrado")
    t = now()
    c.revoked_at = c.revoked_at or t
    for g in db.scalars(select(OAuthGrant).where(OAuthGrant.client_id == c.id, OAuthGrant.revoked_at.is_(None))):
        g.revoked_at = t
    db.commit()
    auth.clear_cache()
    audit(db, acc.p.name, "oauth.block", c.name, c.id)
