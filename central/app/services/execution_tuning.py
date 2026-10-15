"""Ajuste do perfil de execução pela medição: o esforço que o agente usa vale o que custa?

Duas fontes de medida:

- **Uso real:** cada chamada guarda a versão, o modelo e o esforço que a atendeu (UsageEvent). Para o mesmo modelo,
  os níveis de esforço que já rodaram (versões diferentes) são comparados em acerto (chamadas sem erro e casos de
  teste aprovados), custo e tempo.
- **Comparação de perfis:** os casos de teste do agente rodam em stage com o nível atual e os vizinhos (um abaixo e
  um acima), cada um medido em acerto, custo e tempo. É o caminho rápido: não precisa esperar semanas de uso.

Da medição sai uma sugestão (baixar o esforço quando acerta o mesmo e custa ou demora bem menos; subir quando acerta
bem mais), que aparece na página do agente, na caixa de entrada dos admins e no MCP. Nada muda sozinho: aplicar a
sugestão cria uma versão nova, que passa pelos testes antes de produção.
"""
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, deploy, tenancy
from ..db import SessionLocal
from ..models import Agent, ExecutionTrial, TestRun, UsageEvent, now
from . import execution
from .common import PlatformError, audit, get_agent, iso, spec_of

MIN_CALLS = 20      # uso real: perfis com menos chamadas que isso não entram na comparação
SAVE = 0.15         # baixar o esforço: ao menos 15% mais barato ou mais rápido, sem perder acerto
GAIN = 0.10         # subir o esforço: ao menos 10 pontos a mais de acerto
OK_SLACK = 0.02     # uso real: até 2 pontos a menos de chamadas sem erro ainda é "o mesmo acerto"
MAX_CASES = 10
CASE_TIMEOUT_S = 300
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="exec-trial")
LABEL = execution.LEVEL_LABEL


def _rank(level: str) -> int:
    return execution.LEVELS.index(level) if level in execution.LEVELS else -1


def _pct(new: float, old: float) -> int | None:
    return round((new - old) / old * 100) if old else None


# ------------------------------------------------------------------ medição do uso real
def measure(db: Session, agent: Agent, days: int = 30) -> list[dict]:
    """Por (modelo, esforço): chamadas, % sem erro, tempo mediano, custo por chamada, e os casos de teste das versões
    que usaram esse perfil."""
    since = now() - timedelta(days=days)
    groups: dict[tuple, dict] = {}

    def g(model, effort):
        return groups.setdefault((model or "", effort or ""), {
            "model": model or "", "effort": effort or "", "calls": 0, "ok": 0, "lat": [], "cost": 0.0, "tok_out": 0,
            "versions": set(), "cases": 0, "cases_ok": 0})
    rows = db.scalars(select(UsageEvent).where(UsageEvent.agent_id == agent.id, UsageEvent.created_at >= since)
                      .order_by(UsageEvent.id.desc()).limit(5000))
    for ev in rows:
        if not ev.model or ev.model.startswith("mock"):
            continue
        x = g(ev.model, ev.effort)
        x["calls"] += 1
        x["ok"] += 1 if ev.ok else 0
        x["lat"].append(ev.latency_ms or 0)
        x["cost"] += ev.cost_usd or 0
        x["tok_out"] += ev.tokens_out or 0
        if ev.version:
            x["versions"].add(ev.version)
    seen: dict[int, dict] = {}
    for t in db.scalars(select(TestRun).where(TestRun.agent_id == agent.id, TestRun.created_at >= since)):
        if "(mock)" in (t.summary or ""):
            continue
        if t.version not in seen:
            try:
                seen[t.version] = execution.profile_for_version(db, agent, t.version, "stage")
            except PlatformError:
                seen[t.version] = {}
        p = seen[t.version]
        if not p.get("model") or not p.get("connection"):  # versão sem conexão: mock, não mede nada
            continue
        cases = [r for r in (t.results or []) if not str(r.get("name", "")).startswith("smoke")]
        x = g(p["model"], p.get("effort_level") or "")
        x["cases"] += len(cases)
        x["cases_ok"] += sum(1 for r in cases if r.get("passed"))
        x["versions"].add(t.version)
    out = []
    for x in groups.values():
        out.append({"model": x["model"], "effort": x["effort"], "calls": x["calls"],
                    "ok_rate": round(x["ok"] / x["calls"], 3) if x["calls"] else None,
                    "latency_ms_p50": int(statistics.median(x["lat"])) if x["lat"] else None,
                    "cost_per_call": round(x["cost"] / x["calls"], 6) if x["calls"] else None,
                    "tokens_out_avg": round(x["tok_out"] / x["calls"]) if x["calls"] else None,
                    "test_cases": x["cases"],
                    "test_pass_rate": round(x["cases_ok"] / x["cases"], 3) if x["cases"] else None,
                    "versions": sorted(x["versions"])})
    return sorted(out, key=lambda r: (r["model"], _rank(r["effort"])))


