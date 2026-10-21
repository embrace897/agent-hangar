"""Jobs de agentes-com-harness: assíncronos, 1 container efêmero por job.

Fluxo: submit_job grava o job (queued) e o entrega a um pool de workers -> o worker sobe o container ->
o container chama /internal/jobs/<id>/callback -> complete_job acorda o worker por um Event em memória
(sem polling do banco) -> o worker finaliza, registra uso/custo e remove o container.

Várias réplicas: o callback pode chegar a qualquer réplica. Ele grava o resultado no banco; o worker (na réplica que
subiu o container) acorda pelo Event quando é a mesma réplica e, senão, percebe a mudança no banco em até POLL_S.
Cada job guarda a réplica do worker (`worker`): ao reiniciar, uma réplica só marca como órfãos os próprios jobs, e o
agendador encerra jobs abandonados por réplicas que sumiram.
"""
import json
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from .. import config, deploy, observability
from ..db import SessionLocal
from ..models import Job, now
from .catalog import cost_usd, resolve_harness
from .common import PlatformError, audit, get_agent, iso
from .runtime import resolve_spec
from .usage import record_usage

log = logging.getLogger("hangar.jobs")

TERMINAL = ("passed", "failed", "timeout", "error", "cancelled")
POLL_S = 2.0
_pool = ThreadPoolExecutor(max_workers=config.JOB_WORKERS, thread_name_prefix="job")
_callback: dict[int, threading.Event] = {}   # container respondeu
_done: dict[int, threading.Event] = {}       # job finalizado (qualquer estado terminal)
_meta: dict[int, dict] = {}                   # dados que não vão para o banco (preços, canal)


def job_dict(j: Job) -> dict:
    return {"id": j.id, "version": j.version, "env": j.env, "harness_id": j.harness_id,
            "connection": j.connection, "task": j.task, "status": j.status, "result": j.result,
            "diff": j.diff, "logs": j.logs, "duration_ms": j.duration_ms, "tokens_in": j.tokens_in,
            "tokens_out": j.tokens_out, "cost_usd": j.cost_usd, "billing": j.billing or "api",
            "api_equiv_cost_usd": j.api_equiv_cost_usd or 0, "billing_note": j.billing_note or "",
            "created_at": iso(j.created_at), "finished_at": iso(j.finished_at)}


