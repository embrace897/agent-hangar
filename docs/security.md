# Security model

Agent Hangar runs other people's prompts and code-writing agents, so treat it like CI infrastructure. This page
describes what is protected today, and what is **not**.

## Credentials

| Credential | Power | Where it lives |
|---|---|---|
| `ADMIN_TOKEN` | Everything | `.env` of the hangar only. It is never injected into agents or jobs. Use it to bootstrap and as the break-glass login; keep it in a vault once SSO works. |
| UI session | The user's roles (see [access.md](access.md)) | `HttpOnly`, `SameSite=Lax` cookie opened by the OAuth2 login (or by a token login). Only its hash is stored; it expires (`SESSION_TTL_HOURS`) and is revoked on logout or deactivation. Writes need the `X-CSRF-Token` header (double submit). |
| API key, scope `user` | Acts as its owner: `/api`, the hangar MCP, CLI | Personal token. It dies when the owner is deactivated. |
| API key, scope `admin` | UI, `/api`, the hangar MCP, every agent | Shown once, stored as SHA-256. Revocable. Created by admins only. |
| API key, scope `invoke` | Only `/gw/<slug>` for the listed agents | This is what goes into LibreChat, Slack bots, OpenCode… A key owned by a user is revoked automatically when that user loses access to one of the agents. |
| API key, scope `scim` | Only `/scim/v2` | Used by the directory (Entra ID, Okta) for provisioning. |
| Per-agent internal token | `HMAC(INTERNAL_SECRET, slug)` | Injected into that agent's container only. With `X-Agent-Slug`, the hangar lets it call **only its own sub-agents** and, if the spec has the `platform_dashboard` tool, the read-only dashboard. A compromised agent cannot impersonate another. |
| Job token | Single job's callback | Random per job; accepted once. |
| Connection API keys | Your LLM provider | Encrypted at rest with Fernet (`HANGAR_SECRET_KEY`). Never returned by the API (only the last 4 characters). Decrypted only to start the container that needs it. |

Keep `HANGAR_SECRET_KEY` safe and backed up. Losing it makes the stored connection keys unreadable, so you would
have to re-enter them.

## Login and access control

Summary; the full model is in [access.md](access.md).

- **OAuth2 authorization code with PKCE.** `state` and the verifier travel in a signed, 10-minute `HttpOnly`
  cookie. The profile is read from the provider API: Google userinfo, Microsoft Graph, GitHub.
- **Restrictions are checked on the callback.** They are the allowed domains, a fixed Entra tenant (never
  `common`) and required GitHub organizations.
- **An existing user is linked by e-mail only when the provider vouches for it.** That means a verified e-mail
  on Google or GitHub, or a fixed tenant on Entra. Otherwise a misconfigured provider could take over accounts.
- **Local accounts are optional** (`LOCAL_LOGIN=0` turns them off). Passwords are hashed with scrypt, with a
  minimum length of 8, and 5 failures lock the account for 15 minutes. The same error message is returned for an
  unknown user and a wrong password. A password reset ends the user's sessions.
- **Deactivation takes effect immediately.** Deactivating a user (UI or SCIM) ends their sessions and revokes
  all their keys at once. Losing a team role or a grant revokes the invoke keys that no longer apply.

## Composing agents from existing ones

- `plan_agent` and `compose_agent` only create. Source agents (specialists, bases) are never edited, versioned,
  tested, promoted or redeployed because of a composed agent. A composed agent always calls its specialists'
  production version, and the deploy stops if a specialist is not running in production.
- Using an agent as a sub-agent requires being allowed to use it: for another team's agent, an approved access
  request. This applies to `compose_agent`, `design_agent`, `edit_agent` and `PATCH/PUT` on the API. Before 0.13, a
  developer could add any visible agent as a sub-agent.
- Copying an agent as a base requires seeing its spec; skills created during composition never overwrite an
  existing skill. See [composer.md](composer.md).

## Supply chain

Images and the chart are signed with cosign (keyless) and carry an SBOM and SLSA provenance; CI scans images and the
chart with Trivy. How to verify before deploying: [supply-chain.md](supply-chain.md).

## OAuth for the platform MCP

Web clients (Claude.ai, ChatGPT) connect to `/mcp` with OAuth 2.1; see [clients.md](clients.md).
- **PKCE (S256) is mandatory**, and clients are public: there is no client secret to leak.
- **Redirects are restricted** to `OAUTH_REDIRECT_HOSTS` (plus loopback) at registration. An app registered by a
  third party cannot send the code elsewhere. Unknown apps or redirects get an error page, never a redirect.
- The **consent page** shows the app name and the host the browser will return to; codes are signed, live 5 minutes
  and are single-use across all replicas.
- **Tokens:** access tokens last 1 hour and only work on `/mcp` (403 elsewhere). Refresh tokens rotate, and a reused
  refresh token revokes the whole authorization. Only SHA-256 hashes are stored.
- **Revocation:** the person (Minhas chaves), an admin (block the app), or deactivating the person.
- Everything is audited: `oauth.register`, `oauth.authorize`, `oauth.token`, `oauth.revoke`, `oauth.block`,
  `oauth.refresh_reuse`.

## Remote MCPs with OAuth

