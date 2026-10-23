"""Ações pela interface, com cliques de verdade (o i18n_crawl só navega): no console, num agente com o modelo simulado,
salvar a spec, rodar os testes, deploy em stage, Playground, criar um agendamento, salvar o perfil de execução e trocar
o avatar; no portal, como membro, criar uma issue pelo + da coluna, comentar, mudar o status, a busca global (que acha
os agentes da pessoa) e a troca de idioma. Confere cada resultado pela API e apaga o que criou.

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> python tests/e2e/ui_actions.py

Precisa de: pip install playwright httpx && playwright install chromium.
"""
import json
import os
import time
import uuid

import httpx
from playwright.sync_api import sync_playwright

URL = os.environ.get("HANGAR_URL", "http://localhost:8090").rstrip("/")
TOKEN = os.environ["HANGAR_TOKEN"]
RUN = uuid.uuid4().hex[:6]
admin = httpx.Client(base_url=URL + "/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=180)
failed: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(("ok   " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failed.append(name)


def ok(r):
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code} {r.request.method} {r.request.url}: {r.text[:300]}")
    return r.json()


def watch(pg, errors: list):
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.on("response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url.split(URL)[-1][:100]}")
          if "/api/" in r.url and r.status >= 500 else None)


def console(b) -> None:
    team = ok(admin.post("/teams", json={"name": f"E2E UI {RUN}"}))["slug"]
    slug = ok(admin.post("/agents", json={"name": f"E2E UI {RUN}", "objective": "repete o que recebe",
                                          "final_output": "eco", "team": team}))["slug"]
    try:
        pg = b.new_context(viewport={"width": 1400, "height": 950},
                           extra_http_headers={"Authorization": f"Bearer {TOKEN}"}).new_page()
        errors: list = []
        watch(pg, errors)

        def go(tab, wait=2000):
            pg.goto(f"{URL}/ui/#/agents/{slug}/{tab}")
            pg.wait_for_timeout(wait)
        go("spec")
        spec = json.loads(pg.input_value("#sp-json"))
        spec.update(instructions="Responda repetindo a mensagem.", llm={"model": "mock/echo"},
                    tests=[{"name": "eco", "input": "olá mundo", "expect_contains": "olá mundo"}])
        pg.fill("#sp-json", json.dumps(spec, ensure_ascii=False, indent=2))
        pg.click("#sp-save")
        pg.wait_for_timeout(2500)
        a = ok(admin.get(f"/agents/{slug}"))
        check("console: salvar a spec cria a versão 2", a["version"] == 2)
        go("overview")
        pg.click("#b-test")
        lt: dict = {}
        t0 = time.time()
        while time.time() - t0 < 240:
            lt = ok(admin.get(f"/agents/{slug}")).get("last_test") or {}
            if lt.get("version") == 2 and lt.get("status") in ("passed", "failed"):
                break
            pg.wait_for_timeout(2000)
        check("console: rodar os testes", lt.get("status") == "passed", str(lt)[:200])
        before = (ok(admin.get(f"/agents/{slug}")).get("stage") or {}).get("id")  # os testes já deixam um stage no ar
        go("overview")
        pg.click("#b-stage")
        now: dict = {}
        t0 = time.time()
        while time.time() - t0 < 240:
            now = ok(admin.get(f"/agents/{slug}")).get("stage") or {}
            if now.get("id") and now["id"] != before and now.get("status") == "running":
                break
            pg.wait_for_timeout(2000)
        check("console: deploy em stage", bool(now.get("id")) and now.get("id") != before, str(now)[:150])
        go("playground")
        pg.select_option("#penv", "stage")
        pg.fill("#pmsg", "olá do playground")
        pg.click("#psend")
        pg.wait_for_timeout(6000)
        last = pg.locator("#chat .msg.a").last.inner_text()
        check("console: Playground responde", "olá do playground" in last, last[:150])
        go("schedules")
        pg.fill("#sc-msg", "Resumo de teste")
        pg.click("#sc-go")
        pg.wait_for_timeout(2500)
        sch = [s for s in ok(admin.get("/schedules")) if s.get("agent") == slug]
        check("console: criar agendamento", len(sch) == 1, f"{len(sch)}")
        for s in sch:
            admin.delete(f"/schedules/{s['id']}")
        go("overview", 3000)
        pg.uncheck("#ex-compact")
        pg.click("#ex-save")
        pg.wait_for_timeout(2500)
        ex = (ok(admin.get(f"/agents/{slug}"))["spec"] or {}).get("execution") or {}
        check("console: salvar o perfil de execução", ex.get("compact") is False, str(ex))
        go("overview")
        pg.click("#b-av")
        pg.wait_for_timeout(1500)
        pg.click(".av-grid button[data-id='maestro']")
        pg.wait_for_timeout(2000)
        check("console: trocar o avatar", ok(admin.get(f"/agents/{slug}")).get("avatar") == "maestro")
        check("console: nenhum erro de JavaScript nem 5xx", not errors, "; ".join(errors[:5]))
    finally:
        admin.delete(f"/agents/{slug}")
        admin.delete(f"/teams/{team}")


def portal(b) -> None:
    team = ok(admin.post("/teams", json={"name": f"E2E portal {RUN}"}))["slug"]
    email, user, pw = f"e2e-{RUN}-ui@example.com", f"e2e-{RUN}-ui", f"pw-{uuid.uuid4().hex}"
    uid = ok(admin.post("/users", json={"email": email, "name": "E2E UI", "username": user, "password": pw}))["id"]
    ok(admin.post(f"/teams/{team}/members", json={"email": email, "role": "maintainer"}))
    agent = ok(admin.post("/agents", json={"name": f"Radar Portal {RUN}", "objective": "boletim", "final_output": "e-mail",
                                           "team": team}))["slug"]
    me = httpx.Client(base_url=URL + "/api", timeout=60)
    ok(me.post("/auth/password", json={"username": user, "password": pw}))
    me.headers["X-CSRF-Token"] = me.cookies.get("hangar_csrf") or ""
    key = None
    try:
        ctx = b.new_context(viewport={"width": 1400, "height": 950})
        assert ctx.request.post(URL + "/api/auth/password", data={"username": user, "password": pw}).ok
        pg = ctx.new_page()
        errors: list = []
        watch(pg, errors)
        pg.goto(URL + "/app/#/my-issues")
        pg.wait_for_timeout(2500)
        col = pg.locator(".iss-col-add").first
        status = col.get_attribute("data-status")
        col.click()
        pg.wait_for_timeout(1200)
        title = f"E2E portal {RUN}"
        pg.fill("#in-title", title)
        pg.click("#in-go")
        pg.wait_for_timeout(2500)
        mine = [i for i in ok(me.get("/issues", params={"q": RUN})) if i["title"] == title]
        check("portal: criar issue pelo + da coluna", len(mine) == 1 and mine[0]["status"] == status)
        if mine:
            key = mine[0]["key"]
            pg.goto(f"{URL}/app/#/issues/{key}")
            pg.wait_for_timeout(2500)
            pg.fill("#id-cm", "Comentário do teste")
            pg.click("#id-cm-go")
            pg.wait_for_timeout(2000)
            d = ok(me.get(f"/issues/{key}"))
            check("portal: comentar", "Comentário do teste" in str(d.get("timeline")))
            target = next(o for o in pg.eval_on_selector("#ip-status", "s => [...s.options].map(o => o.value)")
                          if o != d["status"] and o != "cancelled")
            pg.select_option("#ip-status", target)
            pg.wait_for_timeout(2000)
            check("portal: mudar o status", ok(me.get(f"/issues/{key}"))["status"] == target)
        pg.goto(URL + "/app/#/")
        pg.wait_for_timeout(2000)
        pg.fill("#gsearch", "Radar Portal")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(2500)
        check("portal: a busca acha o agente da pessoa", f"Radar Portal {RUN}" in pg.inner_text("main"))
        pg.evaluate("setLang('en')")
        pg.wait_for_timeout(3000)
        nav = pg.inner_text("nav")
        check("portal: menu em inglês", "My issues" in nav and "My agents" in nav, nav[:150])
        pg.evaluate("setLang('pt')")
        pg.wait_for_timeout(2000)
        check("portal: nenhum erro de JavaScript nem 5xx", not errors, "; ".join(errors[:5]))
    finally:
        if key:  # só sai da lista concluída ou cancelada, e quem tira é quem tem a issue
            me.patch(f"/issues/{key}", json={"status": "cancelled"})
            me.delete(f"/issues/{key}")
        admin.delete(f"/agents/{agent}")
        admin.patch(f"/users/{uid}", json={"active": False})
        admin.delete(f"/teams/{team}")


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            console(b)
            portal(b)
        finally:
            b.close()
    if failed:
        raise SystemExit(f"{len(failed)} ação(ões) falharam: {', '.join(failed)}")


if __name__ == "__main__":
    main()
