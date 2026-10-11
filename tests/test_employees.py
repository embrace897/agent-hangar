"""Digital employee (F1): contratação em modo self com perguntas ao dono, motor de alçada (cargo, padrão do nível, piso
da empresa, condições), gate, tarefas com checkpoints, humano no circuito (aprovar, aprovar editando, recusar,
instruir, responder, duas pessoas, separação de funções, prazo que escala e depois expira em "não fazer"), ciclo de
vida (experiência, admissão, pausa, desligamento), permissões, relatórios, MCP e a área do admin.

O runtime do agente é simulado (FakeRuntime): ele interpreta "use <tool> {json}" e "ask: <pergunta>" e chama o MESMO
gate da central que o runtime real chama via /internal/gate."""
import json
import re
from datetime import timedelta

import httpx
import pytest

from app import auth, config
from app import services as svc
from app.db import SessionLocal
from app.models import ActionCatalog, AuthorityRule, Employee, EmployeeTask, HumanRequest, TaskCheckpoint, User, now
from app.services import employee_gate as gatemod
from app.services import employee_tasks as tasksmod
from app.services import employees as empmod

from .conftest import ADMIN

MCP_HDR = {"Accept": "application/json, text/event-stream"}
TOOLS = {"send_email": "http:send_email:POST", "pay_invoice": "http:pay_invoice:POST",
         "crm_lookup": "http:crm_lookup:GET", "delete_record": "http:delete_record:DELETE",
         "update_crm": "http:update_crm:PATCH"}


def _session(email: str) -> dict:
    with SessionLocal() as db:
        u = db.query(User).filter(User.email == email).one()
        return {"Authorization": f"Bearer {auth.create_session(db, u)}"}


