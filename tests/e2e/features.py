"""E2E das features das últimas versões, contra um Agent Hangar rodando de verdade (modo mock + LLM de mentira).

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> ECHO_URL=http://e2e-echo:8080 python tests/e2e/features.py

Precisa do sistema de mentira (tests/e2e/echo_server.py) como `e2e-echo` na rede hangar_agents: ele também é um
provedor de LLM no formato da OpenRouter (/llm/v1), que guarda o que recebeu. Exercita:
  - cadastro da empresa e e-mail corporativo (vale no cadastro de membro);
  - provedor de LLM: a lista de modelos vem do provedor, com recursos e preço; recomendação por papel;
  - perfil de execução: o esforço pelo papel chega ao provedor (central → container do agente → LLM);
  - medição: o uso marcado com o perfil e a comparação de perfis em stage (outro esforço só naquela chamada);
  - agente com harness: o job num container novo devolve o diff;
  - conectores: catálogo, servidor MCP remoto (o da própria plataforma) testado e criado, ferramentas para aprovar;
    servidor local (npx) testado num container temporário;
  - Skills: upload com verificação de segurança, detalhe e download.
Apaga o que criou no final (também quando falha). Sai com código != 0 na primeira falha.
"""
import base64
import io
import json
import os
import sys
import time
import uuid
import zipfile

try:  # o SDK oficial (pip install ag-ui-protocol) valida os eventos quando está instalado
    from ag_ui.core import Event as _AguiEvent
    from ag_ui.core import RunAgentInput as _AguiInput
    from pydantic import TypeAdapter as _TypeAdapter
    AGUI_SDK = (_TypeAdapter(_AguiEvent), _AguiInput)
except ImportError:
    AGUI_SDK = None

import httpx

URL = os.environ.get("HANGAR_URL", "http://localhost:8090").rstrip("/")
ECHO = os.environ.get("ECHO_URL", "http://e2e-echo:8080").rstrip("/")
STDIO = os.environ.get("E2E_STDIO", "1") == "1"  # servidor local (npx): precisa da imagem mcp-stdio-runner e de internet
RUN = uuid.uuid4().hex[:6]
admin = httpx.Client(base_url=URL, timeout=300, headers={"Authorization": f"Bearer {os.environ['HANGAR_TOKEN']}"})
passed = 0
made = {"agents": [], "connections": [], "connectors": [], "skills": [], "users": [], "keys": [], "secrets": [],
        "company": None}


def check(name, cond, detail=""):
    global passed
    if not cond:
        print(f"FAIL {name} {str(detail)[:500]}", flush=True)
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


# ------------------------------------------------------------------ empresa
def phase_company():
    before = ok(admin.get("/api/workspace/company"))
    made["company"] = before
    dom = f"e2e{RUN}.test"
    c = ok(admin.put("/api/workspace/company", json={"domains": [dom], "enforce": True}))
    check("empresa: domínio corporativo e regra ligada", c["enforce"] and c["domains"] == [dom], c)
    r = admin.post("/api/users", json={"email": f"x-{RUN}@gmail.com"})
    check("empresa: e-mail fora do domínio é recusado", r.status_code == 400 and f"@{dom}" in r.text, r.text)
    u = ok(admin.post("/api/users", json={"email": f"pessoa-{RUN}@{dom}"}))
    made["users"].append(u["id"])
    check("empresa: e-mail do domínio entra", u["email"].endswith(dom))
    check("empresa: domínio de e-mail pessoal não vira domínio da empresa",
          admin.put("/api/workspace/company", json={"domains": ["gmail.com"]}).status_code == 400)
    restore_company()


def restore_company():
    b = made.get("company")
    if b is not None:
        admin.put("/api/workspace/company", json={"domains": b["domains"], "enforce": b["enforce"], "subdomains": b["subdomains"]})


# ------------------------------------------------------------------ provedor de LLM, modelos e recomendação
def phase_llm() -> str:
    conn = f"e2e-llm-{RUN}"
    ok(admin.post("/api/catalog/llm", json={"name": conn, "provider": "openrouter", "base_url": f"{ECHO}/llm/v1",
                                            "api_key": "e2e", "model_name": ""}))
    made["connections"].append(conn)
    models = ok(admin.get("/api/catalog/models", params={"connection": conn}))
    by = {m["id"]: m for m in models}
    check("modelos: a lista vem do provedor", {"e2e/thinker", "e2e/plain"} <= set(by), list(by))
    t = by["e2e/thinker"]
    check("modelos: recursos e preço de cada um", {"tools", "reasoning"} <= set(t["caps"]) and t["price_in_per_mtok"] == 1.0, t)
    rec = ok(admin.get("/api/catalog/models/recommend", params={"role": "coder", "caps": "tools", "connection": conn}))
    check("modelos: recomendação por papel", rec["candidates"] and rec["candidates"][0]["connection"] == conn, rec)
    return conn


