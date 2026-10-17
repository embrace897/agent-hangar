"""hangar — CLI do Agent Hangar (só stdlib + httpx + pyyaml)."""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import yaml

from . import __version__

CONFIG = Path(os.environ.get("HANGAR_CONFIG", Path.home() / ".config" / "hangar" / "config.json"))


class CliError(Exception):
    pass


# ------------------------------------------------------------------ config / http
def load_config() -> dict:
    cfg = {}
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = os.environ.get("HANGAR_URL") or cfg.get("url")
    token = os.environ.get("HANGAR_TOKEN") or cfg.get("token")
    if not url or not token:
        raise CliError("não autenticado: rode `hangar login <url> --token <chave>` ou defina HANGAR_URL/HANGAR_TOKEN")
    return {"url": url.rstrip("/"), "token": token}


class Api:
    def __init__(self, url: str, token: str, timeout: float = 900):
        self.url = url
        self.c = httpx.Client(base_url=url, timeout=timeout, headers={"Authorization": f"Bearer {token}"})

    def _check(self, r: httpx.Response):
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("detail") or body.get("error") or body
            except Exception:
                msg = r.text
            raise CliError(f"HTTP {r.status_code}: {msg}")
        return r.json() if r.content else None

    def get(self, path, **kw):
        return self._check(self.c.get("/api" + path, **kw))

    def post(self, path, body=None):
        return self._check(self.c.post("/api" + path, json=body or {}))

    def put(self, path, body):
        return self._check(self.c.put("/api" + path, json=body))

    def patch(self, path, body):
        return self._check(self.c.patch("/api" + path, json=body))

    def delete(self, path):
        return self._check(self.c.delete("/api" + path))

    def stream(self, path):
        with self.c.stream("GET", "/api" + path, timeout=None) as r:
            if r.status_code >= 400:
                raise CliError(f"HTTP {r.status_code}")
            event = None
            for line in r.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event:
                    yield event, json.loads(line[6:])
                    event = None


def api() -> Api:
    return Api(**load_config())


# ------------------------------------------------------------------ saída
def out(data, as_json: bool):
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return True
    return False


def table(rows: list[list], headers: list[str]):
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.upper().ljust(w) for h, w in zip(headers, widths, strict=True)))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)))


def usd(v):
    return f"${v:.4f}" if v else "-"


# ------------------------------------------------------------------ comandos
def cmd_login(a):
    url = a.url.rstrip("/")
    token = a.token or os.environ.get("HANGAR_TOKEN") or input("token: ").strip()
    info = Api(url, token).get("/health")
    Api(url, token).get("/overview")  # confere se a chave tem escopo admin
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"url": url, "token": token}), encoding="utf-8")
    try:
        CONFIG.chmod(0o600)
    except OSError:
        pass
    print(f"ok: {url} (Agent Hangar v{info['version']}) — credencial salva em {CONFIG}")


def cmd_agents_ls(a):
    rows = api().get("/agents")
    if out(rows, a.json):
        return
    if a.mine:
        rows = [x for x in rows if x.get("access") in ("admin", "maintainer", "developer", "consumer")]
    table([[x["slug"], (x.get("team") or {}).get("slug", ""), x.get("access") or "",
            x["kind"] + (f"/{x['harness']['id']}" if x.get("harness") else ""), x["status"],
            f"v{x['version']}", "✓" if x.get("stage") else "", "✓" if x.get("prod") else "",
            x["requests_7d"], usd(x.get("cost_7d")), x["model"]] for x in rows],
          ["slug", "team", "access", "kind", "status", "ver", "stage", "prod", "req7d", "cost7d", "model"])


def cmd_agents_get(a):
    d = api().get(f"/agents/{a.slug}")
    if a.spec:
        print(yaml.safe_dump(d["spec"], sort_keys=False, allow_unicode=True) if not a.json
              else json.dumps(d["spec"], indent=2, ensure_ascii=False))
        return
    if out(d, a.json):
        return
    print(f"{d['name']} ({d['slug']}) — {d['status']} v{d['version']} [{d['kind']}]")
    print(f"objetivo: {d['objective']}\nsaída:    {d['final_output']}\nmodelo:   {d['model']}")
    for k, v in d["endpoints"].items():
        print(f"  {k:<11} {v}")
    if d.get("last_test"):
        print(f"último teste: {d['last_test']['status']} — {d['last_test']['summary']}")


