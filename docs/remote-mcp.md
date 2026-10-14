# Remote MCPs with OAuth (Activepieces, Notion, Linear…)

> New connectors are added in **Capacidades → Conectores** ([connectors.md](connectors.md)), which uses this same OAuth flow and adds the bridge, tool approval and status checks.

Many MCP servers require a login: Activepieces, Notion, Linear, Atlassian, and the remote servers of Docker's
catalog. Agent Hangar connects to them **once, as an admin**, and gives them to agents without ever handing
out the credential.

1. **Catálogo → MCPs remotos com OAuth → + Conectar MCP remoto**. Enter a name (it becomes the catalog name) and
   the MCP URL.
2. The Hangar discovers the authorization server from the MCP's `401` response (RFC 9728 → RFC 8414). It then
   registers itself as a client (dynamic client registration, RFC 7591) and sends your browser to the
   provider's consent screen: authorization code with PKCE S256, and the `resource` parameter of RFC 8707.
3. You approve. The Hangar stores the access and refresh tokens **encrypted** and **refreshes them by itself**,
   under a lock so two agents never spend the same refresh token.
4. Add the name to an agent's `mcps` (for example `design_agent(slug, mcps=["activepieces"])`) and ship.

Agents call the MCP through **`/internal/mcp-remote/<name>`** on the central.
- **Who can pass:** the central checks the agent's internal token and lets it through only if that MCP is in the
  agent's spec.
- **What gets forwarded:** the call goes to the provider with the current access token. A `401` triggers one
  forced refresh and a retry.
- **Credential:** the agent never sees the provider token.

**Test** lists the MCP's tools with the stored token. **Reconnect** repeats the consent. **Remove** revokes
the token at the provider (best effort) and removes the MCP from the catalog.

If the central reaches the server at one address and the browser at another, set **"Origem pública para o
navegador"**. For example, `http://activepieces/mcp` inside Docker and `http://localhost:8098` in the browser.

API (admin): `GET/POST /api/remote-mcps`, `GET /api/remote-mcps/callback`, `POST /api/remote-mcps/{name}/test`,
`DELETE /api/remote-mcps/{name}`.

## Activepieces (profile `activepieces`)

[Activepieces](https://github.com/activepieces/activepieces) (MIT community edition) has 280+ business
integrations: Gmail, Google Sheets, Slack, HubSpot, Notion, Salesforce and more. Its MCP gives agents about 40
tools:
- **Run actions:** `ap_run_action` runs any piece action using the connections made in Activepieces.
- **Discover:** `ap_search_actions` and `ap_research_pieces`.
- **Data:** tables and records.
- **Flows:** flows and runs.

In Activepieces you choose which tools the MCP exposes.

```bash
sh scripts/setup.sh            # generates the ACTIVEPIECES_* secrets in .env (setup.ps1 on Windows)
docker compose --profile activepieces up -d
```

1. Open http://localhost:8098, create the admin account and connect the apps (Gmail, Sheets…) there.
2. In the Hangar go to **Catálogo → MCPs remotos com OAuth** and connect:
   - name `activepieces`;
   - URL `http://activepieces/mcp`;
   - browser origin `http://localhost:8098`.
3. Approve the **Agent Hangar** app on the Activepieces screen and pick the project.

The public port passes through a small nginx (`activepieces-edge`) that forwards `X-Forwarded-Host`.
Activepieces builds its OAuth redirects from that header. Without it, the redirects drop the port and go to
`http://localhost/…`.

Tested end to end: an agent created an Activepieces table and inserted records through the proxy (checked
directly in Activepieces), and the token refreshed after expiring.

## Limits of this version

- **One credential per MCP.** The authorization is shared by the agents that use the MCP, not tied to each end
  user.
- **Providers without dynamic client registration** (a pre-registered client_id/secret) are not supported yet.
