# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Fase F: calendário útil, filas do time com itens para pessoas e SLA, playbooks com etapas de pessoas e de Digital
employees, conversas com gente de fora (modelos aprovados, lembretes, promessas, WhatsApp) e reuniões."""
import hashlib
import hmac
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app import config
from app.db import SessionLocal
from app.models import Agent, EmployeeTask, ExternalThread, Meeting, Organization, Playbook, PlaybookRun, User, WorkItem
from app.services import bizcal, calendar_api, correspondence, notify, playbooks, work_queues
from app.services import employee_tasks as tasksmod

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401
from .test_employees_notify import FakeSMTP, _sync


@pytest.fixture(autouse=True)
def _close_leftover_tasks():
    """O que o teste deixou aberto (respostas que acordam tarefas, briefings, ações de reunião) não pode ocupar a fila
    do executor nos testes seguintes."""
    with SessionLocal() as db:
        start = db.query(EmployeeTask.id).order_by(EmployeeTask.id.desc()).limit(1).scalar() or 0
    yield
    with SessionLocal() as db:
        for t in db.query(EmployeeTask).filter(EmployeeTask.id > start, EmployeeTask.status.notin_(tasksmod.TERMINAL)):
            t.status = "cancelled"
        db.commit()


def _agent(slug):
    with SessionLocal() as db:
        return db.query(Agent).filter_by(slug=slug).one().id


# ------------------------------------------------------------------ calendário útil
def test_business_calendar_holidays_and_schedules(client, env):
    cal = bizcal.Calendar({})
    h = cal.holidays(2026)
    assert h["2026-02-16"] == "Carnaval" and h["2026-04-03"] == "Sexta-feira Santa" and h["2026-06-04"] == "Corpus Christi"
    assert h["2026-11-20"] == "Consciência Negra"
    assert cal.add(date(2026, 4, 2), 1) == date(2026, 4, 6)  # pula a Sexta Santa e o fim de semana
    assert cal.add(date(2026, 4, 6), -1) == date(2026, 4, 2)
    assert cal.nth(2026, 1, 1) == date(2026, 1, 2) and cal.nth(2026, 12, -1) == date(2026, 12, 31)
    assert cal.between(date(2026, 4, 2), date(2026, 4, 7)) == 2
    worked = bizcal.Calendar({"removed": ["2026-02-16", "2026-02-17"], "holidays": [{"date": "2026-01-25", "name": "Aniversário de SP"}]})
    assert worked.is_business_day(date(2026, 2, 16)) and worked.is_business_day(date(2026, 1, 26)) is not False
    r = client.put("/api/calendar", headers=ADMIN, json={"holidays": [{"date": "2026-07-09", "name": "Revolução Constitucionalista"}]})
    assert r.status_code == 200 and any(x["date"] == "2026-07-09" for x in r.json()["extra"])
    assert client.put("/api/calendar", headers=env["h"]["mgr"], json={"workdays": [0]}).status_code == 403
    with SessionLocal() as db:
        nxt = bizcal.next_after(db, "@bd 1 09:00", datetime(2026, 12, 31, 12, tzinfo=UTC), "America/Sao_Paulo")
        assert nxt == datetime(2027, 1, 4, 12, tzinfo=UTC)  # 1º/jan é feriado, 2 e 3 são fim de semana
        assert bizcal.next_after(db, "@bd -1 17:00", datetime(2026, 7, 1, tzinfo=UTC), "UTC").date() == date(2026, 7, 31)
        assert bizcal.next_after(db, "@bdaily 08:00", datetime(2026, 7, 9, 9, tzinfo=UTC), "UTC").date() == date(2026, 7, 10)
    assert bizcal.describe("@bd 1 09:00") == "1º dia útil do mês às 09:00"
    client.put("/api/calendar", headers=ADMIN, json={"holidays": []})


# ------------------------------------------------------------------ filas e itens para pessoas
def _queue(client, env, slug_emp):
    r = client.put("/api/queues", headers=env["h"]["mgr"], json={
        "name": f"Backoffice {env['n']}", "team": env["team"], "categories": [
            {"name": "Nota fiscal", "keywords": ["nota", "nf"], "sla_hours": 4, "route": {"type": "employee", "refs": [slug_emp]}},
            {"name": "Cadastro", "keywords": ["cadastro"], "sla_hours": 1, "route": {"type": "people"}}],
        "default_category": "Cadastro"})
    assert r.status_code == 200, r.text
    return r.json()["slug"]


def test_queue_routes_to_employee_or_people_with_sla_and_handback(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    assert client.put("/api/queues", headers=env["h"]["req"], json={"name": "x", "team": env["team"]}).status_code == 403
    q = _queue(client, env, slug)
    a = client.post(f"/api/queues/{q}/items", headers=env["h"]["req"], json={"title": "Lançar a nota 123", "body": "nf do fornecedor X"}).json()
    assert a["category"] == "Nota fiscal" and a["status"] == "employee" and a["employee_task"]
    work()
    assert client.get(f"/api/work-items/{a['id']}", headers=env["h"]["req"]).json()["status"] == "done"
    # vai para as pessoas: pegar a próxima, evidência e SLA
    b = client.post(f"/api/queues/{q}/items", headers=env["h"]["req"], json={"title": "Ajustar o cadastro do cliente"}).json()
    assert b["status"] == "open" and b["category"] == "Cadastro" and b["sla_due_at"]
    with SessionLocal() as db:
        db.get(WorkItem, b["id"]).sla_due_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
        assert work_queues.sweep(db) >= 1
    assert client.get(f"/api/work-items/{b['id']}", headers=env["h"]["mgr"]).json()["sla_breached"] is True
    assert client.post(f"/api/queues/{q}/next", headers=env["h"]["out"]).status_code == 403
    got = client.post(f"/api/queues/{q}/next", headers=env["h"]["mgr"]).json()
    assert got["id"] == b["id"] and got["status"] == "in_progress"  # o estourado vem primeiro
    done = client.post(f"/api/work-items/{b['id']}", headers=env["h"]["mgr"], json={"action": "done", "note": "ajustado"}).json()
    assert done["status"] == "done" and done["handled_by"] == "human"
    # o funcionário prepara e devolve para uma pessoa terminar
    c = client.post(f"/api/queues/{q}/items", headers=env["h"]["req"], json={"title": "Nota sem pedido de compra"}).json()
    with SessionLocal() as db:
        agent = db.get(Agent, _agent(slug))
        g = tasksmod.gate(db, agent, c["employee_task"], "hand_to_person", "platform:hand_to_person",
                          {"summary": "falta o pedido de compra", "draft": "lançamento pronto, só aprovar", "assignee": env["em"]["mgr"]},
                          "", "platform")
        assert g["status"] == "allowed" and f"#{c['id']}" in g["result"]
    item = client.get(f"/api/work-items/{c['id']}", headers=env["h"]["mgr"]).json()
    assert item["status"] == "in_progress" and item["assignee_email"] == env["em"]["mgr"] and "pedido de compra" in item["prepared"]
    work()  # a tarefa do funcionário termina, mas o item fica com a pessoa
    assert client.get(f"/api/work-items/{c['id']}", headers=env["h"]["mgr"]).json()["status"] == "in_progress"
    assert client.post(f"/api/work-items/{c['id']}", headers=env["h"]["mgr"], json={"action": "done"}).json()["handled_by"] == "assisted"
    stats = next(x for x in client.get("/api/queues", headers=env["h"]["mgr"]).json() if x["slug"] == q)["stats"]
    assert stats["automatic"] == 1 and stats["assisted"] == 1 and stats["human"] == 1
    # entrada por webhook, com o token da fila
    tok = client.post(f"/api/queues/{q}/intake-token", headers=env["h"]["mgr"], json={"enabled": True}).json()["token"]
    assert client.post(f"/hooks/queues/{q}", json={"title": "cadastro novo"}).status_code == 401
    r = client.post(f"/hooks/queues/{q}", headers={"Authorization": f"Bearer {tok}"}, json={"title": "cadastro novo", "source": "CRM"})
    assert r.status_code == 200 and r.json()["category"] == "Cadastro"


def test_item_evidence_file_and_four_eyes_approval(client, env):
    with SessionLocal() as db:
        from app.models import Team
        t = db.query(Team).filter_by(slug=env["team"]).one()
        mgr = db.query(User).filter_by(email=env["em"]["mgr"]).one()
        i1 = WorkItem(team_id=t.id, title="Conciliar", status="in_progress", assignee_user_id=mgr.id, evidence="file")
        i2 = WorkItem(team_id=t.id, title="Fechar", status="in_progress", assignee_user_id=mgr.id, evidence="approval")
        db.add_all([i1, i2])
        db.commit()
        ids = i1.id, i2.id
    assert client.post(f"/api/work-items/{ids[0]}", headers=env["h"]["mgr"], json={"action": "done"}).status_code == 400
    up = client.post(f"/api/work-items/{ids[0]}/files?name=conciliacao.xlsx", headers=env["h"]["mgr"], content=b"x")
    assert up.status_code == 200 and up.json()["files"][0]["name"] == "conciliacao.xlsx"
    assert client.post(f"/api/work-items/{ids[0]}", headers=env["h"]["mgr"], json={"action": "done"}).json()["status"] == "done"
    assert client.post(f"/api/work-items/{ids[1]}", headers=env["h"]["mgr"], json={"action": "done"}).json()["status"] == "review"
    assert client.post(f"/api/work-items/{ids[1]}", headers=env["h"]["mgr"], json={"action": "approve"}).status_code == 403
    assert client.post(f"/api/work-items/{ids[1]}", headers=env["h"]["mt2"], json={"action": "approve"}).json()["status"] == "done"


# ------------------------------------------------------------------ playbooks
def test_playbook_steps_people_and_employees_with_business_days(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    steps = [{"key": "extrair", "title": "Extrair os saldos", "assignee": {"type": "employee", "ref": slug}, "due": {"business_days": 1}},
             {"key": "conferir", "title": "Conferir", "assignee": {"type": "person", "ref": env["em"]["mgr"]},
              "depends_on": ["extrair"], "due": {"from": "extrair", "business_days": 1}, "evidence": "file"},
             {"key": "relatorio", "title": "Relatório", "assignee": {"type": "employee", "ref": slug},
              "depends_on": ["conferir"], "due": {"from": "conferir", "business_days": 2}, "criteria": "PDF com os saldos"}]
    bad = client.put("/api/playbooks", headers=env["h"]["own"], json={"name": "x", "team": env["team"], "steps": [
        {"key": "a", "assignee": {"type": "employee", "ref": slug}, "depends_on": ["b"]}]})
    assert bad.status_code == 400
    r = client.put("/api/playbooks", headers=env["h"]["own"], json={"name": f"fechamento_{env['n']}", "team": env["team"],
                                                                    "title": "Fechamento", "steps": steps,
                                                                    "schedule": "@bd 1 08:00", "run_title": "Fechamento {mes}/{ano}"})
    assert r.status_code == 200, r.text
    pb = r.json()
    assert pb["next_run_at"] and pb["schedule_text"].startswith("1º dia útil")
    run = client.post(f"/api/playbooks/{pb['name']}/runs", headers=env["h"]["own"], json={"anchor": "2026-04-02"}).json()
    by = {s["key"]: s for s in run["steps"]}
    assert by["extrair"]["status"] == "open" and by["conferir"]["status"] == "waiting" and by["relatorio"]["status"] == "waiting"
    assert by["extrair"]["due"].startswith("2026-04-06") and by["conferir"]["due"].startswith("2026-04-07")  # Sexta Santa
    assert by["relatorio"]["due"].startswith("2026-04-09")
    work()
    run = client.get(f"/api/playbook-runs/{run['id']}", headers=env["h"]["mgr"]).json()
    by = {s["key"]: s for s in run["steps"]}
    assert by["extrair"]["status"] == "done" and by["conferir"]["status"] == "in_progress"
    item = by["conferir"]["id"]
    assert "ETAPA PRÉVIA 'Extrair os saldos' CONCLUÍDA" in client.get(f"/api/work-items/{item}", headers=env["h"]["mgr"]).json()["body"]
    assert client.post(f"/api/work-items/{item}", headers=env["h"]["mgr"], json={"action": "done"}).status_code == 400  # sem evidência
    client.post(f"/api/work-items/{item}/files?name=saldos.xlsx", headers=env["h"]["mgr"], content=b"ok")
    client.post(f"/api/work-items/{item}", headers=env["h"]["mgr"], json={"action": "done", "note": "conferido, sem diferenças"})
    rel = by["relatorio"]["id"]
    assert task(client, env, rel)["status"] == "new"
    work()
    run = client.get(f"/api/playbook-runs/{run['id']}", headers=env["h"]["mgr"]).json()
    assert run["status"] == "done" and run["done"] == 3
    det = client.get(f"/api/playbooks/{pb['name']}", headers=env["h"]["mgr"]).json()
    assert det["runs"][0]["id"] == run["id"] and {h["key"] for h in det["history"]} == {"extrair", "conferir", "relatorio"}
    # agenda: a execução abre sozinha no 1º dia útil
    with SessionLocal() as db:
        p = db.query(Playbook).filter_by(name=pb["name"]).one()
        due = tasksmod._aware(p.next_run_at)
        assert playbooks.sweep(db, due + timedelta(minutes=1)) == 1
        r2 = db.query(PlaybookRun).filter_by(playbook_id=p.id).order_by(PlaybookRun.id.desc()).first()
        assert r2.title.startswith("Fechamento ") and r2.id != run["id"]
        assert playbooks.sweep(db, due + timedelta(minutes=2)) == 0  # uma vez só


# ------------------------------------------------------------------ gente de fora
@pytest.fixture()
def mail(monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(notify, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify, "_bg", _sync)
    FakeSMTP.sent = []
    return FakeSMTP


@pytest.fixture()
def send_floor(client):
    """O piso da empresa exige aprovação para envio externo; estes testes o baixam para "aviso" (e restauram)."""
    floor = {f["action_type"]: f for f in client.get("/api/admin/authority-floor", headers=ADMIN).json()}
    old = floor.get("send_external") or {"min_mode": "approve", "separation": False}
    client.put("/api/admin/authority-floor", headers=ADMIN,
               json={"items": [{"action_type": "send_external", "min_mode": "notify", "separation": False}]})
    yield
    client.put("/api/admin/authority-floor", headers=ADMIN, json={"items": [
        {"action_type": "send_external", "min_mode": old["min_mode"], "separation": bool(old.get("separation"))}]})


def test_promises_are_read_from_replies():
    base = date(2026, 10, 12)  # segunda
    got = correspondence.promises_in("Oi! Consigo enviar o contrato assinado até sexta. Abraço", base)
    assert got[0]["due"] == "2026-10-16" and "contrato assinado" in got[0]["text"]
    assert correspondence.promises_in("pago até 20/10", base)[0]["due"] == "2026-10-20"
    assert correspondence.promises_in("respondo amanhã", base) == []  # sem "até": não é prazo


def test_external_conversation_with_approved_template_wait_and_reply(client, env, rt, mail, send_floor):
    t = client.put("/api/message-templates", headers=env["h"]["own"], json={
        "name": "cobranca_documento", "subject": "Documento pendente: {{doc}}",
        "body": "Olá {{nome}}, ainda precisamos do {{doc}} para seguir.", "team": env["team"]})
    assert t.status_code == 200 and t.json()["vars"] == ["doc", "nome"] and t.json()["status"] == "draft"
    assert client.post("/api/message-templates/cobranca_documento/approve", headers=env["h"]["own"]).status_code == 403
    assert client.post("/api/message-templates/cobranca_documento/approve", headers=env["h"]["mgr"]).json()["status"] == "approved"
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    # a alçada libera os envios que vêm de um modelo aprovado (com aviso); texto livre continua pedindo aprovação
    rules = [{"action_type": "send_external", "mode": "notify", "conditions": [{"field": "template", "op": "!=", "value": ""}]}]
    assert client.put(f"/api/employees/{slug}/authority", headers=env["h"]["mgr"], json={"rules": rules}).status_code == 200
    args = {"to": "Ana@Fornecedor.com", "name": "Ana Souza", "channel": "email", "template": "cobranca_documento",
            "values": {"nome": "Ana", "doc": "contrato"}, "follow_up_days": 2, "wait_reply": True}
    tid = assign(client, env, slug, f"use send_external_message {json.dumps(args)}")
    work()
    assert task(client, env, tid)["status"] == "waiting_external"
    sent = mail.sent[-1]
    assert sent["To"] == "ana@fornecedor.com" and "contrato" in sent.get_content() and "[#H-" in sent["Subject"]
    conv = client.get(f"/api/tasks/{tid}/conversations", headers=env["h"]["own"]).json()[0]
    assert conv["status"] == "waiting" and conv["next_follow_up_at"] and conv["messages"][0]["template"] == "cobranca_documento"
    assert task(client, env, tid)["diagnosis"]["state"] == "external"
    # a resposta chega pela caixa do funcionário: a conversa é achada pelo código no assunto
    with SessionLocal() as db:
        th = db.query(ExternalThread).filter_by(task_id=tid).one()
        assert correspondence.match_email(db, f"Re: {sent['Subject']}", "ana@fornecedor.com").id == th.id
        assert correspondence.match_email(db, f"Re: {sent['Subject']}", "outro@x.com") is None
        correspondence.receive(db, th, "Segue em anexo. O aditivo envio até sexta.", "Re: ...")
        assert db.get(ExternalThread, th.id).promises[0]["text"].startswith("O aditivo")
    assert task(client, env, tid)["status"] == "new"
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and any("RESPOSTA de Ana Souza" in m["body"] for m in t["messages"])
    # texto livre: a alçada pede aprovação
    free = {"to": "joao@cliente.com", "channel": "email", "subject": "Oi", "body": "texto livre"}
    tid2 = assign(client, env, slug, f"use send_external_message {json.dumps(free)}")
    work()
    assert task(client, env, tid2)["status"] == "waiting_human"
    assert open_request(client, env, tid2)["payload"]["template"] == ""


def test_follow_up_without_reply_then_no_reply_wakes_the_task(client, env, rt, mail, send_floor):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    client.put(f"/api/employees/{slug}/authority", headers=env["h"]["mgr"], json={"rules": [{"action_type": "send_external", "mode": "auto"}]})
    args = {"to": "bia@cliente.com", "channel": "email", "subject": "Proposta", "body": "Segue a proposta.",
            "follow_up_days": 1, "max_follow_ups": 1, "wait_reply": True}
    tid = assign(client, env, slug, f"use send_external_message {json.dumps(args)}")
    work()
    assert task(client, env, tid)["status"] == "waiting_external"
    n = len(mail.sent)
    with SessionLocal() as db:
        th = db.query(ExternalThread).filter_by(task_id=tid).one()
        first = tasksmod._aware(th.next_follow_up_at)
        out = correspondence.sweep(db, first + timedelta(minutes=1))
        assert out["follow_ups"] == 1 and len(mail.sent) == n + 1 and "retomando" in mail.sent[-1].get_content()
        th = db.get(ExternalThread, th.id)
        out = correspondence.sweep(db, tasksmod._aware(th.next_follow_up_at) + timedelta(minutes=1))
        assert out["no_reply"] == 1
    t = task(client, env, tid)
    assert t["status"] == "new" and any("SEM RESPOSTA" in m["body"] for m in t["messages"])


def test_whatsapp_webhook_is_signed_and_replies_reach_the_conversation(client, env, rt, monkeypatch, send_floor):
    sent = []

    class FakeGraph:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            sent.append((url, json))
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(correspondence, "HTTP", lambda: FakeGraph())
    r = client.put("/api/admin/notifications", headers=ADMIN, json={"whatsapp_phone_id": "123", "whatsapp_token": "tok",
                                                                     "whatsapp_app_secret": "shh", "whatsapp_verify_token": "v1"})
    assert r.status_code == 200 and r.json()["whatsapp"]["configured"]
    ok = client.get("/hooks/whatsapp/1", params={"hub.mode": "subscribe", "hub.verify_token": "v1", "hub.challenge": "42"})
    assert ok.text == "42" and client.get("/hooks/whatsapp/1", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                                                         "hub.challenge": "1"}).status_code == 403
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    client.put(f"/api/employees/{slug}/authority", headers=env["h"]["mgr"], json={"rules": [{"action_type": "send_external", "mode": "auto"}]})
    tid = assign(client, env, slug, "use send_external_message " + json.dumps({"to": "+55 11 99999-0000", "channel": "whatsapp",
                                                                               "body": "oi", "wait_reply": True}))
    work()
    assert "fora da janela de 24 h" in task(client, env, tid)["result"]  # primeira mensagem: só com modelo do WhatsApp
    with SessionLocal() as db:  # dentro da janela (a pessoa escreveu antes): texto livre sai
        th = ExternalThread(task_id=tid, employee_id=db.get(EmployeeTask, tid).employee_id, channel="whatsapp",
                            contact="5511999990000", token="abc12345", status="waiting", wait_reply=False,
                            last_in_at=datetime.now(UTC))
        db.add(th)
        db.commit()
        thid = th.id
    payload = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.in1", "from": "5511999990000", "type": "text",
                                                                 "text": {"body": "Pode mandar"}}]}}]}]}
    raw = json.dumps(payload).encode()
    assert client.post("/hooks/whatsapp/1", content=raw, headers={"X-Hub-Signature-256": "sha256=errada"}).status_code == 401
    sig = "sha256=" + hmac.new(b"shh", raw, hashlib.sha256).hexdigest()
    assert client.post("/hooks/whatsapp/1", content=raw, headers={"X-Hub-Signature-256": sig}).status_code == 200
    with SessionLocal() as db:
        assert db.get(ExternalThread, thid).status == "replied"
        db.get(Organization, 1).notify = None
        db.commit()


# ------------------------------------------------------------------ agenda e reuniões
class FakeCalendar:
    def __init__(self):
        self.events = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, data=None, headers=None):
        if "oauth2" in url:
            out = {"access_token": "t", "expires_in": 3600}
        elif url.endswith("getSchedule"):
            out = {"value": [{"scheduleId": p, "scheduleItems": [{"status": "busy", "start": {"dateTime": "2026-10-13T12:00:00.0000000"},
                                                                  "end": {"dateTime": "2026-10-13T14:00:00.0000000"}}]}
                             for p in json["schedules"]]}
        else:
            self.events.append(json)
            out = {"id": "evt-1"}
        return httpx.Response(200, json=out, request=httpx.Request("POST", url))


def test_meeting_times_invites_minutes_and_actions_followed_up(client, env, rt, monkeypatch):
    cal = FakeCalendar()
    monkeypatch.setattr(calendar_api, "HTTP", lambda: cal)
    calendar_api._TOKENS.clear()
    r = client.put("/api/admin/notifications", headers=ADMIN, json={"calendar_provider": "microsoft", "ms_tenant": "t1",
                                                                     "ms_client_id": "c1", "ms_client_secret": "s1"})
    assert r.json()["calendar"]["provider"] == "microsoft"
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    with SessionLocal() as db:
        db.get(Organization, 1).timezone = "America/Sao_Paulo"
        db.commit()
    with SessionLocal() as db:
        agent = db.get(Agent, _agent(slug))
        tid = assign(client, env, slug, "marcar a reunião")
        g = tasksmod.gate(db, agent, tid, "find_meeting_times", "platform:find_meeting_times",
                          {"attendees": [env["em"]["req"]], "duration_min": 60, "from": "2026-10-13", "to": "2026-10-13"}, "", "platform")
        slots = json.loads(g["result"])["slots"]
        assert slots and slots[0] == "2026-10-13 11:00" and all(s[-5:] >= "11:00" for s in slots)  # 12h-14h UTC = 9h-11h em SP
    m = client.post("/api/meetings", headers=env["h"]["mgr"], json={"title": "Comitê de renovações", "starts_at": "2026-10-20 10:00",
                                                                   "attendees": [env["em"]["req"]], "team": env["team"], "employee": slug,
                                                                   "series": "comite", "schedule": True}).json()
    assert m["provider"] == "microsoft" and cal.events[-1]["subject"] == "Comitê de renovações"
    m = client.post(f"/api/meetings/{m['id']}/transcript", headers=env["h"]["mgr"], json={"text": "Ana: renovar ACME. Bia: revisar preço."}).json()
    assert m["minutes_task"] and m["status"] == "held"
    with SessionLocal() as db:
        agent = db.get(Agent, _agent(slug))
        g = tasksmod.gate(db, agent, m["minutes_task"], "record_minutes", "platform:record_minutes",
                          {"meeting_id": m["id"], "summary": "Renovar a ACME com revisão de preço.", "decisions": ["renovar ACME"],
                           "actions": [{"title": "Revisar a tabela de preço", "owner": env["em"]["req"], "due": "2026-10-23"},
                                       {"title": "Rascunhar o aditivo", "owner": slug}]}, "", "platform")
        assert "2 ação(ões)" in g["result"]
    d = client.get(f"/api/meetings/{m['id']}", headers=env["h"]["mgr"]).json()
    assert d["status"] == "minutes" and {a["kind"] for a in d["actions"]} == {"item", "task"}
    nxt = client.post("/api/meetings", headers=env["h"]["mgr"], json={"title": "Comitê de renovações", "starts_at": "2026-10-27 10:00",
                                                                     "team": env["team"], "series": "comite", "employee": slug,
                                                                     "prep_hours": 24}).json()
    assert {p["title"] for p in nxt["previous_open"]} >= {"Revisar a tabela de preço"}
    with SessionLocal() as db:  # briefing 24 h antes
        mt = db.get(Meeting, nxt["id"])
        from app.services import meetings
        assert meetings.sweep(db, tasksmod._aware(mt.starts_at) - timedelta(hours=23)) == 1
        db.get(Organization, 1).notify = None
        db.get(Organization, 1).timezone = "UTC"
        db.commit()