def cmd_apply(a):
    text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
    docs = [d for d in yaml.safe_load_all(text) if d]
    c = api()
    for doc in docs:
        if "document" in doc and "id" in doc:  # arquivo de template
            doc = doc["document"]
        for r in c.post("/apply", doc):
            name = r.get("slug") or r.get("name")
            ver = f" v{r['version']}" if "version" in r else ""
            print(f"{r['kind']:<11} {name:<30} {r['action']}{ver}")
    if a.ship:
        for doc in docs:
            doc = doc.get("document", doc)
            agents = doc.get("agents") or []
            if agents:
                last = agents[-1].get("slug") or agents[-1]["name"]
                cmd_ship(argparse.Namespace(slug=last, json=False))


def cmd_export(a):
    d = api().get(f"/agents/{a.slug}")
    doc = {"agents": [{"name": d["name"], "slug": d["slug"], "objective": d["objective"],
                       "final_output": d["final_output"], **({"owner": d["owner"]} if d.get("owner") else {}),
                       "spec": d["spec"]}]}
    print(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), end="")


def cmd_ship(a):
    steps = api().post(f"/agents/{a.slug}/ship")
    if out(steps, a.json):
        return
    for s in steps:
        print(f"{s['agent']:<30} {s['step']:<6} {s['status']}  {s.get('summary') or s.get('url') or ''}")
    if steps and steps[-1].get("status") == "approval_pending":
        print(f"\ntestes ok — pedido #{steps[-1]['request']} aguarda um mantenedor do time: hangar approvals ls")


def cmd_test(a):
    r = api().post(f"/agents/{a.slug}/test")
    if out(r, a.json):
        return
    print(f"{r['status']} — {r['summary']} ({r['duration_ms']} ms)")
    for x in r["results"]:
        print(f"  {'✓' if x['passed'] else '✗'} {x['name']}: {x['detail'][:110]}")
    if r["status"] != "passed":
        sys.exit(1)


def cmd_deploy(a):
    r = api().post(f"/agents/{a.slug}/deploy", {"env": a.env})
    if r["status"] == "approval_pending":
        print(f"{a.slug} v{r['version']}: pedido de promoção #{r['request']['id']} aguarda aprovação de um mantenedor")
        return
    print(f"{a.slug} v{r['version']} -> {a.env}: {r['status']} {r['url']}")


def cmd_rollback(a):
    r = api().post(f"/agents/{a.slug}/rollback", {"version": a.version})
    print(f"{a.slug}: spec da v{a.version} restaurada como v{r['version']} — rode `hangar ship {a.slug}`")


def cmd_chat(a):
    r = api().post(f"/agents/{a.slug}/chat", {"message": " ".join(a.message), "env": a.env})
    if out(r, a.json):
        return
    print(r["reply"])


def cmd_logs(a):
    print(api().get(f"/agents/{a.slug}/logs", params={"env": a.env})["logs"])


def _print_job(j):
    print(f"job #{j['id']} {j['status']} — {j['harness_id']} · {j['duration_ms']} ms · "
          f"{(j.get('tokens_in') or 0) + (j.get('tokens_out') or 0)} tokens · {usd(j.get('cost_usd'))}")
    print(j.get("result") or "")
    if j.get("diff"):
        print("\n--- diff ---\n" + j["diff"])


def cmd_jobs_run(a):
    c = api()
    task = " ".join(a.task)
    if not a.follow:
        j = c.post(f"/agents/{a.slug}/job", {"task": task, "env": a.env, "timeout_s": a.timeout, "wait": True})
        return out(j, a.json) or _print_job(j)
    j = c.post(f"/agents/{a.slug}/job", {"task": task, "env": a.env, "timeout_s": a.timeout, "wait": False})
    print(f"job #{j['id']} enfileirado", file=sys.stderr)
    try:
        for ev, data in c.stream(f"/jobs/{j['id']}/events"):
            if ev == "status":
                print(f"[{data['status']}]", file=sys.stderr)
            elif ev == "log":
                sys.stderr.write(data["text"])
            elif ev == "done":
                return out(data, a.json) or _print_job(data)
    except KeyboardInterrupt:
        c.post(f"/jobs/{j['id']}/cancel")
        print(f"\njob #{j['id']} cancelado", file=sys.stderr)
        sys.exit(130)


