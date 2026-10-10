# ruff: noqa: F811  (env, rt e channels são fixtures importadas)
"""Digital employees, o que faltava: tarefa direto do chat (Slack e Teams), nota de qualidade dos resultados (juiz por
amostra e o gestor) valendo na carreira, e o painel do gestor com custo, decisões, prazos e gargalos."""
import base64
import hashlib
import hmac
import json
import time

from app.db import SessionLocal
from app.models import Employee, EmployeeTask, User
from app.services import employee_quality as qualitymod
from app.services import employee_work as workmod

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, rt, task, work  # noqa: F401
from .test_employees_notify import SECRET, _slack_on, channels  # noqa: F401


def _event(slack, email, text, event_id, kind="app_mention", **extra):
    body = {"type": "event_callback", "event_id": event_id,
            "event": {"type": kind, "user": slack.uid(email), "text": text, "channel": "C42", "ts": "1700.1", **extra}}
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig, "Content-Type": "application/json"}


def _employee(client, env, rt, name):
    slug = hire(client, env, name=name)
    make_active(client, env, slug, rt)
    return slug


# ------------------------------------------------------------------ Slack
def test_slack_mention_becomes_a_task_and_the_result_comes_back_in_the_thread(client, env, rt, channels):
    _slack_on(client)
    name = f"Renata {env['n']}"
    slug = _employee(client, env, rt, name)
    raw, h = _event(channels, env["em"]["req"], "x", "Ev0")
    challenge = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    ts = h["X-Slack-Request-Timestamp"]
    sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:".encode() + challenge, hashlib.sha256).hexdigest()
    assert client.post("/hooks/slack/events", content=challenge,
                       headers={**h, "X-Slack-Signature": sig}).json() == {"challenge": "abc"}
    raw, h = _event(channels, env["em"]["req"], f"<@UBOT> {name.lower()}, renova o contrato da ACME", "Ev1")
    assert client.post("/hooks/slack/events", content=raw, headers={**h, "X-Slack-Signature": "v0=errada"}).status_code == 401
    assert client.post("/hooks/slack/events", content=raw, headers=h).status_code == 200
    reply = channels.of("chat.postMessage")[-1]
    assert reply["channel"] == "C42" and reply["thread_ts"] == "1700.1" and "Tarefa #" in reply["text"]
    with SessionLocal() as db:
        t = db.query(EmployeeTask).filter_by(source="slack").order_by(EmployeeTask.id.desc()).first()
        tid = t.id
        assert t.title == "renova o contrato da ACME" and t.requester == env["em"]["req"]
        assert t.origin == {"slack": {"channel": "C42", "ts": "1700.1"}}
    n = len(channels.of("chat.postMessage"))
    assert client.post("/hooks/slack/events", content=raw, headers=h).status_code == 200  # o Slack reenviou
    with SessionLocal() as db:
        assert db.query(EmployeeTask).filter_by(source="slack").count() == 1
    assert len(channels.of("chat.postMessage")) == n
    work()
    assert task(client, env, tid)["status"] == "done" and task(client, env, tid)["via"] == "slack"
    done = channels.of("chat.postMessage")[-1]
    assert done["thread_ts"] == "1700.1" and f"Tarefa #{tid} concluída" in done["text"]
    # sem nome reconhecido: diz para quem a pessoa pode pedir; pessoa fora do Hangar: avisa; bot: ignora
    raw, h = _event(channels, env["em"]["req"], "<@UBOT> renova tudo", "Ev2")
    client.post("/hooks/slack/events", content=raw, headers=h)
    assert "Para quem" in channels.of("chat.postMessage")[-1]["text"] and name in channels.of("chat.postMessage")[-1]["text"]
    raw, h = _event(channels, "ninguem@fora.com", f"{name}: oi", "Ev3")
    client.post("/hooks/slack/events", content=raw, headers=h)
    assert "Não encontrei você" in channels.of("chat.postMessage")[-1]["text"]
    n = len(channels.of("chat.postMessage"))
    raw, h = _event(channels, env["em"]["req"], f"{name}: oi", "Ev4", bot_id="B1")
    client.post("/hooks/slack/events", content=raw, headers=h)
    assert len(channels.of("chat.postMessage")) == n
    # mensagem direta (DM) também vale; quem não pode entregar tarefas ao funcionário não consegue
    raw, h = _event(channels, env["em"]["out"], f"{name}: prepara a proposta", "Ev5", kind="message", channel_type="im")
    client.post("/hooks/slack/events", content=raw, headers=h)
    assert "ainda não pode" in channels.of("chat.postMessage")[-1]["text"]
    assert slug


