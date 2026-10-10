# ruff: noqa: F811  (env, rt e erp são fixtures importadas)
"""Fase C: organograma (repasse para o chefe e para quem reporta), Skill Studio (versões, casos, rodadas, restaurar),
pacotes de time (exportar, prévia, instalar, catálogo), política por ação nos plugins (off/ask/allow), a conta pessoal
de quem pede (OAuth2) e o cofre de segredos (secret://, rotação, propostas com quatro olhos)."""
import json
from datetime import timedelta

from app import auth, crypto
from app.db import SessionLocal
from app.models import Agent, Employee, Plugin, PluginInstall, PluginUserAccount, TaskCheckpoint, User, now
from app.services import employee_tasks as tasksmod
from app.services import employee_teamwork as teamwork
from app.services import packages as pkgmod
from app.services import skill_studio
from app.services.common import iso

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, rt, task, work  # noqa: F401
from .test_plugins import MANIFEST as HTTP_MANIFEST
from .test_plugins import _approved, erp  # noqa: F401


def _emp(slug):
    with SessionLocal() as db:
        a = db.query(Agent).filter_by(slug=slug).one()
        return db.query(Employee).filter_by(agent_id=a.id).one().id, a.id


# ------------------------------------------------------------------ organograma
def test_org_chart_reporting_lines_drive_handoffs_and_the_prompt(client, env, rt):
    chief = hire(client, env, name=f"Chefe {env['n']}")
    analyst = hire(client, env, name=f"Analista {env['n']}")
    r = client.patch(f"/api/employees/{analyst}", headers=env["h"]["own"], json={"reports_to": chief})
    assert r.status_code == 200 and r.json()["reports_to"] == chief
    assert client.patch(f"/api/employees/{chief}", headers=env["h"]["own"], json={"reports_to": analyst}).status_code == 400  # ciclo
    assert client.patch(f"/api/employees/{chief}", headers=env["h"]["own"], json={"reports_to": chief}).status_code == 400
    with SessionLocal() as db:
        from app.services.org_chart import handoff_targets
        a_id, _ = _emp(analyst)
        c_id, _ = _emp(chief)
        assert chief in handoff_targets(db, db.get(Employee, a_id))  # sobe para o chefe
        assert analyst in handoff_targets(db, db.get(Employee, c_id))  # o chefe delega para baixo
    nodes = {n["slug"]: n for n in client.get("/api/org-chart", headers=env["h"]["own"]).json()["nodes"]}
    assert nodes[analyst]["reports_to"] == chief and nodes[chief]["reports_to"] is None
    other_team = client.get("/api/org-chart", headers=env["h"]["own"], params={"team": "nao-existe"}).json()["nodes"]
    assert other_team == []  # o filtro por time
    make_active(client, env, analyst, rt)
    tid = assign(client, env, analyst, "levante os contratos")
    with SessionLocal() as db:
        prompt = db.query(TaskCheckpoint).filter_by(task_id=tid).first().messages[0]["content"]
    assert f"Você reporta a {chief}" in prompt
    revs = client.get(f"/api/employees/{analyst}/revisions", headers=env["h"]["own"]).json()
    assert any("reports_to_id" in r["changed"] for r in revs)
    # o repasse para o chefe passa pela checagem (o chefe precisa estar ativo)
    with SessionLocal() as db:
        t = db.get(tasksmod.EmployeeTask, tid)
        out = teamwork.check_handoff(db, db.get(Employee, a_id), t, {"to": chief, "title": "revise"})
        assert out["status"] == "denied" and "não está ativo" in out["message"]


