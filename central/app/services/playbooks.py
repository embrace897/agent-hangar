"""Playbooks: processos recorrentes (fechamento contábil, folha, onboarding de cliente, auditoria…) como um modelo
de etapas — de pessoas ou de Digital employees — com dependências, prazos em dias úteis a partir de um marco ("D+3
úteis"), critério de pronto e evidência exigida (arquivo ou aprovação de outra pessoa).

Cada execução (ex.: "Fechamento out/2026") cria as tarefas e os itens; uma etapa só começa quando as de que depende
terminam (e recebe o que elas entregaram). A agenda (`@bd 1 08:00`) abre a execução sozinha. O quadro da execução
mostra dono, prazo, atraso e status; o histórico compara as execuções (qual etapa costuma atrasar).
"""
from datetime import UTC, date, datetime, time

from sqlalchemy import select

from ..cron import CronError, zone
from ..models import Agent, Employee, EmployeeTask, Playbook, PlaybookRun, User, WorkItem, WorkQueue, now
from . import bizcal
from . import employee_tasks as tasksmod
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso, slugify

MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _team_ok(acc: Access, team_id: int, need: str = "developer") -> bool:
    return acc.p.is_admin or RANK.get(acc.teams.get(team_id, ""), 0) >= RANK[need]


def _check_steps(db, team_id: int, steps) -> list[dict]:
    out, keys = [], set()
    for i, s in enumerate(steps or []):
        key = slugify(str(s.get("key") or s.get("title") or f"etapa-{i + 1}")).replace("-", "_")[:80]
        if key in keys:
            raise PlatformError(f"etapa repetida: {key}")
        a = s.get("assignee") or {}
        kind, ref = a.get("type"), str(a.get("ref") or "").strip()
        if kind == "employee":
            ag = db.scalar(select(Agent).where(Agent.slug == ref))
            if ag is None or db.scalar(select(Employee.id).where(Employee.agent_id == ag.id)) is None:
                raise PlatformError(f"etapa {key}: Digital employee '{ref}' não encontrado")
        elif kind == "person":
            if db.scalar(select(User.id).where(User.email == ref.lower())) is None:
                raise PlatformError(f"etapa {key}: pessoa '{ref}' não encontrada")
            ref = ref.lower()
        elif kind == "queue":
            if db.scalar(select(WorkQueue.id).where(WorkQueue.slug == ref)) is None:
                raise PlatformError(f"etapa {key}: fila '{ref}' não encontrada")
        else:
            raise PlatformError(f"etapa {key}: assignee.type = employee | person | queue")
        deps = [slugify(str(d)).replace("-", "_") for d in s.get("depends_on") or []]
        missing = [d for d in deps if d not in keys]
        if missing:
            raise PlatformError(f"etapa {key} depende de etapa anterior inexistente: {', '.join(missing)}")
        due = s.get("due") or {}
        frm = slugify(str(due.get("from") or "start")).replace("-", "_")
        if frm != "start" and frm not in keys:
            raise PlatformError(f"etapa {key}: o prazo conta de uma etapa anterior ou do início (start)")
        ev = s.get("evidence") or "none"
        if ev not in ("none", "file", "approval"):
            raise PlatformError(f"etapa {key}: evidence = none | file | approval")
        keys.add(key)
        out.append({"key": key, "title": str(s.get("title") or key)[:200], "body": str(s.get("body") or "")[:8000],
                    "assignee": {"type": kind, "ref": ref}, "depends_on": deps,
                    "due": {"from": frm, "business_days": int(due.get("business_days") or 0)},
                    "evidence": ev, "criteria": str(s.get("criteria") or "")[:2000]})
    if not out:
        raise PlatformError("o playbook precisa de pelo menos uma etapa")
    return out


def playbook_dict(db, p: Playbook, acc: Access | None = None) -> dict:
    from ..models import Team
    t = db.get(Team, p.team_id)
    runs = list(db.scalars(select(PlaybookRun).where(PlaybookRun.playbook_id == p.id).order_by(PlaybookRun.id.desc()).limit(12)))
    return {"name": p.name, "title": p.title or p.name, "description": p.description, "team": t.slug if t else None,
            "steps": p.steps, "version": p.version, "status": p.status, "schedule": p.schedule,
            "schedule_text": _describe(p.schedule), "run_title": p.run_title,
            "next_run_at": iso(p.next_run_at) if p.next_run_at else None,
            "runs": [{"id": r.id, "title": r.title, "status": r.status, "anchor": r.anchor, "created_at": iso(r.created_at)}
                     for r in runs],
            "can_edit": bool(acc and _team_ok(acc, p.team_id))}


