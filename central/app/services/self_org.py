"""Auto-organização: a empresa propõe mudanças na própria estrutura, com números, e uma pessoa decide.

A plataforma olha o trabalho de cada dia e propõe:
- **rotina** — o mesmo pedido volta no mesmo ritmo (3+ vezes em 5 semanas, no mesmo dia da semana ou em dias úteis):
  vira rotina do Digital employee, no horário em que costuma chegar;
- **rota da fila** — uma categoria que as pessoas resolvem à mão (5+ itens em 30 dias) passa a ir para o Digital
  employee do time que mais entregou;
- **colega para repasse** — o funcionário tentou repassar para alguém fora da lista dele 2+ vezes: entra na lista.

E o próprio Digital employee propõe (`propose_org_change`): uma rotina, uma instrução para o cargo, um colega, uma rota.

Cada proposta é uma decisão para quem responde pelo funcionário (aprovar, ajustar ou recusar) e só vale depois dela;
uma recusa não volta a ser proposta por 30 dias. Mudanças no cargo (instrução, colegas) rodam as tarefas de ouro.
"""
import re
import statistics
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .. import tenancy
from ..models import (
    Agent,
    Employee,
    EmployeeRoutine,
    EmployeeTask,
    HumanRequest,
    Organization,
    TaskEvent,
    WorkItem,
    WorkQueue,
    now,
)
from . import employee_tasks as tasksmod
from .common import PlatformError, audit
from .decision_kinds import close, handler
from .platform_tools import Ctx, tool

ROUTINE_MIN, QUEUE_MIN, COLLEAGUE_MIN = 3, 5, 2
SOURCES = ("portal", "chat", "email", "mcp", "api", "slack", "teams", "telegram", "discord", "whatsapp", "queue")
_last_run: dict[int, str] = {}
CHANGES = ("routine", "instruction", "colleague", "queue_route", "reports_to")


def _norm(title: str) -> str:
    t = re.sub(r"\d+", "#", (title or "").lower())
    return " ".join(re.findall(r"[\wà-ú#]+", t)[:6])


def _tz(db) -> ZoneInfo:
    o = db.get(Organization, tenancy.current_org())
    try:
        return ZoneInfo((o.timezone if o else None) or "America/Sao_Paulo")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _open_or_recent(db, e: Employee, key: str) -> bool:
    """Já proposta (aberta) ou recusada há menos de 30 dias: não propõe de novo."""
    since = now() - timedelta(days=30)
    for r in db.scalars(select(HumanRequest).where(HumanRequest.employee_id == e.id, HumanRequest.kind == "org_change")):
        if (r.action_payload or {}).get("key") != key:
            continue
        if r.status == "open" or (r.decision == "reject" and tasksmod._aware(r.decided_at or r.created_at) >= since) \
                or r.decision in ("approve", "approve_edited"):
            return True
    return False


def propose(db, e: Employee, change: str, key: str, question: str, payload: dict, source: str = "platform",
            task_id: int | None = None) -> HumanRequest | None:
    if change not in CHANGES:
        raise PlatformError(f"change: {', '.join(CHANGES)}")
    if _open_or_recent(db, e, key):
        return None
    r = HumanRequest(employee_id=e.id, task_id=task_id, kind="org_change", action_type="prod_change", mode="approve",
                     question=question[:4000], action_payload={"change": change, "key": key, "source": source, **payload},
                     assigned_user_id=e.manager_user_id, fallback_user_id=(e.backup_user_ids or [None])[0], approvals=[],
                     created_at=now())
    db.add(r)
    db.commit()
    tasksmod._notify_request(db, e, r)
    return r


