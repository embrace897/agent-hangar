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
LEFTOVERS: list[tuple[str, str]] = []  # o que é da empresa (não do time): arquivado ou descontinuado no fim


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


def phase_c(dev, mgr, mgr_email, team, slug):
    chief = wf.hire(dev, mgr_email, team, "Coordinator")
    r = ok(dev.patch(f"/api/employees/{slug}", json={"reports_to": chief}))
    check("organograma: o chefe", r["reports_to"] == chief, r.get("reports_to"))
    nodes = {n["slug"]: n for n in ok(dev.get("/api/org-chart", params={"team": team}))["nodes"]}
    check("organograma: a árvore", nodes[slug]["reports_to"] == chief, nodes.get(slug))
    skill = f"e2e-tone-{RUN}"
    ok(dev.put(f"/api/skills/{skill}", json={"description": "tone", "content": "Be brief."}))
    ok(dev.put(f"/api/skills/{skill}/cases", json={"cases": [{"name": "hi", "input": "say hi", "expect_contains": "hi"}]}))
    run = ok(dev.post(f"/api/skills/{skill}/test", json={}))
    check("Skill Studio: rodada (mock)", run["passed"] and run["mock"], run)
    ok(dev.put(f"/api/skills/{skill}", json={"description": "tone", "content": "Be brief and kind."}))
    back = ok(dev.post(f"/api/skills/{skill}/restore/1"))
    check("Skill Studio: restaurar a v1", back["version"] == 3 and back["content"] == "Be brief.", back["version"])
    cat = ok(mgr.get("/api/packages/catalog"))
    check("pacotes: catálogo", any(c["id"] == "renewals-team" for c in cat), [c["id"] for c in cat])
    out = ok(mgr.post("/api/packages/install", json={"catalog": "renewals-team", "team": team, "manager": mgr_email}))
    wf.created["agents"].extend(out["created"]["employees"])
    check("pacotes: time pronto instalado em rascunho", len(out["created"]["employees"]) == 2 and out["created"]["projects"], out["created"])
    pkg = ok(dev.post("/api/packages/export", json={"team": team}))
    check("pacotes: exportar o time", pkg["kind"] == "hangar-package" and len(pkg["employees"]) >= 3, len(pkg["employees"]))
    key = f"e2e-key-{RUN}"
    ok(mgr.put("/api/secrets", json={"name": key, "value": f"v-{RUN}", "team": team}))
    prop = ok(dev.post("/api/secrets/proposals", json={"name": key, "value": f"v2-{RUN}", "team": team}))
    d = ok(mgr.post(f"/api/secrets/proposals/{prop['id']}/decide", json={"approve": True}))
    lst = ok(mgr.get("/api/secrets"))
    check("cofre: proposta aprovada (quatro olhos), sem valores na API",
          d["secret"]["version"] == 2 and f"v2-{RUN}" not in str(lst), d["secret"]["version"])
    ok(mgr.delete(f"/api/secrets/{d['secret']['id']}"))


