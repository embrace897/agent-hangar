"""hangar demo — uma empresa de exemplo para a instalação nova não abrir vazia.

Cria o time "Exemplo · Renovações" com você como gestora, uma meta, um projeto e três Digital employees em
organograma (a coordenadora e quem reporta a ela). Cada um faz a experiência e é admitido; a analista recebe uma
primeira tarefa. Sem LLM configurado, usam o modelo de teste (mock/echo, sem custo); com --llm, a conexão escolhida.
`hangar demo --remove` apaga tudo o que o exemplo criou. Rodar de novo não duplica nada.
"""
import time

TEAM = "Exemplo · Renovações"
GOAL = "Nenhuma renovação perdida (exemplo)"
PROJECT = "Renovações do trimestre (exemplo)"
PEOPLE = [
    {"key": "coord", "name": "Coordenadora de renovações (exemplo)", "title": "Coordenadora de renovações",
     "mission": "Garantir que nenhum contrato vença sem proposta, distribuindo o trabalho do time.",
     "responsibilities": ["priorizar os contratos que vencem", "distribuir o trabalho", "relatar o risco da carteira"]},
    {"key": "analyst", "name": "Analista de renovações (exemplo)", "title": "Analista de renovações", "chief": "coord",
     "mission": "Levantar os contratos que vencem e o histórico de cada cliente.",
     "responsibilities": ["listar os contratos que vencem", "resumir o histórico do cliente", "apontar riscos"]},
    {"key": "writer", "name": "Assistente de propostas (exemplo)", "title": "Assistente de propostas", "chief": "coord",
     "mission": "Redigir as propostas de renovação no tom da empresa.",
     "responsibilities": ["redigir a proposta", "ajustar o desconto à política", "preparar o e-mail ao cliente"]},
]
FIRST_TASK = {"title": "Contratos que vencem em 30 dias (exemplo)",
              "body": "Liste os contratos que vencem nos próximos 30 dias e diga quais estão em risco."}


