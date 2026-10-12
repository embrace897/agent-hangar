"""Autenticação: quem está chamando. (O que cada um pode fazer fica em services/access.py.)

Credenciais, cada uma com um alcance mínimo:
- Sessão da UI (cookie HttpOnly `hangar_session`, token "ahs_…"): aberta pelo login OAuth2 (Google, Microsoft
  Entra ID, GitHub…) — vale como o usuário, com os papéis dele. A de emergência (ADMIN_TOKEN) vale como admin.
- ADMIN_TOKEN (bootstrap/emergência) e chaves de API com escopo "admin": administram tudo.
- Chave "user" (token pessoal): age como o usuário dono dela — CLI, MCP da plataforma, API.
- Chave "invoke": só chama agentes pelo gateway /gw (os da lista). É a que vai para LibreChat, Slack, OpenCode…
  Se tem dono, morre quando ele perde o acesso ao agente.
- Chave "scim": só o endpoint /scim/v2 (provisionamento pelo Entra ID, Okta…).
- Token interno por agente (HMAC do slug): o container de um agente só consegue se identificar como ele
  mesmo, e a central só deixa chamar os sub_agents dele (e o dashboard, se a tool estiver na spec).
"""
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import ApiKey, OAuthClient, OAuthGrant, User, UserSession, now

KEY_PREFIX = "ah_"
SESSION_PREFIX = "ahs_"
OAUTH_PREFIX = "aho_"  # token de acesso OAuth do MCP (Claude.ai, ChatGPT…): só vale no /mcp
SCOPES = ("admin", "invoke", "user", "scim")
SESSION_COOKIE = "hangar_session"
CSRF_COOKIE = "hangar_csrf"


@dataclass
class Principal:
    name: str  # vai para a auditoria: e-mail do usuário, "key:<nome>" ou "admin-token"
    scopes: set = field(default_factory=set)
    agents: list = field(default_factory=list)  # vazio = todos (para o escopo invoke)
    client: str | None = None  # chave de conexão de uma ferramenta: vira o canal padrão das métricas
    user_id: int | None = None
    org_role: str | None = None  # admin | auditor | member — só quando age como usuário
    via: str = "key"  # key | session | admin-token | oauth
    org_id: int | None = None  # a empresa de quem chama (None = o token admin, que escolhe pelo X-Hangar-Org)

    @property
    def is_admin(self) -> bool:
        return "admin" in self.scopes or self.org_role == "admin"

    @property
    def is_auditor(self) -> bool:
        return self.is_admin or self.org_role == "auditor"

    @property
    def is_user(self) -> bool:
        """Age como um usuário (sessão ou token pessoal): acessa a API conforme os papéis dele."""
        return "user" in self.scopes and self.user_id is not None

    @property
    def can_use_api(self) -> bool:
        return self.is_admin or self.is_user

    def can_invoke(self, slug: str) -> bool:
        """Checagem sem banco das chaves invoke; usuários passam por access.can(..., "consume")."""
        if self.is_admin:
            return True
        return "invoke" in self.scopes and (not self.agents or slug in self.agents)


ADMIN = Principal("admin-token", {"admin"}, via="admin-token")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(db: Session, name: str, scopes: list[str], agents: list[str] | None = None,
                   actor: str = "admin", client: str | None = None, mode: str | None = None,
                   user_id: int | None = None) -> tuple[ApiKey, str]:
    """Devolve (registro, valor em texto) — o valor só existe aqui; no banco fica apenas o hash."""
    bad = [s for s in scopes if s not in SCOPES]
    if not scopes or bad:
        raise ValueError(f"scopes inválidos: {bad or scopes}. Opções: {list(SCOPES)}")
    if "user" in scopes and not user_id:
        raise ValueError("chave 'user' (token pessoal) precisa de um usuário dono")
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    row = ApiKey(name=name, prefix=raw[:11], key_hash=hash_key(raw), scopes=sorted(set(scopes)),
                 agents=sorted(set(agents or [])), created_by=actor, client=client, mode=mode, user_id=user_id)
    db.add(row)
    db.commit()
    clear_cache()
    return row, raw


def revoke_api_key(db: Session, key_id: int):
    row = db.get(ApiKey, key_id)
    if not row:
        raise ValueError(f"chave {key_id} não encontrada")
    row.revoked_at = now()
    db.commit()
    clear_cache()


def api_key_dict(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes, "agents": k.agents,
            "created_by": k.created_by, "created_at": k.created_at.isoformat() if k.created_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked": k.revoked_at is not None, "client": k.client, "mode": k.mode, "user_id": k.user_id}


# ------------------------------------------------------------------ contas locais (usuário e senha)
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}
MIN_PASSWORD = 8
MAX_FAILS, LOCK_S = 5, 900


def hash_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"a senha precisa ter pelo menos {MIN_PASSWORD} caracteres")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, n, r, p, salt, dk = (stored or "").split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt((password or "").encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=_SCRYPT["maxmem"], dklen=len(dk) // 2)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


def locked_out(key: str) -> bool:
    """Contra força bruta: 5 erros em 15 min bloqueiam o login daquela conta por 15 min (contagem no banco: vale
    para todas as réplicas)."""
    from . import shared
    return shared.hits(f"loginfail:{key}", LOCK_S) >= MAX_FAILS


def register_fail(key: str):
    from . import shared
    shared.hits(f"loginfail:{key}", LOCK_S, add=True)


def clear_fails(key: str):
    from . import shared
    shared.pop(f"loginfail:{key}")


# ------------------------------------------------------------------ sessões da UI
def create_session(db: Session, user: User | None = None, admin: bool = False, label: str = "", method: str = "",
                   user_agent: str = "", ip: str = "") -> str:
    raw = SESSION_PREFIX + secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=hash_key(raw), user_id=user.id if user else None, admin=admin,
                       label=label[:200] or (user.email if user else "admin"),
                       expires_at=now() + timedelta(hours=config.SESSION_TTL_HOURS), method=method[:12],
                       user_agent=(user_agent or "")[:300], ip=(ip or "")[:64], last_seen_at=now()))
    db.commit()
    return raw