class FakeRuntime:
    """Simula o runtime do agente: chama o gate da central antes de cada ferramenta, como o runtime real."""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None, timeout=None):
        slug = re.search(r"agent-(.+?)-(?:prod|stage)", url).group(1)
        task_id = int(headers["X-Hangar-Task"])
        last = next(m["content"] for m in reversed(json["messages"]) if m["role"] == "user")
        self.calls.append((slug, task_id, last))
        content = self._act(slug, task_id, last)
        body = {"choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"total_tokens": 10, "cost_usd": 0.01}, "x_trace": []}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    def _act(self, slug, task_id, last):
        with SessionLocal() as db:
            agent = svc.get_agent(db, slug)
            reply = re.match(r"(DECISÃO|CAPACIDADE|PLANO|PLAN|TAREFA REPASSADA|HANDED-OFF TASK|RESPOSTA de)", last)
            if re.match(r"(DECISÃO|CAPACIDADE|PLANO|PLAN) #\d+:? .{0,60}?(RECUSADO|EXPIROU|INSTRUÇÃO)", last, re.S):
                return "não executei a ação; encerrando com o que ficou pendente"
            nd = re.search(r"\bneed:\s*(.+)", last)
            if nd and "CAPACIDADE" not in last:
                g = tasksmod.gate(db, agent, task_id, "request_capability", "hangar:request_capability",
                                  {"need": nd.group(1).strip()}, "", "capability")
                return f"[[HANGAR_WAITING:{g['request_id']}]] {g['message']}"
            if re.match(r"(PLANO|PLAN) #\d+ (APROVADO|APPROVED)", last):  # plano aprovado: executa as etapas e marca como feitas
                steps = re.findall(r"^\d+\. (.+)$", last, re.M)
                tasksmod.gate(db, agent, task_id, "update_plan", "hangar:update_plan",
                              {"steps": [{"title": s, "status": "done"} for s in steps]}, "", "plan")
                return f"plano executado: {len(steps)} etapas"
            pl = re.search(r"\bplan:\s*(.+)", last)
            if pl and not reply:
                g = tasksmod.gate(db, agent, task_id, "update_plan", "hangar:update_plan",
                                  {"steps": [s.strip() for s in pl.group(1).split(";") if s.strip()]}, "", "plan")
                if g["status"] == "waiting":
                    return f"[[HANGAR_WAITING:{g['request_id']}]] {g['message']}"
            ho = re.search(r"\bhandoff:\s*([\w-]+)\s*\|\s*([^|\n]+)(\|\s*wait)?", last)
            if ho and not reply:
                g = tasksmod.gate(db, agent, task_id, "handoff_task", "hangar:handoff_task",
                                  {"to": ho.group(1), "title": ho.group(2).strip(), "wait": bool(ho.group(3))}, "", "handoff")
                if g["status"] == "waiting":
                    return f"[[HANGAR_WAITING:{g['request_id']}]] {g['message']}"
                return f"negado: {g['message']}" if g["status"] == "denied" else f"repassado: {g.get('result', '')}"
            if last.startswith(("TAREFA REPASSADA", "HANDED-OFF TASK")):
                return f"recebi o resultado do colega: {last[:200]}"
            q = re.search(r"\bask:\s*(.+)", last)
            if q and "RESPOSTA de" not in last:
                g = tasksmod.gate(db, agent, task_id, "ask_human", "hangar:ask_human", {"question": q.group(1)}, "", "ask_human")
                return f"[[HANGAR_WAITING:{g['request_id']}]] {g['message']}"
            from app.services import platform_tools
            platform_tools._load()
            u = next((m for m in re.finditer(r"\buse\s+([A-Za-z0-9_]+)(?:[ \t]+(\{.*\}))?", last)  # um "use" por linha: o prompt pode citar resultados com JSON
                      if m.group(1) in TOOLS or m.group(1) in platform_tools.REGISTRY), None)
            if u and u.group(1) in platform_tools.REGISTRY:  # ferramenta da plataforma: roda na central, via gate
                args = json.loads(u.group(2)) if u.group(2) else {}
                g = tasksmod.gate(db, agent, task_id, u.group(1), f"platform:{u.group(1)}", args, "porque a tarefa pede", "platform")
                if g["status"] == "waiting":
                    return f"[[HANGAR_WAITING:{g['request_id']}]] {g['message']}"
                if g["status"] in ("denied", "simulated"):
                    return f"{'negado' if g['status'] == 'denied' else 'simulado'}: {g.get('message', '')}"
                return f"feito: {u.group(1)} -> {g.get('result', '')}"
            if u:
                args = json.loads(u.group(2)) if u.group(2) else {}
                g = tasksmod.gate(db, agent, task_id, u.group(1), TOOLS[u.group(1)], args, "porque a tarefa pede")
                if g["status"] == "waiting":
                    return f"[[HANGAR_WAITING:{g['request_id']}]] {g['message']}"
                if g["status"] == "denied":
                    return f"negado: {g['message']}"
                if g["status"] == "simulated":  # modo sombra: o runtime real devolve a mensagem e não executa
                    return f"simulado: {u.group(1)} {json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                return f"feito: {u.group(1)} {json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            if "RESPOSTA de" in last:
                return "resposta recebida; tarefa concluída"
            return "resultado: tarefa concluída"


@pytest.fixture()
def rt(monkeypatch):
    fake = FakeRuntime()
    monkeypatch.setattr(tasksmod, "HTTP", lambda: fake)
    return fake


def work():
    """Uma passada do executor: reserva o que está na fila e roda cada tarefa (como o laço em segundo plano)."""
    for tid in tasksmod.tick():
        tasksmod.run_task(tid)


def _probation_tasks(n=3):
    return [{"title": f"Tarefa {i}", "body": f"faça a tarefa {i}", "expected": f"resultado {i}"} for i in range(n)]


def _fields(env, **over):
    f = {"title": "Analista de renovações", "mission": "Garantir que nenhuma renovação de cliente seja perdida.",
         "responsibilities": ["acompanhar renovações", "preparar propostas", "avisar riscos"],
         "manager": env["em"]["mgr"], "backups": [env["em"]["bk"]], "team": env["team"], "systems": ["CRM", "e-mail"],
         "tools": [{"type": "http", "name": n, "url": f"https://api.example.com/{n}",
                    "method": TOOLS[n].rsplit(":", 1)[1]} for n in TOOLS],
         "channels": ["portal", "mcp"], "accept_default_authority": True, "probation_tasks": _probation_tasks()}
    f.update(over)
    return f


@pytest.fixture()
def env(client, uniq, fake_docker, monkeypatch):
    monkeypatch.setattr(svc.runtime, "ship", lambda db, slug, actor="admin", **k: [])  # admissão: o ship é testado à parte
    n = uniq("de")
    team = client.post("/api/teams", headers=ADMIN, json={"name": f"Sales {n}", "require_approval": False}).json()["slug"]
    other = client.post("/api/teams", headers=ADMIN, json={"name": f"Other {n}"}).json()["slug"]
    em = {k: f"{k}-{n}@acme.com" for k in ("own", "mgr", "bk", "req", "mt2", "out")}
    roles = {"own": "developer", "mgr": "maintainer", "bk": "maintainer", "req": "developer", "mt2": "maintainer"}
    for k, role in roles.items():
        client.post(f"/api/teams/{team}/members", headers=ADMIN, json={"email": em[k], "role": role})
    client.post(f"/api/teams/{other}/members", headers=ADMIN, json={"email": em["out"], "role": "developer"})
    h = {k: _session(e) for k, e in em.items()}
    return {"n": n, "team": team, "em": em, "h": h}


def hire(client, env, **over) -> str:
    r = client.post("/api/employees", headers=env["h"]["own"], json=_fields(env, **over))
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True, r.json()
    return r.json()["employee"]["slug"]


def make_active(client, env, slug, rt):
    assert client.post(f"/api/employees/{slug}/probation", headers=env["h"]["own"]).status_code == 200
    work()
    adm = [d for d in client.get("/api/decisions", headers=env["h"]["mgr"]).json()
           if d["kind"] == "admission" and d["employee"] == slug]
    assert len(adm) == 1, "o gestor recebe o pedido de admissão quando a experiência termina"
    r = client.post(f"/api/decisions/{adm[0]['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()["status"] == "active"


def assign(client, env, slug, body, who="req"):
    r = client.post(f"/api/employees/{slug}/tasks", headers=env["h"][who], json={"title": "tarefa", "body": body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def task(client, env, tid, who="own"):
    return client.get(f"/api/tasks/{tid}", headers=env["h"][who]).json()


def open_request(client, env, tid, who="mgr"):
    return next(d for d in client.get("/api/decisions", headers=env["h"][who]).json() if d["task"] and d["task"]["id"] == tid
                and d["kind"] != "notice")


# ------------------------------------------------------------------ contratação em modo self
def test_hire_refuses_without_required_fields_and_asks_the_owner(client, env):
    before = client.get("/api/employees", headers=env["h"]["own"]).json()
    r = client.post("/api/employees", headers=env["h"]["own"], json={"request": "quero um funcionário para renovações",
                                                                      "title": "Analista"}).json()
    assert r["created"] is False
    fields = {m["field"] for m in r["missing"]}
    assert fields == {"mission", "responsibilities", "manager", "systems", "authority", "channels", "probation_tasks"}
    assert all(m["question"] for m in r["missing"]) and "gestor" in next(m["question"] for m in r["missing"]
                                                                         if m["field"] == "manager")
    assert client.get("/api/employees", headers=env["h"]["own"]).json() == before  # nada foi criado
    # pedido em inglês: perguntas em inglês
    en = client.post("/api/employees/plan", headers=env["h"]["own"], json={"request": "I want an employee for the renewals"}).json()
    assert en["ready"] is False and "manager" in next(m["question"] for m in en["missing"] if m["field"] == "manager")
    # gestor que não existe também é pergunta, não invenção
    bad = client.post("/api/employees", headers=env["h"]["own"], json=_fields(env, manager="nobody@nowhere.com")).json()
    assert bad["created"] is False and [m["field"] for m in bad["missing"]] == ["manager"]
    # menos de 3 tarefas de experiência com resultado esperado não serve
    few = client.post("/api/employees", headers=env["h"]["own"], json=_fields(env, probation_tasks=_probation_tasks(2))).json()
    assert [m["field"] for m in few["missing"]] == ["probation_tasks"]


def test_plan_shows_default_authority_and_floor_then_hire_creates_everything(client, env):
    p = client.post("/api/employees/plan", headers=env["h"]["own"], json=_fields(env)).json()
    assert p["ready"] is True and p["missing"] == []
    modes = {x["action_type"]: x["mode"] for x in p["authority_default"]}
    assert modes["read"] == "auto" and modes["send_external"] == "approve" and modes["financial"] == "approve_2"
    slug = hire(client, env)
    d = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()
    assert d["status"] == "onboarding" and d["manager"] and len(d["probation"]) == 3
    assert all(t["status"] == "draft" for t in d["probation"])
    g = client.get(f"/api/agents/{slug}/guide", headers=env["h"]["own"]).json()
    assert "Analista de renovações" in g["doc"]["text"] and "pede aprovação" in g["doc"]["text"]
    with SessionLocal() as db:
        spec = svc.runtime.resolve_spec(db, svc.get_agent(db, slug), "stage")
        assert spec["employee"]["title"] == "Analista de renovações"
        assert {"action_type": "financial", "mode": "approve_2"} in spec["employee"]["authority"]
        assert any(t.get("judge") == "resultado 0" for t in spec["tests"])  # experiência vira teste do cargo


# ------------------------------------------------------------------ motor de alçada
def test_authority_engine_defaults_floor_and_conditions(client, env):
    slug = hire(client, env)
    with SessionLocal() as db:
        e = db.query(Employee).filter(Employee.agent_id == svc.get_agent(db, slug).id).one()
        ev = lambda t, a=None: gatemod.evaluate(db, e, t, a or {})["mode"]  # noqa: E731
        assert ev("read") == "auto" and ev("delegate") == "notify" and ev("write_internal") == "approve"
        assert ev("financial") == "approve_2" and ev("delete") == "approve"
        e.autonomy_level = "pleno"
        db.commit()
        assert ev("write_internal") == "auto" and ev("send_external") == "approve"  # o piso continua
        db.add(AuthorityRule(employee_id=e.id, action_type="send_external", mode="auto", conditions=[]))
        db.add(AuthorityRule(employee_id=e.id, action_type="write_internal", mode="never",
                             conditions=[{"field": "amount", "op": ">", "value": 1000}]))
        db.add(AuthorityRule(employee_id=e.id, action_type="read", mode="approve",
                             conditions=[{"field": "to", "op": "domain_not", "value": "acme.com"}]))
        db.commit()
        assert ev("send_external") == "approve"  # regra mais frouxa que o piso: o piso vence
        assert ev("write_internal", {"amount": 50}) == "auto" and ev("write_internal", {"amount": 5000}) == "never"
        assert ev("read", {"to": "ana@acme.com"}) == "auto" and ev("read", {"to": "x@other.com"}) == "approve"
    r = client.put(f"/api/employees/{slug}/authority", headers=env["h"]["mgr"],
                   json={"rules": [{"action_type": "send_external", "mode": "notify"}]}).json()
    assert r["warnings"] and "piso" in r["warnings"][0]
    assert client.put(f"/api/employees/{slug}/authority", headers=env["h"]["mgr"],
                      json={"rules": [{"action_type": "nope", "mode": "auto"}]}).status_code == 400


def test_tool_classification():
    c = gatemod.auto_classify
    assert c("builtin:calculator") == ("read", False) and c("agent:x") == ("delegate", False)
    assert c("http:x:GET") == ("read", False) and c("http:x:DELETE") == ("delete", False)
    assert c("http:x:POST") == ("write_internal", True) and c("mcp:memory:recall") == ("read", False)
    assert c("mcp:gmail:send_email")[0] == "send_external" and c("mcp:stripe:refund_payment")[0] == "financial"
    assert c("mcp:crm:list_accounts")[0] == "read" and c("mcp:x:delete_row")[0] == "delete"
    assert c("mcp:x:frobnicate") == ("write_internal", True)


# ------------------------------------------------------------------ ciclo de vida e humano no circuito
def test_lifecycle_probation_admission_only_by_the_manager(client, env, rt):
    slug = hire(client, env)
    assert client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"], json={"title": "x"}).status_code == 400
    assert client.post(f"/api/employees/{slug}/probation", headers=env["h"]["out"]).status_code == 403
    assert client.post(f"/api/employees/{slug}/probation", headers=env["h"]["own"]).json()["status"] == "probation"
    work()
    d = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()
    assert all(t["status"] == "done" for t in d["probation"]) and {c[0] for c in rt.calls} == {slug}
    adm = next(x for x in client.get("/api/decisions", headers=env["h"]["mgr"]).json() if x["kind"] == "admission")
    assert "esperado: resultado 0" in adm["question"]
    with SessionLocal() as db:  # um pedido duplicado (corrida entre tarefas que terminam juntas) não admite duas vezes
        dup = HumanRequest(employee_id=db.get(HumanRequest, adm["id"]).employee_id, kind="admission", question="dup", approvals=[])
        db.add(dup)
        db.commit()
        dup_id = dup.id
    assert client.post(f"/api/decisions/{adm['id']}", headers=env["h"]["bk"], json={"decision": "approve"}).status_code == 403
    assert client.post(f"/api/decisions/{adm['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 200
    d = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()
    assert d["status"] == "active" and d["hired_at"]
    with SessionLocal() as db:
        assert db.get(HumanRequest, dup_id).status == "cancelled"
        e = db.query(Employee).filter(Employee.agent_id == svc.get_agent(db, slug).id).one()
        with pytest.raises(svc.PlatformError):
            empmod.admit(db, svc.access.Access(db, auth.Principal("admin-token", {"admin"})), e, True)


def test_task_pauses_for_approval_and_resumes_with_the_exact_action(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, 'use send_email {"to": "ana@cliente.com", "body": "Olá"}')
    work()
    t = task(client, env, tid)
    assert t["status"] == "waiting_human"
    r = open_request(client, env, tid)
    assert r["kind"] == "approval" and r["action_type"] == "send_external" and r["payload"]["to"] == "ana@cliente.com"
    assert r["rationale"] and r["expires_at"] and r["assigned_to"]
    assert client.post(f"/api/decisions/{r['id']}", headers=env["h"]["out"], json={"decision": "approve"}).status_code == 403
    assert client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 200
    assert task(client, env, tid)["status"] == "new"
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and t["result"].startswith("feito: send_email")
    kinds = [e["kind"] for e in t["events"]]
    for k in ("created", "run", "gate", "human_request", "decision", "checkpoint", "tool", "done"):
        assert k in kinds, k
    # a aprovação vale UMA vez, para aquela ação exata
    with SessionLocal() as db:
        agent = svc.get_agent(db, slug)
        again = tasksmod.gate(db, agent, tid, "send_email", TOOLS["send_email"], {"to": "ana@cliente.com", "body": "Olá"})
        assert again["status"] == "waiting"


def test_approve_edited_runs_the_edited_action_and_reject_runs_nothing(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, 'use send_email {"to": "ana@cliente.com", "body": "preço 10%"}')
    work()
    r = open_request(client, env, tid)
    assert client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"],
                       json={"decision": "approve_edited"}).status_code == 400  # sem a edição
    client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"],
                json={"decision": "approve_edited", "edit": {"to": "ana@cliente.com", "body": "preço 5%"}, "reason": "política"})
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and '"body": "preço 5%"' in t["result"]

    tid2 = assign(client, env, slug, 'use send_email {"to": "beto@cliente.com"}')
    work()
    r2 = open_request(client, env, tid2)
    client.post(f"/api/decisions/{r2['id']}", headers=env["h"]["mgr"], json={"decision": "reject", "reason": "não agora"})
    work()
    t2 = task(client, env, tid2)
    assert t2["status"] == "done" and "não executei" in t2["result"]
    with SessionLocal() as db:
        assert db.get(HumanRequest, r2["id"]).grant_hash == ""

    tid3 = assign(client, env, slug, 'use send_email {"to": "caio@cliente.com"}')
    work()
    r3 = open_request(client, env, tid3)
    assert client.post(f"/api/decisions/{r3['id']}", headers=env["h"]["mgr"], json={"decision": "instruct"}).status_code == 400
    client.post(f"/api/decisions/{r3['id']}", headers=env["h"]["mgr"], json={"decision": "instruct",
                                                                               "reason": "ligue em vez de mandar e-mail"})
    work()
    assert "não executei" in task(client, env, tid3)["result"]


def test_two_approvals_and_separation_of_duties(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, 'use pay_invoice {"amount": 4200, "invoice": "INV-9"}', who="mgr")
    work()
    r = open_request(client, env, tid, who="bk")
    assert r["mode"] == "approve_2" and r["action_type"] == "financial"
    # o gestor pediu a tarefa: separação de funções o impede de aprovar o pagamento
    assert client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).status_code == 403
    first = client.post(f"/api/decisions/{r['id']}", headers=env["h"]["bk"], json={"decision": "approve"}).json()
    assert first["status"] == "open" and len(first["approvals"]) == 1
    assert client.post(f"/api/decisions/{r['id']}", headers=env["h"]["bk"], json={"decision": "approve"}).status_code == 400
    second = client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mt2"], json={"decision": "approve"}).json()
    assert second["status"] == "decided" and len(second["approvals"]) == 2
    work()
    assert task(client, env, tid)["status"] == "done"


def test_no_answer_escalates_then_expires_doing_nothing(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, 'use delete_record {"id": 7}')
    work()
    r = open_request(client, env, tid)
    first_assignee = r["assigned_to"]
    with SessionLocal() as db:
        db.get(HumanRequest, r["id"]).expires_at = now() - timedelta(minutes=1)
        db.commit()
        assert tasksmod.sweep(db)["escalated"] == 1
        req = db.get(HumanRequest, r["id"])
        assert req.escalated and req.status == "open"
        req.expires_at = now() - timedelta(minutes=1)
        db.commit()
        assert tasksmod.sweep(db)["expired"] == 1
        assert db.get(HumanRequest, r["id"]).grant_hash == ""
    assert open_request.__name__  # o pedido saiu da fila de todos
    assert not [d for d in client.get("/api/decisions", headers=env["h"]["mgr"]).json() if d["id"] == r["id"]]
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and "não executei" in t["result"] and first_assignee


def test_question_to_the_manager_and_answer(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "ask: qual desconto posso oferecer à ACME?")
    work()
    r = open_request(client, env, tid)
    assert r["kind"] == "question" and "desconto" in r["question"]
    assert client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "answer"}).status_code == 400
    client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "answer", "reason": "até 5%"})
    with SessionLocal() as db:
        cp = db.query(TaskCheckpoint).filter(TaskCheckpoint.task_id == tid).order_by(TaskCheckpoint.seq.desc()).first()
        assert "RESPOSTA de" in cp.messages[-1]["content"] and "até 5%" in cp.messages[-1]["content"]
    work()
    assert task(client, env, tid)["status"] == "done"


