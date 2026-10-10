"""Percorre o portal (/app) e o console (/ui) em inglês e falha se sobrar texto em português na tela.

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> python tests/e2e/i18n_crawl.py [--report]

Cria uma pessoa admin (conta local), um agente, um Digital employee (rascunho) e um plugin (rascunho) para as telas
de detalhe terem conteúdo, e apaga tudo no fim. Ignora o que é dado (data-noi18n, code, pre, campos de formulário e o
que as pessoas cadastraram, buscado pela API). --report só lista, sem falhar. Precisa de: pip install playwright httpx
&& playwright install chromium.
"""
import os
import re
import sys
import uuid

import httpx
from playwright.sync_api import sync_playwright

URL = os.environ.get("HANGAR_URL", "http://localhost:8090").rstrip("/")
RUN = uuid.uuid4().hex[:6]
admin = httpx.Client(base_url=URL, timeout=120, headers={"Authorization": f"Bearer {os.environ['HANGAR_TOKEN']}"})
# acentos, ou palavras que só existem em português ("do", "a", "e" ficam de fora porque existem em inglês). Palavra
# colada em ponto, @, hífen ou letra não conta: company.com, engenheiro-de-codigo
PT = re.compile(r"[ãõçáéíóúâêôàÃÕÇÁÉÍÓÚÂÊÔ]|(?<![.@\w-])(de|da|dos|das|para|com|sem|não|você|seu|sua|uma|um|pelo|"
                r"pela|está|são|já|ainda|nenhum|nenhuma|aqui|agora|quando|cada|ou|também|só|até|onde|isso|este|"
                r"esta)(?![\w.-])", re.I)
# texto que é igual nas duas línguas, ou que é dado criado pelo próprio teste
ALLOW = re.compile(r"^(e2e|E2E|https?://|[\w.-]+@[\w.-]+$)")
# dado (o que as pessoas cadastraram) não se traduz: nomes, descrições, e-mails, chaves, conexões, templates
DATA_ENDPOINTS = ["/api/agents", "/api/users", "/api/keys", "/api/catalog", "/api/templates", "/api/employees",
                  "/api/plugins", "/api/teams", "/api/remote-mcps", "/api/connect", "/api/deployments", "/api/tests",
                  "/api/usage", "/api/schedules", "/api/oauth/clients"]
PAGES_APP = ["", "inbox", "search?q=e2e", "projects", "goals", "budgets", "files", "agents", "agents/catalog", "agents/new", "approvals",
             "employees", "employees/new", "manager", "decisions",
             "templates", "catalog", "plugins", "plugins/new", "connect", "keys", "usage", "teams", "audit", "users"]
PAGES_UI = ["", "inbox", "projects", "goals", "budgets", "files", "agents", "approvals", "workforce/overview", "workforce/catalog", "workforce/floor", "workforce/notify",
            "decisions", "templates", "catalog", "plugins", "providers", "connect", "deployments", "tests", "usage",
            "audit", "teams", "users", "keys", "sso"]

LEFTOVERS_JS = """() => {
  const SKIP = 'script,style,code,pre,textarea,input,select,[data-noi18n],.msg,.run-out,.chat,.md';
  const out = new Set();
  const it = document.createTreeWalker(document.querySelector('main') || document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = it.nextNode())) {
    const el = n.parentElement;
    if (!el || el.closest(SKIP)) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || el.closest('[hidden]')) continue;
    const t = n.nodeValue.replace(/\\s+/g, ' ').trim();
    if (t.length > 1) out.add(t);
  }
  document.querySelectorAll('main [placeholder], main [title]').forEach(e => {
    if (e.closest('[data-noi18n]')) return;
    for (const a of ['placeholder', 'title']) { const v = (e.getAttribute(a) || '').trim(); if (v) out.add(v); }
  });
  return [...out];
}"""


def ok(r):
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code} {r.request.method} {r.request.url}: {r.text[:300]}")
    return r.json()