def cmd_jobs_get(a):
    j = api().get(f"/jobs/{a.id}")
    out(j, a.json) or _print_job(j)


def cmd_jobs_cancel(a):
    print(api().post(f"/jobs/{a.id}/cancel")["status"])


def cmd_templates_ls(a):
    rows = api().get("/templates")
    if out(rows, a.json):
        return
    table([[t["id"], t["title"], t.get("harness") or ("multi" if len(t["agents"]) > 1 else "chat"),
            ", ".join(t["tags"])] for t in rows], ["id", "title", "type", "tags"])


def cmd_templates_apply(a):
    r = api().post(f"/templates/{a.id}/apply", {"connection": a.connection or "",
                                                "harness_connection": a.harness_connection or "", "team": a.team})
    for x in r:
        print(f"{x['kind']:<11} {x.get('slug') or x.get('name'):<30} {x['action']}")


def cmd_keys_ls(a):
    rows = api().get("/keys")
    if out(rows, a.json):
        return
    table([[k["id"], k["name"], k["prefix"] + "…", ",".join(k["scopes"]), ",".join(k["agents"]) or "*",
            k["last_used_at"] or "never", "revoked" if k["revoked"] else "active"] for k in rows],
          ["id", "name", "prefix", "scopes", "agents", "last used", "state"])


def cmd_keys_create(a):
    r = api().post("/keys", {"name": a.name, "scopes": [a.scope], "agents": a.agent or []})
    if out(r, a.json):
        return
    print(r["key"])
    print(f"({a.scope}; guarde agora — a chave não será exibida de novo)", file=sys.stderr)


def cmd_keys_revoke(a):
    api().delete(f"/keys/{a.id}")
    print(f"chave {a.id} revogada")


def cmd_connect(a):
    c = api()
    if a.list:
        rows = c.get(f"/agents/{a.slug}/connections")
        if out(rows, a.json):
            return
        table([[k["id"], k["client_label"], k["mode"], k["prefix"] + "…", k["last_used_at"] or "nunca"] for k in rows],
              ["id", "ferramenta", "modo", "chave", "último uso"])
        return
    if not a.client:
        rows = c.get("/connect/clients")
        if out(rows, a.json):
            return
        table([[x["id"], x["label"], "/".join(x["modes"]), x["note"]] for x in rows], ["cliente", "nome", "modos", "nota"])
        return
    if a.preview:
        r = c.get(f"/agents/{a.slug}/connections/snippet", params={"client": a.client, "mode": a.mode})
    else:
        r = c.post(f"/agents/{a.slug}/connections", {"client": a.client, "mode": a.mode})
    if out(r, a.json):
        return
    print(f"# {r.get('file') or 'configuração'}", file=sys.stderr)
    for step in r["steps"]:
        print(f"#  - {step}", file=sys.stderr)
    print(r["content"])
    if not a.preview:
        print(f"# conexão #{r['connection']['id']} criada — revogue com: hangar keys revoke {r['connection']['id']}",
              file=sys.stderr)


def cmd_whoami(a):
    me = api().get("/me")
    if out(me, a.json):
        return
    role = "admin" if me["is_admin"] else "auditor" if me["is_auditor"] else "membro"
    who = me["user"]["email"] if me["user"] else me["name"]
    print(f"{who} — {role} em {me['org']['name']}")
    for t in me["teams"]:
        print(f"  {t['slug']:<24} {t['role']:<11} ({t['source']})")
    if me["pending"]:
        print(f"{me['pending']} pedido(s) aguardando você: hangar approvals ls")


