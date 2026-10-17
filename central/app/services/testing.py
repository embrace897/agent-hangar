"""Testes em stage: smoke dos protocolos + casos da spec (contains, regex e LLM-as-judge)."""
import json
import re
import time
import uuid

import httpx
from sqlalchemy.orm import Session

from .. import config, crypto, deploy
from ..models import Agent, TestRun
from .catalog import get_connection
from .common import PlatformError, audit, get_agent, spec_of
from .runtime import active_deployment, deploy_env, refresh_deployments

JUDGE_SYSTEM = ("Você avalia a resposta de um agente de IA contra uma rubrica. Seja rigoroso e objetivo. "
                "Responda SOMENTE com um JSON: {\"pass\": true|false, \"reason\": \"<uma frase>\"}.")


def _post(url, body, timeout=120):
    r = httpx.post(url, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _judge_target(db: Session, spec: dict):
    j = spec.get("judge") or {}
    name = j.get("connection") or (spec.get("llm") or {}).get("connection")
    if not name:
        raise PlatformError("caso com `judge` precisa de uma conexão: defina spec.judge.connection (protocolo "
                            "openai) ou use um agente de chat com llm.connection")
    conn = get_connection(db, name)
    if conn.protocol != "openai":
        raise PlatformError(f"o juiz precisa de uma conexão protocol='openai' ('{name}' é {conn.protocol})")
    return conn.base_url, crypto.decrypt(conn.api_key), j.get("model") or conn.model_name


def judge(db: Session, spec: dict, case: dict, output: str) -> tuple[bool, str]:
    base, key, model = _judge_target(db, spec)
    user = (f"Tarefa enviada ao agente:\n{case['input']}\n\nResposta do agente:\n{output}\n\n"
            f"Rubrica (o que a resposta precisa cumprir):\n{case['judge']}")
    r = httpx.post(f"{base}/chat/completions", timeout=120,
                   headers={"Authorization": f"Bearer {key}"} if key else {},
                   json={"model": model, "temperature": 0,
                         "messages": [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}]})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return False, f"juiz não devolveu JSON: {text[:150]}"
    verdict = json.loads(m.group(0))
    return bool(verdict.get("pass")), str(verdict.get("reason", ""))[:300]


def is_mock(spec: dict, env="stage") -> bool:
    """Sem conexão de LLM o agente responde com eco (mock): conferir o conteúdo não diz nada."""
    if spec.get("remote"):  # o agente remoto responde de verdade, com o modelo dele
        return False
    h = spec.get("harness")
    if h:
        return not ((h.get(env) or {}).get("connection") or h.get("connection"))
    llm = spec.get("llm") or {}
    if (llm.get(env) or {}).get("connection") or llm.get("connection"):
        return False
    return config.DEFAULT_MODEL.startswith("mock")


def evaluate(db: Session, spec: dict, case: dict, out: str) -> tuple[bool, str]:
    if is_mock(spec):
        return True, f"(mock: conteúdo não avaliado — associe uma conexão de LLM) {out[:120]}"
    if case.get("expect_contains") and case["expect_contains"].lower() not in out.lower():
        return False, f"esperava conter '{case['expect_contains']}', obteve: {out[:150]}"
    if case.get("expect_regex") and not re.search(case["expect_regex"], out):
        return False, f"regex '{case['expect_regex']}' não casou: {out[:150]}"
    if case.get("judge"):
        ok, reason = judge(db, spec, case, out)
        return ok, f"juiz: {reason} | resposta: {out[:120]}"
    return True, out[:150]


def _record(db: Session, a: Agent, results: list, t0: float, actor: str, suffix="") -> TestRun:
    ok_all = all(r["passed"] for r in results)
    n_ok = sum(r["passed"] for r in results)
    if is_mock(spec_of(a)):
        suffix += " (mock)"
    run = TestRun(agent_id=a.id, version=a.current_version, env="stage", status="passed" if ok_all else "failed",
                  results=results, summary=f"{n_ok}/{len(results)} checks aprovados{suffix}",
                  duration_ms=int((time.time() - t0) * 1000))
    db.add(run)
    if a.status != "production":
        a.status = "tested" if ok_all else "test_failed"
    db.commit()
    db.expire(a, ["tests"])
    audit(db, actor, "test.run", a.slug, f"v{a.current_version}: {run.status} ({run.summary})")
    return run


