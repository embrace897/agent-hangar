"""Filas do time e trabalho para pessoas.

Uma fila é a entrada única dos pedidos de um time (portal, webhook, e-mail encaminhado…). Cada pedido ganha uma
categoria (palavras-chave), um SLA e um destino: um Digital employee — o menos ocupado de uma lista — ou as pessoas do
time, que "pegam a próxima". O Digital employee pode resolver sozinho ou preparar e devolver para um analista terminar
(`hand_to_person`); playbooks e reuniões também criam itens para pessoas. A fila mostra quanto foi automático,
assistido e humano, e o SLA estourado vira alerta para quem mantém o time.
"""
import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import func, select

from ..models import Agent, Employee, EmployeeTask, Team, TeamMember, User, WorkItem, WorkItemFile, WorkQueue, now
from . import employee_tasks as tasksmod
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso, slugify
from .platform_tools import Ctx, tool

OPEN = ("waiting", "open", "in_progress", "employee", "review")
STATUS = ("waiting", "open", "in_progress", "employee", "review", "done", "cancelled")


# ------------------------------------------------------------------ permissões
def _team(db, slug: str) -> Team:
    t = db.scalar(select(Team).where(Team.slug == slug))
    if t is None:
        raise PlatformError(f"time '{slug}' não encontrado")
    return t


def _member(acc: Access, team_id: int | None) -> bool:
    return acc.p.is_admin or acc.p.is_auditor or (team_id is not None and team_id in acc.teams)


def _maintainer(acc: Access, team_id: int | None) -> bool:
    return acc.p.is_admin or (team_id is not None and RANK.get(acc.teams.get(team_id, ""), 0) >= RANK["maintainer"])


# ------------------------------------------------------------------ filas
def _categories(db, cats) -> list[dict]:
    out, seen = [], set()
    for c in cats or []:
        name = str(c.get("name") or "").strip()[:80]
        if not name or name.lower() in seen:
            raise PlatformError(f"categoria sem nome ou repetida: {c}")
        seen.add(name.lower())
        route = c.get("route") or {"type": "people"}
        if route.get("type") not in ("employee", "people"):
            raise PlatformError("route.type: employee | people")
        refs = [str(r) for r in route.get("refs") or ([route["ref"]] if route.get("ref") else [])]
        if route["type"] == "employee":
            if not refs:
                raise PlatformError(f"a categoria {name} vai para um Digital employee: diga qual (route.refs)")
            for r in refs:
                if db.scalar(select(Agent.id).join(Employee, Employee.agent_id == Agent.id).where(Agent.slug == r)) is None:
                    raise PlatformError(f"Digital employee '{r}' não encontrado")
        sla = c.get("sla_hours")
        out.append({"name": name, "keywords": [str(k).strip().lower() for k in c.get("keywords") or [] if str(k).strip()],
                    "sla_hours": float(sla) if sla not in (None, "") else None,
                    "route": {"type": route["type"], "refs": refs}, "priority": max(1, min(int(c.get("priority") or 2), 3))})
    return out


def queue_dict(db, q: WorkQueue, acc: Access | None = None, stats: bool = True) -> dict:
    t = db.get(Team, q.team_id)
    d = {"slug": q.slug, "name": q.name, "description": q.description, "team": t.slug if t else None,
         "team_name": t.name if t else "", "categories": q.categories, "default_category": q.default_category,
         "intake": {"enabled": bool(q.intake_token_hash), "hint": q.intake_token_hint, "url": f"/hooks/queues/{q.slug}"},
         "can_manage": bool(acc and _maintainer(acc, q.team_id))}
    if stats:
        d["stats"] = metrics(db, q)
    return d


