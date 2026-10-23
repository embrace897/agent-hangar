# Installing Agent Hangar

The guided installer, `hangar setup`, asks a few questions and does everything else:
- writes `.env`;
- turns on the right Compose profiles;
- starts the stack;
- registers and **tests** your LLM;
- creates the admin;
- generates the configuration of your AI tools.

`hangar doctor` checks the result and tells you how to fix each problem. This guide covers **Docker** on one machine:
a laptop, a VM or an on-premises server. For a VM on AWS, Azure or Google Cloud, see [cloud-vm.md](cloud-vm.md) (create
the VM, install Docker, then this guide). For a cluster (GKE, AKS, EKS), see [kubernetes.md](kubernetes.md).

## 1. Requirements

| | |
|---|---|
| Docker | Docker Desktop (Windows/macOS) or Docker Engine (Linux) with **Compose 2.20+** |
| Python | **3.10+**, used only to run the installer |
| Machine | 4 vCPU / 8 GB RAM for the base stack; add 2–4 GB for memory (Neo4j) and Data Studio |
| Network | Outbound HTTPS to your LLM provider and to `ghcr.io` (published images) |
| CPU | **x86-64 (amd64) or ARM64**: the published images are multi-architecture since 0.14.1 (Apple Silicon Macs, AWS Graviton, Azure Ampere, GKE T2A) |

One install per Docker host: the stack uses fixed network and volume names (`hangar_agents`, `hangar_jobs`…), so a
second copy on the same machine would share them. For a second environment, use another machine, a VM or Kubernetes
(one namespace each).

## 2. Run the installer

```bash
git clone https://github.com/valteresj2/agent-hangar && cd agent-hangar
./scripts/install.sh                                   # Linux, macOS, WSL
```

```powershell
git clone https://github.com/valteresj2/agent-hangar; cd agent-hangar
powershell -ExecutionPolicy Bypass -File scripts\install.ps1    # Windows
```

The script checks Python and Docker, installs the `hangar` CLI in `.hangar/venv` (isolated, nothing global) and starts
the wizard. If you already have the CLI (`pip install ./cli`), run `hangar setup` instead.

## 3. What the wizard asks

