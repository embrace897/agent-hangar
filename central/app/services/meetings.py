"""Reuniões: marcar pela agenda da empresa, preparar o briefing antes, transformar a transcrição em ata e as ações em
tarefas (de pessoas ou de Digital employees) com prazo — e cobrar na reunião seguinte da mesma série o que ficou
aberto.

- O Digital employee acha horários (`find_meeting_times`) e convida (`schedule_meeting`, pela alçada: convite para
  gente de fora é `send_external`).
- A transcrição chega pelo portal ou pelo webhook (`/hooks/meetings/<empresa>`, com o token da empresa) — Teams,
  Meet e Zoom exportam o texto; o funcionário escreve a ata e registra decisões e ações (`record_minutes`).
- Briefing: `prep_hours` antes do início, o funcionário prepara o pacote (pauta, números, pendências da anterior).
"""
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .. import config, tenancy
from ..models import Agent, Employee, EmployeeTask, Meeting, Organization, Team, User, WorkItem, now
from . import bizcal, calendar_api, notify, task_files
from . import employee_tasks as tasksmod
from .access import Access, Forbidden
from .common import PlatformError, audit, iso
from .platform_tools import Ctx, side_effect, tool


def _cal_ready(db, e) -> bool:
    return bool(calendar_api.provider(notify.org_cfg(db)))


def _org_domains(db) -> set[str]:
    o = db.get(Organization, tenancy.current_org())
    return {d.lower() for d in (o.domains or [])} if o else set()


def _external(db, emails: list[str]) -> list[str]:
    """Convidados de fora da empresa (domínio que não é da empresa e e-mail que não é de uma pessoa da plataforma)."""
    doms = _org_domains(db)
    return [m for m in emails if m.rsplit("@", 1)[-1] not in doms and db.scalar(select(User.id).where(User.email == m)) is None]


# ------------------------------------------------------------------ ferramentas
@tool("find_meeting_times", "Acha horários em que todos estão livres, nos dias úteis e no expediente da empresa.",
      {"properties": {"attendees": {"type": "array", "items": {"type": "string"}, "description": "e-mails"},
                      "duration_min": {"type": "integer"}, "from": {"type": "string", "description": "AAAA-MM-DD"},
                      "to": {"type": "string", "description": "AAAA-MM-DD"}, "organizer": {"type": "string"}},
       "required": ["attendees", "duration_min"]}, "calendar", _cal_ready)
def find_meeting_times(ctx: Ctx, args: dict):
    cfg, cal = notify.org_cfg(ctx.db), bizcal.of(ctx.db)
    people = [str(x).strip().lower() for x in args.get("attendees") or [] if str(x).strip()]
    organizer = str(args.get("organizer") or _manager_email(ctx)).lower()
    z = bizcal.zone(cal.tz)
    start = datetime.fromisoformat(args["from"]).replace(tzinfo=z) if args.get("from") else now()
    end = datetime.fromisoformat(args["to"]).replace(tzinfo=z) + timedelta(days=1) if args.get("to") else start + timedelta(days=7)
    busy = calendar_api.busy(cfg, organizer, sorted({*people, organizer}), start.astimezone(UTC), end.astimezone(UTC))
    slots = calendar_api.free_slots(cal, busy, start, end, max(15, min(int(args.get("duration_min") or 30), 480)))
    return {"slots": [s.astimezone(z).strftime("%Y-%m-%d %H:%M") for s in slots], "timezone": cal.tz,
            "organizer": organizer} if slots else "Nenhum horário livre para todos na janela: amplie o período ou reduza a lista."


def _manager_email(ctx: Ctx) -> str:
    u = ctx.db.get(User, ctx.e.manager_user_id)
    return u.email if u else ""


