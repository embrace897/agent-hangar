# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Sistemas de chamados como porta de entrada: Jira (Cloud, API v3) alimenta a fila; o andamento, o resultado e os
anexos voltam ao chamado; comentários novos chegam à conversa da tarefa; webhook; quatro olhos para pôr no ar."""
import base64

import httpx

from app.db import SessionLocal
from app.models import EmployeeTask, TaskMessage, WorkItem
from app.services import task_files, tickets

from .conftest import ADMIN
from .test_employees import env, hire, make_active, rt, work  # noqa: F401
from .test_phase_g import _close_leftover_tasks  # noqa: F401


def _adf(text):
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


class FakeJira:
    """O suficiente da API REST v3 do Jira Cloud: myself, search/jql, issue, comment, transitions, attachments."""

    def __init__(self):
        self.issues = {}
        self.comments, self.transitions_done, self.attachments, self.auth = [], [], [], []

    def add(self, key, summary, labels=(), done=False, desc="Fornecedor mandou a nota 991.", priority="High"):
        self.issues[key] = {"id": str(10000 + len(self.issues)), "key": key, "fields": {
            "summary": summary, "description": _adf(desc), "issuetype": {"name": "Task"}, "labels": list(labels),
            "priority": {"name": priority}, "reporter": {"emailAddress": "cliente@fornecedor.com"},
            "status": {"name": "Done" if done else "To Do", "statusCategory": {"key": "done" if done else "new"}},
            "updated": f"2026-10-12T10:{len(self.issues):02d}:00.000+0000", "comment": {"comments": []}}}

    def remote_comment(self, key, text, author="Cliente"):
        i = self.issues[key]
        i["fields"]["comment"]["comments"].append({"id": str(900 + len(i["fields"]["comment"]["comments"])),
                                                   "author": {"displayName": author}, "body": _adf(text), "created": "x"})
        i["fields"]["updated"] = i["fields"]["updated"][:-6] + "1+0000" if not i["fields"]["updated"].endswith("1+0000") \
            else i["fields"]["updated"][:-6] + "2+0000"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _resp(self, url, data=None, status=200):
        return httpx.Response(status, json=data if data is not None else {}, request=httpx.Request("GET", url))

    def request(self, method, url, headers=None, json=None, params=None, files=None, **kw):
        self.auth.append((headers or {}).get("Authorization", ""))
        path = url.split("://", 1)[1].split("/", 1)[1]
        if path == "rest/api/3/myself":
            return self._resp(url, {"emailAddress": "bot@acme.com"})
        if path == "rest/api/3/search/jql":
            return self._resp(url, {"issues": list(self.issues.values()), "isLast": True})
        parts = path.split("/")
        key = parts[4] if len(parts) > 4 else ""
        i = self.issues.get(key) or next((x for x in self.issues.values() if x["id"] == key), None)
        if i is None:
            return self._resp(url, {"errorMessages": ["não existe"]}, 404)
        if len(parts) == 5:
            return self._resp(url, i)
        if parts[5] == "comment":
            self.comments.append((i["key"], json["body"]))
            return self._resp(url, {"id": "1"}, 201)
        if parts[5] == "transitions" and method == "GET":
            return self._resp(url, {"transitions": [{"id": "21", "name": "Start", "to": {"name": "In Progress"}},
                                                    {"id": "31", "name": "Resolve", "to": {"name": "Done"}}]})
        if parts[5] == "transitions":
            self.transitions_done.append((i["key"], json["transition"]["id"]))
            return self._resp(url, {}, 204)
        if parts[5] == "attachments":
            self.attachments.append((i["key"], files["file"][0], (headers or {}).get("X-Atlassian-Token")))
            return self._resp(url, [{"id": "a1"}])
        return self._resp(url, {}, 404)


def _text(adf):
    return tickets._adf_text(adf).strip()


def _setup(client, env, slug):
    q = client.put("/api/queues", headers=env["h"]["mgr"], json={
        "name": f"Suporte {env['n']}", "team": env["team"], "categories": [
            {"name": "Nota fiscal", "keywords": ["nota"], "sla_hours": 4, "route": {"type": "employee", "refs": [slug]}},
            {"name": "Geral", "keywords": [], "sla_hours": 24, "route": {"type": "people"}}], "default_category": "Geral"})
    assert q.status_code == 200, q.text
    client.put("/api/secrets", headers=ADMIN, json={"name": f"jira-{env['n']}", "value": "tok-jira-123"})
    return q.json()["slug"]


def test_jira_tickets_feed_the_queue_and_the_work_goes_back(client, env, rt, monkeypatch):
    fake = FakeJira()
    monkeypatch.setattr(tickets, "HTTP", lambda timeout=30: fake)
    monkeypatch.setattr(tickets, "_check_base", lambda kind, url: url.rstrip("/"))  # o host de teste não resolve
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    queue = _setup(client, env, slug)
    body = {"name": f"jira_{env['n']}", "kind": "jira", "queue": queue, "base_url": "https://acme.atlassian.net",
            "auth": {"user": "bot@acme.com", "secret": f"secret://jira-{env['n']}"},
            "config": {"jql": "project = OPS", "category_rules": [{"field": "label", "value": "nota", "category": "Nota fiscal"}],
                       "transitions": {"employee": "In Progress", "done": "Done"}}}
    assert client.put("/api/tickets/connectors", headers=env["h"]["req"], json=body).status_code == 403  # não mantém o time
    bad = client.put("/api/tickets/connectors", headers=env["h"]["mgr"], json={**body, "auth": {"user": "x", "secret": "tok-cru"}})
    assert bad.status_code == 400 and "cofre" in bad.text  # a credencial vai no cofre, nunca na configuração
    c = client.put("/api/tickets/connectors", headers=env["h"]["mgr"], json=body)
    assert c.status_code == 200, c.text
    name = c.json()["name"]
    assert c.json()["status"] == "draft"
    assert client.post(f"/api/tickets/connectors/{name}/sync", headers=env["h"]["mgr"]).status_code == 400  # rascunho
    assert client.post(f"/api/tickets/connectors/{name}/approve", headers=env["h"]["mgr"]).status_code == 403  # quatro olhos
    assert client.post(f"/api/tickets/connectors/{name}/approve", headers=env["h"]["mt2"]).json()["status"] == "active"
    fake.add("OPS-1", "Lançar a nota do fornecedor", labels=["nota"])
    fake.add("OPS-2", "Já resolvido", done=True)
    t = client.post(f"/api/tickets/connectors/{name}/test", headers=env["h"]["mgr"]).json()
    assert t["ok"] and t["as"] == "bot@acme.com"
    assert fake.auth[-1] == "Basic " + base64.b64encode(b"bot@acme.com:tok-jira-123").decode()  # o cofre entrega o token
    r = client.post(f"/api/tickets/connectors/{name}/sync", headers=env["h"]["mgr"]).json()
    assert r["created"] == 1 and r["ignored"] == 1  # o chamado já fechado não entra
    with SessionLocal() as db:
        item = db.query(WorkItem).filter(WorkItem.source_ref == "jira:OPS-1").one()
        assert item.category == "Nota fiscal" and item.priority == 1 and item.status == "employee"
        assert "https://acme.atlassian.net/browse/OPS-1" in item.body and "nota 991" in item.body
        tid = item.employee_task_id
    # o andamento voltou ao chamado: comentário e transição
    assert any(k == "OPS-1" and "Em atendimento por" in _text(b) and "[Agent Hangar]" in _text(b) for k, b in fake.comments)
    assert ("OPS-1", "21") in fake.transitions_done
    detail = client.get(f"/api/work-items/{item.id}", headers=env["h"]["mgr"]).json()
    assert detail["ticket"]["key"] == "OPS-1" and detail["ticket"]["kind"] == "jira"
    # um comentário novo no Jira chega à conversa da tarefa do Digital employee
    fake.remote_comment("OPS-1", "Atenção: o valor certo é R$ 1.500,00")
    client.post(f"/api/tickets/connectors/{name}/sync", headers=env["h"]["mgr"])
    with SessionLocal() as db:
        msgs = db.query(TaskMessage).filter(TaskMessage.task_id == tid).all()
        assert any("R$ 1.500,00" in m.body and m.author.startswith("jira:") for m in msgs)
        n_comments = len(fake.comments)
        task_files.store(db, db.get(EmployeeTask, tid), "lancamento.csv", b"nota,valor\n991,1500\n", "output", "agent:x", "text/csv")
        db.commit()
    work()  # o Digital employee termina
    client.post(f"/api/tickets/connectors/{name}/sync", headers=env["h"]["mgr"])
    done = [b for k, b in fake.comments[n_comments:] if k == "OPS-1"]
    assert done and "Concluído por" in _text(done[-1])
    assert ("OPS-1", "31") in fake.transitions_done and ("OPS-1", "lancamento.csv", "no-check") in fake.attachments
    # nossos comentários não voltam como comentário novo; nada é mandado duas vezes
    before = (len(fake.comments), len(fake.transitions_done), len(fake.attachments))
    client.post(f"/api/tickets/connectors/{name}/sync", headers=env["h"]["mgr"])
    assert (len(fake.comments), len(fake.transitions_done), len(fake.attachments)) == before


def test_jira_webhook_and_ticket_closed_outside(client, env, rt, monkeypatch):
    fake = FakeJira()
    monkeypatch.setattr(tickets, "HTTP", lambda timeout=30: fake)
    monkeypatch.setattr(tickets, "_check_base", lambda kind, url: url.rstrip("/"))
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    queue = _setup(client, env, slug)
    name = client.put("/api/tickets/connectors", headers=env["h"]["mgr"], json={
        "name": f"jw_{env['n']}", "kind": "jira", "queue": queue, "base_url": "https://acme.atlassian.net",
        "auth": {"user": "bot@acme.com", "secret": f"secret://jira-{env['n']}"}, "config": {"jql": "project = OPS"}}).json()["name"]
    client.post(f"/api/tickets/connectors/{name}/approve", headers=env["h"]["mt2"])
    hook = client.post(f"/api/tickets/connectors/{name}/webhook-token", headers=env["h"]["mgr"]).json()["url"]
    path = hook.split("://", 1)[1].split("/", 1)[1]
    fake.add("OPS-7", "Ajustar o cadastro do cliente", desc="Atualizar o endereço de entrega.")
    assert client.post("/" + path.replace("token=", "token=x"), json={"issue": {"key": "OPS-7"}}).status_code == 401
    r = client.post("/" + path, json={"webhookEvent": "jira:issue_created", "issue": {"key": "OPS-7"}})
    assert r.status_code == 200 and r.json()["tickets"] == 1
    with SessionLocal() as db:
        item = db.query(WorkItem).filter(WorkItem.source_ref == "jira:OPS-7").one()
        assert item.status == "open" and item.category == "Geral"  # sem regra: a fila classifica (pessoas)
    assert any("Recebido na fila" in _text(b) for k, b in fake.comments if k == "OPS-7")
    fake.issues["OPS-7"]["fields"]["status"]["statusCategory"]["key"] = "done"  # fechado lá no Jira
    fake.issues["OPS-7"]["fields"]["updated"] = "2026-10-12T11:00:00.000+0000"
    client.post("/" + path, json={"issue": {"key": "OPS-7"}})
    with SessionLocal() as db:
        assert db.get(WorkItem, item.id).status == "cancelled"


def test_other_systems_map_their_tickets():
    """Linear, Asana e ServiceNow: o chamado de cada um vira o mesmo formato (o resto do fluxo é o mesmo do Jira)."""
    from app.models import TicketConnector
    lin = tickets.Linear(TicketConnector(kind="linear", base_url="", auth={}, config={"team": "OPS"}), "k")
    t = lin._ticket({"id": "u1", "identifier": "OPS-3", "url": "https://linear.app/x/OPS-3", "title": "Nota", "description": "d",
                     "priority": 1, "updatedAt": "2026", "state": {"type": "completed"}, "creator": {"email": "a@b.c"},
                     "labels": {"nodes": [{"name": "nota"}]}, "comments": {"nodes": [{"id": "c1", "body": "oi", "user": {"name": "Ana"}}]}})
    assert (t["key"], t["priority"], t["closed"], t["labels"], t["comments"][0]["author"]) == ("OPS-3", "Urgent", True, ["nota"], "Ana")
    asa = tickets.Asana(TicketConnector(kind="asana", base_url="", auth={}, config={"project": "1"}), "k")
    t = asa._ticket({"gid": "9", "name": "Pedido", "notes": "n", "completed": False, "permalink_url": "u", "tags": [{"name": "x"}]})
    assert (t["id"], t["title"], t["closed"], t["labels"]) == ("9", "Pedido", False, ["x"])
    sn = tickets.ServiceNow(TicketConnector(kind="servicenow", base_url="https://acme.service-now.com", auth={"user": "u"},
                                            config={}), "p")
    t = sn._ticket({"sys_id": {"value": "abc", "display_value": "abc"}, "number": {"value": "INC0010", "display_value": "INC0010"},
                    "short_description": {"display_value": "Sem acesso ao ERP"}, "state": {"value": "6", "display_value": "Resolved"},
                    "priority": {"display_value": "2 - High"}})
    assert (t["key"], t["title"], t["closed"], t["priority"]) == ("INC0010", "Sem acesso ao ERP", True, "2 - High")
    assert tickets.PRIO[t["priority"].lower()] == 2 and sn.headers()["Authorization"].startswith("Basic ")
    assert "incident.do?sys_id=abc" in t["url"]
    assert tickets.Linear(TicketConnector(kind="linear", auth={}, config={}), "lin_k").headers()["Authorization"] == "lin_k"
