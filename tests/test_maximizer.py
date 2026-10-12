# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Modo maximizador por projeto: mais rodadas e passos, instrução de ir até o fim, nova tentativa em falha técnica (nunca
em alçada ou orçamento), relatório e painel de supervisão — sem mexer na alçada."""
from app.db import SessionLocal
from app.models import EmployeeTask, Project, TaskEvent, TaskMessage
from app.services import employee_tasks as tasksmod
from app.services import goals, maximizer

from .test_employees import env, hire, make_active, rt, task, work  # noqa: F401
from .test_phase_g import _close_leftover_tasks  # noqa: F401


def _project(client, env, slug):
    r = client.post("/api/projects", headers=env["h"]["mgr"], json={"name": f"Fechamento {env['n']}", "team": env["team"],
                                                                    "employee": slug})
    assert r.status_code == 200, r.text
    return r.json()["slug"]


def test_maximizer_settings_prompt_rounds_retry_and_supervision(client, env, rt, monkeypatch):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    ps = _project(client, env, slug)
    assert client.put(f"/api/projects/{ps}/autonomy", headers=env["h"]["req"], json={"mode": "maximizer"}).status_code == 403
    assert client.put(f"/api/projects/{ps}/autonomy", headers=env["h"]["mgr"], json={"mode": "maximizer", "max_steps": 500}).status_code == 400
    r = client.put(f"/api/projects/{ps}/autonomy", headers=env["h"]["mgr"], json={"mode": "maximizer", "extra_rounds": 10, "auto_retry": 1})
    assert r.status_code == 200 and r.json()["autonomy"]["max_steps"] == 60  # o padrão vem junto
    tid = client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"], json={"title": "Fechar o mês", "body": "tudo", "project": ps}).json()["id"]
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        assert maximizer.extra_rounds(db, t) == 10 and maximizer.max_steps(db, t) == 60
        ctx = goals.task_context(db, t)
        assert "MODO MAXIMIZADOR" in ctx and "handoff_task" in ctx and "alçada continua valendo" in ctx
    # a central pede ao runtime mais passos por rodada
    seen = {}
    orig = rt.post

    def post(url, headers=None, json=None, timeout=None):
        seen["steps"] = (headers or {}).get("X-Hangar-Max-Steps")
        return orig(url, headers, json, timeout)
    monkeypatch.setattr(rt, "post", post)
    work()
    assert seen["steps"] == "60"
    # falha técnica: tenta de novo uma vez, com o erro na conversa; falha de alçada não repete
    tid2 = client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"], json={"title": "Conciliar", "body": "x", "project": ps}).json()["id"]
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid2)
        tasksmod._finish(db, t, "failed", error="timeout ao falar com o ERP")
        assert db.get(EmployeeTask, tid2).status == "new"  # voltou para a fila
        assert any("timeout ao falar com o ERP" in m.body for m in db.query(TaskMessage).filter(TaskMessage.task_id == tid2))
        tasksmod._finish(db, db.get(EmployeeTask, tid2), "failed", error="timeout de novo")
        assert db.get(EmployeeTask, tid2).status == "failed"  # só uma nova tentativa
        assert db.query(TaskEvent).filter(TaskEvent.task_id == tid2, TaskEvent.kind == "auto_retry").count() == 1
    tid3 = client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"], json={"title": "Pagar", "body": "x", "project": ps}).json()["id"]
    with SessionLocal() as db:
        tasksmod._finish(db, db.get(EmployeeTask, tid3), "failed", error="ação negada pela alçada")
        assert db.get(EmployeeTask, tid3).status == "failed"
    # supervisão e relatório
    s = client.get(f"/api/projects/{ps}/supervision", headers=env["h"]["mgr"]).json()
    assert s["mode"] == "maximizer" and s["tasks"] == 3 and s["retries"] == 1 and s["by_status"].get("failed") == 2
    with SessionLocal() as db:
        p = db.query(Project).filter_by(slug=ps).one()
        assert p.autonomy.get("last_report_at")  # o tick do work() já mandou o primeiro relatório
        p.autonomy = {**p.autonomy, "last_report_at": None}  # passou o período
        db.commit()
        assert maximizer.sweep(db) >= 1
        p = db.query(Project).filter_by(slug=ps).one()
        assert "Projeto" in p.autonomy["last_report"] and p.autonomy["last_report_at"]
        assert maximizer.sweep(db) == 0  # o próximo só depois de report_hours
    # desligado: tudo volta ao normal
    client.put(f"/api/projects/{ps}/autonomy", headers=env["h"]["mgr"], json={"mode": "normal"})
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        assert maximizer.extra_rounds(db, t) == 0 and "MAXIMIZADOR" not in goals.task_context(db, t)
    assert task(client, env, tid)["status"] == "done"