| Step | Options | Notes |
|---|---|---|
| **1. Images** | *published* (recommended) · *build* | Published pulls `ghcr.io/valteresj2/*` for this version; build compiles this checkout |
| **2. Address and exposure** | *local* · *Cloudflare Tunnel* · *ngrok* · *Tailscale Funnel* | See [Exposure](#exposure) below. Local = `http://localhost:8090` |
| **3. Admin** | username, password (empty = a strong one is generated and shown once), e-mail | The e-mail also makes that person admin when they sign in with SSO |
| **4. LLM** | OpenAI · Anthropic · Azure OpenAI · Gemini · OpenRouter · DeepSeek · corporate gateway (LiteLLM, Portkey…) · Ollama · any OpenAI-compatible endpoint · later | The connection is registered and **tested with a real call**. Bedrock and Vertex AI go through a gateway such as LiteLLM. You can approve it to receive code (coding agents). *Later*: the agents answer with a test model (made-up answers) until you add an API key in **Console → Provedores de LLM**; the home pages remind you |
| **5. Agent memory** | Neo4j (default) · FalkorDB · none | Uses the LLM above to extract facts. Mem0 on pgvector (experimental) is set by hand: see [memory.md](memory.md#mem0-on-pgvector-experimental) |
| **6. Sign-in** | local accounts · Google · Microsoft Entra ID · GitHub · OAuth2 (Okta, Keycloak, Auth0…) | The wizard shows the redirect URI to register at the provider |
| **7. Extras and tools** | Data Studio · Docker MCP catalog · Activepieces · coding harnesses; code policy; AI tools to configure | Tools: Claude Code, Claude Desktop, Codex, OpenCode, Cursor, VS Code |
| **8. Example company** | yes · no | `hangar demo`: a team, a project and three Digital employees in an org chart, so the portal doesn't open empty (`hangar demo --remove` deletes it) |

## 4. When it finishes

```
── Pronto ──────────────────────────────────────
    Portal e console:   http://localhost:8090   (admin: http://localhost:8090/ui/)
    Usuário admin:      admin
    Diagnóstico:         hangar doctor
```

- **People** sign in at the address and land in the **user portal** (`/app/`). Admins also have the console (`/ui/`).
- **Your AI tools.** The configuration is in `.hangar/clients/`. It holds a `README.md` with each step, ready-to-paste
  files (`claude-code.sh`, `claude_desktop_config.json`, `codex.toml`, `opencode.json`, `cursor-mcp.json`) and your
  personal token in `token.txt`. Then ask the tool: *"create an agent that…"*.
- **VS Code.** Install the extension from `<address>/downloads/agent-hangar-vscode.vsix` and run **Agent Hangar:
  Entrar pelo portal**. No token is needed.
- **Break-glass access.** `ADMIN_TOKEN` in `.env`. Keep it safe; everyday admin work uses the admin account.

## 5. Check it: `hangar doctor`

```
⌂ Agent Hangar — diagnóstico

  ✓ Docker                   daemon respondendo
  ✓ Containers               15 rodando
  ✓ Central                  versão 0.10.0 em http://localhost:8090
  ✓ Admin                    ADMIN_TOKEN aceito
  ✓ LLM openrouter           deepseek/deepseek-v4.1-flash respondeu em 335 ms
  ✓ Memória                  neo4j · LLM deepseek/deepseek-v4.1-flash · 12 episódio(s)
  ✓ Endereço público         https://hangar.acme.com → versão 0.10.0
  ✓ TLS                      certificado válido por 71 dia(s)
  ✓ Login                    Microsoft Entra ID, usuário e senha
  ✓ MCP da plataforma        http://localhost:8090/mcp responde
  ✓ Extensão VS Code         http://localhost:8090/downloads/agent-hangar-vscode.vsix
  ✓ Agentes                  16 registrado(s) · 12 em produção · 13 em stage
```

Each failure comes with the fix (`→ …`). The command exits with `1` when something essential fails, and
`hangar doctor --json` gives machine-readable output for monitoring.

## Exposure

People's browsers, the VS Code extension and remote AI tools need to reach the hangar. Pick one option:

| Option | What you need | Address |
|---|---|---|
| **Local** | Nothing | `http://localhost:8090`, or the machine's IP on your network |
| **Cloudflare Tunnel** | A Cloudflare account and a domain. In Zero Trust → Networks → Tunnels, create a tunnel (Docker), copy the token, and add a **Public Hostname** pointing to `http://central:8080` | `https://hangar.yourcompany.com` |
| **ngrok** | The authtoken and a fixed domain (the free one works) from dashboard.ngrok.com | `https://<domain>.ngrok-free.app` |
| **Tailscale Funnel** | An auth key, with HTTPS and Funnel enabled in the tailnet policy | `https://<machine>.<tailnet>.ts.net` |

All three tunnels give HTTPS without opening ports. Each runs as one more container (`cloudflared`, `ngrok` or
`tailscale`), enabled with a Compose profile. Secure cookies turn on automatically on HTTPS. For a reverse proxy or
ingress you already run, choose *local* and set `PUBLIC_BASE_URL=https://…` in `.env`.

> **ChatGPT and Claude.ai (web):** with a public HTTPS address, paste `https://your-domain/mcp` as a custom connector.
> The app sends you to the portal to sign in and authorize (OAuth). See [clients.md](clients.md#claudeai-and-chatgpt-web-oauth-no-key-to-paste).

## Unattended install

For automation, VM images or CI, put the answers in a file:

```bash
cp setup.example.yaml setup.yaml        # edit it; it holds keys, so it is git-ignored
./scripts/install.sh --answers setup.yaml
```

[`setup.example.yaml`](../setup.example.yaml) documents every field. Add `--no-start` to only generate `.env`.

## Day 2

| Task | How |
|---|---|
| Change an answer (LLM, exposure, memory, SSO…) | Run `hangar setup` again. Previous answers are the defaults, secrets are kept, and `.env` is backed up to `.env.bak-<date>` |
| Upgrade | `git pull`, then `hangar setup` (migrations run on start). Read the CHANGELOG's *Upgrading* notes |
| Check health | `hangar doctor` (or `--json` in a cron) |
| Logs | `docker compose logs -f central` (or any service) |
| Stop / start | `docker compose stop` / `docker compose up -d` (profiles come from `COMPOSE_PROFILES` in `.env`) |

## Manual install (without the wizard)

```bash
./scripts/setup.sh              # Windows: powershell -File scripts/setup.ps1 (writes .env with random secrets)
docker compose up -d --build
```

Open `http://localhost:8090` and sign in with the `ADMIN_TOKEN` from `.env`. Configure the rest in the console:
providers, SSO, users. Optional components are Compose profiles (`--profile memory`, `--profile data-studio`,
`--profile tunnel-cloudflare`…). See `.env.example`.

## Notes

- **One installation per Docker host.** Some networks and volumes have fixed names.
- **`.env` and `.hangar/` hold secrets.** Both are git-ignored; keep them out of backups that leave the company.
