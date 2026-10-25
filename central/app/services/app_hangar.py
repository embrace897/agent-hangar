"""App Hangar (desktop) conectado à plataforma: perfil, dispositivos, heartbeat e design tokens (contrato em
docs/app-hangar.md).

O app entra por OAuth (loopback, escopos `app mcp`, ver mcp_oauth.py) e registra o computador como dispositivo,
amarrado à autorização que recebeu. Um dispositivo fica `revoked` quando:
- o admin revoga (a autorização cai junto, e o mesmo device_id não volta a se registrar para aquela pessoa);
- a autorização acaba (saída pelo app, "Minhas chaves", app bloqueado, refresh expirado) ou a pessoa é desligada —
  aqui o revogador é "sistema:…" e um login novo reativa o dispositivo.
O heartbeat responde `revoked` também para o token de uma autorização já encerrada: o app sai e avisa a pessoa.
"""
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, config
from ..models import AppDevice, OAuthClient, OAuthGrant, Organization, Team, TeamMember, User, now
from .access import Forbidden
from .common import PlatformError, audit, iso

TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "design-tokens.json")
DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SYSTEM = "sistema:"  # revogado pelo fim da autorização ou do acesso (não pelo admin)


class NotFound(PlatformError):
    pass


class Unauthorized(Exception):
    """401: o app faz refresh e repete."""


# ------------------------------------------------------------------ design tokens
_TOKENS: dict = {}


def design_tokens() -> dict:
    """Os tokens do console (static/design-tokens.json) com a versão: o hash do conteúdo, que muda sozinho a cada
    alteração e diz ao app se a cópia embutida dele está em dia."""
    if not _TOKENS:
        with open(TOKENS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        canon = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        _TOKENS.update({"version": hashlib.sha256(canon).hexdigest()[:12], **data})
    return _TOKENS


# ------------------------------------------------------------------ quem é a pessoa
def me(db: Session, p: auth.Principal) -> dict:
    u = db.get(User, p.user_id) if p.user_id else None
    if u is None or not u.active:
        raise Forbidden("usuário sem acesso")
    o = db.get(Organization, u.org_id or 1)
    teams = [{"id": t.id, "slug": t.slug, "name": t.name}
             for t in db.scalars(select(Team).join(TeamMember, TeamMember.team_id == Team.id)
                                 .where(TeamMember.user_id == u.id).order_by(Team.name))]
    return {"user": {"id": u.id, "email": u.email, "name": u.name, "avatar_url": u.avatar_url, "org_role": u.org_role,
                     "teams": teams},
            "org": {"id": o.id, "name": o.name, "slug": o.slug or "", "brand": o.brand or {}} if o else None,
            "platform": {"version": config.VERSION, "features": {}},
            "design_tokens": {"version": design_tokens()["version"]}}


# ------------------------------------------------------------------ dispositivos
def device_dict(d: AppDevice) -> dict:
    return {"id": d.id, "device_id": d.device_id, "name": d.name, "os": d.os, "app_version": d.app_version,
            "status": d.status, "created_at": iso(d.created_at), "last_seen_at": iso(d.last_seen_at)}


def _text(value, size: int) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", str(value or ""))[:size].strip()


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _grant_end(db: Session, g: OAuthGrant | None) -> str:
    """Por que a autorização não vale mais ("" = vale)."""
    if g is None or g.revoked_at is not None:
        return "autorização encerrada"
    if _aware(g.refresh_expires_at) < now():
        return "autorização expirada"
    c = db.get(OAuthClient, g.client_id)
    if c is None or c.revoked_at is not None:
        return "app bloqueado"
    u = db.get(User, g.user_id)
    if u is None or not u.active:
        return "acesso encerrado"
    return ""


def _revoke(d: AppDevice, by: str):
    d.status, d.revoked_at, d.revoked_by = "revoked", now(), by[:254]
    d.llm_key_hash = None  # a chave do proxy de LLM (ahl_…) não volta com um login novo: o app gera outra


def _sync(db: Session, devices) -> None:
    """Dispositivos ativos cuja autorização acabou (saída, revogação, bloqueio, desligamento) passam a revogados."""
    changed = False
    for d in devices:
        if d.status != "active":
            continue
        why = _grant_end(db, db.get(OAuthGrant, d.grant_id) if d.grant_id else None)
        if why:
            _revoke(d, SYSTEM + why)
            changed = True
    if changed:
        db.commit()


def _app_caller(p: auth.Principal) -> auth.Principal:
    if p.via != "oauth" or not p.grant_id or "app" not in p.oauth_scopes or not p.user_id:
        raise Forbidden("entre pelo app Hangar (OAuth com escopo app) para registrar o dispositivo")
    return p


def register_device(db: Session, p: auth.Principal, body: dict) -> dict:
    p = _app_caller(p)
    device_id = str(body.get("device_id") or "").strip()
    if not DEVICE_ID.fullmatch(device_id):
        raise PlatformError("device_id inválido (use o uuid gerado pelo app)")
    fields = {"name": _text(body.get("name"), 120) or "Hangar", "os": _text(body.get("os"), 40),
              "os_version": _text(body.get("os_version"), 60), "app_version": _text(body.get("app_version"), 40),
              "channel": _text(body.get("channel"), 20)}
    d = db.scalar(select(AppDevice).where(AppDevice.user_id == p.user_id, AppDevice.device_id == device_id))
    u = db.get(User, p.user_id)
    if d is not None and d.status == "revoked" and not d.revoked_by.startswith(SYSTEM):
        raise Forbidden("dispositivo revogado pelo administrador — fale com quem administra o Agent Hangar")
    event = None
    if d is None:
        d = AppDevice(user_id=p.user_id, device_id=device_id, org_id=u.org_id or 1)
        db.add(d)
        event = "novo"
    elif d.status == "revoked" or d.grant_id != p.grant_id:
        event = "reconectado"
    for k, v in fields.items():
        setattr(d, k, v)
    d.grant_id, d.status, d.revoked_at, d.revoked_by, d.last_seen_at = p.grant_id, "active", None, "", now()
    db.commit()
    if event:
        audit(db, u.email, "app.device.register", d.name,
              f"{event} · {device_id} · {fields['os']} {fields['os_version']} · app {fields['app_version']}".strip())
    return {"device": device_dict(d)}


def attach(db: Session, p: auth.Principal, device_id: str) -> AppDevice:
    """O dispositivo da pessoa que chama (pelo app). Um login novo neste computador (outra autorização) o assume de
    novo, a não ser que o admin o tenha revogado."""
    p = _app_caller(p)
    d = db.scalar(select(AppDevice).where(AppDevice.user_id == p.user_id, AppDevice.device_id == device_id))
    if d is None:
        raise NotFound("dispositivo não registrado — registre em POST /api/app/devices")
    if d.grant_id != p.grant_id and (d.status == "active" or d.revoked_by.startswith(SYSTEM)):
        d.grant_id, d.status, d.revoked_at, d.revoked_by = p.grant_id, "active", None, ""  # novo login neste computador
    return d


def heartbeat(db: Session, p: auth.Principal | None, token: str, device_id: str, app_version: str = "") -> dict:
    """Sinal de vida do app. Com token válido: atualiza o dispositivo e diz se ele segue ativo. Com o token de uma
    autorização já encerrada (p None): `revoked`, para o app sair; token desconhecido ou só expirado: 401 (o app faz
    refresh e repete)."""
    if p is None:
        g = db.scalar(select(OAuthGrant).where(OAuthGrant.access_hash == auth.hash_key(token or ""))
                      .execution_options(all_orgs=True)) if (token or "").startswith(auth.OAUTH_PREFIX) else None
        why = _grant_end(db, g) if g is not None and "app" in (g.scope or "").split() else ""
        if not why:
            raise Unauthorized("token inválido ou expirado")
        d = db.scalar(select(AppDevice).where(AppDevice.user_id == g.user_id, AppDevice.device_id == device_id)
                      .execution_options(all_orgs=True))
        if d is not None and d.status == "active" and d.grant_id == g.id:
            _revoke(d, SYSTEM + why)
            db.commit()
        return {"status": "revoked"}
    d = attach(db, p, device_id)
    if d.status == "revoked":
        return {"status": "revoked"}
    d.last_seen_at = now()
    if app_version:
        d.app_version = _text(app_version, 40)
    db.commit()
    return {"status": "active"}


# ------------------------------------------------------------------ admin (console)
def admin_devices(db: Session) -> list[dict]:
    rows = list(db.scalars(select(AppDevice).order_by(AppDevice.last_seen_at.desc().nullslast(), AppDevice.id.desc())
                           .limit(1000)))
    _sync(db, rows)
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({d.user_id for d in rows})))} if rows else {}
    out = []
    for d in rows:
        u = users.get(d.user_id)
        out.append({**device_dict(d), "user": {"id": d.user_id, "email": u.email if u else "", "name": u.name if u else ""},
                    "os_version": d.os_version, "channel": d.channel, "revoked_at": iso(d.revoked_at),
                    "revoked_by": d.revoked_by or None})
    return out


