"""E2E das funcionalidades que os outros e2e não exercitam, contra um Agent Hangar rodando de verdade (modo mock).

    HANGAR_URL=http://localhost:8090 HANGAR_TOKEN=<admin> ECHO_URL=http://e2e-echo:8080 python tests/e2e/coverage.py

Ciclo de vida do Digital employee (pausa, parar todos, desligamento), rotinas, webhook de entrada, modo sombra e
carreira, piso da empresa com duas aprovações, orçamento com parada dura (com um LLM de mentira que cobra), governança
(retenção, pedido do titular com aprovação de outro admin, barreira de informação e acesso de emergência), avisos,
pedido de acesso a um agente de outro time, versões e rollback, templates e o compositor, SCIM, calendário, auditoria,
métricas e painéis. Ao contrário dos outros, não para na primeira falha: roda tudo e lista o que falhou no fim.
"""
import os
import sys
import time
import traceback
import uuid

import httpx

sys.path.insert(0, os.path.dirname(__file__))
import workforce as wf  # noqa: E402  (pessoas, contratação e ativação do Digital employee mock)

ok, admin, RUN, ECHO, URL = wf.ok, wf.admin, wf.RUN, wf.ECHO, wf.URL
failures: list[str] = []
passed = 0
CPF = "529.982.247-25"  # válido (dígitos verificadores), de teste


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"ok   {name}", flush=True)
    else:
        failures.append(name)
        print(f"FAIL {name} {str(detail)[:400]}", flush=True)
    return bool(cond)


def section(fn, *args):
    try:
        return fn(*args)
    except BaseException as ex:  # noqa: BLE001 — uma seção quebrada não esconde as outras
        failures.append(f"{fn.__name__}: {ex}")
        print(f"FAIL {fn.__name__}: {ex}", flush=True)
        traceback.print_exc()
        return None


def admin_person(key: str) -> tuple[httpx.Client, str]:
    """Outro admin da empresa (o pedido do titular exige a aprovação de alguém que não pediu)."""
    email, pw = f"e2e-{RUN}-{key}@example.com", f"pw-{uuid.uuid4().hex}"
    u = ok(admin.post("/api/users", json={"email": email, "name": f"E2E {key}", "username": f"e2e-{RUN}-{key}",
                                          "password": pw, "org_role": "admin"}))
    wf.created["users"].append(u["id"])
    c = httpx.Client(base_url=URL, timeout=300)
    ok(c.post("/api/auth/password", json={"username": f"e2e-{RUN}-{key}", "password": pw}))
    c.headers["X-CSRF-Token"] = c.cookies.get("hangar_csrf") or ""
    return c, email


def assign(c, slug, body, title="Task", **extra):
    return ok(c.post(f"/api/employees/{slug}/tasks", json={"title": title, "body": body, **extra}))["id"]


def status_of(c, slug):
    return ok(c.get(f"/api/employees/{slug}"))["status"]


# ------------------------------------------------------------------ ciclo de vida
def lifecycle(dev, mgr, slug, out):
    check("pausa: quem é de fora do time não pausa", out.post(f"/api/employees/{slug}/status", json={"to": "paused"}).status_code
          in (403, 404))
    ok(mgr.post(f"/api/employees/{slug}/status", json={"to": "paused"}))
    tid = assign(mgr, slug, "answer paused")
    time.sleep(8)
    check("pausa: a tarefa espera na fila", wf.task(mgr, tid)["status"] == "new", wf.task(mgr, tid)["status"])
    ok(mgr.post(f"/api/employees/{slug}/status", json={"to": "active"}))
    check("pausa: retomado, a tarefa roda", (wf.until(mgr, tid, ("done", "failed")) or {}).get("status") == "done")
    r = ok(admin.post("/api/admin/workforce/stop-all", json={"reason": f"e2e {RUN}"}))
    check("parar todos: o funcionário pausa", r["paused"] >= 1 and status_of(mgr, slug) == "paused", r)
    ok(mgr.post(f"/api/employees/{slug}/status", json={"to": "active"}))
    check("parar todos: volta ao ativo", status_of(mgr, slug) == "active")
    audit = ok(admin.get("/api/audit", params={"limit": 200}))
    rows = audit if isinstance(audit, list) else audit.get("items") or audit.get("rows") or []
    check("auditoria: a pausa ficou registrada", any("status" in str(x.get("action", "")) or "stop" in str(x.get("action", ""))
                                                     for x in rows), str(rows[:3])[:300])


