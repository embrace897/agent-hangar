"""E2E do trabalho do dia a dia (fases A a D), contra um Agent Hangar rodando de verdade (modo mock, sem LLM).

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> ECHO_URL=http://e2e-echo:8080 python tests/e2e/work.py [--keep FILE]

Usa as pessoas e o Digital employee (mock) de tests/e2e/workforce.py. Exercita metas e projetos (o contexto chega ao
agente), a conversa da tarefa (entregue ao agente e reabrindo uma tarefa concluída), orçamentos, a caixa de entrada e a
busca. Apaga tudo no fim; com --keep FILE, mantém os dados e grava em FILE o usuário e a senha (de teste) da gestora,
para conferir as telas no navegador — apague o arquivo e rode sem --keep depois.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import workforce as wf  # noqa: E402  (pessoas, contratação e ativação do Digital employee mock)

ok, check, admin, RUN = wf.ok, wf.check, wf.admin, wf.RUN


def phase_a(dev, mgr, mgr_email, team):
    slug = wf.hire(dev, mgr_email, team, "Analyst")
    wf.activate(dev, mgr, slug)
    company = ok(admin.post("/api/goals", json={"title": f"Grow recurring revenue {RUN}"}))
    goal = ok(mgr.post("/api/goals", json={"title": "No renewal lost", "team": team, "parent_id": company["id"]}))
    proj = ok(dev.post("/api/projects", json={"name": f"Q4 renewals {RUN}", "team": team, "goal_id": goal["id"],
                                              "instructions": "Prioritise contracts above 50k.", "default_employee": slug}))
    check("projeto: cadeia de metas", [g["title"] for g in proj["goal_chain"]] == ["No renewal lost", company["title"]], proj)
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Renew ACME", "body": "contract ends in 30 days",
                                                            "project": proj["slug"]}))["id"]
    ok(dev.post(f"/api/tasks/{tid}/messages", json={"body": "also include Globex"}))
    t = wf.until(dev, tid, ("done",))
    check("tarefa do projeto concluída", t is not None, t)
    check("tarefa: projeto e metas", t["project"]["slug"] == proj["slug"] and t["goals"][0]["title"] == "No renewal lost", t)
    msgs = t["messages"]
    check("conversa: a mensagem foi entregue e o agente respondeu",
          any(m["body"] == "also include Globex" and m["status"] == "delivered" for m in msgs) and any(m["agent"] for m in msgs), msgs)
    r = ok(dev.post(f"/api/tasks/{tid}/messages", json={"body": "now send it to the manager"}))
    check("conversa: mensagem reabre a tarefa concluída", r["task_status"] == "new", r)
    t = wf.until(dev, tid, ("done",))
    check("conversa: reaberta e concluída de novo", t is not None and sum(m["agent"] for m in t["messages"]) >= 2, t)
    box = ok(dev.get("/api/inbox"))
    check("caixa: a tarefa que eu pedi", any(i["key"] == f"task:{tid}" for i in box["items"]), box["counts"])
    found = ok(dev.get("/api/search", params={"q": "Renew ACME"}))["items"]
    check("busca: acha a tarefa", any(x["url"] == f"#/tasks/{tid}" for x in found), found)
    pol = ok(mgr.post("/api/budgets", json={"scope_type": "project", "scope": proj["slug"], "amount_usd": 25, "window": "month"}))
    check("orçamento do projeto", pol["state"] == "ok" and pol["can_manage"], pol)
    proj = ok(dev.get(f"/api/projects/{proj['slug']}"))
    check("projeto: as tarefas e o custo", proj["tasks"] == 1 and proj["done"] == 1, proj)
    return slug, proj["slug"], tid


def phase_b(dev, mgr, slug):
    first = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Collect contracts", "body": "list them"}))["id"]
    second = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Summarise", "body": "summary",
                                                               "blocked_by": [first], "review": True}))["id"]
    check("dependência: espera a anterior", wf.task(dev, second)["status"] in ("blocked", "new", "in_progress", "in_review"))
    wf.until(dev, first, ("done",))
    t = wf.until(dev, second, ("in_review",))
    check("revisão: o resultado espera o revisor", t is not None and t["diagnosis"]["state"] == "decision", t and t["status"])
    req = next(d for d in ok(mgr.get("/api/decisions")) if d["kind"] == "review" and d["task"]["id"] == second)
    ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "reject", "reason": "add the totals"}))
    t = wf.until(dev, second, ("in_review",))
    req = next(d for d in ok(mgr.get("/api/decisions")) if d["kind"] == "review" and d["task"]["id"] == second)
    ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
    t = wf.task(dev, second)
    check("revisão: mudanças pedidas, depois aprovada", t["status"] == "done" and
          any(ev["kind"] == "review_decision" for ev in t["events"]), t["status"])
    # entregável salvo pelo agente (mock: "use save_file {json}") e anexo lido por ele
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Write the report", "body":
                                                            'use save_file {"name": "report.md", "content": "# Q4"}'}))["id"]
    wf.until(dev, tid, ("done",))
    files = ok(dev.get(f"/api/tasks/{tid}/files"))
    check("arquivos: o entregável salvo pelo agente", any(f["name"] == "report.md" and f["kind"] == "output" for f in files), files)
    rep = next(f for f in files if f["name"] == "report.md")
    check("arquivos: download", dev.get(f"/api/files/{rep['id']}?inline=1").text == "# Q4")
    up = dev.post(f"/api/tasks/{tid}/files?name=notes.txt", content=b"remember ACME", headers={"Content-Type": "text/plain"})
    check("arquivos: anexo de quem pediu", up.status_code == 200 and up.json()["kind"] == "input", up.text[:200])
    lib = ok(dev.get("/api/files"))
    check("biblioteca de entregáveis", any(f["name"] == "report.md" for f in lib), len(lib))
    revs = ok(mgr.get(f"/api/employees/{slug}/revisions"))
    check("histórico do cargo", revs and revs[-1]["version"] == 1, revs[:1])


def main():
    keep = sys.argv[sys.argv.index("--keep") + 1] if "--keep" in sys.argv else ""
    check("health", ok(admin.get("/api/health"))["status"] == "ok")
    team = ok(admin.post("/api/teams", json={"name": f"E2E work {RUN}"}))["slug"]
    wf.created["team"] = team
    mgr, mgr_email, _ = wf.person("mgr", "maintainer", team)
    dev, _, _ = wf.person("dev", "developer", team)
    slug, _, _ = phase_a(dev, mgr, mgr_email, team)
    phase_b(dev, mgr, slug)
    print(f"\n{wf.passed} checks ok", flush=True)
    if keep:
        with open(keep, "w", encoding="utf-8") as f:
            f.write(f"{wf.USERS['mgr']}\n")
        print(f"dados mantidos; login da gestora em {keep}", flush=True)


if __name__ == "__main__":
    keep = "--keep" in sys.argv
    try:
        main()
    finally:
        if not keep:
            wf.cleanup()
        sys.stdout.flush()
