# ruff: noqa: F811  (rt é fixture importada de test_employees)
"""A empresa de exemplo (`hangar demo`) contra o app de verdade: organograma, admissão, projeto, primeira tarefa,
rodar de novo sem duplicar e apagar."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli"))
from app import services as svc  # noqa: E402
from hangar_cli import demo as dm  # noqa: E402
from hangar_cli.main import Api  # noqa: E402

from .conftest import ADMIN  # noqa: E402
from .test_employees import rt, work  # noqa: E402,F401


def _api() -> Api:
    """O Api do CLI falando com o app de verdade (um cliente próprio: o `client` dos outros testes fica intacto)."""
    from fastapi.testclient import TestClient

    from app.main import app
    a = Api("http://testserver", "x")
    a.c = TestClient(app, headers=ADMIN)
    return a


def test_demo_seed_rerun_and_remove(client, fake_docker, rt, monkeypatch):
    monkeypatch.setattr(svc.runtime, "ship", lambda db, slug, actor="admin", **k: [])
    monkeypatch.setattr(dm.time, "sleep", lambda s: work())  # cada espera roda o executor de tarefas
    admin_email = "demo-admin@example.com"
    client.post("/api/users", headers=ADMIN, json={"email": admin_email, "name": "Demo admin", "org_role": "admin"})
    api = _api()
    out = dm.Demo(api, say=lambda *a: None).seed(admin_email)
    emp = out["employees"]
    assert set(emp) == {"coord", "analyst", "writer"}
    nodes = {n["slug"]: n for n in client.get("/api/org-chart", headers=ADMIN).json()["nodes"]}
    assert nodes[emp["analyst"]]["reports_to"] == emp["coord"] and nodes[emp["writer"]]["reports_to"] == emp["coord"]
    assert all(nodes[s]["status"] == "active" and nodes[s]["model"] == "mock/echo" for s in emp.values())
    tasks = client.get(f"/api/employees/{emp['analyst']}/tasks", headers=ADMIN).json()
    assert [t["title"] for t in tasks if t["title"] == dm.FIRST_TASK["title"]]
    assert any(p["name"] == dm.PROJECT for p in client.get("/api/projects", headers=ADMIN).json())
    # rodar de novo não duplica nada
    again = dm.Demo(api, say=lambda *a: None).seed(admin_email)
    assert again["employees"] == emp and again["team"] == out["team"]
    assert sum(1 for t in client.get(f"/api/employees/{emp['analyst']}/tasks", headers=ADMIN).json()
               if t["title"] == dm.FIRST_TASK["title"]) == 1
    gone = dm.Demo(api, say=lambda *a: None).remove()
    assert gone["employees"] == 3 and gone["team"] == out["team"]
    assert all(t["name"] != dm.TEAM for t in client.get("/api/teams", headers=ADMIN).json())
    assert all(p["name"] != dm.PROJECT for p in client.get("/api/projects", headers=ADMIN).json())
