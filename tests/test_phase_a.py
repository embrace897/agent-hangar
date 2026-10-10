# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Fase A: metas e projetos (o porquê chega ao agente), a conversa da tarefa (fila enquanto ele trabalha, reabrir),
orçamentos com escopo e incidentes (aviso, parada e liberação) e a caixa de entrada com a busca."""
from app.db import SessionLocal
from app.models import Agent, BudgetIncident, TaskCheckpoint, TaskMessage, UsageEvent, now
from app.services import budgets

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, rt, task, work  # noqa: F401


def _active(client, env, rt, name="Analista"):
    slug = hire(client, env, name=f"{name} {env['n']}")
    make_active(client, env, slug, rt)
    return slug


def _prompt(tid) -> str:
    with SessionLocal() as db:
        cp = db.query(TaskCheckpoint).filter_by(task_id=tid).order_by(TaskCheckpoint.seq).first()
        return cp.messages[0]["content"]


def _spend(slug, usd):
    with SessionLocal() as db:
        a = db.query(Agent).filter_by(slug=slug).one()
        db.add(UsageEvent(agent_id=a.id, env="prod", channel="api", cost_usd=usd, created_at=now()))
        db.commit()
    budgets._cache.clear()


# ------------------------------------------------------------------ metas e projetos
def test_goals_and_projects_bring_the_why_into_the_task(client, env, rt):
    slug = _active(client, env, rt)
    company = client.post("/api/goals", headers=ADMIN, json={"title": f"Receita recorrente {env['n']}",
                                                             "description": "dobrar a receita em 2027"}).json()
    assert client.post("/api/goals", headers=env["h"]["mgr"], json={"title": "x"}).status_code == 403  # empresa: só admin
    assert client.post("/api/goals", headers=env["h"]["own"], json={"title": "x", "team": env["team"]}).status_code == 403
    team_goal = client.post("/api/goals", headers=env["h"]["mgr"], json={
        "title": "Zero renovações perdidas", "team": env["team"], "parent_id": company["id"]}).json()
    assert team_goal["can_edit"] is True and team_goal["team"] == env["team"]
    r = client.patch(f"/api/goals/{company['id']}", headers=ADMIN, json={"parent_id": team_goal["id"]})
    assert r.status_code == 400  # ciclo (e meta da empresa sob meta de time)
    p = client.post("/api/projects", headers=env["h"]["own"], json={
        "name": f"Renovações Q4 {env['n']}", "team": env["team"], "goal_id": team_goal["id"],
        "instructions": "Priorize contratos acima de R$ 50 mil.", "default_employee": slug}).json()
    assert p["goal"]["id"] == team_goal["id"] and p["default_employee"] == slug
    assert [g["title"] for g in p["goal_chain"]] == ["Zero renovações perdidas", company["title"]]
    r = client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"],
                    json={"title": "Renovar a ACME", "body": "contrato vence em 30 dias", "project": p["slug"]})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    prompt = _prompt(tid)
    assert "Renovações Q4" in prompt and "acima de R$ 50 mil" in prompt
    assert "Zero renovações perdidas" in prompt and company["title"] in prompt
    t = task(client, env, tid)
    assert t["project"]["slug"] == p["slug"] and [g["title"] for g in t["goals"]][0] == "Zero renovações perdidas"
    d = client.get(f"/api/projects/{p['slug']}", headers=env["h"]["req"]).json()
    assert d["tasks"] == 1 and d["task_list"][0]["id"] == tid
    assert client.get(f"/api/projects/{p['slug']}", headers=env["h"]["out"]).status_code == 403  # outro time
    assert all(x["slug"] != p["slug"] for x in client.get("/api/projects", headers=env["h"]["out"]).json())
    client.patch(f"/api/projects/{p['slug']}", headers=env["h"]["own"], json={"status": "archived"})
    r = client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"], json={"title": "x", "project": p["slug"]})
    assert r.status_code == 400 and "arquivado" in r.json()["detail"]
    assert client.delete(f"/api/goals/{company['id']}", headers=ADMIN).status_code == 400  # tem meta-filha
    goals = client.get("/api/goals", headers=env["h"]["out"]).json()
    assert any(g["id"] == team_goal["id"] and g["can_edit"] is False for g in goals)  # ver é aberto; editar, não


# ------------------------------------------------------------------ a conversa da tarefa
def test_thread_queues_messages_delivers_them_and_reopens_finished_tasks(client, env, rt):
    slug = _active(client, env, rt)
    tid = assign(client, env, slug, "resuma as renovações")
    m = client.post(f"/api/tasks/{tid}/messages", headers=env["h"]["req"], json={"body": "inclua a ACME também"}).json()
    assert m["status"] == "queued"
    extra = client.post(f"/api/tasks/{tid}/messages", headers=env["h"]["req"], json={"body": "rascunho"}).json()
    assert client.patch(f"/api/tasks/{tid}/messages/{extra['id']}", headers=env["h"]["req"],
                        json={"body": "e a Globex"}).json()["body"] == "e a Globex"
    assert client.patch(f"/api/tasks/{tid}/messages/{extra['id']}", headers=env["h"]["mgr"],
                        json={"body": "x"}).status_code == 403  # só quem escreveu
    client.delete(f"/api/tasks/{tid}/messages/{extra['id']}", headers=env["h"]["req"])
    assert client.post(f"/api/tasks/{tid}/messages", headers=env["h"]["out"], json={"body": "oi"}).status_code == 403
    work()
    t = task(client, env, tid)
    assert t["status"] == "done"
    msgs = t["messages"]
    assert [x["status"] for x in msgs] == ["delivered", "agent"] and msgs[0]["body"] == "inclua a ACME também"
    with SessionLocal() as db:
        cp = db.query(TaskCheckpoint).filter_by(task_id=tid).order_by(TaskCheckpoint.seq.desc()).first()
        texts = [x["content"] for x in cp.messages if x["role"] == "user"]
        assert any("inclua a ACME também" in x for x in texts) and not any("Globex" in x for x in texts)
    # acompanhamento numa tarefa concluída: reabre e roda de novo
    r = client.post(f"/api/tasks/{tid}/messages", headers=env["h"]["req"], json={"body": "agora mande para o gestor"}).json()
    assert r["task_status"] == "new"
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and sum(x["agent"] for x in t["messages"]) == 2
    assert any(ev["kind"] == "reopened" for ev in t["events"])


def test_a_message_arriving_mid_round_keeps_the_task_going(client, env, rt, monkeypatch):
    slug = _active(client, env, rt)
    tid = assign(client, env, slug, "levante os contratos")
    orig, sent = rt.post, []

    def post(*a, **k):  # durante a rodada, alguém escreve na tarefa
        if not sent:
            with SessionLocal() as db:
                db.add(TaskMessage(task_id=tid, author=env["em"]["req"], body="só os de 2026", status="queued", created_at=now()))
                db.commit()
            sent.append(1)
        return orig(*a, **k)
    monkeypatch.setattr(rt, "post", post)
    work()
    assert task(client, env, tid)["status"] == "new"  # não encerrou: a mensagem chegou no meio
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and any(x["body"] == "só os de 2026" and x["status"] == "delivered" for x in t["messages"])


# ------------------------------------------------------------------ orçamentos
def test_budget_hard_stop_pauses_tasks_until_released(client, env, rt):
    slug = _active(client, env, rt)
    r = client.post("/api/budgets", headers=env["h"]["own"], json={"scope_type": "team", "scope": env["team"], "amount_usd": 1})
    assert r.status_code == 403  # developer não define orçamento
    pol = client.post("/api/budgets", headers=env["h"]["mgr"], json={
        "scope_type": "agent", "scope": slug, "amount_usd": 1.0, "window": "day", "warn_percent": 50}).json()
    assert pol["state"] == "ok" and pol["can_manage"] is True
    assert client.post("/api/budgets", headers=env["h"]["mgr"], json={
        "scope_type": "agent", "scope": slug, "amount_usd": 2}).status_code == 200  # outra janela (mês) é outro orçamento
    _spend(slug, 0.6)
    with SessionLocal() as db:
        budgets.sweep(db)
    b = client.get("/api/budgets", headers=env["h"]["mgr"]).json()
    mine = next(p for p in b["policies"] if p["id"] == pol["id"])
    assert mine["state"] == "warning" and 60 <= mine["percent"] < 100  # + o custo das tarefas de experiência
    _spend(slug, 0.5)
    tid = assign(client, env, slug, "tarefa cara")
    work()
    t = task(client, env, tid)
    assert t["status"] == "waiting_budget" and any(ev["kind"] == "budget" for ev in t["events"])
    inc = next(i for i in client.get("/api/budgets", headers=env["h"]["mgr"]).json()["incidents"]
               if i["policy_id"] == pol["id"] and i["kind"] == "hard_stop")
    assert inc["status"] == "open" and inc["can_resolve"] is True
    assert client.post(f"/api/budgets/incidents/{inc['id']}/release", headers=env["h"]["own"], json={}).status_code == 403
    r = client.post(f"/api/budgets/incidents/{inc['id']}/release", headers=env["h"]["mgr"], json={"note": "fechamento do mês"})
    assert r.json()["status"] == "resolved"
    assert task(client, env, tid)["status"] == "new"  # voltou para a fila
    work()
    assert task(client, env, tid)["status"] == "done"  # liberado o resto da janela: não para de novo hoje
    with SessionLocal() as db:
        assert db.query(BudgetIncident).filter_by(policy_id=pol["id"], kind="hard_stop").count() == 1


def test_raising_the_budget_resolves_the_stop_and_org_scope_is_admin_only(client, env, rt):
    slug = _active(client, env, rt)
    assert client.post("/api/budgets", headers=env["h"]["mgr"], json={"scope_type": "org", "amount_usd": 5}).status_code == 403
    pol = client.post("/api/budgets", headers=env["h"]["mgr"], json={
        "scope_type": "team", "scope": env["team"], "amount_usd": 0.5, "window": "day"}).json()
    _spend(slug, 0.6)
    with SessionLocal() as db:
        a = db.query(Agent).filter_by(slug=slug).one()
        assert budgets.stopped(db, a)["scope"] == f"time {env['team']}"
    r = client.patch(f"/api/budgets/{pol['id']}", headers=env["h"]["mgr"], json={"amount_usd": 5})
    assert r.json()["state"] == "ok"
    with SessionLocal() as db:
        a = db.query(Agent).filter_by(slug=slug).one()
        assert budgets.stopped(db, a) is None
    client.delete(f"/api/budgets/{pol['id']}", headers=env["h"]["mgr"])


# ------------------------------------------------------------------ caixa de entrada e busca
def test_inbox_shows_what_needs_me_and_search_respects_access(client, env, rt):
    slug = _active(client, env, rt)
    tid = assign(client, env, slug, "prepare a proposta da Initech")
    work()
    box = client.get("/api/inbox", headers=env["h"]["req"]).json()
    item = next(i for i in box["items"] if i["key"] == f"task:{tid}")
    assert item["unread"] is True and item["status"] == "done" and box["counts"]["unread"] >= 1
    task(client, env, tid, who="req")  # abrir marca como lida
    item = next(i for i in client.get("/api/inbox", headers=env["h"]["req"]).json()["items"] if i["key"] == f"task:{tid}")
    assert item["unread"] is False
    client.post("/api/inbox/mark", headers=env["h"]["req"], json={"keys": [f"task:{tid}"], "archived": True})
    assert all(i["key"] != f"task:{tid}" for i in client.get("/api/inbox", headers=env["h"]["req"]).json()["items"])
    assert any(i["key"] == f"task:{tid}" for i in client.get("/api/inbox?view=archived", headers=env["h"]["req"]).json()["items"])
    assert task(client, env, tid)["status"] == "done"  # arquivar não muda a tarefa
    # decisão para o gestor
    tid2 = assign(client, env, slug, 'use send_email {"to": "ana@cliente.com", "body": "Olá"}')
    work()
    mgr = client.get("/api/inbox?view=decisions", headers=env["h"]["mgr"]).json()["items"]
    assert any(i["kind"] == "decision" and i["task_id"] == tid2 for i in mgr)
    # busca
    found = client.get("/api/search", headers=env["h"]["req"], params={"q": "Initech"}).json()["items"]
    assert any(x["kind"] == "task" and x["url"] == f"#/tasks/{tid}" for x in found)
    assert not client.get("/api/search", headers=env["h"]["out"], params={"q": "Initech"}).json()["items"]
    who = client.get("/api/search", headers=env["h"]["req"], params={"q": f"Analista {env['n']}"}).json()["items"]
    assert any(x["kind"] == "employee" and x["url"] == f"#/employees/{slug}" for x in who)
    assert client.get("/api/search", headers=env["h"]["req"], params={"q": "a"}).status_code == 400