@tool("schedule_meeting", "Marca a reunião na agenda da empresa e convida as pessoas (o organizador é o seu gestor, "
      "ou quem você indicar). Convite para gente de fora passa pela alçada como envio externo.",
      {"properties": {"title": {"type": "string"}, "start": {"type": "string", "description": "AAAA-MM-DD HH:MM (fuso da empresa)"},
                      "duration_min": {"type": "integer"}, "attendees": {"type": "array", "items": {"type": "string"}},
                      "agenda": {"type": "string"}, "organizer": {"type": "string"}, "series": {"type": "string"}},
       "required": ["title", "start", "attendees"]}, "calendar", _cal_ready)
def schedule_meeting(ctx: Ctx, args: dict):
    db, cal = ctx.db, bizcal.of(ctx.db)
    people = sorted({str(x).strip().lower() for x in args.get("attendees") or [] if str(x).strip()})
    organizer = str(args.get("organizer") or _manager_email(ctx)).lower()
    try:
        start = datetime.strptime(str(args["start"]), "%Y-%m-%d %H:%M").replace(tzinfo=bizcal.zone(cal.tz))
    except ValueError:
        raise PlatformError("start: AAAA-MM-DD HH:MM") from None
    end = start + timedelta(minutes=max(15, min(int(args.get("duration_min") or 30), 480)))
    out = _external(db, people)
    ref = "platform:send_meeting_invite" if out else "platform:schedule_meeting"
    held = side_effect(ctx, "schedule_meeting", ref, {"title": args["title"], "start": args["start"], "attendees": people,
                                                       "external": out})
    if held:
        return held
    ext_id = calendar_api.create_event(notify.org_cfg(db), organizer, str(args["title"])[:300], start.astimezone(UTC),
                                       end.astimezone(UTC), people, str(args.get("agenda") or ""))
    m = Meeting(title=str(args["title"])[:300], series=str(args.get("series") or "")[:120], starts_at=start.astimezone(UTC),
                ends_at=end.astimezone(UTC), organizer=organizer, attendees=people, agenda=str(args.get("agenda") or "")[:8000],
                team_id=ctx.agent.team_id, employee_id=ctx.e.id, provider=calendar_api.provider(notify.org_cfg(db)),
                external_id=ext_id, created_by=f"agent:{ctx.agent.slug}", created_at=now())
    db.add(m)
    db.commit()
    return f"Reunião #{m.id} marcada para {args['start']} ({cal.tz}), {len(people)} convidado(s)."


@tool("record_minutes", "Registra a ata de uma reunião: resumo, decisões e ações. Cada ação vira tarefa com prazo: "
      "owner = e-mail de uma pessoa (vira item para ela) ou slug de um Digital employee (vira tarefa dele).",
      {"properties": {"meeting_id": {"type": "integer"}, "summary": {"type": "string"},
                      "decisions": {"type": "array", "items": {"type": "string"}},
                      "actions": {"type": "array", "items": {"type": "object", "properties": {
                          "title": {"type": "string"}, "owner": {"type": "string"}, "due": {"type": "string", "description": "AAAA-MM-DD"}},
                          "required": ["title", "owner"]}}},
       "required": ["meeting_id", "summary"]}, "calendar")
