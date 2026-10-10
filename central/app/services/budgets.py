"""Orçamentos com escopo e incidentes.

Uma política vale para a empresa, um time, um agente (inclusive o agente de um Digital employee) ou um projeto, numa
janela (dia ou mês, no fuso UTC). O gasto vem do uso registrado (UsageEvent) e, para projetos, do custo das tarefas.

- Ao cruzar warn_percent, abre um incidente de aviso (uma vez por janela) e avisa quem responde pelo escopo.
- Ao cruzar o valor, com hard_stop: abre o incidente de parada. O gateway recusa chamadas dos agentes no escopo (429)
  e as tarefas dos Digital employees param em waiting_budget, sem perder o checkpoint.
- Liberar: aumentar o valor (o incidente fecha sozinho quando o gasto fica abaixo) ou liberar o resto da janela
  (resolve o incidente; a política não para de novo nesta janela). As tarefas paradas voltam para a fila.

O custo registrado não é a fatura do provedor: uso sem preço conhecido conta como zero.
"""
import time
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Agent,
    BudgetIncident,
    BudgetPolicy,
    Employee,
    EmployeeTask,
    Project,
    Team,
    TeamMember,
    UsageEvent,
    User,
    now,
)
from .access import RANK, Access, Forbidden, find_team
from .common import PlatformError, audit, iso

SCOPES = ("org", "team", "agent", "project")
WINDOWS = ("day", "month")
CACHE_S = 30
_cache: dict[tuple, tuple[float, dict | None]] = {}


def _start(window: str) -> datetime:
    n = now()
    return datetime(n.year, n.month, n.day, tzinfo=UTC) if window == "day" else datetime(n.year, n.month, 1, tzinfo=UTC)


def window_key(window: str) -> str:
    return now().strftime("%Y-%m-%d" if window == "day" else "%Y-%m")


def spent(db: Session, p: BudgetPolicy) -> float:
    since = _start(p.window)
    if p.scope_type == "project":
        q = select(func.coalesce(func.sum(EmployeeTask.cost_usd), 0.0)).where(
            EmployeeTask.project_id == p.scope_id, EmployeeTask.created_at >= since)
        return float(db.scalar(q) or 0)
    q = select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(UsageEvent.created_at >= since)
    if p.scope_type == "team":
        q = q.where(UsageEvent.agent_id.in_(select(Agent.id).where(Agent.team_id == p.scope_id)))
    elif p.scope_type == "agent":
        q = q.where(UsageEvent.agent_id == p.scope_id)
    return float(db.scalar(q) or 0)


# ------------------------------------------------------------------ quem responde por um escopo
def _scope_team(db: Session, p: BudgetPolicy) -> int | None:
    if p.scope_type == "team":
        return p.scope_id
    if p.scope_type == "agent":
        a = db.get(Agent, p.scope_id)
        return a.team_id if a else None
    if p.scope_type == "project":
        pr = db.get(Project, p.scope_id)
        return pr.team_id if pr else None
    return None


def can_manage(db: Session, acc: Access, p: BudgetPolicy) -> bool:
    if acc.p.is_admin:
        return True
    if p.scope_type == "org":
        return False
    team_id = _scope_team(db, p)
    return bool(team_id) and RANK.get(acc.teams.get(team_id, ""), 0) >= RANK["maintainer"]


def can_view(db: Session, acc: Access, p: BudgetPolicy) -> bool:
    if acc.p.is_auditor or can_manage(db, acc, p):
        return True
    team_id = _scope_team(db, p)
    return p.scope_type == "org" or (team_id is not None and team_id in acc.teams)


def _people(db: Session, p: BudgetPolicy) -> set[int]:
    if p.scope_type == "org":
        return {u.id for u in db.scalars(select(User).where(User.org_role == "admin", User.active.is_(True)))}
    out = set()
    team_id = _scope_team(db, p)
    if team_id:
        out |= {m.user_id for m in db.scalars(select(TeamMember).where(TeamMember.team_id == team_id,
                                                                       TeamMember.role == "maintainer"))}
    if p.scope_type == "agent":
        e = db.scalar(select(Employee).where(Employee.agent_id == p.scope_id))
        if e:
            out.add(e.manager_user_id)
    return out