def metrics(db, q: WorkQueue, days: int = 30) -> dict:
    since = now() - timedelta(days=days)
    rows = db.execute(select(WorkItem.status, WorkItem.handled_by, WorkItem.sla_breached, WorkItem.created_at, WorkItem.done_at)
                      .where(WorkItem.queue_id == q.id)).all()
    open_ = [r for r in rows if r[0] in OPEN]
    done = [r for r in rows if r[0] == "done" and r[4] and tasksmod._aware(r[4]) >= since]
    hours = [(tasksmod._aware(r[4]) - tasksmod._aware(r[3])).total_seconds() / 3600 for r in done if r[3]]
    by = {k: sum(1 for r in done if r[1] == k) for k in ("employee", "assisted", "human")}
    return {"open": len(open_), "breached": sum(1 for r in open_ if r[2]), "done_30d": len(done),
            "automatic": by["employee"], "assisted": by["assisted"], "human": by["human"],
            "avg_hours": round(sum(hours) / len(hours), 1) if hours else None,
            "unassigned": sum(1 for r in open_ if r[0] == "open")}


def list_queues(db, acc: Access) -> list[dict]:
    return [queue_dict(db, q, acc) for q in db.scalars(select(WorkQueue).order_by(WorkQueue.name)) if _member(acc, q.team_id)]


def _queue(db, slug: str) -> WorkQueue:
    q = db.scalar(select(WorkQueue).where(WorkQueue.slug == slug))
    if q is None:
        raise PlatformError(f"fila '{slug}' não encontrada")
    return q


def save_queue(db, acc: Access, name: str, team: str, slug: str = "", description: str = "", categories=None,
               default_category: str = "") -> dict:
    t = _team(db, team)
    if not _maintainer(acc, t.id):
        raise Forbidden("só quem mantém o time cria e muda filas")
    key = slugify(slug or name)[:80]
    if not key:
        raise PlatformError("dê um nome à fila")
    q = db.scalar(select(WorkQueue).where(WorkQueue.slug == key))
    if q is not None and q.team_id != t.id:
        raise PlatformError(f"já existe uma fila '{key}' de outro time")
    cats = _categories(db, categories)
    if default_category and default_category not in [c["name"] for c in cats]:
        raise PlatformError("a categoria padrão precisa ser uma das categorias")
    if q is None:
        q = WorkQueue(slug=key, team_id=t.id, created_by=acc.p.name, created_at=now())
        db.add(q)
    q.name, q.description, q.categories = (name or key)[:200], (description or "")[:2000], cats
    q.default_category = default_category or (cats[0]["name"] if cats else "")
    db.commit()
    audit(db, acc.p.name, "queue.save", key, f"{len(cats)} categoria(s)")
    return queue_dict(db, q, acc)


def delete_queue(db, acc: Access, slug: str) -> dict:
    """Remove a fila; os itens ficam (sem fila), com o histórico."""
    q = _queue(db, slug)
    if not _maintainer(acc, q.team_id):
        raise Forbidden("só quem mantém o time remove a fila")
    for i in db.scalars(select(WorkItem).where(WorkItem.queue_id == q.id)):
        i.queue_id = None
    db.delete(q)
    db.commit()
    audit(db, acc.p.name, "queue.delete", slug, "")
    return {"deleted": slug}


def intake_token(db, acc: Access, slug: str, enabled: bool = True) -> dict:
    q = _queue(db, slug)
    if not _maintainer(acc, q.team_id):
        raise Forbidden("só quem mantém o time gerencia a entrada da fila")
    if not enabled:
        q.intake_token_hash, q.intake_token_hint = "", ""
        db.commit()
        return queue_dict(db, q, acc, False)
    tok = "qin_" + secrets.token_urlsafe(24)
    q.intake_token_hash, q.intake_token_hint = hashlib.sha256(tok.encode()).hexdigest(), tok[-4:]
    db.commit()
    audit(db, acc.p.name, "queue.intake_token", q.slug, f"…{tok[-4:]}")
    return {**queue_dict(db, q, acc, False), "token": tok}


