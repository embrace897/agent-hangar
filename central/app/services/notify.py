"""Decisões fora do portal, resumo diário, alerta antes de expirar e prazos de tarefa.

Cada pessoa escolhe onde recebe (users.notify.channel; "auto" usa o primeiro disponível, nesta ordem):
- slack: mensagem direta do app do Slack da empresa, com botões na própria mensagem (aprovar, editar, recusar,
  responder). O clique chega em /hooks/slack/interactions assinado pelo Slack, e a decisão é tomada como a pessoa
  dona daquele e-mail no hangar (as mesmas regras do portal: quem pode decidir, quatro olhos, separação de funções).
- teams: cartão num webhook pessoal (Workflows do Teams), com botões que abrem um link assinado.
- email: SMTP, com os mesmos botões de link assinado.
O link assinado vale para UM pedido e UMA pessoa, expira (DECISION_LINK_TTL_H) e só decide por POST: abrir o link
(ou o antivírus do e-mail abrir) não decide nada. Os envios rodam em segundo plano e nunca derrubam o fluxo.
"""
import base64
import hashlib
import hmac
import html
import json
import logging
import smtplib
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.message import EmailMessage

import httpx
from sqlalchemy import select

from .. import config, crypto
from ..cron import zone
from ..db import SessionLocal
from ..models import Agent, Employee, EmployeeTask, HumanRequest, Organization, User, now
from . import employee_tasks as tasksmod
from .access import Access, Forbidden, principal_for
from .common import PlatformError, audit, iso

log = logging.getLogger("hangar.notify")
CHANNELS = ("auto", "slack", "teams", "email", "off")
DEFAULTS = {"digest_hour": 9, "expiry_warn_min": 60, "overdue_escalate_min": 120, "due_soon_min": 60}
LIMITS = {"digest_hour": (-1, 23), "expiry_warn_min": (0, 1440), "overdue_escalate_min": (5, 10080), "due_soon_min": (0, 1440)}
OPEN_TASK = ("new", "in_progress", "waiting_human")
HTTP = lambda: httpx.Client(timeout=15)  # noqa: E731  (os testes trocam)


def SMTP():  # noqa: N802  (os testes trocam)
    cls = smtplib.SMTP_SSL if config.SMTP_SECURITY == "ssl" else smtplib.SMTP
    return cls(config.SMTP_HOST, config.SMTP_PORT, timeout=20)


_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")


def _run(fn, *args):
    try:
        with SessionLocal() as db:
            fn(db, *args)
    except Exception:  # noqa: BLE001 — aviso nunca derruba o fluxo
        log.exception("falha ao enviar aviso (%s)", getattr(fn, "__name__", fn))


def _bg(fn, *args):
    """Envia em segundo plano (os testes trocam por execução direta)."""
    _pool.submit(_run, fn, *args)


# ------------------------------------------------------------------ configuração (empresa e pessoa)
def org_cfg(db) -> dict:
    o = db.get(Organization, 1)
    return {**DEFAULTS, **((o.notify if o else None) or {})}


def _secret(cfg: dict, key: str) -> str:
    try:
        return crypto.decrypt(cfg.get(key) or "")
    except RuntimeError:
        return ""


def slack_ready(cfg: dict) -> bool:
    return bool(cfg.get("slack_bot_token") and cfg.get("slack_signing_secret"))


def email_ready() -> bool:
    return bool(config.SMTP_HOST)


def prefs(u: User) -> dict:
    return {"channel": "auto", "digest": True, **(u.notify or {})}


def available(cfg: dict, p: dict) -> list[str]:
    return [c for c, ok in (("slack", slack_ready(cfg)), ("teams", bool(p.get("teams_webhook"))),
                            ("email", email_ready())) if ok]


def route(cfg: dict, u: User) -> str | None:
    """Canal efetivo da pessoa: o escolhido, se estiver disponível; senão o primeiro disponível; "off" desliga."""
    p = prefs(u)
    if p["channel"] == "off":
        return None
    avail = available(cfg, p)
    if p["channel"] in avail:
        return p["channel"]
    return avail[0] if avail else None


def _hint(v: str) -> str:
    return f"…{v[-4:]}" if v else ""


def slack_manifest() -> str:
    return (f"display_information:\n  name: Agent Hangar\n  description: Ask your Digital employees for work and decide "
            f"what they ask, right in Slack\nfeatures:\n  app_home:\n    messages_tab_enabled: true\n"
            f"    messages_tab_read_only_enabled: false\n  bot_user:\n    display_name: Agent Hangar\n"
            f"    always_online: true\noauth_config:\n  scopes:\n    bot:\n      - app_mentions:read\n      - chat:write\n"
            f"      - im:history\n      - im:write\n      - users:read\n      - users:read.email\nsettings:\n"
            f"  event_subscriptions:\n    request_url: {config.PUBLIC_BASE_URL}/hooks/slack/events\n    bot_events:\n"
            f"      - app_mention\n      - message.im\n  interactivity:\n    is_enabled: true\n"
            f"    request_url: {config.PUBLIC_BASE_URL}/hooks/slack/interactions\n")


