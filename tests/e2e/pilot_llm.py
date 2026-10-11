"""Piloto com um LLM de verdade: os casos do dia a dia do analista em linguagem natural (sem "use <ferramenta>"),
para ver se o modelo escolhe a ferramenta certa, preenche os argumentos e entrega um resultado útil.

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> LLM_CONNECTION=openrouter-deepseek python tests/e2e/pilot_llm.py [caso ...]

Com --keep, mantém o time, o funcionário e as tarefas para investigar. Não para no primeiro erro: cada caso sai como ok/FALHA com as ferramentas que o modelo chamou e o começo do resultado.
Cria um time, duas pessoas e um Digital employee com a conexão indicada; apaga tudo no fim.
"""
import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import workforce as wf  # noqa: E402

ok, admin, RUN = wf.ok, wf.admin, wf.RUN
CONN = os.environ.get("LLM_CONNECTION", "openrouter-deepseek")
LEFTOVERS: list[tuple[str, str]] = []
REPORT: list[dict] = []


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def tools_of(t) -> list[str]:
    return [e["payload"].get("tool", "") for e in (t or {}).get("events", []) if e["kind"] in ("gate", "tool", "tool_call")
            and isinstance(e.get("payload"), dict) and e["payload"].get("tool")]


def record(case, passed, t, note=""):
    REPORT.append({"case": case, "ok": bool(passed), "status": (t or {}).get("status"), "tools": tools_of(t),
                   "result": ((t or {}).get("result") or "")[:300], "note": note})
    print(f"{'ok  ' if passed else 'FALHA'} {case}  tools={tools_of(t)}  {note}", flush=True)


def hire(dev, mgr_email, team):
    job = {"name": f"Analista piloto {RUN}", "title": "Analista de backoffice",
           "mission": "Apoiar o time financeiro e de contratos: extrair dados de documentos, fazer contas com as calculadoras "
                      "certificadas, analisar planilhas, consultar as políticas da empresa e preparar decisões.",
           "responsibilities": ["extrair dados de documentos", "calcular multas e encargos", "analisar planilhas",
                                "responder dúvidas de política", "preparar memorandos de decisão"],
           "manager": mgr_email, "team": team, "systems": ["ERP"], "llm": {"connection": CONN},
           "channels": ["portal"], "accept_default_authority": True,
           "tools": [{"type": "http", "name": "send_email", "url": f"{wf.ECHO}/send", "method": "POST"}],
           "probation_tasks": [{"title": "Quanto é 2 + 2?", "body": "Responda só o número.", "expected": "4"},
                               {"title": "Capital do Brasil", "body": "Responda só o nome da cidade.", "expected": "Brasília"},
                               {"title": "Dias úteis", "body": "Quantos dias úteis tem uma semana comum? Só o número.", "expected": "5"}]}
    d = ok(dev.post("/api/employees", json=job))
    if "employee" not in d:
        raise SystemExit(f"contratação: {json.dumps(d, ensure_ascii=False)[:600]}")
    slug = d["employee"]["slug"]
    wf.created["agents"].append(slug)
    return slug


def ask(dev, slug, title, body, files=None, mode="work"):
    j = {"title": title, "body": body, "mode": mode}
    if files:
        j["files"] = [{"name": n, "content_type": "text/plain" if n.endswith(".txt") else "text/csv",
                       "content_base64": b64(c)} for n, c in files.items()]
    return ok(dev.post(f"/api/employees/{slug}/tasks", json=j))["id"]


ANSWER = ("Não temos política formal sobre isso; use o seu julgamento com os dados da tarefa (para margem, o piso é 20%) "
          "e deixe as premissas claras.")


def finish(dev, mgr, tid, statuses=("done", "failed"), secs=400):
    """Espera a tarefa terminar respondendo, como a gestora, as perguntas que o funcionário fizer no caminho."""
    def step():
        t = wf.task(dev, tid)
        if t["status"] in statuses:
            return t
        if t["status"] == "waiting_human":
            for d in ok(mgr.get("/api/decisions")):
                if d["kind"] == "question" and d["task"] and d["task"]["id"] == tid:
                    ok(mgr.post(f"/api/decisions/{d['id']}", json={"decision": "answer", "reason": ANSWER}))
        return None
    return wf.wait(step, secs, 3)


CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


