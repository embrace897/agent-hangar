"""Vigias: acompanhar uma fonte e só agir quando algo muda ou cruza um limiar — mudança regulatória numa página, preço
de concorrente, número que passou do limite, nova versão de uma política.

- **Fontes:** uma página (`url`: o texto visível), um JSON (`json`: o valor num caminho, ex.: `data.0.price`), a base
  de conhecimento (`knowledge`: os documentos que respondem a uma pergunta) ou um número da própria plataforma
  (`metric`: `open_tasks`, `failed_tasks_24h`, `queue_open:<fila>`, `queue_breached:<fila>`).
- **Condições:** `changed` (o conteúdo mudou desde a última olhada — a primeira só registra a base), `threshold`
  (valor `>`/`>=`/`<`/`<=`/`==` um limite) e `contains` (o texto passou a conter uma frase). Limiar e frase disparam
  na virada (de falso para verdadeiro), não a cada olhada.
- **Ações:** uma tarefa para o Digital employee escolhido (com o que mudou), um aviso para quem criou o vigia, ou só o
  registro. Agenda em cron ou por dia útil (no mínimo a cada 15 minutos).
"""
import difflib
import hashlib
import re
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from ..models import Agent, Employee, EmployeeTask, Team, User, Watcher, WorkItem, WorkQueue, now
from . import bizcal
from . import employee_tasks as tasksmod
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso

HTTP = lambda: httpx.Client(timeout=30, follow_redirects=False)  # noqa: E731  (os testes trocam)
SOURCES = ("url", "json", "knowledge", "metric")
CONDITIONS = ("changed", "threshold", "contains")
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _can(acc: Access, team_id: int | None) -> bool:
    return acc.p.is_admin or (team_id is not None and RANK.get(acc.teams.get(team_id, ""), 0) >= RANK["developer"])


def watcher_dict(db, w: Watcher) -> dict:
    t = db.get(Team, w.team_id) if w.team_id else None
    e = db.get(Employee, w.employee_id) if w.employee_id else None
    a = db.get(Agent, e.agent_id) if e else None
    return {"id": w.id, "name": w.name, "team": t.slug if t else None, "employee": a.slug if a else None, "source": w.source,
            "condition": w.condition, "action": w.action, "schedule": w.schedule, "schedule_text": _describe(w.schedule),
            "enabled": w.enabled, "next_run_at": iso(w.next_run_at) if w.next_run_at else None,
            "last_checked_at": iso(w.last_checked_at) if w.last_checked_at else None,
            "last_fired_at": iso(w.last_fired_at) if w.last_fired_at else None, "last_change": w.last_change,
            "last_error": w.last_error, "fired": w.fired, "value": (w.state or {}).get("value")}


def _describe(expr: str) -> str:
    try:
        return bizcal.describe(expr)
    except Exception:  # noqa: BLE001
        return expr


def list_watchers(db, acc: Access) -> list[dict]:
    return [watcher_dict(db, w) for w in db.scalars(select(Watcher).order_by(Watcher.name))
            if acc.p.is_admin or acc.p.is_auditor or w.team_id in acc.teams]