def admin_view(db, acc: Access) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin configura os canais de decisão da empresa")
    cfg = org_cfg(db)
    return {"slack": {"configured": slack_ready(cfg), "bot_token": _hint(_secret(cfg, "slack_bot_token")),
                      "signing_secret": bool(cfg.get("slack_signing_secret")), "team": cfg.get("slack_team", ""),
                      "interactions_url": f"{config.PUBLIC_BASE_URL}/hooks/slack/interactions",
                      "events_url": f"{config.PUBLIC_BASE_URL}/hooks/slack/events",
                      "manifest": slack_manifest()},
            "email": {"configured": email_ready(), "host": config.SMTP_HOST, "from": config.SMTP_FROM},
            "teams": {"how": "personal", "outgoing": bool(cfg.get("teams_outgoing_secret")),
                      "outgoing_url": f"{config.PUBLIC_BASE_URL}/hooks/teams/messages"},
            **{k: cfg[k] for k in DEFAULTS}}


def set_admin(db, acc: Access, changes: dict) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin configura os canais de decisão da empresa")
    o = db.get(Organization, 1)
    cfg = dict((o.notify if o else None) or {})
    for k, (lo, hi) in LIMITS.items():
        if changes.get(k) is not None:
            try:
                v = int(changes[k])
            except (TypeError, ValueError):
                raise PlatformError(f"{k} precisa ser um número") from None
            if not lo <= v <= hi:
                raise PlatformError(f"{k}: entre {lo} e {hi}")
            cfg[k] = v
    for k in ("slack_bot_token", "slack_signing_secret"):
        if changes.get(k) is not None:
            v = str(changes[k]).strip()
            if k == "slack_bot_token" and v and not v.startswith("xoxb-"):
                raise PlatformError("o token do bot do Slack começa com xoxb-")
            cfg[k] = crypto.encrypt(v) if v else ""
    if changes.get("teams_outgoing_secret") is not None:  # o token de segurança do webhook de saída do Teams
        v = str(changes["teams_outgoing_secret"]).strip()
        if v:
            try:
                base64.b64decode(v, validate=True)
            except ValueError:
                raise PlatformError("o token de segurança do Teams é o texto em base64 que o Teams mostra ao criar o "
                                    "webhook de saída") from None
        cfg["teams_outgoing_secret"] = crypto.encrypt(v) if v else ""
    if changes.get("slack_bot_token"):
        try:
            who = _slack(cfg, "auth.test", {}, form=True)
            cfg["slack_team"] = who.get("team", "")
        except PlatformError as e:
            raise PlatformError(f"o Slack recusou o token: {e}") from None
    o.notify = cfg
    db.commit()
    audit(db, acc.p.name, "notify.settings", "org", ", ".join(sorted(k for k, v in changes.items() if v is not None)))
    return admin_view(db, acc)


def _me(db, acc: Access) -> User:
    u = db.get(User, acc.p.user_id) if acc.p.user_id else None
    if not u:
        raise Forbidden("entre com o seu usuário para escolher onde recebe as decisões")
    return u


def my_view(db, acc: Access) -> dict:
    u = _me(db, acc)
    cfg, p = org_cfg(db), prefs(u)
    return {"channel": p["channel"], "digest": bool(p["digest"]), "teams_webhook": bool(p.get("teams_webhook")),
            "available": available(cfg, p), "route": route(cfg, u), "digest_hour": cfg["digest_hour"],
            "slack_ready": slack_ready(cfg), "email_ready": email_ready(), "email": u.email}


def set_mine(db, acc: Access, changes: dict) -> dict:
    from .schedules import check_webhook
    u = _me(db, acc)
    p = dict(u.notify or {})
    if changes.get("channel") is not None:
        if changes["channel"] not in CHANNELS:
            raise PlatformError(f"channel = {' | '.join(CHANNELS)}")
        p["channel"] = changes["channel"]
    if changes.get("digest") is not None:
        p["digest"] = bool(changes["digest"])
    if changes.get("teams_webhook") is not None:
        url = check_webhook(str(changes["teams_webhook"]).strip())
        p["teams_webhook"] = crypto.encrypt(url) if url else ""
    u.notify = p
    db.commit()
    return my_view(db, acc)


def test_mine(db, acc: Access) -> dict:
    u = _me(db, acc)
    cfg = org_cfg(db)
    via = route(cfg, u)
    if not via:
        raise PlatformError("nenhum canal disponível: peça ao admin para ligar o Slack ou o e-mail, ou cadastre o seu "
                            "webhook do Teams")
    _send_text(db, cfg, u, via, "Agent Hangar", ["Teste: é por aqui que você vai receber as decisões dos seus Digital "
                                                 "employees. / Test: this is where your Digital employees' decisions "
                                                 "will reach you."], f"{config.PUBLIC_BASE_URL}/app/#/decisions")
    return {"sent": True, "via": via}