def test_gate_auto_notify_never_and_actions_outside_a_task(client, env, rt):
    slug = hire(client, env, authority=[{"action_type": "delete", "mode": "never"}], accept_default_authority=False)
    make_active(client, env, slug, rt)
    with SessionLocal() as db:
        agent = svc.get_agent(db, slug)
        assert tasksmod.gate(db, agent, None, "crm_lookup", TOOLS["crm_lookup"], {"q": "acme"})["status"] == "allowed"
        out = tasksmod.gate(db, agent, None, "send_email", TOOLS["send_email"], {"to": "a@b.com"})
        assert out["status"] == "denied" and "tarefa" in out["message"]  # aprovação só dentro de uma tarefa
        assert tasksmod.gate(db, agent, None, "delete_record", TOOLS["delete_record"], {"id": 1})["status"] == "denied"
        e = db.query(Employee).filter(Employee.agent_id == agent.id).one()
        e.autonomy_level = "junior"
        db.commit()
        assert tasksmod.gate(db, agent, None, "update_crm", TOOLS["update_crm"], {"x": 1})["status"] == "allowed"
        notice = db.query(HumanRequest).filter(HumanRequest.employee_id == e.id, HumanRequest.kind == "notice").first()
        assert notice is not None and notice.tool_name == "update_crm"
        # ferramenta desconhecida ganha classificação automática conservadora e vai para revisão do admin
        assert db.query(ActionCatalog).filter(ActionCatalog.tool_ref == TOOLS["update_crm"]).one().classified_by == "auto"
    nid = next(d["id"] for d in client.get("/api/decisions", headers=env["h"]["mgr"]).json() if d["kind"] == "notice")
    assert client.post(f"/api/decisions/{nid}", headers=env["h"]["mgr"], json={"decision": "ack"}).json()["status"] == "decided"


