"""Tarefa direto do chat: "@Agent Hangar Analista, renova o contrato da ACME" no Slack, ou a menção ao webhook de saída
do Teams, vira uma tarefa do Digital employee, com as mesmas permissões do portal (quem pode entregar tarefas a ele).

- Slack (Events API): menção ao app num canal ou mensagem direta. A assinatura do Slack é conferida, o evento repetido
  é ignorado, e a pessoa é achada pelo e-mail do Slack. A resposta vai na thread; ao terminar, o resultado também.
- Teams (webhook de saída do canal, assinado com HMAC): a pessoa é achada pela conta Microsoft (o mesmo id do login
  SSO). O Teams só aceita a resposta na hora; o resultado vai depois pelo canal pessoal da pessoa (Slack, Teams ou
  e-mail, como as decisões).

O texto começa com o nome (ou slug) do funcionário; o resto é o pedido. Sem nome reconhecido, a resposta lista para
quem a pessoa pode pedir.
"""
import base64
import hashlib
import hmac
import html
import json
import re
import unicodedata

from sqlalchemy import select

from .. import config, shared
from ..models import Agent, Employee, EmployeeTask, User
from . import employee_tasks as tasksmod
from . import notify
from .access import Access, Forbidden, principal_for
from .common import PlatformError, audit