def cmd_teams_ls(a):
    rows = api().get("/teams")
    if out(rows, a.json):
        return
    table([[t["slug"], t["name"], t["my_role"] or "", t["members"], t["agents"], "sim" if t["require_approval"] else "não",
            f"${t['spent_month']:.2f}" + (f" / ${t['budget_usd_month']:.2f}" if t["budget_usd_month"] else "")]
           for t in rows], ["slug", "nome", "seu papel", "membros", "agentes", "aprovação", "gasto no mês"])


def cmd_teams_add(a):
    r = api().post(f"/teams/{a.team}/members", {"email": a.email, "role": a.role})
    print(f"{r['email']} agora é {r['role']} em {a.team}")


def cmd_approvals_ls(a):
    r = api().get("/approvals")
    if out(r, a.json):
        return
    if not r["to_decide"]:
        print("nada para você decidir")
    for x in r["to_decide"]:
        if x["kind"] == "promotion":
            print(f"promotion #{x['id']:<5} {x['agent']} v{x['version']} — pedido por {x['requested_by']}"
                  + (" (desatualizado)" if x["stale"] else ""))
        else:
            print(f"access    #{x['id']:<5} {x['user']} quer usar {x['agent']}: {x['reason']}")


def cmd_approvals_decide(a):
    kind = "promotions" if a.kind == "promotion" else "access-requests"
    r = api().post(f"/{kind}/{a.id}/{a.decision}", {"note": a.note or ""} if a.kind == "promotion" else None)
    print(f"{a.kind} #{a.id}: {r['status']}")


def cmd_request_access(a):
    r = api().post(f"/agents/{a.slug}/access-requests", {"reason": " ".join(a.reason or [])})
    print(f"pedido #{r['id']} enviado ao time dono de {a.slug} ({r['status']})")


def cmd_schedules_ls(a):
    rows = api().get(f"/agents/{a.slug}/schedules" if a.slug else "/schedules")
    if out(rows, a.json):
        return
    table([[s["id"], s["agent"], s["when"], s["timezone"], s["next_run_local"] or "—", s["last_status"] or "—",
            "sim" if s["enabled"] else "pausado"] for s in rows],
          ["id", "agente", "quando", "fuso", "próximo", "último", "ativo"])


def cmd_schedules_add(a):
    body = {"message": " ".join(a.message), "cron": a.cron, "run_at": a.at, "timezone": a.tz, "name": a.name or "",
            "notify_url": a.notify or ""}
    s = api().post(f"/agents/{a.slug}/schedules", body)
    if out(s, a.json):
        return
    print(f"agendamento #{s['id']}: {s['when']} ({s['timezone']}) — próximo: {s['next_run_local'] or '—'}")


def cmd_schedules_rm(a):
    api().delete(f"/schedules/{a.id}")
    print(f"agendamento {a.id} removido")


