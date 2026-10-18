"""E2E do app de exemplo com CopilotKit (integrations/copilotkit) num navegador de verdade, contra o Agent Hangar.

    python tests/e2e/copilotkit.py setup > /tmp/ck.env      # agente (LLM de mentira) em stage + chave invoke
    docker run -d --name e2e-copilotkit --network host --env-file /tmp/ck.env agent-hangar/copilotkit-example
    python tests/e2e/copilotkit.py browser /tmp/ck.env       # o navegador conversa pelo app
    python tests/e2e/copilotkit.py cleanup /tmp/ck.env

Precisa de HANGAR_URL, HANGAR_TOKEN (admin), ECHO_URL (tests/e2e/echo_server.py como e2e-echo) e, no passo browser,
APP_URL (padrão http://localhost:3000) e pip install playwright httpx && playwright install chromium. Verifica, pelo
navegador: a resposta do agente chega no chat; o agente pede uma ferramenta do app (open_order), o navegador roda e o
agente usa o resultado; o agente muda o estado do app (JSON Patch) e a tela mostra o estado novo; a chave fica no
servidor do app (não aparece em nenhuma requisição do navegador); o uso entra na medição pelo canal copilotkit.
"""
import json
import os
import sys
import uuid

import httpx

URL = os.environ.get("HANGAR_URL", "http://localhost:8090").rstrip("/")
ECHO = os.environ.get("ECHO_URL", "http://e2e-echo:8080").rstrip("/")
APP = os.environ.get("APP_URL", "http://localhost:3000").rstrip("/")
admin = httpx.Client(base_url=URL, timeout=300, headers={"Authorization": f"Bearer {os.environ['HANGAR_TOKEN']}"})
passed = 0


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


def read_env(path: str) -> dict:
    return dict(line.strip().split("=", 1) for line in open(path, encoding="utf-8") if "=" in line)


def setup():
    run = uuid.uuid4().hex[:6]
    conn = f"e2e-ck-{run}"
    ok(admin.post("/api/catalog/llm", json={"name": conn, "provider": "openrouter", "base_url": f"{ECHO}/llm/v1",
                                            "api_key": "e2e", "model_name": ""}))
    a = ok(admin.post("/api/agents", json={"name": f"Pedidos CopilotKit {run}", "objective": "ajuda com pedidos na tela",
                                           "final_output": "resposta"}))
    slug = a["slug"]
    # o site do app também pode chamar o agente direto do navegador (CORS por agente), para o passo browser conferir
    ok(admin.patch(f"/api/agents/{slug}", json={"llm": {"connection": conn, "model": "e2e/plain"}, "cors": {"origins": [APP]}}))
    ok(admin.post(f"/api/agents/{slug}/deploy", json={"env": "stage"}))
    key = ok(admin.post("/api/keys", json={"name": f"e2e-ck-{run}", "scopes": ["invoke"], "agents": [slug]}))
    # o app lê HANGAR_*; E2E_* é só para o passo cleanup
    print(f"HANGAR_URL={URL}\nHANGAR_GATEWAY=/gw-stage\nHANGAR_AGENT={slug}\nHANGAR_KEY={key['key']}\n"
          f"E2E_KEY_ID={key['id']}\nE2E_CONNECTION={conn}")


