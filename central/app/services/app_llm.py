"""Modelos de token sincronizados com o app Hangar (desktop) e o proxy de LLM dele (contrato em
docs/app-hangar-models.md).

O admin liga, em cada conexão de LLM, "Sincronizar modelos de token com o Hangar desktop". O app lista os modelos em
GET /api/app/models e fala com /api/app/llm/<id>/v1/messages usando a chave do dispositivo (ahl_…). A chave da
conexão nunca sai da plataforma: o proxy injeta a credencial do cofre, aplica as regras da empresa e registra uso e
custo (UsageEvent sem agente, client=hangar-desktop, no time da pessoa: conta no orçamento do time).

- Só conexões com protocol=anthropic (é o formato que o Claude Code fala) e, com code_policy=approved, allow_code: o
  Hangar manda código e saídas de terminal.
- Modelos: os de texto disponíveis na conexão, menos os que o admin escondeu.
- A chave do dispositivo vale enquanto o dispositivo está ativo, a pessoa ativa e a autorização OAuth dele vale;
  gerar outra invalida a anterior. Só o hash fica no banco.
- O time que paga é o primeiro time da pessoa (o de menor id, como nos agentes pessoais).
"""
import hashlib
import json
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, crypto, observability
from ..models import AppDevice, LlmConnection, LlmModel, OAuthGrant, Organization, TeamMember, UsageEvent, User, now
from . import app_hangar as ah
from . import llm_models
from .access import Forbidden
from .common import PlatformError, audit, iso

KEY_PREFIX = "ahl_"
CLIENT = "hangar-desktop"
ANTHROPIC_VERSION = "2023-06-01"
# o preço de cache da Anthropic sobre o preço de entrada: escrever custa 1,25x; ler, 0,1x
CACHE_WRITE, CACHE_READ = 1.25, 0.1


