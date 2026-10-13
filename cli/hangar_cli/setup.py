"""`hangar setup`: instala e configura o Agent Hangar com Docker, de forma guiada (ou sem perguntas, com um arquivo
de respostas). Faz, nesta ordem:

1. confere os pré-requisitos (Docker, Compose v2, porta livre);
2. pergunta o essencial: endereço e exposição (local, Cloudflare Tunnel, ngrok, Tailscale Funnel), conta de admin,
   LLM (provedor direto ou gateway), memória (Neo4j, FalkorDB ou nenhuma), login (SSO), extras e política de código;
3. grava o `.env` (segredos fortes, sem sobrescrever os que já existem) e liga os profiles via COMPOSE_PROFILES;
4. sobe a stack e espera a central ficar saudável;
5. configura pela API: conexão de LLM (testada de verdade), admin local, política de código;
6. gera as configurações das ferramentas de IA (Claude Code/Desktop, Codex, OpenCode, Cursor, VS Code) com um token
   pessoal do admin, em `.hangar/clients/`.

As respostas (sem segredos) ficam em `.hangar/setup.yaml`; rodar de novo reaproveita tudo.
"""
import base64
import getpass
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

# ---------------------------------------------------------------- catálogos
LLM_PROVIDERS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "protocol": "openai", "key": True},
    "anthropic": {"label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com", "protocol": "anthropic", "key": True,
                  "note": "serve o harness claude-code; para agentes de chat use um gateway OpenAI-compatible (LiteLLM, OpenRouter)"},
    "azure": {"label": "Azure OpenAI", "base_url": "https://<recurso>.openai.azure.com/openai/v1", "protocol": "openai", "key": True,
              "note": "use o nome do deployment como modelo"},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "protocol": "openai", "key": True},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "protocol": "openai", "key": True},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "protocol": "openai", "key": True},
    "litellm": {"label": "Gateway corporativo (LiteLLM, Portkey…)", "base_url": "http://host.docker.internal:4000/v1",
                "protocol": "openai", "key": True, "note": "também o caminho para Bedrock e Vertex AI"},
    "ollama": {"label": "Ollama que você já roda (no host)", "base_url": "http://host.docker.internal:11434/v1", "protocol": "openai", "key": False},
    "custom": {"label": "Outro endpoint OpenAI-compatible", "base_url": "", "protocol": "openai", "key": True},
}
EXPOSURES = {
    "local": "Só nesta máquina / rede interna (http://localhost)",
    "cloudflare": "Cloudflare Tunnel (HTTPS público sem abrir portas)",
    "ngrok": "ngrok (HTTPS público com domínio fixo)",
    "tailscale": "Tailscale Funnel (HTTPS público via sua tailnet)",
}
MEMORIES = {"none": "Sem memória de longo prazo", "neo4j": "Neo4j Community (padrão)",
            "falkordb": "FalkorDB (mais rápido, licença SSPL)"}
SSO = {"none": "Só contas locais (usuário e senha)", "google": "Google", "microsoft": "Microsoft Entra ID",
       "github": "GitHub", "oauth2": "Outro provedor OAuth2/OIDC"}
EXTRAS = {"data-studio": "Data Studio (análise de dados, PPTX, dashboards)",
          "mcp-gateway": "Catálogo Docker MCP (230+ servidores MCP prontos)",
          "activepieces": "Activepieces (280+ integrações de negócio)",
          "harness": "Harnesses de código (Claude Code, Codex, DeepSeek) — imagens grandes"}
CLIENTS = {"claude-code": "Claude Code", "claude-desktop": "Claude Desktop", "codex": "Codex CLI", "opencode": "OpenCode",
           "cursor": "Cursor", "vscode": "VS Code (extensão Agent Hangar)"}
TUNNEL_PROFILE = {"cloudflare": "tunnel-cloudflare", "ngrok": "tunnel-ngrok", "tailscale": "tunnel-tailscale"}
SECRET_KEYS = ("ADMIN_TOKEN", "HANGAR_SECRET_KEY", "INTERNAL_SECRET", "POSTGRES_PASSWORD", "MEMORY_TOKEN",
               "NEO4J_PASSWORD", "FALKORDB_PASSWORD", "ACTIVEPIECES_ENCRYPTION_KEY", "ACTIVEPIECES_JWT_SECRET",
               "ACTIVEPIECES_POSTGRES_PASSWORD")


class SetupError(Exception):
    pass


# ---------------------------------------------------------------- terminal
def _c(code: str, s: str) -> str:
    return s if os.environ.get("NO_COLOR") or not sys.stdout.isatty() else f"\033[{code}m{s}\033[0m"


def title(s): print("\n" + _c("1;33", f"── {s} " + "─" * max(4, 60 - len(s))))
def ok(s): print(_c("32", "  ✓ ") + s)
def warn(s): print(_c("33", "  ! ") + s)
def fail(s): print(_c("31", "  ✗ ") + s)
def info(s): print("    " + s)


def ask(prompt: str, default: str = "", secret: bool = False, required: bool = False) -> str:
    while True:
        shown = f"  {prompt}" + (f" [{('••••' if secret else default)}]" if default else "") + ": "
        v = (getpass.getpass(shown) if secret else input(shown)).strip()
        v = v or default
        if v or not required:
            return v
        warn("obrigatório")


def choose(prompt: str, options: dict, default: str) -> str:
    keys = list(options)
    print(f"  {prompt}")
    for i, k in enumerate(keys, 1):
        print(f"    {i}) {options[k]}" + (_c("2", "  (padrão)") if k == default else ""))
    while True:
        v = input(f"  escolha [1-{len(keys)}, padrão {keys.index(default) + 1}]: ").strip()
        if not v:
            return default
        if v.isdigit() and 1 <= int(v) <= len(keys):
            return keys[int(v) - 1]
        if v in options:
            return v
        warn("opção inválida")


