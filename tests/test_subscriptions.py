"""Assinatura Claude/ChatGPT de uma pessoa nos jobs de harness que ELA dispara (services/subscriptions.py)."""
import json
import threading
import time

import pytest

from app import auth, crypto, deploy
from app import db as dbmod
from app import services as svc
from app.models import Job, User, UserSubscription
from app.services import subscriptions as subs

from .conftest import ADMIN

CLAUDE_TOKEN = "sk-ant-oat01-" + "x" * 40
CODEX_AUTH = {"OPENAI_API_KEY": None, "tokens": {"id_token": "id-" + "a" * 30, "access_token": "acc-" + "b" * 30,
                                                 "refresh_token": "ref-" + "c" * 30, "account_id": "acct-1234"}}


class FakeJobs:
    """O container do job: pela assinatura responde `subs_result`; pela API, `api_result`. Guarda o ambiente."""

    def __init__(self):
        self.runs: list[dict] = []
        self.subs_result = {"status": "passed", "result": "feito pela assinatura",
                            "usage": {"input_tokens": 1000, "output_tokens": 500, "reported_cost_usd": 0.42}}
        self.api_result = {"status": "passed", "result": "feito pela API", "usage": {"input_tokens": 10, "output_tokens": 5}}

    def run_job_container(self, name, image, environment, mem_limit, cpus):
        self.runs.append({"image": image, "env": dict(environment)})
        job_id = int(environment["JOB_CALLBACK_URL"].rstrip("/").split("/")[-2])
        r = dict(self.subs_result if environment.get("LLM_AUTH") == "subscription" else self.api_result)

        def cb():
            with dbmod.SessionLocal() as s:
                svc.complete_job(s, s.get(Job, job_id), r["status"], r["result"], r.get("diff", ""), r.get("logs", ""),
                                 r.get("usage"), r.get("extra"))
        threading.Timer(0.05, cb).start()


@pytest.fixture()
def jobs(monkeypatch, fake_docker):
    fj = FakeJobs()
    monkeypatch.setattr(deploy, "run_job_container", fj.run_job_container)
    return fj


@pytest.fixture()
def person(uniq):
    email = f"{uniq('dev')}@acme.com"
    with dbmod.SessionLocal() as s:
        u = User(email=email, name="Dev", org_role="developer", active=True)
        s.add(u)
        s.commit()
        return {"id": u.id, "h": {"Authorization": f"Bearer {auth.create_session(s, u)}"}}


@pytest.fixture()
def enabled(client):
    client.put("/api/workspace/policy", headers=ADMIN, json={"subscriptions": {"enabled": True, "max_concurrent": 2}})
    yield
    client.put("/api/workspace/policy", headers=ADMIN, json={"subscriptions": {"enabled": False}})


def _agent(db, uniq, billing, conn=None, harness="claude-code"):
    a = svc.create_agent(db, uniq("Dev"), "executa tarefas", "diff")
    svc.design_agent(db, a.slug, {"harness": {"id": harness, "billing": billing, **({"connection": conn} if conn else {})}})
    return a


def _conn(db, uniq, protocol="anthropic"):
    name = uniq(protocol)
    svc.upsert_llm_connection(db, name, "https://api.x", "model-x", "sk-api-test", "", "t", protocol, 3.0, 15.0)
    return name


def test_the_company_decides_and_api_keys_are_refused(client, person):
    r = client.put("/api/me/subscriptions/anthropic", headers=person["h"], json={"credential": CLAUDE_TOKEN})
    assert r.status_code == 403 and "não liberou" in r.text  # desligado por padrão
    client.put("/api/workspace/policy", headers=ADMIN, json={"subscriptions": {"enabled": True}})
    try:
        bad = client.put("/api/me/subscriptions/anthropic", headers=person["h"], json={"credential": "sk-ant-api03-" + "z" * 40})
        assert bad.status_code == 400 and "chave de API" in bad.text
        assert client.put("/api/me/subscriptions/openai", headers=person["h"],
                          json={"credential": json.dumps({"OPENAI_API_KEY": "sk-x"})}).status_code == 400
        ok = client.put("/api/me/subscriptions/anthropic", headers=person["h"], json={"credential": CLAUDE_TOKEN, "label": "Max"})
        assert ok.status_code == 200 and ok.json()["hint"] == "xxxx" and "credential" not in ok.json()
        mine = client.get("/api/me/subscriptions", headers=person["h"]).json()
        assert mine["policy"]["enabled"] and [s["vendor"] for s in mine["subscriptions"]] == ["anthropic"]
        assert CLAUDE_TOKEN not in json.dumps(mine) and CLAUDE_TOKEN not in json.dumps(client.get("/api/admin/subscriptions", headers=ADMIN).json())
        with dbmod.SessionLocal() as s:
            row = s.query(UserSubscription).filter_by(user_id=person["id"]).one()
            assert row.credential.startswith("enc:") and crypto.decrypt(row.credential) == CLAUDE_TOKEN
        assert client.get("/api/me/subscriptions", headers=ADMIN).status_code == 403  # o token do admin não é uma pessoa
    finally:
        client.put("/api/workspace/policy", headers=ADMIN, json={"subscriptions": {"enabled": False}})


