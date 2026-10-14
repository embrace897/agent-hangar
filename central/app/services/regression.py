"""Tarefas de ouro: o que o Digital employee precisa continuar acertando. Mudou o cargo (missão, alçada, ferramentas,
configurações — cada mudança vira uma versão no histórico), as tarefas de ouro rodam de novo e a nota de cada uma é
comparada com a rodada anterior. Piorou, o gestor decide: manter a mudança ou voltar à versão anterior do cargo.

- As tarefas de ouro rodam com a configuração de produção, mas **nenhuma ação que muda algo é executada de verdade**
  (como no modo sombra, sem gerar pedidos): a regressão nunca manda e-mail, paga ou grava nada.
- Nota de 1 a 5: o juiz de qualidade (LLM) quando há conexão; sem LLM, a resposta esperada contida no resultado (5),
  parcialmente (3) ou não (1).
- Pior = alguma tarefa caiu 2 pontos ou mais, ou a média caiu 1 ponto ou mais.
"""
import re
import unicodedata

from sqlalchemy import select

from ..models import Agent, Employee, EmployeeRevision, EmployeeTask, GoldenTask, HumanRequest, RegressionRun, now
from . import employee_tasks as tasksmod
from .access import Access
from .common import PlatformError, audit, iso
from .decision_kinds import close, handler


def golden_dict(g: GoldenTask) -> dict:
    return {"id": g.id, "title": g.title, "body": g.body, "expected": g.expected, "active": g.active, "created_by": g.created_by,
            "created_at": iso(g.created_at)}


def run_dict(r: RegressionRun) -> dict:
    return {"id": r.id, "trigger": r.trigger, "revision": r.revision, "status": r.status, "results": r.results,
            "request": r.request_id, "created_at": iso(r.created_at), "finished_at": iso(r.finished_at) if r.finished_at else None}


def view(db, acc: Access, slug: str) -> dict:
    from .employees import find
    e, _ = find(db, acc, slug)
    return {"mode": e.regression_mode or "auto",
            "golden": [golden_dict(g) for g in db.scalars(select(GoldenTask).where(GoldenTask.employee_id == e.id).order_by(GoldenTask.id))],
            "runs": [run_dict(r) for r in db.scalars(select(RegressionRun).where(RegressionRun.employee_id == e.id)
                                                     .order_by(RegressionRun.id.desc()).limit(10))]}


def add(db, acc: Access, slug: str, title: str, body: str, expected: str) -> dict:
    from .employees import find
    e, a = find(db, acc, slug, "manage")
    if not (title or "").strip() or not (expected or "").strip():
        raise PlatformError("a tarefa de ouro precisa de título e da resposta esperada")
    g = GoldenTask(employee_id=e.id, title=title[:300], body=(body or "")[:8000], expected=expected[:4000], created_by=acc.p.name,
                   created_at=now())
    db.add(g)
    db.commit()
    audit(db, acc.p.name, "golden.add", a.slug, g.title[:100])
    return golden_dict(g)


def from_probation(db, acc: Access, slug: str) -> dict:
    from .employees import find
    e, _ = find(db, acc, slug, "manage")
    have = {g.title for g in db.scalars(select(GoldenTask).where(GoldenTask.employee_id == e.id))}
    n = 0
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.employee_id == e.id, EmployeeTask.probation.is_(True))):
        if t.expected and t.title not in have:
            db.add(GoldenTask(employee_id=e.id, title=t.title, body=t.body, expected=t.expected, created_by=acc.p.name, created_at=now()))
            n += 1
    db.commit()
    return {**view(db, acc, slug), "added": n}


def remove(db, acc: Access, golden_id: int) -> dict:
    g = db.get(GoldenTask, golden_id)
    if g is None:
        raise PlatformError("tarefa de ouro não encontrada")
    e = db.get(Employee, g.employee_id)
    from .employees import find
    find(db, acc, db.get(Agent, e.agent_id).slug, "manage")
    g.active = False
    db.commit()
    return golden_dict(g)


def set_mode(db, acc: Access, slug: str, mode: str) -> dict:
    from .employees import find
    if mode not in ("auto", "off"):
        raise PlatformError("mode: auto | off")
    e, _ = find(db, acc, slug, "manage")
    e.regression_mode = mode
    db.commit()
    return view(db, acc, slug)


# ------------------------------------------------------------------ rodadas
def start(db, e: Employee, trigger: str, revision: int | None = None) -> RegressionRun | None:
    goldens = list(db.scalars(select(GoldenTask).where(GoldenTask.employee_id == e.id, GoldenTask.active.is_(True))))
    if not goldens or e.status not in ("active", "probation"):
        return None
    if db.scalar(select(RegressionRun.id).where(RegressionRun.employee_id == e.id, RegressionRun.status == "running")):
        return None  # uma de cada vez
    run = RegressionRun(employee_id=e.id, trigger=trigger[:200], revision=revision, status="running", results=[], created_at=now())
    db.add(run)
    db.flush()
    res = []
    for g in goldens:
        t = tasksmod.create_task(db, e, f"[ouro] {g.title}"[:300], g.body, "regression", f"regressão #{run.id}", None, 3,
                                 expected=g.expected)
        res.append({"golden": g.id, "title": g.title, "task": t.id, "score": None, "before": None})
    run.results = res
    db.commit()
    return run


