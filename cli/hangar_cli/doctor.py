"""`hangar doctor`: diagnóstico da instalação, com a dica de correção de cada problema.

Confere Docker e containers, a central, o acesso de admin, cada conexão de LLM (chamada real e mínima), a memória, a URL
pública (túnel/ingress) e o certificado TLS, o SSO, o MCP da plataforma, a extensão do VS Code e os agentes no ar.
Sai com código 1 se algo essencial falhar — dá para usar em CI ou num cron de monitoramento.
"""
import json
import re
import socket
import ssl
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .setup import _c, read_env

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


def check_docker(root: Path) -> list[Check]:
    if subprocess.run(["docker", "info"], capture_output=True, text=True).returncode != 0:
        return [Check("Docker", FAIL, "o daemon não responde", "abra o Docker Desktop ou rode  sudo systemctl start docker")]
    out = [Check("Docker", OK, "daemon respondendo")]
    r = subprocess.run(["docker", "compose", "ps", "--all", "--format", "json"], cwd=root, capture_output=True, text=True)
    rows = []
    for line in r.stdout.splitlines():  # o Compose recente imprime um JSON por linha
        line = line.strip()
        if line.startswith("["):
            rows += json.loads(line)
        elif line.startswith("{"):
            rows.append(json.loads(line))
    jobs = ("agent-runtime", "harness-")  # serviços que só constroem/rodam e saem
    bad = [x for x in rows if not str(x.get("Service", "")).startswith(jobs)
           and (x.get("State") != "running" or x.get("Health") == "unhealthy")]
    if not rows:
        out.append(Check("Containers", FAIL, "nenhum container da stack", "rode  docker compose up -d  (ou  hangar setup)"))
    elif bad:
        names = ", ".join(f"{x.get('Service')} ({x.get('State')}{'/' + x['Health'] if x.get('Health') else ''})" for x in bad)
        out.append(Check("Containers", FAIL, names, "veja  docker compose logs <serviço>  e  docker compose up -d"))
    else:
        out.append(Check("Containers", OK, f"{len([x for x in rows if x.get('State') == 'running'])} rodando"))
    return out


