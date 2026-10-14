# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Aprendizado da organização: a tarefa que deu certo propõe um artigo (lacuna da base respondida) ou um procedimento
(caminho no navegador); uma pessoa aprova com edição; o reaproveitamento é contado."""
from datetime import timedelta

from app.db import SessionLocal
from app.models import EmployeeTask, KnowledgeGap, LearnedArtifact, Procedure, TaskEvent, now
from app.services import employee_tasks as tasksmod
from app.services import org_learning

from .test_employees import assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401
from .test_phase_g import _close_leftover_tasks, _gate  # noqa: F401

ANSWER = ("O prazo para pedir reembolso de despesas internacionais é de 45 dias corridos após a volta, com a fatura do "
          "cartão e a cotação do dia anotada no pedido.")


def _done(tid, result=ANSWER, score=None):
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        t.status, t.result, t.finished_at = "done", result, now() - timedelta(minutes=5)
        if score is not None:
            tasksmod.event(db, t, "quality", {"score": score, "by": "judge"})
        db.commit()


def test_a_knowledge_gap_answered_becomes_an_article_approved_with_edits_and_reused(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    src = client.put("/api/knowledge/sources", headers=env["h"]["mgr"], json={
        "name": f"pol_{env['n']}", "kind": "upload", "teams": [env["team"]]}).json()["name"]
    client.post(f"/api/knowledge/sources/{src}/documents", headers=env["h"]["mgr"], json={"title": "Viagens", "text": "Hotel nacional até R$ 400."})
    tid = assign(client, env, slug, "prazo de reembolso internacional")
    g = _gate(slug, tid, "search_knowledge", {"query": "prazo reembolso despesas internacionais"})
    assert g["status"] == "allowed"
    with SessionLocal() as db:
        assert db.query(KnowledgeGap).filter(KnowledgeGap.task_id == tid).count() == 1  # a base não respondia
    _done(tid, score=5)
    with SessionLocal() as db:  # sem limite: tarefas de outros testes que já passaram dos 2 minutos entram no mesmo lote
        assert org_learning.sweep(db, limit=100_000) >= 1
        assert db.query(TaskEvent).filter(TaskEvent.task_id == tid, TaskEvent.kind == "learning_checked").count() == 1
        assert org_learning.sweep(db, limit=100_000) == 0  # uma vez só
    req = open_request(client, env, tid)
    assert req["kind"] == "learning" and req["payload"]["kind"] == "article" and "45 dias" in req["payload"]["content"]["text"]
    r = client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={
        "decision": "approve_edited", "edit": {"title": "Reembolso internacional: prazo", "text": ANSWER + " Vale desde 2026."}})
    assert r.status_code == 200, r.text
    arts = client.get("/api/learned", headers=env["h"]["mgr"]).json()
    a = next(x for x in arts if x["task"] == tid)
    assert a["status"] == "approved" and a["title"] == "Reembolso internacional: prazo" and a["target"].startswith("kb:")
    with SessionLocal() as db:
        assert db.query(KnowledgeGap).filter(KnowledgeGap.task_id == tid).one().resolved  # a lacuna se fechou
    # a próxima tarefa acha o artigo — e isso conta como reaproveitamento
    tid2 = assign(client, env, slug, "de novo")
    g = _gate(slug, tid2, "search_knowledge", {"query": "prazo reembolso internacional"})
    assert "Reembolso internacional" in g["result"]
    assert next(x for x in client.get("/api/learned", headers=env["h"]["mgr"]).json() if x["id"] == a["id"])["uses"] == 1
    s = client.get("/api/learned/summary", headers=env["h"]["mgr"]).json()
    assert s["approved"] >= 1 and s["uses"] >= 1


def test_a_browser_path_becomes_a_procedure(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "baixar o extrato no portal do banco")
    with SessionLocal() as db:
        t = db.get(EmployeeTask, tid)
        for p in ({"action": "open", "target": "https://portal.banco.test/login", "domains": ["banco.test"]},
                  {"action": "fill", "target": "Usuário", "value": "ana"}, {"action": "fill", "target": "Senha", "value": "[segredo]"},
                  {"action": "click", "target": "Entrar"}):
            db.add(TaskEvent(task_id=t.id, kind="browser", payload=p, at=now()))
        db.commit()
    _done(tid, result="Extrato baixado e salvo na tarefa.")
    with SessionLocal() as db:
        assert org_learning.sweep(db) == 1
    req = open_request(client, env, tid)
    steps = req["payload"]["content"]["steps"]
    assert req["payload"]["kind"] == "procedure" and len(steps) == 4 and steps[2]["value"] == ""  # o segredo não fica no passo
    client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    with SessionLocal() as db:
        p = db.query(Procedure).filter(Procedure.name == f"aprendido_{tid}").one()
        assert p.domains == ["banco.test"] and [s["action"] for s in p.steps] == ["open", "fill", "fill", "click"]
        org_learning.used_procedure(db, p.name)
        db.commit()
        assert db.query(LearnedArtifact).filter(LearnedArtifact.task_id == tid).one().uses == 1


def test_no_proposal_for_poor_work_or_when_learning_is_off(client, env, rt):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "pergunta")
    _gate(slug, tid, "search_knowledge", {"query": "algo que ninguém escreveu ainda xyz"})
    _done(tid, score=2)  # o juiz deu 2: não vira conhecimento
    tid2 = assign(client, env, slug, "pergunta 2")
    _gate(slug, tid2, "search_knowledge", {"query": "outra coisa sem resposta qwe"})
    assert client.put(f"/api/employees/{slug}/learning-mode", headers=env["h"]["req"], json={"mode": "off"}).status_code == 403
    assert client.put(f"/api/employees/{slug}/learning-mode", headers=env["h"]["own"], json={"mode": "off"}).json() == {"learning_mode": "off"}
    _done(tid2, score=5)
    with SessionLocal() as db:
        assert org_learning.sweep(db) == 0
        assert db.query(LearnedArtifact).filter(LearnedArtifact.task_id.in_([tid, tid2])).count() == 0
    assert task(client, env, tid)["status"] == "done"
    work()