def test_pause_blocks_everything_and_offboard_cancels(client, env, rt, fake_docker):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, 'use send_email {"to": "x@cliente.com"}')
    work()
    assert client.post(f"/api/employees/{slug}/status", headers=env["h"]["req"], json={"to": "paused"}).status_code == 403
    assert client.post(f"/api/employees/{slug}/status", headers=env["h"]["mgr"], json={"to": "paused"}).json()["status"] == "paused"
    with SessionLocal() as db:
        agent = svc.get_agent(db, slug)
        assert tasksmod.gate(db, agent, None, "crm_lookup", TOOLS["crm_lookup"], {})["status"] == "denied"
    assert client.post(f"/api/employees/{slug}/status", headers=env["h"]["mgr"], json={"to": "active"}).json()["status"] == "active"
    r = client.post(f"/api/employees/{slug}/status", headers=env["h"]["own"], json={"to": "offboarded", "reason": "fim"}).json()
    assert r["status"] == "offboarded"
    assert task(client, env, tid)["status"] == "cancelled"
    assert not [d for d in client.get("/api/decisions", headers=env["h"]["mgr"]).json() if d["employee"] == slug]
    assert client.post(f"/api/employees/{slug}/tasks", headers=env["h"]["req"], json={"title": "x"}).status_code == 400


