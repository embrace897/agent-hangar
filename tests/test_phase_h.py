# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Fase H: memorando de decisão (revisor crítico e resultado), vigias, modo par e trilhas, valor (ROI e oportunidades)
e tarefas de ouro com regressão depois de uma mudança no cargo."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.db import SessionLocal
from app.models import (
    Agent,
    DecisionMemo,
    Employee,
    EmployeeTask,
    HumanRequest,
    RegressionRun,
    Team,
    User,
    Watcher,
    WorkItem,
    WorkItemFile,
)
from app.services import decision_memos, knowledge, regression, watchers
from app.services import employee_tasks as tasksmod

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, rt, task, work  # noqa: F401


@pytest.fixture(autouse=True)
def _close_leftover_tasks():
    with SessionLocal() as db:
        start = db.query(EmployeeTask.id).order_by(EmployeeTask.id.desc()).limit(1).scalar() or 0
    yield
    with SessionLocal() as db:
        for t in db.query(EmployeeTask).filter(EmployeeTask.id > start, EmployeeTask.status.notin_(tasksmod.TERMINAL)):
            t.status = "cancelled"
        db.commit()


def _agent(db, slug):
    return db.query(Agent).filter_by(slug=slug).one()


def _gate(slug, tid, name, args):
    with SessionLocal() as db:
        return tasksmod.gate(db, _agent(db, slug), tid, name, f"platform:{name}", args, "", "platform")


MEMO = {"question": "Renovar a ACME com 10% de desconto ou deixar ir?",
        "options": [{"name": "Renovar com desconto", "pros": ["mantém a receita"], "cons": ["margem menor"]},
                    {"name": "Deixar ir", "pros": ["libera o time"], "cons": ["perde R$ 200 mil/ano"]}],
        "criteria": [{"name": "receita", "weight": 3}, {"name": "margem", "weight": 1}],
        "scores": {"Renovar com desconto": {"receita": 9, "margem": 5}, "Deixar ir": {"receita": 2, "margem": 8}},
        "confidence": 0.7, "citations": ["[1] Contrato ACME 2025"]}


