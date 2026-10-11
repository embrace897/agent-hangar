"""Telegram e Discord como canais de entrada: a mensagem para o bot vira tarefa de um Digital employee (com as mesmas
permissões do portal) e o resultado volta para a mesma conversa.

- Telegram: o admin cola o token do bot (BotFather); a plataforma registra o webhook com um segredo próprio
  (cabeçalho X-Telegram-Bot-Api-Secret-Token). Conversa direta com o bot, ou num grupo com /hangar ou @bot.
- Discord: o admin cola o id da aplicação, a chave pública e o token do bot; a plataforma registra o comando
  /hangar e confere a assinatura Ed25519 de toda chamada.

Nenhum dos dois entrega o e-mail da pessoa: cada um liga a própria conta uma vez, com um código de uso único gerado no
portal (Decisões → onde recebo) — "/start CÓDIGO" no Telegram, "/hangar link CÓDIGO" no Discord.
"""
import hmac
import json
import re
import secrets

from sqlalchemy import select

from .. import config, crypto, shared, tenancy
from ..models import Organization, User
from . import notify
from .access import Access, Forbidden
from .common import PlatformError, audit

DISCORD_API = "https://discord.com/api/v10"
LINK_TTL = 900
T = {
    "pt": {"nouser": "Ainda não sei quem você é no Agent Hangar. No portal, abra Decisões → “Conectar {ch}” e mande aqui "
                     "o código: {how}.", "linked": "Pronto: esta conta de {ch} agora é {who} no Agent Hangar.",
           "badcode": "Código inválido ou vencido: gere outro no portal."},
    "en": {"nouser": "I don't know who you are in Agent Hangar yet. In the portal, open Decisions → “Connect {ch}” and send "
                     "the code here: {how}.", "linked": "Done: this {ch} account is now {who} in Agent Hangar.",
           "badcode": "Invalid or expired code: create a new one in the portal."},
}


def _both(key: str, **kw) -> str:
    return T["pt"][key].format(**kw) + "\n\n" + T["en"][key].format(**kw)


# ------------------------------------------------------------------ configuração da empresa (admin)
def admin_view(cfg: dict) -> dict:
    base = config.PUBLIC_BASE_URL
    return {"telegram": {"configured": bool(cfg.get("telegram_bot_token")), "bot": cfg.get("telegram_bot", ""),
                         "webhook_error": cfg.get("telegram_webhook_error", "")},
            "discord": {"configured": bool(cfg.get("discord_bot_token") and cfg.get("discord_public_key")),
                        "app_id": cfg.get("discord_app_id", ""), "command_error": cfg.get("discord_command_error", ""),
                        "interactions_url": f"{base}/hooks/discord/{tenancy.current_org()}"}}


def configure(cfg: dict, changes: dict, org_id: int):
    """Aplica as chaves de Telegram e Discord em cfg (o notify da empresa). Erros de registro no serviço ficam
    gravados (e aparecem na tela) em vez de impedir o salvamento: num ambiente local, o Telegram recusa o webhook."""
    if changes.get("telegram_bot_token") is not None:
        tok = str(changes["telegram_bot_token"]).strip()
        if tok and not re.fullmatch(r"\d+:[\w-]{20,}", tok):
            raise PlatformError("o token do bot do Telegram tem o formato 123456:ABC… (do BotFather)")
        cfg["telegram_bot_token"] = crypto.encrypt(tok) if tok else ""
        cfg["telegram_bot"], cfg["telegram_webhook_error"] = "", ""
        if tok:
            me = _telegram_raw(tok, "getMe", {})
            cfg["telegram_bot"] = (me.get("result") or {}).get("username", "")
            secret = secrets.token_urlsafe(24)
            cfg["telegram_secret"] = crypto.encrypt(secret)
            try:
                _telegram_raw(tok, "setWebhook", {"url": f"{config.PUBLIC_BASE_URL}/hooks/telegram/{org_id}",
                                                  "secret_token": secret, "allowed_updates": ["message"]})
            except PlatformError as e:
                cfg["telegram_webhook_error"] = str(e)[:300]
        else:
            cfg["telegram_secret"] = ""
    for k in ("discord_app_id", "discord_public_key"):
        if changes.get(k) is not None:
            v = str(changes[k]).strip()
            if v and not re.fullmatch(r"\d{5,25}" if k == "discord_app_id" else r"[0-9a-fA-F]{64}", v):
                raise PlatformError("id da aplicação do Discord: só números" if k == "discord_app_id"
                                    else "chave pública do Discord: 64 caracteres hexadecimais")
            cfg[k] = v
    if changes.get("discord_bot_token") is not None:
        v = str(changes["discord_bot_token"]).strip()
        cfg["discord_bot_token"] = crypto.encrypt(v) if v else ""
    if (changes.get("discord_bot_token") or changes.get("discord_app_id")) and cfg.get("discord_bot_token") and cfg.get("discord_app_id"):
        cfg["discord_command_error"] = ""
        try:  # o comando /hangar, com o pedido em texto livre
            _discord(cfg, "PUT", f"/applications/{cfg['discord_app_id']}/commands", [{
                "name": "hangar", "type": 1, "description": "Ask a Digital employee for work / Peça um trabalho",
                "options": [{"name": "text", "type": 3, "required": True,
                             "description": "Name of the Digital employee, then the request / Nome, depois o pedido"}]}])
        except PlatformError as e:
            cfg["discord_command_error"] = str(e)[:300]