def admin_overview(db: Session) -> dict:
    from . import app_llm
    rows = list(db.scalars(select(AppDevice)))
    _sync(db, rows)
    active = [d for d in rows if d.status == "active"]
    seen = [_aware(d.last_seen_at) for d in rows if d.last_seen_at]
    versions = Counter(d.app_version or "?" for d in active)
    return {"devices_active": len(active), "members_connected": len({d.user_id for d in active}),
            "versions": [{"version": v, "count": n} for v, n in sorted(versions.items(), key=lambda x: (-x[1], x[0]))],
            "last_seen_at": iso(max(seen)) if seen else None, "platform_url": config.PUBLIC_BASE_URL,
            "models_synced": app_llm.admin_summary(db)}


def admin_revoke(db: Session, actor: str, device_pk: int) -> dict:
    d = db.get(AppDevice, device_pk)
    if d is None:
        raise NotFound("dispositivo não encontrado")
    if d.status != "revoked" or d.revoked_by.startswith(SYSTEM):
        _revoke(d, actor)  # revogado pelo sistema e depois pelo admin: passa a ser do admin (não volta sozinho)
    g = db.get(OAuthGrant, d.grant_id) if d.grant_id else None
    if g is not None and g.revoked_at is None:
        g.revoked_at = now()
    db.commit()
    auth.clear_cache()
    u = db.get(User, d.user_id)
    audit(db, actor, "app.device.revoke", d.name, f"{d.device_id} · {u.email if u else d.user_id}")
    return {"ok": True}
