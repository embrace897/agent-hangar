# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Fase G: base de conhecimento com as permissões de quem pergunta, navegador governado, governança de dados (DLP,
classificação, retenção, LGPD, barreira de informação) e propostas de alteração em documentos."""
import base64
import json
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO

import httpx
import pytest

from app import auth
from app.db import SessionLocal
from app.models import (
    Agent,
    Employee,
    EmployeeTask,
    KnowledgeDoc,
    KnowledgeGap,
    LlmConnection,
    Organization,
    Project,
    TaskFile,
    User,
)
from app.services import browser, docx_lite, governance, knowledge, task_files
from app.services import employee_tasks as tasksmod

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401


@pytest.fixture(autouse=True)
def _close_leftover_tasks():
    with SessionLocal() as db:
        start = db.query(EmployeeTask.id).order_by(EmployeeTask.id.desc()).limit(1).scalar() or 0
    yield
    with SessionLocal() as db:
        for t in db.query(EmployeeTask).filter(EmployeeTask.id > start, EmployeeTask.status.notin_(tasksmod.TERMINAL)):
            t.status = "cancelled"
        o = db.get(Organization, 1)
        o.governance = None
        db.commit()


def _agent(db, slug):
    return db.query(Agent).filter_by(slug=slug).one()


def _gate(slug, tid, name, args):
    with SessionLocal() as db:
        return tasksmod.gate(db, _agent(db, slug), tid, name, f"platform:{name}", args, "", "platform")


def _admin_session(n) -> dict:
    with SessionLocal() as db:
        u = User(email=f"adm-{n}@acme.com", name="Admin 2", org_role="admin", active=True)
        db.add(u)
        db.commit()
        return {"Authorization": f"Bearer {auth.create_session(db, u)}"}


# ------------------------------------------------------------------ base de conhecimento
def test_knowledge_search_uses_the_requesters_permissions_and_cites(client, env, rt):
    r = client.put("/api/knowledge/sources", headers=env["h"]["mgr"], json={
        "name": f"politicas_{env['n']}", "kind": "upload", "teams": [env["team"]], "review_days": 60})
    assert r.status_code == 200, r.text
    src = r.json()["name"]
    assert client.put("/api/knowledge/sources", headers=env["h"]["req"], json={"name": "x", "kind": "upload",
                                                                               "teams": [env["team"]]}).status_code == 403
    doc = client.post(f"/api/knowledge/sources/{src}/documents", headers=env["h"]["mgr"], json={
        "title": "Política de reembolso", "text": "Viagens internacionais exigem aprovação do diretor.\n\n"
                                                   "Hospedagem nacional até R$ 400 por noite."})
    assert doc.status_code == 200 and doc.json()["chunks"] >= 1
    hits = client.get("/api/knowledge/search", headers=env["h"]["req"], params={"q": "reembolso viagem internacional"}).json()
    assert hits and hits[0]["title"] == "Política de reembolso" and "diretor" in hits[0]["snippet"]
    assert client.get("/api/knowledge/search", headers=env["h"]["out"], params={"q": "reembolso viagem internacional"}).json() == []
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, 'use search_knowledge {"query": "hospedagem nacional limite"}')
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and "R$ 400" in t["result"] and '"n": 1' in t["result"]
    # sem resposta boa: vira lacuna para quem cuida do conteúdo
    client.get("/api/knowledge/search", headers=env["h"]["req"], params={"q": "política de home office"})
    gaps = client.get("/api/knowledge/gaps", headers=env["h"]["mgr"]).json()
    assert any("home office" in g["query"] for g in gaps)
    assert client.get("/api/knowledge/gaps", headers=env["h"]["req"]).status_code == 403
    with SessionLocal() as db:  # vencido: sem revisão há mais que o prazo da fonte
        d = db.query(KnowledgeDoc).filter_by(title="Política de reembolso").one()
        d.review_by = "2020-01-01"
        db.commit()
    assert client.get("/api/knowledge/search", headers=env["h"]["req"], params={"q": "reembolso"}).json()[0]["stale"] is True
    assert any(s["title"] == "Política de reembolso" for s in client.get("/api/knowledge/stale", headers=env["h"]["mgr"]).json())


class FakeConfluence:
    def __init__(self, email):
        self.email, self.puts = email, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, auth=None, params=None, **kw):
        pages = [
            {"id": "1", "title": "Tabela de comissões", "version": {"number": 3, "when": "2026-09-01T10:00:00Z"},
             "body": {"storage": {"value": "<p>Comissão de 5% sobre renovações.</p>"}}, "_links": {"webui": "/pages/1"},
             "restrictions": {"read": {"restrictions": {"user": {"results": [{"email": self.email}]}, "group": {"results": []}}}}},
            {"id": "2", "title": "Manual de vendas", "version": {"number": 1, "when": "2026-09-01T10:00:00Z"},
             "body": {"storage": {"value": "<p>Renovações começam 60 dias antes.</p>"}}, "_links": {"webui": "/pages/2"},
             "restrictions": {"read": {"restrictions": {"user": {"results": []}, "group": {"results": []}}}}}]
        return httpx.Response(200, json={"results": pages}, request=httpx.Request("GET", url))

    def put(self, url, auth=None, json=None, **kw):
        self.puts.append(json)
        return httpx.Response(200, json={}, request=httpx.Request("PUT", url))


def test_confluence_inherits_page_restrictions(client, env, monkeypatch):
    fake = FakeConfluence(env["em"]["req"])
    monkeypatch.setattr(knowledge, "HTTP", lambda: fake)
    r = client.put("/api/knowledge/sources", headers=env["h"]["mgr"], json={
        "name": f"wiki_{env['n']}", "kind": "confluence", "acl_mode": "inherit", "teams": [env["team"]],
        "config": {"base_url": "https://acme.atlassian.net/wiki", "space": "VEND", "email": "bot@acme.com", "api_token": "t0k"}})
    assert r.status_code == 200 and r.json()["config"]["api_token"] is True  # o token não volta
    out = client.post(f"/api/knowledge/sources/{r.json()['name']}/sync", headers=env["h"]["mgr"]).json()
    assert out["docs"] == 2 and not out["error"]
    req = {h["title"] for h in client.get("/api/knowledge/search", headers=env["h"]["req"], params={"q": "comissão renovações"}).json()}
    own = {h["title"] for h in client.get("/api/knowledge/search", headers=env["h"]["own"], params={"q": "comissão renovações"}).json()}
    assert req == {"Tabela de comissões", "Manual de vendas"} and own == {"Manual de vendas"}


# ------------------------------------------------------------------ navegador governado
class FakeBrowser:
    def __init__(self):
        self.calls, self.captcha = [], False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

    def post(self, url, json=None, headers=None):
        name, args = json["params"]["name"], json["params"]["arguments"]
        self.calls.append((name, args))
        out = {"url": "https://portal.banco.test/home", "title": "Banco", "captcha": self.captcha}
        if name == "open":
            host = args["url"].split("/")[2]
            if not any(host == d or host.endswith("." + d) for d in args["allowed"]):
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [
                    {"type": "text", "text": f"{host} não está entre os domínios liberados"}]}}, request=httpx.Request("POST", url))
        if name == "read":
            out.update(text="Saldo: R$ 1.000,00\nBoleto 123", elements=[{"role": "button", "name": "Pagar boleto"}])
        if name == "screenshot":
            out["screenshot"] = base64.b64encode(b"\x89PNG fake").decode()
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": out}},
                              request=httpx.Request("POST", url))


def test_governed_browser_domains_secrets_commit_gate_captcha_and_procedures(client, env, rt, monkeypatch):
    fake = FakeBrowser()
    monkeypatch.setattr(browser, "HTTP", lambda timeout=60: fake)
    monkeypatch.setattr(browser, "_UP", (0.0, False))
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    assert client.put(f"/api/employees/{slug}/browser-domains", headers=env["h"]["own"], json={"domains": ["banco.test"]}).json()["ready"]
    client.put("/api/secrets", headers=ADMIN, json={"name": "banco-senha", "value": "s3nh4-real"})
    tid = assign(client, env, slug, "pagar o boleto")
    g = _gate(slug, tid, "browser_open", {"url": "https://evil.test/x"})
    assert "não está entre os domínios liberados" in g["result"]
    assert _gate(slug, tid, "browser_open", {"url": "https://portal.banco.test/login"})["status"] == "allowed"
    g = _gate(slug, tid, "browser_fill", {"target": "Senha", "value": "secret://banco-senha"})
    assert g["status"] == "allowed" and "s3nh4" not in g["result"]
    assert fake.calls[-1] == ("fill", {"target": "Senha", "value": "s3nh4-real", "allowed": ["banco.test"]})  # o cofre preenche
    events = task(client, env, tid)["events"]
    assert any(e["kind"] == "browser" and e["payload"].get("value") == "[segredo]" for e in events)
    assert _gate(slug, tid, "browser_click", {"target": "Entrar"})["status"] == "allowed"  # navegar não pede alçada
    g = _gate(slug, tid, "browser_click", {"target": "Pagar boleto"})
    assert g["status"] == "waiting" and g["action_type"] == "financial"
    req = open_request(client, env, tid)
    assert "Tela: tela-" in req["rationale"] and "Saldo" in req["rationale"] and req["payload"]["target"] == "Pagar boleto"
    first = client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).json()
    assert first["mode"] == "approve_2" and first["status"] == "open"  # pagamento: o piso exige duas pessoas
    client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mt2"], json={"decision": "approve"})
    g = _gate(slug, tid, "browser_click", {"target": "Pagar boleto"})
    assert g["status"] == "allowed" and fake.calls[-1][0] == "click"
    fake.captcha = True  # CAPTCHA: uma pessoa resolve
    g = _gate(slug, tid, "browser_read", {})
    assert g["status"] == "waiting" and "CAPTCHA" in open_request(client, env, tid)["question"]
    fake.captcha = False
    # procedimento a partir do que deu certo na tarefa, e a execução com variáveis
    pr = client.post("/api/procedures/from-task", headers=env["h"]["own"], json={"task_id": tid, "name": f"boleto_{env['n']}",
                                                                                 "team": env["team"]})
    assert pr.status_code == 200, pr.text
    assert pr.json()["domains"] == ["banco.test"] and pr.json()["steps"][1]["value"] == ""  # o segredo não fica no passo
    p = client.put("/api/procedures", headers=env["h"]["own"], json={
        "name": f"extrato_{env['n']}", "domains": ["banco.test"], "team": env["team"], "steps": [
            {"action": "open", "target": "https://portal.banco.test/extrato?conta={{conta}}"},
            {"action": "click", "target": "Consultar"}]}).json()
    assert p["vars"] == ["conta"]
    tid2 = assign(client, env, slug, "extrato")
    g = _gate(slug, tid2, "run_procedure", {"name": p["name"], "values": {"conta": "123"}})
    assert g["status"] == "allowed" and "Saldo" in g["result"]
    assert ("open", {"url": "https://portal.banco.test/extrato?conta=123", "allowed": ["banco.test"]}) in fake.calls
    assert "faltam valores" in _gate(slug, tid2, "run_procedure", {"name": p["name"], "values": {}})["result"]


# ------------------------------------------------------------------ governança de dados
CPF = "529.982.247-25"


def test_dlp_masks_what_goes_to_the_model_and_executes_with_real_values(client, env, rt):
    r = client.put("/api/governance", headers=ADMIN, json={"dlp": {"mask": ["cpf", "card"], "block_outbound": ["card"]}})
    assert r.status_code == 200 and r.json()["dlp"]["mask"] == ["card", "cpf"]
    assert client.put("/api/governance", headers=env["h"]["mgr"], json={"dlp": {"mask": []}}).status_code == 403
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, f'use crm_lookup {{"cpf": "{CPF}"}}')
    work()
    sent = rt.calls[-1][2]
    assert "[CPF_1]" in sent and CPF not in sent  # o modelo não viu o CPF
    t = task(client, env, tid)
    assert t["status"] == "done" and CPF in t["result"] and "[CPF_1]" not in t["result"]  # as pessoas veem o real
    with SessionLocal() as db:
        g = tasksmod.gate(db, _agent(db, slug), tid, "crm_lookup", "http:crm_lookup:GET", {"cpf": "[CPF_1]"}, "")
        assert g["args"] == {"cpf": CPF}  # o runtime executa com o valor real
    with pytest.raises(Exception, match="cartão|card"):
        with SessionLocal() as db:
            governance.check_outbound(db, "pague com 4111 1111 1111 1111")
    assert governance.detect("CPF 111.111.111-11 inválido") == {}  # dígitos verificadores errados não contam


def test_classification_blocks_models_not_cleared_for_the_data(client, env, rt, monkeypatch):
    slug = hire(client, env)
    with SessionLocal() as db:
        db.add(LlmConnection(name=f"c-{env['n']}", base_url="https://llm.test/v1", model_name="m", protocol="openai",
                             max_classification="internal"))
        db.commit()
    from app.services import common
    real = common.spec_of
    monkeypatch.setattr(common, "spec_of", lambda a, v=None: {**real(a, v), "llm": {"connection": f"c-{env['n']}"}})
    tid = assign(client, env, slug, "algo") if False else None
    with SessionLocal() as db:
        e = db.query(Employee).filter_by(agent_id=_agent(db, slug).id).one()
        t = tasksmod.create_task(db, e, "dados de saúde", "x")
        tid = t.id
        assert governance.llm_blocked(db, t, _agent(db, slug)) == ""
        task_files.store(db, t, "laudo.txt", b"CID F32", "input", "x")
        f = db.query(TaskFile).filter_by(task_id=tid).one()
        f.classification = "restricted"
        db.commit()
        assert governance.effective(db, t) == "restricted"
        assert "só recebe até 'internal'" in governance.llm_blocked(db, t, _agent(db, slug))
    r = client.put(f"/api/tasks/{tid}/classification", headers=env["h"]["own"], json={"classification": "public"})
    assert r.status_code == 403  # baixar é com o admin
    assert client.put(f"/api/governance/llm/c-{env['n']}", headers=ADMIN, json={"max_classification": "restricted"}).status_code == 200


def test_retention_purges_content_but_respects_legal_hold(client, env, rt):
    client.put("/api/governance", headers=ADMIN, json={"retention": {"internal": 30}})
    slug = hire(client, env)
    with SessionLocal() as db:
        e = db.query(Employee).filter_by(agent_id=_agent(db, slug).id).one()
        old = datetime.now(UTC) - timedelta(days=40)
        a = tasksmod.create_task(db, e, "antiga", "conteúdo sensível", status="done")
        b = tasksmod.create_task(db, e, "em litígio", "guardar", status="done")
        for t in (a, b):
            t.finished_at, t.result = old, "resultado"
        b.legal_hold = True
        task_files.store(db, a, "x.txt", b"dado", "input", "x")
        db.commit()
        assert governance.retention_sweep(db) >= 1
        a, b = db.get(EmployeeTask, a.id), db.get(EmployeeTask, b.id)
        assert a.purged_at and a.body == governance.REDACTED and db.query(TaskFile).filter_by(task_id=a.id).count() == 0
        assert b.purged_at is None and b.body == "guardar"


def test_subject_request_finds_and_erases_with_four_eyes(client, env, rt):
    slug = hire(client, env)
    with SessionLocal() as db:
        e = db.query(Employee).filter_by(agent_id=_agent(db, slug).id).one()
        t = tasksmod.create_task(db, e, "cadastro", f"cliente Maria, CPF {CPF.replace('.', '').replace('-', '')}", status="done")
        tid = t.id
    r = client.post("/api/subject-requests", headers=ADMIN, json={"subject": CPF, "kind": "access"}).json()
    assert r["status"] == "done" and any(x["id"] == tid for x in r["report"]["found"]["tasks"])
    er = client.post("/api/subject-requests", headers=ADMIN, json={"subject": CPF, "kind": "erase"}).json()
    assert er["status"] == "open"
    assert client.post(f"/api/subject-requests/{er['id']}/erase", headers=ADMIN, json={}).status_code == 403  # quem pediu
    other = _admin_session(env["n"])
    assert client.post(f"/api/subject-requests/{er['id']}/erase", headers=other, json={}).json()["status"] == "done"
    with SessionLocal() as db:
        assert "[REMOVIDO-LGPD]" in db.get(EmployeeTask, tid).body


def test_information_barrier_and_break_glass(client, env, rt):
    slug = hire(client, env)
    p = client.post("/api/projects", headers=env["h"]["mgr"], json={"name": f"M&A {env['n']}", "team": env["team"]}).json()
    g = client.put(f"/api/projects/{p['slug']}/governance", headers=env["h"]["mgr"],
                   json={"confidential": True, "members": [env["em"]["mgr"]], "classification": "restricted"}).json()
    assert g["confidential"] and g["members"] == [env["em"]["mgr"]]
    with SessionLocal() as db:
        e = db.query(Employee).filter_by(agent_id=_agent(db, slug).id).one()
        t = tasksmod.create_task(db, e, "alvo da aquisição", "segredo", project_id=db.query(Project).filter_by(slug=p["slug"]).one().id)
        tid = t.id
    assert client.get(f"/api/tasks/{tid}", headers=env["h"]["own"]).status_code in (403, 404)  # do time, mas fora da barreira
    assert client.get(f"/api/tasks/{tid}", headers=env["h"]["mgr"]).status_code == 200
    adm = _admin_session(env["n"] + "b")
    assert client.get(f"/api/tasks/{tid}", headers=adm).status_code in (403, 404)  # nem o admin, sem justificativa
    assert client.post(f"/api/projects/{p['slug']}/break-glass", headers=adm, json={"reason": "curto"}).status_code == 400
    assert client.post(f"/api/projects/{p['slug']}/break-glass", headers=adm, json={"reason": "auditoria do contrato 42/2026"}).status_code == 200
    assert client.get(f"/api/tasks/{tid}", headers=adm).status_code == 200


# ------------------------------------------------------------------ propostas de alteração em documentos
def test_document_change_partial_approval_and_docx_redline(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "revisar a política")
    with SessionLocal() as db:
        task_files.store(db, db.get(EmployeeTask, tid), "politica.txt",
                         "Prazo de pagamento: 30 dias.\n\nMulta de 2%.\n\nForo de São Paulo.".encode(), "input", "x", "text/plain")
        db.commit()
    g = _gate(slug, tid, "propose_document_change", {"document": "file:politica.txt", "summary": "alinhar à nova política",
                                                     "edits": [{"find": "30 dias", "replace": "45 dias"},
                                                               {"find": "Multa de 2%.", "replace": "Multa de 1%."}]})
    assert g["status"] == "waiting"
    req = open_request(client, env, tid)
    assert req["kind"] == "doc_change" and len(req["payload"]["hunks"]) == 2
    first = req["payload"]["hunks"][0]
    r = client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve", "edit": {"accept": [first["id"]]}})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        f = db.query(TaskFile).filter_by(task_id=tid, name="politica.txt").order_by(TaskFile.version.desc()).first()
        assert f.version == 2 and "45 dias" in f.data.decode() and "Multa de 2%" in f.data.decode()
    # DOCX: volta com controle de alterações
    with SessionLocal() as db:
        task_files.store(db, db.get(EmployeeTask, tid), "contrato.docx", docx_lite.plain(["Cláusula 1: vigência de 12 meses.",
                                                                                         "Cláusula 2: reajuste pelo IPCA."]),
                         "input", "x")
        db.commit()
    g = _gate(slug, tid, "propose_document_change", {"document": "file:contrato.docx", "summary": "índice",
                                                     "edits": [{"find": "IPCA", "replace": "IGP-M"}]})
    req = open_request(client, env, tid)
    # o documento mudou antes da aprovação: nada é aplicado
    with SessionLocal() as db:
        task_files.store(db, db.get(EmployeeTask, tid), "contrato.docx", docx_lite.plain(["Outro texto."]), "input", "x")
        db.commit()
    assert client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 400
    with SessionLocal() as db:
        task_files.store(db, db.get(EmployeeTask, tid), "contrato.docx", docx_lite.plain(["Cláusula 1: vigência de 12 meses.",
                                                                                         "Cláusula 2: reajuste pelo IPCA."]),
                         "input", "x")
        db.commit()
    assert client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 200
    with SessionLocal() as db:
        f = db.query(TaskFile).filter_by(task_id=tid, name="contrato.docx").order_by(TaskFile.version.desc()).first()
        xml = zipfile.ZipFile(BytesIO(f.data)).read("word/document.xml").decode()
        assert "<w:del " in xml and "<w:ins " in xml and "IGP-M" in xml and "aprovado por" in xml
        assert docx_lite.paragraphs(f.data)[0] == "Cláusula 1: vigência de 12 meses."
    _ = json, KnowledgeGap


def test_document_change_accepts_a_single_find_replace_and_explains_what_is_missing(client, env, rt):
    """Achados do piloto com LLM de verdade: o modelo manda uma troca solta (find/replace) ou esquece o que muda."""
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "revisar a política")
    with SessionLocal() as db:
        task_files.store(db, db.get(EmployeeTask, tid), "politica.txt", b"Prazo: 30 dias.\n\nForo: SP.", "input", "x", "text/plain")
        db.commit()
    g = _gate(slug, tid, "propose_document_change", {"document": "file:politica.txt", "summary": "prazo"})
    assert g["status"] == "allowed" and "find" in g["result"] and "replace" in g["result"]
    g = _gate(slug, tid, "propose_document_change", {"document": "file:politica.txt", "summary": "prazo",
                                                     "find": "30 dias", "replace": "45 dias"})
    assert g["status"] == "waiting" and open_request(client, env, tid)["payload"]["hunks"][0]["after"] == ["Prazo: 45 dias."]


def test_knowledge_source_can_be_removed_by_who_maintains_it(client, env):
    src = client.put("/api/knowledge/sources", headers=env["h"]["mgr"], json={
        "name": f"rem_{env['n']}", "kind": "upload", "teams": [env["team"]]}).json()["name"]
    client.post(f"/api/knowledge/sources/{src}/documents", headers=env["h"]["mgr"], json={"title": "Doc", "text": "conteúdo único zq9"})
    from app.models import Team
    with SessionLocal() as db:
        who = knowledge.principals(db, None, [db.query(Team).filter_by(slug=env["team"]).one().id])
        assert knowledge.search(db, "zq9", who, 5)
    assert client.delete(f"/api/knowledge/sources/{src}", headers=env["h"]["req"]).status_code == 403
    r = client.delete(f"/api/knowledge/sources/{src}", headers=env["h"]["mgr"])
    assert r.status_code == 200 and r.json()["docs"] == 1
    assert all(s["name"] != src for s in client.get("/api/knowledge/sources", headers=ADMIN).json())
    with SessionLocal() as db:
        assert not knowledge.search(db, "zq9", who, 5)