# ------------------------------------------------------------------ ligar a conta da pessoa
def link_code(db, acc: Access, channel: str) -> dict:
    if channel not in ("telegram", "discord", "whatsapp"):
        raise PlatformError("channel: telegram | discord | whatsapp")
    u = notify._me(db, acc)
    cfg = notify.org_cfg(db)
    ready = {"telegram": bool(cfg.get("telegram_bot_token")), "discord": bool(cfg.get("discord_bot_token")),
             "whatsapp": bool(cfg.get("whatsapp_phone_id") and cfg.get("whatsapp_token"))}
    if not ready[channel]:
        raise PlatformError(f"o {channel.title()} ainda não está ligado na empresa: peça ao admin")
    code = secrets.token_hex(4).upper()
    shared.put(f"chatlink:{code}", {"user": u.id, "org": u.org_id or 1, "channel": channel}, LINK_TTL)
    out = {"code": code, "expires_in_s": LINK_TTL}
    if channel == "telegram":
        bot = cfg.get("telegram_bot", "")
        out["how"] = f"/start {code}"
        if bot:
            out["url"] = f"https://t.me/{bot}?start={code}"
    elif channel == "discord":
        out["how"] = f"/hangar text: link {code}"
    else:
        out["how"] = f"link {code}"
    return out


def unlink(db, acc: Access, channel: str) -> dict:
    u = notify._me(db, acc)
    p = dict(u.notify or {})
    p.pop(f"{channel}_id", None)
    u.notify = p
    db.commit()
    return notify.my_view(db, acc)


def _link(db, channel: str, code: str, chat_user: str, org_id: int) -> str:
    data = shared.get(f"chatlink:{code.strip().upper()}")
    if not data or data.get("channel") != channel or data.get("org") != org_id:
        return _both("badcode")
    shared.pop(f"chatlink:{code.strip().upper()}")
    for other in _people(db):  # uma conta de chat é de uma pessoa só
        if (other.notify or {}).get(f"{channel}_id") == chat_user:
            p = dict(other.notify)
            p.pop(f"{channel}_id")
            other.notify = p
    u = db.get(User, data["user"])
    p = dict(u.notify or {})
    p[f"{channel}_id"] = chat_user
    u.notify = p
    db.commit()
    audit(db, u.email, f"notify.{channel}.link", u.email, "")
    return _both("linked", ch=channel.title(), who=u.email)


def _people(db) -> list[User]:
    return list(db.scalars(select(User).where(User.notify.is_not(None))))


def person(db, channel: str, chat_user: str) -> User | None:
    for u in _people(db):
        if (u.notify or {}).get(f"{channel}_id") == chat_user:
            return u if u.active else None
    return None


def linked(u: User) -> dict:
    p = u.notify or {}
    return {"telegram": bool(p.get("telegram_id")), "discord": bool(p.get("discord_id")), "whatsapp": bool(p.get("whatsapp_id"))}


# ------------------------------------------------------------------ Telegram
def _telegram_raw(token: str, method: str, payload: dict) -> dict:
    with notify.HTTP() as h:
        r = h.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
    d = r.json() if r.content else {}
    if not d.get("ok"):
        raise PlatformError(f"Telegram {method}: {d.get('description') or r.status_code}")
    return d


def _telegram(cfg: dict, method: str, payload: dict) -> dict:
    tok = notify._secret(cfg, "telegram_bot_token")
    if not tok:
        raise PlatformError("Telegram não configurado")
    return _telegram_raw(tok, method, payload)


def _org(db, org_id: int) -> Organization:
    o = db.get(Organization, org_id)
    if o is None or o.status == "suspended":
        raise Forbidden("empresa desconhecida")
    tenancy.set_org(o.id)
    return o


def telegram_update(db, org_id: int, raw: bytes, headers: dict):
    o = _org(db, org_id)
    cfg = notify.org_cfg(db)
    secret = notify._secret(cfg, "telegram_secret")
    if not secret or not hmac.compare_digest(headers.get("x-telegram-bot-api-secret-token", ""), secret):
        raise Forbidden("assinatura do Telegram inválida")
    upd = json.loads(raw or b"{}")
    msg = upd.get("message") or {}
    if not msg.get("text") or (msg.get("from") or {}).get("is_bot"):
        return
    if not shared.take_once(f"telegram:{o.id}:{upd.get('update_id')}", 86400):  # o Telegram reenvia
        return
    notify._bg(_telegram_task, msg)  # responde já; o trabalho segue em segundo plano