def browser(env: dict):
    from playwright.sync_api import sync_playwright

    sent = []  # tudo o que o navegador mandou: a chave não pode estar em nenhum lugar
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.on("request", lambda r: sent.append(f"{r.url} {r.headers} {r.post_data or ''}"))
        for _ in range(60):  # o app pode estar subindo
            try:
                page.goto(APP, wait_until="networkidle")
                break
            except Exception:
                page.wait_for_timeout(2000)
        status = page.get_by_test_id("status")
        check("app: a tela do pedido abre com o estado inicial", status.inner_text() == "open", status.inner_text())

        def say(text: str):
            box = page.locator("textarea").first
            box.fill(text)
            box.press("Enter")

        def wait_text(text: str, secs=90):
            page.wait_for_function("t => document.body.innerText.includes(t)", arg=text, timeout=secs * 1000)
            return True

        say("hello from the browser")
        check("CopilotKit: a resposta do agente chega no chat (AG-UI pelo runtime do app)",
              wait_text("eco: hello from the browser"))
        say("use-tool open_order")
        check("CopilotKit: o agente pede a ferramenta do app e o navegador roda", wait_text("open_order(4471)"))
        check("CopilotKit: o agente usa o resultado que o app devolveu", wait_text("eco-tool: order 4471 opened on the screen"))
        check("CopilotKit: o pedido fica destacado na tela", "open" in (page.get_by_test_id("order").get_attribute("class") or ""))
        say("use-tool app_state_update /order/status=paid")
        page.wait_for_function("() => document.querySelector('[data-testid=status]')?.innerText === 'paid'", timeout=90_000)
        check("CopilotKit: o agente muda o estado do app e a tela mostra o estado novo", status.inner_text() == "paid")
        page.screenshot(path=os.environ.get("E2E_SCREENSHOT", "/tmp/copilotkit-e2e.png"))
        b.close()
    check("a chave invoke fica no servidor do app (nada no navegador)", not any(env["HANGAR_KEY"] in s for s in sent),
          len(sent))
    cors_direct(env)
    rows = [u for u in ok(admin.get("/api/usage")) if u["agent"] == env["HANGAR_AGENT"]]
    check("o uso fica medido pelo canal copilotkit, protocolo AG-UI",
          any(u["channel"] == "copilotkit" and u["protocol"] == "agui" for u in rows), rows[:3])


def cors_direct(env: dict):
    """CORS por agente, num navegador de verdade: a página do app (outra origem) chama o gateway com fetch; o navegador
    faz o preflight e só entrega a resposta porque a origem está em spec.cors.origins."""
    from playwright.sync_api import sync_playwright

    gw = f"{env['HANGAR_URL']}{env['HANGAR_GATEWAY']}/{env['HANGAR_AGENT']}"
    js = """async ([url, key]) => {
      try {
        const r = await fetch(url, {method: 'POST', headers: {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
          'Accept': 'text/event-stream'}, body: JSON.stringify({threadId: 't', runId: 'r', messages: [{id: '1', role: 'user',
          content: 'direto do navegador'}], tools: [], context: [], state: {}, forwardedProps: {}})});
        return {status: r.status, text: await r.text()};
      } catch (e) { return {error: String(e)}; }
    }"""
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(APP)
        r = page.evaluate(js, [f"{gw}/agui", env["HANGAR_KEY"]])
        evs = [json.loads(line[6:]) for line in r.get("text", "").splitlines() if line.startswith("data: {")]
        text = "".join(e.get("delta", "") for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
        check("CORS por agente: a página do site liberado chama o gateway direto (preflight no navegador)",
              r.get("status") == 200 and "eco: direto do navegador" in text, (r.get("status"), r.get("error"), text))
        page.goto("about:blank")  # origem que não está na spec: o navegador bloqueia a resposta
        page.set_content("<p>outra origem</p>")
        r = page.evaluate(js, [f"{gw}/agui", env["HANGAR_KEY"]])
        check("CORS por agente: uma origem fora da lista não lê a resposta", "error" in r, r)
        b.close()


def cleanup(env: dict):
    slug = env.get("HANGAR_AGENT")
    if slug:
        admin.post(f"/api/agents/{slug}/stop", json={"env": "stage"})
        admin.delete(f"/api/agents/{slug}")
    if env.get("E2E_KEY_ID"):
        admin.delete(f"/api/keys/{env['E2E_KEY_ID']}")
    if env.get("E2E_CONNECTION"):
        admin.delete(f"/api/catalog/llm/{env['E2E_CONNECTION']}")


def main():
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step == "setup":
        return setup()
    env = read_env(sys.argv[2])
    if step == "browser":
        browser(env)
        print(f"\n{passed} verificações passaram", flush=True)
    elif step == "cleanup":
        cleanup(env)
    else:
        raise SystemExit("uso: copilotkit.py setup | browser <env> | cleanup <env>")


if __name__ == "__main__":
    sys.exit(main())