def from_usage(stats: list[dict], model: str, level: str) -> dict | None:
    """Sugestão pelo uso real, entre níveis do mesmo modelo com chamadas suficientes."""
    cur = next((s for s in stats if s["model"] == model and s["effort"] == (level or "")), None)
    if not cur or cur["calls"] < MIN_CALLS:
        return None
    peers = [s for s in stats if s["model"] == model and s["effort"] in execution.LEVELS and s["effort"] != level
             and s["calls"] >= MIN_CALLS]

    def tests_ok(s):
        return s["test_pass_rate"] is None or cur["test_pass_rate"] is None or s["test_pass_rate"] >= cur["test_pass_rate"]
    # chamadas com erro demais e um nível acima que erra bem menos: subir vem antes de economizar
    better = [s for s in peers if _rank(s["effort"]) > _rank(level) and s["ok_rate"] >= cur["ok_rate"] + 0.05
              and tests_ok(s)]
    if better and cur["ok_rate"] < 0.9:
        s = max(better, key=lambda x: x["ok_rate"])
        return _suggestion("usage", s["effort"], level, _pct(s["cost_per_call"] or 0, cur["cost_per_call"] or 0),
                           _pct(s["latency_ms_p50"] or 0, cur["latency_ms_p50"] or 0),
                           f"no uso real, o esforço {LABEL[s['effort']]} teve {round(s['ok_rate'] * 100)}% de chamadas "
                           f"sem erro, contra {round(cur['ok_rate'] * 100)}% do {LABEL.get(level, level)}")
    cheaper = []
    for s in peers:
        if _rank(s["effort"]) >= _rank(level) or s["ok_rate"] < cur["ok_rate"] - OK_SLACK or not tests_ok(s):
            continue
        cost = _pct(s["cost_per_call"] or 0, cur["cost_per_call"] or 0)
        lat = _pct(s["latency_ms_p50"] or 0, cur["latency_ms_p50"] or 0)
        if (cost is not None and cost <= -SAVE * 100) or (lat is not None and lat <= -SAVE * 100):
            cheaper.append((s, cost, lat))
    if cheaper:
        s, cost, lat = min(cheaper, key=lambda c: (c[0]["cost_per_call"] or 0, c[0]["latency_ms_p50"] or 0))
        return _suggestion("usage", s["effort"], level, cost, lat,
                           f"no uso real ({s['calls']} chamadas), o esforço {LABEL[s['effort']]} teve "
                           f"{round(s['ok_rate'] * 100)}% de chamadas sem erro, contra {round(cur['ok_rate'] * 100)}% do "
                           f"{LABEL.get(level, level)}")
    return None


def _suggestion(source: str, effort: str, current: str, cost_pct, lat_pct, reason: str, **extra) -> dict:
    parts = []
    if cost_pct is not None and cost_pct != 0:
        parts.append(f"custo {'+' if cost_pct > 0 else ''}{cost_pct}%")
    if lat_pct is not None and lat_pct != 0:
        parts.append(f"tempo {'+' if lat_pct > 0 else ''}{lat_pct}%")
    return {"source": source, "effort": effort, "from": current or "", "direction": "down" if _rank(effort) < _rank(current) else "up",
            "cost_change_pct": cost_pct, "latency_change_pct": lat_pct,
            "reason": reason + (f" ({', '.join(parts)})" if parts else ""),
            "patch": {"execution": {"effort": effort}}, **extra}


