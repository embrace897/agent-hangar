"""Agendamentos: o agente em produção dispara sozinho no dia e hora pedidos (cron ou data única).

O agendador é uma thread da central que, a cada SCHEDULER_TICK_S segundos, pega os agendamentos vencidos,
"reserva" cada um com um UPDATE condicional (seguro com mais de uma réplica) e roda a execução num pool:
chama o agente como o playground (uso e custo registrados no canal "schedule"), guarda o resultado no
histórico e, se houver webhook, posta o resultado (formato aceito por Slack/Teams/HTTP genérico).
Execuções perdidas enquanto a central estava parada rodam uma vez, marcadas como "late"."""
import ipaddress
import logging
import socket
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import config
from ..cron import Cron, CronError, zone
from ..db import SessionLocal
from ..models import Agent, Organization, Schedule, ScheduleRun, now
from ..tenancy import current_org
from .access import Access
from .common import PlatformError, audit, get_agent, iso

log = logging.getLogger("hangar.scheduler")
_pool = ThreadPoolExecutor(max_workers=config.SCHEDULE_WORKERS, thread_name_prefix="schedule")
_stop = threading.Event()
HTTP = lambda: httpx.Client(timeout=20)  # noqa: E731  (os testes trocam)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def org_timezone(db: Session) -> str:
    o = db.get(Organization, current_org())
    return (o.timezone if o else None) or "UTC"


# ------------------------------------------------------------------ validação
def check_webhook(url: str) -> str:
    """Só http(s) e, por padrão, só destinos públicos: um webhook não pode virar porta para a rede interna."""
    url = (url or "").strip()
    if not url:
        return ""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise PlatformError("notify_url precisa ser uma URL http(s)")
    if config.SCHEDULE_ALLOW_PRIVATE_WEBHOOKS:
        return url
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        raise PlatformError(f"notify_url: não foi possível resolver '{u.hostname}'") from None
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise PlatformError("notify_url aponta para a rede interna; use um webhook público (Slack, Teams…) "
                                "ou libere com SCHEDULE_ALLOW_PRIVATE_WEBHOOKS=1")
    return url


def _parse_run_at(run_at, tz: str) -> datetime:
    if isinstance(run_at, datetime):
        dt = run_at
    else:
        try:
            dt = datetime.fromisoformat(str(run_at).strip().replace("Z", "+00:00"))
        except ValueError:
            raise PlatformError("run_at deve ser data/hora ISO, ex.: 2026-10-05T09:00") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone(tz))  # sem fuso = no fuso do agendamento
    dt = dt.astimezone(UTC)
    if dt <= now():
        raise PlatformError("run_at está no passado")
    return dt


def compute_next(s: Schedule, after: datetime | None = None) -> datetime | None:
    after = after or now()
    if s.cron:
        return Cron(s.cron).next_after(after, s.timezone)
    ra = _aware(s.run_at)
    return ra if ra and ra > after else None


def when_text(s: Schedule) -> str:
    if s.cron:
        try:
            return Cron(s.cron).describe()
        except CronError:
            return s.cron
    if s.run_at:
        return "uma vez em " + _aware(s.run_at).astimezone(zone(s.timezone)).strftime("%d/%m/%Y às %H:%M")
    return "—"


def schedule_dict(db: Session, s: Schedule) -> dict:
    a = db.get(Agent, s.agent_id)
    tz = zone(s.timezone)
    nxt = _aware(s.next_run_at)
    return {"id": s.id, "agent": a.slug if a else None, "agent_name": a.name if a else None, "name": s.name,
            "cron": s.cron, "run_at": iso(_aware(s.run_at)), "timezone": s.timezone, "when": when_text(s),
            "message": s.message, "notify_url": s.notify_url, "enabled": s.enabled,
            "next_run_at": iso(nxt), "next_run_local": nxt.astimezone(tz).strftime("%d/%m/%Y %H:%M") if nxt else None,
            "last_run_at": iso(_aware(s.last_run_at)), "last_status": s.last_status,
            "agent_in_production": bool(a and a.status == "production"),
            "created_by": s.created_by, "created_at": iso(s.created_at)}


def run_dict(r: ScheduleRun) -> dict:
    return {"id": r.id, "schedule_id": r.schedule_id, "trigger": r.trigger, "status": r.status, "version": r.version,
            "output": r.output, "error": r.error, "notify_status": r.notify_status, "tokens": r.tokens,
            "cost_usd": r.cost_usd, "started_at": iso(_aware(r.started_at)), "finished_at": iso(_aware(r.finished_at))}


def _validate(db: Session, s: Schedule):
    if not (s.message or "").strip():
        raise PlatformError("informe a mensagem/tarefa que o agente recebe a cada disparo")
    if bool(s.cron) == bool(s.run_at):
        raise PlatformError("informe cron (recorrente) OU run_at (uma vez)")
    zone(s.timezone)
    if s.cron:
        try:
            c = Cron(s.cron)
            if c.min_interval_minutes(s.timezone) < config.SCHEDULE_MIN_INTERVAL_MIN:
                raise PlatformError(f"intervalo mínimo entre disparos: {config.SCHEDULE_MIN_INTERVAL_MIN} minutos")
        except CronError as e:
            raise PlatformError(str(e)) from None
        s.cron = c.expr
    s.notify_url = check_webhook(s.notify_url)