def routines(dev, mgr, slug):
    base = f"/api/employees/{slug}/routines"
    check("rotina: cron inválido recusado", mgr.post(base, json={"title": "x", "cron": "não é cron"}).status_code == 400)
    r = ok(mgr.post(base, json={"title": f"Weekly check {RUN}", "body": "answer weekly", "cron": "0 9 * * 1"}))
    check("rotina: criada com próxima execução", bool(r.get("next_run_at")) and r["enabled"], r)
    t = ok(mgr.post(f"{base}/{r['id']}/run"))
    done = wf.until(mgr, t["id"], ("done", "failed"))
    check("rotina: executar agora vira tarefa concluída", done and done["status"] == "done" and done.get("source") == "routine", done)
    off = ok(mgr.patch(f"{base}/{r['id']}", json={"enabled": False}))
    check("rotina: desligada não tem próxima execução", off["enabled"] is False and off["next_run_at"] is None, off)
    ok(mgr.delete(f"{base}/{r['id']}"))
    check("rotina: removida", ok(mgr.get(base)) == [])


def inbound_webhook(dev, mgr, slug):
    w = ok(mgr.post(f"/api/employees/{slug}/webhook", json={}))
    url = f"{URL}/hooks/employees/{slug}"
    check("webhook: sem token é recusado", httpx.post(url, json={"title": "x"}).status_code == 401)
    h = {"Authorization": f"Bearer {w['token']}"}
    first = httpx.post(url, headers=h, json={"title": f"Lead {RUN}", "body": "answer lead", "dedupe_key": f"crm-{RUN}"}).json()
    again = httpx.post(url, headers=h, json={"title": f"Lead {RUN}", "dedupe_key": f"crm-{RUN}"}).json()
    check("webhook: o evento vira tarefa e o repetido não duplica", first["duplicate"] is False and again["duplicate"] is True
          and again["id"] == first["id"], (first, again))
    t = wf.until(mgr, first["id"], ("done", "failed"))
    check("webhook: a tarefa é feita", t and t["status"] == "done", t)


def shadow_and_career(dev, mgr, slug):
    ok(mgr.post(f"/api/employees/{slug}/shadow", json={"on": True, "reason": f"e2e {RUN}"}))
    tid = assign(mgr, slug, 'use send_email {"to": "shadow@x.com"}')
    t = wf.until(mgr, tid, ("done", "failed", "waiting_human"))
    check("sombra: a ação não roda e a tarefa conclui", t and t["status"] == "done", t and t["status"])
    items = ok(mgr.get(f"/api/employees/{slug}/shadow", params={"status": "open"}))["actions"]
    mine = next((x for x in items if (x.get("task") or {}).get("id") == tid or x.get("task_id") == tid), None)
    check("sombra: a ação simulada espera revisão do gestor", mine is not None, items[:2])
    if mine:
        d = ok(mgr.post(f"/api/decisions/{mine['id']}", json={"decision": "agree"}))
        check("sombra: o gestor concorda", d["status"] == "decided" and d["decision"] == "agree", d)
    ok(mgr.post(f"/api/employees/{slug}/shadow", json={"on": False, "reason": "fim do teste"}))
    car = ok(mgr.get(f"/api/employees/{slug}/career"))
    check("carreira: critérios e nível", "criteria" in car and car.get("level"), list(car)[:8])


