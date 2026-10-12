# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Workspace do membro: acessos (aparelho, método, último uso) que o membro e o admin encerram, política de acesso do
workspace, código de ativação (a "chave virtual"), agentes pessoais (invisíveis para o time, com as regras do workspace)
e a promoção para o time com quatro olhos."""
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Agent, UsageEvent, now
from app.services import budgets, workspace

from .conftest import ADMIN
from .test_employees import _fields, env, hire, rt  # noqa: F401
from .test_phase_g import _close_leftover_tasks  # noqa: F401

CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:131.0) Gecko/20100101 Firefox/131.0"


def _member(client, team, role="developer"):
    n = uuid.uuid4().hex[:6]
    email, pw = f"m-{n}@acme.com", f"pw-{uuid.uuid4().hex}"
    u = client.post("/api/users", headers=ADMIN, json={"email": email, "name": f"Membro {n}", "username": f"m-{n}", "password": pw})
    assert u.status_code == 200, u.text
    client.post(f"/api/teams/{team}/members", headers=ADMIN, json={"email": email, "role": role})
    return email, f"m-{n}", pw, u.json()["id"]


def _browser(ua):
    c = TestClient(app, headers={"user-agent": ua})
    return c


def _login(c, username, pw):
    r = c.post("/api/auth/password", json={"username": username, "password": pw})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = c.cookies.get("hangar_csrf") or ""
    return r


def test_accesses_policy_and_activation_code(client, env):
    email, user, pw, uid = _member(client, env["team"])
    a, b = _browser(CHROME_WIN), _browser(FIREFOX_MAC)
    assert _login(a, user, pw).status_code == 200 and _login(b, user, pw).status_code == 200
    mine = a.get("/api/me/sessions").json()
    me = next(s for s in mine if s["current"])
    assert me["device"] == "Chrome · Windows" and me["method"] == "password"
    assert any(s["device"] == "Firefox · macOS" and not s["current"] for s in mine)
    assert a.post("/api/me/sessions/revoke-others").json()["revoked"] == 1
    assert b.get("/api/me").status_code == 401 and a.get("/api/me").status_code == 200  # o Firefox saiu, o Chrome segue
    # o membro gera um código para outro navegador; vale uma vez só
    code = a.post("/api/me/activation-code").json()["code"]
    c = _browser(FIREFOX_MAC)
    assert c.post("/api/auth/activate", json={"code": code.lower().replace("-", "")}).status_code == 200  # tolera minúscula
    assert c.get("/api/me").json()["user"]["email"] == email
    assert _browser(CHROME_WIN).post("/api/auth/activate", json={"code": code}).status_code == 401
    assert next(s for s in c.get("/api/me/sessions").json() if s["current"])["method"] == "activation"
    # o admin gera para um membro; um membro não gera para outra pessoa
    assert a.post("/api/workspace/activation-codes", json={"email": env["em"]["mgr"]}).status_code == 403
    code2 = client.post("/api/workspace/activation-codes", headers=ADMIN, json={"email": email}).json()["code"]
    # política: sem senha e sem código, só SSO e token
    r = client.put("/api/workspace/policy", headers=ADMIN, json={"login": {"password": False, "activation": False}})
    assert r.status_code == 200 and r.json()["login"]["sso"] is True
    try:
        r = _login(_browser(CHROME_WIN), user, pw)
        assert r.status_code == 400 and "senha" in r.text
        assert _browser(CHROME_WIN).post("/api/auth/activate", json={"code": code2}).status_code == 403
        assert a.post("/api/me/activation-code").status_code == 400
        assert client.put("/api/workspace/policy", headers=ADMIN, json={"login": {m: False for m in workspace.METHODS}}).status_code == 400
        assert a.put("/api/workspace/policy", json={"login": {"password": True}}).status_code == 403
    finally:
        client.put("/api/workspace/policy", headers=ADMIN, json={"login": {"password": True, "activation": True}})
    # o admin vê os acessos do workspace e encerra os de um membro
    ss = client.get("/api/workspace/sessions", headers=ADMIN, params={"user_id": uid}).json()
    assert ss and all(s["user"] == email for s in ss)
    assert a.get("/api/workspace/sessions").status_code == 403
    assert client.post(f"/api/workspace/users/{uid}/sessions/revoke", headers=ADMIN).json()["revoked"] >= 2
    assert a.get("/api/me").status_code == 401 and c.get("/api/me").status_code == 401


def test_personal_agents_rules_privacy_promotion_and_budget(client, env, rt):
    owner = env["h"]["req"]  # developer do time
    body = {**_fields(env), "personal": True}
    body.pop("manager", None)  # pessoal: o gestor é o próprio dono
    client.put("/api/workspace/policy", headers=ADMIN, json={"personal_agents": {"enabled": True, "max_per_member": 1,
                                                                                 "monthly_budget_usd": 0}})
    try:
        r = client.post("/api/employees", headers=owner, json=body)
        assert r.status_code == 200 and r.json()["created"], r.text
        slug = r.json()["employee"]["slug"]
        assert r.json()["employee"]["personal"] is True and env["em"]["req"].split("@")[0] in r.json()["employee"]["manager"]
        # o time não vê; o dono e o admin veem
        assert client.get(f"/api/employees/{slug}", headers=env["h"]["mgr"]).status_code == 403
        assert all(x["slug"] != slug for x in client.get("/api/employees", headers=env["h"]["mgr"]).json())
        assert client.get(f"/api/employees/{slug}", headers=owner).status_code == 200
        assert client.get(f"/api/employees/{slug}", headers=ADMIN).status_code == 200
        home = client.get("/api/me/workspace", headers=owner).json()
        assert any(x["slug"] == slug and x["personal"] for x in home["agents"]) and home["personal_count"] == 1
        # limite por membro e conexões de LLM liberadas
        again = client.post("/api/employees", headers=owner, json={**body, "name": "Outro"})
        assert again.status_code == 400 and "limite de 1" in again.text
        client.put("/api/workspace/policy", headers=ADMIN, json={"personal_agents": {"max_per_member": 5, "llm_connections": ["corp"]}})
        bad = client.post("/api/employees", headers=owner, json={**body, "name": "Outro"})
        assert bad.status_code == 400 and "corp" in bad.text
        # orçamento por pessoa
        client.put("/api/workspace/policy", headers=ADMIN, json={"personal_agents": {"monthly_budget_usd": 0.5, "llm_connections": []}})
        with SessionLocal() as db:
            a = db.query(Agent).filter_by(slug=slug).one()
            db.add(UsageEvent(agent_id=a.id, env="prod", channel="employee", protocol="openai", latency_ms=1, ok=True,
                              cost_usd=0.6, created_at=now()))
            db.commit()
            budgets._cache.clear()
            stop = budgets.stopped(db, a)
            assert stop and stop["scope"] == "agentes pessoais do membro" and stop["amount_usd"] == 0.5
        # promoção: o dono pede, outro mantenedor aprova
        assert client.post(f"/api/employees/{slug}/promote-to-team", headers=env["h"]["mgr"], json={}).status_code == 403
        req = client.post(f"/api/employees/{slug}/promote-to-team", headers=owner, json={"note": "o time todo usa"}).json()["request"]
        assert client.post(f"/api/decisions/{req}", headers=owner, json={"decision": "approve"}).status_code in (403, 404)
        d = client.post(f"/api/decisions/{req}", headers=env["h"]["mgr"], json={"decision": "approve"})
        assert d.status_code == 200, d.text
        assert client.get(f"/api/employees/{slug}", headers=env["h"]["mgr"]).json()["personal"] is False
    finally:
        client.put("/api/workspace/policy", headers=ADMIN, json={"personal_agents": {"max_per_member": 5, "monthly_budget_usd": 0,
                                                                                     "llm_connections": []}})


def test_personal_agents_off_or_only_developers(client, env):
    body = {**_fields(env), "personal": True}
    body.pop("manager", None)
    client.put("/api/workspace/policy", headers=ADMIN, json={"personal_agents": {"enabled": False}})
    try:
        r = client.post("/api/employees", headers=env["h"]["req"], json=body)
        assert r.status_code == 403 and "não permite agentes pessoais" in r.text
    finally:
        client.put("/api/workspace/policy", headers=ADMIN, json={"personal_agents": {"enabled": True}})
    assert workspace.device("") == "Navegador" and workspace.device(CHROME_WIN) == "Chrome · Windows"


def test_org_chart_cards_and_reports_to_proposal(client, env):
    chief, sub = hire(client, env, name="Chefe"), hire(client, env, name="Sub")
    nodes = {n["slug"]: n for n in client.get("/api/org-chart", headers=env["h"]["own"]).json()["nodes"]}
    card = nodes[sub]
    assert {"model", "harness", "specialists", "personal", "working_on"} <= set(card) and card["can_manage"] is True
    assert card["personal"] is False and card["working_on"] is None
    # quem só usa os agentes do time não muda o organograma: propõe, e quem gerencia decide
    _, user, pw, _ = _member(client, env["team"], role="consumer")
    c = _browser(CHROME_WIN)
    assert _login(c, user, pw).status_code == 200
    mine = {n["slug"]: n for n in c.get("/api/org-chart").json()["nodes"]}
    assert mine[sub]["can_manage"] is False
    assert c.patch(f"/api/employees/{sub}", json={"reports_to": chief}).status_code == 403
    r = c.post(f"/api/employees/{sub}/propose-reports-to", json={"to": chief, "why": "ele já revisa o trabalho"})
    assert r.status_code == 200, r.text
    assert c.post(f"/api/employees/{sub}/propose-reports-to", json={"to": chief}).status_code == 400  # já proposta
    req = r.json()["request"]
    d = next(x for x in client.get("/api/decisions", headers=env["h"]["mgr"]).json() if x["id"] == req)
    assert d["kind"] == "org_change"
    assert client.post(f"/api/decisions/{req}", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 200
    assert client.get(f"/api/employees/{sub}", headers=env["h"]["own"]).json()["reports_to"] == chief
