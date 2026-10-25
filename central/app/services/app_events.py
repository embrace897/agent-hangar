"""Eventos do app Hangar: GET /api/app/events (SSE; contrato em docs/app-hangar-workspace.md, seção 2).

Fila no banco, para funcionar com várias réplicas sem infraestrutura nova:
- Gravação: um ouvinte `after_flush` olha o que mudou na sessão (pedido de decisão novo ou decidido, tarefa de um
  Digital employee, lido/arquivado da caixa, status de agente e de Digital employee, conexão/modelo de LLM, política de
  código) e grava linhas em `app_events` na mesma transação: rollback não deixa evento fantasma.
- Entrega: cada conexão SSE lê do banco, a cada POLL segundos, as linhas da empresa com id acima do cursor (as de
  `user_id` vazio ou da própria pessoa) e decide **na entrega** se a pessoa pode ver cada uma, com o acesso atual dela.
  Assim qualquer réplica entrega qualquer evento, e uma permissão retirada vale na hora.
- `id:` do SSE = id da linha. Uma transação mais longa pode gravar um id menor depois que o cursor passou: o leitor
  revisita as últimas LOOKBACK linhas e entrega a atrasada sem `id:` (o cursor do cliente nunca anda para trás).
- Retenção curta (RETENTION); o cliente que volta com um Last-Event-ID mais velho que isso recebe o que sobrou e
  confere as listas (o Desktop sempre refaz as listas ao reconectar).
"""
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, event, func, inspect, or_, select
from sqlalchemy.orm import Session

from .. import auth, tenancy
from ..models import (
    Agent,
    AgentVersion,
    AppEvent,
    Employee,
    EmployeeTask,
    HumanRequest,
    InboxState,
    LlmConnection,
    LlmModel,
    Organization,
    TeamMember,
    now,
)
from .access import Access
from .access import of as access_of

KINDS = ("inbox.changed", "decision.created", "decision.decided", "agent.status", "models.changed")
POLL = 2.0  # segundos entre leituras da fila
KEEPALIVE = 25.0  # comentário ": keepalive" quando nada foi enviado nesse tempo
LIFETIME = 1800.0  # a conexão fecha depois disso; o cliente volta com Last-Event-ID
REAUTH = 60.0  # de quanto em quanto tempo o token é conferido de novo (autorização encerrada fecha o stream)
RETRY_MS = 5000
LOOKBACK = 200  # linhas revisitadas atrás do cursor (transações que gravaram um id menor e terminaram depois)
RETENTION = timedelta(hours=24)
_HIDDEN = ("shadow", "cross_review")  # pedidos que não aparecem em Decisões (services/employee_tasks.pending_for)
# o que muda a lista de GET /api/app/models
_CONN_FIELDS = ("name", "provider", "protocol", "allow_code", "sync_desktop", "desktop_hidden_models",
                "price_in_per_mtok", "price_out_per_mtok")
_MODEL_FIELDS = ("model_id", "name", "kind", "caps", "context_length", "price_in_per_mtok", "price_out_per_mtok",
                 "available", "connection_id")


# ------------------------------------------------------------------ gravação
def _changed(obj, *names: str) -> bool:
    st = inspect(obj)
    return any(st.attrs[n].history.has_changes() for n in names)


def _was(obj, name: str):
    old = inspect(obj).attrs[name].history.deleted
    return old[0] if old else None


def _rows(session: Session) -> list[dict]:
    rows: list[dict] = []
    models_orgs: set[int] = set()

    def add(org, kind, ref="", user_id=None, data=None):
        rows.append({"org_id": org or 1, "kind": kind, "ref": ref, "user_id": user_id, "data": data})

    for obj in session.new:
        if isinstance(obj, HumanRequest) and obj.kind not in _HIDDEN:
            add(obj.org_id, "decision.created", f"decision:{obj.id}")
            add(obj.org_id, "inbox.changed", f"decision:{obj.id}")
        elif isinstance(obj, EmployeeTask):
            add(obj.org_id, "inbox.changed", f"task:{obj.id}")
        elif isinstance(obj, InboxState):
            add(obj.org_id, "inbox.changed", user_id=obj.user_id)
        elif isinstance(obj, Agent):
            add(obj.org_id, "agent.status", f"agent:{obj.id}")
        elif isinstance(obj, Employee):
            add(obj.org_id, "agent.status", f"employee:{obj.id}")
        elif isinstance(obj, (LlmConnection, LlmModel)):
            models_orgs.add(obj.org_id or 1)
    for obj in session.dirty:
        if isinstance(obj, HumanRequest) and obj.kind not in _HIDDEN:
            if _changed(obj, "status") and _was(obj, "status") == "open" and obj.status != "open":
                add(obj.org_id, "decision.decided", f"decision:{obj.id}")
                add(obj.org_id, "inbox.changed", f"decision:{obj.id}")
            elif _changed(obj, "assigned_user_id", "fallback_user_id"):  # escalado: outras pessoas passam a ver
                add(obj.org_id, "inbox.changed", f"decision:{obj.id}")
        elif isinstance(obj, EmployeeTask) and _changed(obj, "status", "due_state"):
            add(obj.org_id, "inbox.changed", f"task:{obj.id}")
        elif isinstance(obj, InboxState) and _changed(obj, "read_at", "archived"):
            add(obj.org_id, "inbox.changed", user_id=obj.user_id)
        elif isinstance(obj, Agent) and _changed(obj, "status"):
            add(obj.org_id, "agent.status", f"agent:{obj.id}")
        elif isinstance(obj, Employee) and _changed(obj, "status"):
            add(obj.org_id, "agent.status", f"employee:{obj.id}")
        elif (isinstance(obj, LlmConnection) and _changed(obj, *_CONN_FIELDS)) or (
                isinstance(obj, LlmModel) and _changed(obj, *_MODEL_FIELDS)):
            models_orgs.add(obj.org_id or 1)
        elif isinstance(obj, Organization) and _changed(obj, "code_policy"):
            models_orgs.add(obj.id)
    for obj in session.deleted:
        if isinstance(obj, (LlmConnection, LlmModel)):
            models_orgs.add(obj.org_id or 1)
    for org in sorted(models_orgs):
        add(org, "models.changed")
    return rows