@case
def extracao(dev, mgr, team, slug):
    sch = f"contrato_{RUN}"
    ok(mgr.put("/api/extraction/schemas", json={"name": sch, "title": "Contrato", "team": team, "fields": [
        {"name": "contratante", "label": "Contratante", "type": "text", "required": True},
        {"name": "cnpj", "label": "CNPJ", "type": "cnpj", "required": True},
        {"name": "valor", "label": "Valor mensal", "type": "money", "required": True}]}))
    LEFTOVERS.append(("DELETE", f"/api/extraction/schemas/{sch}"))
    doc = ("CONTRATO DE PRESTAÇÃO DE SERVIÇOS\nPelo presente instrumento, ACME Indústria Ltda., inscrita no CNPJ sob o nº "
           "11.222.333/0001-81, doravante CONTRATANTE, ... O valor mensal dos serviços é de R$ 12.345,67 (doze mil...).")
    tid = ask(dev, slug, "Dados do contrato", f"Extraia os dados do contrato anexo usando o esquema '{sch}'.", {"contrato.txt": doc})
    t = finish(dev, mgr, tid, ("done", "failed", "in_review"))
    ex = ok(dev.get(f"/api/tasks/{tid}/extractions"))
    vals = ex[0]["values"] if ex else {}
    flat = json.dumps(vals, ensure_ascii=False)
    digits = re.sub(r"\D", "", flat)
    record("extração de campos", bool(ex) and "ACME" in flat and "11222333000181" in digits and "1234567" in digits, t, flat[:200])


@case
def calculadora(dev, mgr, team, slug):
    name = f"multa_{RUN}"
    ok(dev.put("/api/calculators", json={"name": name, "title": "Multa por atraso", "team": team,
                                         "inputs": [{"name": "valor"}, {"name": "taxa"}],
                                         "steps": [{"name": "multa", "expr": "round(pct(valor, taxa), 2)"}], "outputs": ["multa"],
                                         "cases": [{"name": "base", "inputs": {"valor": 1000, "taxa": 10}, "expected": {"multa": 100}}]}))
    LEFTOVERS.append(("POST", f"/api/calculators/{name}/deprecate"))
    ok(mgr.post(f"/api/calculators/{name}/certify"))
    tid = ask(dev, slug, "Multa da fatura 88", "Qual a multa por atraso da fatura 88? Valor R$ 2.500,00, taxa de multa 2%. "
              "Use a calculadora certificada de multa.")
    t = finish(dev, mgr, tid, ("done", "failed", "in_review"))
    record("calculadora certificada", t and "calculate" in " ".join(tools_of(t)) and "50" in (t["result"] or ""), t)


@case
def analise(dev, mgr, team, slug):
    if not ok(dev.get("/api/brand")).get("data_studio"):
        REPORT.append({"case": "análise de planilha", "ok": None, "note": "Data Studio desligado"})
        print("--    análise de planilha (Data Studio desligado)", flush=True)
        return
    csv = "conta,valor\nA,10.5\nB,20\nA,5\nC,7\nB,1\n"
    tid = ask(dev, slug, "Total por conta", "Some o valor por conta no extrato anexo e diga qual conta tem o maior total.",
              {"extrato.csv": csv})
    t = finish(dev, mgr, tid)
    r = (t or {}).get("result") or ""
    used = " ".join(tools_of(t))  # Python ou SQL: as duas servem
    record("análise de planilha", t and ("analyze_data" in used or "query_data" in used) and "21" in r and "B" in r, t)


@case
def conhecimento(dev, mgr, team, slug):
    src = ok(mgr.put("/api/knowledge/sources", json={"name": f"pol_{RUN}", "kind": "upload", "teams": [team]}))["name"]
    LEFTOVERS.append(("DELETE", f"/api/knowledge/sources/{src}"))
    ok(mgr.post(f"/api/knowledge/sources/{src}/documents", json={"title": "Política de viagens 2026",
        "text": "Hospedagem nacional: até R$ 400 por noite. Hospedagem internacional: até US$ 180 por noite. "
                "Passagens aéreas em classe econômica; executiva só em voos acima de 8 horas."}))
    tid = ask(dev, slug, "Limite de hotel", "Qual o limite de hospedagem por noite numa viagem nacional, segundo a política da empresa?")
    t = finish(dev, mgr, tid)
    record("busca na base de conhecimento", t and "search_knowledge" in " ".join(tools_of(t)) and "400" in (t["result"] or ""), t)