def test_gate_endpoint_needs_the_agents_own_token(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    other = hire(client, env, title="Outro cargo")
    with SessionLocal() as db:
        e2 = db.query(Employee).filter(Employee.agent_id == svc.get_agent(db, other).id).one()
        tid = tasksmod.create_task(db, e2, "nada", status="draft").id
    body = {"task_id": tid, "tool": "send_email", "ref": TOOLS["send_email"], "args": {"to": "a@b.com"}}
    assert client.post("/internal/gate", json=body).status_code == 401
    hdr = {"X-Agent-Slug": slug, "Authorization": f"Bearer {auth.agent_token(slug)}"}
    r = client.post("/internal/gate", json=body, headers=hdr).json()
    assert r["status"] == "denied" and "outro" in r["message"]  # tarefa de outro Digital employee
    body["task_id"] = None
    body["ref"] = TOOLS["crm_lookup"]
    assert client.post("/internal/gate", json=body, headers=hdr).json()["status"] == "allowed"


def test_permissions_on_the_employee_space(client, env, rt):
    slug = hire(client, env)
    out = env["h"]["out"]
    assert client.get(f"/api/employees/{slug}", headers=out).status_code in (200, 403)
    assert client.post(f"/api/employees/{slug}/tasks", headers=out, json={"title": "x"}).status_code == 403
    assert client.patch(f"/api/employees/{slug}", headers=out, json={"title": "x"}).status_code == 403
    assert client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"autonomy_level": "senior"}).status_code == 403
    assert client.patch(f"/api/employees/{slug}", headers=env["h"]["mgr"],
                        json={"autonomy_level": "junior"}).json()["autonomy_level"] == "junior"
    assert client.patch(f"/api/employees/{slug}", headers=env["h"]["own"],
                        json={"responsibilities": ["só uma"]}).status_code == 400