def setup() -> tuple[str, str, dict]:
    team = ok(admin.post("/api/teams", json={"name": f"E2E i18n {RUN}"}))["slug"]
    email, pw = f"e2e-{RUN}-i18n@example.com", f"pw-{uuid.uuid4().hex}"
    u = ok(admin.post("/api/users", json={"email": email, "name": "E2E i18n", "org_role": "admin",
                                          "username": f"e2e-{RUN}-i18n", "password": pw}))
    ok(admin.post(f"/api/teams/{team}/members", json={"email": email, "role": "maintainer"}))
    made = {"team": team, "user": u["id"], "agent": f"e2e-{RUN}-i18n", "employee": None, "plugin": f"e2e-i18n-{RUN}"}
    ok(admin.post("/api/apply", json={"agents": [{"name": "E2E i18n", "slug": made["agent"], "objective": "eco",
                                                  "final_output": "eco", "team": team}]}))
    me = httpx.Client(base_url=URL, timeout=120)
    ok(me.post("/api/auth/password", json={"username": f"e2e-{RUN}-i18n", "password": pw}))
    me.headers["X-CSRF-Token"] = me.cookies.get("hangar_csrf") or ""
    job = {"name": f"E2E Desk {RUN}", "title": "Desk", "mission": "Keep the work moving.",
           "responsibilities": ["a", "b", "c"], "manager": email, "team": team, "systems": ["ERP"],
           "llm": {"model": "mock/echo"}, "channels": ["portal"], "accept_default_authority": True,
           "probation_tasks": [{"title": f"P{i}", "body": f"p {i}", "expected": f"p {i}"} for i in range(3)]}
    made["employee"] = ok(me.post("/api/employees", json=job))["employee"]["slug"]
    ok(me.post("/api/plugins", json={"team": team, "manifest": {
        "name": made["plugin"], "title": "E2E i18n", "version": "1.0.0", "base_url": "https://example.com",
        "tools": [{"name": "get_one", "method": "GET", "path": "/one"}],
        "triggers": [{"name": "paid", "title": "Paid", "task_title": "Invoice {{event.id}}", "example": {"id": "1"}}]}}))
    made["goal"] = ok(admin.post("/api/goals", json={"title": f"E2E goal {RUN}"}))["id"]
    made["project"] = ok(me.post("/api/projects", json={"name": f"E2E project {RUN}", "team": team, "goal_id": made["goal"],
                                                         "instructions": "i"}))["slug"]
    made["budget"] = ok(admin.post("/api/budgets", json={"scope_type": "project", "scope": made["project"],
                                                         "amount_usd": 10}))["id"]
    return f"e2e-{RUN}-i18n", pw, made


def cleanup(made: dict):
    if made.get("budget"):
        admin.delete(f"/api/budgets/{made['budget']}")
    if made.get("project"):
        admin.patch(f"/api/projects/{made['project']}", json={"status": "archived"})
    if made.get("goal"):
        admin.delete(f"/api/goals/{made['goal']}")
    if made.get("employee"):
        admin.delete(f"/api/agents/{made['employee']}")
    admin.delete(f"/api/agents/{made['agent']}")
    admin.delete(f"/api/plugins/{made['plugin']}")
    admin.patch(f"/api/users/{made['user']}", json={"active": False})
    admin.delete(f"/api/teams/{made['team']}")


def data_values() -> set[str]:
    values: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and 3 <= len(v.strip()) <= 400:
            values.add(v.strip())
    for ep in DATA_ENDPOINTS:
        r = admin.get(ep)
        if r.status_code < 400:
            walk(r.json())
    return {v for v in values if PT.search(v)}


def is_data(text: str, values: set[str]) -> bool:
    t = re.sub(r"^(by|por) ", "", text)
    return t in values or any(len(v) >= 6 and v in t for v in values)


def crawl(username: str, pw: str, made: dict) -> dict[str, list[str]]:
    values = data_values()
    found: dict[str, list[str]] = {}
    errors: list[str] = []
    pages = [("/app/#/" + p) for p in PAGES_APP] + [("/ui/#/" + p) for p in PAGES_UI] + [
        f"/app/#/agents/{made['agent']}", f"/app/#/employees/{made['employee']}/overview",
        f"/app/#/employees/{made['employee']}/settings", f"/app/#/employees/{made['employee']}/authority",
        f"/app/#/plugins/{made['plugin']}", f"/app/#/projects/{made['project']}"]
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        ctx.request.post(f"{URL}/api/auth/password", data={"username": username, "password": pw})
        ctx.add_init_script("localStorage.setItem('hangar_lang', 'en')")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"{pg.url}: {e} @ {' | '.join((e.stack or '').splitlines()[1:3])}"))
        for path in pages:
            pg.goto(URL + path)
            try:
                pg.wait_for_selector("main h1", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            pg.wait_for_load_state("networkidle")  # a tela terminou de carregar: sem corrida com a próxima
            pg.wait_for_timeout(600)
            bad = sorted(t for t in pg.evaluate(LEFTOVERS_JS)
                         if PT.search(t) and not ALLOW.match(t) and not is_data(t, values))
            if bad:
                found[path] = bad
        b.close()
    if errors:
        found["(erros de JavaScript)"] = errors
    return found


def main():
    username, pw, made = setup()
    try:
        found = crawl(username, pw, made)
    finally:
        cleanup(made)
    total = sum(len(v) for v in found.values())
    for path, texts in found.items():
        print(f"\n{path}")
        for t in texts:
            print(f"   {t[:160]}")
    print(f"\n{total} texto(s) em português na interface em inglês")
    if total and "--report" not in sys.argv:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