def scope_name(db: Session, p: BudgetPolicy) -> str:
    """O slug do escopo (vazio para a empresa); a tela junta com o tipo, já traduzido."""
    if p.scope_type == "team":
        t = db.get(Team, p.scope_id)
        return t.slug if t else str(p.scope_id)
    if p.scope_type == "agent":
        a = db.get(Agent, p.scope_id)
        return a.slug if a else str(p.scope_id)
    if p.scope_type == "project":
        pr = db.get(Project, p.scope_id)
        return pr.slug if pr else str(p.scope_id)
    return ""


def scope_label(db: Session, p: BudgetPolicy) -> str:
    """Para a auditoria e os avisos."""
    return {"org": "empresa", "team": "time", "agent": "agente", "project": "projeto"}[p.scope_type] +         (f" {scope_name(db, p)}" if p.scope_type != "org" else "")


# ------------------------------------------------------------------ políticas
def _resolve_scope(db: Session, scope_type: str, scope: str | None) -> int | None:
    if scope_type not in SCOPES:
        raise PlatformError(f"scope_type: {' | '.join(SCOPES)}")
    if scope_type == "org":
        return None
    if not scope:
        raise PlatformError("diga o escopo (slug do time, do agente ou do projeto)")
    if scope_type == "team":
        return find_team(db, scope).id
    if scope_type == "agent":
        a = db.scalar(select(Agent).where(Agent.slug == scope))
        if a is None:
            raise PlatformError(f"agente '{scope}' não existe")
        return a.id
    pr = db.scalar(select(Project).where(Project.slug == scope))
    if pr is None:
        raise PlatformError(f"projeto '{scope}' não existe")
    return pr.id


def policy_dict(db: Session, p: BudgetPolicy, acc: Access | None = None) -> dict:
    s = spent(db, p)
    inc = db.scalar(select(BudgetIncident).where(BudgetIncident.policy_id == p.id, BudgetIncident.window_key == window_key(p.window),
                                                 BudgetIncident.status == "open").order_by(BudgetIncident.id.desc()))
    return {"id": p.id, "scope_type": p.scope_type, "scope": scope_name(db, p), "window": p.window,
            "amount_usd": p.amount_usd, "warn_percent": p.warn_percent, "hard_stop": p.hard_stop, "active": p.active,
            "spent_usd": round(s, 4), "percent": round(100 * s / p.amount_usd, 1) if p.amount_usd else None,
            "state": "stopped" if inc and inc.kind == "hard_stop" else "warning" if inc else "ok",
            "can_manage": bool(acc and can_manage(db, acc, p)), "created_by": p.created_by}


def list_policies(db: Session, acc: Access) -> list[dict]:
    return [policy_dict(db, p, acc) for p in db.scalars(select(BudgetPolicy).order_by(BudgetPolicy.scope_type, BudgetPolicy.id))
            if can_view(db, acc, p)]


def create_policy(db: Session, acc: Access, scope_type: str, scope: str | None, amount_usd: float, window: str = "month",
                  warn_percent: int = 80, hard_stop: bool = True) -> dict:
    sid = _resolve_scope(db, scope_type, scope)
    p = BudgetPolicy(scope_type=scope_type, scope_id=sid, window=window, amount_usd=float(amount_usd),
                     warn_percent=int(warn_percent), hard_stop=bool(hard_stop), created_by=acc.p.name, created_at=now())
    _validate(p)
    if not can_manage(db, acc, p):
        raise Forbidden("orçamentos da empresa: admin; de time, agente ou projeto: o mantenedor do time")
    if db.scalar(select(BudgetPolicy.id).where(BudgetPolicy.scope_type == scope_type, BudgetPolicy.scope_id.is_(sid) if sid is None
                                               else BudgetPolicy.scope_id == sid, BudgetPolicy.window == window)):
        raise PlatformError("já existe um orçamento para este escopo e janela: edite-o")
    db.add(p)
    db.commit()
    audit(db, acc.p.name, "budget.create", scope_label(db, p), f"US$ {p.amount_usd} por {p.window}")
    evaluate(db, p)
    return policy_dict(db, p, acc)


def _validate(p: BudgetPolicy):
    if p.window not in WINDOWS:
        raise PlatformError(f"window: {' | '.join(WINDOWS)}")
    if not p.amount_usd or p.amount_usd <= 0:
        raise PlatformError("amount_usd precisa ser maior que zero")
    if not 1 <= p.warn_percent <= 100:
        raise PlatformError("warn_percent: de 1 a 100")