def cmd_schedules_run(a):
    r = api().post(f"/schedules/{a.id}/run")
    if out(r, a.json):
        return
    print(f"{r['status']}: {(r['output'] or r['error'])[:2000]}")


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hangar", description="Agent Hangar CLI")
    p.add_argument("--version", action="version", version=f"hangar {__version__}")
    p.add_argument("--json", action="store_true", help="saída em JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(parent, name, fn, help_):
        sp = parent.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="saída em JSON")
        sp.set_defaults(fn=fn)
        return sp

    s = add(sub, "login", cmd_login, "salva URL e credencial")
    s.add_argument("url")
    s.add_argument("--token")

    ag = sub.add_parser("agents", help="listar/inspecionar agentes").add_subparsers(dest="sub", required=True)
    s = add(ag, "ls", cmd_agents_ls, "lista agentes (os seus e os do catálogo da empresa)")
    s.add_argument("--mine", action="store_true", help="só os dos seus times")
    s = add(ag, "get", cmd_agents_get, "detalhe de um agente")
    s.add_argument("slug")
    s.add_argument("--spec", action="store_true", help="só a spec (YAML)")

    s = add(sub, "apply", cmd_apply, "aplica um arquivo YAML {skills, mcp_servers, agents} (GitOps)")
    s.add_argument("-f", "--file", required=True, help="arquivo YAML (ou - para stdin)")
    s.add_argument("--ship", action="store_true", help="shipar o último agente de cada documento")
    s = add(sub, "export", cmd_export, "exporta um agente como YAML aplicável")
    s.add_argument("slug")

    for name, fn, help_ in (("ship", cmd_ship, "testa em stage e promove se passar"),
                            ("test", cmd_test, "roda os testes em stage")):
        s = add(sub, name, fn, help_)
        s.add_argument("slug")
    s = add(sub, "deploy", cmd_deploy, "deploy num ambiente")
    s.add_argument("slug")
    s.add_argument("--env", default="stage", choices=["stage", "prod"])
    s = add(sub, "rollback", cmd_rollback, "restaura a spec de uma versão anterior")
    s.add_argument("slug")
    s.add_argument("version", type=int)
    s = add(sub, "chat", cmd_chat, "manda uma mensagem a um agente")
    s.add_argument("slug")
    s.add_argument("message", nargs="+")
    s.add_argument("--env", default="prod", choices=["stage", "prod"])
    s = add(sub, "logs", cmd_logs, "logs do container do agente")
    s.add_argument("slug")
    s.add_argument("--env", default="prod", choices=["stage", "prod"])

    jb = sub.add_parser("jobs", help="jobs de harness").add_subparsers(dest="sub", required=True)
    s = add(jb, "run", cmd_jobs_run, "roda uma tarefa num agente com harness")
    s.add_argument("slug")
    s.add_argument("task", nargs="+")
    s.add_argument("--env", default="stage", choices=["stage", "prod"])
    s.add_argument("--timeout", type=int, default=None)
    s.add_argument("-f", "--follow", action="store_true", help="acompanha status e logs ao vivo (Ctrl+C cancela)")
    s = add(jb, "get", cmd_jobs_get, "estado de um job")
    s.add_argument("id", type=int)
    s = add(jb, "cancel", cmd_jobs_cancel, "cancela um job")
    s.add_argument("id", type=int)

    tp = sub.add_parser("templates", help="galeria de templates").add_subparsers(dest="sub", required=True)
    add(tp, "ls", cmd_templates_ls, "lista templates")
    s = add(tp, "apply", cmd_templates_apply, "aplica um template")
    s.add_argument("id")
    s.add_argument("--connection", help="conexão (protocolo openai) dos agentes de chat")
    s.add_argument("--harness-connection", help="conexão dos agentes com harness")
    s.add_argument("--team", help="time que recebe os agentes (opcional se você só está em um)")

    s = add(sub, "connect", cmd_connect, "conecta um agente a uma ferramenta (MCP ou modelo), plug and play")
    s.add_argument("slug")
    s.add_argument("client", nargs="?", help="claude-code, codex, opencode, librechat, open-webui… (vazio = lista)")
    s.add_argument("--mode", default="mcp", choices=["mcp", "model", "agui"])
    s.add_argument("--preview", action="store_true", help="só mostra o trecho, sem gerar chave")
    s.add_argument("--list", action="store_true", help="lista as conexões ativas do agente")

    ks = sub.add_parser("keys", help="chaves de API").add_subparsers(dest="sub", required=True)
    add(ks, "ls", cmd_keys_ls, "lista chaves")
    s = add(ks, "create", cmd_keys_create, "cria uma chave (o valor sai no stdout)")
    s.add_argument("name")
    s.add_argument("--scope", default="invoke", choices=["invoke", "user", "admin", "scim"],
                   help="user = token pessoal (age como você: CLI, MCP)")
    s.add_argument("--agent", action="append", help="restringe a chave invoke a este agente (repetível)")
    s = add(ks, "revoke", cmd_keys_revoke, "revoga uma chave")
    s.add_argument("id", type=int)

    add(sub, "whoami", cmd_whoami, "quem sou eu: papel na empresa e times")
    tm = sub.add_parser("teams", help="times").add_subparsers(dest="sub", required=True)
    add(tm, "ls", cmd_teams_ls, "lista os times")
    s = add(tm, "add", cmd_teams_add, "adiciona (ou muda o papel de) alguém no time")
    s.add_argument("team")
    s.add_argument("email")
    s.add_argument("--role", default="consumer", choices=["maintainer", "developer", "consumer"])
    ap = sub.add_parser("approvals", help="aprovações de produção e pedidos de acesso").add_subparsers(dest="sub",
                                                                                                     required=True)
    add(ap, "ls", cmd_approvals_ls, "o que está aguardando você")
    for decision in ("approve", "reject"):
        s = add(ap, decision, cmd_approvals_decide, f"{decision} um pedido")
        s.add_argument("kind", choices=["promotion", "access"])
        s.add_argument("id", type=int)
        s.add_argument("--note")
        s.set_defaults(decision=decision)
    sc = sub.add_parser("schedules", help="agendamentos (o agente roda sozinho em produção)").add_subparsers(
        dest="sub", required=True)
    s = add(sc, "ls", cmd_schedules_ls, "lista agendamentos (de um agente ou todos)")
    s.add_argument("slug", nargs="?")
    s = add(sc, "add", cmd_schedules_add, "agenda: --cron '0 9 * * 1-5' ou --at 2026-10-05T09:00")
    s.add_argument("slug")
    s.add_argument("message", nargs="+", help="o que o agente recebe a cada disparo")
    s.add_argument("--cron")
    s.add_argument("--at", help="uma vez: data/hora ISO")
    s.add_argument("--tz", help="fuso (padrão: o da empresa)")
    s.add_argument("--name")
    s.add_argument("--notify", help="webhook (Slack/Teams/HTTP) que recebe o resultado")
    for name, fn, help_ in (("rm", cmd_schedules_rm, "remove"), ("run", cmd_schedules_run, "roda agora")):
        s = add(sc, name, fn, help_)
        s.add_argument("id", type=int)
    s = add(sub, "request-access", cmd_request_access, "pede acesso a um agente de outro time")
    s.add_argument("slug")
    s.add_argument("reason", nargs="*")

    s = add(sub, "setup", cmd_setup, "instala e configura o Agent Hangar com Docker (guiado ou --answers arquivo.yaml)")
    s.add_argument("--dir", default=".", help="pasta do repositório (onde está o docker-compose.yml)")
    s.add_argument("--answers", help="arquivo YAML de respostas (instalação sem perguntas; ver setup.example.yaml)")
    s.add_argument("--no-start", action="store_true", help="só gera o .env, sem subir a stack")
    s.add_argument("--yes", action="store_true", help="não pergunta antes de subir")
    s.add_argument("--target", choices=["docker", "kubernetes"], help="onde instalar (padrão: pergunta)")
    s.add_argument("--configure-only", action="store_true",
                   help="Kubernetes já instalado (Terraform, GitOps): só cadastra LLM, admin e ferramentas, sem helm")
    s = add(sub, "doctor", cmd_doctor, "diagnóstico da instalação (Docker, central, LLM, memória, túnel, SSO, MCP)")
    s.add_argument("--dir", default=".", help="pasta do repositório")
    s.add_argument("--namespace", help="Kubernetes: namespace da instalação (usa kubectl e um port-forward)")
    s.add_argument("--release", default="agent-hangar", help="Kubernetes: nome do release Helm")
    s = add(sub, "cleanup", cmd_cleanup, "apaga os volumes de dados de agentes já excluídos (lista e confirma antes)")
    s.add_argument("--dir", default=".", help="pasta do repositório")
    s.add_argument("--yes", action="store_true", help="não perguntar")
    s = add(sub, "backup", cmd_backup, "backup do banco (pg_dump) em .hangar/backups/")
    s.add_argument("--dir", default=".", help="pasta do repositório")
    s.add_argument("--out", help="pasta de destino (padrão: .hangar/backups)")
    s.add_argument("--namespace", help="Kubernetes: namespace (Postgres do chart)")
    s.add_argument("--release", default="agent-hangar")
    s = add(sub, "restore", cmd_restore, "restaura um backup (faz antes um backup do estado atual)")
    s.add_argument("file", help="arquivo .dump")
    s.add_argument("--dir", default=".")
    s.add_argument("--namespace")
    s.add_argument("--release", default="agent-hangar")
    s.add_argument("--yes", action="store_true", help="não perguntar")
    s = add(sub, "test-drive", cmd_test_drive, "o caminho inteiro (time, Digital employee, experiência, tarefa) contra "
                                                "a sua instalação, com o modelo de teste")
    s.add_argument("--manager", help="e-mail da gestora (padrão: você; com o token admin, uma pessoa de teste)")
    s.add_argument("--keep", action="store_true", help="não apagar o que foi criado")
    s.add_argument("--timeout", type=int, default=300, help="segundos de espera por etapa")
    s.add_argument("--llm", default="", help="conexão de LLM do catálogo (padrão: o modelo de teste mock/echo)")
    s = add(sub, "demo", cmd_demo, "cria uma empresa de exemplo (time, meta, projeto e três Digital employees em "
                                   "organograma) para o portal não abrir vazio; --remove apaga")
    s.add_argument("--manager", help="e-mail da gestora (padrão: você; com o token admin, o primeiro admin)")
    s.add_argument("--llm", default="", help="conexão de LLM do catálogo (padrão: o modelo de teste mock/echo)")
    s.add_argument("--timeout", type=int, default=300, help="segundos de espera por etapa")
    s.add_argument("--remove", action="store_true", help="apaga o exemplo")
    s = add(sub, "upgrade", cmd_upgrade, "atualiza a instalação Docker: backup, nova versão, sobe e confere")
    s.add_argument("--version", help="versão das imagens publicadas (ex.: 0.12.0)")
    s.add_argument("--dir", default=".")
    s.add_argument("--namespace", help="Kubernetes: mostra o passo a passo do upgrade com Helm")
    s.add_argument("--yes", action="store_true")
    return p