# ------------------------------------------------------------------ entrada e roteamento
def classify(q: WorkQueue, title: str, body: str) -> dict | None:
    text = f"{title}\n{body}".lower()
    best, hits = None, 0
    for c in q.categories or []:
        n = sum(1 for k in c.get("keywords") or [] if k and k in text)
        if n > hits:
            best, hits = c, n
    return best or next((c for c in q.categories or [] if c["name"] == q.default_category), None)


def _least_busy(db, slugs: list[str]) -> tuple[Employee, Agent] | None:
    best = None
    for s in slugs:
        a = db.scalar(select(Agent).where(Agent.slug == s))
        e = db.scalar(select(Employee).where(Employee.agent_id == a.id)) if a else None
        if e is None or e.status not in ("probation", "active"):
            continue
        load = db.scalar(select(func.count()).select_from(EmployeeTask).where(
            EmployeeTask.employee_id == e.id, EmployeeTask.status.not_in(tasksmod.TERMINAL))) or 0
        if best is None or load < best[0]:
            best = (load, e, a)
    return (best[1], best[2]) if best else None


def intake(db, q: WorkQueue, title: str, body: str = "", requester: str = "", requester_user_id: int | None = None,
           source: str = "portal", category: str = "", priority: int | None = None) -> WorkItem:
    title = (title or "").strip()[:300]
    if not title:
        raise PlatformError("o pedido precisa de um título")
    cat = next((c for c in q.categories or [] if c["name"] == category), None) if category else classify(q, title, body)
    item = WorkItem(queue_id=q.id, team_id=q.team_id, title=title, body=(body or "")[:20000], category=cat["name"] if cat else "",
                    priority=priority or (cat or {}).get("priority") or 2, status="open", source=source,
                    requester=requester[:254], requester_user_id=requester_user_id, created_by=requester or source,
                    created_at=now())
    if cat and cat.get("sla_hours"):
        item.sla_due_at = now() + timedelta(hours=float(cat["sla_hours"]))
    db.add(item)
    db.flush()
    route = (cat or {}).get("route") or {"type": "people"}
    if route["type"] == "employee":
        pick = _least_busy(db, route.get("refs") or [])
        if pick is not None:
            e, a = pick
            t = tasksmod.create_task(db, e, title, f"{body}\n\n(Pedido #{item.id} da fila {q.name}"
                                     f"{', categoria ' + item.category if item.category else ''}. Se precisar de uma pessoa "
                                     "para terminar, prepare o que puder e use hand_to_person.)", "queue",
                                     requester or q.name, requester_user_id, item.priority,
                                     item.sla_due_at)
            item.status, item.employee_task_id, item.handled_by = "employee", t.id, "employee"
    db.commit()
    return item


def submit(db, acc: Access, slug: str, title: str, body: str = "", category: str = "", priority: int | None = None) -> dict:
    q = _queue(db, slug)
    item = intake(db, q, title, body, acc.p.name, acc.p.user_id, "portal", category, priority)
    audit(db, acc.p.name, "queue.item", q.slug, f"#{item.id} {item.title[:100]}")
    return item_dict(db, item)


def webhook(db, slug: str, token: str, title: str, body: str = "", category: str = "", source: str = "") -> dict:
    from .. import tenancy
    q = db.scalar(tenancy.unscoped(select(WorkQueue).where(WorkQueue.slug == slug)))
    if q is None or not q.intake_token_hash or not secrets.compare_digest(
            hashlib.sha256((token or "").encode()).hexdigest(), q.intake_token_hash):
        raise Forbidden("token inválido")
    tenancy.set_org(q.org_id or 1)
    item = intake(db, q, title, body, (source or "webhook")[:120], None, "webhook", category)
    return {"id": item.id, "status": item.status, "category": item.category}


