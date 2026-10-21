<div align="center">

# ⌂ Agent Hangar

**One home for every AI agent your company builds, whatever tool built it.**

People create agents where they already work: Claude, ChatGPT, Cursor, VS Code, Codex, OpenCode. Agent Hangar
brings them all to one place, runs each one in its own container, and makes them usable from **any** platform,
over **OpenAI-compatible, MCP, A2A and ACP**, and inside your own app over **AG-UI**. Everyone can find them, talk to
them, and combine them into new agents.

[![CI](https://github.com/valteresj2/agent-hangar/actions/workflows/ci.yml/badge.svg)](https://github.com/valteresj2/agent-hangar/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/valteresj2/agent-hangar)](https://github.com/valteresj2/agent-hangar/releases)
![Status](https://img.shields.io/badge/status-alpha-orange)

[Interactive demo](https://valteresj2.github.io/agent-hangar/demo/) · [Example](#from-a-request-in-claude-to-a-working-agent) · [Install](#installation) · [Features](#features) ·
[How it works](#how-it-works) · [Docs](#documentation) · [Roadmap](ROADMAP.md) · [Português](README.pt-BR.md)

</div>

<p align="center"><img src="docs/assets/portal-tour.gif" width="900"
alt="User portal tour: Ana asks Claude for a Sales Assistant. Claude builds, tests and ships it through the Hangar
MCP. Then come her Início page, the agent, a Playground answer, the team memory, the scheduled daily summary,
connections and the company catalog."></p>
<p align="center"><sub>The user portal, recorded on a local install · <a href="docs/assets/portal-tour.mp4">MP4 version</a></sub></p>

### Watch: Agent Hangar end to end

https://github.com/user-attachments/assets/62836990-f66c-4c94-950b-4632cf9fc160

<sub>2 min 45 s, with captions: what Agent Hangar is, how to install it, build, test and ship agents, use them over every protocol (inside your app too), put Digital employees to work, and keep cost and governance under control. [MP4 version](docs/assets/agent-hangar-end-to-end.mp4)</sub>

<details><summary><b>Step-by-step tutorial: from zero to an agent at work, in 9 steps</b> (8 min 34 s, with captions)</summary>

**Part 1 (0:00–3:53):** install in one command, register the company and the model, create the agent from the tool you
already use: chat agents, harness agents (Claude Code, Codex) for complex problems, and agents from other frameworks
over AG-UI.

https://github.com/user-attachments/assets/7bf76ac8-2302-4822-9dc3-ddc10a5e0348

**Part 2 (3:53–6:25):** test in stage and ship with approval, put the agent inside your app over AG-UI, hire a Digital
employee, and work through the issues board, with the approval showing up in Slack, Teams or on your app's screen.

https://github.com/user-attachments/assets/c6ea0048-01c6-45df-a612-62af4e4a2851

**Part 3 (6:25–8:34):** connect tools and skills, pick the right model and effort, and keep cost and audit under
control.

https://github.com/user-attachments/assets/28cfa80f-33b1-49c5-be48-13cc33ada18d

</details>

> **Status: alpha (v0.47).** It works end to end and is covered by tests, but APIs may still change. Run it inside
> your network until you have read [docs/security.md](docs/security.md).

## Why Agent Hangar

Today every AI tool keeps its own agents. A GPT lives in ChatGPT, a Claude project stays in Claude, a Cursor rule
stays in Cursor. Nobody else in the company finds them, they can't be reused anywhere else, and no one knows what
they cost or who approved them. Agent Hangar fixes that:

- **Build anywhere, keep in one place.** Ask Claude, ChatGPT, Cursor, VS Code, Codex or OpenCode for an agent. The
  tool builds it through the hangar's MCP server, and the agent lands in a single company catalog, versioned and
  owned by someone.
- **Platform-agnostic.** An agent built in Claude can be used in ChatGPT, Cursor, VS Code, LibreChat, Open WebUI,
  Slack-style bots, scripts or another agent. Each agent is a standard endpoint (OpenAI-compatible, MCP, A2A, ACP,
  AG-UI), not a feature locked inside one vendor.
- **Everyone can find and talk to them.** The catalog shows every agent, what it does and who owns it. People try
  agents in the portal's playground, request access, and plug them into their own tools in one click. Access
  follows teams, roles and approvals.
- **Agents combine into new agents.** Put existing agents together as a team: a coordinator delegates to
  specialists (a researcher, an analyst, a writer), each keeping its own instructions, tools and model. The result
  is a new agent with a different role, reusing work that already exists.
- **Reuse before you build.** Before creating an agent, the hangar searches the catalog (semantically, across
  languages) for agents, skills and MCPs that already do part of the job. It builds the new agent from those pieces
  and asks only for the skills that are missing. The pieces are read-only: nothing in them ever changes.
  [docs/composer.md](docs/composer.md)
- **Safe to run in a company.**
  - Every change is a new version, tested in stage before it reaches production.
  - Secrets stay encrypted, and the platform is self-hosted (Docker or Kubernetes).
  - Usage, cost and budgets are tracked per agent and team, with an audit log and SSO.
- **Agents that remember and work alone.** Long-term memory per agent and per team, schedules for recurring
  work, and coding agents that edit projects in VS Code.

In short: **your agents stop belonging to a tool and start belonging to your company.**

---

## How it compares

How Agent Hangar compares with CrewAI AMP, LangSmith Deployment, Dify, AWS Bedrock AgentCore and Microsoft Foundry
with Agent 365, as of October 3, 2026. "Not found" means the documentation consulted does not describe the feature;
it does not prove the feature is missing. Where each one is ahead, the model options and the sources:
[docs/comparison.md](docs/comparison.md).

| Criterion | Agent Hangar | CrewAI AMP | LangSmith Deployment | Dify | AWS Bedrock AgentCore | Microsoft Foundry + Agent 365 |
| --- | --- | --- | --- | --- | --- | --- |
| What it is | Self-hosted control plane for agents from any source | Commercial control plane of the CrewAI framework | Agent runtime (Agent Server) with observability and evaluation | Visual platform for LLM apps, agents and RAG | AWS services: Runtime, Gateway and Registry | Azure managed service, with the registry in Agent 365 |
| Hosting | Self-hosted: Docker and Kubernetes (GKE, AKS, EKS) | AMP Cloud or AMP Factory (self-hosted on AWS, Azure or GCP) | Cloud, hybrid or self-hosted (Enterprise plan) | Cloud, VPC or self-hosted (Community, Enterprise) | AWS cloud | Azure cloud |
| LLMs that power the agents | Any, through connections: OpenAI, Anthropic, Azure OpenAI, Gemini, OpenRouter, DeepSeek, Ollama, any OpenAI-compatible endpoint and gateways (LiteLLM, Portkey) | Several providers through litellm, with Azure and Bedrock integrations | Defined by the agent's framework; the runtime does not tie you to a provider | Broad; providers installed from the plugin Marketplace | The Gateway gives access to LLMs; model list not verified | Catalog of 10,000+ models (OpenAI, Anthropic, Meta, DeepSeek and others) |
| Price and license | Apache-2.0; no license cost, you pay for infrastructure and LLM tokens | OSS framework; AMP with a free Basic plan and Enterprise on request, billed per execution | Self-hosted requires the Enterprise plan and a license key; prices not found | Community is free; Cloud from US$59 to US$159 per workspace/month; Enterprise on request | Not verified | Not verified |
| Agents made in Claude, ChatGPT, Cursor, Codex go to one catalog | Yes, that is the focus: the tool creates the agent through the hangar's MCP | Partial: an MCP server exposes AMP deploy operations to clients such as Claude | Partial: deploys LangGraph, Deep Agents and other frameworks | Not found | Yes, for MCP, A2A and agent cards synced by URL (preview) | Partial: agents from Foundry, Copilot Studio and registered by an admin |
| Agent catalog | Yes: owner, version, access requests, playground | Yes: Agent Repositories and Marketplace | Not found | Plugin marketplace, not an agent catalog | Yes: Agent Registry, with optional manual approval | Yes: Agent 365 registry and Entra Agent Registry |
| Platform-agnostic access | OpenAI-compatible, MCP, A2A, ACP and AG-UI, per agent | A2A (0.2 and 0.3, early release) and a REST API per crew | Native MCP and A2A | API, web app, embed and MCP tool | MCP through the Gateway and A2A in the Runtime | A2A 1.0, Responses and Activity; MCP through the Toolbox |
| Portability and lock-in | Low: YAML spec with JSON Schema, GitOps (`hangar apply`), standard endpoints, self-hosted | Medium: framework code is portable; the managed deploy belongs to AMP | Medium: LangGraph is open and there is a standalone Agent Server with Docker; the full platform is Enterprise | Medium: apps export as YAML DSL; self-hosted available | Open protocols (MCP, A2A), but the runtime and Registry stay on AWS | Accepts any framework and A2A, but hosting and registry stay on Azure and Microsoft 365 |
| Agents combined into new agents | Yes: `sub_agents` over A2A, with permission checks | Yes: crews, the core of the framework | Yes: RemoteGraph through MCP and A2A | Yes: workflows with chained agents | Yes: A2A between agents | Yes: A2A tool |
| Tool catalog | 230+ MCP servers from the Docker catalog; remote MCPs with OAuth | Tool Repository and its own MCP servers | Not found | Marketplace with tools and MCP integrations | The Gateway aggregates APIs, Lambdas and MCPs into a virtual MCP | Toolbox: a single MCP endpoint, with governance and versioning |
| Coding agents and IDE | Harnesses (Claude Code, Codex, Hermes, DeepSeek Harness) as agents; VS Code extension; Cline, Roo Code and Continue | Partial: a skills plugin that teaches CrewAI to coding agents | Not found | Not found | Not found | Not found |
| Promotion gated by tests | Yes: immutable versions, stage test gate, approval by a second maintainer, rollback | Not found | Evaluation in the same stack; promotion gate not found | Not found | Not found | Versions agents and creates stable endpoints; gate not found |
| Long-term memory | Temporal graph per agent, team or org (Graphiti) | Memory and knowledge in the framework | Per-thread state, durable execution | Knowledge base (RAG) | Not verified | Memory as a native tool |
| Governance and cost | SSO, SCIM, roles, corporate e-mail rule, audit, DLP and retention, budgets per company, team, agent or project with a hard stop, cost per agent | RBAC and security controls | Custom auth; ABAC when self-hosted | SSO/SAML, RBAC and audit (Enterprise) | IAM/JWT and Cedar policies in the Gateway | Entra ID and Agent 365 controls |
| Data residency and compliance | Data on your infrastructure, encrypted secrets, signed images with SBOM; no certifications cited | On-premises or cloud deployment; certifications not found | Self-hosted for data residency and isolated (air-gapped) environments | Self-hosted keeps data in-house; Enterprise with SOC 2 Type II and ISO 27001 | Not verified | Not verified |
| Observability | Prometheus, JSON logs, OpenTelemetry, metrics per channel | Real-time observability | Strong point: traces and evaluation | Built-in observability | AgentCore Observability and CloudWatch | Application Insights and Agent 365 telemetry |
| Maturity | Alpha (v0.47), Apache-2.0 | OSS with a commercial layer | Commercial, partly Enterprise | 157,000+ GitHub stars | Registry in public preview | Agent 365 generally available; A2A 1.0 GA |

## From a request in Claude to a working agent

A real run on a local install. Ana is a regular user: she maintains the *Comercial* (sales) team and is **not** an
admin. She connected Claude to the hangar's MCP server with her personal token.

**1. She asks.**

> *"Sou a Ana, do time Comercial. Crie no Agent Hangar um agente chamado “Assistente Comercial” para o nosso time
> acompanhar clientes (…). Use a conexão openrouter-deepseek, dê memória compartilhada com o time, inclua testes,
> publique em produção e agende para todo dia útil às 8h um resumo dos clientes com os próximos passos."*
>
> (It translates to: create a sales assistant that tracks clients, give it team memory, add tests, publish it, and
> schedule a daily summary with next steps for weekdays at 8am.)

**2. Claude builds it through the hangar's MCP tools.** The hangar's instructions tell the model which steps to
take and in what order. Ana's permissions apply to every call.

| Step | MCP tool | What happened |
|---|---|---|
| Who am I? | `whoami` | Ana Souza, maintainer of *Comercial* |
| What can I reuse? | `list_catalog` | The `openrouter-deepseek` LLM connection |
| Register | `register_agent` | `assistente-comercial`, owned by *Comercial* |
| Design | `design_agent` | Instructions, LLM, `memory: {scope: team}` and 3 LLM-as-judge tests (v2) |
| Test and ship | `ship_agent` | Stage, then **9/9 checks passed**, then production (43 s) |
| Schedule | `schedule_agent` | "Daily client summary", weekdays at 08:00 (America/Sao_Paulo) |

<p align="center"><img src="docs/assets/portal/claude.png" width="820" alt="The conversation in Claude: the
request, the six MCP tool calls and Claude's summary"></p>

**3. The team uses it.** Ana records the day's news: ACME upgraded to Enterprise; Beta Transportes is on a Starter
trial until 15/10 and its CFO asked for a Pro proposal; Gama Foods renews on 10/11 and complained about support.
The agent writes each item to the **team memory**, a temporal knowledge graph where changed facts keep their
history.

| Her Início page in the portal | Team memory, with dated facts |
|---|---|
| ![Início](docs/assets/portal/inicio.png) | ![Memory](docs/assets/portal/memoria.png) |

**4. The result.** In a new conversation, *"Which clients need contact this week, and why?"* is answered from
memory. At 8am on weekdays, the scheduled run sends the summary on its own:

> **1. Beta Transportes: most urgent.** Starter trial ends on **15/10/2026**; the CFO Rafael Lima asked for a Pro
> proposal. **Next step:** contact **Rafael Lima (CFO) this week, before 15/10**, to present the Pro proposal and
> convert the trial.
> **2. Gama Foods.** Pro since March/2026, **renews on 10/11/2026**; complained about support response times.
> **Next step:** reach out within two weeks to fix the support issue before the renewal (…)

| The Playground answer | The scheduled daily summary (the final result) |
|---|---|
| ![Playground](docs/assets/portal/playground.png) | ![Daily summary](docs/assets/portal/resumo-agendado.png) |

The run was recorded with Claude as the MCP client, with Ana's personal token. The agent's own model is
DeepSeek via OpenRouter. The scripts that reproduce it are in
[scripts/demo/portal-tour](scripts/demo/portal-tour/).

## Installation

A guided installer asks a few questions and does the rest: it writes `.env`, starts the stack, registers and **tests**
your LLM, creates the admin and generates the configuration of your AI tools. Full guide:
[docs/install.md](docs/install.md).

**1. Requirements.** Docker (Desktop or Engine) with Compose 2.20+, and Python 3.10+ to run the installer. Intel/AMD
and ARM64 (Apple Silicon, Graviton) are both supported.

**2. Run the installer.**

```bash
git clone https://github.com/valteresj2/agent-hangar && cd agent-hangar
./scripts/install.sh                                              # Linux, macOS, WSL
# Windows: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

**3. Answer seven questions** (each has a sensible default):

| | Choices |
|---|---|
| Images | published (recommended) · build from this checkout |
| Address | local · **Cloudflare Tunnel** · **ngrok** · **Tailscale Funnel** (public HTTPS without opening ports) |
| Admin | username and password (or a generated one) |
| LLM | OpenAI · Anthropic · Azure OpenAI · Gemini · OpenRouter · DeepSeek · corporate gateway (LiteLLM, Portkey…; also the way to Bedrock/Vertex) · Ollama · any OpenAI-compatible endpoint |
| Memory | Neo4j · FalkorDB · none |
| Sign-in | local accounts · Google · Microsoft Entra ID · GitHub · OAuth2 (Okta, Keycloak, Auth0…) |
| Extras and tools | Data Studio, Docker MCP catalog, Activepieces, coding harnesses; code policy; configs for Claude Code, Claude Desktop, Codex, OpenCode, Cursor, VS Code |

**4. Open it.** Go to the address shown at the end (e.g. `http://localhost:8090`) and sign in. Your AI tools'
configuration is in `.hangar/clients/` (start with its `README.md`). Then ask the tool: *"create an agent that…"*.

**5. Check it.** `hangar doctor` checks Docker, the central, each LLM (with a real call), memory, the public address
and TLS, sign-in, MCP and the VS Code extension, and tells you how to fix anything that fails.

To install without questions (automation, VM images), use `./scripts/install.sh --answers setup.yaml`, with
[`setup.example.yaml`](setup.example.yaml) as the model. To change an answer or upgrade, `git pull` and run
`hangar setup` again: previous answers are the defaults and secrets are kept.

### On a cloud VM (AWS, Azure, Google Cloud)

One VM with Docker runs the same stack: create an Ubuntu 24.04 VM with SSH open only to you, install Docker Engine,
run the installer and pick a tunnel for HTTPS (no inbound ports). Step-by-step commands for EC2, Azure Virtual
Machines and Compute Engine, each linked to the provider's documentation: [docs/cloud-vm.md](docs/cloud-vm.md).

### On Kubernetes (GKE, AKS, EKS)

The same installer deploys the Helm chart (`charts/agent-hangar`); agents become Deployments and harness runs become
Jobs, isolated by NetworkPolicies. Full guide: [docs/kubernetes.md](docs/kubernetes.md).

1. **Requirements:** `kubectl` pointing at the cluster, Helm 3.12+, Python 3.10+.
2. **Run** `./scripts/install.sh --target kubernetes` (or `hangar setup --target kubernetes`).
3. **Answer:** cloud (`gke` · `aks` · `eks` · `local`, which picks the preset), namespace, image registry (or your
   mirror), access (**Ingress with TLS** · **Cloudflare Tunnel** · port-forward), Postgres (in the cluster or
   **managed**: Cloud SQL, Azure Database, RDS), then the same admin/LLM/memory/sign-in/tools questions.
4. **Check:** `hangar doctor --namespace agent-hangar`.

For high availability, run 2+ central replicas with a managed Postgres: rolling updates without downtime, and
shared state in the database. To create everything (cluster, managed Postgres, cloud identity, ingress) in one
`terraform apply`, use [`deploy/terraform`](deploy/terraform/README.md) for GKE, AKS or EKS.

Plain Helm works too: `helm upgrade --install agent-hangar charts/agent-hangar -n agent-hangar --create-namespace
-f charts/agent-hangar/values-gke.yaml -f my-values.yaml`.

<details><summary>Manual install, without the wizard</summary>

```bash
./scripts/setup.sh              # Windows: powershell -File scripts/setup.ps1 (writes .env with random secrets)
docker compose up -d --build    # hangar + Postgres + agent runtime + mock harness
```

Open **http://localhost:8090**, sign in with the `ADMIN_TOKEN` from `.env`, add an LLM under **Provedores de LLM** and
connect your AI client with a personal token from **Minhas chaves**:

```bash
claude mcp add --transport http agent-hangar http://localhost:8090/mcp --header "Authorization: Bearer <key>"
```

Other clients are covered in [docs/clients.md](docs/clients.md).
</details>

### Two web apps

| | Who | What |
|---|---|---|
| **`/app/`**, the user portal | Everyone who is not a platform admin | **Início** (what needs attention, your agents, requests, budget, usage, schedules, connections, memory, what's new), your agents with Playground, Connect, Schedules and Memory, the company catalog, approvals, keys and teams |
| **`/ui/`**, the admin console | Platform admins only | Everything: LLM providers, the MCP catalog, deployments and tests across the company, audit, users, SSO/SCIM |

Everyone signs in on the same page and lands in the right app. Every API call still checks permissions. See
[docs/access.md](docs/access.md#where-each-person-lands).

Both apps are available in **English and Portuguese**. The first visit follows the browser's language, and the
**PT · EN** switch (sidebar footer and sign-in page) changes it; the choice is kept in that browser. What people and
agents wrote (agent names and descriptions, conversations, test outputs, audit details) is shown as written. Messages
generated by the server (API errors, the MCP instructions, the composer's recommendation) are still in Portuguese.

## Features

**Build where people already work.** The hangar is an MCP server, so Claude, ChatGPT, Codex or OpenCode *are* the
builder. They choose between a chat agent, a coding harness or a multi-agent team from the objective. You can also
start from a template or the UI, and edit a live agent later: `edit_agent` makes a new version, tests it in stage
and publishes it only when you say so. Claude.ai and ChatGPT on the web connect by pasting only the `/mcp` URL: the
person signs in and authorizes (OAuth), with no key to copy.

**Nothing reaches production untested.**
- Every change creates an immutable version.
- Promotion is blocked unless that exact version passed its stage tests: protocol smoke tests,
  `expect_contains`/`regex` and **LLM-as-judge** rubrics.
- Teams can require a second maintainer's approval (four eyes).
- Rollback is one call.

**One container per agent, one per job.**
- Chat agents run in hardened containers: read-only file system, all capabilities dropped, memory/CPU/PID limits.
- Coding harnesses (Claude Code, Codex, Hermes, DeepSeek Harness) run in a **fresh container per call**, which
  returns the result and the `git diff`. The Codex, Hermes and DeepSeek images are published. The Claude Code image
  is built locally from this repository, because Claude Code's license does not allow redistributing it; the installer
  does it for you. See [docs/harnesses.md](docs/harnesses.md#the-claude-code-image-is-not-published).

**Coding agents in VS Code.** With the **Agent Hangar extension**, the agents you can use appear in VS Code's chat
model picker. In Agent mode they read, edit and test your project with the editor's own tools, on your machine and
with your approval, while the hangar supplies their instructions, skills, MCPs and memory. Sign-in is one click
through the portal. Cline, Roo Code and Continue work too. See [docs/clients.md](docs/clients.md#vs-code-extension-agent-hangar).

**Every protocol.** Each agent answers on `/v1/chat/completions` (OpenAI-compatible, with streaming), **A2A**
(Agent Card + `message/send`), **ACP**, **MCP** and **AG-UI**. All of it sits behind one authenticated gateway with
per-channel metrics. The **Connect** tab gives a revocable key per tool and the config to paste.

**Agents inside your own app (AG-UI).** Put any agent in your product with [CopilotKit](https://www.copilotkit.ai) or
any [AG-UI](https://docs.ag-ui.com) client. The answer streams token by token, with the model's reasoning, each tool
call and its result. The agent can call your app's tools and change the state on screen (JSON Patch). A Digital
employee keeps its authority: an action that needs approval pauses the run with an interrupt, and the decision stays
with the person the authority names. See [docs/clients.md](docs/clients.md#ag-ui-the-agent-inside-your-app).

**Agents from any framework (remote agents).** An agent built with LangGraph, Google ADK, Mastra, Microsoft Agent
Framework or AWS Strands is governed by the hangar without moving its code: add its AG-UI address to the spec.
It gets a version, stage tests before production, budget, metering and audit, and becomes reachable over OpenAI,
A2A, ACP, MCP and AG-UI. Credentials come only from the vault. See
[docs/clients.md](docs/clients.md#agents-from-other-frameworks-remote-ag-ui-agents).

**The right effort for every model.** Each agent has an execution profile: reasoning effort, output limit, parallel
tools and prompt cache, sent in each provider's dialect and to each harness's flags. The effort starts from the
agent's role, the hangar measures cost and accuracy per model and effort, and a comparison in stage suggests a
cheaper or a stronger level with the numbers. Nothing changes on its own. See [docs/execution.md](docs/execution.md).

**Issues, for people and agents.** A kanban board (board, list, table, timeline) where an issue assigned to a
Digital employee becomes work under its authority and budget: it comments as it goes and hands the result over for
review. Each person sees their own issues; the console is the company's read-only view. Issues are never deleted.
See [docs/issues.md](docs/issues.md).

**Tools for agents.**
- **Connectors**, in the layout of ChatGPT's catalog: remote MCP servers (with OAuth), local servers over stdio in
  an isolated container, or a `mcpServers` JSON imported from Claude Desktop or Cursor. Each tool is approved one by
  one, and a tool that is new or changes stays blocked until an admin approves it
  ([docs/connectors.md](docs/connectors.md)).
- **230+ prebuilt MCP servers** from Docker's official catalog, each in an isolated container
  ([docs/mcp-catalog.md](docs/mcp-catalog.md)).
- **Remote MCPs with OAuth,** such as Activepieces with 280+ business apps, Notion or Linear. They are connected
  once, and the tokens are kept and refreshed by the hangar ([docs/remote-mcp.md](docs/remote-mcp.md)).
- **Skills** in the style of Claude's Skills: upload a `SKILL.md` or a package, with a security check that blocks
  secrets and flags instruction overrides; Skill Studio keeps versions and test cases.
- **Plugins** connect any system with a declarative manifest: tools, a team credential and action types for the
  authority, reviewed by four eyes ([docs/plugins.md](docs/plugins.md)).
- **Data Studio:** Python and DuckDB analysis that produces dashboards and decks.

**A guide for every agent.** Each agent has a **Guide** tab for the people who will use it.
- A flow diagram built from the spec: request, the agent, the skills, tools, memory and specialists it uses, and what
  it delivers.
- A fact sheet with real example requests (the test cases that passed) and where to call it.
- A Markdown text: what it is, what it does, what it does not do, how to use it and its limits.

The AI tool that built the agent writes the text. If a version reaches production without one, the agent's LLM
writes a draft, marked for review until a maintainer approves it. See [docs/guide.md](docs/guide.md).

**Digital employees (agent as employee, with a human in the loop).** Hire an agent for a job instead of only calling
it. It has a job profile, a human manager and an **authority level**; it takes tasks and works on them in the
background.
- **Hiring in self mode:** your AI tool asks you for whatever is missing over MCP (`plan_employee` → `hire_employee`)
  and creates nothing until the job is complete.
- **Probation, then admission:** it first does real tasks in stage; the manager reads the results and admits it to
  production.
- **Authority enforced by the platform, not the prompt:** before every tool call the runtime asks the central. Each
  action type (read, send outside, money, delete…) is done on its own, done with a notice, approved by one or two
  people, or never done, and a company floor always applies.
- **Human in the loop:** the task pauses on the exact action (tool and arguments) and the manager approves, edits,
  rejects or instructs. Unanswered requests go to a backup and then expire without the action being done.
- **Where to manage it:** owners and managers have their own space in the portal (*Digital employees*, *Decisions*);
  admins have the workforce view, the action catalog and the company floor in the console. Daily reports can go to
  Slack or Teams, whichever your company uses.
- **Day-to-day work on its own:**
  - routines from a cron;
  - tasks from other systems through an inbound webhook, without duplicates;
  - goals measured against targets in a weekly report;
  - suggestions learned from the manager's decisions: loosen what is always approved, and turn repeated corrections
    into lessons.

See [docs/digital-employee.md](docs/digital-employee.md).

**Long-term memory.** Add `memory: {scope: agent | team | org}` and the agent remembers customers, decisions and
preferences across conversations ([docs/memory.md](docs/memory.md)).
- The memory is a **temporal knowledge graph** (Graphiti on Neo4j Community or FalkorDB). A fact that changes is
  kept as history, not overwritten.
- The hangar decides which memory each agent reads and writes, and stage never pollutes production.
- Embeddings are computed locally.

**Schedules.** Agents run on their own, on a cron or at a date and time. Results go to history and, optionally, to
a Slack or Teams webhook.

**Teams and governance.**
- Sign-in with Google, Microsoft Entra ID, GitHub or OAuth2, plus local accounts, and SCIM provisioning.
- Company registration on the admin's first access, with the company's e-mail domains and an optional rule that only
  corporate e-mails join, for every way in (invites, SSO, SCIM).
- Maintainer, developer and consumer roles; a company catalog with access requests.
- Budgets per company, team, agent or project, by day or month, with a warning and a hard stop.
- Data governance: classification, DLP, retention and data subject requests ([docs/analyst.md](docs/analyst.md#data-governance)).
- A secrets vault (`secret://`), scoped API keys, an audit log, GitOps (`hangar apply -f agents.yaml`) and a JSON
  Schema for the spec. One installation can serve several isolated companies.

**Bring your own LLM gateway.** The hangar never hosts a model. Agents call your LiteLLM, OpenRouter or provider
through *connections*: URL, model and key, encrypted at rest. Connections can differ per environment and track cost
in US$.

## How it works

```mermaid
flowchart LR
  subgraph People
    B[Claude · ChatGPT · Codex · OpenCode]:::c
    U[User portal /app · admin console /ui]:::c
    T[LibreChat · Open WebUI · Slack · SDKs]:::c
  end
  B -- "MCP /mcp (personal or admin key)" --> H
  U -- "session (SSO or local)" --> H
  T -- "/gw/&lt;slug&gt; OpenAI · A2A · ACP · MCP · AG-UI" --> H
  subgraph Hangar[Agent Hangar]
    H[API · gateway · registry<br/>versions · tests · approvals · audit · usage/cost · schedules]
    DB[(Postgres)]
    M[memory service<br/>Graphiti]
    G[(Neo4j / FalkorDB)]
    H --- DB
    H -- "groups chosen by the hangar" --> M --- G
  end
  H --> P[docker-socket-proxy]
  P --> A1[agent container<br/>stage / prod]
  P --> J[ephemeral job container<br/>claude-code · codex · hermes · dsh]
  A1 -- "memory, sub-agents, OAuth MCPs<br/>via /internal (per-agent token)" --> H
  A1 & J --> L[(Your LLM gateway / provider)]
  H -- "AG-UI" --> R[remote agent<br/>LangGraph · ADK · Mastra…]
  classDef c fill:#eef2ff,stroke:#4f46e5
```

| Concept | In one line |
|---|---|
| **Agent** | Name + objective + expected output + a versioned **spec** (instructions, skills, MCPs, tools, sub-agents, memory, tests) |
| **Chat agent** | An LLM loop with tools, always on, in its own container |
| **Harness agent** | Hands each task to Claude Code / Codex / Hermes / DeepSeek Harness in a throwaway container |
| **Multi-agent** | A chat agent with `sub_agents`: it delegates over A2A through the hangar, which checks it is allowed |
| **Remote agent** | An agent that runs in another framework and speaks AG-UI; the hangar governs it and bridges every protocol to it |
| **Digital employee** | An agent hired for a job, with a human manager and an authority level the platform enforces on every action |
| **Connection** | An external LLM endpoint + default model + key (encrypted) + optional price |
| **Stage → prod** | `ship` = deploy to stage → run tests → record → promote only if green (and approved, if the team requires it) |

## Templates

| Template | Type | What it shows |
|---|---|---|
| `doc-qa` | chat | Grounded Q&A over a document, with citations and refusal to invent |
| `platform-dashboard` | chat + builtin tool | An ops agent that reads the hangar's own live metrics |
| `ticket-triage` | chat | Structured JSON classification + first reply, with regex and judge tests |
| `sql-analyst` | chat (+ your DB MCP) | Read-only SQL generation with safety rules |
| `code-assistant` | chat, coding agent | For VS Code / Cline / Continue: edits and tests your project; ships only after passing two real code evaluations |
| `code-fixer` | harness (Codex) | Fixes code in an ephemeral container and returns a diff |
| `data-studio` | chat + Data Studio MCP | Python/DuckDB analyst: spreadsheets, PDFs and images in; dashboards and PPTX/HTML decks out |
| `research-team` | multi-agent | Orchestrator + researcher + critic + writer over A2A |

Apply one with `hangar templates apply doc-qa --connection my-litellm`, or from **Templates** in the UI. Templates
are plain YAML in [`templates/`](templates/), and they are the easiest way to contribute.

## Use a shipped agent

```bash
hangar keys create librechat --scope invoke --agent doc-qa     # a key that can only call this agent

curl http://localhost:8090/gw/doc-qa/v1/chat/completions \
  -H "Authorization: Bearer ah_..." -H "Content-Type: application/json" \
  -d '{"model":"doc-qa","messages":[{"role":"user","content":"What is the hotel limit?"}]}'
```

| Protocol | Endpoint |
|---|---|
| OpenAI-compatible | `POST /gw/<slug>/v1/chat/completions` (streaming supported) |
| A2A | `GET /gw/<slug>/.well-known/agent.json`, `POST /gw/<slug>/a2a` |
| ACP | `POST /gw/<slug>/acp/runs` |
| MCP | `POST /gw/<slug>/mcp` (one tool named after the agent) |
| AG-UI | `POST /gw/<slug>/agui` (a `RunAgentInput`; the answer is a stream of AG-UI events) |

Use `/gw-stage/<slug>/…` for the stage version. Send `X-Channel: slack` (or any name) for per-channel metrics.

### Data Studio + LibreChat

An agent as the **engine behind a chat UI**. [LibreChat](https://librechat.ai) talks to the `data-studio` agent
through the gateway, and each conversation gets its own workspace and Python kernel in the
[Data Studio MCP server](mcp-servers/data-studio/). See [integrations/librechat](integrations/librechat/).

| Answer in LibreChat | Generated dashboard | Generated deck (PPTX + HTML) |
|---|---|---|
| ![answer](docs/assets/librechat-answer.png) | ![dashboard](docs/assets/dashboard.png) | ![slides](docs/assets/slides.png) |

## CLI and GitOps

```bash
pip install ./cli
hangar login http://localhost:8090 --token <key>
hangar apply -f examples/agents.yaml --ship
hangar jobs run code-fixer "Add input validation to parse_date()" --follow
```

## Documentation

| | |
|---|---|
| [Installation](docs/install.md) | Guided installer, exposure (Cloudflare, ngrok, Tailscale), unattended install, `hangar doctor`, upgrades |
| [Concepts](docs/concepts.md) · [Spec reference](docs/spec.md) · [Templates](docs/templates.md) | What an agent is and how to describe one |
| [Clients](docs/clients.md) | Connecting Claude, ChatGPT, Codex, OpenCode, Cursor, VS Code, LibreChat, Open WebUI; AG-UI in your app; remote agents |
| [Access, teams, SSO and the portal](docs/access.md) | Roles, approvals, budgets, OAuth2/SCIM, where each person lands |
| [Issues (kanban board)](docs/issues.md) | The board for people and Digital employees: assign an issue and the agent works on it, with authority, budget and review |
| [Member workspace](docs/workspace.md) | The member's portal: personal agents, visual org chart, connect my AI, sign-ins and activation codes, installable app with notifications |
| [Memory](docs/memory.md) · [Connectors](docs/connectors.md) · [MCP catalog](docs/mcp-catalog.md) · [Remote MCPs + Activepieces](docs/remote-mcp.md) | What agents can know and use |
| [Harnesses](docs/harnesses.md) | Claude Code, Codex, Hermes and DeepSeek Harness as agents |
| [Execution profile](docs/execution.md) | Reasoning effort, output limit, parallel tools and prompt cache, per provider and harness |
| [Reuse before you build](docs/composer.md) | New agents from existing ones: plan, pieces, skills, read-only rules |
| [Agent guide](docs/guide.md) | The Guide tab: flow from the spec, fact sheet, the users' guide and its drafts |
| [Plugins](docs/plugins.md) | Connect any system with a declarative manifest: tools, team credential, action types for the authority, review and install per team |
| [Digital employee](docs/digital-employee.md) | Agents with a job, a manager and an authority level: self-mode hiring, probation, tasks, decisions, reports |
| [The analyst's day to day](docs/analyst.md) | Data analysis and deliverables, document extraction, calculators, playbooks, queues, knowledge base, governed browser and desktop, data governance |
| [Day-to-day work](docs/work.md) · [Feature map](docs/feature-map.md) | Goals, projects, the task's conversation, budgets, inbox, files, companies; every feature by who uses it |
| [Cloud VM](docs/cloud-vm.md) · [Comparison](docs/comparison.md) | Docker on AWS, Azure or Google Cloud; how Agent Hangar compares with other platforms |
| [Kubernetes](docs/kubernetes.md) · [Terraform](deploy/terraform/README.md) | GKE, AKS, EKS: chart, high availability, vault secrets, cloud identity, one-`apply` stacks |
| [Observability](docs/observability.md) · [Backup and upgrade](docs/backup.md) · [Supply chain](docs/supply-chain.md) | Metrics, logs, traces; `hangar backup`/`upgrade`; signed images and SBOM |
| [API](docs/api.md) · [CLI](docs/cli.md) · [Security](docs/security.md) | Reference and hardening |

## Project status and roadmap

Agent Hangar is in **alpha**: it works end to end, every release runs a full end-to-end suite and a clean-machine
install, and APIs may still change. Recent releases:

| Version | What it added |
|---|---|
| v0.47 | Personal subscriptions for harness jobs removed: every harness agent runs on its API connection again |
| v0.46 | Harness jobs on a personal Claude or ChatGPT subscription, for the jobs a person starts, with plan limits handled and the API cost saved shown |
| v0.45 | Digital employees: a model from another vendor reviews an action before it runs, and anything it doesn't approve goes to a person |
| v0.44 | Digital employees: loops close quietly when they find nothing, and findings reach a person with a short summary |
| v0.43 | Remote agents: the company's DLP on the way to the agent, and the tokens and cost it reports in metering and budgets |
| v0.42 | Per-agent CORS: a web page on an allowed site calls the agent straight from the browser |
| v0.41 | CopilotKit example app (Next.js, v2 API), tested in a real browser in CI |
| v0.40 | Remote agents: agents from LangGraph, Google ADK, Mastra and other AG-UI frameworks, governed by the hangar |
| v0.39 | AG-UI streaming token by token, the model's reasoning, state shared with the app, sub-agents in the stream |
| v0.38 | AG-UI: any agent inside your app (CopilotKit), with a Digital employee's approvals as interrupts |
| v0.37 | Company registration on first access, with an optional corporate e-mail rule |
| v0.36 | Execution profile (effort, output limit, parallel tools, prompt cache), measured and compared per model |
| v0.35 | Local MCP servers (stdio) as connectors, in an isolated container |
| v0.33–v0.34 | Connectors in the layout of ChatGPT's catalog, with tools approved one by one; Skills in the style of Claude's |
| v0.32 | LLM providers page with the model list, prices and capabilities per provider |
| v0.30–v0.31 | Issues board for people and Digital employees; console menu by subject; Data Studio and the browser in Helm |

Earlier releases built the base: the registry with the stage gate (v0.1), teams and SSO (v0.3), memory (v0.6), the
portal (v0.7), coding agents in VS Code (v0.8), the installer and Kubernetes (v0.10), observability and signed images
(v0.12), Digital employees (v0.16–v0.24), several companies per installation (v0.25), the analyst's day to day
(v0.26) and the governed desktop (v0.27). Everything is in the [CHANGELOG](CHANGELOG.md); what comes next is in
[ROADMAP.md](ROADMAP.md).

## Contributing

Issues and PRs are welcome. Templates, harness adapters and docs are great first contributions. Read
[CONTRIBUTING.md](CONTRIBUTING.md). Report security issues through [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE).
