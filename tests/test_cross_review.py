# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Revisão por um modelo de outro fornecedor (issue #56, parte B): o bloco `review` numa regra de alçada. Concordou:
a ação roda (uma vez, a ação exata); discordou, não respondeu ou deu erro: vale on_disagree (falha fecha). Em
observação, quem decide é uma pessoa e a concordância entre os dois vira métrica."""
import re

import pytest
from sqlalchemy import select

from app import services as svc
from app.db import SessionLocal
from app.models import Employee, EmployeeTask, HumanRequest, LlmConnection, UsageEvent
from app.services import cross_review, notify

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401

CRM = 'use update_crm {"id": 7, "email": "ana@cliente.com"}'


def _sync(fn, *args):
    with SessionLocal() as db:
        fn(db, *args)


@pytest.fixture()
def reviewer(monkeypatch, uniq):
    """Conexão do revisor (Google) e as respostas dele, na ordem; cada chamada fica em `calls`."""
    monkeypatch.setattr(notify, "_bg", _sync)
    name = uniq("rev")
    with SessionLocal() as db:
        db.add(LlmConnection(name=name, base_url="http://revisor.test/v1", model_name="google/gemini-2.5-pro",
                             protocol="openai", price_in_per_mtok=1.0, price_out_per_mtok=10.0))
        db.commit()
    state = {"name": name, "answers": [], "calls": []}

    def call(base, key, model, messages, timeout):
        state["calls"].append({"model": model, "messages": messages})
        a = state["answers"].pop(0) if state["answers"] else '{"verdict": "agree", "reason": "ok"}'
        if isinstance(a, Exception):
            raise a
        return {"choices": [{"message": {"content": a}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
    monkeypatch.setattr(cross_review, "CALL", call)
    return state


def _rules(client, env, slug, review, mode="auto"):
    return client.put(f"/api/employees/{slug}/authority", headers=env["h"]["mgr"],
                      json={"rules": [{"action_type": "write_internal", "mode": mode, "review": review}]})


def _reqs(tid, kind=None):
    with SessionLocal() as db:
        q = select(HumanRequest).where(HumanRequest.task_id == tid).order_by(HumanRequest.id)
        return [r for r in db.scalars(q) if kind is None or r.kind == kind]


def test_vendor_comes_from_the_model_not_the_connection():
    v = cross_review.vendor_of
    assert v("anthropic/claude-sonnet-4.5") == v("claude-opus-4") == "anthropic"
    assert v("openrouter/google/gemini-2.5-pro") == v("vertex_ai/gemini-2.0") == v("gemini-2.5-flash") == "google"
    assert v("gpt-4o") == v("openai/o3") == "openai" and v("meta-llama/llama-3.3-70b") == "meta"
    assert v("deepseek/deepseek-chat") == "deepseek" and v("groq/llama-3.1") == "meta" and v("mistralai/foo") == "mistral"
    assert v("acme-private-model") == "" and v("") == ""


def test_rule_validation(client, env, reviewer, monkeypatch):
    slug = hire(client, env)
    bad = _rules(client, env, slug, {"connection": reviewer["name"]}, mode="approve")
    assert bad.status_code == 400 and "auto ou notify" in bad.text
    assert _rules(client, env, slug, {"connection": reviewer["name"], "on_disagree": "auto"}).status_code == 400
    assert "não está no catálogo" in _rules(client, env, slug, {"connection": "nao-existe"}).text
    # o mesmo fornecedor do modelo do funcionário é recusado
    monkeypatch.setattr(cross_review, "employee_model", lambda db, e: "openrouter/google/gemini-2.5-flash")
    same = _rules(client, env, slug, {"connection": reviewer["name"]})
    assert same.status_code == 400 and "outro fornecedor" in same.text
    monkeypatch.setattr(cross_review, "employee_model", lambda db, e: "anthropic/claude-sonnet-4.5")
    ok = _rules(client, env, slug, {"connection": reviewer["name"], "timeout_s": 9999})
    assert ok.status_code == 200, ok.text
    rv = ok.json()["rules"][0]["review"]
    assert rv["shadow"] is True and rv["on_disagree"] == "approve" and rv["timeout_s"] == 300  # observação é o padrão


def test_reviewer_agrees_and_the_exact_action_runs_once(client, env, rt, reviewer):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    assert _rules(client, env, slug, {"connection": reviewer["name"], "shadow": False}).status_code == 200
    client.put("/api/governance", headers=ADMIN, json={"dlp": {"mask": ["email"]}})
    try:
        tid = assign(client, env, slug, CRM)
        work()  # o gate segura a ação; o revisor confere depois que a rodada termina
        rq = _reqs(tid, "cross_review")
        assert len(rq) == 1 and rq[0].status == "decided" and rq[0].decision == "approve"
        assert rq[0].decided_by == "revisor google/gemini-2.5-pro" and rq[0].review["verdict"] == "agree"
        sent = reviewer["calls"][0]["messages"][1]["content"]
        assert "ana@cliente.com" not in sent and re.search(r"\[EMAIL_\d+\]", sent) and "update_crm" in sent  # DLP antes do revisor
        assert not [d for d in client.get("/api/decisions", headers=env["h"]["mgr"]).json() if d["task"] and d["task"]["id"] == tid]
        work()  # a tarefa retoma e executa a ação liberada
        t = task(client, env, tid)
        assert t["status"] == "done"
        with SessionLocal() as db:
            assert db.get(HumanRequest, rq[0].id).grant_used is True
            e = db.scalar(select(Employee).where(Employee.id == rq[0].employee_id))
            u = db.scalars(select(UsageEvent).where(UsageEvent.agent_id == e.agent_id, UsageEvent.channel == "review")).all()
            assert len(u) == 1 and u[0].tokens_in == 1000 and abs(u[0].cost_usd - 0.002) < 1e-9 and u[0].model == "google/gemini-2.5-pro"
            m = svc.employees.metrics(db, e, 30)
            assert m["reviews"] == 1 and m["review_agreed"] == 1 and m["review_cost_usd"] == 0.002
            assert "Revisor: 1 revisão(ões), 1 concordou" in svc.employees.make_report(db, e, "daily").summary
    finally:
        client.put("/api/governance", headers=ADMIN, json={"dlp": {"mask": []}})


@pytest.mark.parametrize("answer", ['{"verdict": "disagree", "reason": "o e-mail não é do cliente 7"}',
                                    RuntimeError("timeout"), "não sei"])
def test_disagree_or_failure_goes_to_a_person_with_the_reason(client, env, rt, reviewer, answer):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    _rules(client, env, slug, {"connection": reviewer["name"], "shadow": False})
    reviewer["answers"].append(answer)
    tid = assign(client, env, slug, CRM)
    work()
    cr = _reqs(tid, "cross_review")[0]
    assert cr.status == "decided" and cr.decision == "escalate"
    d = open_request(client, env, tid)  # falha fecha: vira um pedido de aprovação para o gestor
    assert d["kind"] == "approval" and d["mode"] == "approve" and d["review"]["verdict"] in ("disagree", "error")
    if d["review"]["verdict"] == "disagree":
        assert "não é do cliente 7" in d["review"]["reason"]
    assert task(client, env, tid)["status"] == "waiting_human"
    r = client.post(f"/api/decisions/{d['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    assert r.status_code == 200, r.text
    work()
    assert task(client, env, tid)["status"] == "done"


def test_never_on_disagree_refuses_and_does_not_ask_again(client, env, rt, reviewer):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    _rules(client, env, slug, {"connection": reviewer["name"], "shadow": False, "on_disagree": "never"})
    reviewer["answers"].append('{"verdict": "disagree", "reason": "arriscado"}')
    tid = assign(client, env, slug, CRM)
    work()
    work()
    cr = _reqs(tid, "cross_review")
    assert len(cr) == 1 and cr[0].decision == "reject" and not _reqs(tid, "approval")
    assert task(client, env, tid)["status"] == "done" and len(reviewer["calls"]) == 1
    with SessionLocal() as db:  # a mesma ação de novo, na mesma tarefa: recusada sem perguntar ao revisor
        from app.models import Agent
        t = db.get(EmployeeTask, tid)
        a = db.get(Agent, db.get(Employee, t.employee_id).agent_id)
        g = svc.employee_tasks.gate(db, a, tid, "update_crm", "http:update_crm:PATCH", {"id": 7, "email": "ana@cliente.com"})
        assert g["status"] == "denied" and len(reviewer["calls"]) == 1


def test_shadow_review_measures_agreement_without_deciding(client, env, rt, reviewer):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    _rules(client, env, slug, {"connection": reviewer["name"]})  # observação (padrão)
    reviewer["answers"] += ['{"verdict": "agree", "reason": "certo"}', '{"verdict": "agree", "reason": "certo"}']
    t1, t2 = assign(client, env, slug, CRM), assign(client, env, slug, 'use update_crm {"id": 8}')
    work()
    assert not _reqs(t1, "cross_review")
    d1, d2 = open_request(client, env, t1), open_request(client, env, t2)
    assert d1["kind"] == "approval" and d1["review"]["shadow"] is True
    assert d1["review"]["verdict"] is None and d1["review"]["hidden"] is True  # a pessoa decide sem ver o veredito
    client.post(f"/api/decisions/{d1['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    client.post(f"/api/decisions/{d2['id']}", headers=env["h"]["mgr"], json={"decision": "reject", "reason": "não"})
    with SessionLocal() as db:
        assert svc.employee_tasks.request_dict(db, db.get(HumanRequest, d1["id"]))["review"]["verdict"] == "agree"
        e = db.get(Employee, _reqs(t1)[0].employee_id)
        m = svc.employees.metrics(db, e, 30)
        assert m["reviews"] == 2 and m["review_pairs"] == 2 and m["review_matches"] == 1
        assert svc.employee_work.kpi_actuals(db, e)["cross_review_agreement_rate"] == 50.0


def test_floor_above_the_rule_skips_the_review(client, env, rt, reviewer):
    slug = hire(client, env)
    with SessionLocal() as db:
        from app.models import Agent, OrgAuthorityFloor
        e = db.scalar(select(Employee).join(Agent, Agent.id == Employee.agent_id).where(Agent.slug == slug))
        _rules(client, env, slug, {"connection": reviewer["name"], "shadow": False})
        assert svc.employee_gate.evaluate(db, e, "write_internal", {})["review"]["connection"] == reviewer["name"]
        f = db.scalar(select(OrgAuthorityFloor).where(OrgAuthorityFloor.action_type == "write_internal"))
        old = (f.min_mode, f.separation) if f else None
        if f is None:
            f = OrgAuthorityFloor(action_type="write_internal", min_mode="approve", separation=False)
            db.add(f)
        f.min_mode = "approve"
        db.commit()
        try:
            ev = svc.employee_gate.evaluate(db, e, "write_internal", {})
            assert ev["mode"] == "approve" and ev["review"] is None  # o revisor nunca passa por cima do piso
        finally:
            if old is None:
                db.delete(f)
            else:
                f.min_mode, f.separation = old
            db.commit()


def test_review_timeout_escalates_from_the_sweep(client, env, rt, reviewer, monkeypatch):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    _rules(client, env, slug, {"connection": reviewer["name"], "shadow": False})
    monkeypatch.setattr(cross_review, "kick", lambda db, task: None)  # o revisor nunca começou (a réplica caiu)
    monkeypatch.setattr(notify, "_bg", lambda fn, *a: None)  # e quando começa, não responde
    tid = assign(client, env, slug, CRM)
    work()
    rid = _reqs(tid, "cross_review")[0].id
    no = client.post(f"/api/decisions/{rid}", headers=env["h"]["mgr"], json={"decision": "approve"})
    assert no.status_code == 400 and "revisor" in no.text  # quem decide este pedido é o revisor, não uma pessoa
    with SessionLocal() as db:
        r = db.get(HumanRequest, rid)
        svc.employee_tasks.sweep(db)  # começa (a rodada já terminou)
        db.refresh(r)
        assert r.review.get("started") and r.status == "open"
        from datetime import timedelta

        from app.models import now
        r.expires_at = now() - timedelta(seconds=1)
        db.commit()
        svc.employee_tasks.sweep(db)  # passou do prazo: falha fecha
        db.refresh(r)
        assert r.status == "decided" and r.decision == "escalate" and "prazo" in r.review["reason"]
    assert open_request(client, env, tid)["kind"] == "approval"
    assert task(client, env, tid)["status"] == "waiting_human"
