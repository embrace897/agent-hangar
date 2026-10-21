"""Assinatura Claude (Pro/Max) ou ChatGPT (Plus/Pro) de uma pessoa para os jobs de harness que ELA dispara.

Em agentes com harness (Claude Code, Codex) o custo de tokens pela API é alto; quem já paga uma assinatura pode usá-la
nos próprios jobs. As assinaturas de consumidor são licenças individuais, então a plataforma só usa a assinatura de
alguém em jobs que essa pessoa disparou (portal, CLI, a IA dela pelo MCP da plataforma). Digital employees,
agendamentos, testes e chaves de app atendem outras pessoas: esses usam sempre a conexão de API.

- **A empresa decide:** `access_policy.subscriptions.enabled` (padrão: desligado), o admin liga. Confira os termos de
  uso da Anthropic e da OpenAI antes de ligar.
- **A pessoa conecta:** o token do `claude setup-token` (Claude Code) ou o `~/.codex/auth.json` do `codex login` com a
  conta ChatGPT (Codex). Fica criptografado e nunca volta pela API (só os 4 últimos caracteres).
- **O agente escolhe quem paga:** `harness.billing` = `api` | `subscription` | `subscription_then_api`.
- **Limite do plano:** o container detecta "limite atingido"; a assinatura fica em pausa até o horário de reset (ou uma
  hora), e o job falha (`subscription`) ou é refeito pela API (`subscription_then_api`), sempre com o motivo no job.
  No máximo `max_concurrent` jobs ao mesmo tempo por assinatura.
- **Medição:** o job pela assinatura custa US$ 0 na medição e guarda quanto custaria pela API (`api_equiv_cost_usd`),
  para mostrar a economia.
"""
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import crypto, tenancy
from ..models import Job, Organization, User, UserSubscription, now
from .access import Access, Forbidden
from .common import PlatformError, audit, iso

VENDOR_OF_HARNESS = {"claude-code": "anthropic", "codex": "openai"}
VENDORS = {"anthropic": "Claude (Pro/Max)", "openai": "ChatGPT (Plus/Pro)"}
BILLING = ("api", "subscription", "subscription_then_api")
DEFAULT = {"enabled": False, "max_concurrent": 2}
LIMIT_FALLBACK_MIN = 60


# ------------------------------------------------------------------ política da empresa
def policy(db: Session) -> dict:
    o = db.get(Organization, tenancy.current_org())
    raw = ((o.access_policy if o else None) or {}).get("subscriptions") or {}
    return {**DEFAULT, **raw}


def set_policy(db: Session, changes: dict) -> dict:
    cur = policy(db)
    if "enabled" in changes:
        cur["enabled"] = bool(changes["enabled"])
    if "max_concurrent" in changes:
        cur["max_concurrent"] = max(1, min(int(changes["max_concurrent"] or 1), 10))
    o = db.get(Organization, tenancy.current_org())
    o.access_policy = {**(o.access_policy or {}), "subscriptions": cur}
    return cur


# ------------------------------------------------------------------ a credencial
def normalize(vendor: str, raw: str) -> tuple[str, str]:
    """(credencial a guardar, dica). Recusa chave de API: para isso existe a conexão de LLM."""
    raw = (raw or "").strip()
    if vendor == "anthropic":
        if raw.startswith("sk-ant-api"):
            raise PlatformError("isso é uma chave de API da Anthropic, não a assinatura: cadastre-a como conexão de LLM. "
                                "Para a assinatura, rode  claude setup-token  e cole o token que ele mostra")
        if len(raw) < 20 or any(c.isspace() for c in raw):
            raise PlatformError("cole o token que o  claude setup-token  mostra (uma linha, sem espaços)")
        return raw, raw[-4:]
    if vendor == "openai":
        try:
            data = json.loads(raw)
        except ValueError:
            raise PlatformError("cole o conteúdo do arquivo ~/.codex/auth.json (gerado pelo  codex login  com a "
                                "conta ChatGPT)") from None
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(tokens, dict) or not (tokens.get("refresh_token") or tokens.get("access_token")):
            if isinstance(data, dict) and data.get("OPENAI_API_KEY"):
                raise PlatformError("esse auth.json é de login por chave de API: cadastre a chave como conexão de LLM. "
                                    "Para a assinatura, rode  codex login  e entre com a conta ChatGPT")
            raise PlatformError("auth.json sem tokens da conta ChatGPT: rode  codex login  e entre com a conta ChatGPT")
        hint = str(tokens.get("account_id") or tokens.get("refresh_token") or "")[-4:]
        return json.dumps(data, separators=(",", ":")), hint
    raise PlatformError(f"fornecedor: {', '.join(VENDORS)}")