# ------------------------------------------------------------------ memorando de decisão
def test_decision_memo_critic_decision_and_outcome(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    assert client.put(f"/api/employees/{slug}/critic", headers=env["h"]["own"], json={"mode": "auto"}).json()["critic_mode"] == "auto"
    bad = {**MEMO, "recommendation": "Deixar ir", "risks": []}  # contraria as notas e sem riscos: furo grave
    tid = assign(client, env, slug, f"use submit_decision_memo {json.dumps(bad)}")
    work()
    t = task(client, env, tid)
    assert "REVISOR CRÍTICO" in t["result"] and "maior nota ponderada" in t["result"]
    good = {**MEMO, "recommendation": "Renovar com desconto", "risks": ["a ACME pedir mais desconto no ano que vem"]}
    g = _gate(slug, tid, "submit_decision_memo", good)
    out = json.loads(g["result"])
    assert out["totals"] == {"Renovar com desconto": 8.0, "Deixar ir": 3.5} and out["critic"]["severity"] in ("none", "low")
    mid = out["memo"]
    assert client.post(f"/api/memos/{mid}/decide", headers=env["h"]["out"], json={"option": "Deixar ir"}).status_code == 400  # não lê
    d = client.post(f"/api/memos/{mid}/decide", headers=env["h"]["req"], json={"option": "Renovar com desconto", "follow_up_days": 30})
    assert d.status_code == 200 and d.json()["follow_up_at"]
    with SessionLocal() as db:
        m = db.get(DecisionMemo, mid)
        assert decision_memos.sweep(db, tasksmod._aware(m.follow_up_at) + timedelta(minutes=1)) == 1
        item = db.get(WorkItem, db.get(DecisionMemo, mid).follow_up_item_id)
        assert item.title.startswith("Como foi a decisão") and item.assignee_user_id
    o = client.post(f"/api/memos/{mid}/outcome", headers=env["h"]["req"], json={"outcome": "good", "note": "renovou e pagou em dia"}).json()
    assert o["outcome"] == "good"
    with SessionLocal() as db:
        assert db.get(WorkItem, item.id).status == "done"
    rec = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()["decision_record"]
    assert rec["followed"] == 1 and rec["accuracy"] == 1.0
    assert any(x["id"] == mid for x in client.get("/api/memos", headers=env["h"]["req"]).json())


# ------------------------------------------------------------------ vigias
class FakeSource:
    def __init__(self):
        self.text, self.price = "<html><body><p>Resolução 1/2026: prazo de 30 dias.</p></body></html>", 90

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        if url.endswith(".json"):
            return httpx.Response(200, json={"data": [{"price": self.price}]}, request=httpx.Request("GET", url))
        return httpx.Response(200, text=self.text, headers={"content-type": "text/html"}, request=httpx.Request("GET", url))


def test_watchers_fire_on_change_and_on_threshold_transition(client, env, rt, monkeypatch):
    src = FakeSource()
    monkeypatch.setattr(watchers, "HTTP", lambda: src)
    monkeypatch.setattr(knowledge, "_check_url", lambda u: u)
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    base = {"team": env["team"], "employee": slug, "schedule": "0 * * * *"}
    assert client.post("/api/watchers", headers=env["h"]["own"], json={**base, "name": "x", "schedule": "* * * * *",
                                                                         "source": {"type": "url", "url": "https://reg.test/a"}}).status_code == 400
    w = client.post("/api/watchers", headers=env["h"]["own"], json={**base, "name": "Resoluções",
        "source": {"type": "url", "url": "https://reg.test/resolucoes"}, "condition": {"type": "changed"},
        "action": {"type": "task", "title": "Analisar mudança regulatória"}}).json()
    with SessionLocal() as db:
        assert watchers.check(db, db.get(Watcher, w["id"])) is False  # a primeira olhada só registra a base
        assert watchers.check(db, db.get(Watcher, w["id"])) is False  # nada mudou
        src.text = src.text.replace("30 dias", "45 dias")
        assert watchers.check(db, db.get(Watcher, w["id"])) is True
        t = db.query(EmployeeTask).filter_by(source="watcher").order_by(EmployeeTask.id.desc()).first()
        assert t.title == "Analisar mudança regulatória" and "-Resolução 1/2026: prazo de 30 dias." in t.body and "45 dias" in t.body
    p = client.post("/api/watchers", headers=env["h"]["own"], json={**base, "name": "Preço do concorrente",
        "source": {"type": "json", "url": "https://loja.test/p.json", "path": "data.0.price"},
        "condition": {"type": "threshold", "op": ">", "value": 100}, "action": {"type": "log"}}).json()
    fired = []
    with SessionLocal() as db:
        for price in (90, 120, 130, 80, 110):
            src.price = price
            fired.append(watchers.check(db, db.get(Watcher, p["id"])))
    assert fired == [False, True, False, False, True]  # dispara na virada, não a cada olhada
    lst = {x["name"]: x for x in client.get("/api/watchers", headers=env["h"]["own"]).json()}
    assert lst["Preço do concorrente"]["fired"] == 2 and lst["Resoluções"]["last_change"]
    assert client.get("/api/watchers", headers=env["h"]["out"]).json() == [] or all(
        x["team"] != env["team"] for x in client.get("/api/watchers", headers=env["h"]["out"]).json())


# ------------------------------------------------------------------ modo par e trilhas
def test_pair_mode_review_request_and_learning_track(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    r = client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"], json={
        "title": "me ajude", "body": 'use send_email {"to": "x@cliente.com", "body": "oi"}', "mode": "copilot"})
    tid = r.json()["id"]
    assert r.json()["mode"] == "copilot"
    work()
    t = task(client, env, tid)
    assert t["result"].startswith("negado: MODO PAR") and not any(e["kind"] == "human_request" for e in t["events"])
    with SessionLocal() as db:
        team = db.query(Team).filter_by(slug=env["team"]).one()
        u = db.query(User).filter_by(email=env["em"]["mgr"]).one()
        i = WorkItem(team_id=team.id, title="Conciliação de outubro", body="conciliar o extrato", status="in_progress",
                     assignee_user_id=u.id, result="fiz a conciliação, faltou a conta 3")
        db.add(i)
        db.flush()
        db.add(WorkItemFile(item_id=i.id, name="conciliacao.csv", data=b"a,b", size=3, content_type="text/csv"))
        db.commit()
        iid = i.id
    rr = client.post(f"/api/work-items/{iid}/review-request", headers=env["h"]["mgr"], json={"employee": slug}).json()
    t = task(client, env, rr["task"])
    assert t["mode"] == "copilot" and "faltou a conta 3" in t["body"]
    # trilha tirada de um playbook
    steps = [{"key": "extrair", "title": "Extrair", "assignee": {"type": "employee", "ref": slug}, "due": {"business_days": 1}},
             {"key": "conferir", "title": "Conferir", "assignee": {"type": "person", "ref": env["em"]["mgr"]},
              "depends_on": ["extrair"], "due": {"from": "extrair", "business_days": 2}, "evidence": "file"}]
    client.put("/api/playbooks", headers=env["h"]["own"], json={"name": f"fech_{env['n']}", "team": env["team"], "steps": steps})
    tr = client.post("/api/learning", headers=env["h"]["own"], json={"source": f"playbook:fech_{env['n']}", "team": env["team"]})
    assert tr.status_code == 200, tr.text
    tr = tr.json()
    assert tr["count"] >= 6 and "answer" not in tr["questions"][0]  # a resposta não vai para a tela
    with SessionLocal() as db:
        from app.models import LearningTrack
        right = [q["answer"] for q in db.get(LearningTrack, tr["id"]).questions]
    a = client.post(f"/api/learning/{tr['id']}/answer", headers=env["h"]["req"], json={"answers": right}).json()
    assert a["score"] == 1.0
    wrong = client.post(f"/api/learning/{tr['id']}/answer", headers=env["h"]["mgr"], json={"answers": [(x + 1) % 2 for x in right]}).json()
    assert wrong["score"] < 1.0 and wrong["results"][0]["explain"]
    res = {r["user"]: r for r in client.get(f"/api/learning/{tr['id']}/results", headers=env["h"]["mgr"]).json()}
    assert res[env["em"]["req"]]["best"] == 1.0 and res[env["em"]["bk"]]["attempts"] == 0
    assert client.get(f"/api/learning/{tr['id']}/results", headers=env["h"]["req"]).status_code == 403


