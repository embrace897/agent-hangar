# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Auto-organização: rotina, rota da fila e colega propostos com números; o próprio funcionário propõe; nada vale sem
aprovação; recusa não volta por 30 dias."""
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models import (
    Agent,
    Employee,
    EmployeeRoutine,
    EmployeeTask,
    HumanRequest,
    TaskEvent,
    Team,
    WorkItem,
    WorkQueue,
    now,
)
from app.services import employee_tasks as tasksmod
from app.services import self_org

from .test_employees import assign, env, hire, make_active, rt  # noqa: F401
from .test_phase_g import _close_leftover_tasks, _gate  # noqa: F401


def _emp(db, slug):
    a = db.query(Agent).filter_by(slug=slug).one()
    return db.query(Employee).filter_by(agent_id=a.id).one()


def _handoff(slug, tid, to):
    """O repasse é uma ferramenta do runtime (hangar:handoff_task), não da plataforma."""
    with SessionLocal() as db:
        return tasksmod.gate(db, db.query(Agent).filter_by(slug=slug).one(), tid, "handoff_task", "hangar:handoff_task",
                             {"to": to, "title": "cadastro do cliente"}, "", "handoff")


def _requests(db, e, change):
    return [r for r in db.query(HumanRequest).filter(HumanRequest.employee_id == e.id, HumanRequest.kind == "org_change")
            if (r.action_payload or {}).get("change") == change]


def test_a_recurring_request_becomes_a_routine_after_approval(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    mondays = [datetime.now(UTC).replace(hour=9, minute=5, second=0, microsecond=0) - timedelta(days=7 * k) for k in (1, 2, 3)]
    mondays = [d - timedelta(days=(d.isoweekday() - 1)) for d in mondays]  # segundas às 9h (UTC = fuso da empresa)
    with SessionLocal() as db:
        e = _emp(db, slug)
        for i, d in enumerate(mondays):
            t = tasksmod.create_task(db, e, f"Relatório semanal de vendas {40 + i}", "consolidar as vendas da semana", "portal", "x", None, 2)
            t.created_at, t.status = d, "done"
        db.commit()
        assert self_org.detect_routines(db, e) == 1
        assert self_org.detect_routines(db, e) == 0  # já proposta
        r = _requests(db, e, "routine")[0]
        assert r.action_payload["cron"] == "0 9 * * 1" and r.action_payload["evidence"]["count"] == 3 and "segunda" in r.question
        rid = r.id
    d = client.post(f"/api/decisions/{rid}", headers=env["h"]["mgr"], json={"decision": "approve"})
    assert d.status_code == 200, d.text
    with SessionLocal() as db:
        e = _emp(db, slug)
        rt_ = db.query(EmployeeRoutine).filter(EmployeeRoutine.employee_id == e.id).one()
        assert rt_.title.startswith("Relatório semanal de vendas") and rt_.cron == "0 9 * * 1" and rt_.next_run_at is not None
        assert "schedule" in e.channels
        assert self_org.detect_routines(db, e) == 0  # virou rotina: não propõe de novo


def test_queue_route_and_colleague_proposals_and_a_refusal_does_not_come_back(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    other = hire(client, env, title="Analista de cadastro")
    make_active(client, env, other, rt)
    q = client.put("/api/queues", headers=env["h"]["mgr"], json={"name": f"Back {env['n']}", "team": env["team"], "categories": [
        {"name": "Cadastro", "keywords": ["cadastro"], "sla_hours": 8, "route": {"type": "people"}}], "default_category": "Cadastro"}).json()
    with SessionLocal() as db:
        team = db.query(Team).filter_by(slug=env["team"]).one()
        qq = db.query(WorkQueue).filter_by(slug=q["slug"]).one()
        for i in range(5):
            db.add(WorkItem(queue_id=qq.id, team_id=team.id, title=f"Cadastro {i}", category="Cadastro", status="done",
                            handled_by="human", done_at=now() - timedelta(days=2), created_at=now() - timedelta(days=3)))
        db.commit()
        assert self_org.detect_queue_routes(db) >= 1
        r = next(r for e in db.query(Employee).all() for r in _requests(db, e, "queue_route")
                 if r.action_payload["queue"] == q["slug"])
        assert r.action_payload["evidence"]["done_by_people"] == 5
        rid = r.id
    client.post(f"/api/decisions/{rid}", headers=env["h"]["mgr"], json={"decision": "approve"})
    with SessionLocal() as db:
        cat = db.query(WorkQueue).filter_by(slug=q["slug"]).one().categories[0]
        assert cat["route"]["type"] == "employee" and cat["route"]["refs"][0] in (slug, other)
    # repasse negado duas vezes → propõe o colega; recusado → não volta por 30 dias
    tid = assign(client, env, slug, "repassar")
    for _ in range(2):
        g = _handoff(slug, tid, other)
        assert g["status"] == "denied" and "colegas liberados" in g["message"]
    with SessionLocal() as db:
        assert db.query(TaskEvent).filter(TaskEvent.task_id == tid, TaskEvent.kind == "handoff_denied").count() == 2
        e = _emp(db, slug)
        assert self_org.detect_colleagues(db, e) == 1
        rid = _requests(db, e, "colleague")[0].id
    client.post(f"/api/decisions/{rid}", headers=env["h"]["mgr"], json={"decision": "reject", "reason": "agora não"})
    with SessionLocal() as db:
        assert self_org.detect_colleagues(db, _emp(db, slug)) == 0  # recusado há pouco: não volta
        r = db.get(HumanRequest, rid)
        r.decided_at = now() - timedelta(days=31)  # passou um mês
        db.commit()
        assert self_org.detect_colleagues(db, _emp(db, slug)) == 1
        rid = [r for r in _requests(db, _emp(db, slug), "colleague") if r.status == "open"][0].id
    client.post(f"/api/decisions/{rid}", headers=env["h"]["mgr"], json={"decision": "approve"})
    with SessionLocal() as db:
        assert other in _emp(db, slug).colleagues
    assert "colegas liberados" not in (_handoff(slug, tid, other).get("message") or "")  # agora pode repassar


def test_the_employee_proposes_an_instruction_that_only_counts_after_approval(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "fechamento")
    g = _gate(slug, tid, "propose_org_change", {"change": "instruction", "why": "3 notas voltaram por falta do centro de custo",
                                                "text": "Sempre preencha o centro de custo antes de lançar uma nota."})
    assert g["status"] == "allowed" and "só vale depois" in g["result"]
    assert "faltou" in _gate(slug, tid, "propose_org_change", {"change": "routine", "why": "x"})["result"]
    with SessionLocal() as db:
        e = _emp(db, slug)
        assert not (e.lessons or [])  # nada vale antes da aprovação
        r = _requests(db, e, "instruction")[0]
        assert r.action_payload["source"] == "employee" and r.task_id == tid
        rid = r.id
    client.post(f"/api/decisions/{rid}", headers=env["h"]["mgr"], json={
        "decision": "approve_edited", "edit": {"text": "Sempre preencha o centro de custo e o projeto antes de lançar uma nota."}})
    with SessionLocal() as db:
        assert any("centro de custo e o projeto" in x["text"] for x in _emp(db, slug).lessons)
        assert db.query(EmployeeTask).filter(EmployeeTask.id == tid).one().status != "waiting_human"  # não travou a tarefa
