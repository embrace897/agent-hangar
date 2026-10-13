# ruff: noqa: F811  (env, rt e as outras são fixtures importadas de test_employees)
"""Issues (quadro kanban): criar, ver só o que é do time, atribuir a um Digital employee (a rodada com a governança
de sempre), o resultado como comentário e a revisão, comentários com menções, conversa com a rodada em andamento,
"não iniciar ainda", falha, ferramentas do agente (comentar, mudar status, dividir), sub-issues em etapas,
etiquetas, relações, reações, inscrição na caixa de entrada, apagar e a rotina que abre issue."""
from app.db import SessionLocal
from app.models import EmployeeRoutine, EmployeeTask, Issue, TaskMessage
from app.services import employee_tasks as tasksmod
from app.services import employee_work, platform_tools

from .conftest import ADMIN
from .test_employees import _fields, env, hire, make_active, rt, work  # noqa: F401
from .test_phase_g import _close_leftover_tasks  # noqa: F401


def _issue(client, h, **body):
    r = client.post("/api/issues", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _get(client, h, key):
    r = client.get(f"/api/issues/{key}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _hired(client, env, rt, name="Analista"):
    slug = hire(client, env, name=name)
    make_active(client, env, slug, rt)
    return slug


def test_board_settings_visibility_and_basic_fields(client, env):
    s = client.get("/api/issues/settings", headers=env["h"]["dev" if "dev" in env["h"] else "own"]).json()
    keys = [x["key"] for x in s["statuses"]]
    assert keys[:7] == ["backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"]
    assert client.put("/api/issues/settings/prefix", headers=env["h"]["mgr"], json={"prefix": "OPS"}).status_code == 403
    assert client.post("/api/issues/statuses", headers=ADMIN, json={"name": "Code review", "category": "started"}).status_code in (200, 400)
    own = env["h"]["own"]
    lab = client.post("/api/issues/labels", headers=own, json={"name": "cliente", "color": "#ff0000"}).json()
    i = _issue(client, own, title="Renovar ACME", description="Contrato vence em **30 dias**", team=env["team"],
               priority="high", labels=[lab["id"]], due_date="2030-01-31")
    assert i["status"] == "backlog" and i["priority"] == "high" and i["labels"][0]["name"] == "cliente"
    assert i["key"].split("-")[1].isdigit() and i["team"]["slug"] == env["team"]
    # quem não é do time não vê nem mexe
    assert client.get(f"/api/issues/{i['key']}", headers=env["h"]["out"]).status_code == 403
    assert all(x["key"] != i["key"] for x in client.get("/api/issues", headers=env["h"]["out"]).json())
    assert any(x["key"] == i["key"] for x in client.get("/api/issues", headers=env["h"]["req"]).json())
    # mover de coluna (status), reordenar, filtrar
    r = client.patch(f"/api/issues/{i['key']}", headers=env["h"]["req"], json={"status": "todo", "position": -5})
    assert r.status_code == 200 and r.json()["status"] == "todo" and r.json()["position"] == -5
    assert [x["key"] for x in client.get("/api/issues", headers=own, params={"status": "todo", "label": str(lab["id"])}).json()] == [i["key"]]
    assert client.patch(f"/api/issues/{i['key']}", headers=own, json={"priority": "altissima"}).status_code == 400
    d = _get(client, own, i["key"])
    kinds = [x["kind"] for x in d["timeline"] if x["type"] == "activity"]
    assert kinds[:2] == ["created", "status"] and d["subscribed"] is True


def test_assign_to_employee_runs_with_governance_and_lands_in_review(client, env, rt):
    slug = _hired(client, env, rt)
    own, mgr = env["h"]["own"], env["h"]["mgr"]
    i = _issue(client, own, title="Resumo da carteira", team=env["team"], assignee=f"employee:{slug}")
    assert i["run_started"] and i["status"] == "in_progress" and i["assignee"]["slug"] == slug
    with SessionLocal() as db:
        t = db.query(EmployeeTask).filter(EmployeeTask.issue_id == i["id"]).one()
        assert t.source == "issue" and t.title.startswith(i["key"]) and "Resumo da carteira" in t.title
    work()
    d = _get(client, own, i["key"])
    assert d["status"] == "in_review"  # quem revisa conclui
    agent_c = [x for x in d["timeline"] if x["type"] == "comment" and x["author"]["kind"] == "employee"]
    assert agent_c and "concluída" in agent_c[-1]["body"] and d["runs"][0]["status"] == "done"
    # comentário com menção a outro Digital employee: começa uma rodada dele, sem trocar o responsável
    other = _hired(client, env, rt, name="Revisor")
    r = client.post(f"/api/issues/{i['key']}/comments", headers=mgr, json={"body": f"@[Revisor](employee:{other}) confere?"})
    assert r.status_code == 200 and r.json()["runs_started"] == [other]
    assert _get(client, own, i["key"])["assignee"]["slug"] == slug
    work()
    d = _get(client, mgr, i["key"])
    assert {x["employee"] for x in d["runs"]} == {slug, other}
    # o dono do time conclui
    assert client.patch(f"/api/issues/{i['key']}", headers=mgr, json={"status": "done"}).json()["completed_at"]


def test_backlog_dont_start_yet_follow_up_and_failure(client, env, rt):
    slug = _hired(client, env, rt)
    own = env["h"]["own"]
    i = _issue(client, own, title="Preparar proposta", team=env["team"], assignee=f"employee:{slug}", status="backlog")
    assert not i["run_started"] and i["status"] == "backlog"
    r = client.patch(f"/api/issues/{i['key']}", headers=own, json={"status": "todo", "start": False})
    assert r.json()["run_started"] is False and r.json()["status"] == "todo"  # "não iniciar ainda"
    assert client.post(f"/api/issues/{i['key']}/run", headers=own, json={}).json()["started"] is True
    # comentário com a rodada em andamento: entra na conversa dela
    c = client.post(f"/api/issues/{i['key']}/comments", headers=own, json={"body": "inclua a Globex também"}).json()
    assert c["runs_started"] == []
    with SessionLocal() as db:
        t = db.query(EmployeeTask).filter(EmployeeTask.issue_id == i["id"]).one()
        assert any("Globex" in m.body for m in db.query(TaskMessage).filter(TaskMessage.task_id == t.id))
        tasksmod._finish(db, t, "failed", error="o CRM não respondeu")
    d = _get(client, own, i["key"])
    assert d["status"] == "todo" and any("o CRM não respondeu" in x["body"] for x in d["timeline"] if x["type"] == "comment")
    # sem rodada em andamento, o comentário à responsável começa outra (acompanhamento)
    c = client.post(f"/api/issues/{i['key']}/comments", headers=own, json={"body": "tenta de novo, por favor"}).json()
    with SessionLocal() as db:
        assert db.query(EmployeeTask).filter(EmployeeTask.issue_id == i["id"]).count() == 2
    # quem não pode entregar trabalho ao funcionário não atribui
    assert client.post("/api/issues", headers=env["h"]["out"], json={"title": "x", "assignee": f"employee:{slug}"}).status_code == 403


def test_agent_tools_status_comment_and_sub_issues_with_stages(client, env, rt):
    slug = _hired(client, env, rt)
    helper = _hired(client, env, rt, name="Ajudante")
    own = env["h"]["own"]
    parent = _issue(client, own, title="Migrar contratos", team=env["team"], assignee=f"employee:{slug}", start=False)
    with SessionLocal() as db:
        i = db.query(Issue).filter(Issue.id == parent["id"]).one()
        from app.services import issues as im
        acc = None
        t = im.start_run(db, acc, i)
        a = __import__("app.services", fromlist=["get_agent"]).get_agent(db, slug)
        run = lambda name, args: platform_tools.run(db, a, t.id, name, args)  # noqa: E731
        assert "erro" in run("issue_set_status", {"status": "done"})["result"]  # o agente não conclui
        assert run("issue_set_status", {"status": "blocked", "comment": "falta o acesso ao CRM"})["status"] == "allowed"
        assert run("issue_comment", {"body": "levantei 12 contratos"})["status"] == "allowed"
        s1 = run("create_sub_issue", {"title": "Exportar", "stage": 1, "assignee": helper})
        s2 = run("create_sub_issue", {"title": "Conferir", "stage": 2})
        assert s1["status"] == "allowed" and s2["status"] == "allowed", (s1, s2)
        assert "Migrar contratos" in run("get_issue", {})["result"]
    d = _get(client, own, parent["key"])
    assert d["status"] == "blocked" and len(d["sub_issues"]) == 2
    first = next(x for x in d["sub_issues"] if x["stage"] == 1)
    assert first["assignee"]["slug"] == helper and first["run"]["active"]
    # a etapa 1 termina: a mãe fica sabendo (e o responsável dela acorda para a próxima)
    client.patch(f"/api/issues/{first['key']}", headers=own, json={"status": "done"})
    d = _get(client, own, parent["key"])
    assert any(x["kind"] == "stage_done" and x["data"]["stage"] == 1 for x in d["timeline"] if x["type"] == "activity")


def test_comments_threads_reactions_relations_inbox_and_delete(client, env):
    own, mgr, req = env["h"]["own"], env["h"]["mgr"], env["h"]["req"]
    a = _issue(client, own, title="A", team=env["team"])
    b = _issue(client, req, title="B", team=env["team"])
    assert client.post(f"/api/issues/{a['key']}/relations", headers=own, json={"kind": "blocked_by", "other": b["key"]}).status_code == 200
    rel = _get(client, own, a["key"])["relations"]
    assert rel[0]["kind"] == "blocked_by" and rel[0]["key"] == b["key"]
    assert _get(client, own, b["key"])["relations"][0]["kind"] == "blocks"
    me = client.get("/api/me", headers=mgr).json()["user"]
    c = client.post(f"/api/issues/{a['key']}/comments", headers=own,
                    json={"body": f"@[Gestora](user:{me['id']}) pode olhar?"}).json()
    # a gestora mencionada fica inscrita e vê na caixa de entrada
    box = client.get("/api/inbox", headers=mgr).json()["items"]
    assert any(x["key"] == f"issue:{a['id']}" for x in box)
    reply = client.post(f"/api/issues/{a['key']}/comments", headers=mgr, json={"body": "olhei", "parent_id": c["id"]}).json()
    assert client.post(f"/api/issues/{a['key']}/comments/{c['id']}/resolve", headers=mgr, json={}).json()["resolved"]
    assert client.post(f"/api/issues/{a['key']}/comments/{c['id']}/react", headers=mgr, json={"emoji": "👍"}).json()["reactions"] == {"👍": 1}
    assert client.patch(f"/api/issues/{a['key']}/comments/{c['id']}", headers=mgr, json={"body": "x"}).status_code == 403
    assert client.delete(f"/api/issues/{a['key']}/comments/{c['id']}", headers=own).status_code == 200
    tl = [x for x in _get(client, own, a["key"])["timeline"] if x["type"] == "comment"]
    assert any(x["deleted"] for x in tl) and any(x["id"] == reply["id"] for x in tl)  # a discussão fica
    # apagar: quem criou ou mantenedor; um developer que não criou, não
    assert client.delete(f"/api/issues/{a['key']}", headers=req).status_code == 403
    assert client.delete(f"/api/issues/{a['key']}", headers=mgr).status_code == 200
    assert client.get(f"/api/issues/{a['key']}", headers=own).status_code == 403


def test_routine_opens_an_issue(client, env, rt):
    slug = _hired(client, env, rt)
    r = client.post(f"/api/employees/{slug}/routines", headers=env["h"]["own"],
                    json={"title": "Relatório semanal", "body": "resuma a semana", "cron": "0 9 * * 1", "creates_issue": True})
    assert r.status_code == 200 and r.json()["creates_issue"] is True, r.text
    with SessionLocal() as db:
        rt_ = db.query(EmployeeRoutine).filter(EmployeeRoutine.id == r.json()["id"]).one()
        e = db.get(__import__("app.models", fromlist=["Employee"]).Employee, rt_.employee_id)
        t = employee_work._routine_task(db, e, rt_, "teste")
        assert t is not None and t.issue_id
        i = db.get(Issue, t.issue_id)
        assert i.title == "Relatório semanal" and i.routine_id == rt_.id and i.status == "in_progress"