# ------------------------------------------------------------------ perfil de execução, medição e comparação
def phase_execution(conn: str):
    name = f"Revisor de código {RUN}"
    a = ok(admin.post("/api/agents", json={"name": name, "objective": "revisa código e aponta bugs", "final_output": "parecer"}))
    slug = a["slug"]
    made["agents"].append(slug)
    ok(admin.patch(f"/api/agents/{slug}", json={"llm": {"connection": conn, "model": "e2e/thinker"},
                                                "tests": [{"name": "eco", "input": f"ping-{RUN}", "expect_contains": f"ping-{RUN}"}]}))
    prof = ok(admin.get(f"/api/agents/{slug}/execution", params={"env": "stage"}))
    p = prof["profile"]
    check("execução: esforço automático pelo papel (código = alto)", p["role"] == "coder" and p["effort"] == "high"
          and p["effort_param"] == "reasoning.effort", p)
    ok(admin.post(f"/api/agents/{slug}/deploy", json={"env": "stage"}))
    r = ok(admin.post(f"/api/agents/{slug}/chat", json={"message": f"ola-{RUN}", "env": "stage"}))
    check("execução: o agente responde pelo provedor", f"ola-{RUN}" in r["reply"], r)
    st = ok(admin.get(f"/api/agents/{slug}/execution/tuning"))
    row = next((x for x in st["stats"] if x["model"] == "e2e/thinker" and x["effort"] == "high"), None)
    check("medição: a chamada fica marcada com o modelo e o esforço", row is not None and row["calls"] >= 1, st["stats"])
    t = ok(admin.post(f"/api/agents/{slug}/execution/trials", json={}))
    done = wait(lambda: (lambda d: d if d["status"] != "running" else None)(ok(admin.get(f"/api/agents/{slug}/execution/trials/{t['id']}"))), 300)
    check("comparação de perfis: termina", done and done["status"] == "done", done)
    sent = {x["sent"] for x in done["results"]}
    check("comparação de perfis: cada nível manda um esforço diferente ao provedor", len(sent) == len(done["results"]) >= 2, done["results"])
    check("comparação de perfis: os casos de teste passam em cada nível", all(x["passed"] == x["total"] == 1 for x in done["results"]), done["results"])
    tuning = ok(admin.get(f"/api/agents/{slug}/execution/tuning"))
    check("medição: a comparação aparece para o agente", tuning["trial"] and tuning["trial"]["id"] == t["id"], tuning.get("trial"))
    ok(admin.patch(f"/api/agents/{slug}", json={"execution": {"effort": "low"}}))
    p2 = ok(admin.get(f"/api/agents/{slug}/execution", params={"env": "stage"}))["profile"]
    check("execução: o esforço definido no agente vale", p2["effort"] == "low" and p2["effort_source"] == "definido no agente", p2)
    return slug


def _agui(slug: str, messages: list, tools: list | None = None, state: dict | None = None) -> list[dict]:
    body = {"threadId": f"e2e-{RUN}", "runId": uuid.uuid4().hex[:8], "messages": messages, "tools": tools or [],
            "context": [{"description": "tela", "value": "pedidos"}], "state": state or {}, "forwardedProps": {}}
    r = admin.post(f"/gw-stage/{slug}/agui", json=body, headers={"Accept": "text/event-stream", "X-Channel": "agui"})
    check("AG-UI: responde em stream de eventos", r.status_code == 200 and r.headers.get("content-type", "").startswith(
        "text/event-stream"), (r.status_code, r.text[:200]))
    evs = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    if AGUI_SDK is not None:  # os eventos e o corpo validados pelo SDK oficial do protocolo
        for e in evs:
            AGUI_SDK[0].validate_python(e)
        AGUI_SDK[1].model_validate(body)
    return evs