def choose_many(prompt: str, options: dict, default: list[str]) -> list[str]:
    keys = list(options)
    print(f"  {prompt} (números separados por vírgula; vazio = padrão; 0 = nenhum)")
    for i, k in enumerate(keys, 1):
        print(f"    {i}) {options[k]}" + (_c("2", "  (padrão)") if k in default else ""))
    v = input("  escolha: ").strip()
    if not v:
        return list(default)
    if v == "0":
        return []
    out = []
    for part in re.split(r"[,\s]+", v):
        if part.isdigit() and 1 <= int(part) <= len(keys):
            out.append(keys[int(part) - 1])
        elif part in options:
            out.append(part)
    return list(dict.fromkeys(out))


def yes(prompt: str, default: bool = True) -> bool:
    v = input(f"  {prompt} [{'S/n' if default else 's/N'}]: ").strip().lower()
    return default if not v else v in ("s", "sim", "y", "yes")


# ---------------------------------------------------------------- .env
def read_env(path: Path) -> dict[str, str]:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if m:
                env[m.group(1)] = m.group(2)
    return env


def write_env(path: Path, updates: dict[str, str], template: Path | None = None):
    """Atualiza KEY=valor preservando o resto do arquivo (comentários, ordem); chaves novas vão ao fim."""
    if not path.exists() and template and template.exists():
        shutil.copyfile(template, path)
    elif path.exists():  # reconfiguração: guarda a versão anterior (tem segredos: mesmo cuidado do .env)
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copyfile(path, backup)
        try:
            os.chmod(backup, 0o600)
        except OSError:
            pass
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if m and m.group(1) in updates:
            lines[i] = f"{m.group(1)}={updates[m.group(1)]}"
            seen.add(m.group(1))
    extra = [f"{k}={v}" for k, v in updates.items() if k not in seen]
    if extra:
        lines += ["", "# --- hangar setup ---", *extra]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _rand() -> str:
    return secrets.token_hex(32)


