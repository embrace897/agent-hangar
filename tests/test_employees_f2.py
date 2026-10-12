# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Digital employee F2: rotinas, webhook de entrada com deduplicação, metas medidas, relatório semanal, aprendizado
(sugestões e lições) e espera crescente entre tentativas."""
from datetime import datetime, timedelta

import pytest

from app import config
from app import services as svc
from app.cron import zone
from app.db import SessionLocal
from app.models import Employee, EmployeeReport, EmployeeRoutine, EmployeeTask, TaskCheckpoint, now
from app.services import employee_tasks as tasksmod
from app.services import employee_work as workmod
from app.services import employees as empmod

from .test_employees import TOOLS, assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401


def _emp(db, slug) -> Employee:
    return db.query(Employee).filter(Employee.agent_id == svc.get_agent(db, slug).id).one()


def _local(db, y, mo, d, h) -> datetime:
    return datetime(y, mo, d, h, 0, tzinfo=zone(workmod._tz(db)))


# ------------------------------------------------------------------ rotinas
def test_routine_fires_only_when_active_and_never_twice_at_once(client, env, rt):
    slug = hire(client, env)
    base = f"/api/employees/{slug}/routines"
    assert client.post(base, headers=env["h"]["out"], json={"title": "x", "cron": "0 9 * * 1"}).status_code == 403
    assert client.post(base, headers=env["h"]["own"], json={"title": "x", "cron": "isso não é cron"}).status_code == 400
    assert client.post(base, headers=env["h"]["own"], json={"title": "x", "cron": "* * * * *"}).status_code == 400  # intervalo mínimo
    r = client.post(base, headers=env["h"]["own"], json={"title": "Revisar renovações", "body": "próximos 30 dias",
                                                         "cron": "0 9 * * 1"}).json()
    assert r["next_run_at"] and r["enabled"] and "9" in r["when"]
    d = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()
    assert "schedule" in d["channels"] and d["routines"][0]["id"] == r["id"]
    later = now() + timedelta(days=8)
    with SessionLocal() as db:
        assert workmod.fire_routines(db, later) == []  # em contratação: não dispara
    make_active(client, env, slug, rt)
    with SessionLocal() as db:
        db.get(EmployeeRoutine, r["id"]).next_run_at = now() - timedelta(minutes=1)
        db.commit()
        fired = workmod.fire_routines(db)
        assert len(fired) == 1
        t = db.get(EmployeeTask, fired[0])
        assert t.source == "routine" and t.routine_id == r["id"] and t.title == "Revisar renovações"
        rt_row = db.get(EmployeeRoutine, r["id"])
        assert tasksmod._aware(rt_row.next_run_at) > now()  # reagendada
        rt_row.next_run_at = now() - timedelta(minutes=1)
        db.commit()
        assert workmod.fire_routines(db) == []  # a anterior ainda está na fila: pula
        assert db.get(EmployeeRoutine, r["id"]).skipped == 1
    work()
    assert task(client, env, fired[0])["status"] == "done"
    now_run = client.post(f"{base}/{r['id']}/run", headers=env["h"]["mgr"]).json()
    assert now_run["source"] == "routine"
    assert client.post(f"{base}/{r['id']}/run", headers=env["h"]["mgr"]).status_code == 400  # anterior aberta
    off = client.patch(f"{base}/{r['id']}", headers=env["h"]["mgr"], json={"enabled": False}).json()
    assert off["enabled"] is False and off["next_run_at"] is None
    assert client.delete(f"{base}/{r['id']}", headers=env["h"]["mgr"]).json()["ok"] is True
    assert client.get(base, headers=env["h"]["own"]).json() == []


def test_hire_with_routines_and_measured_kpis(client, env):
    bad = client.post("/api/employees", headers=env["h"]["own"], json={
        **_fields_ok(env), "kpis": [{"name": "x", "metric": "nao_existe", "target": 1}]})
    assert bad.status_code == 400
    slug = hire(client, env, routines=[{"title": "Toda segunda", "cron": "0 9 * * 1"}],
                kpis=[{"name": "Entregas", "metric": "done_rate", "target": 90}, "Cliente feliz"], report_weekday=4)
    d = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()
    assert len(d["routines"]) == 1 and d["report_weekday"] == 4
    assert d["kpi_status"][0] == {"name": "Entregas", "metric": "done_rate", "target": 90.0, "actual": None, "unit": "%",
                                  "higher_is_better": True, "ok": None}
    assert d["kpi_status"][1]["name"] == "Cliente feliz" and d["kpi_status"][1]["metric"] == ""


def _fields_ok(env):
    from .test_employees import _fields
    return _fields(env)


# ------------------------------------------------------------------ webhook de entrada
def test_inbound_webhook_needs_the_token_and_deduplicates(client, env, rt):
    slug = hire(client, env)
    url = f"/hooks/employees/{slug}"
    assert client.post(url, json={"title": "x"}).status_code == 401
    assert client.post(f"/api/employees/{slug}/webhook", headers=env["h"]["out"], json={}).status_code == 403
    w = client.post(f"/api/employees/{slug}/webhook", headers=env["h"]["own"], json={}).json()
    token = w["token"]
    assert token.startswith("ehk_") and w["hint"] == token[-4:] and "curl" in w["example"]
    d = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()
    assert d["webhook"]["enabled"] and "token" not in d["webhook"] and "webhook" in d["channels"]
    assert client.post(url, headers={"Authorization": "Bearer ehk_errado"}, json={"title": "x"}).status_code == 401
    r409 = client.post(url, headers={"Authorization": f"Bearer {token}"}, json={"title": "x"})
    assert r409.status_code == 409, r409.text  # contratação
    make_active(client, env, slug, rt)
    h = {"Authorization": f"Bearer {token}"}
    first = client.post(url, headers=h, json={"title": "Lead novo: Acme", "body": "ligar", "dedupe_key": "crm-77",
                                             "source": "CRM"}).json()
    assert first["duplicate"] is False
    again = client.post(url, headers=h, json={"title": "Lead novo: Acme", "dedupe_key": "crm-77"}).json()
    assert again == {"id": first["id"], "status": again["status"], "duplicate": True}
    other = client.post(url, headers={"X-Hangar-Token": token}, json={"title": "Outro", "dedupe_key": "crm-78"}).json()
    assert other["duplicate"] is False and other["id"] != first["id"]
    t = task(client, env, first["id"])
    assert t["source"] == "webhook" and "CRM" in t["requester"]
    work()
    assert task(client, env, first["id"])["status"] == "done"
    # trocar o token invalida o anterior; desligar fecha o canal
    new = client.post(f"/api/employees/{slug}/webhook", headers=env["h"]["mgr"], json={}).json()["token"]
    assert client.post(url, headers=h, json={"title": "x"}).status_code == 401
    client.post(f"/api/employees/{slug}/webhook", headers=env["h"]["mgr"], json={"enabled": False})
    assert client.post(url, headers={"Authorization": f"Bearer {new}"}, json={"title": "x"}).status_code == 401


# ------------------------------------------------------------------ metas e relatório semanal
def test_kpis_are_measured_and_the_weekly_report_compares_them(client, env, rt):
    slug = hire(client, env, kpis=[{"name": "Concluir", "metric": "done_rate", "target": 90},
                                   {"name": "Custo", "metric": "cost_per_task", "target": 0.001}])
    make_active(client, env, slug, rt)
    for i in range(2):
        assign(client, env, slug, f"tarefa {i}")
    work()
    k = {x["metric"]: x for x in client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()["kpi_status"]}
    assert k["done_rate"]["actual"] == 100.0 and k["done_rate"]["ok"] is True
    assert k["cost_per_task"]["actual"] == 0.01 and k["cost_per_task"]["ok"] is False  # 0.01 por rodada no FakeRuntime
    with SessionLocal() as db:
        e = _emp(db, slug)
        e.report_hour, e.report_weekday, e.last_report_on, e.last_weekly_on = 18, 0, "", ""
        # o tick do work() pode já ter feito os relatórios de hoje (numa segunda depois das 18h): o teste conta só os dele
        db.query(EmployeeReport).filter(EmployeeReport.employee_id == e.id).delete()
        db.commit()
        monday = _local(db, 2026, 10, 5, 19)  # uma segunda-feira, depois das 18h
        empmod.daily_reports(db, monday)
        periods = [r.period for r in db.query(EmployeeReport).filter(EmployeeReport.employee_id == e.id)]
        assert periods.count("weekly") == 1
        weekly = db.query(EmployeeReport).filter(EmployeeReport.employee_id == e.id, EmployeeReport.period == "weekly").one()
        assert "Metas (30 dias)" in weekly.summary and "Concluir" in weekly.summary and weekly.metrics["kpis"]
        empmod.daily_reports(db, monday + timedelta(days=1))  # terça: só o diário
        empmod.daily_reports(db, monday + timedelta(days=7, minutes=1))  # segunda seguinte: outro semanal
        weeklies = db.query(EmployeeReport).filter(EmployeeReport.employee_id == e.id, EmployeeReport.period == "weekly").count()
        assert weeklies == 2
    r = client.post(f"/api/employees/{slug}/reports?period=weekly", headers=env["h"]["mgr"]).json()
    assert "Metas" in r["summary"]
    assert client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"report_weekday": 9}).status_code == 400


# ------------------------------------------------------------------ aprendizado
def test_repeated_corrections_become_a_lesson_in_the_next_tasks(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    reasons = ["sempre com cópia para vendas@acme.com", "nunca prometa desconto", None]
    for i, why in enumerate(reasons):
        tid = assign(client, env, slug, f'use send_email {{"to": "c{i}@cliente.com", "body": "oi"}}')
        work()
        r = open_request(client, env, tid)
        if why is None:
            client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"],
                        json={"decision": "approve_edited", "edit": {"to": f"c{i}@cliente.com", "body": "olá"}})
        else:
            client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "reject", "reason": why})
        work()
    sug = client.get(f"/api/employees/{slug}/suggestions", headers=env["h"]["own"]).json()
    lesson = next(s for s in sug if s["kind"] == "lesson")
    assert lesson["id"] == "lesson:send_email" and lesson["evidence"] == 3
    assert "vendas@acme.com" in lesson["lesson"] and "mudou body" in lesson["lesson"]
    assert client.post(f"/api/employees/{slug}/suggestions/{lesson['id']}/apply", headers=env["h"]["out"], json={}).status_code == 403
    out = client.post(f"/api/employees/{slug}/suggestions/{lesson['id']}/apply", headers=env["h"]["mgr"],
                      json={"text": "Copie vendas@acme.com e nunca prometa desconto."}).json()
    assert out["lesson"]["text"].startswith("Copie vendas")
    assert all(s["id"] != lesson["id"] for s in client.get(f"/api/employees/{slug}/suggestions", headers=env["h"]["own"]).json())
    tid = assign(client, env, slug, "resumo")
    with SessionLocal() as db:
        cp = db.query(TaskCheckpoint).filter(TaskCheckpoint.task_id == tid).order_by(TaskCheckpoint.seq).first()
        assert "Lições do seu gestor" in cp.messages[0]["content"] and "Copie vendas@acme.com" in cp.messages[0]["content"]
    lid = out["lesson"]["id"]
    assert client.delete(f"/api/employees/{slug}/lessons/{lid}", headers=env["h"]["mgr"]).json()["lessons"] == []
    manual = client.post(f"/api/employees/{slug}/lessons", headers=env["h"]["own"], json={"text": "Assine como Equipe Acme"})
    assert manual.json()["lessons"][0]["source"] == "manual"


def test_always_approved_unedited_suggests_loosening_but_never_below_the_floor(client, env, rt, monkeypatch):
    monkeypatch.setattr(config, "LEARN_MIN_APPROVALS", 2)
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    for i in range(2):
        tid = assign(client, env, slug, f'use update_crm {{"id": {i}}}')
        work()
        client.post(f"/api/decisions/{open_request(client, env, tid)['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
        tid = assign(client, env, slug, f'use send_email {{"to": "x{i}@cliente.com"}}')
        work()
        client.post(f"/api/decisions/{open_request(client, env, tid)['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    work()
    sug = {s["id"]: s for s in client.get(f"/api/employees/{slug}/suggestions", headers=env["h"]["own"]).json()}
    assert "loosen:write_internal" in sug and sug["loosen:write_internal"]["to"] == "notify"
    assert "loosen:send_external" not in sug  # o piso da empresa exige aprovação para enviar para fora
    sid = "loosen:write_internal"
    assert client.post(f"/api/employees/{slug}/suggestions/{sid}/apply", headers=env["h"]["own"], json={}).status_code == 403
    out = client.post(f"/api/employees/{slug}/suggestions/{sid}/apply", headers=env["h"]["mgr"], json={}).json()
    assert next(a for a in out["authority"] if a["action_type"] == "write_internal")["mode"] == "notify"
    with SessionLocal() as db:
        g = tasksmod.gate(db, svc.get_agent(db, slug), assign(client, env, slug, "x"), "update_crm", TOOLS["update_crm"], {"id": 9})
        assert g["status"] == "allowed"
    # dispensar esconde a sugestão
    monkeypatch.setattr(config, "LEARN_MIN_APPROVALS", 1)
    with SessionLocal() as db:
        ids = {s["id"] for s in workmod.suggestions(db, _emp(db, slug))}
    for s in ids:
        client.post(f"/api/employees/{slug}/suggestions/{s}/dismiss", headers=env["h"]["mgr"])
    assert client.get(f"/api/employees/{slug}/suggestions", headers=env["h"]["own"]).json() == []


# ------------------------------------------------------------------ espera entre tentativas
def test_failed_round_waits_longer_each_time_before_retrying(client, env, rt, monkeypatch):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "nada")

    class Down:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise ConnectionError("agente fora do ar")
    monkeypatch.setattr(tasksmod, "HTTP", lambda: Down())
    waits = []
    for _ in range(2):
        with SessionLocal() as db:
            db.get(EmployeeTask, tid).not_before = None
            db.commit()
        work()
        with SessionLocal() as db:
            t = db.get(EmployeeTask, tid)
            assert t.status == "new" and t.not_before is not None
            waits.append((tasksmod._aware(t.not_before) - now()).total_seconds())
            t.not_before = now() + timedelta(minutes=5)
            db.commit()
        assert tasksmod.tick() == []  # ainda esperando: ninguém pega
    assert waits[1] > waits[0] * 1.5
    monkeypatch.setattr(tasksmod, "HTTP", lambda: rt)
    with SessionLocal() as db:
        db.get(EmployeeTask, tid).not_before = now() - timedelta(seconds=1)
        db.commit()
    work()
    assert task(client, env, tid)["status"] == "done"


@pytest.mark.parametrize("attempts,expected", [(1, 30), (2, 60), (3, 120), (10, 600)])
def test_retry_at_grows_and_is_capped(attempts, expected):
    assert abs((tasksmod.retry_at(attempts) - now()).total_seconds() - expected) < 2