# ------------------------------------------------------------------ Teams (webhook de saída)
def test_teams_outgoing_webhook_creates_the_task_and_the_result_goes_to_the_person(client, env, rt, channels):
    _slack_on(client)  # o resultado vai pelo canal pessoal da pessoa (aqui, DM no Slack)
    key = base64.b64encode(b"k" * 32).decode()
    r = client.put("/api/admin/notifications", headers=ADMIN, json={"teams_outgoing_secret": key})
    assert r.json()["teams"]["outgoing"] is True and r.json()["teams"]["outgoing_url"].endswith("/hooks/teams/messages")
    assert client.put("/api/admin/notifications", headers=ADMIN, json={"teams_outgoing_secret": "não é base64!"}).status_code == 400
    name = f"Bruno {env['n']}"
    _employee(client, env, rt, name)
    with SessionLocal() as db:
        u = db.query(User).filter_by(email=env["em"]["req"]).one()
        u.provider, u.subject = "microsoft", f"oid-{env['n']}"
        db.commit()

    def send(text, oid):
        raw = json.dumps({"type": "message", "text": f"<at>Hangar</at>&nbsp;{text}", "from": {"aadObjectId": oid},
                          "conversation": {"id": "19:abc@thread.tacv2"}}).encode()
        mac = base64.b64encode(hmac.new(base64.b64decode(key), raw, hashlib.sha256).digest()).decode()
        return client.post("/hooks/teams/messages", content=raw, headers={"Authorization": f"HMAC {mac}"})
    out = send(f"{name}: prepara a proposta da ACME", f"oid-{env['n']}").json()
    assert out["type"] == "message" and "Tarefa #" in out["text"]
    assert "Não encontrei você" in send(f"{name}: oi", "oid-desconhecido").json()["text"]
    raw = json.dumps({"text": "x"}).encode()
    assert client.post("/hooks/teams/messages", content=raw, headers={"Authorization": "HMAC errado"}).status_code == 401
    with SessionLocal() as db:
        t = db.query(EmployeeTask).filter_by(source="teams").order_by(EmployeeTask.id.desc()).first()
        tid = t.id
        assert t.title == "prepara a proposta da ACME" and t.origin["teams"]["conversation"].startswith("19:")
    work()
    dm = channels.of("chat.postMessage")[-1]
    assert dm["channel"] == channels.uid(env["em"]["req"]) and f"#{tid}" in dm["text"]


# ------------------------------------------------------------------ qualidade
def test_quality_judge_samples_tasks_and_the_manager_rating_wins(client, env, rt, channels, monkeypatch):
    calls = []

    def judge(db, e, a, t):
        calls.append(t.id)
        return 2, "faltou o valor da renovação"
    monkeypatch.setattr(qualitymod, "JUDGE", judge)
    slug = _employee(client, env, rt, f"Clara {env['n']}")
    assert not calls  # tarefas de experiência não vão para o juiz
    assert client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"quality_sample": 101}).status_code == 400
    assert client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"quality_sample": 100}).status_code == 200
    tid = assign(client, env, slug, "resuma as renovações da semana")
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and t["quality"] == 2 and t["quality_by"] == "judge" and "valor" in t["quality_note"]
    assert any(ev["kind"] == "quality" for ev in t["events"])
    # o gestor avalia: a nota dele substitui a do juiz; quem só pede tarefas não avalia
    assert client.post(f"/api/tasks/{tid}/quality", headers=env["h"]["req"], json={"score": 5}).status_code == 403
    assert client.post(f"/api/tasks/{tid}/quality", headers=env["h"]["mgr"], json={"score": 9}).status_code == 400
    t = client.post(f"/api/tasks/{tid}/quality", headers=env["h"]["mgr"], json={"score": 5, "note": "ótimo"}).json()
    assert t["quality"] == 5 and t["quality_by"] == env["em"]["mgr"]
    # amostra 0: o juiz não é chamado
    client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"quality_sample": 0})
    tid2 = assign(client, env, slug, "outra tarefa")
    work()
    assert task(client, env, tid2)["quality"] is None and calls == [tid]
    q = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()["quality"]
    assert q["rated"] == 1 and q["avg"] == 5 and q["by_manager"] == 1 and q["sample"] == 0
    # com avaliações suficientes, a nota média entra nos critérios de carreira
    monkeypatch.setattr(qualitymod, "MIN_RATED", 1)
    with SessionLocal() as db:
        e = db.get(Employee, db.get(EmployeeTask, tid).employee_id)
        crit = {c["key"]: c for c in workmod.career_status(db, e)["criteria"]}
    assert crit["quality"]["value"] == 5 and crit["quality"]["ok"] is True


def test_sample_is_deterministic():
    t = EmployeeTask(id=12345)
    assert qualitymod.sampled(t, 100) and not qualitymod.sampled(t, 0)
    assert qualitymod.sampled(t, 50) == qualitymod.sampled(t, 50)
    hits = sum(qualitymod.sampled(EmployeeTask(id=i), 20) for i in range(1, 2001))
    assert 300 < hits < 500  # ~20%


# ------------------------------------------------------------------ painel do gestor
def test_manager_dashboard_shows_cost_decisions_and_bottlenecks(client, env, rt):
    slug = _employee(client, env, rt, f"Davi {env['n']}")
    tid = assign(client, env, slug, 'use send_email {"to": "ana@cliente.com", "body": "Olá"}')
    work()
    assert task(client, env, tid)["status"] == "waiting_human"
    d = client.get("/api/workforce/dashboard", headers=env["h"]["mgr"]).json()
    row = next(r for r in d["employees"] if r["slug"] == slug)
    assert row["pending_decisions"] == 1 and row["waiting"] == 1 and row["team"] == env["team"]
    assert row["done"] >= 3 and row["level"] == "intern"  # as tarefas de experiência
    dec = next(x for x in d["bottlenecks"]["decisions"] if x["employee"] == slug)
    assert dec["task_id"] == tid and dec["holder"] and dec["age_min"] >= 0
    assert any(h["pending"] >= 1 for h in d["bottlenecks"]["holders"])
    team = next(g for g in d["teams"] if g["team"] == env["team"])
    assert team["employees"] >= 1 and team["pending_decisions"] >= 1 and d["total"]["pending_decisions"] >= 1
    # quem não gerencia não vê; admin vê todos; filtro por time
    out = client.get("/api/workforce/dashboard", headers=env["h"]["out"]).json()
    assert all(r["slug"] != slug for r in out["employees"])
    assert any(r["slug"] == slug for r in client.get("/api/workforce/dashboard", headers=ADMIN,
                                                    params={"team": env["team"], "days": 7}).json()["employees"])