# ------------------------------------------------------------------ itens
def item_dict(db, i: WorkItem, detail: bool = False) -> dict:
    q = db.get(WorkQueue, i.queue_id) if i.queue_id else None
    u = db.get(User, i.assignee_user_id) if i.assignee_user_id else None
    late = bool(i.due_at and i.status in OPEN and tasksmod._aware(i.due_at) < now())
    d = {"id": i.id, "title": i.title, "queue": q.slug if q else None, "queue_name": q.name if q else "",
         "category": i.category, "priority": i.priority, "status": i.status, "assignee": (u.name or u.email) if u else None,
         "assignee_email": u.email if u else None, "employee_task": i.employee_task_id, "handled_by": i.handled_by,
         "source": i.source, "requester": i.requester, "due_at": iso(i.due_at) if i.due_at else None, "late": late,
         "sla_due_at": iso(i.sla_due_at) if i.sla_due_at else None, "sla_breached": i.sla_breached,
         "evidence": i.evidence, "playbook_run": i.playbook_run_id, "step": i.step_key,
         "created_at": iso(i.created_at), "done_at": iso(i.done_at) if i.done_at else None}
    if detail:
        d.update(body=i.body, prepared=i.prepared, result=i.result, approved_by=i.approved_by,
                 files=[{"id": f.id, "name": f.name, "size": f.size, "by": f.uploaded_by, "at": iso(f.created_at)}
                        for f in db.scalars(select(WorkItemFile).where(WorkItemFile.item_id == i.id).order_by(WorkItemFile.id))])
    return d


def _item(db, acc: Access, item_id: int) -> WorkItem:
    i = db.get(WorkItem, item_id)
    if i is None or not (_member(acc, i.team_id) or (acc.p.user_id and acc.p.user_id in (i.assignee_user_id, i.requester_user_id))):
        raise PlatformError(f"item #{item_id} não encontrado")
    return i


def items(db, acc: Access, queue: str = "", status: str = "", mine: bool = False) -> list[dict]:
    q = select(WorkItem).order_by(WorkItem.priority, WorkItem.sla_due_at.is_(None), WorkItem.sla_due_at, WorkItem.id)
    if queue:
        qq = _queue(db, queue)
        if not _member(acc, qq.team_id):
            raise Forbidden("você não é do time desta fila")
        q = q.where(WorkItem.queue_id == qq.id)
    if mine:
        q = q.where(WorkItem.assignee_user_id == (acc.p.user_id or -1))
    if status == "open":
        q = q.where(WorkItem.status.in_(OPEN))
    elif status:
        q = q.where(WorkItem.status == status)
    return [item_dict(db, i) for i in db.scalars(q.limit(500))
            if mine or _member(acc, i.team_id) or acc.p.user_id in (i.assignee_user_id, i.requester_user_id)]


def detail(db, acc: Access, item_id: int) -> dict:
    return item_dict(db, _item(db, acc, item_id), True)


def take_next(db, acc: Access, slug: str) -> dict:
    q = _queue(db, slug)
    if not acc.p.user_id or q.team_id not in acc.teams:
        raise Forbidden("só quem é do time pega itens da fila")
    i = db.scalar(select(WorkItem).where(WorkItem.queue_id == q.id, WorkItem.status == "open",
                                         WorkItem.assignee_user_id.is_(None))
                  .order_by(WorkItem.sla_breached.desc(), WorkItem.priority, WorkItem.sla_due_at.is_(None),
                            WorkItem.sla_due_at, WorkItem.id))
    if i is None:
        raise PlatformError("nada na fila agora")
    i.assignee_user_id, i.status, i.started_at = acc.p.user_id, "in_progress", now()
    db.commit()
    return item_dict(db, i, True)


