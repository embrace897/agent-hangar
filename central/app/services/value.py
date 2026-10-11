"""Valor e oportunidades: quanto os Digital employees economizam, a que custo, com quanto retrabalho — e quais pedidos
que ainda são feitos por pessoas valem automatizar.

- **Linha de base:** quanto tempo uma pessoa levaria para um tipo de trabalho e quanto custa a hora (por funcionário,
  por categoria de fila, ou o padrão da empresa). O admin ajusta; sem ajuste, 30 minutos e a hora em zero.
- **ROI** (na janela pedida): tarefas concluídas, horas poupadas (a linha de base de cada uma), custo equivalente
  humano, custo de LLM, valor líquido, retrabalho (resultado devolvido na revisão, tarefa que falhou), quanto das filas
  foi automático/assistido/humano, e o tempo real das pessoas nos itens.
- **Oportunidades:** os pedidos que pessoas fizeram nos últimos 90 dias são agrupados pelas palavras que importam
  ("segunda via boleto", "alterar cadastro fornecedor"); os grupos que se repetem viram sugestões — Digital employee,
  rotina ou playbook — ordenadas pelas horas que tomam.
"""
import re
import unicodedata
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from ..models import Agent, Employee, EmployeeTask, HumanRequest, ValueBaseline, WorkItem, WorkQueue, now
from .access import Access, Forbidden
from .common import PlatformError, audit, iso
from .employee_tasks import _aware

STOP = set("a o e de da do das dos em no na nos nas um uma para por com que se ao aos como mais ou sobre pra pro favor "
           "preciso precisamos solicito solicitação pedido urgente hoje ver fazer the of and to in for please".split())


def _admin(acc: Access):
    if not (acc.p.is_admin or acc.p.is_auditor or any(r == "maintainer" for r in acc.teams.values())):
        raise Forbidden("valor e oportunidades são para gestores, auditores e admins")


def baselines(db, acc: Access) -> list[dict]:
    _admin(acc)
    return [{"scope": b.scope, "minutes": b.minutes, "hourly_cost": b.hourly_cost, "updated_by": b.updated_by,
             "updated_at": iso(b.updated_at)} for b in db.scalars(select(ValueBaseline).order_by(ValueBaseline.scope))]


def set_baselines(db, acc: Access, items: list[dict]) -> list[dict]:
    if not acc.p.is_admin:
        raise Forbidden("só o admin define as linhas de base")
    for it in items or []:
        scope = str(it.get("scope") or "").strip()
        if not (scope == "default" or re.fullmatch(r"(employee:[\w-]+|queue:[\w-]+:.+)", scope)):
            raise PlatformError("scope: default | employee:<slug> | queue:<fila>:<categoria>")
        b = db.scalar(select(ValueBaseline).where(ValueBaseline.scope == scope))
        if it.get("remove"):
            if b is not None:
                db.delete(b)
            continue
        if b is None:
            b = ValueBaseline(scope=scope)
            db.add(b)
        b.minutes, b.hourly_cost = max(0.0, float(it.get("minutes") or 0)), max(0.0, float(it.get("hourly_cost") or 0))
        b.updated_by, b.updated_at = acc.p.name, now()
    db.commit()
    audit(db, acc.p.name, "value.baselines", "", f"{len(items or [])} item(ns)")
    return baselines(db, acc)


def _baseline_map(db) -> dict[str, ValueBaseline]:
    return {b.scope: b for b in db.scalars(select(ValueBaseline))}


def _for(bm: dict, scopes: list[str]) -> tuple[float, float]:
    d = bm.get("default")
    minutes = next((bm[s].minutes for s in scopes if s in bm), d.minutes if d else 30.0)
    cost = next((bm[s].hourly_cost for s in scopes if s in bm and bm[s].hourly_cost), d.hourly_cost if d else 0.0)
    return minutes, cost