def check_central(http: httpx.Client, base: str, token: str) -> tuple[list[Check], bool]:
    try:
        h = http.get(f"{base}/api/health", timeout=5).json()
    except (httpx.HTTPError, ValueError):
        return [Check("Central", FAIL, f"{base} não responde", "veja  docker compose logs central")], False
    out = [Check("Central", OK, f"versão {h.get('version')} em {base}")]
    if not token:
        out.append(Check("Admin", FAIL, "ADMIN_TOKEN vazio no .env", "rode  hangar setup"))
        return out, False
    r = http.get(f"{base}/api/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if r.status_code != 200 or not r.json().get("is_admin"):
        out.append(Check("Admin", FAIL, f"ADMIN_TOKEN recusado (HTTP {r.status_code})", "confira o .env e reinicie a central"))
        return out, False
    out.append(Check("Admin", OK, "ADMIN_TOKEN aceito"))
    return out, True


def check_llm(http: httpx.Client, base: str, hdr: dict) -> list[Check]:
    conns = http.get(f"{base}/api/catalog", headers=hdr, timeout=10).json().get("llm_connections", [])
    if not conns:
        return [Check("LLM", WARN, "nenhuma conexão cadastrada (agentes rodam em mock)",
                      "cadastre em Catálogo → Conexões de LLM ou rode  hangar setup")]
    out = []
    for c in conns:
        try:
            t = http.post(f"{base}/api/catalog/llm/{c['name']}/test", headers=hdr, timeout=60).json()
        except (httpx.HTTPError, ValueError) as e:
            t = {"ok": False, "detail": f"teste falhou: {type(e).__name__}"}
        hint = "" if t.get("ok") else "confira URL, chave e modelo em Catálogo → Conexões de LLM (Editar)"
        out.append(Check(f"LLM {c['name']}", OK if t.get("ok") else FAIL, t.get("detail", ""), hint))
    return out


def check_memory(http: httpx.Client, base: str, hdr: dict, env: dict) -> list[Check]:
    if not env.get("MEMORY_URL"):
        return [Check("Memória", OK, "desligada")]
    s = http.get(f"{base}/api/memory/status", headers=hdr, timeout=60).json()
    if not s.get("ready"):
        return [Check("Memória", FAIL, s.get("error") or "serviço indisponível",
                      "confira o profile memory (docker compose ps memory) e MEMORY_LLM_CONNECTION no .env")]
    detail = f"{s.get('backend')} · LLM {s.get('llm_model')} · {s.get('processed', 0)} episódio(s)"
    if s.get("failed"):
        return [Check("Memória", WARN, detail + f" · {s['failed']} falha(s): {s.get('last_error', '')[:120]}",
                      "use um modelo bom em JSON ou MEMORY_LLM_OUTPUT_MODE=json_schema")]
    return [Check("Memória", OK, detail)]


def tls_days_left(host: str, port: int = 443) -> int:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=8) as sock, ctx.wrap_socket(sock, server_hostname=host) as s:
        exp = datetime.strptime(s.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    return (exp - datetime.now(UTC)).days


def check_public(http: httpx.Client, public: str, local_version: str) -> list[Check]:
    if not public or urlsplit(public).hostname in ("localhost", "127.0.0.1"):
        return [Check("Endereço público", OK, f"{public or 'localhost'} (só local)")]
    try:
        r = http.get(f"{public}/api/health", timeout=15)
        v = r.json().get("version")
    except (httpx.HTTPError, ValueError) as e:
        return [Check("Endereço público", FAIL, f"{public} não responde ({type(e).__name__})",
                      "confira o túnel:  docker compose logs cloudflared|ngrok|tailscale  e o hostname/domínio")]
    out = [Check("Endereço público", OK if v == local_version else WARN, f"{public} → versão {v}",
                 "" if v == local_version else "o endereço público aponta para outra instalação")]
    if public.startswith("https://"):
        try:
            days = tls_days_left(urlsplit(public).hostname)
            out.append(Check("TLS", OK if days > 14 else WARN, f"certificado válido por {days} dia(s)",
                             "" if days > 14 else "renove o certificado"))
        except (OSError, ssl.SSLError) as e:
            out.append(Check("TLS", FAIL, f"certificado inválido: {e}", "confira o certificado do domínio"))
    else:
        out.append(Check("TLS", WARN, "endereço público sem HTTPS", "use um túnel (Cloudflare/ngrok/Tailscale) ou ingress com TLS"))
    return out


def check_sso(http: httpx.Client, base: str) -> list[Check]:
    p = http.get(f"{base}/api/auth/providers", timeout=10).json()
    names = [x.get("label") for x in p.get("providers", [])]
    login = names + (["usuário e senha"] if p.get("password_login") else [])
    if not login:
        return [Check("Login", WARN, "nenhum login além do token de emergência", "crie um admin local (hangar setup) ou configure SSO")]
    return [Check("Login", OK, ", ".join(login))]


def check_mcp(http: httpx.Client, base: str, hdr: dict) -> list[Check]:
    r = http.post(f"{base}/mcp", headers={**hdr, "Accept": "application/json, text/event-stream"}, timeout=15,
                  json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                      "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "hangar-doctor", "version": "1"}}})
    if r.status_code != 200:
        return [Check("MCP da plataforma", FAIL, f"HTTP {r.status_code}", "veja  docker compose logs central")]
    return [Check("MCP da plataforma", OK, f"{base}/mcp responde")]


def check_extension(http: httpx.Client, base: str) -> list[Check]:
    r = http.head(f"{base}/downloads/agent-hangar-vscode.vsix", timeout=10)
    if r.status_code != 200:
        return [Check("Extensão VS Code", WARN, "não incluída nesta imagem da central", "use a imagem publicada ou rebuild da central")]
    return [Check("Extensão VS Code", OK, f"{base}/downloads/agent-hangar-vscode.vsix")]


def check_agents(http: httpx.Client, base: str, hdr: dict, docker: bool = True) -> list[Check]:
    o = http.get(f"{base}/api/overview", headers=hdr, timeout=15).json()
    out = [Check("Agentes", OK, f"{o.get('agents_total', 0)} registrado(s) · {o.get('running_prod', 0)} em produção · "
                                f"{o.get('running_stage', 0)} em stage")]
    orphans = orphan_volumes(http, base, hdr) if docker else []  # volumes só existem na instalação Docker
    if orphans:
        out.append(Check("Volumes órfãos", WARN, f"{len(orphans)} volume(s) de dados de agentes que não existem mais",
                         "hangar cleanup  (lista e apaga só esses, depois de confirmar)"))
    return out


VOLUME = re.compile(r"agent-(.+)-(stage|prod)-data")


def orphan_volumes(http: httpx.Client, base: str, hdr: dict) -> list[str]:
    """Volumes de dados (agent-<slug>-<env>-data) sem container e cujo agente foi excluído."""
    try:
        r = subprocess.run(["docker", "volume", "ls", "-q", "--filter", "dangling=true"], capture_output=True, text=True)
    except OSError:  # sem Docker nesta máquina (Kubernetes, ou o doctor rodando de fora)
        return []
    if r.returncode != 0:
        return []
    try:
        r2 = http.get(f"{base}/api/agents", headers=hdr, timeout=15)
        slugs = {a["slug"] for a in r2.json()} if r2.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        slugs = None
    if slugs is None:  # sem a lista de agentes não dá para saber o que é órfão: não aponta nada
        return []
    return sorted(v for v in r.stdout.split() if (m := VOLUME.fullmatch(v)) and m.group(1) not in slugs)


def cleanup(root: Path, assume_yes: bool = False) -> int:
    """hangar cleanup: apaga os volumes de dados de agentes excluídos (só esses; nada em uso)."""
    env = read_env(root.resolve() / ".env")
    base = f"http://localhost:{env.get('CENTRAL_PORT') or 8090}"
    hdr = {"Authorization": f"Bearer {env.get('ADMIN_TOKEN', '')}"}
    with httpx.Client(follow_redirects=True) as http:
        orphans = orphan_volumes(http, base, hdr)
    if not orphans:
        print("nenhum volume órfão")
        return 0
    print(f"{len(orphans)} volume(s) de agentes que não existem mais:")
    for v in orphans[:20]:
        print("  " + v)
    if len(orphans) > 20:
        print(f"  … e mais {len(orphans) - 20}")
    if not assume_yes and input("Apagar esses volumes? [s/N] ").strip().lower() not in ("s", "sim", "y", "yes"):
        print("nada apagado")
        return 0
    gone = 0
    for v in orphans:
        if subprocess.run(["docker", "volume", "rm", v], capture_output=True).returncode == 0:
            gone += 1
    print(f"{gone} volume(s) apagado(s)")
    return 0


def check_k8s(namespace: str, release: str) -> list[Check]:
    from . import kube
    good, bad = kube.pods_summary(namespace, release)
    if not good and not bad:
        return [Check("Kubernetes", FAIL, f"nenhum pod no namespace {namespace}",
                      f"confira o contexto do kubectl e  helm -n {namespace} status {release}")]
    agents = [p for p in good if p.startswith("agent-") and not p.startswith(kube.fullname(release))]
    out = [Check("Kubernetes", OK if not bad else FAIL,
                 f"{len(good)} pod(s) prontos ({len(agents)} de agentes) no namespace {namespace}"
                 + (f" · com problema: {', '.join(bad)}" if bad else ""),
                 "" if not bad else f"kubectl -n {namespace} describe pod <nome>  e  kubectl -n {namespace} logs <nome>")]
    return out


def diagnose(root: Path, http: httpx.Client | None = None, docker: bool = True, base: str | None = None,
             token: str | None = None, public: str | None = None, memory_on: bool | None = None,
             pre: list[Check] | None = None) -> list[Check]:
    env = read_env(root / ".env")
    base = base or f"http://localhost:{env.get('CENTRAL_PORT') or 8090}"
    token = token if token is not None else env.get("ADMIN_TOKEN", "")
    if memory_on is not None:
        env = {**env, "MEMORY_URL": "on" if memory_on else ""}
    if public is not None:
        env = {**env, "PUBLIC_BASE_URL": public}
    http = http or httpx.Client(follow_redirects=True)
    checks = list(pre or []) + (check_docker(root) if docker else [])
    central, authed = check_central(http, base, token)
    checks += central
    if not authed:
        return checks
    env = {**env, "ADMIN_TOKEN": token}
    hdr = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    version = http.get(f"{base}/api/health", timeout=5).json().get("version")
    for fn in (lambda: check_llm(http, base, hdr), lambda: check_memory(http, base, hdr, env),
               lambda: check_public(http, env.get("PUBLIC_BASE_URL", ""), version), lambda: check_sso(http, base),
               lambda: check_mcp(http, base, hdr), lambda: check_extension(http, base),
               lambda: check_agents(http, base, hdr, docker)):
        try:
            checks += fn()
        except (httpx.HTTPError, ValueError, KeyError) as e:
            checks.append(Check("verificação", WARN, f"não concluída: {type(e).__name__}: {e}"))
    return checks


def doctor(root: Path, as_json: bool = False, namespace: str | None = None, release: str = "agent-hangar") -> int:
    import yaml
    root = root.resolve()
    answers = {}
    if (root / ".hangar" / "setup.yaml").exists():
        answers = yaml.safe_load((root / ".hangar" / "setup.yaml").read_text(encoding="utf-8")) or {}
    k = answers.get("kubernetes") or {}
    if not namespace and answers.get("target") == "kubernetes":
        namespace, release = k.get("namespace", "agent-hangar"), k.get("release", release)
    if namespace:
        from . import kube
        pre = check_k8s(namespace, release)
        token = kube.admin_token_from_cluster(namespace, release)
        public = kube.public_url(k) if k else None
        try:
            with kube.port_forward(namespace, release) as local:
                mem = subprocess.run(["kubectl", "-n", namespace, "get", "deploy", f"{kube.fullname(release)}-memory"],
                                     capture_output=True).returncode == 0
                checks = diagnose(root, docker=False, base=local, token=token,
                                  public=public if public and "localhost" not in public else "", memory_on=mem, pre=pre)
        except RuntimeError as e:
            checks = pre + [Check("Central", FAIL, str(e), f"kubectl -n {namespace} get pods")]
    else:
        checks = diagnose(root)
    if as_json:
        print(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2))
    else:
        print(_c("1", "\n⌂ Agent Hangar — diagnóstico\n"))
        icon = {OK: _c("32", "✓"), WARN: _c("33", "!"), FAIL: _c("31", "✗")}
        width = max(len(c.name) for c in checks)
        for c in checks:
            print(f"  {icon[c.status]} {c.name.ljust(width)}  {c.detail}")
            if c.hint and c.status != OK:
                print(f"    {' ' * width}  → {c.hint}")
        n_fail = sum(c.status == FAIL for c in checks)
        n_warn = sum(c.status == WARN for c in checks)
        print("\n  " + (_c("32", "Tudo certo.") if not n_fail and not n_warn else
                        f"{n_fail} problema(s), {n_warn} aviso(s)."))
    return 1 if any(c.status == FAIL for c in checks) else 0