def view(s: UserSubscription, db: Session | None = None) -> dict:
    d = {"id": s.id, "vendor": s.vendor, "plan": VENDORS.get(s.vendor, s.vendor), "label": s.label, "hint": s.hint,
         "status": _status(s), "limited_until": iso(_aware(s.limited_until)) if s.limited_until else None,
         "last_error": s.last_error, "last_used_at": iso(_aware(s.last_used_at)) if s.last_used_at else None,
         "created_at": iso(_aware(s.created_at)) if s.created_at else None}
    if db is not None:
        since = now() - timedelta(days=30)
        row = db.execute(select(func.count(), func.coalesce(func.sum(Job.api_equiv_cost_usd), 0)).where(
            Job.subscription_id == s.id, Job.billing == "subscription", Job.created_at >= since)).one()
        d.update(jobs_30d=int(row[0] or 0), saved_usd_30d=round(float(row[1] or 0), 4))
    return d


def _aware(dt):
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _status(s: UserSubscription) -> str:
    if s.status == "limited" and s.limited_until and _aware(s.limited_until) <= now():
        return "active"
    return s.status


# ------------------------------------------------------------------ a pessoa: conectar, ver, remover
def _me(acc: Access) -> int:
    if not acc.p.user_id:
        raise Forbidden("a assinatura é de uma pessoa: entre com a sua conta")
    return acc.p.user_id


def mine(db: Session, acc: Access) -> dict:
    uid = _me(acc)
    rows = db.scalars(select(UserSubscription).where(UserSubscription.user_id == uid).order_by(UserSubscription.vendor))
    return {"policy": policy(db), "vendors": VENDORS, "subscriptions": [view(s, db) for s in rows]}


def connect(db: Session, acc: Access, vendor: str, credential: str, label: str = "") -> dict:
    uid = _me(acc)
    if not policy(db)["enabled"]:
        raise Forbidden("a empresa não liberou o uso de assinaturas pessoais (o admin liga em Membros → Política)")
    value, hint = normalize(vendor, credential)
    s = db.scalar(select(UserSubscription).where(UserSubscription.user_id == uid, UserSubscription.vendor == vendor))
    if s is None:
        s = UserSubscription(user_id=uid, vendor=vendor)
        db.add(s)
    s.credential, s.hint, s.label = crypto.encrypt(value), hint, (label or "").strip()[:120]
    s.status, s.limited_until, s.last_error, s.updated_at = "active", None, "", now()
    db.commit()
    audit(db, acc.p.name, "subscription.connect", vendor, f"…{hint}")
    return view(s, db)


def remove(db: Session, acc: Access, vendor: str) -> dict:
    uid = _me(acc)
    s = db.scalar(select(UserSubscription).where(UserSubscription.user_id == uid, UserSubscription.vendor == vendor))
    if s is None:
        raise PlatformError(f"você não tem assinatura {vendor} conectada")
    db.delete(s)
    db.commit()
    audit(db, acc.p.name, "subscription.remove", vendor, "")
    return {"ok": True}


# ------------------------------------------------------------------ o admin: ver (sem a credencial) e revogar
def admin_list(db: Session, acc: Access) -> list[dict]:
    if not acc.p.is_admin:
        raise Forbidden("só o admin")
    names = {u.id: (u.name or u.email) for u in db.scalars(select(User))}
    return [{**view(s, db), "user": names.get(s.user_id, str(s.user_id))}
            for s in db.scalars(select(UserSubscription).order_by(UserSubscription.user_id))]