@event.listens_for(Session, "after_flush")
def _record(session: Session, flush_context):
    rows = _rows(session)
    if rows:
        stamp = now()
        session.connection().execute(AppEvent.__table__.insert(), [{**r, "created_at": stamp} for r in rows])


_PURGED = [0.0]


def purge(db: Session, force: bool = False) -> int:
    """Apaga o que passou da retenção (no máximo a cada 10 min por processo)."""
    if not force and time.monotonic() - _PURGED[0] < 600:
        return 0
    _PURGED[0] = time.monotonic()
    n = db.execute(tenancy.unscoped(delete(AppEvent).where(AppEvent.created_at < now() - RETENTION))).rowcount or 0
    db.commit()
    return n


# ------------------------------------------------------------------ entrega
@dataclass
class Reader:
    """O estado de uma conexão SSE."""
    user_id: int
    org: int
    cursor: int = 0
    floor: int = 0  # onde a leitura começou: nada abaixo disso é entregue (nem como atrasado)
    seen: set[int] = field(default_factory=set)
    version: str | None = None  # a última lista de modelos que esta conexão anunciou


def frame(kind: str, data: dict, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _unread(db: Session, acc: Access) -> int:
    from .inbox import items
    return items(db, acc, "unread")["counts"]["unread"]


def _maintainer(db: Session, uid: int, team_id: int | None) -> bool:
    return bool(team_id) and db.scalar(select(TeamMember.id).where(
        TeamMember.team_id == team_id, TeamMember.user_id == uid, TeamMember.role == "maintainer")) is not None


def _manages(e: Employee, uid: int) -> bool:
    return uid in (e.manager_user_id, e.owner_user_id, *(e.backup_user_ids or []))


def _decision(db: Session, acc: Access, ref: str) -> tuple[HumanRequest, Employee] | None:
    from .employee_tasks import can_decide
    r = db.get(HumanRequest, int(ref.split(":", 1)[1]))
    e = db.get(Employee, r.employee_id) if r else None
    if r is None or e is None or r.kind in _HIDDEN or not can_decide(db, acc, r, e):
        return None
    return r, e


def _task_visible(db: Session, uid: int, ref: str) -> bool:
    t = db.get(EmployeeTask, int(ref.split(":", 1)[1]))
    if t is None:
        return False
    e = db.get(Employee, t.employee_id)
    return t.requester_user_id == uid or (e is not None and (_manages(e, uid) or _maintainer(db, uid, e.team_id)))


def _agent_status(db: Session, uid: int, ref: str) -> dict | None:
    """agent.status de um agente ou Digital employee da pessoa (os de Meus agentes: pessoal, gerenciado por ela, de um
    time que ela mantém ou criado por ela). O agente de um Digital employee aparece pelo status do funcionário."""
    what, rid = ref.split(":", 1)
    if what == "employee":
        e = db.get(Employee, int(rid))
        a = db.get(Agent, e.agent_id) if e else None
        if e is None or a is None:
            return None
        mine = a.personal_owner_id == uid or _manages(e, uid) or _maintainer(db, uid, e.team_id)
        return {"slug": a.slug, "status": e.status} if mine else None
    a = db.get(Agent, int(rid))
    if a is None or db.scalar(select(Employee.id).where(Employee.agent_id == a.id)) is not None:
        return None
    creator = db.scalar(select(AgentVersion.created_user_id).where(AgentVersion.agent_id == a.id,
                                                                    AgentVersion.version == 1))
    mine = a.personal_owner_id == uid or creator == uid or _maintainer(db, uid, a.team_id)
    return {"slug": a.slug, "status": a.status} if mine else None


def start(db: Session, p: auth.Principal, last_event_id: str | None) -> tuple[Reader, list[str]]:
    """Abre a leitura. Sem Last-Event-ID: começa agora e manda o retrato da caixa (inbox.changed com o id atual, que
    serve de cursor). Com ele: retoma dali, sem retrato."""
    purge(db)
    reader = Reader(p.user_id or 0, tenancy.current_org())
    last = (last_event_id or "").strip()
    if last.isdigit():
        reader.cursor = reader.floor = int(last)
        return reader, []
    reader.cursor = reader.floor = db.scalar(select(func.coalesce(func.max(AppEvent.id), 0))) or 0
    return reader, [frame("inbox.changed", {"unread": _unread(db, access_of(db, p))}, reader.cursor)]


def poll(db: Session, p: auth.Principal, reader: Reader) -> list[str]:
    """As mensagens novas desta pessoa (filtradas pelo que ela pode ver agora)."""
    rows = [r for r in db.scalars(select(AppEvent).where(
        AppEvent.org_id == reader.org, AppEvent.id > max(reader.floor, reader.cursor - LOOKBACK),
        or_(AppEvent.user_id.is_(None), AppEvent.user_id == reader.user_id)).order_by(AppEvent.id).limit(500))
        if r.id not in reader.seen]
    if not rows:
        return []
    acc, uid, out = access_of(db, p), reader.user_id, []
    visible_inbox = [r.id for r in rows if r.kind == "inbox.changed" and (
        r.user_id == uid or (r.ref.startswith("decision:") and _decision(db, acc, r.ref))
        or (r.ref.startswith("task:") and _task_visible(db, uid, r.ref)))]
    for r in rows:
        reader.seen.add(r.id)
        eid = r.id if r.id > reader.cursor else None  # atrasada: sem id, o cursor do cliente não volta
        reader.cursor = max(reader.cursor, r.id)
        msg = None
        if r.kind == "inbox.changed":
            if visible_inbox and r.id == visible_inbox[-1]:  # várias no mesmo lote: uma contagem só, na última
                msg = {"unread": _unread(db, acc)}
        elif r.kind in ("decision.created", "decision.decided"):
            hit = _decision(db, acc, r.ref)
            if hit and r.kind == "decision.decided":
                msg = {"id": hit[0].id}
            elif hit:
                req, e = hit
                task = db.get(EmployeeTask, req.task_id) if req.task_id else None
                agent = db.get(Agent, e.agent_id)
                msg = {"id": req.id, "title": req.question or (task.title if task else "") or req.tool_name or req.kind,
                       "agent": agent.slug if agent else ""}
        elif r.kind == "agent.status":
            msg = _agent_status(db, uid, r.ref)
        elif r.kind == "models.changed":
            from .app_llm import models_for_app
            try:
                version = models_for_app(db, p)["version"]
            except Exception:  # noqa: BLE001 — pessoa sem acesso: a reautenticação fecha o stream
                version = None
            if version and version != reader.version:
                reader.version, msg = version, {"version": version}
        if msg is not None:
            out.append(frame(r.kind, msg, eid))
    reader.seen = {i for i in reader.seen if i > reader.cursor - LOOKBACK}
    return out


def _still_valid(token: str) -> bool:
    from ..db import SessionLocal
    with SessionLocal() as s:
        p = auth.authenticate(s, token)
    return p is not None and (p.via != "oauth" or "app" in p.oauth_scopes)


async def stream(request, p: auth.Principal, token: str, last_event_id: str | None):
    """O corpo do GET /api/app/events: retrato/retomada, leituras a cada POLL, keepalive, reautenticação e fim
    depois de LIFETIME (o cliente reconecta com Last-Event-ID)."""
    from fastapi.concurrency import run_in_threadpool

    from ..db import SessionLocal
    org, uid = tenancy.current_org(), p.user_id

    def in_org(fn, *args):
        tenancy.set_org(org, uid)
        with SessionLocal() as s:
            return fn(s, p, *args)

    reader, first = await run_in_threadpool(in_org, start, last_event_id)
    yield f"retry: {RETRY_MS}\n\n"
    for m in first:
        yield m
    began = sent = checked = time.monotonic()
    while time.monotonic() - began < LIFETIME:
        await asyncio.sleep(POLL)
        if await request.is_disconnected():
            return
        if time.monotonic() - checked >= REAUTH:
            checked = time.monotonic()
            if not await run_in_threadpool(_still_valid, token):
                return
        msgs = await run_in_threadpool(in_org, poll, reader)
        for m in msgs:
            yield m
        if msgs:
            sent = time.monotonic()
        elif time.monotonic() - sent >= KEEPALIVE:
            sent = time.monotonic()
            yield ": keepalive\n\n"