def phase_agui(slug: str):
    """AG-UI no agente de chat, pelo gateway: texto, uma ferramenta do app (ida e volta) e a medição por canal."""
    evs = _agui(slug, [{"id": "1", "role": "user", "content": f"agui-{RUN}"}])
    kinds = [e["type"] for e in evs]
    text = "".join(e.get("delta", "") for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
    check("AG-UI: a rodada começa, responde e termina", kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED"
          and f"agui-{RUN}" in text, kinds)
    check("AG-UI: a resposta chega em stream, pedaço por pedaço", kinds.count("TEXT_MESSAGE_CONTENT") > 1, kinds)
    check("AG-UI: o raciocínio do modelo aparece à parte", "REASONING_MESSAGE_CONTENT" in kinds, kinds)
    evs = _agui(slug, [{"id": "1", "role": "user", "content": "use-tool app_state_update"}],
                state={"pedido": {"id": "4471", "status": "aberto"}})
    snap = next((e["snapshot"] for e in evs if e["type"] == "STATE_SNAPSHOT"), None)
    check("AG-UI: o agente muda o estado do app (JSON Patch) e o app recebe o estado novo",
          any(e["type"] == "STATE_DELTA" for e in evs) and snap == {"pedido": {"id": "4471", "status": "pago"}},
          [e["type"] for e in evs])
    tool = {"name": "abrir_pedido", "description": "abre um pedido na tela do app",
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}
    user = {"id": "1", "role": "user", "content": "use-tool abrir_pedido"}
    evs = _agui(slug, [user], [tool])
    start = next((e for e in evs if e["type"] == "TOOL_CALL_START"), None)
    args = "".join(e["delta"] for e in evs if e["type"] == "TOOL_CALL_ARGS")
    check("AG-UI: o agente pede a ferramenta do app", start and start["toolCallName"] == "abrir_pedido"
          and json.loads(args) == {"id": "4471"}, [e["type"] for e in evs])
    evs = _agui(slug, [user, {"id": "2", "role": "assistant", "toolCalls": [{"id": start["toolCallId"], "type": "function", "function": {
        "name": "abrir_pedido", "arguments": args}}]}, {"id": "3", "role": "tool", "toolCallId": start["toolCallId"],
                                                       "content": "pedido 4471 aberto"}], [tool])
    text = "".join(e.get("delta", "") for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
    check("AG-UI: o agente usa o resultado que o app devolveu", "pedido 4471 aberto" in text, text)
    rows = [u for u in ok(admin.get("/api/usage")) if u["agent"] == slug and u["protocol"] == "agui"]
    check("AG-UI: o uso fica medido por canal, com tokens", rows and rows[0]["channel"] == "agui" and rows[0]["tokens"] > 0, rows[:2])


def phase_remote():
    """Agente remoto (AG-UI): um agente de fora do hangar, governado por ele. A credencial sai do cofre; os testes em
    stage passam pela ponte; o agente fica disponível em OpenAI, A2A, MCP e AG-UI."""
    sec = f"e2e-remoto-{RUN}"
    s = ok(admin.put("/api/secrets", json={"name": sec, "value": f"tok-{RUN}"}))
    made["secrets"].append(s["id"])
    a = ok(admin.post("/api/agents", json={"name": f"Estoque remoto {RUN}", "objective": "responde sobre estoque",
                                           "final_output": "resposta"}))
    slug = a["slug"]
    made["agents"].append(slug)
    ok(admin.patch(f"/api/agents/{slug}", json={
        "remote": {"url": f"{ECHO}/agui-agent", "headers": {"Authorization": f"secret://{sec}"},
                   "prices": {"input_per_mtok": 2, "output_per_mtok": 10}},
        "tests": [{"name": "eco", "input": f"sku-{RUN}", "expect_contains": f"sku-{RUN}"}]}))
    bad = admin.patch(f"/api/agents/{slug}", json={"remote": {"headers": {"Authorization": "Bearer exposto"}}})
    check("agente remoto: credencial na spec é recusada (só pelo cofre)", bad.status_code == 400, bad.text[:200])
    p = ok(admin.post(f"/api/agents/{slug}/remote/test", json={"env": "stage"}))
    check("agente remoto: teste de conexão (endereço, cofre, rodada AG-UI)", p["ok"], p["steps"])
    t = ok(admin.post(f"/api/agents/{slug}/test"))
    check("agente remoto: testes em stage pela ponte", t["status"] == "passed" and "(remoto)" in t["summary"], t["summary"])
    used = next((x for x in ok(admin.get("/api/secrets")).get("secrets", []) if x["name"] == sec), {})
    check("agente remoto: a credencial sai do cofre (uso registrado)", used.get("uses", 0) >= 1, used)
    gw = f"/gw-stage/{slug}"
    r = ok(admin.post(f"{gw}/v1/chat/completions", json={"messages": [
        {"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}, {"role": "user", "content": f"via-openai-{RUN}"}]}))
    check("agente remoto: OpenAI-compatible pela ponte", r["choices"][0]["message"]["content"] == f"remoto: via-openai-{RUN}", r)
    a2a = ok(admin.post(f"{gw}/a2a", json={"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {"message": {
        "kind": "message", "role": "user", "messageId": "m", "parts": [{"kind": "text", "text": f"via-a2a-{RUN}"}]}}}))
    check("agente remoto: A2A pela ponte", a2a["result"]["parts"][0]["text"] == f"remoto: via-a2a-{RUN}", a2a)
    tools = ok(admin.post(f"{gw}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))["result"]["tools"]
    call = ok(admin.post(f"{gw}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": tools[0]["name"], "arguments": {"message": f"via-mcp-{RUN}"}}}))
    check("agente remoto: MCP pela ponte", call["result"]["content"][0]["text"] == f"remoto: via-mcp-{RUN}", call)
    evs = _agui(slug, [{"id": "1", "role": "user", "content": f"via-agui-{RUN}"}])
    kinds = [e["type"] for e in evs]
    check("agente remoto: AG-UI direto, evento por evento", kinds[0] == "RUN_STARTED" and "TOOL_CALL_RESULT" in kinds
          and kinds[-1] == "RUN_FINISHED", kinds)
    usage = [u for u in ok(admin.get("/api/usage")) if u["agent"] == slug]
    rows = {u["protocol"] for u in usage}
    check("agente remoto: cada protocolo entra na medição", {"openai", "a2a", "mcp", "agui"} <= rows, rows)
    check("agente remoto: os tokens que o agente informa entram na medição, com o custo pelo preço da spec",
          all(u["tokens"] == 150 and abs(u["cost_usd"] - (120 * 2 + 30 * 10) / 1e6) < 1e-9 for u in usage), usage[:3])
    # DLP da empresa na ponte: o agente remoto vê [CPF_…]; o app recebe o CPF de volta
    before = ok(admin.get("/api/governance"))
    made["governance"] = before
    cpf = "529.982.247-25"
    ok(admin.put("/api/governance", json={"dlp": {"mask": ["cpf"]}}))
    try:
        evs = _agui(slug, [{"id": "1", "role": "user", "content": f"cliente {cpf}"}])
    finally:
        restore_governance()
    seen = next((e["value"] for e in evs if e["type"] == "CUSTOM" and e.get("name") == "e2e.seen"), "")
    text = "".join(e.get("delta", "") for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
    check("agente remoto: DLP da empresa, o agente vê a marca e não o CPF", cpf not in seen and "(CPF_" in seen, seen)
    check("agente remoto: DLP da empresa, o app recebe o CPF real", text == f"remoto: cliente {cpf}", text)


def restore_governance():
    b = made.get("governance")
    if b is not None:
        admin.put("/api/governance", json={"dlp": b["dlp"]})


def phase_harness():
    a = ok(admin.post("/api/agents", json={"name": f"Dev harness {RUN}", "objective": "corrige código", "final_output": "diff"}))
    slug = a["slug"]
    made["agents"].append(slug)
    ok(admin.patch(f"/api/agents/{slug}", json={"harness": {"id": "claude-code"}}))
    job = ok(admin.post(f"/api/agents/{slug}/job", json={"task": f"tarefa {RUN}", "env": "stage", "timeout_s": 120, "wait": True}))
    check("harness: o job roda num container novo e devolve o diff", job["status"] == "passed" and "JOB_NOTES.md" in job["diff"], job.get("status"))
    prof = ok(admin.get(f"/api/agents/{slug}/execution", params={"env": "stage"}))["profile"]
    check("harness: o perfil sabe que é mock sem conexão", prof["target"] == "claude-code", prof)


# ------------------------------------------------------------------ conectores
def phase_connectors():
    cat = ok(admin.get("/api/connectors"))
    check("conectores: catálogo com seções e itens", len(cat.get("sections", [])) >= 5 and len(cat.get("items", [])) >= 20,
          (len(cat.get("sections", [])), len(cat.get("items", []))))
    key = ok(admin.post("/api/keys", json={"name": f"e2e-conector-{RUN}", "scopes": ["admin"]}))
    made["keys"].append(key["id"])
    cfg = {"url": "http://localhost:8080/mcp", "transport": "auto", "auth": "bearer", "token": key["key"]}
    t = ok(admin.post("/api/connectors/test", json=cfg))
    check("conectores: servidor MCP remoto testado (handshake e ferramentas)", t.get("ok") and len(t.get("tools", [])) > 10, {k: t.get(k) for k in ("ok", "error", "protocol_version")})
    name = f"e2e-mcp-{RUN}"
    tools = [x["name"] for x in t["tools"]][:3]
    c = ok(admin.post("/api/connectors", json={**cfg, "name": name, "scope": "org", "tools": tools, "accept_risk": True}))
    made["connectors"].append(name)
    d = ok(admin.get(f"/api/connectors/{name}"))
    approved = [x for x in d["tools"] if x.get("state") == "approved"]
    check("conectores: só as ferramentas escolhidas ficam aprovadas", len(approved) == len(tools) and len(d["tools"]) > len(tools),
          [(x["name"], x.get("state")) for x in d["tools"]][:6])
    check("conectores: o token vai para o cofre (o conector guarda só a referência)", key["key"] not in str(c) + str(d))
    if STDIO:
        t2 = ok(admin.post("/api/connectors/test", json={"url": "", "stdio": {
            "runner": "npx", "package": "@modelcontextprotocol/server-filesystem@2025.8.21", "args": ["/tmp"]}, "timeout_s": 240}))
        check("conectores: servidor local (npx) testado num container temporário", t2.get("ok") and t2.get("tools"), {k: t2.get(k) for k in ("ok", "error")})


# ------------------------------------------------------------------ skills
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def phase_skills():
    name = f"e2e-skill-{RUN}"
    md = f"---\nname: {name}\ndescription: Responder em tom formal\n---\n\nResponda sempre em tom formal e cite a fonte.\n"
    r = ok(admin.post("/api/skills/upload", json={"files": [{"name": f"{name}.md", "data": _b64(md.encode())}]}))
    res = r["results"][0]
    check("skills: upload com verificação de segurança", res.get("ok") or res.get("status") in ("created", "updated"), res)
    made["skills"].append(name)
    d = ok(admin.get(f"/api/skills/{name}"))
    check("skills: detalhe com o conteúdo", "tom formal" in (d.get("content") or ""), d.get("name"))
    z = admin.get(f"/api/skills/{name}/download")
    check("skills: download do pacote .zip com SKILL.md", z.status_code == 200 and "SKILL.md" in zipfile.ZipFile(io.BytesIO(z.content)).namelist()[0])
    bad = f"---\nname: {name}-vaza\ndescription: x\n---\n\nchave: sk-or-v1-{'a' * 64}\n"
    r = ok(admin.post("/api/skills/upload", json={"files": [{"name": "vaza.md", "data": _b64(bad.encode())}], "check_only": True}))
    check("skills: a verificação barra segredo no conteúdo", not r["results"][0].get("ok", False), r["results"][0])


# ------------------------------------------------------------------ limpeza
def cleanup():
    restore_company()
    restore_governance()
    for slug in made["agents"]:
        for env in ("stage", "prod"):
            admin.post(f"/api/agents/{slug}/stop", json={"env": env})
        admin.delete(f"/api/agents/{slug}")
    for n in made["connectors"]:
        admin.delete(f"/api/connectors/{n}")
    for n in made["connections"]:
        admin.delete(f"/api/catalog/llm/{n}")
    for n in made["skills"]:
        admin.post(f"/api/skills/{n}/status", json={"status": "deprecated"})
    for k in made["keys"]:
        admin.delete(f"/api/keys/{k}")
    for sid in made["secrets"]:
        admin.delete(f"/api/secrets/{sid}")
    for u in made["users"]:
        admin.patch(f"/api/users/{u}", json={"active": False})


def main():
    try:
        check("health", ok(admin.get("/api/health"))["status"] == "ok")
        phase_company()
        conn = phase_llm()
        phase_agui(phase_execution(conn))
        phase_remote()
        phase_harness()
        phase_connectors()
        phase_skills()
    finally:
        cleanup()
    print(f"\n{passed} verificações passaram", flush=True)


if __name__ == "__main__":
    sys.exit(main())