# ------------------------------------------------------------------ link assinado
def _key() -> bytes:
    return hashlib.sha256(b"hangar-decision-link:" + (config.SECRET_KEY or config.INTERNAL_SECRET or "").encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign(request_id: int, user_id: int, ttl_h: int | None = None) -> str:
    body = f"{request_id}.{user_id}.{int(time.time() + 3600 * (ttl_h or config.DECISION_LINK_TTL_H))}"
    return f"{body}.{_b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest()[:18])}"


def verify(token: str) -> tuple[int, int]:
    try:
        rid, uid, exp, mac = token.split(".")
        good = _b64(hmac.new(_key(), f"{rid}.{uid}.{exp}".encode(), hashlib.sha256).digest()[:18])
        ok = hmac.compare_digest(mac, good)
    except ValueError:
        ok = False
    if not ok:
        raise Forbidden("link inválido")
    if int(exp) < time.time():
        raise Forbidden("link vencido: decida pelo portal")
    return int(rid), int(uid)


def link(request_id: int, user_id: int, d: str = "") -> str:
    return f"{config.PUBLIC_BASE_URL}/hooks/decide/{sign(request_id, user_id)}" + (f"?d={d}" if d else "")


def portal(request_id: int | None = None) -> str:
    return f"{config.PUBLIC_BASE_URL}/app/#/" + (f"tasks/{request_id}" if request_id else "decisions")


# ------------------------------------------------------------------ envio
def _slack(cfg: dict, method: str, payload: dict, form: bool = False) -> dict:
    token = _secret(cfg, "slack_bot_token")
    if not token:
        raise PlatformError("Slack não configurado")
    with HTTP() as h:
        kw = {"data": payload} if form else {"json": payload}
        r = h.post(f"https://slack.com/api/{method}", headers={"Authorization": f"Bearer {token}"}, **kw)
    d = r.json()
    if not d.get("ok"):
        raise PlatformError(f"Slack {method}: {d.get('error', r.status_code)}")
    return d


def slack_user(db, cfg: dict, u: User) -> str:
    p = dict(u.notify or {})
    if not p.get("slack_id"):
        p["slack_id"] = _slack(cfg, "users.lookupByEmail", {"email": u.email}, form=True)["user"]["id"]
        u.notify = p
        db.commit()
    return p["slack_id"]


def _teams(u: User, card: dict):
    from .schedules import HTTP as SHTTP
    from .schedules import check_webhook
    url = check_webhook(_secret(prefs(u), "teams_webhook"))
    with SHTTP() as h:
        r = h.post(url, json={"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]})
    if r.status_code >= 400:
        raise PlatformError(f"Teams respondeu {r.status_code}")


def _email(to: str, subject: str, text: str, body_html: str):
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, config.SMTP_FROM, to
    m.set_content(text)
    m.add_alternative(body_html, subtype="html")
    with SMTP() as s:
        if config.SMTP_SECURITY == "starttls":
            s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(m)


def _card(title: str, lines: list[str], actions: list[tuple[str, str]], code: str = "") -> dict:
    body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
            *({"type": "TextBlock", "text": x, "wrap": True, "spacing": "Small"} for x in lines)]
    if code:
        body.append({"type": "TextBlock", "text": code, "fontType": "Monospace", "wrap": True, "spacing": "Medium"})
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4",
            "body": body, "actions": [{"type": "Action.OpenUrl", "title": t, "url": url} for t, url in actions]}


def _mail_html(title: str, lines: list[str], actions: list[tuple[str, str]], code: str = "") -> str:
    btn = "".join(f'<a href="{html.escape(url)}" style="display:inline-block;margin:4px 6px 4px 0;padding:9px 16px;'
                  f'border-radius:6px;background:{"#1f6feb" if i == 0 else "#57606a"};color:#fff;text-decoration:none;'
                  f'font-weight:600">{html.escape(t)}</a>' for i, (t, url) in enumerate(actions))
    pre = (f'<pre style="background:#f6f8fa;padding:10px;border-radius:6px;white-space:pre-wrap">{html.escape(code)}</pre>'
           if code else "")
    return (f'<div style="font-family:system-ui,sans-serif;font-size:14px;color:#1f2328;max-width:620px">'
            f'<h2 style="font-size:17px">{html.escape(title)}</h2>'
            + "".join(f"<p style='margin:6px 0'>{html.escape(x)}</p>" for x in lines) + pre + f"<p>{btn}</p></div>")


def _send_text(db, cfg: dict, u: User, via: str, title: str, lines: list[str], url: str, label: str = "Agent Hangar"):
    if via == "slack":
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n" + "\n".join(lines)[:2900]}},
                  {"type": "actions", "elements": [{"type": "button", "action_id": "hangar:open", "url": url,
                                                    "text": {"type": "plain_text", "text": label}}]}]
        _slack(cfg, "chat.postMessage", {"channel": slack_user(db, cfg, u), "text": title, "blocks": blocks})
    elif via == "teams":
        _teams(u, _card(title, lines, [(label, url)]))
    elif via == "email":
        _email(u.email, title, "\n".join([*lines, url]), _mail_html(title, lines, [(label, url)]))


# ------------------------------------------------------------------ conteúdo de um pedido
L = {
    "pt": {"approve": "Aprovar", "edit": "Editar", "reject": "Recusar", "answer": "Responder", "open": "Abrir no portal",
           "approval": "quer fazer uma ação que precisa da sua aprovação", "question": "tem uma pergunta para você",
           "admission": "terminou a experiência: admitir?", "capability": "precisa de uma capacidade que não tem",
           "notice": "fez uma ação e avisa", "plan": "propõe um plano para a tarefa", "why": "Motivo", "task": "Tarefa", "expires": "Expira",
           "escalated": "Escalado para você (sem resposta no prazo)", "expiring": "Vai expirar em breve",
           "two": "Exige duas aprovações", "approved": "Aprovado por", "approve_edited": "Aprovado com edição por",
           "rejected": "Recusado por", "answered": "Respondido por", "instructed": "Instruído por",
           "expired": "Expirou sem resposta", "first": "1 de 2 aprovações:", "risk": "risco",
           "decided": "Decidido por", "cancelled": "Cancelado", "via_portal": "Decida pelo portal"},
    "en": {"approve": "Approve", "edit": "Edit", "reject": "Reject", "answer": "Answer", "open": "Open in the portal",
           "approval": "wants to take an action that needs your approval", "question": "has a question for you",
           "admission": "finished probation: hire?", "capability": "needs a capability it doesn't have",
           "notice": "took an action and lets you know", "plan": "proposes a plan for the task", "why": "Reason", "task": "Task", "expires": "Expires",
           "escalated": "Escalated to you (no answer in time)", "expiring": "Expires soon",
           "two": "Needs two approvals", "approved": "Approved by", "approve_edited": "Approved with edits by",
           "rejected": "Rejected by", "answered": "Answered by", "instructed": "Instructed by",
           "expired": "Expired without an answer", "first": "1 of 2 approvals:", "risk": "risk",
           "decided": "Decided by", "cancelled": "Cancelled", "via_portal": "Decide in the portal"},
}


