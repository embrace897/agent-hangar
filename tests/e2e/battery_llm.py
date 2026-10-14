"""Bateria com um LLM de verdade sobre o que o piloto (pilot_llm.py) não cobre: agente simples (montar, testar,
publicar, chamar), multiagente, agente com harness (job de código), leitura de imagem (pelo gateway e anexada à
tarefa de um Digital employee), o quadro de issues com um Digital employee real (rodada, comentário, sub-issue
delegada) e a governança sob o modelo real (modo sombra, DLP, orçamento).

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> LLM_CONNECTION=openrouter-deepseek ECHO_URL=http://e2e-echo:8080 \
        python tests/e2e/battery_llm.py [bloco ...] [--keep]

Não para no primeiro erro: cada verificação sai como ok/FALHA com o tempo e um trecho do que voltou; no fim, o
resumo e (com BATTERY_REPORT=<arquivo>) o relatório em JSON. Cria e apaga tudo o que usa.
"""
import base64
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import workforce as wf  # noqa: E402

admin, RUN, ok = wf.admin, wf.RUN, wf.ok
CONN = os.environ.get("LLM_CONNECTION", "openrouter-deepseek")
REPORT: list[dict] = []
T0 = time.time()


def check(block: str, name: str, cond, detail="", t0=None):
    took = round(time.time() - (t0 or T0), 1)
    REPORT.append({"block": block, "check": name, "ok": bool(cond), "secs": took, "detail": str(detail)[:500]})
    print(f"{'ok   ' if cond else 'FALHA'} [{block}] {name} ({took}s){'' if cond else '  ' + str(detail)[:400]}", flush=True)
    return bool(cond)