def submit_job(db: Session, slug: str, task: str, env="stage", timeout_s=None, actor="admin",
               channel="api", exec_override: dict | None = None, user_id: int | None = None) -> Job:
    """Valida, grava como `queued` e devolve na hora. A execução roda num worker. user_id: a pessoa que disparou o
    job (portal, CLI, a IA dela pelo MCP); só com ela o job pode rodar pela assinatura dela (services/subscriptions.py)."""
    if not task or not task.strip():
        raise PlatformError("task vazia")
    a = get_agent(db, slug)
    spec = resolve_spec(db, a, env)
    hconf = resolve_harness(db, spec, env)
    from . import subscriptions as subs
    billing = (spec.get("harness") or {}).get("billing") or "api"
    sub, note = subs.choose(db, hconf["harness_id"], billing, user_id)
    if sub is None and billing == "subscription":
        raise PlatformError(f"este agente roda só pela assinatura de quem dispara o job: {note}")
    timeout_s = min(max(int(timeout_s or config.JOB_TIMEOUT_S), 10), config.JOB_MAX_TIMEOUT_S)
    skills_txt = "\n\n".join(f"## {s['name']}\n{s['content']}" for s in spec.get("skills_resolved", []))
    system_prompt = "\n\n".join(x for x in (spec.get("instructions", ""), skills_txt) if x)
    mcp_servers = {m["name"]: {"type": "http", "url": m["url"]} for m in spec.get("mcps", [])}
    from . import execution  # o entrypoint traduz em flags do CLI (--effort, model_reasoning_effort, --reasoning…)
    prof = None if hconf["mock"] else execution.resolve(
        db, spec, hconf["harness_id"], hconf["connection"], hconf["model"], a.name, a.objective)
    # exec_override: comparação de perfis (outro nível de esforço só neste job)
    exec_opts = exec_override if exec_override is not None else (execution.harness_options(prof) if prof else {})

    job = Job(agent_id=a.id, version=a.current_version, env=env, harness_id=hconf["harness_id"],
              connection=hconf["connection"], task=task, status="queued", token=secrets.token_hex(24),
              result="", diff="", logs="", container_name="", duration_ms=0, worker=config.REPLICA_ID,
              billing="subscription" if sub else "api", user_id=user_id, subscription_id=sub.id if sub else None,
              billing_note=note)
    db.add(job)
    db.commit()
    job.container_name = deploy.job_container_name(job.id)
    db.commit()
    image = config.harness_image("base" if hconf["mock"] and not sub else hconf["harness_id"])
    environment = {
        "HARNESS_ID": hconf["harness_id"],  # sem LLM_API_KEY o entrypoint roda em modo mock
        "TASK": task, "SYSTEM_PROMPT": system_prompt, "MCP_CONFIG_JSON": json.dumps(mcp_servers),
        # nomes genéricos; o entrypoint mapeia para a env var de cada harness (ANTHROPIC_API_KEY, …)
        "LLM_BASE_URL": hconf["base_url"], "LLM_API_KEY": hconf["api_key"], "LLM_MODEL": hconf["model"],
        "LLM_EXEC_JSON": json.dumps(exec_opts),
        "JOB_CALLBACK_URL": f"{config.INTERNAL_BASE_URL}/internal/jobs/{job.id}/callback",
        "JOB_TOKEN": job.token,
    }
    fallback = None
    if sub is not None:  # pela assinatura: sem a chave da conexão; se o agente aceita, a API fica de reserva
        if billing == "subscription_then_api" and not hconf["mock"]:
            fallback = (image, dict(environment))
        environment.update(subs.environment(sub), LLM_API_KEY="", LLM_BASE_URL="",
                           LLM_MODEL=(spec.get("harness") or {}).get("subscription_model") or "")
    _callback[job.id], _done[job.id] = threading.Event(), threading.Event()
    _meta[job.id] = {"prices": hconf["prices"], "channel": channel, "fallback": fallback,
                     "tags": {"version": a.current_version, "model": hconf["model"],
                              "effort": (exec_override or {}).get("level", "") if exec_override is not None
                              else ((prof or {}).get("effort_level") or "")}}
    audit(db, actor, "job.submit", slug, f"#{job.id} v{a.current_version} {task[:120]}")
    _pool.submit(_execute, job.id, image, environment, timeout_s, actor)
    return job


def _execute(job_id: int, image: str, environment: dict, timeout_s: int, actor: str):
    t0 = time.time()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job.status == "cancelled":
            return _finish(db, job, t0, actor)
        job.status = "running"
        db.commit()
        try:
            deploy.run_job_container(job.container_name, image, environment, config.JOB_MEM_LIMIT, config.JOB_CPUS)
        except Exception as e:
            job.status, job.result = "error", f"Falha ao iniciar o container do job: {e}"
            return _finish(db, job, t0, actor)
        _wait_status(db, job_id, _callback[job_id], timeout_s, ("running",))
        db.expire_all()
        job = db.get(Job, job_id)
        if job.status == "limited":  # a assinatura bateu no limite do plano
            fb = (_meta.get(job_id) or {}).get("fallback")
            if not fb:
                job.status = "failed"
                job.result = ("A assinatura atingiu o limite do plano; o job não rodou. Tente depois do reset, ou deixe o "
                              "agente usar a API quando isso acontecer (harness.billing = subscription_then_api).\n\n"
                              + (job.result or ""))[:20000]
                return _finish(db, job, t0, actor)
            deploy.remove_job_container(job.container_name)
            job.status, job.billing, job.subscription_id = "running", "api", None
            job.billing_note = "; ".join(x for x in (job.billing_note, "limite da assinatura: refeito pela API") if x)
            job.result, job.diff, job.tokens_in, job.tokens_out, job.cost_usd, job.finished_at = "", "", 0, 0, 0.0, None
            db.commit()
            _callback[job_id] = threading.Event()
            try:
                deploy.run_job_container(job.container_name, fb[0], fb[1], config.JOB_MEM_LIMIT, config.JOB_CPUS)
            except Exception as e:
                job.status, job.result = "error", f"Falha ao iniciar o container do job (API): {e}"
                return _finish(db, job, t0, actor)
            _wait_status(db, job_id, _callback[job_id], timeout_s, ("running",))
            db.expire_all()
            job = db.get(Job, job_id)
        if job.status == "running":
            job.status, job.result = "timeout", f"Job excedeu o limite de {timeout_s}s."
        return _finish(db, job, t0, actor)