# ------------------------------------------------------------------ comparação de perfis em stage
def _levels_for(db: Session, spec: dict, target: str, conn: str, model: str, base: str) -> list[str]:
    """O nível atual e os vizinhos que de fato mudam o que é enviado (na OpenRouter, mínimo e baixo são o mesmo)."""
    by_sent: dict[str, list[str]] = {}
    for lv in execution.LEVELS:
        p = execution.resolve(db, {**spec, "execution": {"effort": lv}}, target, conn, model)
        if p.get("effort"):
            by_sent.setdefault(p["effort"], []).append(lv)
    # cada grupo aparece pelo nível atual, senão pelo nível de mesmo nome do que é enviado ("low" e não "minimal")
    order = sorted((base if base in g else (sent if sent in g else g[0]) for sent, g in by_sent.items()), key=_rank)
    if base not in order:
        return order[:3]
    i = order.index(base)
    return order[max(0, i - 1): i + 2]


def _cases(spec: dict) -> list[dict]:
    return [c for c in spec.get("tests") or [] if not c.get("workspace")][:MAX_CASES]


def start_trial(db: Session, slug: str, actor: str, levels: list[str] | None = None, wait: bool = False) -> ExecutionTrial:
    """Roda os casos de teste em stage com o nível de esforço atual e os vizinhos (ou `levels`). Em segundo plano;
    acompanhe por get_trial / a página do agente."""
    a = get_agent(db, slug)
    spec = spec_of(a)
    prof = execution.profile_for_version(db, a, a.current_version, "stage")
    if not prof.get("connection"):
        raise PlatformError("o agente não tem conexão de LLM em stage (modo mock): não há o que comparar")
    target = prof["target"]
    base = prof.get("effort_level") or ""
    probe = execution.resolve(db, {**spec, "execution": {"effort": "high"}}, target, prof["connection"], prof["model"])
    if not probe.get("effort"):
        raise PlatformError("não há esforço para comparar: " + "; ".join(probe["warnings"])
                            if probe["warnings"] else "o modelo ou o harness não aceita esforço de raciocínio")
    cases = _cases(spec)
    if not cases:
        raise PlatformError("o agente não tem casos de teste (sem workspace) para comparar: escreva alguns em spec.tests")
    if db.scalar(select(ExecutionTrial).where(ExecutionTrial.agent_id == a.id, ExecutionTrial.status == "running")):
        raise PlatformError("já há uma comparação de perfis rodando para este agente")
    if levels:
        bad = [x for x in levels if x not in execution.LEVELS]
        if bad:
            raise PlatformError(f"níveis inválidos: {bad} (opções: {list(execution.LEVELS)})")
        levels = [x for x in execution.LEVELS if x in set(levels)]
        if base and base not in levels:
            levels = sorted({*levels, base}, key=_rank)
    else:
        levels = _levels_for(db, spec, target, prof["connection"], prof["model"], base or "medium")
    if len(levels) < 2:
        raise PlatformError("só um nível de esforço muda o que é enviado a este modelo: não há o que comparar")
    t = ExecutionTrial(agent_id=a.id, version=a.current_version, model=prof["model"], baseline=base, levels=levels,
                       results=[], status="running", created_by=actor)
    db.add(t)
    db.commit()
    audit(db, actor, "execution.trial", slug, f"v{a.current_version} {prof['model']}: {', '.join(levels)} × {len(cases)} caso(s)")
    org = tenancy.current_org()
    if wait:
        _run(t.id, org)
        db.refresh(t)
    else:
        _pool.submit(_run, t.id, org)
    return t


def _ask_chat(slug: str, text: str, opts: dict) -> tuple[str, dict]:
    """Uma pergunta ao container de stage com outro perfil só nesta requisição (headers que só a central assina)."""
    r = httpx.post(deploy.internal_url(slug, "stage") + "/v1/chat/completions", timeout=CASE_TIMEOUT_S,
                   json={"messages": [{"role": "user", "content": text}]},
                   headers={"X-Hangar-Exec": json.dumps(opts), "X-Hangar-Internal": auth.agent_env_token(slug, "stage"),
                            "X-Channel": "profile-trial"})
    r.raise_for_status()
    d = r.json()
    return d["choices"][0]["message"].get("content") or "", d.get("usage") or {}