def _describe(expr: str) -> str:
    if not expr:
        return ""
    try:
        return bizcal.describe(expr)
    except CronError:
        return expr


def _get(db, name: str) -> Playbook:
    p = db.scalar(select(Playbook).where(Playbook.name == slugify(name or "").replace("-", "_")))
    if p is None:
        raise PlatformError(f"playbook '{name}' não encontrado")
    return p


def list_playbooks(db, acc: Access) -> list[dict]:
    return [playbook_dict(db, p, acc) for p in db.scalars(select(Playbook).where(Playbook.status == "active")
                                                         .order_by(Playbook.name))
            if acc.p.is_admin or acc.p.is_auditor or p.team_id in acc.teams]


def detail(db, acc: Access, name: str) -> dict:
    p = _get(db, name)
    if not (acc.p.is_admin or acc.p.is_auditor or p.team_id in acc.teams):
        raise PlatformError(f"playbook '{name}' não encontrado")
    d = playbook_dict(db, p, acc)
    d["history"] = history(db, p)
    return d


def save(db, acc: Access, name: str, team: str, title: str = "", description: str = "", steps=None,
         schedule: str = "", run_title: str = "") -> dict:
    from ..models import Team
    t = db.scalar(select(Team).where(Team.slug == team))
    if t is None:
        raise PlatformError(f"time '{team}' não encontrado")
    if not _team_ok(acc, t.id):
        raise Forbidden("só quem desenvolve no time cria e muda playbooks")
    key = slugify(name or title).replace("-", "_")[:80]
    if not key:
        raise PlatformError("dê um nome ao playbook")
    clean = _check_steps(db, t.id, steps)
    if schedule:
        try:
            bizcal.validate(schedule)
        except CronError as e:
            raise PlatformError(f"agenda inválida: {e}") from None
    p = db.scalar(select(Playbook).where(Playbook.name == key))
    if p is not None and p.team_id != t.id:
        raise PlatformError(f"já existe um playbook '{key}' de outro time")
    if p is None:
        p = Playbook(name=key, team_id=t.id, created_by=acc.p.name, version=0)
        db.add(p)
    changed = p.steps != clean
    p.title, p.description, p.steps = (title or key)[:200], (description or "")[:4000], clean
    p.version += 1 if changed or not p.version else 0
    p.schedule, p.run_title, p.status, p.updated_at = (schedule or "").strip(), (run_title or "")[:200], "active", now()
    p.next_run_at = bizcal.next_after(db, p.schedule, now(), _tz(db)) if p.schedule else None
    db.commit()
    audit(db, acc.p.name, "playbook.save", key, f"v{p.version} {len(clean)} etapa(s)")
    return playbook_dict(db, p, acc)


def archive(db, acc: Access, name: str) -> dict:
    p = _get(db, name)
    if not _team_ok(acc, p.team_id):
        raise Forbidden("sem permissão")
    p.status, p.next_run_at = "archived", None
    db.commit()
    return playbook_dict(db, p, acc)


def _tz(db) -> str:
    from ..models import Organization
    from ..tenancy import current_org
    o = db.get(Organization, current_org())
    return (o.timezone if o else None) or "UTC"


# ------------------------------------------------------------------ execução
def _due(db, cal, base: date, n: int) -> datetime:
    d = cal.add(base, n)
    end = time.fromisoformat(cal.end)
    return datetime.combine(d, end, tzinfo=zone(_tz(db))).astimezone(UTC)