def generated_secrets(env: dict) -> dict:
    """Segredos que faltam (nunca troca um que já existe: trocar HANGAR_SECRET_KEY tornaria ilegíveis as chaves
    de API já criptografadas)."""
    gen = {"ADMIN_TOKEN": _rand, "INTERNAL_SECRET": _rand, "POSTGRES_PASSWORD": _rand, "MEMORY_TOKEN": _rand,
           "NEO4J_PASSWORD": _rand, "FALKORDB_PASSWORD": _rand, "ACTIVEPIECES_JWT_SECRET": _rand,
           "ACTIVEPIECES_POSTGRES_PASSWORD": _rand,
           "ACTIVEPIECES_ENCRYPTION_KEY": lambda: secrets.token_hex(16),
           "HANGAR_SECRET_KEY": lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
    return {k: f() for k, f in gen.items() if not env.get(k)}


def public_url(a: dict) -> str:
    exp = a["exposure"]
    mode = exp["mode"]
    if mode == "cloudflare":
        return f"https://{exp['hostname']}"
    if mode == "ngrok":
        return f"https://{exp['domain']}"
    if mode == "tailscale":
        return f"https://{exp['hostname']}.{exp['tailnet']}"
    return f"http://localhost:{a.get('port', 8090)}"


def compose_profiles(a: dict) -> list[str]:
    p = []
    mem = a.get("memory", "none")
    if mem == "neo4j":
        p.append("memory")
    elif mem == "falkordb":
        p.append("memory-falkordb")
    p += [x for x in a.get("extras", []) if x in EXTRAS]
    if a["exposure"]["mode"] in TUNNEL_PROFILE:
        p.append(TUNNEL_PROFILE[a["exposure"]["mode"]])
    return p


def env_updates(a: dict, env: dict, repo_version: str) -> dict[str, str]:
    """Tudo que o .env precisa para as escolhas feitas (sem os segredos aleatórios, que vêm de generated_secrets)."""
    u: dict[str, str] = {"PUBLIC_BASE_URL": public_url(a), "CENTRAL_PORT": str(a.get("port", 8090)),
                         "COMPOSE_PROFILES": ",".join(compose_profiles(a)), "LOCAL_LOGIN": "1"}
    if a.get("images") == "published":
        u["HANGAR_REGISTRY"] = "ghcr.io/valteresj2"
        u["HANGAR_VERSION"] = repo_version
    llm_name = (a.get("llm") or {}).get("name", "")
    mem = a.get("memory", "none")
    if mem in ("neo4j", "falkordb"):
        u.update({"MEMORY_URL": "http://memory:8000", "MEMORY_BACKEND": mem})
        if llm_name:  # sem LLM escolhido agora, mantém a conexão que a memória já usa
            u["MEMORY_LLM_CONNECTION"] = llm_name
    else:
        u["MEMORY_URL"] = ""
    exp = a["exposure"]
    if exp["mode"] == "cloudflare":
        u["CLOUDFLARE_TUNNEL_TOKEN"] = exp["token"]
    elif exp["mode"] == "ngrok":
        u.update({"NGROK_AUTHTOKEN": exp["authtoken"], "NGROK_DOMAIN": exp["domain"]})
    elif exp["mode"] == "tailscale":
        u.update({"TS_AUTHKEY": exp["authkey"], "TS_HOSTNAME": exp["hostname"]})
    sso = a.get("sso") or {"provider": "none"}
    prov = sso.get("provider", "none")
    if prov != "none":
        prefix = {"google": "OAUTH_GOOGLE", "microsoft": "OAUTH_MICROSOFT", "github": "OAUTH_GITHUB",
                  "oauth2": "OAUTH_OAUTH2"}[prov]
        u[f"{prefix}_CLIENT_ID"] = sso.get("client_id", "")
        u[f"{prefix}_CLIENT_SECRET"] = sso.get("client_secret", "")
        if prov == "microsoft" and sso.get("tenant"):
            u["OAUTH_MICROSOFT_TENANT"] = sso["tenant"]
        if sso.get("allowed_domains") and prov in ("google", "microsoft"):
            u[f"{prefix}_ALLOWED_DOMAINS"] = sso["allowed_domains"]
        if prov == "oauth2":
            for k in ("authorize_url", "token_url", "userinfo_url", "label"):
                if sso.get(k):
                    u[f"OAUTH_OAUTH2_{k.upper()}"] = sso[k]
    if a.get("admin", {}).get("email"):
        u["BOOTSTRAP_ADMIN_EMAILS"] = a["admin"]["email"]
    return u


def public_answers(a: dict) -> dict:
    """As respostas sem segredos (para .hangar/setup.yaml e para rodar de novo)."""
    def scrub(o):
        if isinstance(o, dict):
            return {k: ("" if re.search(r"(key|token|secret|password|authkey)$", k, re.I) else scrub(v)) for k, v in o.items()}
        return o
    return scrub(a)


# ---------------------------------------------------------------- ferramentas de IA
def client_configs(base: str, key: str, clients: list[str]) -> dict[str, str]:
    """Arquivo -> conteúdo. `key` é o token pessoal do admin (age como ele, com os papéis dele)."""
    mcp = f"{base}/mcp"
    out: dict[str, str] = {}
    lines = ["# Conectar as ferramentas de IA ao Agent Hangar", "",
             f"MCP da plataforma: `{mcp}` · token pessoal: veja `token.txt` nesta pasta (não compartilhe).",
             "Depois de conectar, peça na ferramenta: *\"crie um agente que…\"*.", ""]
    if "claude-code" in clients:
        cmd = f'claude mcp add --transport http --scope user agent-hangar {mcp} --header "Authorization: Bearer {key}"'
        out["claude-code.sh"] = cmd + "\n"
        lines += ["## Claude Code", "```bash", "sh claude-code.sh   # ou rode o comando do arquivo", "```", ""]
    if "claude-desktop" in clients:
        args = ["-y", "mcp-remote", mcp, "--header", f"Authorization: Bearer {key}"]
        if mcp.startswith("http:"):
            args.append("--allow-http")
        out["claude_desktop_config.json"] = json.dumps({"mcpServers": {"agent-hangar": {"command": "npx", "args": args}}}, indent=2)
        lines += ["## Claude Desktop", "Settings → Developer → Edit Config: mescle `claude_desktop_config.json` e reinicie.", ""]
    if "codex" in clients:
        out["codex.toml"] = f'[mcp_servers.agent_hangar]\nurl = "{mcp}"\nhttp_headers = {{ Authorization = "Bearer {key}" }}\n'
        lines += ["## Codex CLI", "Acrescente `codex.toml` ao `~/.codex/config.toml`.", ""]
    if "opencode" in clients:
        out["opencode.json"] = json.dumps({"$schema": "https://opencode.ai/config.json", "mcp": {"agent-hangar": {
            "type": "remote", "url": mcp, "enabled": True, "headers": {"Authorization": f"Bearer {key}"}}}}, indent=2)
        lines += ["## OpenCode", "Mescle `opencode.json` no seu `opencode.json`.", ""]
    if "cursor" in clients:
        out["cursor-mcp.json"] = json.dumps({"mcpServers": {"agent-hangar": {"url": mcp, "headers": {"Authorization": f"Bearer {key}"}}}}, indent=2)
        lines += ["## Cursor", "Mescle `cursor-mcp.json` em `~/.cursor/mcp.json`.", ""]
    if "vscode" in clients:
        lines += ["## VS Code (extensão Agent Hangar)", "```bash", f"curl -LO {base}/downloads/agent-hangar-vscode.vsix",
                  "code --install-extension agent-hangar-vscode.vsix", "```",
                  "No VS Code: **Agent Hangar: Entrar pelo portal** (login com a sua conta, sem token). Os agentes aparecem no "
                  "seletor de modelos do chat e o MCP da plataforma é registrado sozinho.", ""]
    lines += ["## ChatGPT e Claude.ai (web)", "Os conectores web exigem MCP em HTTPS público com OAuth 2.1 — previsto na "
              "próxima fase. Enquanto isso, use Claude Code/Desktop, Codex, OpenCode, Cursor ou VS Code.", ""]
    out["README.md"] = "\n".join(lines)
    out["token.txt"] = key + "\n"
    return out


# ---------------------------------------------------------------- perguntas
def interactive(prev: dict, env: dict, target: str | None = None, configure_only: bool = False) -> dict:
    a: dict = {}
    if configure_only:
        pk = prev.get("kubernetes") or {}
        title("Instalação existente (Terraform, GitOps…)")
        a["target"] = "kubernetes"
        a["kubernetes"] = {"namespace": ask("Namespace", pk.get("namespace", "agent-hangar")),
                           "release": ask("Nome do release Helm", pk.get("release", "agent-hangar"))}
        host = ask("Host público (vazio = sem; acesso por port-forward)", pk.get("host", ""))
        if host:
            a["kubernetes"].update(exposure="ingress", host=host)
        _interactive_common(a, prev)
        return a
    title("Onde instalar")
    a["target"] = target or choose("Onde o Agent Hangar vai rodar?", {
        "docker": "Docker nesta máquina (notebook, VM, servidor)",
        "kubernetes": "Kubernetes (GKE, AKS, EKS ou outro cluster)"}, prev.get("target", "docker"))
    if a["target"] == "kubernetes":
        _interactive_k8s(a, prev)
    else:
        _interactive_docker(a, prev, env)
    _interactive_common(a, prev)
    return a


def _interactive_k8s(a: dict, prev: dict):
    from . import kube
    pk = prev.get("kubernetes") or {}
    title("1/7 · Cluster")
    info(f"contexto atual do kubectl: {kube.current_context()}")
    k: dict = {"provider": choose("Qual nuvem / cluster?", kube.PROVIDERS, pk.get("provider", "gke"))}
    k["namespace"] = ask("Namespace", pk.get("namespace", "agent-hangar"))
    k["release"] = ask("Nome do release Helm", pk.get("release", "agent-hangar"))
    k["registry"] = ask("Registry das imagens (espelho privado, se houver)", pk.get("registry", "ghcr.io/valteresj2"))
    title("2/7 · Exposição")
    k["exposure"] = choose("Como as pessoas vão acessar?", kube.EXPOSURES, pk.get("exposure", "ingress"))
    if k["exposure"] == "ingress":
        k["host"] = ask("Host público (ex.: hangar.suaempresa.com)", pk.get("host", ""), required=True)
        if k["provider"] in ("aks", "local"):
            k["issuer"] = ask("ClusterIssuer do cert-manager para o TLS (vazio = sem cert-manager)", pk.get("issuer", "letsencrypt"))
        else:
            info("TLS: GKE usa certificado gerenciado do Google; EKS usa o certificado do ACM no ALB (ajuste o ARN no values-eks)")
    elif k["exposure"] == "cloudflare":
        info("No Zero Trust: crie o túnel e um Public Hostname apontando para "
             f"http://{kube.fullname(k['release'])}-central:8080")
        k["hostname"] = ask("Hostname público", pk.get("hostname", ""), required=True)
        k["token"] = ask("Token do túnel", "", secret=True, required=True)
    title("Banco de dados")
    k["database"] = choose("Postgres", {"bundled": "No cluster (StatefulSet) — simples, para começar",
                                         "external": "Gerenciado (Cloud SQL, Azure Database, RDS) — recomendado em produção"},
                           pk.get("database", "bundled"))
    if k["database"] == "external":
        k["database_url"] = ask("URL (postgresql+psycopg://usuario:senha@host:5432/hangar?sslmode=require)", "", secret=True, required=True)
    k["replicas"] = int(ask("Réplicas da central (2+ = alta disponibilidade; recomendado com Postgres gerenciado)",
                            str(pk.get("replicas", 2 if k["database"] == "external" else 1))) or 1)
    k["allow_private_egress"] = yes("Os agentes precisam alcançar IPs privados (ex.: gateway de LLM dentro da rede)?",
                                    bool(pk.get("allow_private_egress", False)))
    a["kubernetes"] = k
    a["images"] = "published"


def _interactive_docker(a: dict, prev: dict, env: dict):
    title("1/7 · Imagens")
    a["images"] = choose("Como obter as imagens?", {
        "published": "Baixar as imagens publicadas (rápido, recomendado)",
        "build": "Construir a partir deste código (para quem altera o Hangar)"}, prev.get("images", "published"))

    title("2/7 · Endereço e exposição")
    prev_exp = prev.get("exposure") or {"mode": "local"}
    mode = choose("Como as pessoas vão acessar o Hangar?", EXPOSURES, prev_exp.get("mode", "local"))
    exp: dict = {"mode": mode}
    a["port"] = int(ask("Porta local da central", str(prev.get("port", 8090))))
    if mode == "cloudflare":
        info("No Cloudflare Zero Trust: Networks → Tunnels → Create → Docker. Copie o token e adicione um Public")
        info("Hostname apontando para o serviço  http://central:8080")
        exp["hostname"] = ask("Hostname público (ex.: hangar.suaempresa.com)", prev_exp.get("hostname", ""), required=True)
        exp["token"] = ask("Token do túnel", env.get("CLOUDFLARE_TUNNEL_TOKEN", ""), secret=True, required=True)
    elif mode == "ngrok":
        info("Em dashboard.ngrok.com: copie o authtoken e reserve um domínio (o domínio gratuito também serve).")
        exp["domain"] = ask("Domínio ngrok (ex.: hangar-acme.ngrok-free.app)", prev_exp.get("domain", ""), required=True)
        exp["authtoken"] = ask("Authtoken", env.get("NGROK_AUTHTOKEN", ""), secret=True, required=True)
    elif mode == "tailscale":
        info("Em login.tailscale.com: gere uma auth key (reutilizável) e habilite HTTPS + Funnel na política da tailnet.")
        exp["hostname"] = ask("Nome da máquina na tailnet", prev_exp.get("hostname", "agent-hangar"))
        exp["tailnet"] = ask("Domínio da tailnet (ex.: acme.ts.net)", prev_exp.get("tailnet", ""), required=True)
        exp["authkey"] = ask("Auth key", env.get("TS_AUTHKEY", ""), secret=True, required=True)
    a["exposure"] = exp


def _interactive_common(a: dict, prev: dict):
    title("3/7 · Administrador")
    prev_admin = prev.get("admin") or {}
    admin = {"username": ask("Usuário do admin", prev_admin.get("username", "admin"))}
    admin["password"] = ask("Senha do admin (mín. 8; vazio = gerar uma forte)", "", secret=True)
    admin["email"] = ask("E-mail do admin (opcional; vira admin também no login SSO)", prev_admin.get("email", ""))
    a["admin"] = admin

    title("4/7 · LLM")
    prev_llm = prev.get("llm") or {}
    prov = choose("De onde vêm os modelos?", {**{k: v["label"] for k, v in LLM_PROVIDERS.items()},
                   "none": "Configurar depois (até lá, os agentes respondem com um modelo de teste)"},
                  prev_llm.get("provider", "openrouter"))
    llm: dict = {"provider": prov}
    if prov == "none":
        warn("sem provedor, os agentes respondem com um modelo de teste (respostas fictícias). Para o trabalho de verdade, "
             "cadastre a chave de API de um provedor depois em Console → Provedores de LLM, ou rode  hangar setup  de novo")
    if prov != "none":
        p = LLM_PROVIDERS[prov]
        if p.get("note"):
            info(p["note"])
        llm["name"] = ask("Nome da conexão", prev_llm.get("name") or prov)
        llm["base_url"] = ask("URL base", prev_llm.get("base_url") or p["base_url"], required=True)
        llm["model"] = ask("Modelo padrão (ex.: gpt-4.1-mini, anthropic/claude-sonnet-4.5, llama3.1)", prev_llm.get("model", ""), required=True)
        llm["api_key"] = ask("Chave de API", "", secret=True, required=p["key"]) or ("ollama" if prov == "ollama" else "")
        llm["protocol"] = p["protocol"]
        llm["approve_for_code"] = yes("Aprovar esta conexão para receber código (agentes de código no VS Code)?", True)
    a["llm"] = llm

    title("5/7 · Memória dos agentes")
    a["memory"] = choose("Memória de longo prazo (grafo de fatos com histórico)?", MEMORIES, prev.get("memory", "neo4j" if prov != "none" else "none"))
    if a["memory"] != "none" and prov == "none":
        warn("a memória precisa de um LLM para extrair os fatos: configure a conexão depois e ajuste MEMORY_LLM_CONNECTION")

    title("6/7 · Login")
    prev_sso = prev.get("sso") or {}
    sp = choose("Login das pessoas", SSO, prev_sso.get("provider", "none"))
    sso: dict = {"provider": sp}
    if sp != "none":
        cb = f"{_public_base(a)}/api/auth/callback/{sp}"
        info(f"Registre no provedor a URI de redirecionamento:  {cb}")
        sso["client_id"] = ask("Client ID", prev_sso.get("client_id", ""), required=True)
        sso["client_secret"] = ask("Client secret", "", secret=True, required=True)
        if sp == "microsoft":
            sso["tenant"] = ask("Tenant (ID ou domínio, ex.: acme.onmicrosoft.com)", prev_sso.get("tenant", ""), required=True)
        if sp in ("google", "microsoft"):
            sso["allowed_domains"] = ask("Domínios de e-mail permitidos (ex.: acme.com; vazio = todos)", prev_sso.get("allowed_domains", ""))
        if sp == "oauth2":
            sso["label"] = ask("Nome no botão de login (ex.: Okta, Keycloak)", prev_sso.get("label", "SSO"))
            for k, label in (("authorize_url", "URL de autorização"), ("token_url", "URL de token"), ("userinfo_url", "URL de userinfo")):
                sso[k] = ask(label, prev_sso.get(k, ""), required=True)
    a["sso"] = sso

    title("7/7 · Extras e ferramentas")
    if a.get("target") == "kubernetes":
        a["extras"] = []
        info("No Kubernetes: Data Studio, catálogo Docker MCP e Activepieces ainda não fazem parte do chart")
    else:
        a["extras"] = choose_many("Componentes opcionais", EXTRAS, prev.get("extras", []))
    a["code_policy"] = choose("Política de código (quais LLMs recebem código dos devs)", {
        "any": "Qualquer conexão", "approved": "Só conexões aprovadas para código"}, prev.get("code_policy", "approved"))
    a["clients"] = choose_many("Gerar configuração para quais ferramentas de IA?", CLIENTS,
                               prev.get("clients", ["claude-code", "vscode"]))
    a["demo"] = yes("Criar uma empresa de exemplo (time, projeto e três Digital employees) para o portal não abrir vazio?",
                    bool(prev.get("demo", True)))


def _public_base(a: dict) -> str:
    if a.get("target") == "kubernetes":
        from . import kube
        return kube.public_url(a["kubernetes"])
    return public_url(a)


def validate(a: dict) -> dict:
    """Completa padrões e confere o arquivo de respostas (modo sem perguntas)."""
    a = json.loads(json.dumps(a or {}))
    a.setdefault("target", "docker")
    a.setdefault("images", "published")
    a.setdefault("port", 8090)
    a.setdefault("exposure", {"mode": "local"})
    a.setdefault("admin", {"username": "admin"})
    a.setdefault("llm", {"provider": "none"})
    a.setdefault("memory", "none")
    a.setdefault("sso", {"provider": "none"})
    a.setdefault("extras", [])
    a.setdefault("code_policy", "approved")
    a.setdefault("clients", ["claude-code", "vscode"])
    a["demo"] = bool(a.get("demo", False))
    errs = []
    if a["target"] not in ("docker", "kubernetes"):
        errs.append(f"target: {a['target']} (docker | kubernetes)")
    if a["target"] == "kubernetes":
        from . import kube
        k = a.setdefault("kubernetes", {})
        k.setdefault("provider", "local")
        k.setdefault("namespace", "agent-hangar")
        k.setdefault("release", "agent-hangar")
        k.setdefault("registry", "ghcr.io/valteresj2")
        k.setdefault("exposure", "portforward")
        k.setdefault("database", "bundled")
        k["replicas"] = int(k.get("replicas") or 1)
        if not 1 <= k["replicas"] <= 10:
            errs.append("kubernetes.replicas: de 1 a 10")
        if k["provider"] not in kube.PROVIDERS:
            errs.append(f"kubernetes.provider: {k['provider']} ({', '.join(kube.PROVIDERS)})")
        if k["exposure"] not in kube.EXPOSURES:
            errs.append(f"kubernetes.exposure: {k['exposure']} ({', '.join(kube.EXPOSURES)})")
        need = {"ingress": ("host",), "cloudflare": ("hostname", "token")}.get(k["exposure"], ())
        errs += [f"kubernetes.{f} é obrigatório para {k['exposure']}" for f in need if not k.get(f)]
        if k["database"] == "external" and not k.get("database_url"):
            errs.append("kubernetes.database_url é obrigatório com database: external")
        a["extras"] = []
    mode = a["exposure"].get("mode", "local")
    need = {"cloudflare": ("hostname", "token"), "ngrok": ("domain", "authtoken"), "tailscale": ("hostname", "tailnet", "authkey")}
    if mode not in EXPOSURES:
        errs.append(f"exposure.mode: {mode} (opções: {', '.join(EXPOSURES)})")
    for k in need.get(mode, ()):
        if not a["exposure"].get(k):
            errs.append(f"exposure.{k} é obrigatório para {mode}")
    prov = a["llm"].get("provider", "none")
    if prov not in (*LLM_PROVIDERS, "none"):
        errs.append(f"llm.provider: {prov}")
    elif prov != "none":
        p = LLM_PROVIDERS[prov]
        a["llm"].setdefault("name", prov)
        a["llm"].setdefault("base_url", p["base_url"])
        a["llm"].setdefault("protocol", p["protocol"])
        a["llm"].setdefault("approve_for_code", True)
        if prov == "ollama":
            a["llm"].setdefault("api_key", "ollama")
        for k in ("base_url", "model"):
            if not a["llm"].get(k) or "<" in a["llm"].get(k, ""):
                errs.append(f"llm.{k} é obrigatório")
        if p["key"] and not a["llm"].get("api_key"):
            errs.append("llm.api_key é obrigatório para " + prov)
    if a["memory"] not in MEMORIES:
        errs.append(f"memory: {a['memory']}")
    if a["sso"].get("provider", "none") not in SSO:
        errs.append(f"sso.provider: {a['sso'].get('provider')}")
    elif a["sso"].get("provider") != "none":
        sso = a["sso"]
        if not (sso.get("client_id") and sso.get("client_secret")):
            errs.append("sso.client_id e sso.client_secret são obrigatórios")
        if sso["provider"] == "microsoft" and not sso.get("tenant"):
            errs.append("sso.tenant é obrigatório para Microsoft Entra ID")
        if sso["provider"] == "oauth2":
            errs += [f"sso.{k} é obrigatório para oauth2" for k in ("authorize_url", "token_url", "userinfo_url") if not sso.get(k)]
    pw = a["admin"].get("password") or ""
    if pw and len(pw) < 8:
        errs.append("admin.password precisa de 8+ caracteres")
    if errs:
        raise SetupError("respostas inválidas:\n  - " + "\n  - ".join(errs))
    return a


# ---------------------------------------------------------------- sistema
def run(cmd: list[str], cwd: Path, check=True, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=capture)


def compose_ok(version: str) -> bool:
    """Compose 2.20+ (profiles por COMPOSE_PROFILES, pull --ignore-pull-failures); versões 3, 4, 5… também servem."""
    m = re.search(r"(\d+)\.(\d+)", version or "")
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (2, 20)


def preflight(root: Path, port: int) -> list[str]:
    problems = []
    if not shutil.which("docker"):
        return ["Docker não encontrado. Instale o Docker Desktop (Windows/macOS) ou Docker Engine (Linux)."]
    r = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True)
    if r.returncode != 0:
        problems.append("Docker Compose v2 não encontrado (`docker compose`).")
    elif not compose_ok(r.stdout):
        problems.append(f"Docker Compose {r.stdout.strip()} — use a versão 2.20 ou mais nova.")
    if subprocess.run(["docker", "info"], capture_output=True, text=True).returncode != 0:
        problems.append("O daemon do Docker não está respondendo (abra o Docker Desktop ou `sudo systemctl start docker`).")
    if not (root / "docker-compose.yml").exists():
        problems.append(f"docker-compose.yml não encontrado em {root} — rode na pasta do repositório (ou use --dir).")
    running = subprocess.run(["docker", "ps", "--filter", "name=central", "--format", "{{.Ports}}"], capture_output=True, text=True).stdout
    if f":{port}->" not in running:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                problems.append(f"A porta {port} já está em uso por outro programa (escolha outra porta).")
    return problems