# ------------------------------------------------------------------ política por ação e testar ação
def test_tool_policy_hides_off_actions_and_ask_forces_approval(client, env, rt, erp):
    name = f"erp-pol-{env['n']}"
    _approved(client, env, name)
    url = f"/api/plugins/{name}/install"
    body = {"team": env["team"], "settings": {"tenant": "acme", "company_id": "42"}, "credential": "erp-secret-123"}
    assert client.put(url, headers=env["h"]["mgr"], json={**body, "tool_policy": {"nope": "off"}}).status_code == 400
    r = client.put(url, headers=env["h"]["mgr"], json={**body, "tool_policy": {"send_invoice": "off", "get_invoice": "ask"}})
    assert r.status_code == 200 and r.json()["tool_policy"] == {"send_invoice": "off", "get_invoice": "ask"}
    # um agente comum: a ação desligada some, e a "ask" exige um Digital employee
    slug = client.post("/api/agents", headers=env["h"]["own"], json={"name": f"Bot {env['n']}", "objective": "o",
                                                                     "final_output": "f"}).json()["slug"]
    client.patch(f"/api/agents/{slug}", headers=env["h"]["own"], json={"plugins": [name]})
    hdr = {"X-Agent-Slug": slug, "Authorization": f"Bearer {auth.agent_token(slug)}"}
    ep = f"/internal/plugins/{name}/mcp"
    tools = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_invoice"]
    off = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                             "params": {"name": "send_invoice", "arguments": {"number": "1"}}}).json()
    assert "desligada" in off["error"]["message"]
    ask = client.post(ep, headers=hdr, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                             "params": {"name": "get_invoice", "arguments": {"number": "1"}}}).json()
    assert ask["result"]["isError"] is True and "exige aprovação" in ask["result"]["content"][0]["text"]
    assert not erp.calls
    # um Digital employee: a alçada diria "sozinho" para ler, mas a política pede aprovação; desligada é negada
    emp = hire(client, env, name=f"Usa ERP {env['n']}")
    make_active(client, env, emp, rt)
    tid = assign(client, env, emp, "consulte a fatura")
    with SessionLocal() as db:
        a = db.query(Agent).filter_by(slug=emp).one()
        g = tasksmod.gate(db, a, tid, "get_invoice", f"mcp:plugin-{name}:get_invoice", {"number": "1"})
        assert g["status"] == "waiting"
        g2 = tasksmod.gate(db, a, tid, "send_invoice", f"mcp:plugin-{name}:send_invoice", {"number": "1"})
        assert g2["status"] == "denied"
    # testar uma ação: o mantenedor, com a credencial do time (vai para a auditoria por chamada)
    assert client.post(f"{url}/try", headers=env["h"]["own"], json={"team": env["team"], "tool": "get_invoice",
                                                                    "args": {"number": "9"}}).status_code == 403
    t = client.post(f"{url}/try", headers=env["h"]["mgr"], json={"team": env["team"], "tool": "get_invoice",
                                                                 "args": {"number": "9"}}).json()
    assert t["ok"] is True and '"total": 1500' in t["result"]
    calls = client.get(f"/api/plugins/{name}/calls", headers=env["h"]["mgr"], params={"team": env["team"]}).json()["items"]
    assert calls[0]["caller"] == f"teste:{env['em']['mgr']}"


# ------------------------------------------------------------------ conta pessoal (OAuth2)
OAUTH_HTTP = HTTP_MANIFEST.replace(
    "auth: {{type: api_key, in: header, name: X-ERP-Key, label: Chave da API do ERP}}",
    "auth: {{type: oauth2, authorize_url: 'https://login.example.com/a', token_url: 'https://login.example.com/t'}}")


