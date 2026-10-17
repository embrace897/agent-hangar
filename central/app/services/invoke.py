"""Chamar um agente já deployado (UI, MCP e ponte de protocolos dos agentes com harness e dos agentes remotos)."""
import time

import httpx
from sqlalchemy.orm import Session

from .. import deploy
from ..db import SessionLocal
from .common import PlatformError, get_agent, spec_of
from .runtime import active_deployment, refresh_deployments
from .usage import record_usage


def chat(db: Session, slug: str, message: str, env="prod", channel="api", actor="admin", timeout: float = 180,
         session: str | None = None, protocol: str = "openai") -> dict:
    """session: vira X-Session-Id no agente (workspace próprio da execução, ex.: agendamentos). protocol: por onde a
    chamada chegou (a ponte do gateway diz A2A, ACP, MCP…), para a medição de um agente remoto."""
    from .jobs import run_harness_job

    a = get_agent(db, slug)
    refresh_deployments(db, [a])
    if not active_deployment(a, env):
        raise PlatformError(f"'{slug}' não está rodando em {env}")
    if spec_of(a).get("harness"):
        t0 = time.time()
        job = run_harness_job(db, slug, message, env=env, actor=actor, channel=channel)  # uso registrado no job
        return {"reply": job.result, "job_id": job.id, "diff": job.diff, "status": job.status,
                "usage": {"prompt_tokens": job.tokens_in, "completion_tokens": job.tokens_out,
                          "total_tokens": job.tokens_in + job.tokens_out, "cost_usd": job.cost_usd},
                "trace": [{"tool": f"harness:{job.harness_id}", "args": {}, "result": (job.diff or "")[:300]}],
                "latency_ms": int((time.time() - t0) * 1000)}
    if spec_of(a).get("remote"):  # agente remoto: a rodada vai pelo AG-UI até ele (services/remote_agui.py)
        from . import remote_agui
        t0, ok = time.time(), False
        try:
            out = remote_agui.ask(db, a, spec_of(a), env, [{"role": "user", "content": message}], session)
            ok = out["status"] != "failed"
            return {"reply": out["reply"], "status": out["status"], "usage": {}, "trace": out["trace"],
                    "latency_ms": out["latency_ms"], "interrupts": out["interrupts"]}
        finally:
            record_usage(db, a.id, env, channel, protocol, int((time.time() - t0) * 1000), ok, None)
    t0, ok, data = time.time(), True, None
    try:
        headers = {"X-Channel": channel, **({"X-Session-Id": session} if session else {})}
        deploy.ensure_up(slug, env)
        r = httpx.post(deploy.internal_url(slug, env) + "/v1/chat/completions", timeout=timeout, headers=headers,
                       json={"messages": [{"role": "user", "content": message}]})
        r.raise_for_status()
        data = r.json()
        return {"reply": data["choices"][0]["message"]["content"], "usage": data.get("usage", {}),
                "trace": data.get("x_trace", []), "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        ok = False
        raise PlatformError(f"Falha ao chamar o agente: {e}") from e
    finally:
        record_usage(db, a.id, env, channel, "openai", int((time.time() - t0) * 1000), ok, data)


def chat_new_session(slug: str, message: str, env: str, channel: str, protocol: str = "openai") -> dict:
    """Mesmo que chat(), com sessão própria — para rodar numa thread sem segurar a sessão de quem chamou
    (antes o gateway mantinha uma conexão do pool presa durante todo o job de harness)."""
    with SessionLocal() as db:
        return chat(db, slug, message, env, channel, protocol=protocol)
