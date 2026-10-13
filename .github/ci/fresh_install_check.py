"""Depois da instalação do zero (git clone + scripts/install.sh --answers): o que uma pessoa faz nos primeiros minutos.

    python fresh_install_check.py <pasta do clone> <usuário admin> [--demo]

Entra no portal com a senha do admin (como no navegador), abre o portal e o app instalável, confere o Início do
membro e, com --demo, a empresa de exemplo no organograma; usa o token pessoal que o setup gerou para falar com o
MCP da plataforma, como a ferramenta de IA da pessoa faria. Roda com o Python do CLI (.hangar/venv), que tem httpx.
"""
import json
import os
import sys
from pathlib import Path

import httpx

URL = os.environ.get("HANGAR_URL", "http://localhost:8090")
root, user = Path(sys.argv[1]), sys.argv[2]
demo = "--demo" in sys.argv
failed = []


def check(name: str, cond: bool, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {str(detail)[:400]}"), flush=True)
    if not cond:
        failed.append(name)


c = httpx.Client(base_url=URL, timeout=60, follow_redirects=True)
r = c.post("/api/auth/password", json={"username": user, "password": os.environ["ADMIN_PW"]})
check("login do admin com a senha da instalação", r.status_code == 200, r.text)
c.headers["X-CSRF-Token"] = c.cookies.get("hangar_csrf") or ""
me = c.get("/api/me").json()
check("sessão: é o admin", me.get("is_admin") is True, me)
page = c.get("/app/")
check("portal /app abre", page.status_code == 200 and "app.js" in page.text, page.status_code)
check("console /ui abre para o admin", c.get("/ui/").status_code == 200)
if "manifest.webmanifest" in page.text:  # 0.29+: o portal instala como app
    check("app instalável: manifesto e service worker",
          c.get("/app/manifest.webmanifest").status_code == 200 and c.get("/app/sw.js").status_code == 200)
ws = c.get("/api/me/workspace")
if ws.status_code != 404:
    check("Início do membro", ws.status_code == 200 and "agents" in ws.json(), ws.text)
if demo:
    nodes = {n["name"]: n for n in c.get("/api/org-chart").json()["nodes"]}
    ex = [n for name, n in nodes.items() if "(exemplo)" in name]
    check("exemplo: três Digital employees no organograma", len(ex) == 3, list(nodes))
    check("exemplo: dois reportam à coordenadora", sum(1 for n in ex if n.get("reports_to")) == 2, ex)
    check("exemplo: admitidos", all(n["status"] == "active" for n in ex), [(n["name"], n["status"]) for n in ex])
    mine = ws.json().get("agents", []) if ws.status_code == 200 else []
    check("exemplo: aparecem em Meus agentes do admin", sum(1 for a in mine if "(exemplo)" in a["name"]) == 3, mine)

token = (root / ".hangar" / "clients" / "token.txt").read_text(encoding="utf-8").strip()
check("token pessoal gerado pelo setup", token.startswith("ah_"))
h = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
init = httpx.post(URL + "/mcp", headers=h, timeout=60, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fresh-install", "version": "1"}}})
if init.headers.get("mcp-session-id"):
    h["mcp-session-id"] = init.headers["mcp-session-id"]
httpx.post(URL + "/mcp", headers=h, timeout=60, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
tl = httpx.post(URL + "/mcp", headers=h, timeout=60, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
body = tl.text
if body.startswith("event:") or "\ndata:" in body:  # resposta em SSE
    body = next(line[5:] for line in body.splitlines() if line.startswith("data:"))
tools = {t["name"] for t in json.loads(body).get("result", {}).get("tools", [])}
check("MCP da plataforma com o token pessoal (a IA da pessoa)", {"plan_employee", "hire_employee"} <= tools, sorted(tools)[:20])

print(f"\n{'FALHOU: ' + ', '.join(failed) if failed else 'instalação do zero: tudo certo'}")
sys.exit(1 if failed else 0)
