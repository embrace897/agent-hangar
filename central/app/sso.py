"""Login OAuth2 (authorization code + PKCE): Google, Microsoft Entra ID, GitHub e um provedor OAuth2 genérico
(Keycloak, Okta, Auth0… — e SAML, com o Keycloak fazendo a ponte).

Fluxo: /api/auth/login/<provedor> redireciona ao provedor com `state` + PKCE (guardados num cookie assinado);
/api/auth/callback/<provedor> troca o code pelo access token, lê o perfil na API do provedor (userinfo do Google,
Microsoft Graph, API do GitHub), aplica as restrições (domínio, tenant, organização do GitHub), cria o usuário
no primeiro login, sincroniza os times pelos grupos mapeados e abre a sessão (cookie HttpOnly).

Vincular a um usuário existente pelo e-mail só acontece quando o provedor garante o e-mail (verificado no
Google/GitHub, tenant fixo no Entra ID); do contrário um provedor mal configurado poderia assumir contas.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, config, crypto
from .models import GroupMapping, SsoProvider, TeamMember, User, now
from .services.access import reconcile_keys, user_by_email
from .services.common import PlatformError, audit

PROVIDERS = {
    "google": {"label": "Google",
               "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
               "token_url": "https://oauth2.googleapis.com/token",
               "scopes": "https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile"},
    "microsoft": {"label": "Microsoft Entra ID",
                  "authorize_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
                  "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                  "scopes": "https://graph.microsoft.com/User.Read"},
    "github": {"label": "GitHub",
               "authorize_url": "https://github.com/login/oauth/authorize",
               "token_url": "https://github.com/login/oauth/access_token",
               "scopes": "read:user user:email read:org"},
    "oauth2": {"label": "OAuth2", "authorize_url": "", "token_url": "", "scopes": ""},
}
SETTING_KEYS = ("tenant", "allowed_domains", "orgs", "fetch_groups", "label", "authorize_url", "token_url",
                "userinfo_url", "scopes", "groups_field", "trust_email")
MULTI_TENANT = {"common", "organizations", "consumers"}
STATE_COOKIE = "hangar_oauth"

# Tests trocam por um cliente com MockTransport.
HTTP = lambda: httpx.Client(timeout=15)  # noqa: E731


class AuthError(PlatformError):
    """Login recusado (mensagem mostrada ao usuário na tela de login)."""


@dataclass
class Identity:
    subject: str
    email: str
    name: str = ""
    avatar: str = ""
    email_verified: bool = False
    domain: str = ""
    groups: list = field(default_factory=list)


# ------------------------------------------------------------------ configuração (ambiente + banco)
def _env_list(v: str) -> list[str]:
    return [x.strip().lower() for x in (v or "").split(",") if x.strip()]


def _from_env(provider: str) -> dict:
    pre = f"OAUTH_{provider.upper()}_"
    g = lambda k, d="": os.environ.get(pre + k, d)  # noqa: E731
    conf = {"client_id": g("CLIENT_ID"), "client_secret": g("CLIENT_SECRET"), "enabled": bool(g("CLIENT_ID")),
            "tenant": g("TENANT"), "allowed_domains": _env_list(g("ALLOWED_DOMAINS")), "orgs": _env_list(g("ORGS")),
            "fetch_groups": g("FETCH_GROUPS") == "1", "label": g("LABEL"), "authorize_url": g("AUTHORIZE_URL"),
            "token_url": g("TOKEN_URL"), "userinfo_url": g("USERINFO_URL"), "scopes": g("SCOPES"),
            "groups_field": g("GROUPS_FIELD", "groups"), "trust_email": g("TRUST_EMAIL") == "1"}
    return conf


def provider_config(db: Session, provider: str) -> dict:
    if provider not in PROVIDERS:
        raise AuthError(f"provedor desconhecido: {provider}")
    conf = {**PROVIDERS[provider], **{k: v for k, v in _from_env(provider).items() if v not in ("", [], None)}}
    conf.setdefault("enabled", False)
    row = db.scalar(select(SsoProvider).where(SsoProvider.provider == provider))
    if row:
        conf["enabled"] = row.enabled
        if row.client_id:
            conf["client_id"] = row.client_id
        if row.client_secret:
            conf["client_secret"] = crypto.decrypt(row.client_secret)
        conf.update({k: v for k, v in (row.settings or {}).items() if k in SETTING_KEYS and v not in ("", None)})
    conf["label"] = conf.get("label") or PROVIDERS[provider]["label"]
    conf["groups_field"] = conf.get("groups_field") or "groups"
    conf["provider"] = provider
    conf["ready"] = bool(conf.get("enabled") and conf.get("client_id") and conf.get("client_secret")
                         and (provider != "microsoft" or conf.get("tenant"))
                         and (provider != "oauth2" or (conf.get("authorize_url") and conf.get("token_url")
                                                       and conf.get("userinfo_url"))))
    return conf


def public_providers(db: Session) -> list[dict]:
    return [{"id": p, "label": c["label"]} for p in PROVIDERS if (c := provider_config(db, p))["ready"]]


def redirect_uri(provider: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/api/auth/callback/{provider}"


def admin_view(db: Session) -> list[dict]:
    out = []
    for p in PROVIDERS:
        c = provider_config(db, p)
        out.append({"id": p, "label": c["label"], "enabled": bool(c.get("enabled")), "ready": c["ready"],
                    "client_id": c.get("client_id", ""), "has_secret": bool(c.get("client_secret")),
                    "redirect_uri": redirect_uri(p),
                    "settings": {k: c.get(k) for k in SETTING_KEYS if k in c and k != "label"} | {"label": c["label"]}})
    return out


def save_provider(db: Session, provider: str, enabled: bool, client_id: str, client_secret: str, settings: dict,
                  actor: str) -> dict:
    if provider not in PROVIDERS:
        raise PlatformError(f"provedor desconhecido: {provider}")
    clean = {}
    for k, v in (settings or {}).items():
        if k not in SETTING_KEYS:
            raise PlatformError(f"configuração desconhecida: {k}")
        if k in ("allowed_domains", "orgs") and isinstance(v, str):
            v = _env_list(v)
        clean[k] = v
    if provider == "microsoft" and (clean.get("tenant") or "").lower() in MULTI_TENANT:
        raise PlatformError("use o tenant da empresa (ID ou domínio), não common/organizations/consumers")
    row = db.scalar(select(SsoProvider).where(SsoProvider.provider == provider))
    if not row:
        row = SsoProvider(provider=provider, settings={})
        db.add(row)
    row.enabled, row.client_id, row.settings = enabled, client_id.strip(), clean
    if client_secret:
        row.client_secret = crypto.encrypt(client_secret)
    db.commit()
    audit(db, actor, "sso.provider", provider, f"enabled={enabled} settings={sorted(clean)}")
    return next(x for x in admin_view(db) if x["id"] == provider)


# ------------------------------------------------------------------ state assinado + PKCE
def _state_key() -> bytes:
    return hmac.new(config.INTERNAL_SECRET.encode(), b"oauth-state", hashlib.sha256).digest()


def sign_state(data: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    sig = hmac.new(_state_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_state(raw: str, max_age=600) -> dict:
    try:
        body, sig = raw.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(_state_key(), body.encode(), hashlib.sha256).hexdigest()):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        raise AuthError("login expirado ou inválido — tente de novo") from None
    if time.time() - data.get("t", 0) > max_age:
        raise AuthError("login expirado — tente de novo")
    return data


def safe_next(nxt: str | None) -> str:
    """Só volta para dentro da UI (nada de redirecionamento aberto)."""
    return nxt if nxt and nxt.startswith("#/") and "//" not in nxt else "#/"


def start(db: Session, provider: str, nxt: str | None) -> tuple[str, str]:
    """Devolve (URL do provedor, valor do cookie de state)."""
    c = provider_config(db, provider)
    if not c["ready"]:
        raise AuthError(f"login com {c['label']} não está configurado")
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    scopes = c["scopes"]
    if provider == "microsoft" and wants_groups(db, c):
        scopes += " https://graph.microsoft.com/GroupMember.Read.All"
    params = {"client_id": c["client_id"], "redirect_uri": redirect_uri(provider), "response_type": "code",
              "scope": scopes, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "google":
        params["prompt"] = "select_account"
        if len(c.get("allowed_domains") or []) == 1:
            params["hd"] = c["allowed_domains"][0]  # só sugere a conta certa; a checagem é no callback
    if provider == "microsoft":
        params["response_mode"] = "query"
    url = c["authorize_url"].format(tenant=c.get("tenant", "")) + "?" + urllib.parse.urlencode(params)
    return url, sign_state({"s": state, "v": verifier, "p": provider, "n": safe_next(nxt), "t": int(time.time())})


def wants_groups(db: Session, c: dict) -> bool:
    return bool(c.get("fetch_groups")) or bool(db.scalar(select(GroupMapping.id).where(
        GroupMapping.provider == c["provider"]).limit(1)))


# ------------------------------------------------------------------ callback
def finish(db: Session, provider: str, code: str, state: str, cookie_state: str) -> tuple[User, str]:
    """Troca o code, lê o perfil, aplica as regras e devolve (usuário, next)."""
    data = read_state(cookie_state)
    if data.get("p") != provider or not hmac.compare_digest(data.get("s", ""), state or ""):
        raise AuthError("state inválido — tente de novo")
    if not code:
        raise AuthError("o provedor não devolveu o código de autorização")
    c = provider_config(db, provider)
    if not c["ready"]:
        raise AuthError("provedor desativado")
    with HTTP() as http:
        tok = http.post(c["token_url"].format(tenant=c.get("tenant", "")), headers={"Accept": "application/json"},
                        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider),
                              "client_id": c["client_id"], "client_secret": c["client_secret"],
                              "code_verifier": data["v"]})
        body = tok.json() if tok.headers.get("content-type", "").startswith("application/json") else {}
        access = body.get("access_token")
        if tok.status_code >= 400 or not access:
            raise AuthError(f"o provedor recusou o código ({body.get('error', tok.status_code)})")
        ident = FETCHERS[provider](http, access, c, wants_groups(db, c))
    return login_identity(db, provider, ident, c), data.get("n", "#/")


def _get(http, url, token, **kw) -> dict | list:
    r = http.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json", **kw.pop("headers", {})},
                 **kw)
    if r.status_code >= 400:
        raise AuthError(f"falha ao ler o perfil no provedor ({r.status_code})")
    return r.json()


def _google(http, token, c, groups) -> Identity:
    u = _get(http, "https://www.googleapis.com/oauth2/v3/userinfo", token)
    email = (u.get("email") or "").lower()
    return Identity(subject=str(u["sub"]), email=email, name=u.get("name", ""), avatar=u.get("picture", ""),
                    email_verified=bool(u.get("email_verified")), domain=(u.get("hd") or "").lower())


def _microsoft(http, token, c, groups) -> Identity:
    u = _get(http, "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName", token)
    email = (u.get("mail") or u.get("userPrincipalName") or "").lower()
    names = []
    if groups:
        url = "https://graph.microsoft.com/v1.0/me/memberOf/microsoft.graph.group?$select=id,displayName&$top=999"
        while url:
            page = _get(http, url, token)
            for g in page.get("value", []):
                names += [g.get("id", ""), g.get("displayName", "")]
            url = page.get("@odata.nextLink")
    fixed_tenant = (c.get("tenant") or "").lower() not in MULTI_TENANT
    return Identity(subject=str(u["id"]), email=email, name=u.get("displayName", ""), email_verified=fixed_tenant,
                    groups=[n for n in names if n])


def _github(http, token, c, groups) -> Identity:
    gh = {"headers": {"Accept": "application/vnd.github+json"}}
    u = _get(http, "https://api.github.com/user", token, **gh)
    emails = _get(http, "https://api.github.com/user/emails", token, **gh)
    primary = next((e for e in emails if e.get("primary") and e.get("verified")), None) or \
        next((e for e in emails if e.get("verified")), None)
    if not primary:
        raise AuthError("sua conta do GitHub não tem e-mail verificado")
    orgs = c.get("orgs") or []
    member_of = []
    for org in orgs:
        r = http.get(f"https://api.github.com/user/memberships/orgs/{org}",
                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        if r.status_code == 200 and r.json().get("state") == "active":
            member_of.append(org)
    if orgs and not member_of:
        raise AuthError(f"é preciso ser membro da organização do GitHub: {', '.join(orgs)}")
    teams = []
    if groups:
        page = 1
        while True:
            batch = _get(http, f"https://api.github.com/user/teams?per_page=100&page={page}", token, **gh)
            teams += [f"{t['organization']['login']}/{t['slug']}".lower() for t in batch]
            if len(batch) < 100:
                break
            page += 1
    return Identity(subject=str(u["id"]), email=primary["email"].lower(), name=u.get("name") or u.get("login", ""),
                    avatar=u.get("avatar_url", ""), email_verified=True, groups=teams)


def _generic(http, token, c, groups) -> Identity:
    u = _get(http, c["userinfo_url"], token)
    g = u.get(c.get("groups_field") or "groups") or []
    verified = bool(u.get("email_verified")) or bool(c.get("trust_email"))
    return Identity(subject=str(u.get("sub") or u.get("id")), email=(u.get("email") or "").lower(),
                    name=u.get("name") or u.get("preferred_username", ""), avatar=u.get("picture", ""),
                    email_verified=verified, groups=[str(x) for x in (g if isinstance(g, list) else [g])])


FETCHERS = {"google": _google, "microsoft": _microsoft, "github": _github, "oauth2": _generic}


def login_identity(db: Session, provider: str, ident: Identity, c: dict) -> User:
    if not ident.email or "@" not in ident.email:
        raise AuthError("o provedor não informou um e-mail")
    domains = [d.lower() for d in c.get("allowed_domains") or []]
    email_domain = ident.email.rsplit("@", 1)[1]
    if domains and email_domain not in domains and ident.domain not in domains:
        raise AuthError(f"o domínio {email_domain} não é permitido")
    if not ident.email_verified:
        raise AuthError("o provedor não garante este e-mail (verificado/tenant fixo) — login recusado")
    u = db.scalar(select(User).where(User.provider == provider, User.subject == ident.subject))
    u = u or user_by_email(db, ident.email)  # pré-cadastro, SCIM ou outro provedor com o mesmo e-mail verificado
    created = u is None
    if created:
        from .services.orgs import org_for_email
        u = User(email=ident.email, name=ident.name or ident.email.split("@")[0], active=True, org_role="member",
                 org_id=org_for_email(db, ident.email))  # a empresa dona do domínio (multiempresa)
        db.add(u)
    if not u.active:
        raise AuthError("usuário desativado — fale com um administrador")
    if not u.provider:
        u.provider, u.subject = provider, ident.subject
    u.name = ident.name or u.name
    u.avatar_url = ident.avatar or u.avatar_url
    u.last_login_at = now()
    if ident.email in config.BOOTSTRAP_ADMIN_EMAILS and u.org_role != "admin":
        u.org_role = "admin"
        audit(db, "sistema", "user.bootstrap_admin", ident.email)
    db.commit()
    sync_groups(db, u, f"sso:{provider}", provider, ident.groups)
    audit(db, u.email, "auth.login", provider, "primeiro login" if created else "")
    auth.clear_cache()
    return u


def sync_groups(db: Session, u: User, source: str, provider: str, groups: list[str]):
    """Times vindos de grupos mapeados. Só mexe em associações que a própria sincronização criou (source);
    quem foi adicionado à mão continua como está."""
    wanted_groups = {g.lower() for g in groups or []}
    desired: dict[int, str] = {}
    rank = {"consumer": 1, "developer": 2, "maintainer": 3}
    for m in db.scalars(select(GroupMapping).where(GroupMapping.provider == provider)):
        if m.external_group.lower() in wanted_groups and rank[m.role] > rank.get(desired.get(m.team_id, ""), 0):
            desired[m.team_id] = m.role
    changed = False
    current = {m.team_id: m for m in db.scalars(select(TeamMember).where(TeamMember.user_id == u.id))}
    for team_id, m in current.items():
        if m.source == source and team_id not in desired:
            db.delete(m)
            changed = True
        elif m.source == source and m.role != desired[team_id]:
            m.role = desired[team_id]
            changed = True
    for team_id, role in desired.items():
        if team_id not in current:
            db.add(TeamMember(team_id=team_id, user_id=u.id, role=role, source=source))
            changed = True
    if changed:
        db.commit()
        reconcile_keys(db, [u.id], "sso")


# ------------------------------------------------------------------ mapeamento de grupos
def mappings(db: Session) -> list[dict]:
    from .models import Team
    teams = {t.id: t for t in db.scalars(select(Team))}
    return [{"id": m.id, "provider": m.provider, "external_group": m.external_group, "team_id": m.team_id,
             "team": teams[m.team_id].slug if m.team_id in teams else None, "role": m.role}
            for m in db.scalars(select(GroupMapping).order_by(GroupMapping.provider, GroupMapping.external_group))]


def add_mapping(db: Session, provider: str, external_group: str, team_id: int, role: str, actor: str) -> dict:
    if provider not in (*PROVIDERS, "scim"):
        raise PlatformError(f"provedor inválido: {provider}")
    if role not in ("maintainer", "developer", "consumer"):
        raise PlatformError("papel inválido")
    if not external_group.strip():
        raise PlatformError("informe o grupo externo")
    m = GroupMapping(provider=provider, external_group=external_group.strip(), team_id=team_id, role=role)
    db.add(m)
    db.commit()
    audit(db, actor, "sso.mapping.add", provider, f"{external_group} -> time #{team_id} ({role})")
    return next(x for x in mappings(db) if x["id"] == m.id)


def delete_mapping(db: Session, mapping_id: int, actor: str):
    m = db.get(GroupMapping, mapping_id)
    if not m:
        raise PlatformError("mapeamento não encontrado")
    db.delete(m)
    db.commit()
    audit(db, actor, "sso.mapping.delete", m.provider, m.external_group)
