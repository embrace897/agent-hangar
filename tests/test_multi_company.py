# ruff: noqa: F811  (env é fixture importada de test_employees)
"""Várias empresas numa instalação: cada empresa só vê o que é dela (inclusive por id), quem administra a instalação
escolhe a empresa pelo X-Hangar-Org, nomes podem repetir entre empresas (slugs não), uma empresa suspensa não entra, o
SSO coloca cada pessoa na empresa do domínio e o trabalho em segundo plano fica na empresa certa."""
import hashlib
import hmac
import json
import time
import uuid

from app import auth, tenancy
from app.db import SessionLocal
from app.models import Agent, EmployeeTask, Organization, TaskEvent, User
from app.services import notify
from app.services.orgs import org_for_email

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, rt  # noqa: F401


def _new_org(client, n):
    pw = f"pw-{uuid.uuid4().hex}"
    r = client.post("/api/orgs", headers=ADMIN, json={"name": f"Beta {n}", "admin_email": f"boss-{n}@beta.example",
                                                     "admin_username": f"boss-{n}", "admin_password": pw,
                                                     "domains": [f"beta-{n}.example"]})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        u = db.scalar(tenancy.unscoped(__import__("sqlalchemy").select(User).where(User.email == f"boss-{n}@beta.example")))
        h = {"Authorization": f"Bearer {auth.create_session(db, u)}"}
    return r.json(), h


def test_each_company_sees_only_its_own_data(client, env, rt):
    n = env["n"]
    a_slug = hire(client, env, name=f"Alfa {n}")
    make_active(client, env, a_slug, rt)
    a_task = assign(client, env, a_slug, "tarefa da empresa A")
    org, bh = _new_org(client, n)
    assert org["users"] == 1 and org["teams"] == 1 and org["slug"]
    # a empresa B não vê nada da A: listas, detalhes e por id
    assert all(x["slug"] != a_slug for x in client.get("/api/agents", headers=bh).json())
    assert [t["slug"] for t in client.get("/api/teams", headers=bh).json()] == [org["team"]]
    assert client.get(f"/api/tasks/{a_task}", headers=bh).status_code in (400, 403, 404)  # para a B, não existe
    assert client.get(f"/api/agents/{a_slug}", headers=bh).status_code in (400, 404)
    assert client.get("/api/search", headers=bh, params={"q": "empresa A"}).json()["items"] == []
    me = client.get("/api/me", headers=bh).json()
    assert me["org"]["id"] == org["id"] and not me.get("instance_admin")
    # a B cria o próprio agente; a A não o vê, o admin da instalação vê escolhendo a empresa
    b = client.post("/api/agents", headers=bh, json={"name": f"Beta bot {n}", "objective": "o", "final_output": "f"})
    assert b.status_code == 200, b.text
    b_slug = b.json()["slug"]
    assert all(x["slug"] != b_slug for x in client.get("/api/agents", headers=env["h"]["own"]).json())
    assert all(x["slug"] != b_slug for x in client.get("/api/agents", headers=ADMIN).json())
    assert any(x["slug"] == b_slug for x in client.get("/api/agents", headers={**ADMIN, "X-Hangar-Org": str(org["id"])}).json())
    assert client.get("/api/agents", headers={**bh, "X-Hangar-Org": "1"}).json() == client.get("/api/agents", headers=bh).json()
    with SessionLocal() as db:
        aid = db.scalar(tenancy.unscoped(__import__("sqlalchemy").select(Agent.id).where(Agent.slug == a_slug)))
        with tenancy.using(org["id"]):
            assert db.get(Agent, aid) is None  # o filtro vale também para db.get por id
        assert db.get(Agent, aid) is not None  # sem contexto (trabalho em segundo plano): vê tudo
    # o mesmo nome de conexão de LLM nas duas empresas; o mesmo slug de time, não
    for h in (ADMIN, {**ADMIN, "X-Hangar-Org": str(org["id"])}):
        r = client.post("/api/catalog/llm", headers=h, json={"name": f"main-{n}", "base_url": "https://llm.example.com/v1",
                                                             "model_name": "m", "protocol": "openai"})
        assert r.status_code == 200, r.text
    r = client.post("/api/teams", headers=bh, json={"name": "Sales", "slug": env["team"]})
    assert r.status_code == 400 and "já existe" in r.json()["detail"]