def save(db, acc: Access, body: dict, wid: int | None = None) -> dict:
    t = db.scalar(select(Team).where(Team.slug == body.get("team"))) if body.get("team") else None
    if body.get("team") and t is None:
        raise PlatformError("time não encontrado")
    w = db.get(Watcher, wid) if wid else None
    if not _can(acc, (w.team_id if w else None) if wid else (t.id if t else None)):
        raise Forbidden("só quem desenvolve no time cria vigias")
    src, cond, action = body.get("source") or {}, body.get("condition") or {"type": "changed"}, body.get("action") or {"type": "log"}
    if src.get("type") not in SOURCES:
        raise PlatformError(f"source.type: {', '.join(SOURCES)}")
    if src["type"] in ("url", "json"):
        from .knowledge import _check_url
        _check_url(str(src.get("url") or ""))
    if cond.get("type") not in CONDITIONS:
        raise PlatformError(f"condition.type: {', '.join(CONDITIONS)}")
    if cond["type"] == "threshold" and (cond.get("op") not in OPS or cond.get("value") is None):
        raise PlatformError(f"limiar: op ({', '.join(OPS)}) e value")
    if cond["type"] == "contains" and not str(cond.get("text") or "").strip():
        raise PlatformError("diga a frase (condition.text)")
    if action.get("type") not in ("task", "notify", "log"):
        raise PlatformError("action.type: task | notify | log")
    emp = None
    if action["type"] == "task":
        from .employees import find
        e, _ = find(db, acc, str(body.get("employee") or ""), "assign")
        emp = e.id
    sched = str(body.get("schedule") or "0 * * * *").strip()
    try:
        from ..cron import Cron
        if bizcal.parse(sched) is None and Cron(sched).min_interval_minutes() < 15:
            raise PlatformError("no mínimo 15 minutos entre as olhadas")
    except PlatformError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise PlatformError(f"agenda inválida: {ex}") from None
    if w is None:
        w = Watcher(created_by=acc.p.name, created_at=now(), team_id=t.id if t else None, state={})
        db.add(w)
    w.name, w.source, w.condition, w.action, w.schedule = (body.get("name") or "Vigia")[:200], src, cond, action, sched
    w.employee_id, w.enabled = emp or w.employee_id, bool(body.get("enabled", True))
    w.next_run_at = now() if w.enabled else None  # olha logo (registra a base)
    db.commit()
    audit(db, acc.p.name, "watcher.save", f"#{w.id}", w.name)
    return watcher_dict(db, w)


def delete(db, acc: Access, wid: int) -> dict:
    w = db.get(Watcher, wid)
    if w is None or not _can(acc, w.team_id):
        raise PlatformError("vigia não encontrado")
    db.delete(w)
    db.commit()
    return {"deleted": wid}


# ------------------------------------------------------------------ olhar
def _get_path(data, path: str):
    cur = data
    for part in [p for p in str(path or "").split(".") if p]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def read(db, w: Watcher) -> str | float:
    s = w.source
    if s["type"] == "url":
        from .knowledge import _check_url, _html_text
        with HTTP() as h:
            r = h.get(_check_url(s["url"]))
        r.raise_for_status()
        text = _html_text(r.text) if "html" in r.headers.get("content-type", "html") else r.text
        if s.get("select"):  # um trecho só (o que vem depois desta frase)
            i = text.find(s["select"])
            text = text[i:i + 4000] if i >= 0 else ""
        return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()[:20000]
    if s["type"] == "json":
        from .knowledge import _check_url
        with HTTP() as h:
            r = h.get(_check_url(s["url"]))
        r.raise_for_status()
        v = _get_path(r.json(), s.get("path", ""))
        return float(v) if isinstance(v, (int, float)) else str(v)
    if s["type"] == "knowledge":
        from . import knowledge
        who = {"org"} | ({f"team:{w.team_id}"} if w.team_id else set())
        hits = knowledge.search(db, str(s.get("query") or ""), who, 5)
        return "\n".join(f"{h['title']} ({h['updated']})" for h in hits)
    metric = str(s.get("metric") or "")
    if metric in ("open_tasks", "failed_tasks_24h"):
        ids = [e.id for e in db.scalars(select(Employee).join(Agent, Agent.id == Employee.agent_id).where(Agent.team_id == w.team_id))]
        q = select(func.count()).select_from(EmployeeTask).where(EmployeeTask.employee_id.in_(ids or [-1]))
        q = q.where(EmployeeTask.status.not_in(tasksmod.TERMINAL)) if metric == "open_tasks" else \
            q.where(EmployeeTask.status == "failed", EmployeeTask.finished_at >= now() - timedelta(hours=24))
        return float(db.scalar(q) or 0)
    if metric.startswith(("queue_open:", "queue_breached:")):
        kind, slug = metric.split(":", 1)
        q = db.scalar(select(WorkQueue).where(WorkQueue.slug == slug))
        if q is None:
            raise PlatformError(f"fila '{slug}' não encontrada")
        from .work_queues import OPEN
        cond = [WorkItem.queue_id == q.id, WorkItem.status.in_(OPEN)]
        if kind == "queue_breached":
            cond.append(WorkItem.sla_breached.is_(True))
        return float(db.scalar(select(func.count()).select_from(WorkItem).where(*cond)) or 0)
    raise PlatformError(f"métrica desconhecida: {metric}")