def test_budget_and_round_limits(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    client.patch(f"/api/employees/{slug}", headers=env["h"]["mgr"], json={"task_budget_usd": 0.005})
    tid = assign(client, env, slug, "ask: posso continuar?")
    work()  # custa 0.01 por rodada no FakeRuntime: passou do orçamento
    r = open_request(client, env, tid)
    client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "answer", "reason": "sim"})
    work()
    t = task(client, env, tid)
    assert t["status"] == "failed" and "orçamento" in t["error"]


def test_reports_and_webhook(client, env, rt, monkeypatch):
    sent = []

    class Hook:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            sent.append((url, json))
            return httpx.Response(200)
    monkeypatch.setattr(svc.schedules, "HTTP", lambda: Hook())
    monkeypatch.setattr(svc.schedules, "check_webhook", lambda u: u)
    slug = hire(client, env, report_webhook="https://hooks.example.com/x")
    make_active(client, env, slug, rt)
    assert any("precisa de você" in s[1]["text"] for s in sent)  # o pedido de admissão avisou no webhook
    r = client.post(f"/api/employees/{slug}/reports", headers=env["h"]["mgr"]).json()
    assert r["delivered"] is True and "concluída" in r["summary"]
    assert client.get(f"/api/employees/{slug}/reports", headers=env["h"]["own"]).json()[0]["id"] == r["id"]
    with SessionLocal() as db:
        e = db.query(Employee).filter(Employee.agent_id == svc.get_agent(db, slug).id).one()
        e.report_hour, e.last_report_on = 0, ""
        db.commit()
        first = empmod.daily_reports(db)
        assert first >= 1 and empmod.daily_reports(db) == 0  # uma vez por dia