def phase_d(dev, mgr, slug):
    import subprocess
    # várias empresas: a nova não vê nada da principal; quem administra a instalação entra nela pelo X-Hangar-Org
    org = ok(admin.post("/api/orgs", json={"name": f"E2E Beta {RUN}", "admin_email": f"boss-{RUN}@beta-e2e.test",
                                           "domains": [f"beta-{RUN}.test"]}))
    inside = {"X-Hangar-Org": str(org["id"])}
    teams = ok(admin.get("/api/teams", headers=inside))
    check("empresas: a nova só vê o próprio time", [t["slug"] for t in teams] == [org["team"]], teams)
    check("empresas: os agentes da principal não aparecem",
          all(a["slug"] != slug for a in ok(admin.get("/api/agents", headers=inside))), org["id"])
    check("empresas: o funcionário da principal não existe lá", admin.get(f"/api/employees/{slug}", headers=inside).status_code in (400, 403, 404))
    ok(admin.patch(f"/api/orgs/{org['id']}", json={"status": "suspended"}))  # a empresa de teste fica suspensa
    # Digital employee externo: o worker de exemplo busca a tarefa, pede a alçada e entrega o resultado
    r = ok(dev.put(f"/api/employees/{slug}/runtime", json={"runtime": "external"}))
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "External run", "body": "summarise the renewals"}))["id"]
    worker = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "external-worker", "worker.py")
    env = {**os.environ, "HANGAR_URL": wf.URL, "HANGAR_WORKER_TOKEN": r["token"], "WORKER_NAME": f"e2e-{RUN}"}
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY"):
        env.pop(k, None)
    out = subprocess.run([sys.executable, worker, "--once"], env=env, capture_output=True, text=True, timeout=120)
    t = wf.task(dev, tid)
    check("worker externo: a tarefa feita fora", t["status"] == "done" and "summarise the renewals" in (t["result"] or ""),
          (t["status"], out.stdout[-300:], out.stderr[-300:]))
    ext = ok(dev.get(f"/api/employees/{slug}"))["external"]
    check("worker externo: online, com nome", ext["online"] and ext["worker"].get("name") == f"e2e-{RUN}", ext)
    ok(dev.put(f"/api/employees/{slug}/runtime", json={"runtime": "hangar"}))


def phase_e(dev, mgr, team, slug):
    import base64
    import json
    # extração com esquema: um lote de dois contratos; o de CNPJ inválido vai para conferência campo a campo
    fields = [{"name": "contratante", "label": "Contratante", "type": "text", "required": True},
              {"name": "cnpj", "label": "CNPJ", "type": "cnpj", "required": True},
              {"name": "valor", "label": "Valor", "type": "money", "required": True}]
    sch = f"contrato_{RUN}"
    ok(mgr.put("/api/extraction/schemas", json={"name": sch, "title": "Contrato", "fields": fields, "team": team}))
    LEFTOVERS.append(("DELETE", f"/api/extraction/schemas/{sch}"))
    docs = {"acme.txt": "Contratante: ACME\nCNPJ: 11.222.333/0001-81\nValor: 1.500,00\n",
            "beta.txt": "Contratante: Beta\nCNPJ: 11.222.333/0001-00\nValor: 900,00\n"}
    b = ok(dev.post(f"/api/employees/{slug}/batch", json={"title": f"Contratos {RUN}", "schema": sch, "files": [
        {"name": n, "content_base64": base64.b64encode(d.encode()).decode(), "content_type": "text/plain"} for n, d in docs.items()]}))
    review = wf.wait(lambda: next((d for d in ok(mgr.get("/api/decisions")) if d["kind"] == "review_fields"
                                   and d["task"] and d["task"]["id"] in b["tasks"]), None))
    check("extração: o campo em dúvida vai para conferência", review is not None and review["payload"]["fields"]["cnpj"]["error"] == "CNPJ inválido",
          review and review["payload"]["fields"]["cnpj"])
    ok(mgr.post(f"/api/decisions/{review['id']}", json={"decision": "approve", "edit": {"fields": {"cnpj": "11222333000181"}}}))
    p = wf.until(dev, b["parent"], ("done", "failed"))
    files = {f["name"]: f for f in ok(dev.get(f"/api/tasks/{b['parent']}/files"))}
    csv = dev.get(f"/api/files/{files[f'{sch}-consolidado.csv']['id']}").text if f"{sch}-consolidado.csv" in files else ""
    check("extração: lote consolidado com o valor conferido", p and p["status"] == "done" and "ACME" in csv and "11.222.333/0001-81" in csv
          and csv.count("\n") >= 3, (p and p["status"], csv[:300]))
    # calculadora: quem escreve não certifica; a conta sai com memória de cálculo e passa por revisão
    calc = {"name": f"multa_{RUN}", "title": "Multa", "team": team, "requires_review": True,
            "inputs": [{"name": "valor"}, {"name": "taxa"}], "steps": [{"name": "multa", "expr": "round(pct(valor, taxa), 2)"}],
            "outputs": ["multa"], "cases": [{"name": "base", "inputs": {"valor": 1000, "taxa": 10}, "expected": {"multa": 100}}]}
    ok(dev.put("/api/calculators", json=calc))
    LEFTOVERS.append(("POST", f"/api/calculators/multa_{RUN}/deprecate"))
    check("calculadora: quatro olhos", dev.post(f"/api/calculators/multa_{RUN}/certify").status_code == 403)
    ok(mgr.post(f"/api/calculators/multa_{RUN}/certify"))
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Multa", "body": "use calculate " + json.dumps(
        {"calculator": f"multa_{RUN}", "inputs": {"valor": 2500, "taxa": 2}})}))["id"]
    t = wf.until(dev, tid, ("in_review", "done", "failed"))
    rev = next((d for d in ok(mgr.get("/api/decisions")) if d["kind"] == "review" and d["task"]["id"] == tid), None)
    if rev:
        ok(mgr.post(f"/api/decisions/{rev['id']}", json={"decision": "approve"}))
    files = {f["name"]: f for f in ok(dev.get(f"/api/tasks/{tid}/files"))}
    memo = json.loads(dev.get(f"/api/files/{files[f'memoria-de-calculo-multa_{RUN}.json']['id']}").text) if files else {}
    check("calculadora: conta certificada, memória e revisão", t and t["status"] == "in_review" and memo.get("outputs") == {"multa": "50.00"},
          (t and t["status"], memo))
    # análise sobre os arquivos da tarefa (só com o Data Studio no ar)
    if ok(dev.get("/api/brand")).get("data_studio"):
        code = "df = pd.read_csv('extrato.csv'); r = df.groupby('conta').valor.sum(); print(r.to_dict()); r.to_csv('por_conta.csv')"
        CSV = b"conta,valor\nA,10\nB,20\nA,5\n"
        tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Análise", "body": "use analyze_data " + json.dumps({"code": code}),
                                                                "files": [{"name": "extrato.csv", "content_type": "text/csv",
                                                                           "content_base64": base64.b64encode(CSV).decode()}]}))["id"]
        t = wf.until(dev, tid, ("done", "failed"), 300)
        names = [f["name"] for f in ok(dev.get(f"/api/tasks/{tid}/files"))]
        check("análise: Python sobre o arquivo da tarefa, entregável de volta", t and t["status"] == "done"
              and "'A': 15" in (t["result"] or "") and "por_conta.csv" in names, (t and t["result"], names))
    else:
        print("   (Data Studio desligado: análise não testada)", flush=True)