def png(text: str) -> str:
    """Uma imagem com texto (como a foto de um pedido): base64 de um PNG."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (720, 260), "white")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        f = ImageFont.load_default()
    for i, line in enumerate(text.split("\n")):
        d.text((30, 30 + i * 52), line, fill="black", font=f)
    b = io.BytesIO()
    im.save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


def chat(slug, content, conv="", timeout=180):
    h = {"X-Conversation-Id": conv} if conv else {}
    r = admin.post(f"/gw/{slug}/v1/chat/completions", json={"messages": [{"role": "user", "content": content}]},
                   headers=h, timeout=timeout)
    return r.status_code, (r.json()["choices"][0]["message"]["content"] if r.status_code == 200 else r.text)


AGENTS: list[str] = []


# ------------------------------------------------------------------ blocos
def simples():
    b, s = "agente simples", f"bat-{RUN}-simples"
    t0 = time.time()
    doc = {"agents": [{"name": f"Bateria simples {RUN}", "slug": s, "objective": "responder perguntas de cálculo e de texto",
                       "final_output": "resposta curta", "spec": {
                           "instructions": "Responda em português, curto e direto. Para contas, use a calculadora.",
                           "llm": {"connection": CONN}, "tools": [{"type": "builtin", "name": "calculator"}],
                           "tests": [{"input": "Quanto é 17 vezes 23? Responda só o número.", "expect_contains": "391"}]}}]}
    AGENTS.append(s)
    check(b, "montar (apply)", ok(admin.post("/api/apply", json=doc))[0]["action"] == "created", t0=t0)
    t0 = time.time()
    tr = admin.post(f"/api/agents/{s}/test", timeout=600)
    res = tr.json() if tr.status_code < 400 else {}
    check(b, "testar em stage (com o LLM)", res.get("status") == "passed", json.dumps(res, ensure_ascii=False)[:400], t0)
    t0 = time.time()
    steps = admin.post(f"/api/agents/{s}/ship", timeout=600)
    check(b, "publicar (ship)", steps.status_code == 200 and steps.json()[-1]["step"] == "prod", steps.text[:300], t0)
    t0 = time.time()
    code, txt = chat(s, "Quanto é 1234 * 5678? Responda só o número.")
    check(b, "chamar pelo gateway (OpenAI)", code == 200 and "7006652" in txt.replace(".", "").replace(",", ""), txt, t0)
    t0 = time.time()
    # o gateway é OpenAI-compatible e sem estado: o histórico vai na requisição (como o LibreChat manda)
    hist = [{"role": "user", "content": "Meu nome é Ana e meu número da sorte é 7."}, {"role": "assistant", "content": "Anotado, Ana."},
            {"role": "user", "content": "Qual é o meu nome e o meu número da sorte?"}]
    r = admin.post(f"/gw/{s}/v1/chat/completions", json={"messages": hist}, timeout=180)
    txt = r.json()["choices"][0]["message"]["content"] if r.status_code == 200 else r.text
    check(b, "conversa com histórico", r.status_code == 200 and "Ana" in txt and "7" in txt, txt, t0)
    usage = [u for u in ok(admin.get("/api/usage")) if u.get("agent") == s]
    check(b, "uso e custo registrados", usage and usage[0].get("tokens", 0) > 0, usage[:1])


def multi():
    b = "multiagente"
    calc, red, orch = f"bat-{RUN}-calc", f"bat-{RUN}-redator", f"bat-{RUN}-orq"
    AGENTS.extend([orch, calc, red])
    spec = lambda instr, extra=None: {"instructions": instr, "llm": {"connection": CONN}, **(extra or {})}  # noqa: E731
    doc = {"agents": [
        {"name": f"Calculista {RUN}", "slug": calc, "objective": "fazer contas exatas", "final_output": "número",
         "spec": spec("Faça a conta pedida com a calculadora e responda só o número.", {"tools": [{"type": "builtin", "name": "calculator"}]})},
        {"name": f"Redator {RUN}", "slug": red, "objective": "escrever frases curtas e claras", "final_output": "frase",
         "spec": spec("Escreva uma frase curta e profissional em português com o conteúdo pedido.")},
        {"name": f"Orquestrador {RUN}", "slug": orch, "objective": "resolver pedidos combinando especialistas",
         "final_output": "resposta final", "spec": spec(
             "Você coordena especialistas: delegue a conta ao calculista e a redação ao redator, e entregue a resposta final.",
             {"sub_agents": [calc, red]})}]}
    t0 = time.time()
    ok(admin.post("/api/apply", json=doc))
    steps = admin.post(f"/api/agents/{orch}/ship", timeout=900)
    check(b, "publicar o orquestrador e os especialistas", steps.status_code == 200, steps.text[:300], t0)
    t0 = time.time()
    code, txt = chat(orch, "Calcule 15% de 8.420 e escreva uma frase avisando o cliente desse desconto em reais.", timeout=300)
    check(b, "delegar conta e redação (A2A interno)", code == 200 and "1263" in txt.replace(".", "").replace(",", ""), txt, t0)
    used = {u["agent"] for u in ok(admin.get("/api/usage")) if u.get("agent") in (calc, red, orch)}
    check(b, "os especialistas trabalharam (uso registrado)", {calc, orch} <= used, used)


def harness():
    b, s = "harness", f"bat-{RUN}-codigo"
    AGENTS.append(s)
    conns = {c["name"]: c for c in ok(admin.get("/api/catalog"))["llm_connections"]}
    was = conns[CONN].get("allow_code")
    if not was:  # a política de código só deixa conexões aprovadas receberem código
        ok(admin.patch(f"/api/catalog/llm/{CONN}/code", json={"allow_code": True}))
    try:
        t0 = time.time()
        ok(admin.post("/api/apply", json={"agents": [{"name": f"Codex {RUN}", "slug": s, "objective": "escrever código Python",
                                                      "final_output": "diff", "spec": {"harness": {"id": "codex", "connection": CONN}}}]}))
        st = admin.post(f"/api/agents/{s}/ship", timeout=900)
        check(b, "publicar agente com harness (codex)", st.status_code == 200, st.text[:300], t0)
        t0 = time.time()
        j = admin.post(f"/api/agents/{s}/job", timeout=1800, json={"wait": True, "task": (
            "Crie o arquivo soma.py com a função soma(a, b) que devolve a + b, e o arquivo test_soma.py com um teste "
            "pytest para ela. Não crie outros arquivos.")})
        jj = j.json() if j.status_code < 400 else {}
        diff = jj.get("diff") or ""
        check(b, "job de código com o LLM (diff com soma.py e o teste)", jj.get("status") == "passed" and "soma.py" in diff
              and "def soma" in diff, json.dumps({k: jj.get(k) for k in ("status", "error", "summary")}, ensure_ascii=False)[:500], t0)
    finally:
        if not was:
            admin.patch(f"/api/catalog/llm/{CONN}/code", json={"allow_code": False})


def visao():
    b, s = "leitura de imagem", f"bat-{RUN}-visao"
    AGENTS.append(s)
    ok(admin.post("/api/apply", json={"agents": [{"name": f"Leitor {RUN}", "slug": s, "objective": "ler imagens e documentos",
                                                  "final_output": "dados extraídos", "spec": {
                                                      "instructions": "Leia a imagem e responda exatamente o que foi perguntado.",
                                                      "llm": {"connection": CONN}}}]}))
    admin.post(f"/api/agents/{s}/ship", timeout=600)
    img = png("PEDIDO 4721\nCLIENTE: ACME LTDA\nTOTAL: R$ 1.250,00")
    t0 = time.time()
    code, txt = chat(s, [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
                         {"type": "text", "text": "Qual o número do pedido, o cliente e o total? Responda em uma linha."}], timeout=300)
    check(b, "imagem na conversa (gateway → modelo com visão)", code == 200 and "4721" in txt and "ACME" in txt.upper()
          and "1.250" in txt, txt, t0)


def funcionario():
    """Um Digital employee com o LLM real: experiência, tarefa com anexo de imagem, o quadro de issues (rodada,
    comentário que entra na rodada, sub-issue delegada) e a governança (sombra, DLP, orçamento)."""
    b = "digital employee"
    team = ok(admin.post("/api/teams", json={"name": f"Bateria {RUN}"}))["slug"]
    wf.created["team"] = team
    mgr, mgr_email, _ = wf.person("mgr", "maintainer", team)
    dev, _, _ = wf.person("dev", "developer", team)
    job = lambda name, title, extra=None: {  # noqa: E731
        "name": f"{name} {RUN}", "title": title, "mission": f"Atender o time como {title.lower()}.",
        "responsibilities": ["entender o pedido", "fazer o trabalho com as ferramentas", "relatar o resultado"],
        "manager": mgr_email, "team": team, "systems": ["e-mail"], "llm": {"connection": CONN, "max_steps": 20},
        "channels": ["portal"], "accept_default_authority": True,
        "tools": [{"type": "http", "name": "send_email", "url": f"{wf.ECHO}/send", "method": "POST"}],
        "probation_tasks": [{"title": "Quanto é 2 + 2?", "body": "Responda só o número.", "expected": "4"},
                            {"title": "Capital do Brasil", "body": "Responda só o nome da cidade.", "expected": "Brasília"},
                            {"title": "Dias úteis", "body": "Quantos dias úteis tem uma semana comum? Só o número.", "expected": "5"}],
        **(extra or {})}
    t0 = time.time()
    slug = ok(dev.post("/api/employees", json=job("Analista", "Analista de operações")))["employee"]["slug"]
    helper = ok(dev.post("/api/employees", json=job("Assistente", "Assistente de operações")))["employee"]["slug"]
    wf.created["agents"].extend([slug, helper])
    for s in (slug, helper):
        wf.activate(dev, mgr, s)
    check(b, "contratar, experiência com o LLM e admissão (2 funcionários)", True, t0=t0)

    t0 = time.time()
    img = png("NOTA FISCAL 88123\nFORNECEDOR: BETA SA\nVALOR: R$ 3.490,00")
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Ler a nota fiscal anexada", "body": (
        "Leia a imagem anexada e diga o número da nota, o fornecedor e o valor."),
        "files": [{"name": "nota.png", "content_base64": img, "content_type": "image/png"}]}))["id"]
    t = wf.until(dev, tid, ("done", "failed"), 600)
    res = (t or {}).get("result") or ""
    check(b, "imagem anexada à tarefa (o funcionário lê)", t and t["status"] == "done" and "88123" in res and "BETA" in res.upper(),
          res[:300] or (t or {}).get("error"), t0)

    # quadro de issues com o modelo real
    t0 = time.time()
    i = ok(dev.post("/api/issues", json={"title": f"Resumo de riscos {RUN}", "team": team, "assignee": f"employee:{slug}",
                                         "description": "Liste em 3 tópicos os principais riscos de um atraso de entrega de 10 dias "
                                                        "para um cliente B2B. Seja objetivo."}))
    d = wf.wait(lambda: (lambda x: x if x["status"] in ("in_review", "todo") else None)(ok(dev.get(f"/api/issues/{i['key']}"))), 600)
    agent_c = [x for x in (d or {}).get("timeline", []) if x["type"] == "comment" and x["author"]["kind"] == "employee"]
    check(b, "issue atribuída: a rodada com o LLM termina em revisão com o resultado", d and d["status"] == "in_review" and agent_c,
          (d or {}).get("status"), t0)
    t0 = time.time()
    i2 = ok(dev.post("/api/issues", json={"title": f"Preparar comunicado {RUN}", "team": team, "assignee": f"employee:{slug}",
                                          "description": (
                                              "Divida este trabalho: crie uma sub-issue para o funcionário "
                                              f"'{helper}' redigir um e-mail curto ao cliente sobre o atraso (use create_sub_issue com "
                                              f"assignee {helper}) e depois comente na issue o que fez.")}))
    d2 = wf.wait(lambda: (lambda x: x if x["sub"]["total"] >= 1 and x["status"] != "in_progress" else None)(
        ok(dev.get(f"/api/issues/{i2['key']}"))), 600)
    subs = (d2 or {}).get("sub_issues", [])
    check(b, "o funcionário divide a issue e delega a sub-issue ao colega (alçada)", subs and subs[0]["assignee"]
          and subs[0]["assignee"].get("slug") == helper, json.dumps(subs[:1], ensure_ascii=False)[:300], t0)

    # governança sob o modelo real
    t0 = time.time()
    ok(mgr.patch(f"/api/employees/{slug}", json={"shadow": True}))
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Avisar o cliente", "body": (
        "Envie um e-mail (send_email) para cliente@exemplo.com dizendo que a entrega atrasa 10 dias.")}))["id"]
    t = wf.until(dev, tid, ("done", "failed"), 600)
    sim = [e for e in (t or {}).get("events", []) if e["kind"] == "shadow"]  # a ação simulada (nada saiu)
    check(b, "modo sombra: o envio é só simulado", t and sim, [e["kind"] for e in (t or {}).get("events", [])][-12:], t0)
    ok(mgr.patch(f"/api/employees/{slug}", json={"shadow": False}))

    t0 = time.time()
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Conferir cadastro", "body": (
        "O CPF do cliente é 529.982.247-25. Repita o CPF na resposta e diga se o formato está correto.")}))["id"]
    t = wf.until(dev, tid, ("done", "failed"), 600)
    gov = ok(admin.get("/api/governance")) if admin.get("/api/governance").status_code == 200 else {}
    check(b, "DLP: a pessoa vê o CPF real no resultado", t and "529.982.247-25" in (t.get("result") or ""),
          (t or {}).get("result", "")[:200], t0)
    check(b, "DLP ligado na empresa (mascarar antes do modelo)", bool(gov), json.dumps(gov, ensure_ascii=False)[:200])

    t0 = time.time()
    pol = ok(mgr.post("/api/budgets", json={"scope_type": "agent", "scope": slug, "amount_usd": 0.000001, "window": "month",
                                           "hard_stop": True}))
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Mais uma", "body": "Responda: 1+1?"}))["id"]
    t = wf.until(dev, tid, ("done", "failed", "waiting_budget"), 300)
    check(b, "orçamento estourado para o funcionário (parada)", t and t["status"] == "waiting_budget", (t or {}).get("status"), t0)
    admin.delete(f"/api/budgets/{pol['id']}")


def navegador():
    """Navegador governado com o modelo real: só os domínios do cargo; a página lida vira resposta."""
    b = "navegador governado"
    team = ok(admin.post("/api/teams", json={"name": f"Bateria nav {RUN}"}))["slug"]
    mgr, mgr_email, _ = wf.person("navm", "maintainer", team)
    dev, _, _ = wf.person("navd", "developer", team)
    slug = ok(dev.post("/api/employees", json={
        "name": f"Pesquisador {RUN}", "title": "Pesquisador web", "mission": "Ler páginas da web liberadas e resumir.",
        "responsibilities": ["abrir a página", "ler o conteúdo", "resumir"], "manager": mgr_email, "team": team,
        "systems": ["navegador"], "llm": {"connection": CONN, "max_steps": 15}, "channels": ["portal"],
        "accept_default_authority": True,
        "probation_tasks": [{"title": "Quanto é 2 + 2?", "body": "Responda só o número.", "expected": "4"},
                            {"title": "Capital do Brasil", "body": "Responda só o nome da cidade.", "expected": "Brasília"},
                            {"title": "Dias úteis", "body": "Quantos dias úteis tem uma semana comum? Só o número.", "expected": "5"}]}))["employee"]["slug"]
    wf.created["agents"].append(slug)
    wf.activate(dev, mgr, slug)
    ok(mgr.put(f"/api/employees/{slug}/browser-domains", json={"domains": ["example.com"]}))
    t0 = time.time()
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Ler a página", "body": (
        "Abra https://example.com no navegador e diga o título da página e do que ela trata, em uma frase.")}))["id"]
    t = wf.until(dev, tid, ("done", "failed"), 600)
    used = [e["payload"].get("tool") for e in (t or {}).get("events", []) if e["kind"] in ("tool", "gate")
            and isinstance(e.get("payload"), dict)]
    check(b, "abre a página liberada e responde com o que leu", t and t["status"] == "done" and "browser_open" in used
          and "Example Domain" in (t.get("result") or ""), ((t or {}).get("result") or (t or {}).get("error") or "")[:300], t0)
    t0 = time.time()
    tid = ok(dev.post(f"/api/employees/{slug}/tasks", json={"title": "Outra página", "body": (
        "Abra https://wikipedia.org no navegador e diga o título da página.")}))["id"]
    # o navegador recusa; o funcionário não improvisa: pede ao gestor para liberar o domínio (e espera a decisão)
    t = wf.until(dev, tid, ("done", "failed", "waiting_human"), 600)
    evs = (t or {}).get("events", [])
    blocked = any("não está entre os domínios liberados" in json.dumps(e.get("payload"), ensure_ascii=False) for e in evs)
    asked = any(e["kind"] == "human_request" and (e.get("payload") or {}).get("kind") == "capability" for e in evs)
    check(b, "domínio fora do cargo: o navegador recusa e o funcionário pede a liberação", t and blocked and asked,
          [e["kind"] for e in evs][-10:], t0)
    admin.delete(f"/api/teams/{team}")


def memoria():
    """Memória de longo prazo com o modelo real: o fato dito numa conversa volta em outra."""
    b, s = "memória do agente", f"bat-{RUN}-memoria"
    AGENTS.append(s)
    ok(admin.post("/api/apply", json={"agents": [{"name": f"Memória {RUN}", "slug": s, "objective": "atender clientes lembrando preferências",
                                                  "final_output": "resposta", "spec": {
                                                      "instructions": "Quando o usuário contar uma preferência de cliente, guarde com "
                                                                      "memory__remember. Antes de responder sobre um cliente, consulte memory__recall.",
                                                      "llm": {"connection": CONN}, "memory": {"scope": "agent", "write": True}}}]}))
    st = admin.post(f"/api/agents/{s}/ship", timeout=600)
    check(b, "publicar agente com memória", st.status_code == 200, st.text[:300])
    t0 = time.time()
    code, txt = chat(s, f"Guarde isto: o cliente Zeta{RUN} prefere pagar por boleto, nunca por cartão.", f"mem1-{RUN}")
    check(b, "guardar o fato (memory__remember)", code == 200, txt, t0)
    t0 = time.time()
    found = ""
    for _ in range(12):  # a extração do fato é assíncrona (o worker de memória usa o LLM)
        time.sleep(10)
        code, found = chat(s, f"Como o cliente Zeta{RUN} prefere pagar?", f"mem2-{RUN}-{_}")
        if code == 200 and "boleto" in found.lower():
            break
    check(b, "lembrar em outra conversa (memory__recall)", "boleto" in found.lower(), found, t0)


BLOCKS = {"simples": simples, "multi": multi, "harness": harness, "visao": visao, "funcionario": funcionario,
          "navegador": navegador, "memoria": memoria}


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    for name, fn in BLOCKS.items():
        if only and name not in only:
            continue
        try:
            fn()
        except SystemExit as e:
            check(name, "bloco terminou sem erro", False, str(e))
        except Exception as e:  # noqa: BLE001
            check(name, "bloco terminou sem erro", False, f"{type(e).__name__}: {e}")
    good = sum(r["ok"] for r in REPORT)
    print(f"\n{good}/{len(REPORT)} verificações ok com {CONN} em {round(time.time() - T0)}s", flush=True)
    if os.environ.get("BATTERY_REPORT"):
        with open(os.environ["BATTERY_REPORT"], "w", encoding="utf-8") as f:
            json.dump(REPORT, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" not in sys.argv:
            for s in AGENTS:
                admin.delete(f"/api/agents/{s}")
            wf.cleanup()