# ------------------------------------------------------------------ valor e oportunidades
def test_roi_with_baselines_and_opportunities(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    assert client.put("/api/value/baselines", headers=env["h"]["mgr"], json={"items": []}).status_code == 403
    client.put("/api/value/baselines", headers=ADMIN, json={"items": [{"scope": "default", "minutes": 30, "hourly_cost": 60},
                                                                       {"scope": f"employee:{slug}", "minutes": 45}]})
    for i in range(2):
        assign(client, env, slug, f"tarefa {i}")
    work()
    roi = client.get("/api/value/roi", headers=env["h"]["mgr"], params={"team": env["team"]}).json()
    row = next(r for r in roi["employees"] if r["employee"] == slug)
    assert row["done"] == 2 and row["hours_saved"] == 1.5 and row["human_cost"] == 90.0 and row["net"] == round(90 - row["llm_cost"], 2)
    with SessionLocal() as db:
        team = db.query(Team).filter_by(slug=env["team"]).one()
        start = datetime.now(UTC) - timedelta(hours=2)
        for t in ("Segunda via de boleto 123", "segunda via do boleto 456", "Boleto: segunda via 789", "Alterar endereço"):
            db.add(WorkItem(team_id=team.id, title=t, status="done", handled_by="human", started_at=start,
                            done_at=start + timedelta(minutes=20)))
        db.commit()
    ops = client.get("/api/value/opportunities", headers=env["h"]["mgr"]).json()
    top = next(o for o in ops if "boleto" in o["label"])
    assert top["count"] >= 3 and top["hours"] >= 1.0 and "Digital employee" in top["suggestion"]
    rep = client.get("/api/value/report", headers=env["h"]["mgr"])
    assert rep.status_code == 200 and "Horas poupadas" in rep.text and "Oportunidades" in rep.text
    client.put("/api/value/baselines", headers=ADMIN, json={"items": [{"scope": "default", "remove": True},
                                                                       {"scope": f"employee:{slug}", "remove": True}]})


# ------------------------------------------------------------------ tarefas de ouro e regressão
def test_golden_tasks_regression_after_a_job_change(client, env, rt, monkeypatch):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    g = client.post(f"/api/employees/{slug}/golden", headers=env["h"]["own"], json={
        "title": "Lookup", "body": 'use crm_lookup {"id": 7}', "expected": "feito crm_lookup"}).json()
    client.post(f"/api/employees/{slug}/golden", headers=env["h"]["own"], json={
        "title": "Aviso ao cliente", "body": 'use send_email {"to": "a@b.com", "body": "oi"}', "expected": "simulado send_email"})
    run = client.post(f"/api/employees/{slug}/golden/run", headers=env["h"]["own"]).json()
    assert len(run["results"]) == 2
    work()
    v = client.get(f"/api/employees/{slug}/golden", headers=env["h"]["own"]).json()
    assert v["runs"][0]["status"] == "passed" and [x["score"] for x in v["runs"][0]["results"]] == [5, 5]
    with SessionLocal() as db:  # o e-mail da tarefa de ouro foi simulado, sem pedido de decisão
        assert db.query(HumanRequest).filter(HumanRequest.task_id.in_([x["task"] for x in run["results"]])).count() == 0
    assert g["expected"] == "feito crm_lookup"
    # mudou o cargo: a regressão roda sozinha; piorou, o gestor decide voltar
    r = client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"mission": "Missão nova e diferente."})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        e = db.query(Employee).filter_by(agent_id=_agent(db, slug).id).one()
        cur = db.query(RegressionRun).filter_by(employee_id=e.id, status="running").one()
        assert "versão" in cur.trigger
    monkeypatch.setattr(regression, "score", lambda db, e, t: 2)
    work()
    with SessionLocal() as db:
        cur = db.get(RegressionRun, cur.id)
        assert cur.status == "worse" and cur.request_id
        req = db.get(HumanRequest, cur.request_id)
        assert req.kind == "regression" and "5 → 2" in req.question
    d = client.post(f"/api/decisions/{cur.request_id}", headers=env["h"]["mgr"], json={"decision": "revert"})
    assert d.status_code == 200, d.text
    assert client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()["mission"] != "Missão nova e diferente."
    with SessionLocal() as db:
        assert db.get(RegressionRun, cur.id).status == "reverted"
        assert db.query(RegressionRun).filter_by(employee_id=e.id, status="running").count() == 0  # restaurar não roda de novo