def repo_version(root: Path) -> str:
    cfg = root / "central" / "app" / "config.py"
    m = re.search(r'^VERSION = "([^"]+)"', cfg.read_text(encoding="utf-8"), re.M) if cfg.exists() else None
    return m.group(1) if m else "latest"


def wait_healthy(base: str, timeout=300) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/health", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(3)
    return False


# ---------------------------------------------------------------- configuração pela API
class Api:
    def __init__(self, base: str, token: str):
        self.base, self.h = base.rstrip("/"), {"Authorization": f"Bearer {token}"}

    def req(self, method: str, path: str, **kw) -> httpx.Response:
        return httpx.request(method, self.base + path, headers=self.h, timeout=60, **kw)


def configure(api: Api, a: dict, local_base: str) -> dict:
    """Passos idempotentes pela API. Devolve {admin_password (se gerada), client_token}."""
    result: dict = {}
    llm = a.get("llm") or {}
    if llm.get("provider", "none") != "none":
        r = api.req("POST", "/api/catalog/llm", json={"name": llm["name"], "base_url": llm["base_url"],
                                                       "model_name": llm["model"], "api_key": llm.get("api_key", ""),
                                                       "protocol": llm["protocol"], "description": f"hangar setup · {llm['provider']}"})
        if r.status_code >= 400:
            raise SetupError(f"não consegui cadastrar a conexão de LLM: {r.text[:200]}")
        ok(f"conexão de LLM '{llm['name']}' cadastrada")
        t = api.req("POST", f"/api/catalog/llm/{llm['name']}/test").json()
        (ok if t.get("ok") else warn)(f"teste do LLM: {t.get('detail')}")
        if llm.get("approve_for_code"):
            api.req("PATCH", f"/api/catalog/llm/{llm['name']}/code", json={"allow_code": True})
            ok("conexão aprovada para receber código")
    api.req("PATCH", "/api/org", json={"code_policy": a.get("code_policy", "approved")})
    ok(f"política de código: {a.get('code_policy', 'approved')}")

    admin = a["admin"]
    users = api.req("GET", "/api/users").json()
    exists = any((u.get("username") or "") == admin["username"] for u in users if isinstance(u, dict))
    password = admin.get("password") or ""
    if not exists:
        if not password:
            password = secrets.token_urlsafe(12)
            result["admin_password"] = password
        r = api.req("POST", "/api/users", json={"username": admin["username"], "password": password, "org_role": "admin",
                                                "name": admin["username"], "email": admin.get("email", "")})
        if r.status_code >= 400:
            raise SetupError(f"não consegui criar o admin: {r.text[:200]}")
        ok(f"admin '{admin['username']}' criado")
    else:
        ok(f"admin '{admin['username']}' já existe (senha mantida)")
    if password and a.get("clients"):
        result["client_token"] = personal_token(local_base, admin["username"], password)
    return result