def verbs(r: HumanRequest) -> list[str]:
    """Botões que a mensagem oferece: o resto (capacidade, aviso) é decidido no portal."""
    return {"approval": ["approve", "edit", "reject"], "question": ["answer"],
            "admission": ["approve", "reject"], "plan": ["approve", "reject"]}.get(r.kind, [])


def _who(db, e: Employee) -> tuple[str, str]:
    a = db.get(Agent, e.agent_id)
    return (a.name if a else e.title), (a.slug if a else "")


def _local(db, dt) -> str:
    from .schedules import org_timezone
    return tasksmod._aware(dt).astimezone(zone(org_timezone(db))).strftime("%Y-%m-%d %H:%M")


def content(db, r: HumanRequest, why: str = "new") -> dict:
    e = db.get(Employee, r.employee_id)
    lang = tasksmod.lang_of(e)
    t = L[lang]
    name, _ = _who(db, e)
    task = db.get(EmployeeTask, r.task_id) if r.task_id else None
    who = name if name.strip().lower() == (e.title or "").strip().lower() else f"{name} ({e.title})"
    title = f"{who} {t.get(r.kind, t['approval'])} — #{r.id}"
    lines = []
    if why in ("escalated", "expiring"):
        lines.append(("⏰ " if why == "expiring" else "↪ ") + t[why])
    if task:
        lines.append(f"{t['task']}: {task.title}" + (f" ({task.requester})" if task.requester else ""))
    code = ""
    if r.kind == "approval":
        lines.append(f"{r.tool_name} · {r.action_type} · {t['risk']} {r.risk}")
        code = json.dumps(r.edited_payload or r.action_payload, ensure_ascii=False, indent=2)[:2500]
        if r.mode == "approve_2":
            lines.append(t["two"] + (f" — {t['first']} {r.approvals[0]['by']}" if r.approvals else ""))
    elif r.kind == "question":
        lines.append(r.question[:2000])
    elif r.kind == "capability":
        lines.append(str((r.action_payload or {}).get("need", ""))[:500])
    elif r.kind == "plan":
        code = "\n".join(f"{i + 1}. {s}" for i, s in enumerate((r.edited_payload or r.action_payload or {}).get("steps", [])))
    if r.rationale and r.kind != "question":
        lines.append(f"{t['why']}: {r.rationale[:600]}")
    if r.expires_at and r.status == "open":
        lines.append(f"{t['expires']}: {_local(db, r.expires_at)}")
    return {"title": title, "lines": lines, "code": code, "lang": lang, "t": t, "task_id": task.id if task else None}


def outcome(r: HumanRequest, t: dict) -> str:
    if r.status == "open":
        return f"{t['first']} {r.approvals[0]['by']}" if r.approvals else ""
    if r.status == "expired":
        return "⌛ " + t["expired"]
    if r.status == "cancelled":
        return t["cancelled"]
    label = {"approve": t["approved"], "approve_edited": t["approve_edited"], "reject": t["rejected"],
             "answer": t["answered"], "instruct": t["instructed"]}.get(r.decision, t["decided"])
    mark = "✅" if r.decision in ("approve", "approve_edited", "answer") else "⛔" if r.decision == "reject" else "•"
    return f"{mark} {label} {r.decided_by}" + (f" — {r.reason[:300]}" if r.reason else "")


