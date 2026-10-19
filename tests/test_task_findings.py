# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Laços com resultado "sem achados" (issue #56, parte A): só o que importa chega a uma pessoa, e a plataforma garante a
coerência (não o prompt)."""
from sqlalchemy import select

from app import services as svc
from app.db import SessionLocal
from app.models import EmployeeRoutine, EmployeeTask, TaskEvent
from app.services import employee_tasks as tasksmod
from app.services import task_findings

from .test_employees import assign, env, hire, make_active, rt, task, work  # noqa: F401


def _inbox_tasks(client, env, who="req"):
    return [i["task_id"] for i in client.get("/api/inbox", headers=env["h"][who]).json()["items"] if i["kind"] == "task"]


def _emp(db, slug):
    from app.models import Agent, Employee
    a = db.scalar(select(Agent).where(Agent.slug == slug))
    return db.scalar(select(Employee).where(Employee.agent_id == a.id))


def test_no_findings_closes_quietly_and_is_counted_apart(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    quiet = assign(client, env, slug, "confira as renovações\nfinish: false")
    found = assign(client, env, slug, "confira os pagamentos\nfinish: true | Fatura 88 cobrada em dobro; estornar a segunda.")
    plain = assign(client, env, slug, "resuma o mês")
    work()
    q, fd, pl = task(client, env, quiet), task(client, env, found), task(client, env, plain)
    assert q["status"] == fd["status"] == pl["status"] == "done"
    assert q["finding"] is False and q["result"] == "Sem achados."
    assert fd["finding"] is True and fd["finding_summary"].startswith("Fatura 88") and pl["finding"] is None
    # fora da caixa de entrada de quem pediu; o achado e a tarefa comum continuam lá
    box = _inbox_tasks(client, env)
    assert quiet not in box and found in box and plain in box
    # contado à parte: métricas, metas e relatório
    with SessionLocal() as db:
        e = _emp(db, slug)
        m = svc.employees.metrics(db, e, 30)
        assert m["no_findings"] == 1 and m["findings"] == 1 and m["actioned_findings"] == 0
        k = svc.employee_work.kpi_actuals(db, e)
        assert k["finding_rate"] == 50.0 and k["actioned_finding_rate"] == 0.0
        c = svc.employee_work.career_status(db, e)
        tasks = next((x for x in c["criteria"] if x["key"] == "tasks"), None)
        if tasks is not None:  # a execução sem achados não conta como tarefa concluída na carreira
            assert tasks["value"] == m["done"] - 1
        rep = svc.employees.make_report(db, e, "daily")
        assert "Laços: 2 execução(ões), 1 com achado" in rep.summary


def test_platform_enforces_coherence(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "verificação")
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        # achado precisa de resumo curto
        assert task_findings.declare(db, t, True, "")["status"] == "denied"
        long = task_findings.declare(db, t, True, "x" * 401)
        assert long["status"] == "denied" and "401" in long["message"]
        assert task_findings.declare(db, t, "talvez", "")["status"] == "denied"
        # agiu: não pode ser "sem achados"
        tasksmod.event(db, t, "gate", {"tool": "update_crm", "action_type": "write_internal", "mode": "auto"})
        db.commit()
        d = task_findings.declare(db, t, False, "")
        assert d["status"] == "denied" and "executou uma ação" in d["message"]
        ok = task_findings.declare(db, t, True, "Cadastro desatualizado; corrigi o e-mail.")
        assert ok["status"] == "allowed" and t.finding is True


def test_declared_no_findings_loses_effect_if_something_changed_later(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "verificação")
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        assert task_findings.declare(db, t, False, "")["status"] == "allowed"
        tasksmod.event(db, t, "shadow", {"tool": "send_email", "action_type": "send_external"})  # depois da declaração
        db.commit()
        tasksmod._finish(db, t, "done", result="enviei o aviso")
        db.refresh(t)
        assert t.finding is None and t.result == "enviei o aviso"  # vira uma tarefa comum, que aparece para as pessoas
        kinds = [ev.payload for ev in db.scalars(select(TaskEvent).where(TaskEvent.task_id == tid, TaskEvent.kind == "finding"))]
        assert kinds[-1]["findings"] is None and kinds[-1]["void"] == ["shadow"]


def test_external_worker_reports_no_findings(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    token = client.put(f"/api/employees/{slug}/runtime", headers=env["h"]["own"], json={"runtime": "external"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    tid = assign(client, env, slug, "confira os alertas")
    assert client.post("/hooks/worker/heartbeat", headers=hdr, json={}).json()["task"]["id"] == tid
    r = client.post(f"/hooks/worker/tasks/{tid}/result", headers=hdr, json={"status": "done", "result": "nada", "findings": False})
    assert r.status_code == 200, r.text
    t = task(client, env, tid)
    assert t["finding"] is False and t["result"] == "Sem achados."
    tid2 = assign(client, env, slug, "confira de novo")
    client.post("/hooks/worker/heartbeat", headers=hdr, json={})
    r = client.post(f"/hooks/worker/tasks/{tid2}/result", headers=hdr, json={"status": "done", "result": "achei", "findings": True})
    assert r.status_code == 409 and "resumo" in r.text  # achado sem resumo é recusado (recusa do worker: 409)


def test_quiet_routine_is_suggested_in_the_weekly_report(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    with SessionLocal() as db:
        e = _emp(db, slug)
        r = EmployeeRoutine(employee_id=e.id, title="Conferir backups", body="", cron="0 * * * *", enabled=True)
        db.add(r)
        db.flush()
        for _ in range(4):
            t = tasksmod.create_task(db, e, "Conferir backups", "", "routine", "rotina", None, 2, routine_id=r.id)
            t.finding, t.status = False, "done"
        db.commit()
        assert svc.employees.quiet_routines(db, e) == ["Conferir backups"]
        rep = svc.employees.make_report(db, e, "weekly")
        assert "Rotina 'Conferir backups': nenhum achado em 4 semanas" in rep.summary


def test_loop_tasks_get_the_finish_task_guidance(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    with SessionLocal() as db:
        e = _emp(db, slug)
        loop = tasksmod.create_task(db, e, "Conferir", "", "routine", "rotina", None, 2)
        plain = tasksmod.create_task(db, e, "Resumo", "", "portal", "ana", None, 2)
        from app.services import employee_teamwork as teamwork
        assert any("finish_task(findings=false)" in x for x in teamwork.prompt_extras(db, e, loop, []))
        assert not any("finish_task" in x for x in teamwork.prompt_extras(db, e, plain, []))