def admin_revoke(db: Session, acc: Access, sid: int) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin")
    s = db.get(UserSubscription, sid)
    if s is None:
        raise PlatformError(f"assinatura #{sid} não existe")
    db.delete(s)
    db.commit()
    audit(db, acc.p.name, "subscription.revoke", s.vendor, f"#{sid} de {s.user_id}")
    return {"ok": True}


# ------------------------------------------------------------------ o job: quem paga
def choose(db: Session, harness_id: str, billing: str, user_id: int | None) -> tuple[UserSubscription | None, str]:
    """A assinatura que paga este job, ou (None, o motivo de não usar)."""
    if billing not in ("subscription", "subscription_then_api"):
        return None, ""
    vendor = VENDOR_OF_HARNESS.get(harness_id)
    if vendor is None:
        return None, f"o harness '{harness_id}' não roda por assinatura (só claude-code e codex)"
    if not user_id:
        return None, "job sem uma pessoa por trás (Digital employee, agendamento, teste ou chave de app): usa a API"
    pol = policy(db)
    if not pol["enabled"]:
        return None, "a empresa não liberou assinaturas pessoais"
    s = db.scalar(select(UserSubscription).where(UserSubscription.user_id == user_id, UserSubscription.vendor == vendor))
    if s is None:
        return None, f"você não tem assinatura {VENDORS[vendor]} conectada (Meus acessos)"
    st = _status(s)
    if st == "invalid":
        return None, f"a assinatura {VENDORS[vendor]} foi recusada pelo fornecedor: conecte de novo ({s.last_error[:120]})"
    if st == "limited":
        return None, f"a assinatura {VENDORS[vendor]} está no limite do plano até {iso(_aware(s.limited_until))}"
    running = db.scalar(select(func.count()).select_from(Job).where(
        Job.subscription_id == s.id, Job.status.in_(("queued", "running")))) or 0
    if running >= pol["max_concurrent"]:
        return None, f"a assinatura {VENDORS[vendor]} já tem {running} job(s) rodando (máximo {pol['max_concurrent']})"
    if s.status == "limited":  # o limite passou
        s.status, s.limited_until = "active", None
    s.last_used_at = now()
    return s, ""


def environment(s: UserSubscription) -> dict:
    """O que o container precisa para rodar pela assinatura (o entrypoint não usa LLM_API_KEY nesse modo)."""
    value = crypto.decrypt(s.credential)
    if s.vendor == "anthropic":
        return {"LLM_AUTH": "subscription", "CLAUDE_CODE_OAUTH_TOKEN": value}
    return {"LLM_AUTH": "subscription", "CODEX_AUTH_JSON": value}


def scrub(text: str, s: UserSubscription | None) -> str:
    """Tira a credencial de qualquer texto que o job devolveu (resultado, logs)."""
    if not text or s is None:
        return text
    value = crypto.decrypt(s.credential)
    secrets_ = [value] if s.vendor == "anthropic" else []
    if s.vendor == "openai":
        try:
            t = json.loads(value).get("tokens") or {}
            secrets_ = [str(v) for v in t.values() if isinstance(v, str) and len(v) > 12]
        except ValueError:
            pass
    for v in secrets_:
        text = text.replace(v, "[assinatura]")
    return text


def limited(db: Session, s: UserSubscription, info: dict):
    """O fornecedor disse "limite do plano": a assinatura pausa até o reset informado (ou uma hora)."""
    until = None
    at = (info or {}).get("resets_at")
    if isinstance(at, (int, float)) and at > 0:
        until = datetime.fromtimestamp(at, tz=UTC)
    if until is None or until <= now():
        until = now() + timedelta(minutes=LIMIT_FALLBACK_MIN)
    s.status, s.limited_until = "limited", until
    s.last_error = str((info or {}).get("message") or "limite do plano")[:500]
    s.updated_at = now()


def refreshed(db: Session, s: UserSubscription, auth_json: str):
    """O Codex renovou o login durante o job: guarda o auth.json novo (o refresh token pode ter mudado)."""
    try:
        value, hint = normalize("openai", auth_json)
    except PlatformError:
        return
    s.credential, s.hint, s.updated_at = crypto.encrypt(value), hint, now()


def rejected(db: Session, s: UserSubscription, message: str):
    s.status, s.last_error, s.updated_at = "invalid", (message or "credencial recusada")[:500], now()