def _run(trial_id: int, org: int):
    with tenancy.using(org), SessionLocal() as db:
        t = db.get(ExecutionTrial, trial_id)
        try:
            _execute(db, t)
            t.status = "done"
        except Exception as e:  # noqa: BLE001 — a falha fica registrada na comparação
            t.status, t.error = "failed", str(e)[:2000]
        t.finished_at = now()
        t.cost_usd = round(sum(r.get("cost_usd") or 0 for r in t.results or []), 6)
        sug = from_trial(t)
        t.summary = sug["reason"] if sug else ("nenhum nível foi melhor que o atual" if t.status == "done" else "")
        db.commit()


def _execute(db: Session, t: ExecutionTrial):
    from .runtime import active_deployment, deploy_env, refresh_deployments
    from .testing import evaluate

    a = db.get(Agent, t.agent_id)
    spec = spec_of(a, t.version)
    prof = execution.profile_for_version(db, a, t.version, "stage")
    harness = prof["target"] != "runtime"
    if not harness:
        refresh_deployments(db, [a])
        dep = active_deployment(a, "stage")
        if not dep or dep.version != t.version:
            deploy_env(db, a.slug, "stage", t.created_by)
        deploy.ensure_up(a.slug, "stage")
    results = []
    for lv in t.levels or []:
        p = execution.resolve(db, {**spec, "execution": {**(spec.get("execution") or {}), "effort": lv}}, prof["target"],
                              prof["connection"], prof["model"], a.name, a.objective)
        row = {"level": lv, "sent": p.get("effort"), "cases": [], "passed": 0, "total": 0, "cost_usd": 0.0}
        for i, case in enumerate(_cases(spec)):
            s, usage, cost = time.time(), {}, 0.0
            try:
                if harness:
                    from .jobs import run_harness_job
                    job = run_harness_job(db, a.slug, case["input"], env="stage", timeout_s=case.get("timeout_s"),
                                          actor=t.created_by, channel="profile-trial",
                                          exec_override={**execution.harness_options(p), "level": lv})
                    out, cost = job.result or "", job.cost_usd or 0.0
                    usage = {"completion_tokens": job.tokens_out}
                    ok, detail = evaluate(db, spec, case, out) if job.status == "passed" else (False, f"[{job.status}] {out}")
                else:
                    out, usage = _ask_chat(a.slug, case["input"], execution.runtime_options(p))
                    cost = float(usage.get("cost_usd") or 0)
                    ok, detail = evaluate(db, spec, case, out)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, str(e)
            row["cases"].append({"name": case.get("name") or f"caso {i + 1}", "passed": ok, "detail": str(detail)[:300],
                                 "latency_ms": int((time.time() - s) * 1000), "cost_usd": round(cost, 6),
                                 "tokens_out": int(usage.get("completion_tokens") or 0)})
            row["cost_usd"] += cost
        n = len(row["cases"])
        row.update(total=n, passed=sum(c["passed"] for c in row["cases"]), cost_usd=round(row["cost_usd"], 6),
                   pass_rate=round(sum(c["passed"] for c in row["cases"]) / n, 3) if n else None,
                   avg_cost_usd=round(row["cost_usd"] / n, 6) if n else None,
                   avg_latency_ms=int(statistics.mean(c["latency_ms"] for c in row["cases"])) if n else None)
        results.append(row)
        t.results = list(results)  # parcial: a página mostra o progresso
        db.commit()