def run_now(db, acc: Access, slug: str) -> dict:
    from .employees import find
    e, a = find(db, acc, slug, "manage")
    r = start(db, e, f"manual ({acc.p.name})")
    if r is None:
        raise PlatformError("nada para rodar: cadastre tarefas de ouro (ou espere a rodada em andamento)")
    audit(db, acc.p.name, "regression.run", a.slug, f"#{r.id}")
    return run_dict(r)


def on_revision(db, e: Employee, rev: EmployeeRevision):
    if (e.regression_mode or "auto") != "auto" or (rev.summary or "").lower().startswith("restaur"):
        return
    start(db, e, f"versão {rev.version} do cargo: {rev.summary[:120]}", rev.version)


def _norm(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", s)


def score(db, e: Employee, t: EmployeeTask) -> int:
    if t.status != "done":
        return 1
    from . import employee_quality
    a = db.get(Agent, e.agent_id)
    try:
        got = employee_quality.JUDGE(db, e, a, t)
    except Exception:  # noqa: BLE001 — o juiz falhou: vale a regra simples
        got = None
    if got is not None:
        return int(got[0])
    exp, res = " ".join(_norm(t.expected)), " ".join(_norm(t.result))
    if exp and exp in res:
        return 5
    want = set(_norm(t.expected))
    hit = len(want & set(_norm(t.result))) / len(want) if want else 0
    return 3 if hit >= 0.6 else 1


LOW_SCORE = 2  # nota (1-5) que, sem linha de base, marca a rodada como "abaixo do esperado"


def task_finished(db, t: EmployeeTask):
    if t.source != "regression":
        return
    run = next((r for r in db.scalars(select(RegressionRun).where(RegressionRun.employee_id == t.employee_id,
                                                                  RegressionRun.status == "running"))
                if any(x["task"] == t.id for x in r.results)), None)
    if run is None:
        return
    e = db.get(Employee, t.employee_id)
    res = [dict(x) for x in run.results]
    for x in res:
        if x["task"] == t.id:
            x["score"] = score(db, e, t)
    run.results = res
    if any(x["score"] is None for x in res):
        db.commit()
        return
    prev = db.scalar(select(RegressionRun).where(RegressionRun.employee_id == e.id, RegressionRun.id < run.id,
                                                 RegressionRun.status.in_(("passed", "kept", "reverted")))
                     .order_by(RegressionRun.id.desc()))
    before = {x["golden"]: x["score"] for x in (prev.results if prev else [])}
    for x in res:
        x["before"] = before.get(x["golden"])
    run.results, run.finished_at = res, now()
    compared = [x for x in res if x["before"] is not None]
    worse = any(x["score"] <= x["before"] - 2 for x in compared) or (
        compared and sum(x["score"] for x in compared) / len(compared) <= sum(x["before"] for x in compared) / len(compared) - 1)
    if not worse:
        # sem rodada anterior para comparar, a nota absoluta decide: tarefa de ouro com nota 1-2 não passou
        low = [x for x in res if x["before"] is None and x["score"] <= LOW_SCORE]
        run.status = "low" if low else "passed"
        db.commit()
        return
    run.status = "worse"
    lines = [f"- {x['title']}: {x['before']} → {x['score']}" for x in compared if x["score"] != x["before"]]
    r = HumanRequest(employee_id=e.id, kind="regression", action_type="prod_change", mode="approve",
                     question=f"A mudança ({run.trigger}) piorou as tarefas de ouro:\n" + "\n".join(lines),
                     action_payload={"run": run.id, "revision": run.revision, "results": res},
                     assigned_user_id=e.manager_user_id, fallback_user_id=(e.backup_user_ids or [None])[0], approvals=[],
                     created_at=now())
    db.add(r)
    db.flush()
    run.request_id = r.id
    db.commit()
    tasksmod._notify_request(db, e, r)


@handler("regression")
def decide_regression(db, acc, r: HumanRequest, e: Employee, task, decision: str, edit: dict, reason: str, who: str):
    from .employee_tasks import request_dict
    run = db.get(RegressionRun, (r.action_payload or {}).get("run"))
    if decision not in ("keep", "revert"):
        raise PlatformError("regressão: decision = keep (manter a mudança) | revert (voltar à versão anterior do cargo)")
    if decision == "revert":
        rev = (r.action_payload or {}).get("revision")
        if not rev or rev <= 1:
            raise PlatformError("não há versão anterior para voltar")
        from .employee_revisions import restore
        a = db.get(Agent, e.agent_id)
        restore(db, acc, a.slug, rev - 1)
    close(r, decision, reason, who)
    if run is not None:
        run.status = "kept" if decision == "keep" else "reverted"
    db.commit()
    return request_dict(db, r, e)