- **Storage:** provider tokens (access and refresh) are stored encrypted with Fernet.
- **Where they are used:** only inside the central, which proxies agent calls at `/internal/mcp-remote/<name>`.
  The proxy requires the agent's per-agent internal token, and that MCP in the agent's spec.
- **Connecting:** consent happens once, as an admin, with PKCE and a signed state cookie. Removing the MCP revokes
  the refresh token at the provider (best effort).

## Docker MCP catalog

The optional gateway (profile `mcp-gateway`):
- has Docker access through a restricted proxy (containers and images only);
- runs signed images;
- keeps the servers it starts on `hangar_mcp_servers`, a network with internet egress but no agents or database;
- has LLM-driven server management (`mcp-add`/`mcp-remove`) disabled.

Agents reach it without a token, only from the internal network. See [mcp-catalog.md](mcp-catalog.md).

## Agent memory

The optional memory service (profile `memory`) is reachable only by the central, with `MEMORY_TOKEN`:
- **Groups:** agents call `/internal/memory/mcp` on the central, which decides the memory groups from the spec.
  The agent never picks them.
- **Stage vs production:** a per-environment token keeps stage containers from writing production memory.
- **Database:** the graph database (Neo4j or FalkorDB) sits on an internal network with no internet access.
- **Embeddings:** computed locally by default.

See [memory.md](memory.md).

## Coding agents (client tools)

In coding mode (VS Code, Cline, Continue, code evaluations), the developer's files and terminal output go to the
agent's LLM:
- **Where things run.** The editor runs the tools on the developer's machine, with the developer's approval. The
  hangar never gets access to that machine.
- **Which LLMs.** With `code_policy: approved`, only LLM connections approved for code can receive it. The gateway
  enforces this live, with no redeploy.
- **Secrets.** The runtime masks obvious secrets in client tool results and user text before the LLM call.
- **Code evaluations** run in throwaway sandboxes with the same isolation as harness jobs.

See [clients.md](clients.md#coding-agents-in-vs-code-client-tools).

## Networks

| Network | Members | Why |
|---|---|---|
| `db_net` (internal) | Postgres, hangar | Agents and tool servers (which may execute code) can't reach the database |
| `hangar_agents` | hangar, agent containers, MCP servers such as Data Studio | Gateway → agents, agents → hangar `/internal`, agents → MCPs |
| `hangar_jobs` | hangar, harness job containers | Job callbacks. Jobs don't see agents |
| `docker_api` (internal) | hangar, docker-socket-proxy | Only the hangar talks to Docker |
| `hangar_memory` | hangar, memory service | Only the central reaches the memory service. It has egress to the extraction LLM |
| `memory_db` (internal) | memory service, Neo4j / FalkorDB | The graph database has no internet access and no agents |

## Containers

- **Agents** get a read-only root FS, a 64 MB tmpfs, `cap_drop: ALL` and `no-new-privileges`, plus memory, CPU and
  PID limits. They run on the `hangar_agents` network and never receive the Docker socket.
- **Jobs** get a new container per call and a non-root user, run on the separate `hangar_jobs` network, and are
  removed after the callback or timeout.

## The Docker socket (read this)

The hangar does not mount `/var/run/docker.sock`. It talks to
[`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy) on an internal network, and
the proxy only allows the container, network and volume APIs. `exec`, `build`, image pulls, swarm, secrets and
plugins are denied.

**Limitation.** Anyone who controls the hangar process can still create a container with arbitrary settings,
such as a privileged container or a host bind mount. With a Docker backend, that is equivalent to root on the
host. The proxy reduces the attack surface, but it is not a sandbox boundary against a compromised hangar. For
real multi-tenant isolation, use a rootless Docker/Podman host, a dedicated VM, or the Kubernetes driver
([kubernetes.md](kubernetes.md)), with its NetworkPolicies, together with admission policies.

## Data Studio (code execution)

The Data Studio MCP server runs user-driven Python and SQL. Each conversation (session) has its own directory and its
own Linux uid, and every file-reading or SQL tool runs inside that session's kernel. One conversation can't read
another's files or the link-signing secret; the smoke test checks this. Limits:
- Kernels have outbound network access.
- Resource limits are per container, not per session.

With `DATA_STUDIO_SANDBOX=container`, each conversation gets its own sandbox container:
- only its workspace is mounted;
- per-session memory, CPU and PID limits;
- read-only root FS and no capabilities;
- a separate network with no path to agents or the database.

Use that mode when users don't trust each other. In that mode Data Studio talks to Docker through its own socket
proxy, so the Docker-socket caveat above applies to it too. See `mcp-servers/data-studio/README.md`.

## Network exposure

- Put the hangar behind TLS (a reverse proxy) before exposing it. ChatGPT connectors require HTTPS anyway.
- `/internal/*` is reachable from the published port, but every route there checks its own credential (a job
  token or a per-agent HMAC).
- Job containers can reach the internet, because they must call your LLM and MCPs. An egress allowlist is on the
  roadmap.

## Known gaps (tracked as issues)

These are not implemented yet:

- Egress allowlist for jobs.
- Human approval for destructive actions.
- Input/output guardrails.
- Secret references for HTTP tool auth headers (today, do not put tokens in tool URLs).
- Rate limits (monthly budgets per team exist; see [access.md](access.md)).
- SAML directly (use a broker such as Keycloak with the generic OAuth2 provider).

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
