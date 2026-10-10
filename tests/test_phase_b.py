# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Fase B: dependências entre tarefas (e o resultado delas no prompt), revisão antes de concluir, arquivos das tarefas
(anexos, entregáveis e versões), continuar uma tarefa com falha (sem repetir ações às cegas), pausar a árvore e o
histórico do cargo."""
from app.db import SessionLocal
from app.models import Agent, EmployeeTask, TaskCheckpoint, TaskEvent, now
from app.services import employee_tasks as tasksmod

from .test_employees import assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401


def _active(client, env, rt, name="Analista"):
    slug = hire(client, env, name=f"{name} {env['n']}")
    make_active(client, env, slug, rt)
    return slug


def _new(client, env, slug, body, who="req", **extra):
    r = client.post(f"/api/employees/{slug}/tasks", headers=env["h"][who], json={"title": body[:40], "body": body, **extra})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _last_user_text(tid) -> str:
    with SessionLocal() as db:
        cp = db.query(TaskCheckpoint).filter_by(task_id=tid).order_by(TaskCheckpoint.seq.desc()).first()
        return "\n".join(m["content"] for m in cp.messages if m["role"] == "user")


# ------------------------------------------------------------------ dependências
def test_a_task_waits_for_its_dependencies_and_gets_their_results(client, env, rt):
    slug = _active(client, env, rt)
    first = _new(client, env, slug, "levante os contratos")
    second = _new(client, env, slug, "escreva o resumo", blocked_by=[first])
    assert task(client, env, second)["status"] == "blocked"
    assert task(client, env, second)["diagnosis"]["state"] == "dependency"
    work()  # roda a primeira; a segunda sai do bloqueio com o resultado dela
    assert task(client, env, first)["status"] == "done"
    t = task(client, env, second)
    assert t["status"] in ("new", "done") and t["dependencies"]["depends_on"][0]["id"] == first
    assert f"TAREFA PRÉVIA #{first} CONCLUÍDA" in _last_user_text(second)
    work()
    assert task(client, env, second)["status"] == "done"
    assert task(client, env, first)["dependencies"]["blocks"][0]["id"] == second
    # ciclo e dependência numa tarefa que já começou
    third = _new(client, env, slug, "outra coisa", blocked_by=[second])
    assert client.post(f"/api/tasks/{second}/dependencies", headers=env["h"]["req"], json={"on": third}).status_code == 400


def test_a_failed_dependency_leaves_the_task_stuck_until_removed(client, env, rt):
    slug = _active(client, env, rt)
    first = _new(client, env, slug, "base")
    second = _new(client, env, slug, "depois", blocked_by=[first])
    client.post(f"/api/tasks/{first}/cancel", headers=env["h"]["req"])
    t = task(client, env, second)
    assert t["status"] == "blocked" and any(ev["kind"] == "dependency_failed" for ev in t["events"])
    assert t["diagnosis"]["failed_deps"] == [first]
    r = client.delete(f"/api/tasks/{second}/dependencies/{first}", headers=env["h"]["req"])
    assert r.status_code == 200 and task(client, env, second)["status"] == "new"


# ------------------------------------------------------------------ revisão
def test_review_holds_the_result_until_the_reviewer_approves_or_asks_for_changes(client, env, rt):
    slug = _active(client, env, rt)
    tid = _new(client, env, slug, "rascunho do contrato", review=True)
    work()
    t = task(client, env, tid)
    assert t["status"] == "in_review" and t["result"] and t["diagnosis"]["state"] == "decision"
    req = open_request(client, env, tid)
    assert req["kind"] == "review"
    assert client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "reject"}).status_code == 400
    r = client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "reject", "reason": "inclua a multa"})
    assert r.status_code == 200, r.text
    assert task(client, env, tid)["status"] == "new" and "MUDANÇAS PEDIDAS" in _last_user_text(tid)
    work()
    assert task(client, env, tid)["status"] == "in_review"
    req = open_request(client, env, tid)
    client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    assert task(client, env, tid)["status"] == "done"
    # política do cargo: todo resultado passa pelo gestor
    client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"review_policy": "manager"})
    t2 = _new(client, env, slug, "outra tarefa")
    work()
    assert task(client, env, t2)["status"] == "in_review"


# ------------------------------------------------------------------ arquivos
def test_files_inputs_outputs_versions_and_library(client, env, rt):
    slug = _active(client, env, rt)
    tid = _new(client, env, slug, "analise a planilha")
    r = client.post(f"/api/tasks/{tid}/files?name=contratos.csv", headers={**env["h"]["req"], "Content-Type": "text/csv"},
                    content=b"cliente,valor\nACME,100\n")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "input" and r.json()["version"] == 1 and r.json()["content_type"] == "text/csv"
    assert client.post(f"/api/tasks/{tid}/files?name=x.txt", headers=env["h"]["out"], content=b"x").status_code == 403
    assert client.post(f"/api/tasks/{tid}/files?name=", headers=env["h"]["req"], content=b"x").status_code == 400
    odd = client.post(f"/api/tasks/{tid}/files?name=../../etc/passwd", headers=env["h"]["req"], content=b"x").json()
    assert odd["name"] == "passwd"  # o caminho some: fica só o nome
    # o agente lê o anexo e salva o entregável (duas vezes: versão 2)
    with SessionLocal() as db:
        a = db.query(Agent).filter_by(slug=slug).one()
        assert "contratos.csv v1" in tasksmod.gate(db, a, tid, "list_files", "hangar:list_files", {}, "", "files")["result"]
        assert "ACME,100" in tasksmod.gate(db, a, tid, "read_file", "hangar:read_file", {"name": "contratos.csv"}, "", "files")["result"]
        for text in ("# Relatório v1", "# Relatório v2"):
            out = tasksmod.gate(db, a, tid, "save_file", "hangar:save_file", {"name": "relatorio.md", "content": text}, "", "files")
            assert out["status"] == "allowed" and "salvo" in out["result"]
        other = db.query(EmployeeTask).filter(EmployeeTask.id != tid).first()
        denied = tasksmod.gate(db, a, other.id, "list_files", "hangar:list_files", {}, "", "files")
        assert denied["status"] == "denied"  # só a tarefa do próprio funcionário
    files = {f["name"]: f for f in client.get(f"/api/tasks/{tid}/files", headers=env["h"]["req"]).json()}
    rep = files["relatorio.md"]
    assert rep["kind"] == "output" and rep["version"] == 2 and len(rep["history"]) == 1 and rep["uploaded_by"].startswith("agent:")
    got = client.get(f"/api/files/{rep['id']}?inline=1", headers=env["h"]["req"])
    assert got.text == "# Relatório v2" and got.headers["content-type"].startswith("text/markdown")
    assert "sandbox" in got.headers["content-security-policy"]
    assert client.get(f"/api/files/{rep['id']}", headers=env["h"]["out"]).status_code == 403
    html = client.post(f"/api/tasks/{tid}/files?name=x.html", headers={**env["h"]["req"], "Content-Type": "text/html"},
                       content=b"<script>alert(1)</script>").json()
    raw = client.get(f"/api/files/{html['id']}?inline=1", headers=env["h"]["req"])
    assert raw.headers["content-type"] == "application/octet-stream" and raw.headers["content-disposition"].startswith("attachment")
    lib = client.get("/api/files", headers=env["h"]["req"]).json()
    assert any(f["name"] == "relatorio.md" and f["version"] == 2 for f in lib)
    assert all(f["task_id"] != tid for f in client.get("/api/files", headers=env["h"]["out"]).json())
    # o anexo chega ao funcionário pela conversa
    assert any("arquivo anexado: contratos.csv" in m["body"] for m in task(client, env, tid)["messages"])


# ------------------------------------------------------------------ continuar uma tarefa com falha
def test_continue_a_failed_task_asks_to_confirm_possible_side_effects(client, env, rt):
    slug = _active(client, env, rt)
    tid = _new(client, env, slug, "atualize o CRM")
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        tasksmod.event(db, t, "run", {"run": 1})
        db.add(TaskEvent(task_id=tid, kind="gate", payload={"tool": "update_crm", "action_type": "write_internal", "mode": "auto"}))
        t.status, t.error, t.finished_at, t.runs = "failed", "LLM fora do ar", now(), 12
        db.commit()
    d = task(client, env, tid)["diagnosis"]
    assert d["state"] == "failed" and d["side_effects"] == ["update_crm (write_internal)"] and d["actions"] == ["continue"]
    r = client.post(f"/api/tasks/{tid}/continue", headers=env["h"]["req"], json={})
    assert r.status_code == 400 and "update_crm" in r.json()["detail"]
    r = client.post(f"/api/tasks/{tid}/continue", headers=env["h"]["req"], json={"confirm": True, "note": "já atualizei"})
    assert r.json() == {"status": "new", "extra_runs": 3}
    assert "NÃO repita o que já foi feito: update_crm" in _last_user_text(tid)
    work()  # tinha estourado as 12 rodadas: com as extras, roda de novo
    assert task(client, env, tid)["status"] == "done"
    assert client.post(f"/api/tasks/{tid}/continue", headers=env["h"]["req"], json={}).status_code == 400  # não falhou


# ------------------------------------------------------------------ pausar
def test_hold_pauses_the_task_and_resume_puts_it_back(client, env, rt):
    slug = _active(client, env, rt)
    tid = _new(client, env, slug, "espere")
    assert client.post(f"/api/tasks/{tid}/hold", headers=env["h"]["out"], json={"on": True}).status_code == 403
    assert client.post(f"/api/tasks/{tid}/hold", headers=env["h"]["req"], json={"on": True}).json()["changed"] == 1
    work()
    assert task(client, env, tid)["status"] == "on_hold"
    client.post(f"/api/tasks/{tid}/hold", headers=env["h"]["mgr"], json={"on": False})
    work()
    assert task(client, env, tid)["status"] == "done"


# ------------------------------------------------------------------ histórico do cargo
def test_job_revisions_record_changes_and_restore(client, env, rt):
    slug = hire(client, env, name=f"Revisada {env['n']}")
    revs = client.get(f"/api/employees/{slug}/revisions", headers=env["h"]["own"]).json()
    assert len(revs) == 1 and revs[0]["summary"] == "contratação"
    client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"mission": "Nova missão", "task_budget_usd": 3})
    client.patch(f"/api/employees/{slug}", headers=env["h"]["own"], json={"mission": "Nova missão"})  # sem mudança: não grava
    revs = client.get(f"/api/employees/{slug}/revisions", headers=env["h"]["own"]).json()
    assert [r["version"] for r in revs] == [2, 1] and set(revs[0]["changed"]) == {"mission", "task_budget_usd"}
    assert client.post(f"/api/employees/{slug}/revisions/1/restore", headers=env["h"]["req"]).status_code == 403
    d = client.post(f"/api/employees/{slug}/revisions/1/restore", headers=env["h"]["own"]).json()
    assert d["mission"] == revs[1]["snapshot"]["mission"] and d["task_budget_usd"] == revs[1]["snapshot"]["task_budget_usd"]
    revs = client.get(f"/api/employees/{slug}/revisions", headers=env["h"]["own"]).json()
    assert revs[0]["version"] == 3 and revs[0]["summary"] == "restaurada a v1"