def _wait_status(db: Session, job_id: int, ev: threading.Event | None, timeout_s: float, waiting: tuple) -> None:
    """Espera o job sair de `waiting`: pelo Event (mesma réplica) ou consultando o banco (callback em outra réplica)."""
    deadline = time.monotonic() + timeout_s
    while (left := deadline - time.monotonic()) > 0:
        if ev is not None and ev.wait(min(POLL_S, left)):
            return
        if ev is None:
            time.sleep(min(POLL_S, left))
        db.expire_all()
        j = db.get(Job, job_id)
        if j is None or j.status not in waiting:
            return


def _finish(db: Session, job: Job, t0: float, actor: str):
    meta = _meta.pop(job.id, {})
    job.duration_ms = int((time.time() - t0) * 1000)
    prices = meta.get("prices") or (None, None)
    # preço do catálogo tem prioridade; sem ele, vale a estimativa que o próprio harness reportou (já gravada pelo
    # callback em cost_usd, em qualquer réplica)
    cost = (cost_usd(job.tokens_in, job.tokens_out, prices) if any(prices) else round(float(job.cost_usd or 0), 6))
    if job.billing == "subscription":  # pago pela assinatura: US$ 0 na medição, e quanto custaria pela API
        job.api_equiv_cost_usd, job.cost_usd = cost, 0.0
    else:
        job.cost_usd = cost
    if not job.logs and job.container_name:
        job.logs = deploy.job_logs(job.container_name, 200)
    job.finished_at = job.finished_at or now()
    db.commit()
    if job.container_name:
        deploy.remove_job_container(job.container_name)
    record_usage(db, job.agent_id, job.env, meta.get("channel", "api"), "harness-job", job.duration_ms,
                 job.status == "passed", tokens_in=job.tokens_in, tokens_out=job.tokens_out, cost=job.cost_usd,
                 tags=meta.get("tags") or {"version": job.version})
    audit(db, actor, "job.finish", str(job.agent_id), f"#{job.id} [{job.status}] {job.duration_ms}ms")
    observability.record_job(job.status)
    ev = _done.pop(job.id, None)
    _callback.pop(job.id, None)
    if ev:
        ev.set()


def complete_job(db: Session, job: Job, status: str, result: str, diff: str, logs: str, usage: dict | None,
                 extra: dict | None = None):
    """Chamado pelo callback do container (já autenticado pelo token do job). extra (jobs pela assinatura):
    subscription_limit {resets_at, message}, codex_auth (o auth.json renovado), auth_rejected (a credencial caiu)."""
    if job.status not in ("running", "queued"):
        return
    usage, extra = usage or {}, extra or {}
    if job.subscription_id:
        from ..models import UserSubscription
        from . import subscriptions as subs
        sub = db.get(UserSubscription, job.subscription_id)
        if sub is not None:
            if status == "limited" or extra.get("subscription_limit"):
                status = "limited"
                subs.limited(db, sub, extra.get("subscription_limit") or {})
            if extra.get("auth_rejected"):
                subs.rejected(db, sub, str(extra["auth_rejected"]))
            if extra.get("codex_auth"):
                subs.refreshed(db, sub, str(extra["codex_auth"]))
            result, logs, diff = subs.scrub(result, sub), subs.scrub(logs, sub), subs.scrub(diff, sub)
    elif status == "limited":
        status = "failed"
    job.status, job.result, job.diff, job.logs = status, result[:20000], diff[:50000], logs[:20000]
    job.tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    job.tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    if isinstance(usage.get("reported_cost_usd"), (int, float)):
        job.cost_usd = float(usage["reported_cost_usd"])
    job.finished_at = now()
    db.commit()
    ev = _callback.get(job.id)
    if ev:
        ev.set()


def cancel_job(db: Session, job_id: int, actor="admin") -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise PlatformError(f"job {job_id} não encontrado")
    if job.status in TERMINAL:
        return job
    job.status, job.result, job.finished_at = "cancelled", "Cancelado.", now()
    db.commit()
    if job.container_name:
        deploy.remove_job_container(job.container_name)
    ev = _callback.get(job.id)
    if ev:
        ev.set()
    audit(db, actor, "job.cancel", str(job.agent_id), f"#{job.id}")
    return job