def _telegram_task(db, msg: dict):
    from . import chat_tasks
    cfg = notify.org_cfg(db)
    chat, mid = msg["chat"]["id"], msg.get("message_id")
    who = str((msg.get("from") or {}).get("id", ""))
    text = msg["text"].strip()
    bot = cfg.get("telegram_bot", "")
    group = msg["chat"].get("type") in ("group", "supergroup")
    m = re.match(r"^/(\w+)(@\w+)?\s*(.*)$", text, re.S)
    if m and m.group(1) in ("start", "link"):
        reply = _link(db, "telegram", m.group(3), who, tenancy.current_org()) if m.group(3).strip() else \
            _both("nouser", ch="Telegram", how="/start CÓDIGO")
        return _telegram(cfg, "sendMessage", {"chat_id": chat, "text": reply})
    if m and m.group(1) == "hangar":
        text = m.group(3)
    elif bot and f"@{bot}".lower() in text.lower():
        text = re.sub(rf"@{re.escape(bot)}", "", text, flags=re.I).strip()
    elif group:
        return  # num grupo, só o que é para o bot
    u = person(db, "telegram", who)
    if u is None:
        return _telegram(cfg, "sendMessage", {"chat_id": chat, "text": _both("nouser", ch="Telegram", how="/start CÓDIGO")})
    try:
        out = chat_tasks.create_from_chat(db, u, text, "telegram", {"telegram": {"chat": chat, "message": mid}})
    except (PlatformError, Forbidden) as e:
        out = {"text": str(e)}
    _telegram(cfg, "sendMessage", {"chat_id": chat, "text": out["text"][:4000], "reply_to_message_id": mid})
    if out.get("task_id"):
        audit(db, u.email, "employee.task.chat", "telegram", f"#{out['task_id']}")


# ------------------------------------------------------------------ Discord
def _discord(cfg: dict, method: str, path: str, payload=None) -> dict | list:
    tok = notify._secret(cfg, "discord_bot_token")
    if not tok:
        raise PlatformError("Discord não configurado")
    with notify.HTTP() as h:
        r = h.request(method, DISCORD_API + path, json=payload, headers={"Authorization": f"Bot {tok}"})
    if r.status_code >= 400:
        raise PlatformError(f"Discord {method} {path.split('/')[1]}: {r.status_code} {r.text[:200]}")
    return r.json() if r.content else {}


def _verify_discord(cfg: dict, raw: bytes, headers: dict):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key, sig, ts = cfg.get("discord_public_key", ""), headers.get("x-signature-ed25519", ""), headers.get("x-signature-timestamp", "")
    if not (key and sig and ts):
        raise Forbidden("assinatura do Discord ausente")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(bytes.fromhex(sig), ts.encode() + raw)
    except (InvalidSignature, ValueError):
        raise Forbidden("assinatura do Discord inválida") from None


def discord_interaction(db, org_id: int, raw: bytes, headers: dict) -> dict:
    from . import chat_tasks
    _org(db, org_id)
    cfg = notify.org_cfg(db)
    _verify_discord(cfg, raw, headers)
    body = json.loads(raw or b"{}")
    if body.get("type") == 1:  # PING do Discord ao cadastrar a URL
        return {"type": 1}
    if body.get("type") != 2:
        return {"type": 4, "data": {"content": "?", "flags": 64}}
    who = str(((body.get("member") or {}).get("user") or body.get("user") or {}).get("id", ""))
    text = next((o.get("value", "") for o in (body.get("data") or {}).get("options") or [] if o.get("name") == "text"), "")
    text = str(text).strip()
    m = re.match(r"^link\s+(\S+)$", text, re.I)
    if m:  # só quem mandou vê (ephemeral)
        return {"type": 4, "data": {"content": _link(db, "discord", m.group(1), who, tenancy.current_org()), "flags": 64}}
    u = person(db, "discord", who)
    if u is None:
        return {"type": 4, "data": {"content": _both("nouser", ch="Discord", how="/hangar text: link CÓDIGO"), "flags": 64}}
    try:
        out = chat_tasks.create_from_chat(db, u, text, "discord", {"discord": {"channel": body.get("channel_id", "")}})
    except (PlatformError, Forbidden) as e:
        out = {"text": str(e)}
    if out.get("task_id"):
        audit(db, u.email, "employee.task.chat", "discord", f"#{out['task_id']}")
    return {"type": 4, "data": {"content": out["text"][:1900], "allowed_mentions": {"parse": []}}}


# ------------------------------------------------------------------ o resultado volta para a conversa
def deliver(db, cfg: dict, origin: dict, head: str, text: str, url: str, label: str) -> bool:
    from . import correspondence
    if correspondence.deliver_result(db, cfg, origin, head, text, url):
        return True
    if origin.get("telegram"):
        o = origin["telegram"]
        _telegram(cfg, "sendMessage", {"chat_id": o["chat"], "text": f"{head}\n{text[:3500]}\n{url}",
                                       "reply_to_message_id": o.get("message")})
        return True
    if origin.get("discord") and origin["discord"].get("channel"):
        _discord(cfg, "POST", f"/channels/{origin['discord']['channel']}/messages",
                 {"content": f"**{head}**\n{text[:1700]}\n{label}: <{url}>", "allowed_mentions": {"parse": []}})
        return True
    return False