def update_policy(db: Session, acc: Access, policy_id: int, changes: dict) -> dict:
    p = db.get(BudgetPolicy, policy_id)
    if p is None or not can_manage(db, acc, p):
        raise Forbidden("orçamento não encontrado ou sem permissão")
    for k in ("amount_usd", "warn_percent", "hard_stop", "active", "window"):
        if changes.get(k) is not None:
            setattr(p, k, changes[k])
    _validate(p)
    db.commit()
    audit(db, acc.p.name, "budget.update", scope_label(db, p), ", ".join(f"{k}={v}" for k, v in changes.items() if v is not None))
    evaluate(db, p, actor=acc.p.name)
    return policy_dict(db, p, acc)


def delete_policy(db: Session, acc: Access, policy_id: int):
    p = db.get(BudgetPolicy, policy_id)
    if p is None or not can_manage(db, acc, p):
        raise Forbidden("orçamento não encontrado ou sem permissão")
    label = scope_label(db, p)
    db.delete(p)
    db.commit()
    _cache.clear()
    release_tasks(db)
    audit(db, acc.p.name, "budget.delete", label)


# ------------------------------------------------------------------ incidentes
def _open(db: Session, p: BudgetPolicy, kind: str, s: float):
    key = window_key(p.window)
    old = db.scalar(select(BudgetIncident).where(BudgetIncident.policy_id == p.id, BudgetIncident.window_key == key,
                                                 BudgetIncident.kind == kind))
    if old is not None:  # uma vez por janela (resolvido = liberado até o fim da janela)
        return None
    i = BudgetIncident(policy_id=p.id, window_key=key, kind=kind, spent_usd=round(s, 4), amount_usd=p.amount_usd,
                       opened_at=now())
    db.add(i)
    if kind == "hard_stop":  # a parada substitui o aviso
        for w in db.scalars(select(BudgetIncident).where(BudgetIncident.policy_id == p.id, BudgetIncident.window_key == key,
                                                         BudgetIncident.kind == "warning", BudgetIncident.status == "open")):
            w.status, w.resolved_by, w.resolved_at = "resolved", "sistema", now()
    db.commit()
    audit(db, "sistema", f"budget.{kind}", scope_label(db, p), f"US$ {s:.2f} de US$ {p.amount_usd:.2f} ({p.window})")
    _tell(db, p, i)
    return i


def _tell(db: Session, p: BudgetPolicy, i: BudgetIncident):
    from ..config import PUBLIC_BASE_URL
    from . import notify
    cfg = notify.org_cfg(db)
    title = (f"Orçamento parado: {scope_label(db, p)}" if i.kind == "hard_stop" else f"Orçamento em {p.warn_percent}%: "
             f"{scope_label(db, p)}")
    lines = [f"US$ {i.spent_usd:.2f} de US$ {p.amount_usd:.2f} ({'hoje' if p.window == 'day' else 'neste mês'})."]
    if i.kind == "hard_stop":
        lines.append("O trabalho no escopo está parado até alguém liberar (aumentar o orçamento ou liberar o resto da janela).")
    for uid in _people(db, p):
        u = db.get(User, uid)
        via = notify.route(cfg, u) if u and u.active else None
        if via:
            notify._bg(_send, uid, via, title, lines, f"{PUBLIC_BASE_URL}/app/#/budgets")


def _send(db: Session, user_id: int, via: str, title: str, lines: list, url: str):
    from . import notify
    notify._send_text(db, notify.org_cfg(db), db.get(User, user_id), via, title, lines, url, "Abrir orçamentos")


def evaluate(db: Session, p: BudgetPolicy, actor: str = "") -> BudgetIncident | None:
    """Confere a política agora: abre o aviso ou a parada; com o valor aumentado, fecha a parada aberta."""
    _cache.clear()
    if not p.active:
        return None
    s = spent(db, p)
    key = window_key(p.window)
    stop = db.scalar(select(BudgetIncident).where(BudgetIncident.policy_id == p.id, BudgetIncident.window_key == key,
                                                  BudgetIncident.kind == "hard_stop", BudgetIncident.status == "open"))
    if stop is not None and s < p.amount_usd:  # aumentaram o orçamento: libera
        stop.status, stop.resolved_by, stop.resolved_at = "resolved", actor or "sistema", now()
        stop.note = f"orçamento aumentado para US$ {p.amount_usd:.2f}"
        db.commit()
        release_tasks(db)
        return None
    if p.hard_stop and s >= p.amount_usd:
        return _open(db, p, "hard_stop", s)
    if s >= p.amount_usd * p.warn_percent / 100:
        return _open(db, p, "warning", s)
    return None