class ProxyError(Exception):
    """Recusa do proxy, no formato de erro da API da Anthropic (o Claude Code mostra a mensagem)."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message

    def body(self) -> dict:
        return {"type": "error", "error": {"type": self.kind, "message": self.message}, "detail": self.message}


# ------------------------------------------------------------------ conexões e modelos sincronizados
def _code_policy(db: Session, org_id: int | None) -> str:
    o = db.get(Organization, org_id or 1)
    return (o.code_policy if o else "") or "any"


def blocked_reason(db: Session, c: LlmConnection) -> str:
    """Por que esta conexão não pode ir para o app ("" = pode)."""
    if c.protocol != "anthropic":
        return ("só conexões com protocolo anthropic (API de Mensagens, /v1/messages) vão para o Hangar desktop: é o "
                f"formato que o Claude Code fala, e esta é '{c.protocol}'")
    if _code_policy(db, c.org_id) == "approved" and not c.allow_code:
        return ("a empresa só manda código para conexões aprovadas, e o Hangar envia arquivos e saídas de terminal: "
                "aprove esta conexão para código antes de sincronizar")
    return ""


def _text_models(db: Session, c: LlmConnection) -> list[LlmModel]:
    return list(db.scalars(select(LlmModel).where(LlmModel.connection_id == c.id, LlmModel.kind == "text",
                                                  LlmModel.available.is_(True)).order_by(LlmModel.model_id)))


def visible_models(db: Session, c: LlmConnection) -> list[LlmModel]:
    hidden = set(c.desktop_hidden_models or [])
    return [m for m in _text_models(db, c) if m.model_id not in hidden]


def desktop_state(db: Session, c: LlmConnection) -> dict:
    """O estado do botão no console: ligado, por que não pode, quantos modelos vão para o app e quais estão escondidos."""
    text = _text_models(db, c)
    hidden = set(c.desktop_hidden_models or [])
    reason = blocked_reason(db, c)
    return {"sync": bool(c.sync_desktop), "reason": reason, "active": bool(c.sync_desktop) and not reason,
            "models": sum(1 for m in text if m.model_id not in hidden), "models_total": len(text),
            "hidden": sorted(hidden)}


def set_desktop(db: Session, actor: str, name: str, sync: bool | None = None, model_id: str | None = None,
                hidden: bool | None = None) -> dict:
    """Liga/desliga a sincronização e esconde/mostra um modelo no app (só admin; auditado)."""
    from .catalog import get_connection
    c = get_connection(db, name)
    changes = []
    if sync is not None and bool(sync) != bool(c.sync_desktop):
        why = blocked_reason(db, c) if sync else ""
        if why:
            raise PlatformError(why)
        c.sync_desktop = bool(sync)
        changes.append("sincronizada com o Hangar desktop" if sync else "não sincronizada com o Hangar desktop")
    if model_id is not None and hidden is not None:
        if not db.scalar(select(LlmModel.id).where(LlmModel.connection_id == c.id, LlmModel.model_id == model_id)):
            raise PlatformError(f"O modelo '{model_id}' não está na lista da conexão '{c.name}'")
        current = [m for m in (c.desktop_hidden_models or []) if m != model_id]
        c.desktop_hidden_models = sorted(current + [model_id]) if hidden else current
        changes.append(f"{model_id} {'escondido do' if hidden else 'mostrado no'} app")
    db.commit()
    if changes:
        audit(db, actor, "llm_connection.desktop", c.name, "; ".join(changes))
    return desktop_state(db, c)


def _synced(db: Session) -> list[LlmConnection]:
    return [c for c in db.scalars(select(LlmConnection).where(LlmConnection.sync_desktop.is_(True))
                                  .order_by(LlmConnection.name)) if not blocked_reason(db, c)]


def models_for_app(db: Session, p: auth.Principal) -> dict:
    """GET /api/app/models: as conexões sincronizadas e permitidas, com os modelos visíveis. `version` muda quando a
    lista muda (o app compara para saber se precisa refazer os perfis)."""
    u = db.get(User, p.user_id) if p.user_id else None
    if u is None or not u.active:
        raise Forbidden("usuário sem acesso")
    out = []
    for c in _synced(db):
        models = []
        for m in visible_models(db, c):
            pin, pout = llm_models.prices_for(db, c, m.model_id)
            models.append({"model_id": m.model_id, "name": m.name or m.model_id, "context_length": m.context_length,
                           "caps": m.caps or [], "price_in_per_mtok": pin, "price_out_per_mtok": pout})
        if models:
            out.append({"id": c.id, "name": c.name, "provider": c.provider or "custom",
                        "base_path": f"/api/app/llm/{c.id}", "models": models})
    canon = json.dumps(out, sort_keys=True, separators=(",", ":")).encode()
    return {"version": hashlib.sha256(canon).hexdigest()[:12], "connections": out}


def admin_summary(db: Session) -> dict:
    """Para a visão geral do App Hangar: quantas conexões e quantos modelos vão para o app."""
    conns = _synced(db)
    return {"connections": len(conns), "models": sum(len(visible_models(db, c)) for c in conns)}


# ------------------------------------------------------------------ chave do dispositivo
def rotate_key(db: Session, p: auth.Principal, device_id: str) -> dict:
    """Gera ou troca a chave do proxy de LLM deste dispositivo: a anterior deixa de valer na hora."""
    d = ah.attach(db, p, device_id)
    if d.status != "active":
        db.commit()
        raise Forbidden("dispositivo revogado — fale com quem administra o Agent Hangar")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    rotated = bool(d.llm_key_hash)
    d.llm_key_hash, d.llm_key_prefix, d.llm_key_created_at = auth.hash_key(key), key[:12], now()
    d.last_seen_at = now()
    db.commit()
    u = db.get(User, d.user_id)
    audit(db, u.email if u else str(d.user_id), "app.device.llm_key", d.name,
          f"{'trocada' if rotated else 'criada'} · {d.device_id} · {d.llm_key_prefix}…")
    return {"key": key, "prefix": d.llm_key_prefix, "created_at": iso(d.llm_key_created_at)}


def device_of_key(db: Session, key: str) -> AppDevice:
    """O dispositivo dono da chave ahl_…; 401 se a chave não vale (trocada, dispositivo revogado, pessoa desligada,
    autorização encerrada)."""
    if not key.startswith(KEY_PREFIX):
        raise ProxyError(401, "authentication_error", "use a chave do dispositivo (ahl_…) gerada pelo app Hangar")
    d = db.scalar(select(AppDevice).where(AppDevice.llm_key_hash == auth.hash_key(key)).execution_options(all_orgs=True))
    if d is None or d.status != "active":
        raise ProxyError(401, "authentication_error", "chave inválida ou dispositivo revogado — conecte o app de novo")
    why = ah._grant_end(db, db.get(OAuthGrant, d.grant_id) if d.grant_id else None)
    if why:
        ah._revoke(d, ah.SYSTEM + why)
        db.commit()
        raise ProxyError(401, "authentication_error", f"{why} — conecte o app de novo")
    return d


# ------------------------------------------------------------------ proxy
def team_of(db: Session, user_id: int) -> int | None:
    return db.scalar(select(TeamMember.team_id).where(TeamMember.user_id == user_id).order_by(TeamMember.team_id).limit(1))


def target(db: Session, d: AppDevice, connection_id: int) -> dict:
    """A conexão para onde o proxy repassa, depois das regras: sincronizada, protocolo e política de código."""
    c = db.get(LlmConnection, connection_id)
    if c is None:
        raise ProxyError(404, "not_found_error", f"conexão {connection_id} não existe nesta empresa")
    if not c.sync_desktop:
        raise ProxyError(403, "permission_error", f"a conexão '{c.name}' não está sincronizada com o Hangar desktop")
    why = blocked_reason(db, c)
    if why:
        raise ProxyError(403, "permission_error", why)
    return {"id": c.id, "name": c.name, "base_url": c.base_url.rstrip("/"), "provider": c.provider or "custom",
            "key": crypto.decrypt(c.api_key) if c.api_key else "", "visible": [m.model_id for m in visible_models(db, c)],
            "device": d.id, "user": d.user_id, "org": d.org_id, "team": team_of(db, d.user_id)}


def check_model(t: dict, model: str):
    if model not in t["visible"]:
        raise ProxyError(400, "invalid_request_error",
                         f"o modelo '{model}' não está liberado para o Hangar desktop na conexão '{t['name']}'. "
                         f"Modelos liberados: {', '.join(t['visible']) or 'nenhum'}")


def check_budget(db: Session, t: dict):
    from . import budgets
    from . import org as orgmod
    if orgmod.budget_blocked(db, t["team"]):
        raise ProxyError(429, "rate_limit_error", "orçamento mensal do time esgotado — fale com o mantenedor do time")
    stop = budgets.stopped(db, None, None, team_id=t["team"])
    if stop:
        raise ProxyError(429, "rate_limit_error",
                         f"orçamento parado ({stop['scope']}, US$ {stop['amount_usd']:g} por "
                         f"{'dia' if stop['window'] == 'day' else 'mês'}) — peça para liberar")


def upstream_headers(t: dict, incoming: dict) -> dict:
    """Os cabeçalhos para o provedor: versão e betas da API que o app pediu e a credencial do cofre. A API oficial da
    Anthropic usa x-api-key; gateways no formato Anthropic (LiteLLM, OpenRouter…) aceitam Bearer, então vão os dois."""
    h = {"content-type": "application/json", "anthropic-version": incoming.get("anthropic-version") or ANTHROPIC_VERSION,
         "accept": incoming.get("accept") or "application/json"}
    if incoming.get("anthropic-beta"):
        h["anthropic-beta"] = incoming["anthropic-beta"]
    if t["key"]:
        h["x-api-key"] = t["key"]
        if t["provider"] != "anthropic" and "api.anthropic.com" not in t["base_url"]:
            h["authorization"] = f"Bearer {t['key']}"
    return h


def merge_usage(acc: dict, usage: dict | None):
    """Junta os blocos `usage` (message_start traz a entrada; message_delta, a saída acumulada): o último valor vence."""
    for k, v in (usage or {}).items():
        if isinstance(v, int) and not isinstance(v, bool):
            acc[k] = v


def sse_usage(line: bytes, acc: dict):
    """Tokens de uma linha SSE da API de Mensagens: eventos message_start e message_delta."""
    if not line.startswith(b"data:") or b'"usage"' not in line:
        return
    try:
        ev = json.loads(line[5:])
    except ValueError:
        return
    if not isinstance(ev, dict):
        return
    if ev.get("type") == "message_start" and isinstance(ev.get("message"), dict):
        merge_usage(acc, ev["message"].get("usage"))
    elif ev.get("type") == "message_delta":
        merge_usage(acc, ev.get("usage"))


def cost_of(prices: tuple, u: dict) -> float:
    p_in, p_out = prices or (None, None)
    p_in, p_out = p_in or 0, p_out or 0
    total = (u.get("input_tokens", 0) * p_in + u.get("cache_creation_input_tokens", 0) * p_in * CACHE_WRITE
             + u.get("cache_read_input_tokens", 0) * p_in * CACHE_READ + u.get("output_tokens", 0) * p_out)
    return round(total / 1_000_000, 6)


def record(db: Session, t: dict, model: str, usage: dict, ok: bool, latency_ms: int):
    """O uso de uma chamada (só metadados: nunca o corpo nem a resposta)."""
    c = db.get(LlmConnection, t["id"])
    prices = llm_models.prices_for(db, c, model) if c is not None else (None, None)
    ev = UsageEvent(agent_id=None, env="app", channel=CLIENT, protocol="anthropic", org_id=t["org"],
                    tokens_in=int(usage.get("input_tokens", 0)), tokens_out=int(usage.get("output_tokens", 0)),
                    tokens_cache_read=int(usage.get("cache_read_input_tokens", 0)),
                    tokens_cache_write=int(usage.get("cache_creation_input_tokens", 0)),
                    cost_usd=cost_of(prices, usage), latency_ms=latency_ms, ok=ok, user_id=t["user"], team_id=t["team"],
                    device_id=t["device"], connection_id=t["id"], client=CLIENT, model=(model or "")[:200])
    db.add(ev)
    db.commit()
    observability.record_invocation(CLIENT, "app", CLIENT, "anthropic", ok, latency_ms, ev.tokens_in, ev.tokens_out,
                                    ev.cost_usd)
    return ev


def models_list(t: dict, db: Session) -> dict:
    """GET /v1/models no formato da Anthropic: só os modelos visíveis da conexão (sem chamar o provedor)."""
    names = {m.model_id: m for m in db.scalars(select(LlmModel).where(LlmModel.connection_id == t["id"]))}
    data = [{"type": "model", "id": mid, "display_name": (names[mid].name if mid in names else "") or mid,
             "created_at": iso(names[mid].first_seen) if mid in names else None} for mid in t["visible"]]
    return {"data": data, "has_more": False, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None}
