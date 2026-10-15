# Connectors (MCP)

**Capacidades → Conectores** lists every MCP server the agents can use. It follows the layout of ChatGPT's plugin
catalog:

- a search box;
- a row of installed connectors;
- *Catálogo* / *Personalizados*;
- sections by category (Populares, Produtividade, Comunicação, Vendas e CRM, Finanças, Dados, Desenvolvimento, IA,
  Monitoramento, Segurança), with each connector's icon and a **+** button.

## Where connectors come from

| Source | What | How it connects |
|---|---|---|
| Official | MCP servers that SaaS vendors publish: Notion, Linear, Atlassian, Asana, GitHub, Sentry, Stripe, PayPal, Square, Supabase, Neon, Canva, Figma, Intercom, monday.com, Webflow, Vercel | OAuth (you authorize on the vendor's site), or a token kept in the vault |
| Docker MCP catalog, remote | ~30 HTTP servers (HubSpot, Mercado Pago, Mercado Libre, Hugging Face, Context7…) | Through the bridge |
| Docker MCP catalog, container | 237 servers that run in isolated containers | The gateway (`mcp-gateway` profile, see [mcp-catalog.md](mcp-catalog.md)) |
| Custom | Any remote MCP server, added in *Adicionar personalizado* or imported from a `mcpServers` JSON | Through the bridge |

The vendor addresses for the official servers are the ones the vendors publish. *Testar conexão* confirms that one
still answers.

**Icons.** The central downloads each icon once and serves it itself, so the browser does not reach out to the
internet on every page.

- Icons come only from known sources: the vendor's domain or the Docker catalog.
- Custom connectors use the icon the admin uploads (PNG, JPEG, WebP or SVG, up to 64 KB) or the domain's icon.

## The bridge

Agents never talk to a remote MCP server directly. They call `/internal/mcp-proxy/<name>` on the central, with their
own internal token. The central then opens the session with the real server:

- over Streamable HTTP or legacy SSE (the agent always speaks Streamable HTTP);
- with the credential from the vault: a Bearer token, custom headers or OAuth tokens that renew themselves.

Only agents that have the connector in their spec get through. Scope also applies:

- a **team** connector is only for that team's agents;
- a **personal** connector is only for its owner's personal agents.

## Testing the connection

*Testar conexão* checks the server without saving anything:

- **Handshake:** runs `initialize` and reads the protocol version, the server name and version, and the declared
  capabilities.
- **Lists:** tools, resources, resource templates and prompts.

Errors come back distinct:

- timeout;
- cannot connect (address, DNS, network);
- `401`/`403` (the page suggests OAuth when the server advertises it);
- no MCP server at that address (`404`/`405`);
- an address the vendor turned off (`410`);
- a protocol error.

**Transport:**

- *Automático* tries Streamable HTTP first and falls back to legacy SSE.
- WebSocket is not supported: the MCP specification only defines stdio and Streamable HTTP.

## Tool governance

When you save, you choose which tools reach the agents. Tools the server marks as destructive start unchecked. From
then on:

- **New tool:** it stays blocked until an admin approves it.
- **Changed tool:** a tool whose description or parameters change after approval also stays blocked until an admin
  approves the new version. A silent description change is how "tool poisoning" hides instructions for the model.
- **Admin inbox:** it shows both cases, and also connectors whose authentication expired.
- **Status check:** the central re-tests every bridged connector every `CONNECTOR_CHECK_MIN` minutes (default 30;
  `0` turns it off). *Reconectar* runs the check now.
- **Action type per tool:** read, internal write, sends outside, publishes, deletes or financial. It sets the
  Digital employees' authority the same way the action catalog does. A tool the server marks `readOnlyHint` starts
  as read.

## Local servers (stdio)

*Adicionar personalizado → Túnel (stdio)* runs an MCP server that is meant to run locally, such as an `npx` or `uvx`
package. Only admins add them, because they run code.

**The package**

- **Runner:** `npx` (Node) or `uvx` (Python).
- **Pinned version required:** `name@1.2.3` for npm, `name==1.2.3` for Python (`name@1.2.3` is accepted and
  converted). The package never changes without you knowing.
- **Command and arguments:** the arguments are passed as a list, with no shell. For `uvx`, the package's command
  defaults to the package name.
- **Environment variables:** values marked as secret go to the vault. They reach only the server's container, never
  the agents.
- **Allowed domains:** the package registry is always allowed (`registry.npmjs.org`, or `pypi.org` and
  `files.pythonhosted.org`); you add any others the server needs.

**The container.** Each server gets one container of its own (`mcp-stdio-<name>`, image `mcp-stdio-runner`), the same
way code plugins run:

- an isolated network (`hangar_plugins`, `internal: true`);
- a read-only disk, with packages installed in a temporary area (`MCP_STDIO_TMP`, default 1 GB);
- no privileges (`cap_drop: ALL`, `no-new-privileges`), with memory and CPU limits (`MCP_STDIO_MEM`, `MCP_STDIO_CPUS`).

**How it is reached**

- **Outbound:** internet access only goes through the central's proxy, which allows the registry and the declared
  domains. Every refused attempt shows on the connector page.
- **Inbound:** a bridge inside the container turns stdio into HTTP. Only the central can call it, with a token derived
  from the internal secret. Agents still go through the central's bridge, with the same tool approval as remote
  connectors.
- **Kubernetes:** the server becomes a Deployment with the `plugin` role, under the chart's NetworkPolicy.

**Lifecycle**

- **Testing:** *Testar conexão* starts a temporary container, installs the package, lists tools, resources and prompts,
  and removes it.
- **Status check:** if the container stops, the status check brings it back up.
- **Removal:** removing the connector removes its container.

## Secrets

The connector stores no secret values. The token and the headers marked as secret go to the vault, and the connector
keeps only `secret://name`. The page shows only the reference.

## Importing `mcpServers`

*Importar JSON* reads the `mcpServers` format of Claude Desktop, Cursor and Claude Code's `.mcp.json`:

- **Remote entries** (`url`) become connectors, through the bridge.
- **Headers:** the ones that look like secrets (`Authorization`, `*token*`, `*key*`, `*secret*`…) go to the vault.
- **Local entries** (`command`): `npx` and `uvx` become local servers (below). The import asks for a pinned version when the JSON has none; other commands (`node`, `python`, `docker`, binaries) are not imported.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/connectors` | Catalog by section, plus installed connectors |
| GET | `/api/connectors/icon?key=` | A connector's icon (cached by the central) |
| POST | `/api/connectors/test` | `{url, transport, auth, token, headers, timeout_s}` or `{stdio: {runner, package, entry, args, env, egress}}`: test without saving (stdio in a temporary container) |
| POST | `/api/connectors` | Create: `{name, url, transport, auth, token, headers, scope, team, icon_data, tools, accept_risk}`. With OAuth it returns `authorize_url` |
| GET · DELETE | `/api/connectors/{name}` | Details: status, server, tools, who uses it. Delete is refused while an agent uses it |
| POST | `/api/connectors/{name}/check` | Reconnect: test now and update status and tools |
| PATCH | `/api/connectors/{name}/tools/{tool}` | `{state: approved \| disabled}` and/or `{action_type}` |
| POST | `/api/connectors/import/preview` | `{text}`: what a `mcpServers` JSON would import |