def test_probation_stage_tests_carry_title_and_body(client, env):
    """O teste em stage da experiência manda o título e o pedido, como uma tarefa de verdade (a pergunta pode estar no título)."""
    slug = hire(client, env, probation_tasks=[{"title": "Quanto é 2 + 2?", "body": "Responda só o número.", "expected": "4"},
                                              {"title": "Tarefa 1", "body": "faça a tarefa 1", "expected": "resultado 1"},
                                              {"title": "Tarefa 2", "body": "", "expected": "resultado 2"}])
    from app.models import AgentVersion
    with SessionLocal() as db:
        spec = db.query(AgentVersion).filter_by(agent_id=_agent(db, slug).id).order_by(AgentVersion.id.desc()).first().spec
    cases = {t["name"]: t["input"] for t in spec["tests"] if t["name"].startswith("experiência")}
    assert cases["experiência 1"] == "Quanto é 2 + 2?\n\nResponda só o número." and cases["experiência 3"] == "Tarefa 2"


def test_memo_recommendation_matches_the_option_as_the_model_writes_it():
    names = ["A) Renovar com 10% de desconto", "B) Deixar o contrato acabar", "C) Renovar negociando a margem"]
    m = decision_memos._match_option
    assert m("C", names) == names[2] and m("b)", names) == names[1] and m("(a)", names) == names[0]
    assert m("C) Renovar negociando a margem", names) == names[2] and m("b) deixar o contrato", names) == names[1]
    assert m("Renovar", names) is None and m("", names) is None and m("D", names) is None
