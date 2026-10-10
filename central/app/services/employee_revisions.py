"""Histórico do cargo: cada mudança no cargo, na alçada ou nas configurações de um Digital employee vira uma revisão (o
retrato depois da mudança, quem mudou e quando). Restaurar aplica o retrato antigo como uma revisão nova — nada se perde.

O nível de autonomia e o modo sombra têm o próprio histórico (a carreira) e não entram no retrato.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuthorityRule, Employee, EmployeeRevision, now
from .access import Access
from .common import PlatformError, audit, iso

FIELDS = ("title", "mission", "responsibilities", "systems", "kpis", "working_hours", "task_budget_usd",
          "task_time_limit_min", "report_hour", "report_weekday", "plan_policy", "recall", "quality_sample", "review_policy",
          "colleagues", "channels", "reports_to_id")
RULE_FIELDS = ("action_type", "mode", "conditions", "approver", "expires_in_min", "note")


def snapshot(db: Session, e: Employee) -> dict:
    snap = {k: getattr(e, k) for k in FIELDS}
    snap["rules"] = [{k: getattr(r, k) for k in RULE_FIELDS}
                     for r in db.scalars(select(AuthorityRule).where(AuthorityRule.employee_id == e.id).order_by(AuthorityRule.id))]
    return snap


def record(db: Session, e: Employee, who: str, summary: str) -> EmployeeRevision | None:
    """Grava o retrato atual, se mudou desde a última revisão."""
    snap = snapshot(db, e)
    last = db.scalar(select(EmployeeRevision).where(EmployeeRevision.employee_id == e.id).order_by(EmployeeRevision.version.desc()))
    if last is not None and last.snapshot == snap:
        return None
    v = (db.scalar(select(func.max(EmployeeRevision.version)).where(EmployeeRevision.employee_id == e.id)) or 0) + 1
    r = EmployeeRevision(employee_id=e.id, version=v, snapshot=snap, summary=summary[:300], changed_by=who, at=now())
    db.add(r)
    db.commit()
    return r


def diff(old: dict, new: dict) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def list_revisions(db: Session, acc: Access, slug: str) -> list[dict]:
    from .employees import find
    e, _ = find(db, acc, slug)
    rows = list(db.scalars(select(EmployeeRevision).where(EmployeeRevision.employee_id == e.id).order_by(EmployeeRevision.version.desc())))
    out = []
    for i, r in enumerate(rows):
        prev = rows[i + 1].snapshot if i + 1 < len(rows) else {}
        out.append({"version": r.version, "summary": r.summary, "changed_by": r.changed_by, "at": iso(r.at),
                    "changed": diff(prev, r.snapshot) if prev else [], "snapshot": r.snapshot, "current": i == 0})
    return out


def restore(db: Session, acc: Access, slug: str, version: int) -> dict:
    from .employees import employee_dict, find
    e, _ = find(db, acc, slug, "manage")
    r = db.scalar(select(EmployeeRevision).where(EmployeeRevision.employee_id == e.id, EmployeeRevision.version == int(version)))
    if r is None:
        raise PlatformError(f"revisão v{version} não existe")
    snap = r.snapshot
    for k in FIELDS:
        if k in snap:
            setattr(e, k, snap[k])
    for old in db.scalars(select(AuthorityRule).where(AuthorityRule.employee_id == e.id)):
        db.delete(old)
    for rule in snap.get("rules") or []:
        db.add(AuthorityRule(employee_id=e.id, **{k: rule.get(k) for k in RULE_FIELDS if k in rule}))
    db.commit()
    record(db, e, acc.p.name, f"restaurada a v{r.version}")
    audit(db, acc.p.name, "employee.restore", slug, f"v{r.version}")
    return employee_dict(db, e, acc, detail=True)