def test_requester_identity_uses_the_personal_account_of_whoever_asked(client, env, rt, erp):
    name = f"erp-id-{env['n']}"
    r = client.post("/api/plugins", headers=env["h"]["own"], json={"manifest": OAUTH_HTTP.format(name=name), "team": env["team"]})
    assert r.status_code == 200, r.text
    client.post(f"/api/plugins/{name}/submit", headers=env["h"]["own"])
    client.post(f"/api/plugins/{name}/review", headers=ADMIN, json={"decision": "approve"})
    url = f"/api/plugins/{name}/install"
    body = {"team": env["team"], "settings": {"tenant": "acme", "company_id": "42"}, "oauth_client_id": "cid"}
    assert client.put(url, headers=env["h"]["mgr"], json={**body, "identity_mode": "nope"}).status_code == 400
    assert client.put(url, headers=env["h"]["mgr"], json={**body, "identity_mode": "requester"}).json()["identity_mode"] == "requester"
    exp = iso(now() + timedelta(hours=1))
    with SessionLocal() as db:
        p = db.query(Plugin).filter_by(name=name).one()
        i = db.query(PluginInstall).filter_by(plugin_id=p.id).one()
        sec = json.loads(crypto.decrypt(i.secret))
        sec["oauth"].update(access_token="team-token-1234", expires_at=exp)
        i.secret = crypto.encrypt(json.dumps(sec))
        uid = db.query(User).filter_by(email=env["em"]["req"]).one().id
        db.add(PluginUserAccount(plugin_id=p.id, user_id=uid, secret=crypto.encrypt(json.dumps(
            {"oauth": {"client_id": "cid", "access_token": "ana-personal-9876", "expires_at": exp}})), connected_at=now()))
        db.commit()
    assert client.get(f"/api/plugins/{name}", headers=env["h"]["req"]).json()["my_account"] is True
    emp = hire(client, env, name=f"Pessoal {env['n']}")
    make_active(client, env, emp, rt)
    client.patch(f"/api/agents/{emp}", headers=env["h"]["own"], json={"plugins": [name]})
    hdr = {"X-Agent-Slug": emp, "Authorization": f"Bearer {auth.agent_token(emp)}"}

    def call(task_id):
        return client.post(f"/internal/plugins/{name}/mcp", headers={**hdr, "X-Hangar-Task": str(task_id)}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_invoice", "arguments": {"number": "1"}}}).json()["result"]
    mine = assign(client, env, emp, "consulte", who="req")
    out = call(mine)
    assert out["isError"] is False and erp.calls[-1]["headers"]["Authorization"] == "Bearer ana-personal-9876"
    other = assign(client, env, emp, "consulte", who="own")  # quem pediu não conectou a conta pessoal
    out = call(other)
    assert out["isError"] is True and "conta pessoal" in out["content"][0]["text"]
    client.put(url, headers=env["h"]["mgr"], json={"team": env["team"], "identity_mode": "prefer_requester"})
    assert call(other)["isError"] is False and erp.calls[-1]["headers"]["Authorization"] == "Bearer team-token-1234"
    assert client.delete(f"/api/plugins/{name}/my-account", headers=env["h"]["req"]).status_code == 200
    # conectar a conta pessoal: o state assinado leva quem é a pessoa
    r = client.post(f"/api/plugins/{name}/my-account/connect", headers=env["h"]["req"], json={"team": env["team"]})
    assert r.status_code == 200 and r.json()["authorize_url"].startswith("https://login.example.com/a")
    assert client.post(f"/api/plugins/{name}/my-account/connect", headers=env["h"]["out"], json={"team": env["team"]}).status_code == 403


# ------------------------------------------------------------------ cofre de segredos
def test_vault_secrets_are_referenced_rotated_and_proposed(client, env, erp):
    name = f"erp-vault-{env['n']}"
    _approved(client, env, name)
    key = f"erp-key-{env['n']}".lower()
    assert client.put("/api/secrets", headers=env["h"]["own"], json={"name": key, "value": "v1", "team": env["team"]}).status_code == 403
    s = client.put("/api/secrets", headers=env["h"]["mgr"], json={"name": key, "value": "vault-value-111", "team": env["team"]}).json()
    assert s["version"] == 1 and s["ref"] == f"secret://{key}" and "vault-value" not in json.dumps(s)
    url = f"/api/plugins/{name}/install"
    body = {"team": env["team"], "settings": {"tenant": "acme", "company_id": "42"}}
    assert client.put(url, headers=env["h"]["mgr"], json={**body, "credential": "secret://nao-existe"}).status_code == 400
    assert client.put(url, headers=env["h"]["mgr"], json={**body, "credential": f"secret://{key}"}).status_code == 200
    t = client.post(f"{url}/test", headers=env["h"]["mgr"], json={"team": env["team"]}).json()
    assert t["ok"] is True and erp.calls[-1]["headers"]["X-ERP-Key"] == "vault-value-111"
    client.put("/api/secrets", headers=env["h"]["mgr"], json={"name": key, "value": "vault-value-222", "team": env["team"]})
    client.post(f"{url}/test", headers=env["h"]["mgr"], json={"team": env["team"]})
    assert erp.calls[-1]["headers"]["X-ERP-Key"] == "vault-value-222"  # a rotação vale na hora
    lst = client.get("/api/secrets", headers=env["h"]["mgr"]).json()
    mine = next(x for x in lst["secrets"] if x["name"] == key)
    assert mine["version"] == 2 and mine["uses"] >= 2 and mine["last_used_by"].startswith("plugin:")
    assert "vault-value" not in json.dumps(lst)
    # proposta: quem não administra propõe; quem administra aprova (e quem propôs não aprova)
    p = client.post("/api/secrets/proposals", headers=env["h"]["own"], json={"name": key, "value": "vault-value-333",
                                                                             "team": env["team"], "note": "chave nova"}).json()
    assert client.post(f"/api/secrets/proposals/{p['id']}/decide", headers=env["h"]["own"], json={"approve": True}).status_code == 403
    d = client.post(f"/api/secrets/proposals/{p['id']}/decide", headers=env["h"]["mgr"], json={"approve": True}).json()
    assert d["status"] == "approved" and d["secret"]["version"] == 3
    client.post(f"{url}/test", headers=env["h"]["mgr"], json={"team": env["team"]})
    assert erp.calls[-1]["headers"]["X-ERP-Key"] == "vault-value-333"


# ------------------------------------------------------------------ Skill Studio
def test_skill_studio_versions_cases_runs_and_restore(client, env, monkeypatch):
    name = f"tom-de-voz-{env['n']}"
    r = client.put(f"/api/skills/{name}", headers=env["h"]["own"], json={"description": "Tom de voz", "content": "Seja breve."})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["can_edit"] is True
    client.put(f"/api/skills/{name}", headers=env["h"]["own"], json={"description": "Tom de voz", "content": "Seja breve e cordial.",
                                                                     "note": "mais cordial"})
    assert client.put(f"/api/skills/{name}", headers=env["h"]["out"], json={"content": "x"}).status_code == 403
    assert client.put(f"/api/skills/{name}/cases", headers=env["h"]["own"], json={"cases": [{"name": "x"}]}).status_code == 400
    client.put(f"/api/skills/{name}/cases", headers=env["h"]["own"],
               json={"cases": [{"name": "saudação", "input": "diga oi", "expect_contains": "Olá"}]})
    mock = client.post(f"/api/skills/{name}/test", headers=env["h"]["own"], json={}).json()
    assert mock["mock"] is True and mock["passed"] is True  # sem conexão: rodada mock, conteúdo não avaliado
    client.post("/api/catalog/llm", headers=ADMIN, json={"name": f"llm-{env['n']}", "base_url": "https://llm.example.com/v1",
                                                         "model_name": "m", "protocol": "openai"})
    monkeypatch.setattr(skill_studio, "CALL", lambda base, key, model, system, user: "Olá! " + system[:20])
    run = client.post(f"/api/skills/{name}/test", headers=env["h"]["own"], json={"connection": f"llm-{env['n']}"}).json()
    assert run["passed"] is True and run["version"] == 2 and run["results"][0]["output"].startswith("Olá! Seja breve e cordial")
    monkeypatch.setattr(skill_studio, "CALL", lambda *a: "Oi")
    assert client.post(f"/api/skills/{name}/test", headers=env["h"]["own"], json={"connection": f"llm-{env['n']}"}).json()["passed"] is False
    d = client.get(f"/api/skills/{name}", headers=env["h"]["own"]).json()
    assert [r["version"] for r in d["revisions"]][:2] == [2, 1] and len(d["runs"]) == 3
    back = client.post(f"/api/skills/{name}/restore/1", headers=env["h"]["own"]).json()
    assert back["version"] == 3 and back["content"] == "Seja breve." and back["revisions"][0]["note"] == "restaurada a v1"
    dep = client.post(f"/api/skills/{name}/status", headers=env["h"]["own"], json={"status": "deprecated"}).json()
    assert dep["status"] == "deprecated"
    assert any(s["name"] == name and s["status"] == "deprecated" for s in client.get("/api/skills", headers=env["h"]["out"]).json())


# ------------------------------------------------------------------ pacotes de time
def test_packages_export_preview_install_and_catalog(client, env, rt):
    chief = hire(client, env, name=f"Coord {env['n']}")
    analyst = hire(client, env, name=f"Analyst {env['n']}")
    client.patch(f"/api/employees/{analyst}", headers=env["h"]["own"], json={"reports_to": chief, "review_policy": "manager"})
    goal = client.post("/api/goals", headers=env["h"]["mgr"], json={"title": "Pacote meta", "team": env["team"]}).json()
    client.post("/api/projects", headers=env["h"]["own"], json={"name": f"Projeto pacote {env['n']}", "team": env["team"],
                                                                "goal_id": goal["id"], "default_employee": analyst})
    pkg = client.post("/api/packages/export", headers=env["h"]["own"], json={"team": env["team"], "name": "Meu time"}).json()
    assert pkg["kind"] == "hangar-package" and {e["slug"] for e in pkg["employees"]} >= {chief, analyst}
    a = next(e for e in pkg["employees"] if e["slug"] == analyst)
    assert a["reports_to"] == chief and a["review_policy"] == "manager" and len(a["probation_tasks"]) == 3
    assert client.post("/api/packages/export", headers=env["h"]["out"], json={"team": env["team"]}).status_code == 403
    # prévia e instalação (no mesmo time: os slugs já usados são renomeados); tudo nasce em rascunho
    team = env["team"]
    assert client.post("/api/packages/preview", headers=env["h"]["own"], json={"package": pkg, "team": team,
                                                                            "manager": env["em"]["mgr"]}).status_code == 403
    pv = client.post("/api/packages/preview", headers=env["h"]["mgr"], json={"package": pkg, "team": team,
                                                                            "manager": env["em"]["mgr"]}).json()
    assert pv["ok"] is True and all(x["renamed"] for x in pv["employees"] if x["from"] in (chief, analyst))
    bad = client.post("/api/packages/preview", headers=env["h"]["mgr"], json={"package": pkg, "team": team, "manager": "x@y.z"}).json()
    assert bad["ok"] is False
    out = client.post("/api/packages/install", headers=env["h"]["mgr"], json={"package": pkg, "team": team,
                                                                             "manager": env["em"]["mgr"]}).json()
    assert "created" in out, out
    made = out["created"]["employees"]
    assert len(made) >= 2 and out["created"]["projects"] and out["created"]["goals"] == ["Pacote meta"]
    new_analyst, new_chief = out["slugs"][analyst], out["slugs"][chief]
    assert new_analyst in made and new_analyst != analyst
    d = client.get(f"/api/employees/{new_analyst}", headers=env["h"]["mgr"]).json()
    assert d["status"] == "onboarding" and d["reports_to"] == new_chief and d["review_policy"] == "manager"
    # catálogo de times prontos
    cat = client.get("/api/packages/catalog", headers=env["h"]["mgr"]).json()
    assert any(c["id"] == "renewals-team" and len(c["employees"]) == 2 for c in cat)
    out = client.post("/api/packages/install", headers=env["h"]["mgr"], json={"catalog": "renewals-team", "team": team,
                                                                             "manager": env["em"]["mgr"]}).json()
    assert len(out["created"]["employees"]) == 2 and out["created"]["projects"]
    assert pkgmod._scrub({"headers": {"Authorization": "Bearer x", "X-Api-Key": "k", "Accept": "json"}}) == {
        "headers": {"Authorization": "", "X-Api-Key": "", "Accept": "json"}}