def test_mcp_self_mode_asks_then_hires(client, env):
    pat = {"Authorization": "Bearer " + client.post("/api/keys", headers=env["h"]["own"],
                                                    json={"name": "claude", "scopes": ["user"]}).json()["key"]}

    def call(tool, args):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        r = client.post("/mcp", json=body, headers={**MCP_HDR, **pat}).json()["result"]
        assert not r["isError"], r["content"][0]["text"]
        return json.loads(r["content"][0]["text"])

    names = {t["name"] for t in client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                            headers={**MCP_HDR, **pat}).json()["result"]["tools"]}
    assert {"plan_employee", "hire_employee", "start_probation", "assign_task", "task_status", "employee_status",
            "my_pending_decisions", "decide", "pause_employee", "set_authority", "offboard_employee"} <= names
    p = call("plan_employee", {"request": "quero um analista de renovações", "title": "Analista"})
    assert p["ready"] is False and len(p["missing"]) >= 5
    h = call("hire_employee", {"title": "Analista", "mission": "x"})
    assert h["created"] is False and h["missing"]
    f = _fields(env)
    h = call("hire_employee", f)
    assert h["created"] is True and h["employee"]["status"] == "onboarding"
    s = call("employee_status", {"slug": h["employee"]["slug"]})
    assert s["title"] == "Analista de renovações" and s["authority"]