def end_session(db: Session, raw: str):
    row = db.scalar(select(UserSession).where(UserSession.token_hash == hash_key(raw or "")))
    if row and row.revoked_at is None:
        row.revoked_at = now()
        db.commit()
    clear_cache()


def revoke_user_sessions(db: Session, user_id: int):
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))):
        s.revoked_at = now()
    for g in db.scalars(select(OAuthGrant).where(OAuthGrant.user_id == user_id, OAuthGrant.revoked_at.is_(None))):
        g.revoked_at = now()  # apps conectados por OAuth (Claude.ai, ChatGPT…) caem junto
    db.commit()
    clear_cache()


def _user_principal(u: User, via: str, name: str | None = None) -> Principal | None:
    if not u or not u.active:
        return None
    return Principal(name or u.email, {"user"}, user_id=u.id, org_role=u.org_role, via=via, org_id=u.org_id or 1)


# Cache curto: evita um SELECT por request no gateway. Revogação/mudança de papel limpa o cache.
_CACHE: dict[str, tuple[float, Principal | None]] = {}
_TTL = 30.0


def clear_cache():
    _CACHE.clear()


def _aware(dt):
    from datetime import UTC
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def authenticate(db: Session, token: str) -> Principal | None:
    if not token:
        return None
    if config.ADMIN_TOKEN and hmac.compare_digest(token, config.ADMIN_TOKEN):
        return ADMIN
    if not token.startswith((KEY_PREFIX, SESSION_PREFIX, OAUTH_PREFIX)):
        return None
    h = hash_key(token)
    hit = _CACHE.get(h)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    principal = None
    if token.startswith(OAUTH_PREFIX):
        g = db.scalar(select(OAuthGrant).where(OAuthGrant.access_hash == h))
        c = db.get(OAuthClient, g.client_id) if g else None
        if g and c and g.revoked_at is None and c.revoked_at is None and _aware(g.access_expires_at) > now():
            u = db.get(User, g.user_id)
            principal = _user_principal(u, "oauth", f"{u.email} (app:{c.name})") if u else None
            if principal is not None:
                g.last_used_at = now()
                db.commit()
    elif token.startswith(SESSION_PREFIX):
        s = db.scalar(select(UserSession).where(UserSession.token_hash == h))
        if s and s.revoked_at is None and _aware(s.expires_at) > now():
            if s.last_seen_at is None or _aware(s.last_seen_at) < now() - timedelta(minutes=1):
                s.last_seen_at = now()  # o "último acesso" que o membro e o admin veem
                db.commit()
            if s.user_id:
                principal = _user_principal(db.get(User, s.user_id), "session")
            elif s.admin:
                principal = Principal(f"sessão:{s.label}", {"admin"}, via="session")
    else:
        row = db.scalar(select(ApiKey).where(ApiKey.key_hash == h))
        if row and row.revoked_at is None:
            owner = db.get(User, row.user_id) if row.user_id else None
            if row.user_id and (owner is None or not owner.active):
                principal = None  # dono desligado: a chave morre junto
            elif "user" in (row.scopes or []):
                principal = _user_principal(owner, "key", f"{owner.email} (key:{row.name})")
            else:
                principal = Principal(f"key:{row.name}", set(row.scopes or []), list(row.agents or []), row.client,
                                      user_id=row.user_id, org_id=row.org_id or 1)
            if principal is not None:
                row.last_used_at = now()
                db.commit()
    _CACHE[h] = (time.monotonic(), principal)
    return principal


def token_from_headers(headers: dict) -> str:
    return headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ").strip()


def cookie(headers: dict, name: str) -> str:
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


# ------------------------------------------------------------------ tokens internos (agente -> central)
def agent_token(slug: str) -> str:
    if not config.INTERNAL_SECRET:
        raise RuntimeError("INTERNAL_SECRET não definido")
    return hmac.new(config.INTERNAL_SECRET.encode(), f"agent:{slug}".encode(), hashlib.sha256).hexdigest()


def verify_agent_token(slug: str, token: str) -> bool:
    return bool(slug and token and config.INTERNAL_SECRET) and hmac.compare_digest(agent_token(slug), token)


def agent_env_token(slug: str, env: str) -> str:
    """Prova o ambiente (stage/prod) do container: o token do agente é o mesmo nos dois, e um container de
    stage não pode gravar na memória de produção se passando por prod."""
    if not config.INTERNAL_SECRET:
        raise RuntimeError("INTERNAL_SECRET não definido")
    return hmac.new(config.INTERNAL_SECRET.encode(), f"agent-env:{slug}:{env}".encode(), hashlib.sha256).hexdigest()


def verified_env(slug: str, env: str, token: str) -> str:
    """O ambiente declarado, se o token confere; senão stage (o menos privilegiado)."""
    if env in ("stage", "prod") and token and config.INTERNAL_SECRET and \
            hmac.compare_digest(agent_env_token(slug, env), token):
        return env
    return "stage"
