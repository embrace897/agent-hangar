# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Primeiros passos, avisos da empresa e o "ao vivo": cada um mostra só o que a pessoa pode ver."""
from datetime import timedelta

from app.db import SessionLocal
from app.models import Announcement, now

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, rt, work  # noqa: F401


def test_onboarding_for_the_admin_and_for_a_person(client, env):
    adm = client.get("/api/onboarding", headers=ADMIN).json()
    ids = [s["id"] for s in adm["steps"]]
    assert ids[:2] == ["llm", "team"] and "employee" in ids and "channel" in ids and adm["total"] == len(ids)
    assert next(s for s in adm["steps"] if s["id"] == "team")["done"]  # os testes já criaram times
    me = client.get("/api/onboarding", headers=env["h"]["req"]).json()
    assert [s["id"] for s in me["steps"]][:2] == ["team", "agent"] and me["steps"][0]["done"]
    assert not me["hidden"]
    assert client.put("/api/onboarding", headers=env["h"]["req"], json={"hidden": True}).json()["hidden"] is True
    assert client.get("/api/onboarding", headers=env["h"]["req"]).json()["hidden"] is True
    client.put("/api/onboarding", headers=env["h"]["req"], json={"hidden": False})


def test_announcements_only_admins_publish_and_expired_ones_disappear(client, env):
    assert client.post("/api/announcements", headers=env["h"]["mgr"], json={"text": "oi"}).status_code == 403
    assert client.post("/api/announcements", headers=ADMIN, json={"text": " "}).status_code == 400
    a = client.post("/api/announcements", headers=ADMIN, json={"text": f"Manutenção sábado {env['n']}", "level": "warn",
                                                               "days": 2}).json()
    assert a["level"] == "warn" and a["expires_at"]
    seen = client.get("/api/announcements", headers=env["h"]["req"]).json()
    assert any(x["id"] == a["id"] for x in seen)
    with SessionLocal() as db:
        db.get(Announcement, a["id"]).expires_at = now() - timedelta(minutes=1)
        db.commit()
    assert all(x["id"] != a["id"] for x in client.get("/api/announcements", headers=env["h"]["req"]).json())
    assert any(x["id"] == a["id"] for x in client.get("/api/announcements?all=true", headers=ADMIN).json())
    assert client.delete(f"/api/announcements/{a['id']}", headers=env["h"]["req"]).status_code == 403
    assert client.delete(f"/api/announcements/{a['id']}", headers=ADMIN).json() == {"deleted": a["id"]}


def test_live_shows_only_tasks_the_person_can_read(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    before = client.get("/api/live", headers=env["h"]["req"]).json()["last"]
    tid = assign(client, env, slug, "resuma o pipeline")
    work()
    mine = client.get("/api/live", headers=env["h"]["req"], params={"since": before}).json()
    kinds = {e["kind"] for e in mine["events"] if e["task"] == tid}
    assert {"created", "run", "done"} <= kinds and mine["last"] > before
    assert all(e["employee"] == slug for e in mine["events"] if e["task"] == tid)
    out = client.get("/api/live", headers=env["h"]["out"], params={"since": before}).json()
    assert all(e["task"] != tid for e in out["events"])  # de outro time: não vê
    again = client.get("/api/live", headers=env["h"]["req"], params={"since": mine["last"]}).json()
    assert all(e["id"] > mine["last"] for e in again["events"])