def record_minutes(ctx: Ctx, args: dict):
    db = ctx.db
    m = db.get(Meeting, int(args.get("meeting_id") or 0))
    if m is None:
        raise PlatformError("reunião não encontrada")
    m.minutes = str(args.get("summary") or "")[:20000]
    m.decisions = [str(d)[:1000] for d in args.get("decisions") or []]
    m.status = "minutes"
    made = []
    cal = bizcal.of(db)
    for n, a in enumerate(args.get("actions") or [], start=1):
        title, owner = str(a.get("title") or "").strip()[:300], str(a.get("owner") or "").strip()
        if not title or not owner:
            continue
        due = None
        if a.get("due"):
            try:
                d = datetime.fromisoformat(str(a["due"])[:10]).date()
                due = datetime.combine(d, datetime.strptime(cal.end, "%H:%M").time(), tzinfo=bizcal.zone(cal.tz)).astimezone(UTC)
            except ValueError:
                pass
        ag = db.scalar(select(Agent).where(Agent.slug == owner))
        e = db.scalar(select(Employee).where(Employee.agent_id == ag.id)) if ag else None
        if e is not None:
            t = tasksmod.create_task(db, e, title, f"Ação da reunião \"{m.title}\" (#{m.id}).\n\nAta:\n{m.minutes[:3000]}",
                                     "meeting", m.organizer or "reunião", None, 2, due, dedupe_key=f"meeting:{m.id}:{n}")
            made.append(f"tarefa #{t.id} ({ag.name})")
            continue
        u = db.scalar(select(User).where(User.email == owner.lower()))
        team = m.team_id or ctx.agent.team_id
        i = WorkItem(team_id=team, title=title, body=f"Ação da reunião \"{m.title}\" (#{m.id}).", source="meeting",
                     source_ref=f"meeting:{m.id}", status="in_progress" if u else "open", assignee_user_id=u.id if u else None,
                     requester=m.organizer, due_at=due, created_by=f"agent:{ctx.agent.slug}", created_at=now())
        db.add(i)
        db.flush()
        made.append(f"item #{i.id} ({owner})")
    db.commit()
    return f"Ata da reunião #{m.id} registrada; {len(made)} ação(ões): {', '.join(made) or 'nenhuma'}."


# ------------------------------------------------------------------ portal
def meeting_dict(db, m: Meeting, detail: bool = False) -> dict:
    e = db.get(Employee, m.employee_id) if m.employee_id else None
    a = db.get(Agent, e.agent_id) if e else None
    d = {"id": m.id, "title": m.title, "series": m.series, "starts_at": iso(m.starts_at) if m.starts_at else None,
         "ends_at": iso(m.ends_at) if m.ends_at else None, "organizer": m.organizer, "attendees": m.attendees,
         "status": m.status, "employee": a.slug if a else None, "employee_name": a.name if a else None,
         "provider": m.provider, "prep_hours": m.prep_hours, "prep_task": m.prep_task_id, "minutes_task": m.minutes_task_id}
    if detail:
        acts = actions(db, m)
        d.update(agenda=m.agenda, minutes=m.minutes, decisions=m.decisions or [], transcript=bool(m.transcript), actions=acts,
                 previous_open=_previous_open(db, m))
    return d


def actions(db, m: Meeting) -> list[dict]:
    out = []
    for i in db.scalars(select(WorkItem).where(WorkItem.source_ref == f"meeting:{m.id}").order_by(WorkItem.id)):
        u = db.get(User, i.assignee_user_id) if i.assignee_user_id else None
        out.append({"kind": "item", "id": i.id, "title": i.title, "owner": (u.email if u else "time"), "status": i.status,
                    "due": iso(i.due_at) if i.due_at else None, "link": f"#/work/{i.id}"})
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.dedupe_key.like(f"meeting:{m.id}:%")).order_by(EmployeeTask.id)):
        e = db.get(Employee, t.employee_id)
        a = db.get(Agent, e.agent_id) if e else None
        out.append({"kind": "task", "id": t.id, "title": t.title, "owner": a.name if a else "", "status": t.status,
                    "due": iso(t.due_at) if t.due_at else None, "link": f"#/tasks/{t.id}"})
    return out


def _previous_open(db, m: Meeting) -> list[dict]:
    if not m.series:
        return []
    prev = db.scalars(select(Meeting).where(Meeting.series == m.series, Meeting.id != m.id,
                                            Meeting.starts_at < (m.starts_at or now())).order_by(Meeting.starts_at.desc()).limit(5))
    return [{**a, "meeting": p.id} for p in prev for a in actions(db, p) if a["status"] not in ("done", "cancelled", "failed")]


def _visible(acc: Access, m: Meeting) -> bool:
    u = acc.p.name.lower()
    return acc.p.is_admin or acc.p.is_auditor or (m.team_id in acc.teams) or u == m.organizer or u in (m.attendees or [])