def _diff(old: str, new: str) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "antes", "agora", lineterm="", n=1))
    return "\n".join(lines[2:80])


def check(db, w: Watcher, at=None) -> bool:
    """Olha a fonte, avalia a condição e age se disparou. Devolve se disparou."""
    at = at or now()
    st = dict(w.state or {})
    try:
        value = read(db, w)
    except (PlatformError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        w.last_error, w.last_checked_at = str(e)[:300], at
        db.commit()
        return False
    w.last_error, w.last_checked_at = "", at
    c = w.condition
    fired, change = False, ""
    if c["type"] == "changed":
        h = hashlib.sha256(str(value).encode()).hexdigest()
        if st.get("hash") and st["hash"] != h:
            fired = True
            change = _diff(str(st.get("value", "")), str(value)) if isinstance(value, str) else f"{st.get('value')} → {value}"
        st["hash"] = h
    else:
        if c["type"] == "threshold":
            try:
                now_true = OPS[c["op"]](float(value), float(c["value"]))
            except (TypeError, ValueError):
                now_true = False
            change = f"{value} {c['op']} {c['value']}"
        else:
            now_true = str(c["text"]).lower() in str(value).lower()
            change = f"apareceu “{c['text']}”"
        fired = now_true and not st.get("active")
        st["active"] = now_true
    st["value"] = value if not isinstance(value, str) else value[:20000]
    w.state = st
    if fired:
        w.fired += 1
        w.last_fired_at, w.last_change = at, change[:4000]
        _act(db, w, change)
    db.commit()
    return fired


def _act(db, w: Watcher, change: str):
    a = w.action or {}
    title = (a.get("title") or f"Vigia: {w.name}").replace("{name}", w.name)[:300]
    body = (f"{a.get('body') or ''}\n\nO vigia \"{w.name}\" disparou ({w.condition['type']}).\nO que mudou:\n{change}\n"
            f"Fonte: {w.source.get('url') or w.source.get('query') or w.source.get('metric') or ''}").strip()
    if a.get("type") == "task" and w.employee_id:
        e = db.get(Employee, w.employee_id)
        if e is not None and e.status in ("probation", "active"):
            tasksmod.create_task(db, e, title, body, "watcher", f"vigia:{w.name}", None, 2)
    elif a.get("type") == "notify":
        from . import notify
        u = db.scalar(select(User).where(User.email == (w.created_by or "").lower()))
        if u is not None:
            cfg = notify.org_cfg(db)
            via = notify.route(cfg, u)
            if via:
                notify._bg(_tell, u.id, title, change)
    audit(db, "vigia", "watcher.fired", f"#{w.id}", change[:200])


def _tell(db, user_id: int, title: str, change: str):
    from .. import config
    from . import notify
    u = db.get(User, user_id)
    cfg = notify.org_cfg(db)
    via = notify.route(cfg, u) if u else None
    if via:
        notify._send_text(db, cfg, u, via, title, [change[:1500]], f"{config.PUBLIC_BASE_URL}/app/#/watchers")


def run_now(db, acc: Access, wid: int) -> dict:
    w = db.get(Watcher, wid)
    if w is None or not _can(acc, w.team_id):
        raise PlatformError("vigia não encontrado")
    check(db, w)
    return watcher_dict(db, w)


def sweep(db, at=None) -> int:
    at, n = at or now(), 0
    tz = "UTC"
    from ..models import Organization
    from ..tenancy import current_org
    o = db.get(Organization, current_org())
    tz = (o.timezone if o else None) or tz
    for w in db.scalars(select(Watcher).where(Watcher.enabled.is_(True), Watcher.next_run_at.is_not(None),
                                              Watcher.next_run_at <= at)).all():
        w.next_run_at = bizcal.next_after(db, w.schedule, at, tz)
        db.commit()
        n += check(db, w, at)
    return n