def cmd_demo(a):
    from .demo import Demo
    d = Demo(api())
    try:
        if a.remove:
            print(f"exemplo apagado: {d.remove()}")
        else:
            res = d.seed(a.manager, a.llm, a.timeout)
            if getattr(a, "json", False):
                print(json.dumps(res, indent=2, ensure_ascii=False))
    except (TimeoutError, RuntimeError) as e:
        raise CliError(str(e)) from None


def cmd_test_drive(a):
    from .testdrive import Drive
    d = Drive(api())
    try:
        res = d.run(a.manager, a.timeout, a.llm)
        if getattr(a, "json", False):
            print(json.dumps(res, indent=2, ensure_ascii=False))
    except (TimeoutError, RuntimeError) as e:
        raise CliError(str(e)) from None
    finally:
        if a.keep and d.made:
            print(f"mantido (--keep): {d.made}")
        elif d.made:
            d.cleanup()
            print("limpo: o que o test-drive criou foi apagado (use --keep para olhar no portal)")


def cmd_setup(a):
    from .setup import SetupError, setup
    try:
        sys.exit(setup(Path(a.dir), a.answers, start=not a.no_start, assume_yes=a.yes, target=a.target,
                       configure_only=a.configure_only))
    except SetupError as e:
        raise CliError(str(e)) from None
    except KeyboardInterrupt:
        print("\ninterrompido — rode  hangar setup  de novo quando quiser (as respostas ficam salvas)")
        sys.exit(130)


def cmd_cleanup(a):
    from .doctor import cleanup
    sys.exit(cleanup(Path(a.dir), a.yes))


def cmd_doctor(a):
    from .doctor import doctor
    sys.exit(doctor(Path(a.dir), as_json=getattr(a, "json", False), namespace=a.namespace, release=a.release))


def cmd_backup(a):
    from .backup import backup
    from .setup import SetupError
    try:
        backup(Path(a.dir), a.out, a.namespace, a.release)
    except SetupError as e:
        raise CliError(str(e)) from None


def cmd_restore(a):
    from .backup import restore
    from .setup import SetupError
    try:
        sys.exit(restore(Path(a.dir), a.file, a.namespace, a.release, a.yes))
    except SetupError as e:
        raise CliError(str(e)) from None


def cmd_upgrade(a):
    from .backup import upgrade
    from .setup import SetupError
    try:
        sys.exit(upgrade(Path(a.dir), a.version, a.yes, a.namespace))
    except SetupError as e:
        raise CliError(str(e)) from None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows com saída redirecionada usa cp1252 por padrão
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except CliError as e:
        print(f"erro: {e}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError as e:
        print(f"erro de conexão: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