def phase_f(dev, mgr, mgr_email, team, slug):
    import json
    # fila: "nota" vai para o Digital employee; o resto, para as pessoas (que pegam a próxima)
    q = ok(mgr.put("/api/queues", json={"name": f"Backoffice {RUN}", "team": team, "categories": [
        {"name": "Nota", "keywords": ["nota"], "sla_hours": 4, "route": {"type": "employee", "refs": [slug]}},
        {"name": "Geral", "keywords": [], "sla_hours": 24, "route": {"type": "people"}}], "default_category": "Geral"}))["slug"]
    LEFTOVERS.append(("DELETE", f"/api/queues/{q}"))
    a = ok(dev.post(f"/api/queues/{q}/items", json={"title": "Lançar a nota 991", "body": "nota do fornecedor"}))
    b = ok(dev.post(f"/api/queues/{q}/items", json={"title": "Ajustar um cadastro"}))
    item = wf.wait(lambda: (lambda i: i if i["status"] == "done" else None)(ok(dev.get(f"/api/work-items/{a['id']}"))))
    got = ok(mgr.post(f"/api/queues/{q}/next"))
    ok(mgr.post(f"/api/work-items/{got['id']}", json={"action": "done", "note": "feito"}))
    stats = next(x for x in ok(mgr.get("/api/queues")) if x["slug"] == q)["stats"]
    check("fila: o Digital employee resolve, a pessoa pega a próxima", item is not None and got["id"] == b["id"]
          and stats["automatic"] == 1 and stats["human"] == 1, stats)
    # playbook: Digital employee → pessoa → Digital employee, com prazos em dias úteis
    steps = [{"key": "extrair", "title": "Extrair", "assignee": {"type": "employee", "ref": slug}, "due": {"business_days": 1}},
             {"key": "conferir", "title": "Conferir", "assignee": {"type": "person", "ref": mgr_email}, "depends_on": ["extrair"],
              "due": {"from": "extrair", "business_days": 1}},
             {"key": "relatorio", "title": "Relatório", "assignee": {"type": "employee", "ref": slug}, "depends_on": ["conferir"],
              "due": {"from": "conferir", "business_days": 1}}]
    pb = ok(dev.put("/api/playbooks", json={"name": f"fech_{RUN}", "team": team, "title": "Fechamento", "steps": steps}))
    run = ok(dev.post(f"/api/playbooks/{pb['name']}/runs", json={}))
    conf = next(s for s in run["steps"] if s["key"] == "conferir")["id"]
    ready = wf.wait(lambda: (lambda i: i if i["status"] in ("in_progress", "open") else None)(ok(mgr.get(f"/api/work-items/{conf}"))))
    ok(mgr.post(f"/api/work-items/{conf}", json={"action": "done", "note": "conferido"}))
    final = wf.wait(lambda: (lambda r: r if r["status"] == "done" else None)(ok(mgr.get(f"/api/playbook-runs/{run['id']}"))))
    check("playbook: etapas em ordem, da pessoa e dos Digital employees", ready is not None and final is not None
          and "ETAPA PRÉVIA" in ready["body"], (ready and ready["status"], final and final["status"]))
    LEFTOVERS.append(("DELETE", f"/api/playbooks/{pb['name']}"))
    # reunião: a transcrição vira tarefa de ata; as ações viram item da pessoa e tarefa do funcionário
    m = ok(mgr.post("/api/meetings", json={"title": f"Comitê {RUN}", "team": team, "employee": slug, "series": f"comite-{RUN}",
                                           "attendees": [mgr_email]}))
    LEFTOVERS.append(("DELETE", f"/api/meetings/{m['id']}"))
    m = ok(mgr.post(f"/api/meetings/{m['id']}/transcript", json={"text": "Ana: revisar o preço. Bot: rascunhar o aditivo."}))
    wf.until(dev, m["minutes_task"], ("done", "failed"))
    rec = {"meeting_id": m["id"], "summary": "Revisar preço e rascunhar o aditivo.", "decisions": ["renovar"],
           "actions": [{"title": "Revisar preço", "owner": mgr_email}, {"title": "Rascunhar o aditivo", "owner": slug}]}
    ok(mgr.post(f"/api/tasks/{m['minutes_task']}/messages", json={"body": "use record_minutes " + json.dumps(rec)}))
    d = wf.wait(lambda: (lambda x: x if x["status"] == "minutes" else None)(ok(mgr.get(f"/api/meetings/{m['id']}"))))
    check("reunião: ata e ações viram trabalho", d is not None and {a["kind"] for a in d["actions"]} == {"item", "task"},
          d and d["actions"])


def main():
    keep = sys.argv[sys.argv.index("--keep") + 1] if "--keep" in sys.argv else ""
    check("health", ok(admin.get("/api/health"))["status"] == "ok")
    team = ok(admin.post("/api/teams", json={"name": f"E2E work {RUN}"}))["slug"]
    wf.created["team"] = team
    mgr, mgr_email, _ = wf.person("mgr", "maintainer", team)
    dev, _, _ = wf.person("dev", "developer", team)
    slug, _, _ = phase_a(dev, mgr, mgr_email, team)
    phase_b(dev, mgr, slug)
    phase_c(dev, mgr, mgr_email, team, slug)
    phase_d(dev, mgr, slug)
    phase_e(dev, mgr, team, slug)
    phase_f(dev, mgr, mgr_email, team, slug)
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
            for method, path in LEFTOVERS:
                admin.request(method, path)
            wf.cleanup()
        sys.stdout.flush()