@case
def documento(dev, mgr, team, slug):
    pol = "Política de reembolso\n\nO prazo para pedir reembolso é de 30 dias após a despesa.\n\nForo: São Paulo."
    tid = ask(dev, slug, "Atualizar política", "Na política anexa, mude o prazo de reembolso de 30 para 45 dias. "
              "Proponha a alteração no documento para aprovação.", {"politica.txt": pol})
    req = wf.wait(lambda: next((d for d in ok(mgr.get("/api/decisions")) if d["kind"] == "doc_change" and d["task"]["id"] == tid), None)
                  or (lambda x: x if x["status"] in ("done", "failed") else None)(wf.task(dev, tid)), 300)
    if req and req.get("kind") == "doc_change":
        ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
    t = finish(dev, mgr, tid)
    f = next((x for x in ok(dev.get(f"/api/tasks/{tid}/files")) if x["name"] == "politica.txt"), None)
    body = dev.get(f"/api/files/{f['id']}").text if f else ""
    record("proposta de alteração de documento", req and req.get("kind") == "doc_change" and "45 dias" in body and "Foro" in body, t,
           f"versão {f and f['version']}")


@case
def memorando(dev, mgr, team, slug):
    ok(mgr.put(f"/api/employees/{slug}/critic", json={"mode": "auto"}))
    tid = ask(dev, slug, "Renovar ACME?", "Precisamos decidir: renovar o contrato com a ACME dando 10% de desconto, ou deixar o "
              "contrato acabar? A ACME paga R$ 200 mil/ano, margem atual 30%; com o desconto a margem cai para 22%. "
              "O time gasta 15% do tempo com a conta e há dois concorrentes interessados em fechar conosco. Prepare um "
              "memorando de decisão com opções, critérios com peso e recomendação.")
    t = finish(dev, mgr, tid)
    memos = [m for m in ok(dev.get("/api/memos")) if m["task"] == tid]
    m = memos[-1] if memos else None
    record("memorando de decisão (com revisor crítico)", m is not None and len(m["options"]) >= 2 and len(m["criteria"]) >= 2
           and m["recommendation"], t, m and f"rec={m['recommendation']} conf={m['confidence']} crítico={(m['critic'] or {}).get('severity')}")


@case
def modo_par(dev, mgr, team, slug):
    tid = ask(dev, slug, "Me ajude com o e-mail", "Mande um e-mail para cliente@acme.com cobrando a fatura 88 em atraso.", mode="copilot")
    t = finish(dev, mgr, tid)
    # perguntar à gestora pode; pedir aprovação para agir (ou agir) não
    sent = [e for e in (t or {}).get("events", []) if e["kind"] == "human_request" and e["payload"].get("kind") != "question"]
    record("modo par (orienta, não envia)", t and t["status"] == "done" and not sent and "fatura" in (t["result"] or "").lower(), t)


@case
def ouro(dev, mgr, team, slug):
    ok(mgr.post(f"/api/employees/{slug}/golden", json={"title": "Prazo de reembolso", "body": "Qual o prazo padrão para pedir "
                "reembolso de despesas? Responda em uma frase. (O prazo é 30 dias.)", "expected": "30 dias"}))
    ok(mgr.post(f"/api/employees/{slug}/golden/run"))
    run = wf.wait(lambda: (lambda g: g["runs"][0] if g["runs"] and g["runs"][0]["status"] != "running" else None)(
        ok(mgr.get(f"/api/employees/{slug}/golden"))), 900)
    REPORT.append({"case": "tarefa de ouro com juiz", "ok": bool(run and run["status"] == "passed" and run["results"][0]["score"] >= 4),
                   "note": run and f"nota {run['results'][0]['score']}"})
    print(f"{'ok  ' if REPORT[-1]['ok'] else 'FALHA'} tarefa de ouro com juiz  {REPORT[-1]['note']}", flush=True)


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    team = ok(admin.post("/api/teams", json={"name": f"Piloto LLM {RUN}"}))["slug"]
    wf.created["team"] = team
    mgr, mgr_email, _ = wf.person("mgr", "maintainer", team)
    dev, _, _ = wf.person("dev", "developer", team)
    slug = hire(dev, mgr_email, team)
    try:
        wf.activate(dev, mgr, slug)
    except SystemExit:
        for r in ok(admin.get("/api/tests")):
            if r["agent"] == slug:
                print(json.dumps(r, ensure_ascii=False)[:3000], flush=True)
        raise
    for name, fn in CASES.items():
        if only and name not in only:
            continue
        try:
            fn(dev, mgr, team, slug)
        except SystemExit as e:
            REPORT.append({"case": name, "ok": False, "note": str(e)[:300]})
            print(f"FALHA {name}  {str(e)[:300]}", flush=True)
    good = sum(1 for r in REPORT if r["ok"])
    print(f"\n{good}/{sum(1 for r in REPORT if r['ok'] is not None)} casos ok com {CONN}", flush=True)
    out = os.environ.get("PILOT_REPORT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(REPORT, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print("dados mantidos (--keep)", flush=True)
            sys.exit(0)
        for method, path in LEFTOVERS:
            admin.request(method, path)
        wf.cleanup()
        sys.stdout.flush()