def _run_harness_tests(db: Session, a: Agent, spec: dict, actor="admin") -> TestRun:
    from .jobs import run_harness_job

    t0, results = time.time(), []
    cases = spec.get("tests") or [{"name": "smoke", "input": "Responda apenas: ok", "expect_contains": "ok"}]
    for i, case in enumerate(cases):
        s = time.time()
        if case.get("workspace"):
            results.append({"name": case.get("name") or f"caso {i + 1}", "passed": False, "latency_ms": 0,
                            "detail": "avaliação de código (workspace) é para agentes de chat; agentes com harness "
                                      "são avaliados pelo resultado do job"})
            continue
        try:
            job = run_harness_job(db, a.slug, case["input"], env="stage", timeout_s=case.get("timeout_s"),
                                  actor=actor, channel="test")
            out = job.result or ""
            if job.status != "passed":
                ok, detail = False, f"[{job.status}] {out}"
            else:
                ok, detail = evaluate(db, spec, case, out)
        except Exception as e:
            ok, detail = False, str(e)
        results.append({"name": case.get("name") or f"caso {i + 1}", "passed": ok, "detail": detail[:400],
                        "latency_ms": int((time.time() - s) * 1000)})
    return _record(db, a, results, t0, actor, " (harness)")


def _run_remote_tests(db: Session, a: Agent, spec: dict, actor="admin") -> TestRun:
    """Agente remoto: os testes passam pela mesma ponte que o produto usa (AG-UI até o agente, no endereço de stage).
    Primeiro a rodada completa (RUN_STARTED … RUN_FINISHED); depois os casos da spec."""
    from . import remote_agui

    t0, results = time.time(), []

    def check(name, fn):
        s = time.time()
        try:
            passed, detail = fn()
        except Exception as e:
            passed, detail = False, str(e)
        results.append({"name": name, "passed": passed, "detail": str(detail)[:400],
                        "latency_ms": int((time.time() - s) * 1000)})

    def smoke():
        out = remote_agui.ask(db, a, spec, "stage", [{"role": "user", "content": "ping"}])
        ok = out["status"] == "passed" and bool(out["reply"].strip())
        return ok, out["reply"][:120] if ok else (out["error"] or out["reply"] or "sem resposta")

    check("smoke: rodada AG-UI no agente remoto", smoke)
    for i, case in enumerate(spec.get("tests", [])):
        def run_case(case=case):
            if case.get("workspace"):
                return False, "avaliação de código (workspace) é para agentes de chat do hangar"
            out = remote_agui.ask(db, a, spec, "stage", [{"role": "user", "content": case["input"]}])
            if out["status"] == "failed":
                return False, out["error"] or out["reply"]
            return evaluate(db, spec, case, out["reply"])
        check(f"caso: {case.get('name') or i + 1}", run_case)
    return _record(db, a, results, t0, actor, " (remoto)")


def _code_case(db: Session, a: Agent, spec: dict, case: dict) -> tuple[bool, str]:
    """Caso com workspace: o agente trabalha num mini-projeto real (sandbox) e o `check` decide."""
    from . import code_eval

    if is_mock(spec):
        return True, "(mock: avaliação de código não executada — associe uma conexão de LLM)"
    if (spec.get("llm") or {}).get("client_tools") is False:
        return False, "a avaliação de código usa ferramentas do cliente, e a spec as desliga (llm.client_tools=false)"
    from .catalog import code_allowed
    ok_code, msg = code_allowed(db, spec, "stage")
    if not ok_code:
        return False, msg
    return code_eval.run(a.slug, case)


