"""E2E da força de trabalho digital e dos plugins, contra um Agent Hangar rodando de verdade (modo mock, sem LLM).

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> ECHO_URL=http://e2e-echo:8080 python tests/e2e/workforce.py

Precisa do sistema de mentira (tests/e2e/echo_server.py) rodando como `e2e-echo` nas redes hangar_agents e
hangar_plugins, e da central com PLUGIN_ALLOW_PRIVATE=1 e PLUGIN_ALLOW_UNPINNED=1 (o echo está numa rede privada e o
plugin com código usa a imagem local do runtime). Cria um time e duas pessoas (gestora e desenvolvedor), e exercita:
  - plugin HTTP: quatro olhos, testes em stage, instalação do time, credencial injetada e mascarada;
  - plugin com código: um servidor FastMCP num container isolado, ferramenta não declarada escondida;
  - Creator mode pelo MCP: rascunho -> stage -> testes -> envio -> revisão de outra pessoa;
  - Digital employee: contratação, experiência, admissão, promoção (quatro olhos), aprovação de ação, plano aprovado
    antes da execução, repasse entre funcionários com espera, plugin pela alçada e um gatilho assinado (HMAC).
Apaga tudo no final (também quando falha). Sai com código != 0 na primeira falha.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx
import yaml

URL = os.environ.get("HANGAR_URL", "http://localhost:8090").rstrip("/")
ECHO = os.environ.get("ECHO_URL", "http://e2e-echo:8080").rstrip("/")
RUN = uuid.uuid4().hex[:6]
admin = httpx.Client(base_url=URL, timeout=300, headers={"Authorization": f"Bearer {os.environ['HANGAR_TOKEN']}"})
passed = 0
created = {"agents": [], "plugins": [], "users": [], "team": None, "llm": []}
USERS: dict[str, str] = {}  # login de teste de cada pessoa criada ("usuario senha"), para o --keep do work.py

SERVER = r'''
import os
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
m = FastMCP("ledger", host="0.0.0.0", port=9000, stateless_http=True, json_response=True, streamable_http_path="/mcp",
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

@m.tool()
def balance(account: str) -> str:
    """Saldo de uma conta."""
    return f"account {account}: 1500.00 (key {os.environ['LEDGER_KEY']})"

@m.tool()
def wipe_ledger() -> str:
    """Não declarada no manifesto."""
    return "WIPED"

m.run(transport="streamable-http")
'''


def check(name, cond, detail=""):
    global passed
    if not cond:
        print(f"FAIL {name} {str(detail)[:400]}", flush=True)
        raise SystemExit(1)
    passed += 1
    print(f"ok   {name}", flush=True)


def ok(r: httpx.Response):
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code} {r.request.method} {r.request.url}: {r.text[:400]}")
    return r.json()


def wait(fn, secs=240, every=2):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


# ------------------------------------------------------------------ pessoas
def person(key: str, role: str, team: str) -> tuple[httpx.Client, str, str]:
    email, pw = f"e2e-{RUN}-{key}@example.com", f"pw-{uuid.uuid4().hex}"
    u = ok(admin.post("/api/users", json={"email": email, "name": f"E2E {key}", "username": f"e2e-{RUN}-{key}",
                                          "password": pw}))
    created["users"].append(u["id"])
    ok(admin.post(f"/api/teams/{team}/members", json={"email": email, "role": role}))
    USERS[key] = f"e2e-{RUN}-{key} {pw}"
    c = httpx.Client(base_url=URL, timeout=300)
    ok(c.post("/api/auth/password", json={"username": f"e2e-{RUN}-{key}", "password": pw}))
    c.headers["X-CSRF-Token"] = c.cookies.get("hangar_csrf") or ""  # sessão por cookie: double-submit, como o portal
    pat = ok(c.post("/api/keys", json={"name": f"e2e-{RUN}", "scopes": ["user"]}))["key"]
    return c, email, pat


def task(c, tid):
    return ok(c.get(f"/api/tasks/{tid}"))


def until(c, tid, statuses, secs=240):
    return wait(lambda: (lambda d: d if d["status"] in statuses else None)(task(c, tid)), secs)


def approve_plugin(dev, name):
    ok(dev.post(f"/api/plugins/{name}/submit"))
    d = ok(admin.post(f"/api/plugins/{name}/review", json={"decision": "approve"}))
    check(f"{name}: aprovado por outra pessoa", d["status"] == "approved")


def hire(dev, mgr_email, team, name, plugins=(), tools=()):
    job = {"name": f"{name} {RUN}", "title": name, "mission": "Keep the work moving.",
           "responsibilities": ["handle requests", "update records", "flag risks"], "manager": mgr_email, "team": team,
           "systems": ["ERP"], "llm": {"model": "mock/echo"}, "channels": ["portal", "mcp"], "accept_default_authority": True,
           "tools": [{"type": "http", "name": "send_email", "url": f"{ECHO}/send", "method": "POST"}, *tools],
           "probation_tasks": [{"title": f"Probation {i}", "body": f"answer {i}", "expected": f"answer {i}"} for i in range(3)]}
    d = ok(dev.post("/api/employees", json=job))
    slug = d["employee"]["slug"]
    created["agents"].append(slug)
    if plugins:
        ok(dev.patch(f"/api/agents/{slug}", json={"plugins": list(plugins)}))
    return slug


def activate(dev, mgr, slug):
    ok(dev.post(f"/api/employees/{slug}/probation"))
    done = wait(lambda: (lambda e: e if e["probation"] and all(t["status"] == "done" for t in e["probation"]) else None)(
        ok(dev.get(f"/api/employees/{slug}"))), 300)
    check(f"{slug}: experiência concluída", done is not None)
    adm = next(d for d in ok(mgr.get("/api/decisions")) if d["kind"] == "admission" and d["employee"] == slug)
    ok(mgr.post(f"/api/decisions/{adm['id']}", json={"decision": "approve"}))
    promo = next((x for x in ok(admin.get("/api/approvals"))["to_decide"] if x["kind"] == "promotion" and x["agent"] == slug), None)
    if promo:  # o time exige quatro olhos para produção
        ok(admin.post(f"/api/promotions/{promo['id']}/approve", json={}))
    check(f"{slug}: ativo", ok(dev.get(f"/api/employees/{slug}"))["status"] == "active")


# ------------------------------------------------------------------ cenários
def scenario_http_plugin(dev, mgr, team) -> str:
    name = f"e2e-erp-{RUN}"
    manifest = {"name": name, "title": "E2E ERP", "version": "1.0.0", "category": "finance", "base_url": ECHO,
                "auth": {"type": "api_key", "name": "X-ERP-Key"},
                "tools": [{"name": "get_record", "method": "GET", "path": "/records/{id}", "action": "read"},
                          {"name": "send_record", "method": "POST", "path": "/records/send", "action": "send_external"}],
                "triggers": [{"name": "record_overdue", "title": "Record overdue", "task_title": "Follow up {{event.id}}",
                              "dedupe": "id", "signature": {"header": "X-Sig", "prefix": "sha256=", "secret": "hook_secret"}}],
                "settings": [{"key": "hook_secret", "title": "Hook secret", "secret": True}],
                "tests": [{"name": "lê um registro", "tool": "get_record", "args": {"id": "7"}, "expect_contains": "/records/7"},
                          {"name": "outro registro", "tool": "get_record", "args": {"id": "x"}, "expect_status": 200}]}
    ok(dev.post("/api/plugins", json={"manifest": manifest, "team": team}))
    created["plugins"].append(name)
    check("plugin: sem os testes em stage, não envia", dev.post(f"/api/plugins/{name}/submit").status_code == 400)
    ok(dev.put(f"/api/plugins/{name}/install", json={"team": team, "stage": True, "credential": f"stage-{RUN}"}))
    rep = ok(dev.post(f"/api/plugins/{name}/tests", json={"team": team}))
    check("plugin: testes em stage passam", rep["passed"], rep)
    check("plugin: quem criou não aprova", dev.post(f"/api/plugins/{name}/review", json={"decision": "approve"}).status_code == 403)
    approve_plugin(dev, name)
    r = ok(mgr.put(f"/api/plugins/{name}/install", json={"team": team, "credential": f"prod-{RUN}",
                                                         "secrets": {"hook_secret": f"whsec-{RUN}"}}))
    check("plugin: instalado no time, segredos não voltam", r["credential"] and f"prod-{RUN}" not in json.dumps(r))
    t = ok(mgr.post(f"/api/plugins/{name}/install/test", json={"team": team}))
    check("plugin: testar conexão mascara a credencial ecoada", t["ok"] and "[segredo]" in t["sample"] and f"prod-{RUN}" not in t["sample"], t)
    return name


def scenario_server_plugin(dev, mgr, team) -> str:
    name = f"e2e-ledger-{RUN}"
    manifest = {"name": name, "title": "E2E Ledger", "version": "1.0.0", "runtime": "server",
                "auth": {"type": "api_key"}, "server": {"image": "agent-hangar/agent-runtime:latest",
                                                        "command": ["python", "-c", SERVER], "port": 9000,
                                                        "credential_env": "LEDGER_KEY"},
                "tools": [{"name": "balance", "description": "Account balance", "action": "read"}],
                "tests": [{"tool": "balance", "args": {"account": "42"}, "expect_contains": "1500.00"}]}
    ok(dev.post("/api/plugins", json={"manifest": manifest, "team": team}))
    created["plugins"].append(name)
    ok(dev.put(f"/api/plugins/{name}/install", json={"team": team, "stage": True, "credential": f"lstage-{RUN}"}))
    rep = ok(dev.post(f"/api/plugins/{name}/tests", json={"team": team}))
    check("plugin com código: testes em stage (container de stage)", rep["passed"], rep)
    approve_plugin(dev, name)
    r = ok(mgr.put(f"/api/plugins/{name}/install", json={"team": team, "credential": f"lprod-{RUN}"}))
    check("plugin com código: o container do time sobe", r["server"]["state"] == "running", r.get("server"))
    t = ok(mgr.post(f"/api/plugins/{name}/install/test", json={"team": team}))
    check("plugin com código: só as declaradas; a credencial volta mascarada",
          t["ok"] and "[segredo]" in t["sample"] and f"lprod-{RUN}" not in t["sample"], t)
    return name


async def scenario_creator_mode(dev_pat, team):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    name = f"e2e-crm-{RUN}"
    doc = {"openapi": "3.0.3", "info": {"title": "CRM", "version": "1.0.0"}, "servers": [{"url": ECHO}],
           "paths": {"/customers": {"get": {"operationId": "findCustomer",
                                            "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}]}}}}

    async def call(s, tool, args):
        res = await s.call_tool(tool, args)
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        try:
            return res.isError, json.loads(text)
        except ValueError:
            return res.isError, text
    async with streamablehttp_client(URL + "/mcp", headers={"Authorization": f"Bearer {dev_pat}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            err, out = await call(s, "plugin_from_openapi", {"document": json.dumps(doc), "name": name})
            check("creator: OpenAPI -> manifesto", not err and out["tools"] == 1, out)
            m = yaml.safe_load(out["manifest_yaml"])
            m["tests"] = [{"tool": "find_customer", "args": {"q": "ACME"}, "expect_contains": "ACME"}]
            err, d = await call(s, "plugin_draft", {"manifest": yaml.safe_dump(m), "team": team})
            check("creator: rascunho e o link do portal para a credencial de teste", not err and "STAGE" in d["next"], d)
            created["plugins"].append(name)
            err, out = await call(s, "plugin_submit", {"name": name})
            check("creator: sem testes em stage, não envia", err)
    return name


def scenario_employees(dev, mgr, mgr_email, team, erp, ledger):
    a = hire(dev, mgr_email, team, "Collections Desk", plugins=(erp, ledger))
    b = hire(dev, mgr_email, team, "Billing Desk",
             tools=[{"type": "http", "name": "update_record", "url": f"{ECHO}/records", "method": "PATCH"}])
    for s in (a, b):
        activate(dev, mgr, s)
    ok(dev.patch(f"/api/employees/{a}", json={"colleagues": [b]}))
    # 1) ação que pede aprovação
    tid = ok(mgr.post(f"/api/employees/{a}/tasks", json={"title": "Notify", "body": 'use send_email {"to": "c@x.com"}'}))["id"]
    t = until(mgr, tid, ("waiting_human",))
    req = next(q for q in t["requests"] if q["status"] == "open")
    ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
    check("funcionário: ação aprovada e executada", until(mgr, tid, ("done", "failed"))["status"] == "done")
    # 2) plano aprovado antes da execução
    tid = ok(mgr.post(f"/api/employees/{a}/tasks", json={"title": "Big", "plan_approval": True, "body": "plan: Gather; Compute; Send"}))["id"]
    t = until(mgr, tid, ("waiting_human",))
    req = next(q for q in t["requests"] if q["kind"] == "plan")
    ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
    t = until(mgr, tid, ("done", "failed"))
    check("funcionário: plano aprovado, 100%", t["status"] == "done" and t["progress"]["pct"] == 100, t.get("progress"))
    # 3) repasse com espera
    tid = ok(mgr.post(f"/api/employees/{a}/tasks", json={"title": "Renew", "body": f"handoff: {b} | Collect the invoice | wait"}))["id"]
    t = until(mgr, tid, ("done", "failed"), 300)
    check("funcionário: repasse ao colega e retomada com o resultado", t["status"] == "done" and len(t["children"]) == 1
          and "recebi o resultado do colega" in t["result"], t.get("result"))
    # 4) plugin HTTP pela alçada: ler sozinho, enviar pede aprovação
    tid = ok(mgr.post(f"/api/employees/{a}/tasks", json={"title": "Look up", "body": f'use plugin-{erp}__get_record {{"id": "9"}}'}))["id"]
    t = until(mgr, tid, ("done", "failed", "waiting_human"))
    check("plugin no funcionário: leitura direta, credencial mascarada",
          t["status"] == "done" and "/records/9" in t["result"] and "[segredo]" in t["result"] and f"prod-{RUN}" not in t["result"], t.get("result"))
    tid = ok(mgr.post(f"/api/employees/{a}/tasks", json={"title": "Send", "body": f'use plugin-{erp}__send_record {{"id": "9"}}'}))["id"]
    t = until(mgr, tid, ("waiting_human", "done", "failed"))
    req = next((q for q in t["requests"] if q["status"] == "open"), None)
    check("plugin no funcionário: enviar para fora pede aprovação", req and req["action_type"] == "send_external", t.get("status"))
    ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
    check("plugin no funcionário: aprovado e enviado", until(mgr, tid, ("done", "failed"))["status"] == "done")
    # 5) plugin com código
    tid = ok(mgr.post(f"/api/employees/{a}/tasks", json={"title": "Balance", "body": f'use plugin-{ledger}__balance {{"account": "42"}}'}))["id"]
    t = until(mgr, tid, ("done", "failed", "waiting_human"))
    check("plugin com código no funcionário", t["status"] == "done" and "1500.00" in t["result"] and f"lprod-{RUN}" not in t["result"], t.get("result"))
    # 6) gatilho assinado
    inst = ok(mgr.put(f"/api/plugins/{erp}/install", json={"team": team, "triggers": {"record_overdue": {"employee": a}}}))
    hook = next(x for x in inst["triggers"] if x["name"] == "record_overdue")["url"].split("/hooks/", 1)[1]
    raw = json.dumps({"id": f"r-{RUN}"}).encode()
    sig = "sha256=" + hmac.new(f"whsec-{RUN}".encode(), raw, hashlib.sha256).hexdigest()
    check("gatilho: assinatura errada recusada", httpx.post(f"{URL}/hooks/{hook}", content=raw, headers={"X-Sig": "sha256=0"}).status_code == 401)
    r = httpx.post(f"{URL}/hooks/{hook}", content=raw, headers={"X-Sig": sig}).json()
    check("gatilho: evento vira tarefa do funcionário", r["duplicate"] is False and until(mgr, r["id"], ("done", "failed"))["title"] == f"Follow up r-{RUN}")
    check("gatilho: o mesmo evento não duplica", httpx.post(f"{URL}/hooks/{hook}", content=raw, headers={"X-Sig": sig}).json()["duplicate"])
    # 7) laço: "sem achados" fecha em silêncio; achado traz o resumo (finish_task, pelo runtime e pelo gate de verdade)
    quiet = ok(mgr.post(f"/api/employees/{b}/tasks", json={"title": "Check", "body": 'use finish_task {"findings": false}'}))["id"]
    t = until(mgr, quiet, ("done", "failed"))
    check("laço sem achados: conclui em silêncio", t["status"] == "done" and t["finding"] is False and t["result"] in ("Sem achados.", "No findings."), t)
    box = [i["task_id"] for i in ok(mgr.get("/api/inbox"))["items"] if i["kind"] == "task"]
    check("laço sem achados: fora da caixa de entrada de quem pediu", quiet not in box, box[:5])
    found = ok(mgr.post(f"/api/employees/{b}/tasks", json={"title": "Check", "body":
        'use finish_task {"findings": true, "summary": "Fatura 88 cobrada em dobro; estornar a segunda."}'}))["id"]
    t = until(mgr, found, ("done", "failed"))
    check("laço com achado: o resumo chega à pessoa", t["finding"] is True and t["finding_summary"].startswith("Fatura 88"), t)
    rep = ok(dev.post(f"/api/employees/{b}/reports", params={"period": "daily"}))
    check("laço: o relatório conta execuções e achados à parte", rep["metrics"]["no_findings"] >= 1 and rep["metrics"]["findings"] >= 1
          and ("Laços:" in rep["summary"] or "Loops:" in rep["summary"]), rep["summary"])
    # 8) revisor de outro fornecedor: concordou, a ação roda; discordou, vai para uma pessoa com o motivo
    rev = f"e2e-rev-{RUN}"
    ok(admin.post("/api/catalog/llm", json={"name": rev, "base_url": f"{ECHO}/llm/v1", "model_name": "google/gemini-2.5-pro",
                                           "api_key": "e2e", "protocol": "openai"}))
    created["llm"].append(rev)
    same = mgr.put(f"/api/employees/{b}/authority", json={"rules": [{"action_type": "write_internal", "mode": "approve",
                                                                     "review": {"connection": rev}}]})
    check("revisor: só em regra auto ou notify", same.status_code == 400, same.text)
    ok(mgr.put(f"/api/employees/{b}/authority", json={"rules": [{"action_type": "write_internal", "mode": "auto",
                                                                 "review": {"connection": rev, "shadow": False}}]}))
    tid = ok(mgr.post(f"/api/employees/{b}/tasks", json={"title": "Update", "body": 'use update_record {"id": "9", "status": "paid"}'}))["id"]
    t = until(mgr, tid, ("done", "failed"))
    cr = next((q for q in t["requests"] if q["kind"] == "cross_review"), None)
    check("revisor concordou: a ação exata rodou, sem passar por uma pessoa", t["status"] == "done" and cr and cr["decision"] == "approve"
          and cr["decided_by"] == "revisor google/gemini-2.5-pro" and not [q for q in t["requests"] if q["kind"] == "approval"], t.get("requests"))
    tid = ok(mgr.post(f"/api/employees/{b}/tasks", json={"title": "Update", "body": 'use update_record {"id": "9", "note": "disagree-me"}'}))["id"]
    t = until(mgr, tid, ("waiting_human", "done", "failed"))
    req = wait(lambda: next((q for q in task(mgr, tid)["requests"] if q["kind"] == "approval" and q["status"] == "open"), None), 60)
    check("revisor discordou: vai para a pessoa, com o motivo", req and req["review"]["verdict"] == "disagree"
          and "disagree" in req["review"]["reason"], req or t)
    ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
    check("revisor discordou: a pessoa aprovou e a ação rodou", until(mgr, tid, ("done", "failed"))["status"] == "done")
    rep = ok(dev.post(f"/api/employees/{b}/reports", params={"period": "daily"}))
    check("revisor: revisões, concordância e custo no relatório", rep["metrics"]["reviews"] == 2 and rep["metrics"]["review_agreed"] == 1
          and rep["metrics"]["review_pairs"] == 1 and ("Revisor:" in rep["summary"] or "Reviewer:" in rep["summary"]), rep["summary"])


def cleanup():
    for s in created["agents"]:
        admin.delete(f"/api/agents/{s}")
    for p in created["plugins"]:
        admin.delete(f"/api/plugins/{p}")
    for u in created["users"]:
        admin.patch(f"/api/users/{u}", json={"active": False})
    if created["team"]:
        admin.delete(f"/api/teams/{created['team']}")
    for name in created["llm"]:
        admin.delete(f"/api/catalog/llm/{name}")


def main():
    check("health", ok(admin.get("/api/health"))["status"] == "ok")
    team = ok(admin.post("/api/teams", json={"name": f"E2E {RUN}"}))["slug"]
    created["team"] = team
    mgr, mgr_email, _ = person("mgr", "maintainer", team)
    dev, _, dev_pat = person("dev", "developer", team)
    erp = scenario_http_plugin(dev, mgr, team)
    ledger = scenario_server_plugin(dev, mgr, team)
    asyncio.run(scenario_creator_mode(dev_pat, team))
    scenario_employees(dev, mgr, mgr_email, team, erp, ledger)
    print(f"\n{passed} checks ok", flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
        sys.stdout.flush()