# ------------------------------------------------------------------ CRUD (permissões: editar o agente)
def create(db: Session, acc: Access, slug: str, message: str, cron: str | None = None, run_at=None,
           timezone: str | None = None, name: str = "", notify_url: str = "", enabled: bool = True) -> Schedule:
    a = get_agent(db, slug)
    acc.require("edit", a)
    tz = timezone or org_timezone(db)
    s = Schedule(agent_id=a.id, name=(name or "").strip()[:200], cron=(cron or "").strip() or None,
                 run_at=_parse_run_at(run_at, tz) if run_at else None, timezone=tz, message=message or "",
                 notify_url=notify_url or "", enabled=enabled, created_by=acc.p.name,
                 created_user_id=acc.p.user_id)
    _validate(db, s)
    s.name = s.name or when_text(s)
    s.next_run_at = compute_next(s) if enabled else None
    db.add(s)
    db.commit()
    audit(db, acc.p.name, "schedule.create", a.slug, f"#{s.id} {when_text(s)} ({tz})")
    return s


def _own(db: Session, acc: Access, schedule_id: int, action: str = "edit") -> tuple[Schedule, Agent]:
    s = db.get(Schedule, schedule_id)
    a = db.get(Agent, s.agent_id) if s else None
    if not s or not a or not acc.can("view", a):
        raise PlatformError(f"agendamento {schedule_id} não encontrado")
    acc.require(action, a)
    return s, a


def update_schedule(db: Session, acc: Access, schedule_id: int, **fields) -> Schedule:
    s, a = _own(db, acc, schedule_id)
    for k in ("name", "message", "notify_url", "enabled"):
        if fields.get(k) is not None:
            setattr(s, k, fields[k])
    if fields.get("timezone"):
        s.timezone = fields["timezone"]
    if fields.get("cron"):
        s.cron, s.run_at = fields["cron"].strip(), None
    elif fields.get("run_at"):
        s.cron, s.run_at = None, _parse_run_at(fields["run_at"], s.timezone)
    _validate(db, s)
    s.next_run_at = compute_next(s) if s.enabled else None
    db.commit()
    audit(db, acc.p.name, "schedule.update", a.slug, f"#{s.id} {when_text(s)} enabled={s.enabled}")
    return s


def delete(db: Session, acc: Access, schedule_id: int):
    s, a = _own(db, acc, schedule_id)
    db.query(ScheduleRun).filter(ScheduleRun.schedule_id == s.id).delete()
    db.delete(s)
    db.commit()
    audit(db, acc.p.name, "schedule.delete", a.slug, f"#{schedule_id}")


def list_for(db: Session, acc: Access, slug: str | None = None) -> list[dict]:
    q = select(Schedule).order_by(Schedule.id.desc())
    if slug:
        q = q.where(Schedule.agent_id == get_agent(db, slug).id)
    agents = {a.id: a for a in db.scalars(select(Agent))}
    return [schedule_dict(db, s) for s in db.scalars(q) if s.agent_id in agents and acc.can("usage", agents[s.agent_id])]


def runs(db: Session, acc: Access, schedule_id: int, limit: int = 20) -> list[dict]:
    _own(db, acc, schedule_id, "usage")
    return [run_dict(r) for r in db.scalars(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id)
                                            .order_by(ScheduleRun.id.desc()).limit(limit))]


def run_now(db: Session, acc: Access, schedule_id: int) -> dict:
    _own(db, acc, schedule_id)
    return run_dict(execute(schedule_id, "manual"))


# ------------------------------------------------------------------ execução
def _notify(s: Schedule, a: Agent, r: ScheduleRun) -> str:
    if not s.notify_url:
        return ""
    status = {"passed": "concluído", "failed": "falhou", "skipped": "pulado"}.get(r.status, r.status)
    text = f"*{a.name}* — {s.name} ({status})\n\n{(r.output or r.error)[:3500]}"
    payload = {"text": text, "agent": a.slug, "schedule": s.name, "schedule_id": s.id, "run_id": r.id,
               "status": r.status, "output": r.output, "error": r.error, "at": iso(_aware(r.started_at))}
    try:
        with HTTP() as http:
            resp = http.post(check_webhook(s.notify_url), json=payload)
        return f"enviado ({resp.status_code})" if resp.status_code < 400 else f"falhou ({resp.status_code})"
    except Exception as e:  # o webhook nunca derruba a execução
        return f"falhou ({type(e).__name__})"


def execute(schedule_id: int, trigger: str = "schedule") -> ScheduleRun:
    """Roda um agendamento agora, dentro da empresa dele."""
    from .. import tenancy
    with tenancy.using(tenancy.org_of(Schedule, schedule_id)):
        return _execute(schedule_id, trigger)