def start(db, p: Playbook, anchor: date, title: str, actor: str, actor_id: int | None = None) -> PlaybookRun:
    cal = bizcal.of(db)
    run = PlaybookRun(playbook_id=p.id, version=p.version, title=title[:300], anchor=anchor.isoformat(), status="running",
                      created_by=actor, created_at=now(), steps={})
    db.add(run)
    db.flush()
    dues: dict[str, date] = {}
    steps = {}
    for s in p.steps:
        base = anchor if s["due"]["from"] == "start" else dues[s["due"]["from"]]
        due_at = _due(db, cal, base, s["due"]["business_days"])
        dues[s["key"]] = due_at.astimezone(zone(_tz(db))).date()
        waiting = bool(s["depends_on"])
        body = (s["body"] + (f"\n\nCritério de pronto: {s['criteria']}" if s["criteria"] else "")
                + f"\n\n(Etapa '{s['title']}' do playbook {p.title}, execução \"{run.title}\".)").strip()
        a = s["assignee"]
        if a["type"] == "employee":
            ag = db.scalar(select(Agent).where(Agent.slug == a["ref"]))
            e = db.scalar(select(Employee).where(Employee.agent_id == ag.id))
            t = tasksmod.create_task(db, e, f"{run.title}: {s['title']}"[:300], body, "playbook", actor, actor_id, 2, due_at,
                                     status="blocked" if waiting else "new")
            if s["evidence"] == "approval":
                t.review_required = True
            steps[s["key"]] = {"kind": "task", "id": t.id, "due": due_at.isoformat(), "title": s["title"], "owner": ag.name}
        else:
            u = db.scalar(select(User).where(User.email == a["ref"])) if a["type"] == "person" else None
            q = db.scalar(select(WorkQueue).where(WorkQueue.slug == a["ref"])) if a["type"] == "queue" else None
            i = WorkItem(queue_id=q.id if q else None, team_id=p.team_id, title=f"{run.title}: {s['title']}"[:300], body=body,
                         status="waiting" if waiting else ("in_progress" if u else "open"), assignee_user_id=u.id if u else None,
                         source="playbook", source_ref=f"run:{run.id}", requester=actor, requester_user_id=actor_id,
                         due_at=due_at, evidence=s["evidence"], playbook_run_id=run.id, step_key=s["key"],
                         created_by=actor, created_at=now())
            db.add(i)
            db.flush()
            steps[s["key"]] = {"kind": "item", "id": i.id, "due": due_at.isoformat(), "title": s["title"],
                               "owner": (u.name or u.email) if u else (q.name if q else "")}
    run.steps = steps
    db.commit()
    return run


def run_now(db, acc: Access, name: str, anchor: str = "", title: str = "") -> dict:
    p = _get(db, name)
    if not _team_ok(acc, p.team_id, "developer") and p.team_id not in acc.teams:
        raise Forbidden("só quem é do time abre uma execução")
    d = date.fromisoformat(anchor) if anchor else datetime.now(UTC).astimezone(zone(_tz(db))).date()
    run = start(db, p, d, title or _title(p, d), acc.p.name, acc.p.user_id)
    audit(db, acc.p.name, "playbook.run", p.name, f"#{run.id} {run.title}")
    return run_dict(db, run)


def _title(p: Playbook, d: date) -> str:
    t = p.run_title or f"{p.title} {{data}}"
    return t.replace("{mes}", MONTHS[d.month - 1]).replace("{ano}", str(d.year)).replace("{data}", d.strftime("%d/%m/%Y"))


def _step_state(db, s: dict) -> tuple[str, object]:
    obj = db.get(EmployeeTask, s["id"]) if s["kind"] == "task" else db.get(WorkItem, s["id"])
    if obj is None:
        return "cancelled", None
    st = obj.status
    if st in ("done",):
        return "done", obj
    if st in ("failed", "cancelled", "expired"):
        return st if st != "expired" else "failed", obj
    if st in ("blocked", "waiting"):
        return "waiting", obj
    return "in_progress" if st not in ("new", "open") else "open", obj


def advance(db, run_id: int):
    """Libera as etapas cujas dependências terminaram (com o que elas entregaram) e fecha a execução no fim."""
    run = db.get(PlaybookRun, run_id)
    if run is None or run.status != "running":
        return
    p = db.get(Playbook, run.playbook_id)
    defs = {s["key"]: s for s in p.steps} if p else {}
    states = {k: _step_state(db, s) for k, s in run.steps.items()}
    for k, s in run.steps.items():
        st, obj = states[k]
        deps = (defs.get(k) or {}).get("depends_on") or []
        if st != "waiting" or not all(states.get(d, ("done",))[0] == "done" for d in deps):
            continue
        ctx = []
        for d in deps:
            _, dobj = states[d]
            out = (getattr(dobj, "result", "") or "")[:3000]
            ctx.append(f"ETAPA PRÉVIA '{run.steps[d]['title']}' CONCLUÍDA:\n{out or '(sem texto)'}")
        if s["kind"] == "task":
            t = obj
            e = db.get(Employee, t.employee_id)
            cp = tasksmod.last_checkpoint(db, t)
            prompt = list(cp.messages) if cp else [{"role": "user", "content": tasksmod.task_prompt(t, tasksmod.lang_of(e))}]
            tasksmod.checkpoint(db, t, [{"role": "user", "content": "\n\n".join(ctx)}, *prompt] if ctx else prompt)
            t.status = "new"
            tasksmod.event(db, t, "unblocked", {"playbook_run": run.id})
        else:
            obj.status = "in_progress" if obj.assignee_user_id else "open"
            if ctx:
                obj.body = (obj.body + "\n\n" + "\n\n".join(ctx))[:20000]
        states[k] = ("open", obj)
    if all(st == "done" for st, _ in states.values()):
        run.status, run.finished_at = "done", now()
    db.commit()