def act(db, acc: Access, item_id: int, action: str, note: str = "", assignee: str = "") -> dict:
    i = _item(db, acc, item_id)
    who = acc.p.name
    if action == "assign":
        u = db.scalar(select(User).where(User.email == (assignee or "").strip().lower())) if assignee else \
            db.get(User, acc.p.user_id) if acc.p.user_id else None
        if u is None:
            raise PlatformError("pessoa não encontrada")
        if i.team_id and not db.scalar(select(TeamMember.id).where(TeamMember.team_id == i.team_id, TeamMember.user_id == u.id)):
            raise PlatformError(f"{u.email} não é do time")
        i.assignee_user_id = u.id
        if i.status in ("open", "waiting"):
            i.status = "in_progress" if i.status == "open" else i.status
            i.started_at = i.started_at or now()
    elif action == "start":
        if i.status != "open":
            raise PlatformError(f"o item está {i.status}")
        i.assignee_user_id = i.assignee_user_id or acc.p.user_id
        i.status, i.started_at = "in_progress", now()
    elif action == "done":
        if i.status not in ("open", "in_progress"):
            raise PlatformError(f"o item está {i.status}")
        if i.evidence == "file" and not db.scalar(select(WorkItemFile.id).where(WorkItemFile.item_id == i.id)):
            raise PlatformError("esta etapa exige evidência: anexe um arquivo antes de concluir")
        i.result = (note or i.result or "")[:20000]
        if i.evidence == "approval":
            i.status = "review"  # outra pessoa aprova
        else:
            _close(db, i, who)
    elif action == "approve":
        if i.status != "review":
            raise PlatformError("nada para aprovar")
        if not _maintainer(acc, i.team_id) or acc.p.user_id == i.assignee_user_id:
            raise Forbidden("quatro olhos: aprova quem mantém o time, e não quem fez")
        i.approved_by = who
        _close(db, i, who)
    elif action == "reject":
        if i.status != "review" or not note:
            raise PlatformError("diga o que ajustar (note)")
        i.status, i.result = "in_progress", f"{i.result}\n\n[ajustar — {who}] {note}"[:20000]
    elif action == "reopen":
        if i.status not in ("done", "cancelled"):
            raise PlatformError("só um item concluído ou cancelado reabre")
        i.status, i.done_at = "open" if not i.assignee_user_id else "in_progress", None
    elif action == "cancel":
        if not (_maintainer(acc, i.team_id) or acc.p.user_id == i.requester_user_id):
            raise Forbidden("cancela quem pediu ou quem mantém o time")
        i.status = "cancelled"
    elif action == "release":
        i.assignee_user_id, i.status = None, "open" if i.status == "in_progress" else i.status
    else:
        raise PlatformError("ação: assign | start | done | approve | reject | reopen | cancel | release")
    db.commit()
    audit(db, who, f"work_item.{action}", f"#{i.id}", (note or assignee)[:120])
    if i.status == "done" and i.playbook_run_id:
        from . import playbooks
        playbooks.advance(db, i.playbook_run_id)
    return item_dict(db, i, True)


def _close(db, i: WorkItem, who: str):
    i.status, i.done_at = "done", now()
    if not i.handled_by:
        i.handled_by = "human"
    elif i.handled_by == "employee" and i.prepared:
        i.handled_by = "assisted"


def attach(db, acc: Access, item_id: int, name: str, data: bytes, content_type: str = "") -> dict:
    from .. import config
    i = _item(db, acc, item_id)
    if len(data) > config.FILES_MAX_MB * 1024 * 1024:
        raise PlatformError(f"arquivo grande demais (máx. {config.FILES_MAX_MB} MB)")
    from .task_files import _clean, _ctype
    name = _clean(name)
    db.add(WorkItemFile(item_id=i.id, name=name, content_type=_ctype(name, content_type), size=len(data), data=data,
                        uploaded_by=acc.p.name, created_at=now()))
    db.commit()
    return item_dict(db, i, True)


def file(db, acc: Access, file_id: int) -> WorkItemFile:
    f = db.get(WorkItemFile, file_id)
    if f is None:
        raise PlatformError("arquivo não encontrado")
    _item(db, acc, f.item_id)
    return f