def seed_demo(base: str, admin_token: str, a: dict):
    """A empresa de exemplo (hangar demo), com o admin como gestora e o LLM escolhido (ou o modelo de teste)."""
    from .demo import Demo
    from .main import Api as CliApi
    title("Empresa de exemplo")
    api = CliApi(base, admin_token)
    admin = next((u for u in api.get("/users") if (u.get("username") or "") == a["admin"]["username"]), None)
    llm = (a.get("llm") or {}).get("name", "") if (a.get("llm") or {}).get("provider", "none") != "none" else ""
    try:
        Demo(api, say=info).seed(admin["email"] if admin else None, llm)
    except Exception as e:  # noqa: BLE001 — o exemplo é um extra: a instalação segue pronta
        warn(f"não consegui criar o exemplo ({e}); rode depois:  hangar demo")


def personal_token(base: str, username: str, password: str) -> str:
    """Token pessoal do admin (escopo user) para as ferramentas de IA: entra com a senha e cria a chave."""
    with httpx.Client(base_url=base, timeout=30) as c:
        r = c.post("/api/auth/password", json={"username": username, "password": password})
        if r.status_code >= 400:
            raise SetupError(f"login do admin falhou: {r.text[:200]}")
        csrf = c.cookies.get("hangar_csrf", "")
        k = c.post("/api/keys", json={"name": "ferramentas de IA (hangar setup)", "scopes": ["user"]},
                   headers={"X-CSRF-Token": csrf})
        if k.status_code >= 400:
            raise SetupError(f"não consegui criar o token pessoal: {k.text[:200]}")
        return k.json()["key"]