def wait_job(job_id: int, timeout_s: float) -> Job:
    with SessionLocal() as db:
        _wait_status(db, job_id, _done.get(job_id), timeout_s, ("queued", "running"))
        job = db.get(Job, job_id)
        if job and job.status in TERMINAL and job.id in _done:  # terminal no banco, worker ainda fechando: espera ele
            _done[job.id].wait(30)
            db.expire_all()
            job = db.get(Job, job_id)
        return job


def run_harness_job(db: Session, slug: str, task: str, env="stage", timeout_s=None, actor="admin",
                    channel="api", exec_override: dict | None = None, user_id: int | None = None) -> Job:
    """Versão síncrona (MCP, testes, gateway): submete e espera terminar."""
    job = submit_job(db, slug, task, env, timeout_s, actor, channel, exec_override, user_id)
    limit = min(max(int(timeout_s or config.JOB_TIMEOUT_S), 10), config.JOB_MAX_TIMEOUT_S) + 30
    return wait_job(job.id, limit)


def recover_orphans():
    """No startup: jobs DESTA réplica que estavam em fila/rodando quando ela caiu não têm mais worker esperando por
    eles — marca como erro. Remove containers de job que sobraram, menos os de jobs ativos de outras réplicas."""
    from sqlalchemy import select

    from .. import shared

    with SessionLocal() as db:
        active = db.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all()
        # os meus (reiniciei) e os de réplicas sem heartbeat (sumiram); jobs de réplicas vivas ficam
        orphans = [j for j in active if j.worker in ("", config.REPLICA_ID) or not shared.replica_alive(j.worker)]
        for j in orphans:
            j.status, j.result, j.finished_at = "error", "Interrompido: a central reiniciou durante o job.", now()
        if orphans:
            db.commit()
            log.warning("%d job(s) órfão(s) marcados como erro", len(orphans))
        keep = {j.container_name for j in db.scalars(select(Job).where(Job.status.in_(("queued", "running"))))}
    deploy.cleanup_job_containers(keep=keep, min_age_s=config.JOB_MAX_TIMEOUT_S + 180)


def sweep_abandoned() -> int:
    """Jobs cuja réplica sumiu (sem heartbeat) ou presos muito além do limite: encerra como erro."""
    from datetime import UTC, timedelta

    from sqlalchemy import select

    from .. import shared

    limit = now() - timedelta(seconds=config.JOB_MAX_TIMEOUT_S + 300)
    grace = now() - timedelta(seconds=shared.HEARTBEAT_TTL_S)
    with SessionLocal() as db:
        stale = []
        for j in db.scalars(select(Job).where(Job.status.in_(("queued", "running")))):
            created = j.created_at if j.created_at.tzinfo else j.created_at.replace(tzinfo=UTC)
            if created < limit or (j.worker != config.REPLICA_ID and created < grace and not shared.replica_alive(j.worker)):
                stale.append(j)
        for j in stale:
            j.status, j.finished_at = "error", now()
            j.result = f"Abandonado: a réplica da central que rodava o job ({j.worker or '?'}) não respondeu mais."
            db.commit()
            if j.container_name:
                deploy.remove_job_container(j.container_name)
        return len(stale)


_last_prune = 0.0


def housekeeping():
    global _last_prune
    if n := sweep_abandoned():
        log.warning("%d job(s) abandonado(s) por réplicas que sumiram encerrados", n)
    import time
    if time.monotonic() - _last_prune > 3600:  # auditoria por chamada dos plugins: guarda PLUGIN_CALLS_DAYS dias
        _last_prune = time.monotonic()
        from . import plugins
        with SessionLocal() as db:
            plugins.prune_calls(db)


def job_events(job_id: int, poll=1.0):
    """Gerador de eventos para SSE: status, novas linhas de log do container enquanto roda, e o resultado."""
    sent_status, sent_logs = None, ""
    while True:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if not job:
                yield {"event": "error", "data": {"message": "job não encontrado"}}
                return
            if job.status != sent_status:
                sent_status = job.status
                yield {"event": "status", "data": {"status": job.status}}
            if job.status == "running" and job.container_name:
                current = deploy.job_logs(job.container_name, 400)
                if current.startswith("(sem logs"):
                    current = sent_logs
                if current != sent_logs:
                    new = current[len(sent_logs):] if current.startswith(sent_logs) else current
                    sent_logs = current
                    if new.strip():
                        yield {"event": "log", "data": {"text": new}}
            if job.status in TERMINAL and job_id not in _done:
                yield {"event": "done", "data": job_dict(job)}
                return
        time.sleep(poll)