# ------------------------------------------------------------------ o que a plataforma percebe
def detect_routines(db, e: Employee) -> int:
    tz, n = _tz(db), 0
    since = now() - timedelta(days=35)
    groups: dict[str, list[EmployeeTask]] = defaultdict(list)
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.employee_id == e.id, EmployeeTask.created_at >= since,
                                                   EmployeeTask.source.in_(SOURCES), EmployeeTask.probation.is_(False))):
        groups[_norm(t.title)].append(t)
    have = {_norm(r.title) for r in db.scalars(select(EmployeeRoutine).where(EmployeeRoutine.employee_id == e.id))}
    for key, ts in groups.items():
        if len(ts) < ROUTINE_MIN or not key or key in have:
            continue
        local = [tasksmod._aware(t.created_at).astimezone(tz) for t in ts]
        days = {d.date() for d in local}
        if len(days) < ROUTINE_MIN:
            continue  # vários no mesmo dia não é ritmo
        hour = int(statistics.median(d.hour for d in local))
        wd = statistics.mode(d.isoweekday() % 7 for d in local)
        weekly = sum(d.isoweekday() % 7 == wd for d in local) >= max(ROUTINE_MIN, len(local) * 2 // 3)
        cron = f"0 {hour} * * {wd}" if weekly else f"0 {hour} * * 1-5"
        last = max(ts, key=lambda t: tasksmod._aware(t.created_at))
        when = (f"toda {['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado'][wd]}" if weekly
                else "todo dia útil") + f" às {hour}h"
        n += propose(db, e, "routine", f"routine:{key}",
                     f"\"{last.title}\" foi pedido {len(ts)} vezes em 5 semanas, {when}. Virar rotina do Digital employee "
                     f"(ele faz sozinho, {when})?",
                     {"title": last.title, "body": last.body, "cron": cron, "evidence": {"tasks": [t.id for t in ts][-10:],
                                                                                         "count": len(ts)}}) is not None
    return n


def detect_queue_routes(db) -> int:
    n, since = 0, now() - timedelta(days=30)
    for q in db.scalars(select(WorkQueue)).all():
        emps = [(e, a) for e, a in db.execute(select(Employee, Agent).join(Agent, Agent.id == Employee.agent_id)
                                              .where(Agent.team_id == q.team_id, Employee.status == "active")).all()]
        if not emps:
            continue
        for cat in q.categories or []:
            if (cat.get("route") or {}).get("type") == "employee":
                continue
            done = db.query(WorkItem).filter(WorkItem.queue_id == q.id, WorkItem.category == cat["name"],
                                             WorkItem.status == "done", WorkItem.handled_by == "human",
                                             WorkItem.done_at >= since).count()
            if done < QUEUE_MIN:
                continue
            best = max(emps, key=lambda ea: db.query(EmployeeTask).filter(EmployeeTask.employee_id == ea[0].id,
                                                                          EmployeeTask.status == "done").count())
            e, a = best
            n += propose(db, e, "queue_route", f"queue_route:{q.slug}:{cat['name']}",
                         f"A categoria \"{cat['name']}\" da fila {q.name} teve {done} pedidos resolvidos à mão em 30 dias. "
                         f"Mandar os próximos para {a.name}? (Se ele não conseguir, devolve para as pessoas.)",
                         {"queue": q.slug, "category": cat["name"], "to": a.slug, "evidence": {"done_by_people": done}}) is not None
    return n


def detect_colleagues(db, e: Employee) -> int:
    since, n = now() - timedelta(days=30), 0
    tries: dict[str, int] = defaultdict(int)
    for ev in db.scalars(select(TaskEvent).join(EmployeeTask, EmployeeTask.id == TaskEvent.task_id)
                         .where(EmployeeTask.employee_id == e.id, TaskEvent.kind == "handoff_denied", TaskEvent.at >= since)):
        to = (ev.payload or {}).get("to")
        if to:
            tries[to] += 1
    for to, k in tries.items():
        if k < COLLEAGUE_MIN or to in (e.colleagues or []):
            continue
        c = db.scalar(select(Employee).join(Agent, Agent.id == Employee.agent_id).where(Agent.slug == to))
        if c is None or c.status != "active":
            continue
        n += propose(db, e, "colleague", f"colleague:{to}",
                     f"Tentou repassar trabalho para {to} {k} vezes em 30 dias, mas {to} não está entre os colegas dele. "
                     "Liberar o repasse?", {"to": to, "evidence": {"denied_handoffs": k}}) is not None
    return n


def sweep(db, at=None) -> int:
    """Uma vez por dia, por empresa."""
    today = (at or now()).date().isoformat()
    org = tenancy.current_org()
    if _last_run.get(org) == today:
        return 0
    _last_run[org] = today
    n = detect_queue_routes(db)
    for e in db.scalars(select(Employee).where(Employee.status == "active")).all():
        n += detect_routines(db, e) + detect_colleagues(db, e)
    return n


# ------------------------------------------------------------------ o próprio funcionário propõe
@tool("propose_org_change", "Propõe ao seu gestor uma mudança na forma de trabalhar — e ela só vale se ele aprovar: "
      "change=routine (algo que você deveria fazer sozinho num ritmo: title, body, cron), instruction (uma regra para "
      "o seu cargo, aprendida no trabalho: text), colleague (alguém para quem você deveria poder repassar: to) ou "
      "queue_route (uma categoria de fila que deveria vir para você: queue, category). Diga por quê, com números.",
      {"properties": {"change": {"type": "string", "enum": list(CHANGES)}, "why": {"type": "string"},
                      "title": {"type": "string"}, "body": {"type": "string"}, "cron": {"type": "string"},
                      "text": {"type": "string"}, "to": {"type": "string"}, "queue": {"type": "string"},
                      "category": {"type": "string"}},
       "required": ["change", "why"]}, "org")
def propose_org_change(ctx: Ctx, args: dict):
    change = str(args.get("change") or "")
    why = str(args.get("why") or "").strip()
    if not why:
        raise PlatformError("diga por quê (why), com números")
    fields = {k: str(args[k])[:4000] for k in ("title", "body", "cron", "text", "to", "queue", "category") if args.get(k)}
    if change == "queue_route":
        fields["to"] = ctx.agent.slug
    need = {"routine": ("title", "cron"), "instruction": ("text",), "colleague": ("to",), "queue_route": ("queue", "category")}
    missing = [k for k in need.get(change, ()) if not fields.get(k)]
    if missing:
        raise PlatformError(f"faltou: {', '.join(missing)}")
    key = f"{change}:{fields.get('title') or fields.get('text', '')[:60] or fields.get('to') or fields.get('category')}"
    r = propose(ctx.db, ctx.e, change, key, f"{ctx.agent.name} propõe: {why}", fields, "employee", ctx.task.id)
    if r is None:
        return "Essa proposta já está com o seu gestor (ou foi recusada há pouco). Siga o trabalho."
    return f"Proposta #{r.id} enviada ao seu gestor. Ela só vale depois que ele aprovar; siga o trabalho como está."


# ------------------------------------------------------------------ decisão
@handler("org_change")
def decide_org_change(db, acc, r: HumanRequest, e: Employee, task, decision: str, edit: dict, reason: str, who: str):
    from . import bizcal, employee_revisions, employee_work, regression
    from .employee_tasks import request_dict
    if decision not in ("approve", "approve_edited", "reject"):
        raise PlatformError("org_change: approve | approve_edited (edit: cron, text, title) | reject")
    p = {**(r.action_payload or {}), **{k: v for k, v in (edit or {}).items() if k in ("cron", "text", "title", "body") and v}}
    a = db.get(Agent, e.agent_id)
    if decision != "reject":
        change = p.get("change")
        if change == "routine":
            tz = str(_tz(db))
            rt = EmployeeRoutine(employee_id=e.id, title=str(p.get("title"))[:300], body=str(p.get("body") or ""),
                                 cron=employee_work._cron(str(p["cron"]), tz), timezone=tz, priority=2, enabled=True,
                                 created_by=who)
            rt.next_run_at = bizcal.next_after(db, rt.cron, now(), tz)
            if "schedule" not in (e.channels or []):
                e.channels = [*(e.channels or []), "schedule"]
            db.add(rt)
        elif change == "instruction":
            employee_work.add_lesson(db, e, str(p.get("text") or ""), f"org_change:{r.id}", who)
            regression.start(db, e, f"instrução nova no cargo (proposta #{r.id})")  # o cargo mudou: as tarefas de ouro rodam
        elif change == "colleague":
            e.colleagues = sorted({*(e.colleagues or []), str(p["to"])})
            db.commit()
            employee_revisions.record(db, e, who, f"colega para repasse: {p['to']} (proposta #{r.id})")
        elif change == "reports_to":
            from .org_chart import set_chief
            set_chief(db, e, p.get("to") or None, who)
            db.commit()
            employee_revisions.record(db, e, who, f"passa a reportar a {p.get('to') or 'ninguém'} (proposta #{r.id})")
        elif change == "queue_route":
            q = db.scalar(select(WorkQueue).where(WorkQueue.slug == p.get("queue")))
            if q is None:
                raise PlatformError("a fila não existe mais")
            cats = [dict(c) for c in q.categories or []]
            hit = next((c for c in cats if c["name"] == p.get("category")), None)
            if hit is None:
                raise PlatformError("a categoria não existe mais")
            hit["route"] = {"type": "employee", "refs": [p.get("to") or a.slug]}
            q.categories = cats
        r.action_payload = p
    close(r, decision, reason, who)
    db.commit()
    audit(db, who, f"org_change.{decision}", a.slug, f"{p.get('change')} #{r.id}")
    return request_dict(db, r, e)


def propose_reports_to(db, acc, slug: str, to: str, why: str = "") -> dict:
    """No organograma, quem não gerencia o agente propõe a mudança de chefe; quem gerencia decide."""
    from .employees import find
    e, a = find(db, acc, slug, "view")
    if to:
        find(db, acc, to, "view")
    r = propose(db, e, "reports_to", f"reports_to:{to or '-'}",
                f"{acc.p.name} propõe que {a.name} passe a reportar a {to or 'ninguém'}. {why}".strip(),
                {"to": to, "evidence": {"by": acc.p.name}}, "person")
    if r is None:
        raise PlatformError("essa mudança já foi proposta (ou recusada há pouco)")
    return {"request": r.id}