def list_meetings(db, acc: Access) -> list[dict]:
    return [meeting_dict(db, m) for m in db.scalars(select(Meeting).order_by(Meeting.starts_at.desc().nullslast(), Meeting.id.desc())
                                                    .limit(200)) if _visible(acc, m)]


def get(db, acc: Access, mid: int) -> dict:
    m = db.get(Meeting, mid)
    if m is None or not _visible(acc, m):
        raise PlatformError("reunião não encontrada")
    return meeting_dict(db, m, True)


def create(db, acc: Access, title: str, starts_at: str = "", duration_min: int = 30, attendees=None, agenda: str = "",
           team: str = "", employee: str = "", prep_hours: int = 0, series: str = "", schedule: bool = False) -> dict:
    team_id = None
    if team:
        t = db.scalar(select(Team).where(Team.slug == team))
        if t is None:
            raise PlatformError(f"time '{team}' não encontrado")
        if not (acc.p.is_admin or t.id in acc.teams):
            raise Forbidden("você não é deste time")
        team_id = t.id
    e_id = None
    if employee:
        from .employees import find
        e, _ = find(db, acc, employee, "assign")
        e_id = e.id
    cal = bizcal.of(db)
    start = None
    if starts_at:
        try:
            start = datetime.strptime(starts_at[:16].replace("T", " "), "%Y-%m-%d %H:%M").replace(tzinfo=bizcal.zone(cal.tz))
        except ValueError:
            raise PlatformError("starts_at: AAAA-MM-DD HH:MM") from None
    people = sorted({str(x).strip().lower() for x in attendees or [] if str(x).strip()})
    organizer = acc.p.name.lower() if "@" in acc.p.name else ""
    m = Meeting(title=(title or "").strip()[:300] or "Reunião", series=(series or "")[:120],
                starts_at=start.astimezone(UTC) if start else None,
                ends_at=(start + timedelta(minutes=int(duration_min or 30))).astimezone(UTC) if start else None,
                organizer=organizer, attendees=people, agenda=(agenda or "")[:8000], team_id=team_id, employee_id=e_id,
                prep_hours=max(0, min(int(prep_hours or 0), 72)), created_by=acc.p.name, created_at=now())
    if schedule:
        cfg = notify.org_cfg(db)
        if not calendar_api.provider(cfg):
            raise PlatformError("a agenda da empresa não está configurada")
        if not start or not organizer:
            raise PlatformError("para marcar na agenda: início e um organizador com e-mail")
        m.external_id = calendar_api.create_event(cfg, organizer, m.title, m.starts_at, m.ends_at, people, m.agenda)
        m.provider = calendar_api.provider(cfg)
    db.add(m)
    db.commit()
    audit(db, acc.p.name, "meeting.create", f"#{m.id}", m.title[:120])
    return meeting_dict(db, m, True)


def delete(db, acc: Access, mid: int) -> dict:
    m = db.get(Meeting, mid)
    if m is None or not _visible(acc, m):
        raise PlatformError("reunião não encontrada")
    if not (acc.p.is_admin or acc.p.name.lower() in (m.organizer, (m.created_by or "").lower())):
        raise Forbidden("remove quem organizou a reunião (ou o admin)")
    db.delete(m)
    db.commit()
    audit(db, acc.p.name, "meeting.delete", f"#{mid}", "")
    return {"deleted": mid}