WORKING = ("probation", "active", "paused")
L = {
    "pt": {"who": "Para quem é o pedido? Comece com o nome do Digital employee, por exemplo “{ex}: renova o contrato da "
                  "ACME”. Você pode pedir para: {names}.",
           "none": "Você ainda não pode entregar tarefas a nenhum Digital employee.",
           "what": "O que {name} deve fazer? Escreva o pedido depois do nome.",
           "made": "Tarefa #{id} para {name}: “{title}”. Eu aviso aqui quando terminar. {url}",
           "made_teams": "Tarefa #{id} para {name}: “{title}”. O resultado chega no seu canal de avisos. {url}",
           "nouser": "Não encontrei você no Agent Hangar pelo e-mail desta conta do Slack.",
           "nouser_teams": "Não encontrei você no Agent Hangar: entre uma vez no portal com a sua conta Microsoft.",
           "done": "Tarefa #{id} concluída por {name}:", "failed": "Tarefa #{id} ({name}) falhou:",
           "open": "Abrir a tarefa"},
    "en": {"who": "Who is it for? Start with the Digital employee's name, for example “{ex}: renew the ACME contract”. "
                  "You can ask: {names}.",
           "none": "You can't assign tasks to any Digital employee yet.",
           "what": "What should {name} do? Write the request after the name.",
           "made": "Task #{id} for {name}: “{title}”. I'll post here when it's done. {url}",
           "made_teams": "Task #{id} for {name}: “{title}”. The result goes to your notification channel. {url}",
           "nouser": "I couldn't find you in Agent Hangar by this Slack account's e-mail.",
           "nouser_teams": "I couldn't find you in Agent Hangar: sign in to the portal once with your Microsoft account.",
           "done": "Task #{id} done by {name}:", "failed": "Task #{id} ({name}) failed:", "open": "Open the task"},
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def assignable(db, acc: Access) -> list[tuple[Employee, Agent]]:
    from .employees import find
    out = []
    for e in db.scalars(select(Employee).where(Employee.status.in_(WORKING))):
        a = db.get(Agent, e.agent_id)
        try:
            find(db, acc, a.slug, "assign")
        except (Forbidden, PlatformError):
            continue
        out.append((e, a))
    return out


def match(choices: list[tuple[Employee, Agent]], text: str) -> tuple[tuple[Employee, Agent] | None, str]:
    """O funcionário cujo nome (ou slug) abre o texto, e o pedido que sobra. O nome mais longo ganha."""
    raw = (text or "").strip().lstrip("@").strip()
    words = raw.split()
    prefixes = [_norm(" ".join(words[:k])) for k in range(1, min(len(words), 8) + 1)]
    best, size, length = None, 0, 0
    for e, a in choices:
        for cand in {_norm(a.name), _norm(a.slug)}:
            if cand and cand in prefixes and len(cand) > length:  # "Ana Paula" ganha de "Ana"
                best, size, length = (e, a), prefixes.index(cand) + 1, len(cand)
    if best is None:
        return None, raw
    return best, re.sub(r"^[\s,:;.!—-]+", "", " ".join(words[size:])).strip()


def _title(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return (first[:117] + "…") if len(first) > 120 else first


def create_from_chat(db, u: User, text: str, source: str, origin: dict) -> dict:
    from . import employees
    acc = Access(db, principal_for(u))
    choices = assignable(db, acc)
    (hit, rest) = match(choices, text)
    lang = tasksmod.lang_of(hit[0]) if hit else "pt"
    t = L[lang]
    if not choices:
        return {"text": t["none"]}
    if hit is None:
        return {"text": t["who"].format(ex=choices[0][1].name, names=", ".join(a.name for _, a in choices[:15]))}
    e, a = hit
    if not rest:
        return {"text": t["what"].format(name=a.name)}
    d = employees.assign(db, acc, a.slug, _title(rest), rest, source=source)
    task = db.get(EmployeeTask, d["id"])
    task.origin = origin
    db.commit()
    url = f"{config.PUBLIC_BASE_URL}/app/#/tasks/{task.id}"
    key = "made_teams" if source == "teams" else "made"
    return {"text": t[key].format(id=task.id, name=a.name, title=task.title, url=url), "task_id": task.id}


# ------------------------------------------------------------------ Slack
def slack_event(db, raw: bytes, headers: dict) -> dict | None:
    notify.verify_slack_any(db, raw, headers)  # acha a empresa pela assinatura
    body = json.loads(raw or b"{}")
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}
    if body.get("type") != "event_callback":
        return None
    ev = body.get("event") or {}
    if ev.get("bot_id") or ev.get("subtype"):  # bots (inclusive as respostas deste app) e edições não viram tarefa
        return None
    if not (ev.get("type") == "app_mention" or (ev.get("type") == "message" and ev.get("channel_type") == "im")):
        return None
    if not shared.take_once(f"slack-event:{body.get('event_id') or ev.get('ts')}", 3600):  # o Slack reenvia
        return None
    notify._bg(_slack_task, ev)  # o Slack espera resposta em 3 s
    return None


def _slack_post(cfg: dict, channel: str, ts: str, text: str):
    notify._slack(cfg, "chat.postMessage", {"channel": channel, "thread_ts": ts, "text": text[:3900],
                                            "unfurl_links": False})


def _slack_task(db, ev: dict):
    cfg = notify.org_cfg(db)
    channel, ts = ev.get("channel", ""), ev.get("thread_ts") or ev.get("ts", "")
    text = re.sub(r"<@[A-Z0-9]+>", "", ev.get("text") or "").strip()
    text = html.unescape(re.sub(r"<(https?://[^|>]+)(\|[^>]*)?>", r"\1", text))  # links do Slack: <url|texto>
    u = notify._slack_person(db, cfg, ev.get("user", "")) if ev.get("user") else None
    if not u or not u.active:
        _slack_post(cfg, channel, ts, L["pt"]["nouser"] + " / " + L["en"]["nouser"])
        return
    try:
        out = create_from_chat(db, u, text, "slack", {"slack": {"channel": channel, "ts": ts}})
    except (PlatformError, Forbidden) as e:
        out = {"text": str(e)}
    _slack_post(cfg, channel, ts, out["text"])
    if out.get("task_id"):
        audit(db, u.email, "employee.task.chat", "slack", f"#{out['task_id']}")


# ------------------------------------------------------------------ Teams (webhook de saída do canal)
def teams_message(db, raw: bytes, headers: dict) -> dict:
    from sqlalchemy import select as _select

    from .. import tenancy
    from ..models import Organization
    got = headers.get("authorization", "")
    if not got.startswith("HMAC "):
        raise Forbidden("assinatura do Teams ausente")
    org = None
    for o in db.scalars(_select(Organization).order_by(Organization.id)):  # a assinatura diz de que empresa é
        secret = notify._secret({**(o.notify or {})}, "teams_outgoing_secret")
        if not secret:
            continue
        try:
            want = base64.b64encode(hmac.new(base64.b64decode(secret), raw, hashlib.sha256).digest()).decode()
        except ValueError:
            continue
        if hmac.compare_digest(got[5:].strip(), want):
            org = o.id
            break
    if org is None:
        raise Forbidden("assinatura do Teams inválida")
    tenancy.set_org(org)
    body = json.loads(raw or b"{}")
    text = re.sub(r"<at>.*?</at>", "", body.get("text") or "", flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ").strip()
    oid = (body.get("from") or {}).get("aadObjectId") or ""
    u = db.scalar(select(User).where(User.provider == "microsoft", User.subject == oid, User.active.is_(True))) if oid else None
    if u is None:
        return {"type": "message", "text": L["pt"]["nouser_teams"] + " / " + L["en"]["nouser_teams"]}
    origin = {"teams": {"conversation": ((body.get("conversation") or {}).get("id") or "")[:300]}}
    try:
        out = create_from_chat(db, u, text, "teams", origin)
    except (PlatformError, Forbidden) as e:
        out = {"text": str(e)}
    if out.get("task_id"):
        audit(db, u.email, "employee.task.chat", "teams", f"#{out['task_id']}")
    return {"type": "message", "text": out["text"]}


# ------------------------------------------------------------------ o resultado volta para quem pediu
def task_finished(db, task: EmployeeTask):
    o = task.origin or {}
    if task.source not in ("slack", "teams") or not o or task.status not in ("done", "failed"):
        return
    notify._bg(_deliver_result, task.id)


def _deliver_result(db, task_id: int):
    task = db.get(EmployeeTask, task_id)
    e = db.get(Employee, task.employee_id) if task else None
    a = db.get(Agent, e.agent_id) if e else None
    if not task or not a:
        return
    t = L[tasksmod.lang_of(e)]
    url = f"{config.PUBLIC_BASE_URL}/app/#/tasks/{task.id}"
    head = t["done" if task.status == "done" else "failed"].format(id=task.id, name=a.name)
    text = (task.result if task.status == "done" else task.error) or ""
    cfg = notify.org_cfg(db)
    s = (task.origin or {}).get("slack")
    if s:
        _slack_post(cfg, s["channel"], s["ts"], f"{head}\n{text[:3200]}\n<{url}|{t['open']}>")
        return
    u = db.get(User, task.requester_user_id) if task.requester_user_id else None
    via = notify.route(cfg, u) if u and u.active else None
    if via:
        notify._send_text(db, cfg, u, via, head, [text[:6000]], url, t["open"])