def _execute(schedule_id: int, trigger: str = "schedule") -> ScheduleRun:
    """Roda um agendamento agora (numa sessão própria) e grava o resultado no histórico."""
    from .invoke import chat
    from .org import budget_blocked
    from .runtime import active_deployment, refresh_deployments

    with SessionLocal() as db:
        s = db.get(Schedule, schedule_id)
        a = db.get(Agent, s.agent_id)
        r = ScheduleRun(schedule_id=s.id, agent_id=a.id, trigger=trigger, status="running")
        db.add(r)
        db.commit()
        try:
            refresh_deployments(db, [a])
            dep = active_deployment(a, "prod")
            if not dep:
                r.status, r.error = "skipped", "o agente não está em produção (o agendamento vale quando ele subir)"
            elif budget_blocked(db, a.team_id):
                r.status, r.error = "skipped", "orçamento mensal do time esgotado"
            else:
                r.version = dep.version
                out = chat(db, a.slug, s.message, "prod", "schedule", actor=f"schedule:{s.id}",
                           timeout=config.SCHEDULE_RUN_TIMEOUT_S, session=f"schedule-{s.id}-{r.id}")
                u = out.get("usage") or {}
                r.output = (out.get("reply") or "")[:50000]
                r.tokens = int(u.get("total_tokens") or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)) or 0)
                r.cost_usd = float(u.get("cost_usd") or 0)
                r.status = "failed" if out.get("status") in ("failed", "error", "timeout") else "passed"
        except Exception as e:
            r.status, r.error = "failed", str(e)[:4000]
        r.finished_at = now()
        s.last_run_at, s.last_status = r.started_at, r.status
        db.commit()
        r.notify_status = _notify(s, a, r)
        db.commit()
        audit(db, f"schedule:{s.id}", f"schedule.run.{r.status}", a.slug, f"#{r.id} {trigger}")
        db.refresh(r)
        db.expunge(r)
        return r


def tick(at: datetime | None = None) -> list[int]:
    """Reserva os agendamentos vencidos e devolve [(id, trigger)] para executar (os testes chamam direto, com `at`)."""
    at = at or now()
    claimed = []
    with SessionLocal() as db:
        due = db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at.is_not(None),
                                                Schedule.next_run_at <= at)).all()
        for s in due:
            old = s.next_run_at
            late = (at - _aware(old)).total_seconds() > max(config.SCHEDULER_TICK_S * 3, 120)
            nxt = compute_next(s, at)
            res = db.execute(update(Schedule).where(Schedule.id == s.id, Schedule.next_run_at == old)
                             .values(next_run_at=nxt, enabled=nxt is not None))
            db.commit()
            if res.rowcount == 1:  # outra réplica não pegou antes
                claimed.append((s.id, "late" if late else "schedule"))
    return claimed


def _loop():
    log.info("agendador ligado (a cada %ss)", config.SCHEDULER_TICK_S)
    while not _stop.wait(config.SCHEDULER_TICK_S):
        try:
            for sid, trig in tick():
                _pool.submit(execute, sid, trig)
        except Exception:
            log.exception("falha no tick do agendador")
        try:
            hibernate_idle_stages()
        except Exception:
            log.exception("falha ao hibernar os stages ociosos")
        try:
            refresh_llm_models()
        except Exception:
            log.exception("falha ao atualizar as listas de modelos")


def hibernate_idle_stages() -> list[str]:
    """Stage sem uso há STAGE_IDLE_MIN minutos (em nenhuma réplica) para; acorda sozinho na próxima chamada."""
    from .. import deploy, shared
    if config.STAGE_IDLE_MIN <= 0 or not shared.take_once("stage-hibernate", 60):  # uma réplica por minuto
        return []
    out = deploy.hibernate_idle(config.STAGE_IDLE_MIN * 60)
    if out:
        log.info("stage ocioso hibernado: %s", ", ".join(out))
    return out


def refresh_llm_models() -> bool:
    """A cada 5 minutos (uma réplica só) confere quais conexões estão com a lista de modelos vencida e atualiza em
    segundo plano — os provedores respondem devagar e o laço do agendador não espera."""
    from .. import shared
    if config.LLM_MODELS_REFRESH_MIN <= 0 or not shared.take_once("llm-models-refresh", 300):
        return False
    from . import llm_models
    _pool.submit(llm_models.refresh_due)
    if shared.take_once("model-profiles", 3600):  # fichas: fontes sem custo e a pesquisa automática, de hora em hora
        from . import model_profiles
        _pool.submit(model_profiles.run_auto)
    return True


def start():
    if not config.SCHEDULER_ENABLED:
        return
    _stop.clear()
    threading.Thread(target=_loop, name="scheduler", daemon=True).start()


def stop():
    _stop.set()


def wait_idle(timeout: float = 30):
    """Testes: espera o pool esvaziar."""
    t0 = time.time()
    while _pool._work_queue.qsize() and time.time() - t0 < timeout:  # noqa: SLF001
        time.sleep(0.05)