def add_transcript(db, m: Meeting, text: str, who: str) -> Meeting:
    if not (text or "").strip():
        raise PlatformError("a transcrição está vazia")
    m.transcript, m.status = text[:400_000], "held"
    db.commit()
    if m.employee_id and not m.minutes_task_id:
        e = db.get(Employee, m.employee_id)
        pending = _previous_open(db, m)
        body = (f"Escreva a ata da reunião \"{m.title}\" (#{m.id}) a partir da transcrição anexa (transcricao.txt): "
                f"resumo, decisões e ações com dono e prazo, e registre com record_minutes (meeting_id={m.id})."
                + ("\n\nPendências da reunião anterior desta série (verifique se foram tratadas):\n"
                   + "\n".join(f"- {p['title']} ({p['owner']}, {p['status']})" for p in pending) if pending else ""))
        t = tasksmod.create_task(db, e, f"Ata: {m.title}"[:300], body, "meeting", who, None, 2, status="draft")
        task_files.store(db, t, "transcricao.txt", m.transcript.encode(), "input", who, "text/plain", f"reunião #{m.id}")
        t.status = "new"
        m.minutes_task_id = t.id
        db.commit()
    return m


def transcript(db, acc: Access, mid: int, text: str) -> dict:
    m = db.get(Meeting, mid)
    if m is None or not _visible(acc, m):
        raise PlatformError("reunião não encontrada")
    add_transcript(db, m, text, acc.p.name)
    audit(db, acc.p.name, "meeting.transcript", f"#{m.id}", f"{len(text)} caracteres")
    return meeting_dict(db, m, True)


def hook_token(db, acc: Access) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin gera o token das transcrições")
    import hashlib
    tok = "mtg_" + secrets.token_urlsafe(24)
    o = db.get(Organization, tenancy.current_org())
    o.notify = {**(o.notify or {}), "meetings_token": hashlib.sha256(tok.encode()).hexdigest(), "meetings_hint": tok[-4:]}
    db.commit()
    return {"token": tok, "url": f"{config.PUBLIC_BASE_URL}/hooks/meetings/{o.id}"}


def hook(db, org_id: int, token: str, payload: dict) -> dict:
    import hashlib
    import hmac
    o = db.get(Organization, org_id)
    want = ((o.notify or {}) if o else {}).get("meetings_token") or ""
    if not want or not hmac.compare_digest(hashlib.sha256((token or "").encode()).hexdigest(), want):
        raise Forbidden("token inválido")
    tenancy.set_org(o.id)
    m = db.get(Meeting, int(payload.get("meeting_id") or 0)) if payload.get("meeting_id") else None
    if m is None and payload.get("external_id"):
        m = db.scalar(select(Meeting).where(Meeting.external_id == str(payload["external_id"])))
    if m is None:
        return {"error": "reunião não encontrada (mande meeting_id ou external_id)"}
    add_transcript(db, m, str(payload.get("transcript") or ""), "webhook")
    return {"id": m.id, "minutes_task": m.minutes_task_id}


def sweep(db, at: datetime | None = None) -> int:
    """Briefings: N horas antes do início, o funcionário prepara o pacote."""
    at, n = at or now(), 0
    for m in db.scalars(select(Meeting).where(Meeting.prep_hours > 0, Meeting.prep_task_id.is_(None),
                                              Meeting.employee_id.is_not(None), Meeting.starts_at.is_not(None),
                                              Meeting.starts_at > at)).all():
        if tasksmod._aware(m.starts_at) - timedelta(hours=m.prep_hours) > at:
            continue
        e = db.get(Employee, m.employee_id)
        if e is None or e.status not in ("probation", "active"):
            continue
        pending = _previous_open(db, m)
        body = (f"Prepare o briefing da reunião \"{m.title}\" ({iso(m.starts_at)}): objetivo, pauta, números e documentos "
                f"relevantes, perguntas em aberto.\nPauta: {m.agenda or '(sem pauta)'}\nParticipantes: {', '.join(m.attendees or [])}"
                + ("\nPendências da anterior:\n" + "\n".join(f"- {p['title']} ({p['owner']}, {p['status']})" for p in pending)
                   if pending else ""))
        t = tasksmod.create_task(db, e, f"Briefing: {m.title}"[:300], body, "meeting", m.organizer or "reunião", None, 1,
                                 m.starts_at)
        m.prep_task_id = t.id
        db.commit()
        n += 1
    return n