def roi(db, acc: Access, days: int = 30, team: str = "") -> dict:
    _admin(acc)
    since = now() - timedelta(days=max(1, min(int(days or 30), 365)))
    bm = _baseline_map(db)
    team_id = None
    if team:
        from ..models import Team
        t = db.scalar(select(Team).where(Team.slug == team))
        team_id = t.id if t else -1
    item_by_task = {i.employee_task_id: i for i in db.scalars(select(WorkItem).where(WorkItem.employee_task_id.is_not(None)))}
    queues = {q.id: q.slug for q in db.scalars(select(WorkQueue))}
    rejected = {r.task_id for r in db.scalars(select(HumanRequest).where(HumanRequest.kind == "review", HumanRequest.decision == "reject"))}
    rows, tot = [], defaultdict(float)
    for e in db.scalars(select(Employee)):
        a = db.get(Agent, e.agent_id)
        if a is None or (team_id is not None and a.team_id != team_id):
            continue
        tasks = list(db.scalars(select(EmployeeTask).where(EmployeeTask.employee_id == e.id, EmployeeTask.probation.is_(False),
                                                           EmployeeTask.source != "regression", EmployeeTask.finished_at >= since)))
        if not tasks:
            continue
        done = [t for t in tasks if t.status == "done"]
        minutes = human_cost = 0.0
        for t in done:
            i = item_by_task.get(t.id)
            scopes = ([f"queue:{queues.get(i.queue_id)}:{i.category}"] if i and i.queue_id else []) + [f"employee:{a.slug}"]
            m, c = _for(bm, scopes)
            minutes += m
            human_cost += m / 60 * c
        llm = sum(t.cost_usd or 0 for t in tasks)
        rework = sum(1 for t in tasks if t.id in rejected) + sum(1 for t in tasks if t.status == "failed")
        row = {"employee": a.slug, "name": a.name, "tasks": len(tasks), "done": len(done), "failed": sum(t.status == "failed" for t in tasks),
               "hours_saved": round(minutes / 60, 1), "human_cost": round(human_cost, 2), "llm_cost": round(llm, 2),
               "net": round(human_cost - llm, 2), "cost_per_task": round(llm / len(done), 3) if done else None,
               "rework_rate": round(rework / len(tasks), 2) if tasks else 0.0}
        rows.append(row)
        for k in ("tasks", "done", "failed", "hours_saved", "human_cost", "llm_cost", "net"):
            tot[k] += row[k]
    items = list(db.scalars(select(WorkItem).where(WorkItem.status == "done", WorkItem.done_at >= since)))
    if team_id is not None:
        items = [i for i in items if i.team_id == team_id]
    by = {k: sum(1 for i in items if i.handled_by == k) for k in ("employee", "assisted", "human")}
    people_h = sum((_aware(i.done_at) - _aware(i.started_at)).total_seconds() / 3600 for i in items
                   if i.handled_by in ("assisted", "human") and i.started_at and i.done_at)
    q = sum(by.values())
    return {"days": days, "employees": sorted(rows, key=lambda r: -r["net"]),
            "total": {k: round(v, 2) for k, v in tot.items()},
            "queues": {**by, "automation_rate": round(by["employee"] / q, 2) if q else None,
                       "assisted_rate": round(by["assisted"] / q, 2) if q else None},
            "people_hours_on_items": round(people_h, 1)}


def report(db, acc: Access, days: int = 30) -> str:
    r = roi(db, acc, days)
    t = r["total"]
    lines = [f"# Valor dos Digital employees — últimos {days} dias", "",
             f"- Tarefas concluídas: **{int(t.get('done', 0))}** (falhas: {int(t.get('failed', 0))})",
             f"- Horas poupadas: **{t.get('hours_saved', 0)} h**",
             f"- Custo equivalente humano: {t.get('human_cost', 0)} · custo de LLM: {t.get('llm_cost', 0)} · **líquido: {t.get('net', 0)}**"]
    if r["queues"]["automation_rate"] is not None:
        lines.append(f"- Filas: {round(r['queues']['automation_rate'] * 100)}% automático, "
                     f"{round(r['queues']['assisted_rate'] * 100)}% assistido")
    lines += ["", "| Digital employee | Concluídas | Horas poupadas | Líquido | Retrabalho |", "|---|---|---|---|---|"]
    lines += [f"| {e['name']} | {e['done']} | {e['hours_saved']} | {e['net']} | {round(e['rework_rate'] * 100)}% |" for e in r["employees"]]
    ops = opportunities(db, acc)[:5]
    if ops:
        lines += ["", "## Oportunidades", *[f"- {o['label']}: {o['per_month']} por mês, {o['hours']} h — {o['suggestion']}" for o in ops]]
    return "\n".join(lines)


def _norm(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b\d[\d./-]*\b", " ", s)
    return [w for w in re.findall(r"[a-z]{3,}", s) if w not in STOP]


def opportunities(db, acc: Access, days: int = 90) -> list[dict]:
    _admin(acc)
    since = now() - timedelta(days=days)
    groups: dict[tuple, list[WorkItem]] = defaultdict(list)
    for i in db.scalars(select(WorkItem).where(WorkItem.created_at >= since, WorkItem.handled_by.in_(("human", "assisted", "")),
                                               WorkItem.source.not_in(("memo",)))):
        toks = list(dict.fromkeys(_norm(i.title)))[:3]
        if len(toks) >= 2:
            groups[tuple(sorted(toks))].append(i)
    out = []
    for key, items in groups.items():
        if len(items) < 3:
            continue
        hours = sum(((_aware(i.done_at) - _aware(i.started_at)).total_seconds() / 3600) for i in items if i.started_at and i.done_at)
        human = sum(1 for i in items if i.handled_by != "assisted")
        per_month = round(len(items) * 30 / days, 1)
        sug = ("candidato a Digital employee (ou a uma categoria de fila que vá para um)" if human >= len(items) * 0.7 else
               "o Digital employee já prepara: veja se dá para ele terminar sozinho")
        if any(i.source == "playbook" for i in items):
            sug = "etapa de playbook feita por pessoas: candidata a passar para um Digital employee"
        out.append({"label": " ".join(key), "count": len(items), "per_month": per_month, "hours": round(hours, 1),
                    "human": human, "examples": [i.title for i in items[:3]], "suggestion": sug})
    return sorted(out, key=lambda o: -(o["hours"] or o["count"]))