def test_job_by_the_person_runs_on_their_subscription(client, db, uniq, jobs, person, enabled):
    conn = _conn(db, uniq)
    a = _agent(db, uniq, "subscription_then_api", conn)
    client.put("/api/me/subscriptions/anthropic", headers=person["h"], json={"credential": CLAUDE_TOKEN})
    job = svc.run_harness_job(db, a.slug, "crie um arquivo", "stage", 30, "Dev", "admin-ui", user_id=person["id"])
    env = jobs.runs[-1]["env"]
    assert env["LLM_AUTH"] == "subscription" and env["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE_TOKEN
    assert env["LLM_API_KEY"] == "" and env["LLM_BASE_URL"] == ""  # a chave da conexão não vai junto
    assert job.status == "passed" and job.billing == "subscription" and job.cost_usd == 0
    assert job.api_equiv_cost_usd == round((1000 * 3 + 500 * 15) / 1e6, 6)  # quanto custaria pela API
    d = svc.job_dict(job)
    assert d["billing"] == "subscription" and d["api_equiv_cost_usd"] > 0
    mine = client.get("/api/me/subscriptions", headers=person["h"]).json()["subscriptions"][0]
    assert mine["jobs_30d"] == 1 and mine["saved_usd_30d"] == job.api_equiv_cost_usd


def test_jobs_without_a_person_always_use_the_api(db, uniq, jobs, person, client, enabled):
    conn = _conn(db, uniq)
    a = _agent(db, uniq, "subscription_then_api", conn)
    client.put("/api/me/subscriptions/anthropic", headers=person["h"], json={"credential": CLAUDE_TOKEN})
    job = svc.run_harness_job(db, a.slug, "x", "stage", 30, "agendamento", "schedule")  # sem user_id
    assert job.billing == "api" and jobs.runs[-1]["env"]["LLM_API_KEY"] == "sk-api-test"
    assert "sem uma pessoa" in job.billing_note
    only = _agent(db, uniq, "subscription")
    with pytest.raises(svc.PlatformError, match="só pela assinatura"):
        svc.submit_job(db, only.slug, "x")


def test_limit_pauses_the_subscription_and_falls_back_to_the_api(client, db, uniq, jobs, person, enabled):
    conn = _conn(db, uniq)
    a = _agent(db, uniq, "subscription_then_api", conn)
    client.put("/api/me/subscriptions/anthropic", headers=person["h"], json={"credential": CLAUDE_TOKEN})
    reset = int(time.time()) + 3 * 3600
    jobs.subs_result = {"status": "limited", "result": f"Claude AI usage limit reached|{reset} {CLAUDE_TOKEN}",
                        "extra": {"subscription_limit": {"resets_at": reset, "message": "usage limit reached"}}}
    job = svc.run_harness_job(db, a.slug, "x", "stage", 30, "Dev", "admin-ui", user_id=person["id"])
    assert [r["env"].get("LLM_AUTH") for r in jobs.runs[-2:]] == ["subscription", None]  # refeito pela API
    assert job.status == "passed" and job.billing == "api" and job.result == "feito pela API"
    assert "refeito pela API" in job.billing_note and job.cost_usd > 0
    mine = client.get("/api/me/subscriptions", headers=person["h"]).json()["subscriptions"][0]
    assert mine["status"] == "limited" and mine["limited_until"]
    # enquanto está no limite, o próximo job nem tenta a assinatura
    job2 = svc.run_harness_job(db, a.slug, "y", "stage", 30, "Dev", "admin-ui", user_id=person["id"])
    assert job2.billing == "api" and "limite do plano" in job2.billing_note
    # sem a API de reserva, o job falha com o motivo (e a credencial nunca aparece no resultado)
    with dbmod.SessionLocal() as s:
        row = s.query(UserSubscription).filter_by(user_id=person["id"]).one()
        row.status, row.limited_until = "active", None
        s.commit()
    only = _agent(db, uniq, "subscription")
    job3 = svc.run_harness_job(db, only.slug, "z", "stage", 30, "Dev", "admin-ui", user_id=person["id"])
    assert job3.status == "failed" and "limite do plano" in job3.result and CLAUDE_TOKEN not in job3.result


def test_codex_refresh_is_kept_and_rejected_credential_is_flagged(client, db, uniq, jobs, person, enabled):
    a = _agent(db, uniq, "subscription", harness="codex")
    client.put("/api/me/subscriptions/openai", headers=person["h"], json={"credential": json.dumps(CODEX_AUTH)})
    new = {**CODEX_AUTH, "tokens": {**CODEX_AUTH["tokens"], "refresh_token": "ref-" + "n" * 30}}
    jobs.subs_result = {"status": "passed", "result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1},
                        "extra": {"codex_auth": json.dumps(new)}}
    job = svc.run_harness_job(db, a.slug, "x", "stage", 30, "Dev", "admin-ui", user_id=person["id"])
    assert json.loads(jobs.runs[-1]["env"]["CODEX_AUTH_JSON"]) == CODEX_AUTH
    assert job.billing == "subscription" and jobs.runs[-1]["image"].endswith("harness-codex:latest")  # sem conexão: não é mock
    with dbmod.SessionLocal() as s:
        row = s.query(UserSubscription).filter_by(user_id=person["id"], vendor="openai").one()
        assert json.loads(crypto.decrypt(row.credential))["tokens"]["refresh_token"] == "ref-" + "n" * 30
    jobs.subs_result = {"status": "failed", "result": "401 Unauthorized", "extra": {"auth_rejected": "401 Unauthorized"}}
    svc.run_harness_job(db, a.slug, "x", "stage", 30, "Dev", "admin-ui", user_id=person["id"])
    mine = {s["vendor"]: s for s in client.get("/api/me/subscriptions", headers=person["h"]).json()["subscriptions"]}
    assert mine["openai"]["status"] == "invalid"
    with pytest.raises(svc.PlatformError, match="recusada"):
        svc.submit_job(db, a.slug, "x", user_id=person["id"])


def test_concurrency_limit_and_admin_revoke(client, db, uniq, person, enabled, monkeypatch, fake_docker):
    monkeypatch.setattr(deploy, "run_job_container", lambda *a, **k: None)  # os jobs ficam rodando
    conn = _conn(db, uniq)
    a = _agent(db, uniq, "subscription_then_api", conn)
    client.put("/api/me/subscriptions/anthropic", headers=person["h"], json={"credential": CLAUDE_TOKEN})
    j1 = svc.submit_job(db, a.slug, "1", user_id=person["id"])
    j2 = svc.submit_job(db, a.slug, "2", user_id=person["id"])
    j3 = svc.submit_job(db, a.slug, "3", user_id=person["id"])
    assert (j1.billing, j2.billing, j3.billing) == ("subscription", "subscription", "api")
    assert "2 job(s) rodando" in j3.billing_note
    for j in (j1, j2, j3):
        svc.cancel_job(db, j.id)
    sid = client.get("/api/me/subscriptions", headers=person["h"]).json()["subscriptions"][0]["id"]
    assert client.delete(f"/api/admin/subscriptions/{sid}", headers=person["h"]).status_code == 403
    assert client.delete(f"/api/admin/subscriptions/{sid}", headers=ADMIN).json()["ok"] is True
    assert client.get("/api/me/subscriptions", headers=person["h"]).json()["subscriptions"] == []


def test_vendor_mapping():
    assert subs.VENDOR_OF_HARNESS == {"claude-code": "anthropic", "codex": "openai"}
    with dbmod.SessionLocal() as s:
        assert subs.choose(s, "hermes", "subscription", 1)[1].startswith("o harness 'hermes'")
        assert subs.choose(s, "claude-code", "api", 1) == (None, "")