def task_finished(db, task: EmployeeTask):
    for run in db.scalars(select(PlaybookRun).where(PlaybookRun.status == "running")):
        if any(s["kind"] == "task" and s["id"] == task.id for s in run.steps.values()):
            advance(db, run.id)
            return


def run_dict(db, run: PlaybookRun) -> dict:
    p = db.get(Playbook, run.playbook_id)
    defs = {s["key"]: s for s in (p.steps if p else [])}
    steps = []
    for k, s in run.steps.items():
        st, obj = _step_state(db, s)
        due = datetime.fromisoformat(s["due"])
        done_at = getattr(obj, "finished_at", None) or getattr(obj, "done_at", None)
        late = (st != "done" and due < now()) or bool(done_at and tasksmod._aware(done_at) > due)
        steps.append({"key": k, "title": s["title"], "kind": s["kind"], "id": s["id"], "owner": s.get("owner"),
                      "due": s["due"], "status": st, "late": late, "depends_on": (defs.get(k) or {}).get("depends_on", []),
                      "evidence": (defs.get(k) or {}).get("evidence", "none"),
                      "done_at": iso(tasksmod._aware(done_at)) if done_at else None,
                      "link": f"#/tasks/{s['id']}" if s["kind"] == "task" else f"#/work/{s['id']}"})
    return {"id": run.id, "playbook": p.name if p else None, "playbook_title": p.title if p else "", "title": run.title,
            "anchor": run.anchor, "status": run.status, "version": run.version, "created_by": run.created_by,
            "created_at": iso(run.created_at), "finished_at": iso(run.finished_at) if run.finished_at else None,
            "steps": steps, "done": sum(s["status"] == "done" for s in steps), "late": sum(s["late"] for s in steps)}


def run_detail(db, acc: Access, run_id: int) -> dict:
    run = db.get(PlaybookRun, run_id)
    p = db.get(Playbook, run.playbook_id) if run else None
    if run is None or p is None or not (acc.p.is_admin or acc.p.is_auditor or p.team_id in acc.teams):
        raise PlatformError(f"execução #{run_id} não encontrada")
    return run_dict(db, run)


def history(db, p: Playbook, n: int = 6) -> list[dict]:
    """Por etapa: em quantas das últimas execuções atrasou."""
    runs = [run_dict(db, r) for r in db.scalars(select(PlaybookRun).where(PlaybookRun.playbook_id == p.id)
                                                 .order_by(PlaybookRun.id.desc()).limit(n))]
    out = []
    for s in p.steps:
        seen = [st for r in runs for st in r["steps"] if st["key"] == s["key"]]
        out.append({"key": s["key"], "title": s["title"], "runs": len(seen), "late": sum(x["late"] for x in seen)})
    return out


def sweep(db, at: datetime | None = None) -> int:
    """Abre as execuções agendadas (uma por vez por playbook; seguro com várias réplicas)."""
    from sqlalchemy import update
    at, n = at or now(), 0
    for p in db.scalars(select(Playbook).where(Playbook.status == "active", Playbook.next_run_at.is_not(None),
                                               Playbook.next_run_at <= at)).all():
        old = p.next_run_at
        nxt = bizcal.next_after(db, p.schedule, at, _tz(db))
        res = db.execute(update(Playbook).where(Playbook.id == p.id, Playbook.next_run_at == old).values(next_run_at=nxt))
        db.commit()
        if res.rowcount != 1:
            continue
        d = at.astimezone(zone(_tz(db))).date()
        run = start(db, p, d, _title(p, d), f"agenda:{p.name}")
        audit(db, "agenda", "playbook.run", p.name, f"#{run.id} {run.title}")
        n += 1
    return n