def from_trial(t: ExecutionTrial) -> dict | None:
    """Sugestão de uma comparação: o nível acima que acerta bem mais; senão, o mais barato que acerta o mesmo que o
    atual (e custa ou demora bem menos)."""
    res = [r for r in t.results or [] if r.get("total")]
    base = next((r for r in res if r["level"] == t.baseline), None)
    if base is None and res:  # o agente não enviava esforço: o meio da faixa é a referência
        base = res[len(res) // 2]
    if base is None:
        return None
    # acertar mais vem antes de economizar: se um nível acima acerta bem mais, a sugestão é subir
    higher = [r for r in res if _rank(r["level"]) > _rank(base["level"]) and r["pass_rate"] >= base["pass_rate"] + GAIN]
    if higher:
        r = max(higher, key=lambda x: (x["pass_rate"], -(x["avg_cost_usd"] or 0)))
        return _suggestion("trial", r["level"], base["level"], _pct(r["avg_cost_usd"] or 0, base["avg_cost_usd"] or 0),
                           _pct(r["avg_latency_ms"] or 0, base["avg_latency_ms"] or 0),
                           f"nos testes, o esforço {LABEL[r['level']]} acertou {r['passed']}/{r['total']}, contra "
                           f"{base['passed']}/{base['total']} do {LABEL[base['level']]}", trial=t.id)
    lower = []
    for r in res:
        if (_rank(r["level"]) >= _rank(base["level"]) or r["pass_rate"] < base["pass_rate"]
                or not base["pass_rate"]):  # economizar num nível que não acerta nada não é sugestão
            continue
        cost, lat = _pct(r["avg_cost_usd"] or 0, base["avg_cost_usd"] or 0), _pct(r["avg_latency_ms"] or 0, base["avg_latency_ms"] or 0)
        if (cost is not None and cost <= -SAVE * 100) or (lat is not None and lat <= -SAVE * 100):
            lower.append((r, cost, lat))
    if lower:
        r, cost, lat = min(lower, key=lambda x: (x[0]["avg_cost_usd"] or 0, x[0]["avg_latency_ms"] or 0))
        return _suggestion("trial", r["level"], base["level"], cost, lat,
                           f"nos testes, o esforço {LABEL[r['level']]} acertou {r['passed']}/{r['total']}, como o "
                           f"{LABEL[base['level']]} ({base['passed']}/{base['total']})", trial=t.id)
    return None


def trial_dict(t: ExecutionTrial) -> dict:
    return {"id": t.id, "version": t.version, "model": t.model, "baseline": t.baseline, "levels": t.levels or [],
            "results": t.results or [], "status": t.status, "summary": t.summary, "error": t.error,
            "cost_usd": t.cost_usd, "created_by": t.created_by, "created_at": iso(t.created_at),
            "finished_at": iso(t.finished_at)}


def get_trial(db: Session, agent: Agent, trial_id: int | None = None) -> ExecutionTrial | None:
    q = select(ExecutionTrial).where(ExecutionTrial.agent_id == agent.id)
    if trial_id:
        q = q.where(ExecutionTrial.id == trial_id)
    return db.scalar(q.order_by(ExecutionTrial.id.desc()).limit(1))


# ------------------------------------------------------------------ juntando tudo
def suggest(db: Session, agent: Agent, days: int = 30) -> dict:
    """A medição do agente e a sugestão de perfil (da comparação mais recente, se ainda vale para a versão e o modelo
    atuais; senão, do uso real)."""
    prof = execution.profile_for_version(db, agent, None, "prod")
    level = prof.get("effort_level") or ""
    stats = measure(db, agent, days)
    t = get_trial(db, agent)
    sug, stale = None, False
    if t and t.status == "done":
        stale = t.version != agent.current_version or t.model != prof.get("model")
        if not stale:
            sug = from_trial(t)
            if sug and sug["effort"] == level:
                sug = None
    if sug is None and prof.get("model"):
        sug = from_usage(stats, prof["model"], level)
    if sug and sug["effort"] == level:
        sug = None
    return {"agent": agent.slug, "name": agent.name, "model": prof.get("model") or "", "effort": level,
            "effort_source": prof.get("effort_source"), "stats": stats, "trial": trial_dict(t) if t else None,
            "trial_stale": stale, "suggestion": sug}


def suggestions(db: Session) -> list[dict]:
    """Agentes com uma sugestão de perfil (a caixa de entrada dos admins mostra)."""
    out = []
    for a in db.scalars(select(Agent)):
        try:
            r = suggest(db, a)
        except PlatformError:
            continue
        if r["suggestion"]:
            out.append({**r, "at": now()})
    return out


_CACHE: dict[int, tuple[float, list]] = {}


def cached_suggestions(db: Session, ttl: float = 600) -> list[dict]:
    org = tenancy.current_org()
    hit = _CACHE.get(org)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = suggestions(db)
    _CACHE[org] = (time.time(), val)
    return val