def floor_two_approvals(dev, mgr, slug, adm2):
    before = {x["action_type"]: x for x in ok(admin.get("/api/admin/authority-floor"))}
    old = before.get("send_external") or {"min_mode": "approve", "separation": False}
    ok(admin.put("/api/admin/authority-floor", json={"items": [{"action_type": "send_external", "min_mode": "approve_2",
                                                                "separation": False}]}))
    try:
        tid = assign(mgr, slug, 'use send_email {"to": "two@x.com"}')
        t = wf.until(mgr, tid, ("waiting_human", "done", "failed"))
        req = next((q for q in (t or {}).get("requests", []) if q["status"] == "open" and q["kind"] == "approval"), None)
        check("piso: duas aprovações exigidas", req and req["mode"] == "approve_2", req)
        if req:
            one = ok(mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
            check("piso: a primeira aprovação não basta", one["status"] == "open" and len(one["approvals"]) == 1, one)
            again = mgr.post(f"/api/decisions/{req['id']}", json={"decision": "approve"})
            check("piso: a mesma pessoa não aprova duas vezes", again.status_code == 400, again.text)
            two = ok(adm2.post(f"/api/decisions/{req['id']}", json={"decision": "approve"}))
            check("piso: a segunda pessoa libera", two["status"] == "decided", two)
            check("piso: a ação roda depois das duas", (wf.until(mgr, tid, ("done", "failed")) or {}).get("status") == "done")
    finally:
        ok(admin.put("/api/admin/authority-floor", json={"items": [{"action_type": "send_external", "min_mode": old["min_mode"],
                                                                    "separation": bool(old.get("separation"))}]}))


def budget_hard_stop(dev, mgr, team):
    """Parada dura pelo gateway: um agente com um LLM de mentira que cobra; estourou, a próxima chamada é recusada até
    alguém liberar. (A parada da tarefa do Digital employee, waiting_budget, é coberta pelos testes de unidade.)"""
    conn = f"e2e-cov-llm-{RUN}"
    ok(admin.post("/api/catalog/llm", json={"name": conn, "provider": "openrouter", "base_url": f"{ECHO}/llm/v1",
                                           "api_key": "e2e", "model_name": ""}))
    wf.created.setdefault("llm", []).append(conn)
    slug = ok(dev.post("/api/agents", json={"name": f"Priced helper {RUN}", "objective": "Answer questions.",
                                           "final_output": "An answer.", "team": team}))["slug"]
    wf.created["agents"].append(slug)
    ok(dev.patch(f"/api/agents/{slug}", json={"llm": {"connection": conn, "model": "e2e/thinker"}}))
    ok(dev.post(f"/api/agents/{slug}/deploy", json={"env": "stage"}))
    gw = f"/gw-stage/{slug}/v1/chat/completions"
    call = lambda: dev.post(gw, json={"model": slug, "messages": [{"role": "user", "content": f"oi {RUN}"}]})  # noqa: E731
    first = call()
    check("orçamento: a chamada pelo gateway responde", first.status_code == 200, first.text[:200])
    pol = ok(mgr.post("/api/budgets", json={"scope_type": "agent", "scope": slug, "amount_usd": 0.000001, "window": "day"}))
    blocked = wf.wait(lambda: (lambda r: r if r.status_code == 429 else None)(call()), 60, 3)
    check("orçamento: estourou, a próxima chamada é recusada (429)", blocked is not None and "orçamento" in blocked.text,
          blocked.text[:200] if blocked is not None else "sem 429")
    b = ok(mgr.get("/api/budgets"))
    inc = next((i for i in b["incidents"] if i["policy_id"] == pol["id"] and i["kind"] == "hard_stop" and i["status"] == "open"), None)
    check("orçamento: incidente aberto para o gestor", inc is not None, b["incidents"][:3])
    if inc:
        check("orçamento: o desenvolvedor não libera", dev.post(f"/api/budgets/incidents/{inc['id']}/release", json={}).status_code == 403)
        ok(mgr.post(f"/api/budgets/incidents/{inc['id']}/release", json={"note": f"e2e {RUN}"}))
        again = wf.wait(lambda: (lambda r: r if r.status_code == 200 else None)(call()), 60, 3)
        check("orçamento: liberado, volta a responder", again is not None)
    ok(mgr.delete(f"/api/budgets/{pol['id']}"))


def governance(dev, mgr, mgr_email, team, slug, adm2):
    gov = ok(admin.get("/api/governance"))
    old_ret = gov.get("retention", {}).get("internal")
    g = ok(admin.put("/api/governance", json={"retention": {"internal": 400}}))
    check("retenção: política gravada", g["retention"]["internal"] == 400, g.get("retention"))
    ok(admin.put("/api/governance", json={"retention": {"internal": old_ret}}))
    tid = assign(mgr, slug, f"answer client {CPF} renewal")
    wf.until(mgr, tid, ("done", "failed"))
    acc = ok(admin.post("/api/subject-requests", json={"subject": CPF, "kind": "access"}))
    found = (acc.get("report") or {}).get("found") or {}
    check("titular: acesso acha a tarefa", acc["status"] == "done" and any(x["id"] == tid for x in found.get("tasks", [])), found)
    er = ok(admin.post("/api/subject-requests", json={"subject": CPF, "kind": "erase"}))
    check("titular: quem pediu não aprova a remoção", admin.post(f"/api/subject-requests/{er['id']}/erase", json={}).status_code == 403)
    done = ok(adm2.post(f"/api/subject-requests/{er['id']}/erase", json={}))
    check("titular: outro admin remove", done["status"] == "done", done)
    t = wf.task(mgr, tid)
    check("titular: o dado some da tarefa", CPF not in (t.get("body") or "") and CPF not in (t.get("result") or ""), t.get("body"))
    # barreira de informação
    p = ok(mgr.post("/api/projects", json={"name": f"M&A {RUN}", "team": team}))
    ok(mgr.put(f"/api/projects/{p['slug']}/governance", json={"confidential": True, "members": [mgr_email], "classification": "restricted"}))
    bt = assign(mgr, slug, "answer confidential", project=p["slug"])
    check("barreira: quem é do time mas não do projeto não vê", dev.get(f"/api/tasks/{bt}").status_code in (403, 404))
    check("barreira: nem outro admin, sem justificativa", adm2.get(f"/api/tasks/{bt}").status_code in (403, 404))
    check("barreira: justificativa curta recusada", adm2.post(f"/api/projects/{p['slug']}/break-glass", json={"reason": "x"}).status_code == 400)
    ok(adm2.post(f"/api/projects/{p['slug']}/break-glass", json={"reason": f"auditoria do contrato e2e {RUN}"}))
    check("barreira: acesso de emergência abre", adm2.get(f"/api/tasks/{bt}").status_code == 200)


def workspace_bits(dev, mgr, team, slug):
    a = ok(admin.post("/api/announcements", json={"text": f"Manutenção e2e {RUN}", "level": "warn", "days": 1}))
    seen = ok(dev.get("/api/announcements"))
    seen = seen if isinstance(seen, list) else seen.get("items", [])
    check("avisos: o membro vê", any(x.get("id") == a.get("id") for x in seen), seen[:2])
    ok(admin.delete(f"/api/announcements/{a['id']}"))
    live = mgr.get("/api/live")
    check("ao vivo: responde", live.status_code == 200, live.text[:200])
    wd = mgr.get("/api/workforce/dashboard")
    check("painel do gestor: responde", wd.status_code == 200, wd.text[:200])
    for path in ("/api/usage", "/api/approvals", "/api/inbox"):
        r = mgr.get(path)
        check(f"painel: {path}", r.status_code == 200, r.text[:200])
    m = admin.get("/api/metrics")
    check("métricas Prometheus", m.status_code == 200 and "# TYPE" in m.text, m.text[:200])
    check("métricas: só admin e auditor", dev.get("/api/metrics").status_code == 403)
    cal = ok(admin.get("/api/calendar"))
    ok(admin.put("/api/calendar", json={"holidays": [*cal.get("holidays", []), {"date": "2026-12-31", "name": f"e2e {RUN}"}]}))
    check("calendário: feriado gravado", any(h["name"] == f"e2e {RUN}" for h in ok(admin.get("/api/calendar"))["holidays"]))
    ok(admin.put("/api/calendar", json={"holidays": cal.get("holidays", [])}))
    mem = admin.get("/api/memory/status")
    check("memória: status", mem.status_code == 200, mem.text[:200])


def access_and_versions(dev, mgr, team, out):
    slug = ok(dev.post("/api/agents", json={"name": f"Pricing helper {RUN}", "objective": "Answer pricing questions.",
                                           "final_output": "An answer.", "team": team}))["slug"]
    wf.created["agents"].append(slug)
    r = ok(out.post(f"/api/agents/{slug}/access-requests", json={"reason": f"e2e {RUN}"}))
    todo = ok(mgr.get("/api/approvals"))["to_decide"]
    check("acesso: o mantenedor vê o pedido", any(x["kind"] == "access" and x["id"] == r["id"] for x in todo))
    check("acesso: o desenvolvedor não aprova", dev.post(f"/api/access-requests/{r['id']}/approve").status_code == 403)
    check("acesso: aprovado", ok(mgr.post(f"/api/access-requests/{r['id']}/approve"))["status"] == "approved")
    conn = out.post(f"/api/agents/{slug}/connections", json={"client": "claude-code"})
    check("acesso: com acesso, conecta a ferramenta", conn.status_code == 200, conn.text[:200])
    # versões e rollback
    v1 = ok(dev.get(f"/api/agents/{slug}"))
    ok(dev.patch(f"/api/agents/{slug}", json={"instructions": f"Always cite the price table. {RUN}"}))
    v2 = ok(dev.get(f"/api/agents/{slug}"))
    check("versão: a edição cria uma versão nova", v2.get("version", 0) > v1.get("version", 0), (v1.get("version"), v2.get("version")))
    rb = ok(dev.post(f"/api/agents/{slug}/rollback", json={"version": v1["version"]}))
    check("rollback: o conteúdo antigo volta como versão nova", rb.get("version", 0) > v2["version"]
          and (rb.get("spec") or {}).get("instructions") == (v1.get("spec") or {}).get("instructions"),
          (rb.get("version"), (rb.get("spec") or {}).get("instructions")))
    steps = ok(dev.post(f"/api/agents/{slug}/ship"))
    check("ship: stage, testes e produção", any(s.get("step") == "prod" for s in steps), steps)


def templates_and_composer(dev, team):
    ts = ok(dev.get("/api/templates"))
    ts = ts if isinstance(ts, list) else ts.get("templates", [])
    check("templates: galeria", len(ts) > 0)
    plan = ok(dev.post("/api/compose/plan", json={"request": "an agent that answers renewal questions", "capabilities": []}))
    check("compositor: plano a partir do catálogo", isinstance(plan, dict) and plan, str(plan)[:200])
    if ts:
        t = next((x for x in ts if not x.get("harness")), ts[0])
        r = dev.post(f"/api/templates/{t['id']}/apply", json={"team": team})
        check("templates: aplicar cria os agentes em rascunho", r.status_code == 200, r.text[:300])
        if r.status_code == 200:
            body = r.json()
            for a in body.get("agents", []) if isinstance(body, dict) else []:
                s = a.get("slug") if isinstance(a, dict) else a
                if s:
                    wf.created["agents"].append(s)


def scim():
    key = ok(admin.post("/api/keys", json={"name": f"scim-{RUN}", "scopes": ["scim"]}))["key"]
    h = {"Authorization": f"Bearer {key}"}
    email = f"scim-{RUN}@example.com"
    r = httpx.post(f"{URL}/scim/v2/Users", headers=h, json={"userName": email, "externalId": f"ext-{RUN}", "active": True})
    check("SCIM: cria a pessoa", r.status_code == 201, r.text[:200])
    if r.status_code == 201:
        uid = r.json()["id"]
        check("SCIM: duplicado recusado", httpx.post(f"{URL}/scim/v2/Users", headers=h, json={"userName": email}).status_code == 409)
        p = httpx.patch(f"{URL}/scim/v2/Users/{uid}", headers=h, json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": [{"op": "replace", "value": {"active": False}}]})
        check("SCIM: desativa", p.status_code == 200 and p.json().get("active") is False, p.text[:200])
    check("SCIM: sem a chave, recusado", httpx.get(f"{URL}/scim/v2/Users", headers={"Authorization": "Bearer x"}).status_code == 401)


def offboard(dev, mgr, slug):
    r = ok(mgr.post(f"/api/employees/{slug}/status", json={"to": "offboarded", "reason": f"e2e {RUN}"}))
    check("desligamento: status", r["status"] == "offboarded", r.get("status"))
    a = mgr.post(f"/api/employees/{slug}/tasks", json={"title": "x", "body": "answer"})
    check("desligamento: não recebe tarefa", a.status_code in (400, 409), a.status_code)


def main():
    check("health", ok(admin.get("/api/health"))["status"] == "ok")
    team = ok(admin.post("/api/teams", json={"name": f"E2E cov {RUN}"}))["slug"]
    wf.created["team"] = team
    mgr, mgr_email, _ = wf.person("mgr", "maintainer", team)
    dev, _, _ = wf.person("dev", "developer", team)
    adm2, _ = admin_person("adm2")
    other = ok(admin.post("/api/teams", json={"name": f"E2E other {RUN}"}))["slug"]
    out, _, _ = wf.person("out", "developer", other)
    slug = wf.hire(dev, mgr_email, team, "Coverage Desk")
    wf.activate(dev, mgr, slug)
    section(lifecycle, dev, mgr, slug, out)
    section(routines, dev, mgr, slug)
    section(inbound_webhook, dev, mgr, slug)
    section(shadow_and_career, dev, mgr, slug)
    section(floor_two_approvals, dev, mgr, slug, adm2)
    section(budget_hard_stop, dev, mgr, team)
    section(governance, dev, mgr, mgr_email, team, slug, adm2)
    section(workspace_bits, dev, mgr, team, slug)
    section(access_and_versions, dev, mgr, team, out)
    section(templates_and_composer, dev, team)
    section(scim)
    section(offboard, dev, mgr, slug)
    print(f"\n{passed} checks ok, {len(failures)} falha(s)", flush=True)
    for f in failures:
        print(f"FAIL {f}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        wf.cleanup()
        for t in ok(admin.get("/api/teams")):  # o time de fora (o principal o cleanup já apaga)
            if isinstance(t, dict) and t.get("slug", "").startswith("e2e-other-"):
                admin.delete(f"/api/teams/{t['slug']}")
        sys.stdout.flush()
    sys.exit(code)
