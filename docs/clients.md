# Connecting clients

Two different things connect to the hangar:

1. **Builders.** AI clients that create and manage agents use the hangar's MCP at `/mcp` with an **admin** key.
2. **Consumers.** Apps and people using a shipped agent go through `/gw/<slug>/…` with an **invoke** key limited
   to that agent (`hangar keys create my-app --scope invoke --agent <slug>`).

Replace `<key>` below. The UI's **Connect** page shows these snippets with your URL.

## Builders (platform MCP)

**Claude Code**
```bash
claude mcp add --transport http agent-hangar http://localhost:8090/mcp --header "Authorization: Bearer <key>"
```

**Claude Desktop** (`claude_desktop_config.json`, via [mcp-remote](https://www.npmjs.com/package/mcp-remote))
```json
{"mcpServers": {"agent-hangar": {"command": "npx", "args": ["-y", "mcp-remote", "http://localhost:8090/mcp",
  "--header", "Authorization: Bearer <key>", "--allow-http"]}}}
```

**Codex CLI** (`~/.codex/config.toml`)
```toml
[mcp_servers.agent-hangar]
url = "http://localhost:8090/mcp"
http_headers = { Authorization = "Bearer <key>" }
```

**OpenCode** (`opencode.json`)
```json
{"mcp": {"agent-hangar": {"type": "remote", "url": "http://localhost:8090/mcp",
  "headers": {"Authorization": "Bearer <key>"}}}}
```

### Claude.ai and ChatGPT (web): OAuth, no key to paste

Web clients connect with **only the URL** `https://your-domain/mcp`. The hangar implements the MCP authorization spec
(OAuth 2.1 with PKCE and dynamic client registration):

1. **Claude.ai:** Settings → Connectors → *Add custom connector* → paste the URL.
   **ChatGPT:** Settings → Apps & Connectors → *Create* (developer mode) → paste the URL, authentication *OAuth*.
2. The app opens the hangar portal. Sign in (SSO or local account) if needed, then click **Autorizar**.
3. Done: the app uses the platform MCP **as you**, with your teams and roles.

The app gets a 1-hour access token, renewed with a rotating refresh token (30 days without use and you authorize
again). The token only works on `/mcp`, not on the rest of the API. You see and revoke connected apps in
**Minhas chaves → Apps conectados**. Admins see every app there, and **Bloquear** disconnects an app for everyone.
Deactivating a person disconnects their apps.

Requirements and policy:
- A **public HTTPS** address (`PUBLIC_BASE_URL`): a domain with TLS or a tunnel (`hangar setup` → address).
- Apps may only redirect to the hosts in `OAUTH_REDIRECT_HOSTS` (default: `claude.ai`, `claude.com`, `chatgpt.com`,
  `chat.openai.com`, `platform.openai.com`) or to `localhost`. Add hosts for other web clients, or turn the feature off
  with `OAUTH_ENABLED=0`.
- Desktop and CLI clients that support MCP OAuth (for example the MCP Inspector, which redirects to localhost)
  work the same way; personal tokens keep working for the others.

Then ask, for example: *"Create an agent that triages support tickets into P1–P4 and drafts a reply. Test it and
ship it."*

## Plug a shipped agent into a tool (plug and play)

Open the agent in the UI, go to the **Connect** tab, pick a tool and a mode. The hangar creates an `invoke` key that
only reaches that agent, tags it with the tool (usage shows up under that channel) and gives you the config to paste.
Every connection is optional and independent: connect the same agent to Claude Code as a tool and to Open WebUI as a
model, and revoke either one alone with **Disconnect**.

| Mode | What the tool sees | Offered for |
|---|---|---|
| **MCP (tool)** | One tool named after the agent; its skills, MCPs and LLM run behind it | Every tool |
| **Model** | The agent is a chat model (OpenAI-compatible, streaming, attachments) | LibreChat, Open WebUI, OpenCode, OpenAI SDKs |
| **Model, as a coding agent** | The agent is the model, and the editor's own tools (read and edit files, terminal) run on the developer's machine | VS Code chat, Cline / Roo Code, Continue |

| Tool | MCP | Model | Where the config goes |
|---|:-:|:-:|---|
| Claude Code | ✓ | | `claude mcp add --transport http …` |
| Claude Desktop | ✓ | | `claude_desktop_config.json` (via `mcp-remote`) |
| Codex CLI | ✓ | | `~/.codex/config.toml` |
| OpenCode | ✓ | ✓ | `opencode.json` (`mcp` or `provider`) |
| Cursor | ✓ | | `.cursor/mcp.json` |
| VS Code (Copilot) | ✓ | ✓ | `.vscode/mcp.json`, or *Manage models → OpenAI Compatible* for the coding agent |
| Cline / Roo Code | | ✓ | *API Provider: OpenAI Compatible* (base URL, key, model id) |
| Continue | | ✓ | `~/.continue/config.yaml` (`provider: openai`, `capabilities: [tool_use]`) |
| LibreChat | ✓ | ✓ | `librechat.yaml` (`mcpServers` or `endpoints.custom`) |
| Open WebUI | ✓ | ✓ | Admin → Settings → *External Tools* (MCP) or *Connections* (OpenAI API) |
| OpenAI SDK / other | | ✓ | `base_url` + key |
| Other MCP client | ✓ | | Streamable HTTP URL + `Authorization` header |

The same flow is available without the UI:

```bash
hangar connect ticket-triage                            # list tools and modes
hangar connect ticket-triage claude-code                # MCP (default): creates the key, prints the config
hangar connect ticket-triage open-webui --mode model    # as a model
hangar connect ticket-triage librechat --preview        # config with a placeholder, creates nothing
hangar connect ticket-triage --list                     # active connections (revoke: hangar keys revoke <id>)
```

From an AI client connected to the platform MCP, ask for it: the `connect_agent(slug, client, mode)` tool returns the
same key and config.

Notes:
- Only **prod** agents answer on `/gw/<slug>`; connect after `ship`.
- MCP usage counts only real tool calls (handshake and `tools/list` are not metered), with tokens and cost.
- Open WebUI: to get one Data Studio workspace per chat, run it with `ENABLE_FORWARD_USER_INFO_HEADERS=true`; the
  runtime reads `X-OpenWebUI-Chat-Id`.
- Claude Code asks once to approve servers added with `--scope project`; `--scope user` (the default in the
  snippet) does not.

## VS Code extension: Agent Hangar

This is the plug-and-play way to use agents in VS Code. The hangar builds the extension with the central image and
serves it at `/downloads/agent-hangar-vscode.vsix`. The source is in [extensions/vscode](../extensions/vscode/).

| | |
|---|---|
| **Your agents as chat models** | The chat agents you can use appear in VS Code's model picker (`lm.registerLanguageModelChatProvider`). In **Agent** mode the agent works on your project with VS Code's own tools (files, terminal) through the client-tools path below |
| **Platform MCP** | Registered by the extension (`lm.registerMcpServerDefinitionProvider`) with your personal token, so the chat can create, edit, test and publish agents with your roles |
| **Sign-in** | Through the portal, with PKCE: the extension opens `/app/#/vscode`, you confirm (you are already signed in with SSO or a local account), and VS Code comes back connected. No keys to copy |
| **Test** | **Agent Hangar: Testar conexão** runs the same end-to-end check as the Connect tab |
| **stage** | Set `agentHangar.environment` to `stage` to see the stage version of the agents you edit, and test them before publishing |

**How sign-in works:**
1. `POST /api/vscode/authorize` runs in the portal, with your session. It returns a signed, single-use code, valid
   for 5 minutes and bound to the extension's PKCE challenge.
2. The browser goes to `vscode://agent-hangar.agent-hangar/auth`.
3. The extension exchanges code + verifier at `POST /api/auth/vscode/token` for a personal key: scope `user`, client
   `vscode-ext`, listed under **Minhas chaves**.
4. Signing out of the extension revokes the key on the server. Losing access revokes it too.

**Install:**
1. In the portal, go to **Conectar ferramentas** (or an agent's **Connect** tab) and use **Baixar extensão (.vsix)**.
2. Run `code --install-extension agent-hangar-vscode.vsix`.
3. Click **Abrir no VS Code** in the portal. You need VS Code 1.104 or later.

## Coding agents in VS Code (client tools)

A coding client sends its **own tools** with each request, in the OpenAI format (`tools`): read and edit files,
search the project, run a terminal command. Examples are the VS Code chat, Cline, Roo Code and Continue. The agent
works like this:
- It offers the model the client's tools next to its own: MCPs, memory, sub-agents, skills.
- When the model calls **one of the agent's tools**, the agent runs it on the server, as always.
- When the model calls **one of the client's tools**, the agent returns it to the client (`tool_calls`, with
  `finish_reason: "tool_calls"`, streaming or not). The editor runs it on the developer's machine, with the
  developer's approval, and sends the result back. The loop goes on until the final answer.
- If both kinds come in the same round, the server part runs first. Its context is kept and put back in place when
  the client sends its results, so nothing is lost between requests.

So the files and the terminal never leave the developer's machine, and the hangar adds the governed part:
- instructions and company standards (skills);
- MCPs and memory;
- versioning with tests;
- a per-tool key, usage and cost per person.

When client tools are present, the agent also gets a short standing instruction: read before editing, make small
verifiable changes, run the project's tests or build before finishing, and never read or expose secrets.

**Connect:** in the agent's **Connect** tab, choose *VS Code → Como modelo*, *Cline / Roo Code* or *Continue*. You get a
per-tool key and the values to paste: base URL `…/gw/<slug>/v1`, the key and the model id `<slug>`.

**Test the connection:** the **Testar conexão** card in the same tab, `POST /api/agents/<slug>/connections/test`, or
the platform MCP tool `test_agent_connection`, runs the exact path a coding client takes:
1. It sends a client tool (`hangar_ping`) and checks, over the stream, that the agent calls it
   (`tool_calls` + `finish_reason: tool_calls`).
2. It returns the result the way the editor would, and checks that the agent uses it to answer.

The test costs one short LLM call and works in stage (for whoever can edit the agent) or in prod.

**Governance: which LLMs may receive code.** In the coding path, the developer's files and terminal output go to the
agent's LLM.
- **Company policy.** An admin chooses it in **Catálogo → Conexões de LLM**, or with
  `PATCH /api/org {"code_policy": …}`:
  - `any` (the default) keeps coding mode open for every connection;
  - `approved` allows it only with connections marked **approved for code**, for example the corporate gateway.
    Approve a connection with `PATCH /api/catalog/llm/<name>/code {"allow_code": true}`.
- **Where it applies.** The connection that counts is the effective one for each environment, including per-environment
  overrides.
- **When it takes effect.** It is enforced live, with no redeploy:
  - The gateway answers `403` (`type: code_policy`) to any request that carries client tools.
  - The connection test explains why.
  - Code evaluations fail with the same message.
  - Plain chat with the agent is not affected.

**Secrets never reach the model.** In coding mode, the runtime masks obvious secrets in the client's tool results and
in the user's text before calling the LLM:
- private keys;
- AWS, GitHub, Slack and Google keys;
- `sk-…` API keys;
- JWTs and the hangar's own keys;
- `.env`-style `*_SECRET/_PASSWORD/_TOKEN/_API_KEY=…` lines;
- `password = "…"` literals;
- passwords inside database URLs.

Only the value is replaced; names and code stay, so the agent can still say "this key is hardcoded". The agent's
standing instructions also tell it not to read `.env` files or credentials. Turn masking off per agent with
`llm.redact_secrets: false`.

**Turning it off:** set `llm.client_tools: false` in an agent's spec. The agent then ignores the tools a client sends
and answers only with its own. Harness agents don't take client tools, because they run in their own throwaway
container.

## Consumers (a shipped agent)

**LibreChat**: the full kit, with per-conversation workspaces, attachments and a Docker Compose file, is in [integrations/librechat](../integrations/librechat/). Minimal `librechat.yaml`:
```yaml
endpoints:
  custom:
    - name: "Ticket Triage"
      apiKey: "<invoke key>"
      baseURL: "http://hangar:8090/gw/ticket-triage/v1"
      models: { default: ["ticket-triage"] }
      headers: { X-Channel: "librechat" }
```

**Any OpenAI SDK**
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8090/gw/ticket-triage/v1", api_key="<invoke key>")
client.chat.completions.create(model="ticket-triage", messages=[{"role": "user", "content": "..."}])
```

**As an MCP tool** (the agent's skills and tools run behind a single tool):
```bash
claude mcp add --transport http ticket-triage http://localhost:8090/gw/ticket-triage/mcp --header "Authorization: Bearer <invoke key>"
```

**A2A.** Agent Card: `GET /gw/<slug>/.well-known/agent.json`. RPC: `POST /gw/<slug>/a2a` with `message/send`.

## AG-UI: the agent inside your app

[AG-UI](https://docs.ag-ui.com) is the open protocol between an agent and the interface of an app (CopilotKit, React
Native, Slack, Teams, and the SDKs for TypeScript, Python, Kotlin, Go and others). Every shipped agent answers it at
`POST /gw/<slug>/agui`, with the same invoke key, usage metering and audit as the other protocols. In the agent's
**Connect** tab, pick **CopilotKit** or **Outro cliente AG-UI** to get the key and the code to paste.

The app sends a `RunAgentInput` (`threadId`, `runId`, `messages`, `tools`, `context`) and receives SSE events:

| What happens | Events |
|---|---|
| The run | `RUN_STARTED`, then `RUN_FINISHED` or `RUN_ERROR` |
| The answer | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| A tool of the agent (MCP, HTTP, sub-agent) | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT` |
| A tool of the app (`tools` in the input) | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`. The app runs the tool and sends the result in the next run, as a `role: tool` message |
| Tokens and cost | `CUSTOM` named `hangar.usage` |

What the hangar adds to the protocol:
- **The thread is the agent's session.** `threadId` separates workspaces and files per conversation, as
  `X-Conversation-Id` does for LibreChat.
- **The app's tools follow the client-tools rules.** Under the company's code policy, an agent whose LLM connection is
  not approved for code answers `403` to a run that carries tools. `llm.client_tools: false` turns them off.
- **A Digital employee works through a task.** The conversation becomes a task (source `agui`), so every action goes
  through the employee's authority.
  - **When the run pauses:** an action needs approval, the employee asks a question, or the delivery goes to review.
    The run ends with `RUN_FINISHED` and `outcome: interrupt`.
  - **What the interrupt carries:** the reason (`hangar.approval`, `hangar.question`, `hangar.capability`,
    `hangar.review`), the task, the tool and the action type. The action's arguments never leave the hangar.
  - **Who decides:** the person the authority names, in the hangar (portal, Slack, Teams, e-mail). The app's key never
    gains the power to approve.
  - **Resuming:** the app sends `resume: [{interruptId, status: "resolved"}]` and gets what happened since, up to the
    answer or the next interrupt. `status: "cancelled"` cancels the task. Only the person who asked (or an admin) can
    follow a task this way.
- **Long tasks.** A run follows the task for up to 10 minutes, then ends with the interrupt `hangar.still_running`.
  Resuming keeps following the same task.
- **Harness agents** (Claude Code, Codex…) run a job per run: `STEP_STARTED` and `STEP_FINISHED` around it, the
  answer, and a `CUSTOM` event `hangar.job` with the job id and the diff.

```bash
curl -N http://localhost:8090/gw/ticket-triage/agui \
  -H "Authorization: Bearer <invoke key>" -H "Content-Type: application/json" -H "Accept: text/event-stream" \
  -d '{"threadId": "t1", "runId": "r1", "messages": [{"id": "m1", "role": "user", "content": "Olá!"}],
       "tools": [], "context": [], "state": {}, "forwardedProps": {}}'
```

**Slack / Teams.** Native adapters are on the roadmap. Until then, a bot can call the OpenAI-compatible endpoint
with `X-Channel: slack`.