# ------------------------------------------------------------------ o Digital employee e os itens
def task_finished(db, task: EmployeeTask):
    """O item da fila que um Digital employee tinha acompanha o fim da tarefa (se ele não o devolveu a uma pessoa)."""
    i = db.scalar(select(WorkItem).where(WorkItem.employee_task_id == task.id, WorkItem.status == "employee"))
    if i is None:
        return
    if task.status == "done":
        i.result, i.status, i.done_at, i.handled_by = (task.result or "")[:20000], "done", now(), "employee"
    else:  # falhou: vai para as pessoas, com o que se sabe
        i.status, i.handled_by = "open", "assisted"
        i.prepared = f"O Digital employee não conseguiu terminar: {task.error or task.status}"[:20000]
    db.commit()
    if i.status == "done" and i.playbook_run_id:
        from . import playbooks
        playbooks.advance(db, i.playbook_run_id)


@tool("hand_to_person", "Entrega o trabalho a uma pessoa do time para terminar (quando faltar acesso, julgamento ou "
      "assinatura humana): deixe pronto o que conseguiu (draft) e diga o que falta (summary). Vira um item na fila do "
      "time (ou para a pessoa indicada). Depois, encerre a sua tarefa dizendo o que deixou pronto.",
      {"properties": {"summary": {"type": "string", "description": "o que falta a pessoa fazer"},
                      "draft": {"type": "string", "description": "o que você deixou pronto"},
                      "assignee": {"type": "string", "description": "e-mail de uma pessoa do time (opcional)"},
                      "title": {"type": "string"}}, "required": ["summary"]}, "queues")
def hand_to_person(ctx: Ctx, args: dict):
    db, t = ctx.db, ctx.task
    summary, draft = str(args.get("summary") or "").strip(), str(args.get("draft") or "").strip()
    if not summary:
        raise PlatformError("diga o que falta (summary)")
    i = db.scalar(select(WorkItem).where(WorkItem.employee_task_id == t.id, WorkItem.status == "employee"))
    a = ctx.agent
    if i is None:
        q = db.scalar(select(WorkQueue).where(WorkQueue.team_id == a.team_id).order_by(WorkQueue.id)) if a.team_id else None
        i = WorkItem(queue_id=q.id if q else None, team_id=a.team_id, title=str(args.get("title") or t.title)[:300],
                     body=t.body[:20000], priority=t.priority, source="employee", source_ref=f"task:{t.id}",
                     requester=t.requester, requester_user_id=t.requester_user_id, employee_task_id=t.id,
                     created_by=f"agent:{a.slug}", created_at=now(), due_at=t.due_at)
        db.add(i)
    i.prepared = (f"{summary}\n\n---\n{draft}" if draft else summary)[:20000]
    i.status, i.handled_by = "open", "assisted"
    if args.get("assignee"):
        u = db.scalar(select(User).where(User.email == str(args["assignee"]).strip().lower()))
        if u is not None and (not a.team_id or db.scalar(select(TeamMember.id).where(TeamMember.team_id == a.team_id,
                                                                                      TeamMember.user_id == u.id))):
            i.assignee_user_id, i.status, i.started_at = u.id, "in_progress", now()
    db.flush()
    tasksmod.event(db, t, "handoff", {"to": "person", "item": i.id})
    db.commit()
    return f"Entregue ao time como item #{i.id}. Encerre a tarefa dizendo o que deixou pronto e o que ficou com a pessoa."


def sweep(db) -> int:
    """SLA estourado: marca e avisa quem mantém o time (uma vez por item)."""
    n = 0
    for i in db.scalars(select(WorkItem).where(WorkItem.status.in_(OPEN), WorkItem.sla_breached.is_(False),
                                               WorkItem.sla_due_at.is_not(None), WorkItem.sla_due_at < now())).all():
        i.sla_breached = True
        n += 1
        audit(db, "sla", "work_item.sla_breached", f"#{i.id}", i.title[:120])
    db.commit()
    return n