def run_tests(db: Session, slug: str, actor="admin") -> TestRun:
    a = get_agent(db, slug)
    spec = spec_of(a)
    if spec.get("harness"):
        return _run_harness_tests(db, a, spec, actor)
    if spec.get("remote"):
        refresh_deployments(db, [a])
        dep = active_deployment(a, "stage")
        if not dep or dep.version != a.current_version:
            deploy_env(db, slug, "stage", actor)
        return _run_remote_tests(db, a, spec, actor)
    refresh_deployments(db, [a])
    dep = active_deployment(a, "stage")
    if not dep or dep.version != a.current_version:
        deploy_env(db, slug, "stage", actor)
    deploy.ensure_up(slug, "stage")
    base = deploy.internal_url(slug, "stage")
    results, t0 = [], time.time()

    def check(name, fn):
        s = time.time()
        try:
            passed, detail = fn()
        except Exception as e:
            passed, detail = False, str(e)
        results.append({"name": name, "passed": passed, "detail": str(detail)[:400],
                        "latency_ms": int((time.time() - s) * 1000)})

    def t_health():
        return httpx.get(base + "/health", timeout=5).json()["status"] == "ok", "ok"

    def t_card():
        c = httpx.get(base + "/.well-known/agent.json", timeout=5).json()
        return bool(c.get("name") and c.get("url") and c.get("skills")), "Agent Card A2A"

    # multiagente encadeia chamadas (especialistas, memória): mais tempo para os smokes que passam pelo LLM
    smoke_timeout = 300 if spec.get("sub_agents") else 120

    def t_openai():
        out = _post(base + "/v1/chat/completions", {"messages": [{"role": "user", "content": "ping"}]}, smoke_timeout)
        text = out["choices"][0]["message"]["content"]
        return bool(text.strip()), text[:120] or "resposta vazia"

    def t_a2a():
        r = _post(base + "/a2a", {"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {
            "message": {"kind": "message", "role": "user", "messageId": str(uuid.uuid4()),
                        "parts": [{"kind": "text", "text": "ping"}]}}}, smoke_timeout)
        if "result" not in r:
            return False, str(r.get("error"))
        return True, r["result"]["parts"][0]["text"][:120]

    def t_acp():
        r = _post(base + "/acp/runs", {"agent_name": slug, "input": [
            {"role": "user", "parts": [{"content_type": "text/plain", "content": "ping"}]}]}, smoke_timeout)
        return r["status"] == "completed", r["output"][0]["parts"][0]["content"][:120]

    def t_mcp():
        r = httpx.post(base + "/mcp", timeout=30, headers={"Accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r.json().get("result", {}).get("tools", [])
        return bool(tools), f"{len(tools)} tool(s)"

    def t_agui():
        r = httpx.post(base + "/agui", timeout=smoke_timeout, headers={"Accept": "text/event-stream"}, json={
            "threadId": f"smoke-{uuid.uuid4().hex[:8]}", "runId": "smoke", "messages": [
                {"id": "1", "role": "user", "content": "ping"}], "tools": [], "context": [], "state": {}, "forwardedProps": {}})
        evs = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
        kinds = [e.get("type") for e in evs]
        text = "".join(e.get("delta", "") for e in evs if e.get("type") == "TEXT_MESSAGE_CONTENT")
        ok = bool(kinds) and kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED" and bool(text.strip())
        return ok, text[:120] if ok else f"eventos: {kinds[:8]}"

    check("smoke: health", t_health)
    check("smoke: agent card A2A", t_card)
    check("smoke: OpenAI-compatible", t_openai)
    check("smoke: A2A message/send", t_a2a)
    check("smoke: ACP run", t_acp)
    check("smoke: MCP tools/list", t_mcp)
    check("smoke: AG-UI run", t_agui)

    for i, case in enumerate(spec.get("tests", [])):
        def run_case(case=case):
            if case.get("workspace"):
                return _code_case(db, a, spec, case)
            r = _post(base + "/v1/chat/completions", {"messages": [{"role": "user", "content": case["input"]}]})
            return evaluate(db, spec, case, r["choices"][0]["message"]["content"])
        check(f"{'código' if case.get('workspace') else 'caso'}: {case.get('name') or i + 1}", run_case)

    return _record(db, a, results, t0, actor)