def sweep(db: Session):
    """De tempos em tempos (o executor das tarefas chama): avisos e paradas, mesmo sem nova chamada."""
    for p in db.scalars(select(BudgetPolicy).where(BudgetPolicy.active.is_(True))):
        evaluate(db, p)


def incident_dict(db: Session, i: BudgetIncident) -> dict:
    p = db.get(BudgetPolicy, i.policy_id)
    return {"id": i.id, "policy_id": i.policy_id, "scope_type": p.scope_type if p else "", "scope": scope_name(db, p) if p else "—",
            "kind": i.kind,
            "status": i.status, "window_key": i.window_key, "spent_usd": i.spent_usd, "amount_usd": i.amount_usd,
            "opened_at": iso(i.opened_at), "resolved_by": i.resolved_by, "resolved_at": iso(i.resolved_at) if i.resolved_at else None,
            "note": i.note}


def list_incidents(db: Session, acc: Access, status: str = "") -> list[dict]:
    q = select(BudgetIncident).order_by(BudgetIncident.id.desc()).limit(200)
    if status:
        q = q.where(BudgetIncident.status == status)
    out = []
    for i in db.scalars(q):
        p = db.get(BudgetPolicy, i.policy_id)
        if p and can_view(db, acc, p):
            out.append({**incident_dict(db, i), "can_resolve": i.status == "open" and can_manage(db, acc, p)})
    return out


def resolve(db: Session, acc: Access, incident_id: int, note: str = "") -> dict:
    """Libera o resto da janela: a política não para de novo até a próxima janela."""
    i = db.get(BudgetIncident, incident_id)
    p = db.get(BudgetPolicy, i.policy_id) if i else None
    if i is None or p is None or not can_manage(db, acc, p):
        raise Forbidden("incidente não encontrado ou sem permissão")
    if i.status != "open":
        raise PlatformError("o incidente já foi resolvido")
    i.status, i.resolved_by, i.resolved_at, i.note = "resolved", acc.p.name, now(), (note or "").strip()[:500]
    db.commit()
    _cache.clear()
    audit(db, acc.p.name, "budget.release", scope_label(db, p), f"incidente #{i.id} {i.kind}")
    release_tasks(db)
    return incident_dict(db, i)


# ------------------------------------------------------------------ onde a parada vale
def _policies_for(db: Session, agent: Agent | None, project_id: int | None) -> list[BudgetPolicy]:
    conds = [("org", None)]
    if agent is not None:
        conds.append(("agent", agent.id))
        if agent.team_id:
            conds.append(("team", agent.team_id))
    if project_id:
        conds.append(("project", project_id))
    out = []
    for st, sid in conds:
        q = select(BudgetPolicy).where(BudgetPolicy.active.is_(True), BudgetPolicy.hard_stop.is_(True),
                                       BudgetPolicy.scope_type == st)
        q = q.where(BudgetPolicy.scope_id.is_(None)) if sid is None else q.where(BudgetPolicy.scope_id == sid)
        out += list(db.scalars(q))
    return out


def stopped(db: Session, agent: Agent | None, project_id: int | None = None) -> dict | None:
    """A parada que vale agora para o agente (e o projeto), ou None. Com cache curto: o gateway chama a cada request."""
    key = (agent.id if agent else None, project_id)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    out = None
    for p in _policies_for(db, agent, project_id):
        k = window_key(p.window)
        stop = db.scalar(select(BudgetIncident).where(BudgetIncident.policy_id == p.id, BudgetIncident.window_key == k,
                                                      BudgetIncident.kind == "hard_stop"))
        if stop is None and spent(db, p) >= p.amount_usd:
            stop = _open(db, p, "hard_stop", spent(db, p))
        if stop is not None and stop.status == "open":
            out = {"incident": stop.id, "scope": scope_label(db, p), "amount_usd": p.amount_usd, "window": p.window}
            break
    _cache[key] = (time.monotonic(), out)
    return out


def release_tasks(db: Session):
    """Depois de liberar: as tarefas paradas por orçamento voltam para a fila (cada uma confere de novo ao rodar)."""
    from . import employee_tasks as tasksmod
    _cache.clear()
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.status == "waiting_budget")):
        t.status = "new"
        tasksmod.event(db, t, "budget", {"released": True})
    db.commit()