def _blocks(db, r: HumanRequest, c: dict) -> list[dict]:
    t = c["t"]
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{c['title']}*\n" + "\n".join(c["lines"])[:2800]}}]
    if c["code"]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{c['code'][:2800]}```"}})
    done = outcome(r, t)
    if done:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": done}]})
    if r.status == "open":
        style = {"approve": "primary", "reject": "danger", "answer": "primary"}
        els = []
        for v in verbs(r):
            b = {"type": "button", "action_id": f"hangar:{v}", "value": str(r.id), "text": {"type": "plain_text", "text": t[v]}}
            if v in style:
                b["style"] = style[v]
            els.append(b)
        els.append({"type": "button", "action_id": "hangar:open", "url": portal(c["task_id"]),
                    "text": {"type": "plain_text", "text": t["open"]}})
        blocks.append({"type": "actions", "block_id": f"hangar:{r.id}", "elements": els})
    return blocks


def _deliver_request(db, request_id: int, user_id: int, why: str):
    r = db.get(HumanRequest, request_id)
    u = db.get(User, user_id)
    if not r or r.status != "open" or not u or not u.active:
        return
    cfg = org_cfg(db)
    via = route(cfg, u)
    if not via:
        return
    c = content(db, r, why)
    if via == "slack":
        res = _slack(cfg, "chat.postMessage", {"channel": slack_user(db, cfg, u), "text": c["title"],
                                               "blocks": _blocks(db, r, c)})
        r.chat_refs = [*(r.chat_refs or []), {"channel": res["channel"], "ts": res["ts"], "user_id": u.id}]
        db.commit()
        return
    t = c["t"]
    actions = [(t[v], link(r.id, u.id, v)) for v in verbs(r)] or [(t["via_portal"], portal(c["task_id"]))]
    if verbs(r):
        actions.append((t["open"], portal(c["task_id"])))
    if via == "teams":
        _teams(u, _card(c["title"], c["lines"], actions, c["code"]))
    else:
        _email(u.email, c["title"], "\n".join([*c["lines"], c["code"], *(f"{a}: {b}" for a, b in actions)]),
               _mail_html(c["title"], c["lines"], actions, c["code"]))


def request_opened(r: HumanRequest, why: str = "new"):
    """Pedido novo, escalado ou prestes a expirar: vai para quem está com ele agora."""
    if r.kind in tasksmod.QUIET or not r.assigned_user_id:
        return
    _bg(_deliver_request, r.id, r.assigned_user_id, why)


def _refresh(db, request_id: int):
    r = db.get(HumanRequest, request_id)
    if not r or not r.chat_refs:
        return
    cfg = org_cfg(db)
    c = content(db, r)
    for ref in r.chat_refs:
        try:
            _slack(cfg, "chat.update", {"channel": ref["channel"], "ts": ref["ts"], "text": c["title"],
                                        "blocks": _blocks(db, r, c)})
        except PlatformError:
            log.warning("não consegui atualizar a mensagem do pedido #%s no Slack", r.id)


def request_changed(r: HumanRequest):
    """Decidido (no portal, no Slack ou por link) ou expirado: a mensagem no Slack mostra o desfecho e perde os botões."""
    if r.chat_refs:
        _bg(_refresh, r.id)


# ------------------------------------------------------------------ decidir fora do portal
def decide_as(db, u: User, request_id: int, decision: str, edit: dict | None, reason: str, via: str) -> dict:
    if not u or not u.active:
        raise Forbidden("usuário desativado")
    out = tasksmod.decide(db, Access(db, principal_for(u)), request_id, decision, edit, reason)
    r = db.get(HumanRequest, request_id)
    e = db.get(Employee, r.employee_id)
    audit(db, u.email, "employee.decision.channel", _who(db, e)[1], f"#{request_id} {decision} via {via}")
    return out


def _verify_slack(cfg: dict, raw: bytes, headers: dict):
    secret = _secret(cfg, "slack_signing_secret")
    ts, sig = headers.get("x-slack-request-timestamp", ""), headers.get("x-slack-signature", "")
    if not secret or not ts.isdigit() or abs(time.time() - int(ts)) > 300:
        raise Forbidden("assinatura do Slack ausente ou vencida")
    good = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        raise Forbidden("assinatura do Slack inválida")


def _slack_person(db, cfg: dict, slack_id: str) -> User | None:
    info = _slack(cfg, "users.info", {"user": slack_id}, form=True)["user"]
    email = ((info.get("profile") or {}).get("email") or "").strip().lower()
    u = db.scalar(select(User).where(User.email == email)) if email else None
    if u and (u.notify or {}).get("slack_id") != slack_id:
        u.notify = {**(u.notify or {}), "slack_id": slack_id}
        db.commit()
    return u


def _ephemeral(response_url: str, text: str):
    if not (response_url or "").startswith("https://hooks.slack.com/"):
        return
    with HTTP() as h:
        h.post(response_url, json={"response_type": "ephemeral", "replace_original": False, "text": text})


def _modal(r: HumanRequest, verb: str, t: dict, meta: dict) -> dict:
    blocks = []
    if verb == "edit":
        blocks.append({"type": "input", "block_id": "payload", "label": {"type": "plain_text", "text": "JSON"},
                       "element": {"type": "plain_text_input", "action_id": "v", "multiline": True,
                                   "initial_value": json.dumps(r.edited_payload or r.action_payload, ensure_ascii=False,
                                                               indent=2)[:2900]}})
    label = t["answer"] if verb == "answer" else t["why"]
    blocks.append({"type": "input", "block_id": "reason", "optional": verb == "edit" or (verb == "reject" and r.kind != "plan"),
                   "label": {"type": "plain_text", "text": label},
                   "element": {"type": "plain_text_input", "action_id": "v", "multiline": True}})
    return {"type": "modal", "callback_id": f"hangar:{verb}", "private_metadata": json.dumps(meta),
            "title": {"type": "plain_text", "text": f"{t[verb]} #{r.id}"[:24]},
            "submit": {"type": "plain_text", "text": t[verb][:24]}, "close": {"type": "plain_text", "text": "×"},
            "blocks": blocks}


SLACK_DECISION = {"approve": "approve", "reject": "reject", "edit": "approve_edited", "answer": "answer"}


def slack_interaction(db, raw: bytes, headers: dict) -> dict | None:
    """Clique num botão ou envio de um modal no Slack. Devolve o corpo da resposta (ou None = 200 vazio)."""
    cfg = org_cfg(db)
    _verify_slack(cfg, raw, headers)
    form = urllib.parse.parse_qs(raw.decode())
    payload = json.loads((form.get("payload") or ["{}"])[0])
    kind = payload.get("type")
    if kind == "block_actions":
        act = (payload.get("actions") or [{}])[0]
        verb = (act.get("action_id") or "").removeprefix("hangar:")
        if verb not in SLACK_DECISION:
            return None
        rid, resp = int(act.get("value") or 0), payload.get("response_url", "")
        u = _slack_person(db, cfg, payload["user"]["id"])
        r = db.get(HumanRequest, rid)
        if not u or not r:
            _ephemeral(resp, "Seu e-mail do Slack não corresponde a um usuário do Agent Hangar. / Your Slack e-mail "
                             "doesn't match an Agent Hangar user.")
            return None
        if verb == "approve":
            try:
                decide_as(db, u, rid, "approve", None, "", "slack")
            except PlatformError as e:
                _ephemeral(resp, f"#{rid}: {e}")
                return None
            request_changed(db.get(HumanRequest, rid))
            return None
        t = L[tasksmod.lang_of(db.get(Employee, r.employee_id))]
        meta = {"r": rid, "resp": resp}
        _slack(cfg, "views.open", {"trigger_id": payload.get("trigger_id"), "view": _modal(r, verb, t, meta)})
        return None
    if kind == "view_submission":
        view = payload.get("view") or {}
        verb = (view.get("callback_id") or "").removeprefix("hangar:")
        if verb not in SLACK_DECISION:
            return None
        meta = json.loads(view.get("private_metadata") or "{}")
        vals = (view.get("state") or {}).get("values") or {}
        reason = ((vals.get("reason") or {}).get("v") or {}).get("value") or ""
        edit = None
        if verb == "edit":
            try:
                edit = json.loads(((vals.get("payload") or {}).get("v") or {}).get("value") or "")
                assert isinstance(edit, dict)
            except (ValueError, AssertionError):
                return {"response_action": "errors", "errors": {"payload": "JSON inválido (um objeto) / invalid JSON"}}
        u = _slack_person(db, cfg, payload["user"]["id"])
        if not u:
            return {"response_action": "errors", "errors": {"reason": "usuário não encontrado no Agent Hangar"}}
        try:
            decide_as(db, u, int(meta.get("r") or 0), SLACK_DECISION[verb], edit, reason, "slack")
        except PlatformError as e:
            return {"response_action": "errors", "errors": {"payload" if verb == "edit" else "reason": str(e)[:150]}}
        request_changed(db.get(HumanRequest, int(meta["r"])))
        return None
    return None


# ------------------------------------------------------------------ página do link assinado (Teams, e-mail)
PAGE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1"><meta name="referrer" content="no-referrer"><title>Agent Hangar — #{rid}</title><style>
body{{font-family:system-ui,sans-serif;background:#f6f8fa;color:#1f2328;margin:0;padding:24px 16px}}
.c{{max-width:640px;margin:auto;background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:20px}}
h1{{font-size:18px;margin:0 0 10px}} p{{margin:6px 0}} pre,textarea{{width:100%;box-sizing:border-box;font:13px ui-monospace,monospace;
background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;padding:10px;white-space:pre-wrap}}
textarea{{min-height:70px}} form{{margin-top:14px;padding-top:12px;border-top:1px solid #eaeef2}}
button{{font-weight:600;border:0;border-radius:6px;padding:9px 16px;color:#fff;background:#1f6feb;cursor:pointer}}
button.r{{background:#cf222e}} button.g{{background:#57606a}} .m{{color:#57606a;font-size:13px}} .on{{outline:2px solid #fd8c73;border-radius:8px;padding:0 10px 10px}}
@media (prefers-color-scheme:dark){{body{{background:#0d1117;color:#e6edf3}}.c{{background:#161b22;border-color:#30363d}}
pre,textarea{{background:#0d1117;color:#e6edf3;border-color:#30363d}}.m{{color:#8b949e}}}}</style></head><body><div class="c">
{body}</div></body></html>"""


def _page(lang: str, rid, body: str) -> str:
    return PAGE.format(lang="en" if lang == "en" else "pt-BR", rid=rid, body=body)


def link_page(db, token: str, d: str = "") -> str:
    rid, uid = verify(token)
    r = db.get(HumanRequest, rid)
    if not r:
        raise PlatformError("pedido não existe")
    c = content(db, r)
    t, esc = c["t"], html.escape
    head = (f"<h1>{esc(c['title'])}</h1>" + "".join(f"<p>{esc(x)}</p>" for x in c["lines"])
            + (f"<pre>{esc(c['code'])}</pre>" if c["code"] else ""))
    if r.status != "open" or not verbs(r):
        done = outcome(r, t) or t["via_portal"]
        return _page(c["lang"], rid, head + f"<p><b>{esc(done)}</b></p><p><a href='{portal(c['task_id'])}'>{t['open']}</a></p>")
    forms = []
    hi = lambda v: ' class="on"' if d == v else ""  # noqa: E731
    if "approve" in verbs(r):
        forms.append(f'<form method="post"{hi("approve")}><input type="hidden" name="decision" value="approve">'
                     f'<button>{t["approve"]}</button></form>')
    if "edit" in verbs(r):
        forms.append(f'<form method="post"{hi("edit")}><input type="hidden" name="decision" value="approve_edited">'
                     f'<p class="m">{t["edit"]} (JSON)</p><textarea name="edit">{esc(c["code"])}</textarea>'
                     f'<p class="m">{t["why"]}</p><textarea name="reason"></textarea><p><button>{t["edit"]} + '
                     f'{t["approve"]}</button></p></form>')
    if "reject" in verbs(r):
        forms.append(f'<form method="post"{hi("reject")}><input type="hidden" name="decision" value="reject">'
                     f'<p class="m">{t["why"]}</p><textarea name="reason"></textarea><p><button class="r">{t["reject"]}'
                     f'</button></p></form>')
    if "answer" in verbs(r):
        forms.append(f'<form method="post"{hi("answer")}><input type="hidden" name="decision" value="answer">'
                     f'<textarea name="reason" required></textarea><p><button>{t["answer"]}</button></p></form>')
    u = db.get(User, uid)
    who = f"<p class='m'>{esc(u.email if u else '')} · <a href='{portal(c['task_id'])}'>{t['open']}</a></p>"
    return _page(c["lang"], rid, head + who + "".join(forms))


def link_post(db, token: str, form: dict) -> str:
    rid, uid = verify(token)
    decision = (form.get("decision") or "").strip()
    if decision not in ("approve", "approve_edited", "reject", "answer"):
        raise PlatformError("decisão inválida")
    edit = None
    if decision == "approve_edited":
        try:
            edit = json.loads(form.get("edit") or "")
        except ValueError:
            raise PlatformError("JSON inválido na edição") from None
    decide_as(db, db.get(User, uid), rid, decision, edit, (form.get("reason") or "").strip(), "link")
    r = db.get(HumanRequest, rid)
    request_changed(r)
    return link_page(db, token)


# ------------------------------------------------------------------ varredura (a cada tick do executor)
def sweep(db) -> dict:
    out = {"reminded": 0, "digests": 0, **due_sweep(db)}
    cfg = org_cfg(db)
    t = now()
    warn = timedelta(minutes=cfg["expiry_warn_min"])
    if cfg["expiry_warn_min"]:
        for r in db.scalars(select(HumanRequest).where(HumanRequest.status == "open", HumanRequest.reminded.is_(False),
                                                       HumanRequest.kind.in_(tasksmod.BLOCKING),
                                                       HumanRequest.expires_at.is_not(None),
                                                       HumanRequest.expires_at <= t + warn)).all():
            left = tasksmod._aware(r.expires_at) - t
            span = tasksmod._aware(r.expires_at) - tasksmod._aware(r.created_at)
            if left <= timedelta(0) or left > span / 2:  # vencido (o sweep escala) ou prazo curto demais para avisar
                continue
            r.reminded = True
            db.commit()
            request_opened(r, "expiring")
            out["reminded"] += 1
    out["digests"] = digests(db, cfg)
    return out


def _local_now(db):
    from .schedules import org_timezone
    return now().astimezone(zone(org_timezone(db)))


def digests(db, cfg: dict | None = None) -> int:
    """Resumo diário de pendências, na hora configurada (fuso da empresa), uma vez por dia por pessoa."""
    cfg = cfg or org_cfg(db)
    hour = cfg["digest_hour"]
    local = _local_now(db)
    if hour < 0 or local.hour < hour:
        return 0
    today = local.date().isoformat()
    ids = set(db.scalars(select(HumanRequest.assigned_user_id).where(
        HumanRequest.status == "open", HumanRequest.kind.not_in(("shadow",)), HumanRequest.assigned_user_id.is_not(None))))
    late = db.execute(select(EmployeeTask, Employee).join(Employee, Employee.id == EmployeeTask.employee_id).where(
        EmployeeTask.status.in_(OPEN_TASK), EmployeeTask.due_state.in_(("overdue", "escalated")))).all()
    ids |= {e.manager_user_id for _, e in late}
    sent = 0
    for u in db.scalars(select(User).where(User.id.in_(ids), User.active.is_(True))).all():
        p = prefs(u)
        if not p["digest"] or p.get("last_digest") == today:
            continue
        via = route(cfg, u)
        if not via:
            continue  # sem canal: se ligar um mais tarde no mesmo dia, ainda recebe o resumo
        u.notify = {**(u.notify or {}), "last_digest": today}
        db.commit()
        _bg(_deliver_digest, u.id, via)
        sent += 1
    return sent


def _deliver_digest(db, user_id: int, via: str):
    u = db.get(User, user_id)
    pending = tasksmod.pending_for(db, Access(db, principal_for(u)))
    blocking = [d for d in pending if d["kind"] != "notice"]
    notices = len(pending) - len(blocking)
    late = [(tk, e) for tk, e in db.execute(select(EmployeeTask, Employee).join(
        Employee, Employee.id == EmployeeTask.employee_id).where(
        EmployeeTask.status.in_(OPEN_TASK), EmployeeTask.due_state.in_(("overdue", "escalated")),
        Employee.manager_user_id == u.id)).all()]
    if not blocking and not notices and not late:
        return
    en = any(tasksmod.lang_of(db.get(Employee, tk.employee_id)) == "en" for tk, _ in late) or (
        not late and _lang_of_pending(db, blocking))
    lines = []
    if blocking:
        mine = sum(1 for d in blocking if d["mine"])
        lines.append(f"{len(blocking)} decision(s) waiting ({mine} assigned to you):" if en else
                      f"{len(blocking)} decisão(ões) aguardando ({mine} atribuída(s) a você):")
        for d in blocking[:10]:
            exp = f" — {'expires' if en else 'expira'} {_local(db, _parse(d['expires_at']))}" if d.get("expires_at") else ""
            what = d.get("question") if d["kind"] == "question" else (d.get("tool") or d["kind"])
            lines.append(f"• #{d['id']} {d.get('employee_name') or d['employee']}: {str(what)[:120]}{exp}")
    if notices:
        lines.append(f"{notices} notice(s) to read." if en else f"{notices} aviso(s) para ler.")
    if late:
        lines.append(f"{len(late)} overdue task(s):" if en else f"{len(late)} tarefa(s) atrasada(s):")
        for tk, e in late[:10]:
            lines.append(f"• {_who(db, e)[0]}: {tk.title} ({'due' if en else 'prazo'} {_local(db, tk.due_at)})")
    title = "Agent Hangar — your pending items today" if en else "Agent Hangar — suas pendências de hoje"
    _send_text(db, org_cfg(db), u, via, title, lines, portal(), "Open decisions" if en else "Abrir decisões")


def _parse(s: str):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _lang_of_pending(db, items: list[dict]) -> bool:
    for d in items:
        r = db.get(HumanRequest, d["id"])
        e = db.get(Employee, r.employee_id) if r else None
        if e:
            return tasksmod.lang_of(e) == "en"
    return False


# ------------------------------------------------------------------ prazos de tarefa
def _people(db, e: Employee, task: EmployeeTask, stage: str) -> set[int]:
    if stage == "escalated":
        return {*(e.backup_user_ids or []), *tasksmod._team_maintainers(db, e.team_id)} - {e.manager_user_id} or \
            {e.manager_user_id}
    ids = {e.manager_user_id}
    if stage == "overdue" and task.requester_user_id:
        ids.add(task.requester_user_id)
    return ids


def _deliver_due(db, task_id: int, stage: str):
    task = db.get(EmployeeTask, task_id)
    e = db.get(Employee, task.employee_id) if task else None
    if not task or not e:
        return
    en = tasksmod.lang_of(e) == "en"
    name = _who(db, e)[0]
    due = _local(db, task.due_at)
    waiting = db.scalar(select(HumanRequest.id).where(HumanRequest.task_id == task.id, HumanRequest.status == "open",
                                                      HumanRequest.kind.in_(tasksmod.BLOCKING)))
    title = {"soon": f"⏰ {name}: '{task.title}' is due {due}" if en else f"⏰ {name}: '{task.title}' vence {due}",
             "overdue": f"⚠️ {name}: '{task.title}' is overdue (due {due})" if en else
                        f"⚠️ {name}: '{task.title}' está atrasada (prazo {due})",
             "escalated": f"🚨 {name}: '{task.title}' is still overdue (due {due}) — escalated to you" if en else
                          f"🚨 {name}: '{task.title}' continua atrasada (prazo {due}) — escalada para você"}[stage]
    lines = [f"Status: {task.status}"]
    if waiting:
        lines.append(f"Waiting for decision #{waiting}." if en else f"Aguardando a decisão #{waiting}.")
    cfg = org_cfg(db)
    for uid in _people(db, e, task, stage):
        u = db.get(User, uid)
        via = route(cfg, u) if u and u.active else None
        if via:
            try:
                _send_text(db, cfg, u, via, title, lines, f"{config.PUBLIC_BASE_URL}/app/#/tasks/{task.id}",
                           "Open task" if en else "Abrir tarefa")
            except Exception:  # noqa: BLE001 — um destinatário com problema não impede os outros
                log.exception("aviso de prazo da tarefa #%s para %s", task.id, u.email)


def due_sweep(db) -> dict:
    """Prazo chegando: sobe a prioridade e avisa o gestor. Venceu: avisa gestor e quem pediu. Continua vencida depois de
    overdue_escalate_min: escala para os substitutos e mantenedores do time."""
    cfg = org_cfg(db)
    t = now()
    out = {"due_soon": 0, "overdue": 0, "due_escalated": 0}
    rows = db.scalars(select(EmployeeTask).where(EmployeeTask.status.in_(OPEN_TASK), EmployeeTask.due_at.is_not(None),
                                                 EmployeeTask.due_state != "escalated")).all()
    for task in rows:
        due = tasksmod._aware(task.due_at)
        stage = None
        if due <= t - timedelta(minutes=cfg["overdue_escalate_min"]) and task.due_state == "overdue":
            stage = "escalated"
        elif due <= t and task.due_state in ("", "soon"):
            stage = "overdue"
        elif task.due_state == "" and cfg["due_soon_min"]:
            span = due - tasksmod._aware(task.created_at)
            window = min(timedelta(minutes=cfg["due_soon_min"]), span / 4)
            if t < due <= t + window:
                stage = "soon"
        if not stage:
            continue
        task.due_state, task.priority = stage, 1
        tasksmod.event(db, task, "due", {"stage": stage, "due_at": iso(due)})
        db.commit()
        if stage == "escalated":
            e = db.get(Employee, task.employee_id)
            audit(db, "prazo", "employee.task.overdue_escalated", _who(db, e)[1] if e else "", f"tarefa #{task.id}")
        _bg(_deliver_due, task.id, stage)
        out["due_soon" if stage == "soon" else "overdue" if stage == "overdue" else "due_escalated"] += 1
    return out
