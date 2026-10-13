"""hangar test-drive — o caminho inteiro em um comando, contra a sua instalação, sem LLM e sem custo.

Cria um time, contrata um Digital employee com o modelo de teste (mock/echo), roda a experiência, admite, entrega uma
tarefa e mostra o resultado e os links do portal. No fim apaga o que criou (com --keep, deixa tudo para você olhar).
"""
import time
import uuid

LINE = "─" * 60


class Drive:
    def __init__(self, api, say=print):
        self.api, self.say = api, say
        self.made: dict = {}

    def step(self, text: str):
        self.say(f"  ✓ {text}")

    def wait(self, fn, secs: int, what: str):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            got = fn()
            if got:
                return got
            time.sleep(2)
        raise TimeoutError(f"{what}: passou de {secs}s (o executor de tarefas está ligado? veja `hangar doctor`)")

    def run(self, manager: str | None = None, timeout: int = 300, llm: str = "") -> dict:
        a, n = self.api, uuid.uuid4().hex[:6]
        health = a.get("/health")
        self.say(f"Agent Hangar {health.get('version', '')} em {a.url}\n{LINE}")
        me = a.get("/me")
        email = manager or ((me.get("user") or {}).get("email"))
        if not email:  # token admin: uma pessoa de teste faz o papel de gestora
            email = f"test-drive-{n}@example.invalid"
            self.made["user"] = a.post("/users", {"email": email, "name": "Test drive", "org_role": "member"})["id"]
            self.step(f"pessoa de teste {email}")
        team = a.post("/teams", {"name": f"Test drive {n}"})["slug"]
        self.made["team"] = team
        a.post(f"/teams/{team}/members", {"email": email, "role": "maintainer"})
        self.step(f"time {team}, com {email} como gestora")
        job = {"name": f"Test drive {n}", "title": "Analista de teste", "mission": "Mostrar o caminho de ponta a ponta.",
               "responsibilities": ["entender o pedido", "fazer o trabalho", "entregar o resultado"], "manager": email,
               "team": team, "systems": ["nenhum"], "llm": {"connection": llm} if llm else {"model": "mock/echo"}, "channels": ["portal"],
               "accept_default_authority": True,
               "probation_tasks": [{"title": f"Experiência {i}", "body": f"responda {i}", "expected": f"responda {i}"}
                                   for i in range(3)]}
        slug = a.post("/employees", job)["employee"]["slug"]
        self.made["agent"] = slug
        self.step(f"Digital employee contratado: {slug} " + (f"(conexão {llm})" if llm else "(modelo mock/echo, sem custo)"))
        a.post(f"/employees/{slug}/probation")
        self.wait(lambda: (lambda e: e["probation"] and all(t["status"] == "done" for t in e["probation"]))(
            a.get(f"/employees/{slug}")), timeout, "experiência")
        self.step("experiência: 3 tarefas concluídas em stage")
        adm = self.wait(lambda: next((d for d in a.get("/decisions") if d["kind"] == "admission" and d["employee"] == slug), None),
                        60, "pedido de admissão")
        a.post(f"/decisions/{adm['id']}", {"decision": "approve"})
        promo = next((x for x in (a.get("/approvals").get("to_decide") or []) if x.get("kind") == "promotion"
                      and x.get("agent") == slug), None)
        if promo:  # o time exige quatro olhos para produção
            a.post(f"/promotions/{promo['id']}/approve", {})
        self.wait(lambda: a.get(f"/employees/{slug}")["status"] == "active", 60, "admissão")
        self.step("admitido: ativo em produção")
        tid = a.post(f"/employees/{slug}/tasks", {"title": "Resumo do trimestre",
                                                  "body": "Resuma em três linhas o que precisa de atenção neste trimestre."})["id"]
        t = self.wait(lambda: (lambda x: x if x["status"] in ("done", "failed") else None)(a.get(f"/tasks/{tid}")),
                      timeout, "tarefa")
        if t["status"] != "done":
            raise RuntimeError(f"a tarefa #{tid} falhou: {t.get('error')}")
        self.step(f"tarefa #{tid} concluída ({len(t.get('events') or [])} eventos na linha do tempo)")
        base = a.url
        self.say(f"{LINE}\nResultado: {(t.get('result') or '')[:300]}\n\nNo portal:\n"
                 f"  {base}/app/#/tasks/{tid}\n  {base}/app/#/employees/{slug}\n  {base}/app/#/live")
        return {"employee": slug, "team": team, "task": tid, "result": t.get("result")}

    def cleanup(self):
        a = self.api
        if self.made.get("agent"):
            a.delete(f"/agents/{self.made['agent']}")
        if self.made.get("team"):
            a.delete(f"/teams/{self.made['team']}")
        if self.made.get("user"):
            a.patch(f"/users/{self.made['user']}", {"active": False})
        self.made.clear()