class Demo:
    def __init__(self, api, say=print):
        self.api, self.say = api, say

    def _wait(self, fn, secs: int, what: str):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            got = fn()
            if got:
                return got
            time.sleep(2)
        raise TimeoutError(f"{what}: passou de {secs}s (o executor de tarefas está ligado? veja `hangar doctor`)")

    def _manager(self, manager: str | None) -> str:
        if manager:
            return manager
        me = self.api.get("/me")
        if (me.get("user") or {}).get("email"):
            return me["user"]["email"]
        admins = [u for u in self.api.get("/users") if u.get("org_role") == "admin" and u.get("active", True)]
        if not admins:
            raise RuntimeError("nenhum admin com conta: informe --manager <e-mail>")
        return sorted(admins, key=lambda u: u["id"])[0]["email"]

    def _team(self) -> dict | None:
        return next((t for t in self.api.get("/teams") if t.get("name") == TEAM), None)

    def _ours(self, team_slug: str) -> dict:
        """Os Digital employees do exemplo que já existem (por nome), para rodar de novo sem duplicar."""
        names = {p["name"]: p["key"] for p in PEOPLE}
        return {names[e["name"]]: e["slug"] for e in self.api.get("/employees")
                if e.get("name") in names and e.get("status") != "offboarded" and _team_of(e) in (team_slug, None)}

    def seed(self, manager: str | None = None, llm: str = "", timeout: int = 300) -> dict:
        a = self.api
        email = self._manager(manager)
        t = self._team()
        if t is None:
            t = a.post("/teams", {"name": TEAM, "description": "Criado por `hangar demo` — apague com `hangar demo --remove`.",
                                  "require_approval": False})
        team = t["slug"]
        a.post(f"/teams/{team}/members", {"email": email, "role": "maintainer"})
        self.say(f"  ✓ time {TEAM}, com {email} como gestora")
        goal = next((g for g in a.get("/goals") if g.get("title") == GOAL), None) or a.post("/goals", {"title": GOAL, "team": team})
        made = self._ours(team)
        model = {"connection": llm} if llm else {"model": "mock/echo"}
        for p in PEOPLE:
            if p["key"] in made:
                continue
            job = {"name": p["name"], "title": p["title"], "mission": p["mission"], "responsibilities": p["responsibilities"],
                   "manager": email, "team": team, "systems": ["CRM (exemplo)", "e-mail"], "llm": model,
                   "channels": ["portal", "mcp"], "accept_default_authority": True,
                   "probation_tasks": [{"title": f"Experiência {i}", "body": f"Explique em uma frase como você faria a parte {i} do seu trabalho.",
                                        "expected": "uma frase objetiva"} for i in (1, 2, 3)]}
            r = a.post("/employees", job)
            if not r.get("created"):
                raise RuntimeError(f"não consegui contratar {p['name']}: {r.get('missing') or r}")
            made[p["key"]] = r["employee"]["slug"]
            self.say(f"  ✓ contratado: {p['name']}")
        for p in PEOPLE:
            if p.get("chief"):
                a.patch(f"/employees/{made[p['key']]}", {"reports_to": made[p["chief"]]})
        self.say("  ✓ organograma: a analista e a assistente reportam à coordenadora")
        for p in PEOPLE:
            self._activate(made[p["key"]], timeout)
        self.say("  ✓ experiência feita e os três admitidos")
        proj = next((x for x in a.get("/projects") if x.get("name") == PROJECT), None)
        if proj is None:
            proj = a.post("/projects", {"name": PROJECT, "team": team, "goal_id": goal["id"], "default_employee": made["coord"],
                                        "instructions": "Priorize os contratos acima de 50 mil."})
        analyst = made["analyst"]
        tasks = a.get(f"/employees/{analyst}/tasks") if analyst else []
        if not any(x.get("title") == FIRST_TASK["title"] for x in tasks if isinstance(x, dict)):
            a.post(f"/employees/{analyst}/tasks", {**FIRST_TASK, "project": proj["slug"]})
        self.say("  ✓ meta, projeto e a primeira tarefa da analista")
        base = a.url
        self.say(f"\nAbra o portal:\n  {base}/app/#/          (Início)\n  {base}/app/#/org       (Organograma)\n"
                 f"  {base}/app/#/mine      (Meus agentes)\nPara apagar o exemplo:  hangar demo --remove")
        return {"team": team, "employees": made, "project": proj["slug"]}

    def _activate(self, slug: str, timeout: int):
        a = self.api
        e = a.get(f"/employees/{slug}")
        if e["status"] == "active":
            return
        if e["status"] == "onboarding":
            a.post(f"/employees/{slug}/probation")
        self._wait(lambda: (lambda x: x["probation"] and all(t["status"] == "done" for t in x["probation"]))(
            a.get(f"/employees/{slug}")), timeout, f"experiência de {slug}")
        adm = self._wait(lambda: next((d for d in a.get("/decisions") if d["kind"] == "admission" and d["employee"] == slug), None),
                         60, f"pedido de admissão de {slug}")
        a.post(f"/decisions/{adm['id']}", {"decision": "approve"})
        promo = next((x for x in (a.get("/approvals").get("to_decide") or []) if x.get("kind") == "promotion"
                      and x.get("agent") == slug), None)
        if promo:
            a.post(f"/promotions/{promo['id']}/approve", {})
        self._wait(lambda: a.get(f"/employees/{slug}")["status"] == "active", 60, f"admissão de {slug}")

    def remove(self) -> dict:
        a = self.api
        t = self._team()
        gone = {"employees": 0}
        if t is not None:
            for slug in self._ours(t["slug"]).values():
                a.delete(f"/agents/{slug}")
                gone["employees"] += 1
        for p in a.get("/projects"):
            if p.get("name") == PROJECT:  # projeto não se apaga: fica arquivado, fora das listas
                a.patch(f"/projects/{p['slug']}", {"status": "archived"})
                gone["project"] = p["slug"]
        for g in a.get("/goals"):
            if g.get("title") == GOAL:
                a.delete(f"/goals/{g['id']}")
                gone["goal"] = g["id"]
        if t is not None:
            a.delete(f"/teams/{t['slug']}")
            gone["team"] = t["slug"]
        return gone


def _team_of(e: dict):
    t = e.get("team")
    return t.get("slug") if isinstance(t, dict) else None