def test_only_instance_admins_manage_companies_and_suspension_blocks_access(client, env):
    org, bh = _new_org(client, env["n"] + "s")
    assert client.get("/api/orgs", headers=bh).status_code == 403  # admin da B não administra a instalação
    assert client.get("/api/orgs", headers=env["h"]["own"]).status_code == 403
    assert any(o["id"] == org["id"] for o in client.get("/api/orgs", headers=ADMIN).json())
    assert client.patch(f"/api/orgs/{org['id']}", headers=ADMIN, json={"status": "suspended"}).json()["status"] == "suspended"
    assert client.get("/api/me", headers=bh).status_code == 403
    assert client.patch("/api/orgs/1", headers=ADMIN, json={"status": "suspended"}).status_code == 400
    client.patch(f"/api/orgs/{org['id']}", headers=ADMIN, json={"status": "active"})
    assert client.get("/api/me", headers=bh).status_code == 200
    assert client.get("/api/agents", headers={**ADMIN, "X-Hangar-Org": "nao-existe"}).status_code == 404
    dup = client.post("/api/orgs", headers=ADMIN, json={"name": "x", "admin_email": f"boss-{env['n']}s@beta.example"})
    assert dup.status_code == 400  # uma pessoa pertence a uma empresa
    with SessionLocal() as db:
        assert org_for_email(db, f"ana@beta-{env['n']}s.example") == org["id"]
        assert org_for_email(db, "ana@outro.example") == 1


def test_background_work_lands_in_the_right_company(client, env, rt, monkeypatch):
    org, bh = _new_org(client, env["n"] + "b")
    slug = hire(client, env, name=f"Fundo {env['n']}")
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "algo")
    with SessionLocal() as db:  # registro novo sem contexto: herda a empresa do pai (a tarefa)
        t = db.get(EmployeeTask, tid)
        t.org_id = org["id"]
        db.commit()
        db.add(TaskEvent(task_id=tid, kind="note", payload={}))
        db.commit()
        ev = db.scalars(tenancy.unscoped(__import__("sqlalchemy").select(TaskEvent).where(TaskEvent.task_id == tid)
                                         .order_by(TaskEvent.id.desc()))).first()
        assert ev.org_id == org["id"]
        t.org_id = 1
        db.commit()
    # o Slack acha a empresa pela assinatura
    secret = "beta-signing"
    r = client.put("/api/admin/notifications", headers={**ADMIN, "X-Hangar-Org": str(org["id"])},
                   json={"slack_signing_secret": secret})
    assert r.status_code == 200, r.text
    raw = json.dumps({"type": "url_verification", "challenge": "c"}).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    with SessionLocal() as db, tenancy.using(None):
        notify.verify_slack_any(db, raw, {"x-slack-request-timestamp": ts, "x-slack-signature": sig})
        assert tenancy.current() == org["id"]
    with SessionLocal() as db:
        o = db.get(Organization, org["id"])
        o.notify = None
        db.commit()


def test_instance_admin_person_acts_inside_another_company(client, env):
    org, _ = _new_org(client, env["n"] + "i")
    with SessionLocal() as db:
        u = User(email=f"inst-{env['n']}@hangar.test", name="Inst", org_role="admin", active=True, org_id=1)
        db.add(u)
        db.commit()
        h = {"Authorization": f"Bearer {auth.create_session(db, u)}"}
    for extra in ({"X-Hangar-Org": str(org["id"])}, {"Cookie": f"hangar_org={org['id']}"}):
        me = client.get("/api/me", headers={**h, **extra})
        assert me.status_code == 200, me.text  # a própria pessoa (da empresa 1) continua visível
        assert me.json()["org"]["id"] == org["id"] and me.json()["user"]["email"] == f"inst-{env['n']}@hangar.test"
        assert [t["slug"] for t in client.get("/api/teams", headers={**h, **extra}).json()] == [org["team"]]
    # um cookie de empresa que não existe mais não tranca ninguém do lado de fora: volta para a própria
    assert client.get("/api/me", headers={**h, "Cookie": "hangar_org=99999"}).json()["org"]["id"] == 1
    # quem não administra a instalação: o cookie é ignorado
    assert client.get("/api/me", headers={**env["h"]["own"], "Cookie": f"hangar_org={org['id']}"}).json()["org"]["id"] == 1