# ---------------------------------------------------------------- orquestração
def setup(root: Path, answers_file: str | None = None, start: bool = True, assume_yes: bool = False,
          target: str | None = None, configure_only: bool = False) -> int:
    try:  # as mensagens saem na ordem certa, intercaladas com a saída do docker compose
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    root = root.resolve()
    state = root / ".hangar"
    state.mkdir(exist_ok=True)
    env_path = root / ".env"
    env = read_env(env_path)
    prev = yaml.safe_load((state / "setup.yaml").read_text(encoding="utf-8")) if (state / "setup.yaml").exists() else {}

    print(_c("1", "\n⌂ Agent Hangar — instalação guiada"))
    if configure_only:  # instalação feita por Terraform/GitOps: só configura (LLM, admin, ferramentas), sem helm
        target = "kubernetes"
    if answers_file:
        raw = yaml.safe_load(Path(answers_file).read_text(encoding="utf-8")) or {}
        if target:
            raw["target"] = target
        a = validate(raw)
    else:
        a = validate(interactive(prev or {}, env, target, configure_only))
    if a["target"] == "kubernetes":
        return setup_kubernetes(root, state, a, start, configure_only)
    title("Pré-requisitos")
    problems = preflight(root, a["port"])
    for p in problems:
        fail(p)
    if problems:
        return 2
    ok("Docker, Docker Compose e porta livre")

    title("Configuração (.env)")
    secrets_new = generated_secrets(env)
    updates = {**secrets_new, **env_updates(a, env, repo_version(root))}
    write_env(env_path, updates, root / ".env.example")
    (state / "setup.yaml").write_text(yaml.safe_dump(public_answers(a), allow_unicode=True, sort_keys=False), encoding="utf-8")
    ok(f".env atualizado ({len(secrets_new)} segredo(s) novo(s); os existentes foram mantidos)")
    ok(f"profiles: {updates['COMPOSE_PROFILES'] or '(nenhum extra)'} · endereço: {updates['PUBLIC_BASE_URL']}")
    if not start:
        info("--no-start: suba depois com  docker compose up -d   e rode  hangar setup  de novo para configurar")
        return 0

    title("Subindo a stack")
    if not assume_yes and not answers_file and not yes("Subir agora com docker compose?", True):
        info("Ok. Rode  docker compose up -d  quando quiser e depois  hangar setup  de novo.")
        return 0
    if a["images"] == "published":
        run(["docker", "compose", "pull", "--ignore-pull-failures"], root, check=False)
        run(["docker", "compose", "up", "-d"], root)
    else:
        run(["docker", "compose", "up", "-d", "--build"], root)
    if "harness" in a.get("extras", []):
        if a["images"] == "build":
            run(["docker", "compose", "--profile", "harness", "build"], root, check=False)
        else:  # a imagem do Claude Code não é publicada (licença proprietária): construída aqui, a partir deste código
            run(["docker", "compose", "--profile", "harness", "build", "harness-claude-code"], root, check=False)
    local = f"http://localhost:{a['port']}"
    if not wait_healthy(local):
        fail("a central não ficou saudável em 5 min — veja  docker compose logs central")
        return 1
    ok(f"central no ar em {local}")

    title("Configurando")
    env = read_env(env_path)
    result = configure(Api(local, env["ADMIN_TOKEN"]), a, local)
    if a.get("demo"):
        seed_demo(local, env["ADMIN_TOKEN"], a)
    base = updates["PUBLIC_BASE_URL"]
    if result.get("client_token"):
        cdir = state / "clients"
        cdir.mkdir(exist_ok=True)
        for name, content in client_configs(base, result["client_token"], a["clients"]).items():
            p = cdir / name
            p.write_text(content, encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        ok(f"configurações das ferramentas de IA em {cdir} (veja README.md lá)")
    elif a.get("clients"):
        warn("sem a senha do admin não dá para gerar o token pessoal: rode de novo informando a senha, ou crie um "
             "token pessoal em Minhas chaves e siga docs/clients.md")

    title("Pronto")
    info(f"Portal e console:   {base}   (admin: {base}/ui/)")
    info(f"Usuário admin:      {a['admin']['username']}" + (f"   senha gerada: {result['admin_password']}" if result.get("admin_password") else ""))
    if result.get("admin_password"):
        warn("anote a senha gerada agora — ela não fica salva em lugar nenhum")
    info("Acesso de emergência: o valor de ADMIN_TOKEN no .env (guarde com cuidado)")
    info("Diagnóstico:         hangar doctor")
    if a["llm"].get("provider", "none") == "none":
        warn(f"nenhum provedor de LLM: os agentes respondem com um modelo de teste. Cadastre a chave de API de um provedor "
             f"(OpenAI, Anthropic, Gemini, OpenRouter, DeepSeek, Azure, gateway da empresa) em  {base}/ui/#/providers  "
             "ou rode  hangar setup  de novo")
    return 0


def setup_kubernetes(root: Path, state: Path, a: dict, start: bool, configure_only: bool = False) -> int:
    from . import kube
    k = a["kubernetes"]
    if configure_only:
        return _configure_existing(state, a)
    title("Pré-requisitos")
    problems = kube.preflight()
    for p in problems:
        (fail if start else warn)(p)
    if problems and start:
        return 2
    if not problems:
        ok(f"kubectl e helm prontos · contexto: {kube.current_context()}")

    title("Values do chart")
    old = yaml.safe_load((state / "secrets.values.yaml").read_text(encoding="utf-8")) if (state / "secrets.values.yaml").exists() else {}
    values, secret_values = kube.build_values(a, repo_version(root))
    secret_values = kube.merge_secrets(secret_values, old or {})
    vp, sp = kube.write_values(state, values, secret_values)
    (state / "setup.yaml").write_text(yaml.safe_dump(public_answers(a), allow_unicode=True, sort_keys=False), encoding="utf-8")
    ok(f"{vp.name} e {sp.name} (segredos, permissão 600) em {state}")
    cmd = kube.helm_command(root, a, vp, sp)
    info("helm: " + " ".join(cmd))
    if not start:
        info("--no-start: rode o comando acima e depois  hangar setup --target kubernetes  para configurar")
        return 0

    title("Instalando no cluster (helm)")
    if run(cmd, root, check=False).returncode != 0:
        fail("o helm falhou — veja a saída acima e  kubectl -n " + k["namespace"] + " get pods")
        return 1
    ok(f"release {k['release']} no ar no namespace {k['namespace']}")

    return _configure_and_finish(state, a, secret_values["secrets"]["adminToken"])


def _configure_existing(state: Path, a: dict) -> int:
    from . import kube
    k = a["kubernetes"]
    title("Pré-requisitos")
    problems = kube.preflight()
    for p in problems:
        fail(p)
    if problems:
        return 2
    token = kube.admin_token_from_cluster(k["namespace"], k["release"])
    if not token:
        fail(f"não achei o Secret {kube.fullname(k['release'])}-secrets no namespace {k['namespace']} "
             "(confira o contexto do kubectl, o namespace e o release)")
        return 1
    ok(f"instalação encontrada · contexto: {kube.current_context()} · namespace {k['namespace']}")
    (state / "setup.yaml").write_text(yaml.safe_dump(public_answers(a), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return _configure_and_finish(state, a, token)


def _configure_and_finish(state: Path, a: dict, admin_token: str) -> int:
    from . import kube
    k = a["kubernetes"]
    title("Configurando")
    with kube.port_forward(k["namespace"], k["release"]) as local:
        result = configure(Api(local, admin_token), a, local)
    base = kube.public_url(k)
    if result.get("client_token"):
        cdir = state / "clients"
        cdir.mkdir(exist_ok=True)
        for name, content in client_configs(base, result["client_token"], a["clients"]).items():
            (cdir / name).write_text(content, encoding="utf-8")
            try:
                os.chmod(cdir / name, 0o600)
            except OSError:
                pass
        ok(f"configurações das ferramentas de IA em {cdir}")

    title("Pronto")
    if k["exposure"] == "portforward":
        info(f"Acesso:  kubectl -n {k['namespace']} port-forward svc/{kube.fullname(k['release'])}-central 18090:8080"
             "  →  http://localhost:18090")
    else:
        info(f"Portal e console:   {base}   (DNS do host -> load balancer/túnel)")
    info(f"Usuário admin:      {a['admin']['username']}" + (f"   senha gerada: {result['admin_password']}" if result.get("admin_password") else ""))
    if result.get("admin_password"):
        warn("anote a senha gerada agora — ela não fica salva em lugar nenhum")
    info(f"Diagnóstico:         hangar doctor --namespace {k['namespace']}")
    return 0