def test_admin_workforce_catalog_floor_and_stop_all(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    mgr = env["h"]["mgr"]
    assert client.get("/api/admin/workforce", headers=mgr).status_code == 403
    w = client.get("/api/admin/workforce", headers=ADMIN).json()
    assert any(x["slug"] == slug for x in w["employees"]) and "decisions_aging" in w
    assert client.put("/api/admin/action-catalog", headers=mgr, json={"tool_ref": "x", "action_type": "read"}).status_code == 403
    c = client.put("/api/admin/action-catalog", headers=ADMIN, json={"tool_ref": TOOLS["update_crm"],
                                                                   "action_type": "write_internal", "reversible": True}).json()
    assert c["review"] is False
    fl = client.put("/api/admin/authority-floor", headers=ADMIN,
                    json={"items": [{"action_type": "write_internal", "min_mode": "approve", "separation": False}]}).json()
    assert next(x for x in fl if x["action_type"] == "write_internal")["min_mode"] == "approve"
    with SessionLocal() as db:
        e = db.query(Employee).filter(Employee.agent_id == svc.get_agent(db, slug).id).one()
        e.autonomy_level = "senior"
        db.commit()
        assert gatemod.evaluate(db, e, "write_internal", {})["source"] == "piso da empresa"
    client.put("/api/admin/authority-floor", headers=ADMIN,
               json={"items": [{"action_type": "write_internal", "min_mode": "auto", "separation": False}]})
    assert client.post("/api/admin/workforce/stop-all", headers=ADMIN, json={"reason": "teste"}).json()["paused"] >= 1
    assert client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()["status"] == "paused"


def test_lost_round_goes_back_to_the_queue(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "nada")
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        t.status, t.started_at = "in_progress", now() - timedelta(hours=2)  # a réplica caiu no meio da rodada
        db.commit()
        assert tasksmod.sweep(db)["requeued"] == 1
        assert db.get(EmployeeTask, tid).not_before is not None  # volta com espera, não na hora
    work()
    assert task(client, env, tid)["status"] == "new"
    with SessionLocal() as db:
        db.get(EmployeeTask, tid).not_before = now() - timedelta(seconds=1)  # a espera passou
        db.commit()
    work()
    assert task(client, env, tid)["status"] == "done"
    assert config.EMPLOYEE_MAX_RUNS >= 3


def test_task_prompt_and_runtime_profile_follow_the_job_language(client, env):
    slug = hire(client, env, title="Renewals analyst", mission="Make sure no customer renewal is missed.",
                responsibilities=["track renewals", "flag risks", "draft proposals"])
    with SessionLocal() as db:
        e = db.query(Employee).filter(Employee.agent_id == svc.get_agent(db, slug).id).one()
        assert tasksmod.lang_of(e) == "en"
        assert empmod.runtime_profile(db, svc.get_agent(db, slug))["lang"] == "en"
        t = db.query(EmployeeTask).filter(EmployeeTask.employee_id == e.id).first()
        assert tasksmod.task_prompt(t, "en").startswith(f"TASK #{t.id}") and "probation task" in tasksmod.task_prompt(t, "en")
        assert tasksmod.task_prompt(t).startswith(f"TAREFA #{t.id}")


def test_admission_respects_the_teams_four_eyes_for_production(client, env, rt, monkeypatch):
    monkeypatch.setattr(svc.runtime, "promote", lambda db, slug, actor="admin", **k: None)

    def ship(db, slug, actor="admin", _seen=None, promote_prod=True):  # como o real: testes em stage aprovados
        from app.models import TestRun
        a = svc.get_agent(db, slug)
        db.add(TestRun(agent_id=a.id, version=a.current_version, status="passed", summary="ok"))
        db.commit()
        return []
    monkeypatch.setattr(svc.runtime, "ship", ship)
    client.patch(f"/api/teams/{env['team']}", headers=ADMIN, json={"require_approval": True})
    slug = hire(client, env)
    assert client.post(f"/api/employees/{slug}/probation", headers=env["h"]["own"]).status_code == 200
    work()
    adm = next(d for d in client.get("/api/decisions", headers=env["h"]["mgr"]).json() if d["kind"] == "admission")
    r = client.post(f"/api/decisions/{adm['id']}", headers=env["h"]["mgr"], json={"decision": "approve"}).json()
    assert r["employee_status"] == "approval_pending" and "Aprovações" in r["message"]
    assert client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()["status"] == "probation"
    promo = next(x for x in client.get("/api/approvals", headers=env["h"]["mt2"]).json()["to_decide"] if x["kind"] == "promotion")
    assert client.post(f"/api/promotions/{promo['id']}/approve", headers=env["h"]["mgr"], json={}).status_code == 403  # quatro olhos
    assert client.post(f"/api/promotions/{promo['id']}/approve", headers=env["h"]["mt2"], json={}).status_code == 200
    d = client.get(f"/api/employees/{slug}", headers=env["h"]["own"]).json()
    assert d["status"] == "active" and d["hired_at"]
