# HTTP API

Interactive OpenAPI docs: `GET /docs`. Authenticate with `Authorization: Bearer <token>` or
`X-API-Key: <token>`. The token is an admin key, a personal token (scope `user`) or a session token.
The browser UI uses the session cookie and must send `X-CSRF-Token` (the value of the `hangar_csrf` cookie) on
writes.

Every route below checks the caller's permission on the resource ([access.md](access.md)). Lists are filtered to
what the caller can see. Admins see everything.

## Login and session (`/api/auth`, open)

| Method | Path | Description |
|---|---|---|
| GET | `/api/auth/providers` | Configured login providers |
| GET | `/api/auth/login/{provider}?next=#/…` | Redirects to Google, Microsoft, GitHub or generic OAuth2 (authorization code + PKCE) |
| GET | `/api/auth/callback/{provider}` | Provider callback; opens the session and redirects to the UI |
| POST | `/api/auth/token` | `{token}` → session cookie (ADMIN_TOKEN, admin key or personal token) |
| POST | `/api/auth/activate` | `{code}` → session cookie (activation code, single use; see [workspace.md](workspace.md#signing-in)) |
| POST | `/api/auth/logout` | Ends the session |
| GET | `/api/me` | Who am I: company role, teams, pending approvals |

## Company, teams, users, approvals

| Method | Path | Description |
|---|---|---|
| GET / PATCH | `/api/org` | Company name, default visibility, time zone and `code_policy` (`any` \| `approved`) (PATCH: admin) |
| GET / POST | `/api/teams` | List (with spend and your role) / create `{name, description?, require_approval?, maintainer?}` (admin) |
| GET / PATCH / DELETE | `/api/teams/{team}` | Detail / name, description (maintainer), `require_approval`, `budget_usd_month`, `budget_enforce` (admin) / delete (admin, no agents) |
| GET / POST | `/api/teams/{team}/members` | Members / add `{email, role}` (maintainer; pre-registers unknown e-mails) |
| PATCH / DELETE | `/api/teams/{team}/members/{user_id}` | Change role / remove |
| GET / POST / PATCH | `/api/users`, `/api/users/{id}` | List (auditor) / pre-register / role and `active` (admin; deactivating revokes sessions and keys) |
| GET | `/api/approvals` | What you can decide now, and your own requests |
| POST | `/api/promotions/{id}/approve` · `/reject` | Production approval (the requester cannot approve) |
| POST | `/api/agents/{slug}/access-requests` | `{reason}` request to use an agent of another team |
| GET | `/api/agents/{slug}/grants` | Pending and approved access (maintainer) |
| POST | `/api/access-requests/{id}/approve` · `/reject` · `/revoke` | Decide or revoke (revoking also revokes the person's keys) |
| PATCH | `/api/agents/{slug}/access` | `{visibility, expose_spec, team}` (maintainer) |
| GET | `/api/sso` | Providers, group mappings, SCIM URL (admin) |
| PUT | `/api/sso/{provider}` | `{enabled, client_id, client_secret, settings}` (secret encrypted) |
| POST / DELETE | `/api/sso/mappings`, `/api/sso/mappings/{id}` | Group → team `{provider, external_group, team, role}` |

## Schedules (`/api`)

A scheduled agent runs by itself in production at the chosen day and time. Creating, changing and deleting a
schedule requires the right to edit the agent. Team members can see schedules and their runs.

| Method | Path | Description |
|---|---|---|
| GET / POST | `/api/agents/{slug}/schedules` | List / create `{message, cron? \| run_at?, timezone?, name?, notify_url?}` |
| GET | `/api/schedules` | Every schedule you can see |
| PATCH / DELETE | `/api/schedules/{id}` | Change time, message or webhook; pause or resume with `enabled` / delete |
| POST | `/api/schedules/{id}/run` | Run now and wait for the result |
| GET | `/api/schedules/{id}/runs` | History: status, answer, tokens, cost, webhook delivery |

- **`cron`:** five fields (minute, hour, day of month, month, day of week) in the schedule's time zone.
  Examples: `0 9 * * 1-5` runs on weekdays at 09:00, and `0 8 5 * *` runs on the 5th at 08:00. Aliases such as
  `@daily` work too.
- **`run_at`:** a one-off run, for example `2026-10-05T09:00`.
- **Time zone:** defaults to the company's, set with `PATCH /api/org {"timezone": "America/Sao_Paulo"}`.
- **When it runs:** only while the agent is in production. A schedule created earlier waits, and takes effect
  when the agent goes live.
- **`notify_url`:** receives `{text, agent, schedule, status, output, …}`, a payload that works with Slack and
  Teams incoming webhooks.
- **Limits:** runs at least 5 minutes apart, and webhooks to the internal network are blocked.

## Remote MCPs with OAuth (`/api/remote-mcps`, admin)

| Method | Path | Description |
|---|---|---|
| GET / POST | `/api/remote-mcps` | List / start a connection `{name, url, description?, browser_base?}` → `{authorize_url}` |
| GET | `/api/remote-mcps/callback` | OAuth redirect target (stores the tokens, creates the catalog MCP) |
| POST | `/api/remote-mcps/{name}/test` | List the tools with the stored token |
| DELETE | `/api/remote-mcps/{name}` | Revoke and remove |

Agents reach the MCP at `/internal/mcp-remote/{name}` with their internal token. See [remote-mcp.md](remote-mcp.md).

## VS Code extension

| Method | Path | Description |
|---|---|---|
| POST | `/api/vscode/authorize` | Portal (signed-in user): `{challenge, state, device}` → a signed, single-use `code` (5 min) and the `vscode://` redirect |
| POST | `/api/auth/vscode/token` | Extension (open route): `{code, verifier}` (PKCE) → personal key `{token, key_id, user}`, scope `user`, client `vscode-ext` |
| GET | `/downloads/agent-hangar-vscode.vsix` | The extension package, built with the central image |

## OAuth for the platform MCP (Claude.ai, ChatGPT…)

Discovery and token endpoints are open (CORS enabled); the consent and management routes need a signed-in user.

| Method | Path | Description |
|---|---|---|
| GET | `/.well-known/oauth-protected-resource[/mcp]` | RFC 9728 metadata. `/mcp` without a token answers 401 with `WWW-Authenticate: Bearer resource_metadata="…"` |
| GET | `/.well-known/oauth-authorization-server` | RFC 8414 metadata (also at `/.well-known/openid-configuration`) |
| POST | `/oauth/register` | Dynamic client registration (RFC 7591): public clients only (`token_endpoint_auth_method=none`); redirect hosts limited by `OAUTH_REDIRECT_HOSTS`; 30 registrations/hour per IP. Optional `scope` (`app`, `mcp`; default `mcp`) and `software_id`/`software_version`, kept on the client |
| GET | `/oauth/authorize` | Validates the request (PKCE S256 required) and sends the browser to the portal consent page `/app/#/oauth`. Loopback redirects (`127.0.0.1`, `[::1]`, `localhost`) match on any port (RFC 8252 §7.3). `scope` must be among the client's registered scopes; without it, `mcp` |
| POST | `/oauth/token` | `authorization_code` (+ `code_verifier`) or `refresh_token` (rotating; reusing an old one revokes the authorization) |
| POST | `/oauth/revoke` | RFC 7009: revokes the authorization owning the token |
| GET / POST | `/api/oauth/consent` | Portal: request details / `{params, approve}` → `{redirect}` back to the app with `code` and `state` |
| GET / DELETE | `/api/me/oauth[/{id}]` | Connected apps of the caller (admins: everyone's) / revoke one |
| GET / DELETE | `/api/oauth/clients[/{client_id}]` | Admin: registered apps / block an app and all its authorizations |

Scopes: a token with `mcp` works on `/mcp`; a token with `app` works on `/api/app/*` (never on `/api/app/admin/*`) and
on the member routes listed for the app (below). Authorizations created before scopes existed are `mcp`.

## Hangar desktop app (`/api/app`)

Bearer OAuth token with scope `app` (contract and flow: [app-hangar.md](app-hangar.md)). Errors are `{"detail": …}`:
401 means refresh and retry once; 403 means a revoked device or no access.

| Method | Path | Description |
|---|---|---|
| GET | `/api/app/me` | `{user:{id,email,name,avatar_url,org_role,teams}, org:{id,name,slug,brand}, platform:{version,features}, design_tokens:{version}}` |
| POST | `/api/app/devices` | `{device_id, name, os, os_version, app_version, channel}` → `{device}`; idempotent per user + `device_id`, bound to the token's authorization. A device revoked by an admin answers 403 |
| POST | `/api/app/devices/{device_id}/heartbeat` | `{app_version}` → `{status: "active"|"revoked"}`; also answers `revoked` for the token of an authorization that has ended |
| GET | `/api/app/design-tokens` | The console tokens (`static/design-tokens.json`): `{version, light, dark, fonts, radius}` |
| GET | `/api/app/models` | Token models synced with the desktop app: `{version, connections:[{id, name, provider, base_path, models:[{model_id, name, context_length, caps, price_in_per_mtok, price_out_per_mtok}]}]}` |
| POST | `/api/app/devices/{device_id}/llm-key` | Creates or rotates the device's LLM proxy key: `{key:"ahl_…", prefix, created_at}` (shown once; the previous key stops working) |
| GET | `/api/app/events` | Server-sent events for the person (see below) |
| GET | `/api/app/admin/overview` | Admin (console session or admin key): `{devices_active, members_connected, versions, last_seen_at, platform_url, models_synced:{connections, models}}` |
| GET | `/api/app/admin/devices` | Admin: every device with its owner, status and last signal |
| POST | `/api/app/admin/devices/{id}/revoke` | Admin: revokes the device and its OAuth authorization (audit `app.device.revoke`) |

A device becomes `revoked` when an admin revokes it, or when its authorization ends (sign-out from the app, revocation in
"My keys", app blocked, person deactivated). Only the admin case keeps the same `device_id` from registering again.

### Member routes with the app token

Contract: [app-hangar-workspace.md](app-hangar-workspace.md) (section 4 has the response of each route). A short,
explicit list of member routes also accepts the `app` token, always with the person's own access (teams and roles).
Each one is marked in code with `member_route(allow_app=True)` (`routers/deps.py`); the middleware lets an OAuth token
reach an `/api` route only when it carries that mark, and the dependency checks the scope again.

| Method | Path | With the app token |
|---|---|---|
| GET | `/api/me/workspace` | The member's home: decisions to take, their agents, tasks, month cost |
| GET | `/api/decisions` | Open decisions the person can take |
| GET | `/api/org-chart?mine=1` | Only with `mine=1` (without it: 403) |
| GET | `/api/inbox[?view=]` | Inbox items and counts |

Everything else refuses the app token with 403: admin, auditor, settings, keys, secrets, OAuth/SSO, and member routes
outside the list (writes such as `/api/inbox/mark` and `/api/decisions/{id}` come with M2). Refusals by the middleware
are `{"error": …}`; the `mine=1` refusal comes from the route and is `{"detail": …}`.

### Events (`GET /api/app/events`)

Server-sent events for the person (`text/event-stream`), with the app token (or any credential of a person; the admin
token has no person and gets 403). Each message is `id: <n>`, `event: <type>`, `data: <json>`; a `: keepalive` comment
goes out after 25 s of silence; the stream starts with `retry: 5000` and closes after 30 minutes (reconnect with
`Last-Event-ID`). Without `Last-Event-ID` the first message is `inbox.changed` with the current count, whose `id` is the
cursor; with it, the stream resumes after that id (no snapshot).

| Event | Data | When |
|---|---|---|
| `inbox.changed` | `{unread}` | A decision the person can take opens, is decided or escalated; a task they requested or manage changes status; they mark items read/archived |
| `decision.created` | `{id, title, agent}` | A decision opens that the person can take (`agent` is the slug) |
| `decision.decided` | `{id}` | It was decided (or expired/cancelled) |
| `agent.status` | `{slug, status}` | An agent or Digital employee of "My agents" is created or changes status (Digital employees report the employee status) |
| `models.changed` | `{version}` | The list of `GET /api/app/models` changed (`version` is the same value) |

How it works with several replicas: changes are written to the `app_events` table in the same transaction as the change
(an `after_flush` hook in `services/app_events.py`); every stream reads that table from its cursor every 2 s and decides
**at delivery** whether the person may see each event, with their current access. Rows older than 24 h are deleted.
A row committed late by a longer transaction is still delivered, without `id:`, so the client's cursor never goes back.
Events are hints: the app refetches the list it shows.

### Synced token models and the LLM proxy (`/api/app/llm`)

Contract: [app-hangar-models.md](app-hangar-models.md). An admin turns on **Sync token models with Hangar desktop** per
LLM connection (`PATCH /api/catalog/llm/{name}/desktop` with `{sync}` or `{model_id, hidden}`, audited as
`llm_connection.desktop`). Only `protocol = "anthropic"` connections qualify and, with `code_policy = "approved"`, only
connections approved for code; otherwise the call answers 400 with the reason. `GET /api/catalog` shows each
connection's `desktop: {sync, reason, active, models, models_total, hidden}`.

The app talks to the proxy with the device key (`Authorization: Bearer ahl_…` or `x-api-key: ahl_…`, never the OAuth
token). The connection's key is taken from the vault and never leaves the platform. Errors use the Anthropic format
(`{"type":"error","error":{"type","message"}}`, plus `detail`).

| Method | Path | Description |
|---|---|---|
| POST | `/api/app/llm/{connection_id}/v1/messages` | Forwarded to `<base_url>/v1/messages` (JSON or SSE, streamed without buffering) with `anthropic-version`/`anthropic-beta` |
| POST | `/api/app/llm/{connection_id}/v1/messages/count_tokens` | Forwarded; no usage recorded, no budget check |
| GET | `/api/app/llm/{connection_id}/v1/models[/{id}]` | The connection's visible models (answered by the platform) |

Refusals: invalid, rotated or revoked key, deactivated person or ended authorization → 401; connection not synced or
code policy → 403; model hidden or not a text model → 400 (with the allowed list); team budget (`budget_enforce`) or a
hard-stop budget policy → 429. Each call records a `UsageEvent` without an agent (`client = "hangar-desktop"`, person,
team, device, connection, model, input/output/cache tokens and cost) that counts toward the team budget. Only metadata
is stored: never the body or the response.

## Reuse before you build (`/api/compose`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/compose/plan` | `{request, capabilities?, limit?}` → similar agents (only visible, only in production), skills, MCPs, templates, access per piece, gaps and questions, `compose_hint` |
| POST | `/api/compose` | Creates a NEW agent from pieces: `specialists`, `base`, `skills`, `new_skills`, `mcps`, `copy_tests_from`, plus only the new `instructions`/`tests`. Source agents are never changed |
| GET | `/api/agents/{slug}/lineage` | `built_from` (base, specialists with versions and whether they changed since) and `used_by` |
| GET | `/api/compose/stats` | Admins and auditors: plans, composed agents, reuse rate, tokens reused vs written |

See [composer.md](composer.md). MCP tools: `plan_agent`, `compose_agent`, `agent_lineage`.

## Agent avatar

| Method | Path | Description |
|---|---|---|
| GET | `/api/avatars` | The characters an agent can have: `id`, `label`, `url` (`/ui/avatars/<id>.webp`) and the `roles` each one stands for |
| PUT | `/api/agents/{slug}/avatar` | Editors (the owner, the team): `{avatar}`. It changes only the picture: no new version, no tests |

Agents get one when they are created: the `avatar` the creator passes (`POST /api/agents`, the MCP tools
`register_agent`, `compose_agent`, `build_multi_agent`), or else one picked from the name and goal. Agents created
before avatars existed get the one for their role when they are shown. MCP tool: `list_avatars`.

## Agent guide (`/api/agents/{slug}/guide`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/agents/{slug}/guide` | Anyone who can see the agent: `facts` (goal, versions, model, pieces, example requests, endpoints when the caller can use it), `graph` (`nodes`, `edges` of the flow) and `doc` (`text`, `source`, `reviewed`, `version`, `outdated`) |
| PUT | `/api/agents/{slug}/guide` | Editors: `{text}` in Markdown. It creates no new agent version and needs no tests |
| POST | `/api/agents/{slug}/guide/approve` | Editors: marks the generated draft as reviewed |
| POST | `/api/agents/{slug}/guide/generate` | Editors: new draft from the agent's LLM, written only from the spec and the passed tests |

See [guide.md](guide.md). MCP tools: `get_agent_guide`, `set_agent_guide`.

## Digital employee API

See [digital-employee.md](digital-employee.md). "Manage" means the owner, the manager, a maintainer of the team or
an admin.

| Method | Path | Description |
|---|---|---|
| GET | `/api/employees` | Digital employees you can see, with status, task counts, open decisions and 30-day cost |
| POST | `/api/employees/plan` | Creates nothing: `missing` (each with the question for the owner), `optional`, `reuse` and `authority_default` |
| POST | `/api/employees` | Hire. With a required field missing it returns `created: false` and the questions |
| GET | `/api/employees/{slug}` | Job, rules, effective authority, probation tasks and `metrics` |
| PATCH | `/api/employees/{slug}` | Manage: job, responsibilities (3+), systems, channels, manager, backups, limits, report webhook and hour. Only the manager or an admin changes `autonomy_level` |
| POST | `/api/employees/{slug}/probation` | Manage: deploys stage and queues the probation tasks |
| POST | `/api/employees/{slug}/status` | Manage: `{to: paused \| active \| offboarded, reason}` |
| PUT | `/api/employees/{slug}/authority` | Manage: `{rules: [...]}`; the answer includes `warnings` for rules below the company floor |
| GET / POST | `/api/employees/{slug}/tasks` | List (`?status=`) or give a task `{title, body, priority, due_at, plan_approval}` |
| PUT | `/api/employees/{slug}/inbox` | Manage: the e-mail inbox `{host, port, user, password, folder, allowed, enabled}` (password stored encrypted, never returned) |
| POST | `/api/employees/{slug}/inbox/test` | Connects to the inbox and returns the unread count |
| GET / POST | `/api/employees/{slug}/reports` | List reports, or generate one now (manage; `?period=daily\|weekly`) |
| GET / POST | `/api/employees/{slug}/routines` | List, or create `{title, body, cron, timezone?, priority?, enabled?}` (manage) |
| PATCH / DELETE | `/api/employees/{slug}/routines/{id}` | Change (`enabled=false` pauses it) or remove a routine (manage) |
| POST | `/api/employees/{slug}/routines/{id}/run` | Create the routine's task now, unless its previous task is still open (manage) |
| POST | `/api/employees/{slug}/webhook` | `{enabled}`: generates or rotates the inbound token (shown once) or turns the webhook off (manage) |
| GET | `/api/employees/{slug}/shadow` | Simulated actions (`?status=open` to review) and agreement stats |
| POST | `/api/employees/{slug}/shadow` | `{on, reason}`: switch shadow mode (manager or admin) |
| GET | `/api/employees/{slug}/career` | Level, criteria for the next one with values, warning signs, shadow stats, history |
| GET | `/api/employees/{slug}/suggestions` | What the decisions of the last 30 days suggest (loosen an action type, or a lesson) |
| POST | `/api/employees/{slug}/suggestions/{id}/apply` | `{text?}` (manage; loosening authority: manager or admin only) |
| POST | `/api/employees/{slug}/suggestions/{id}/dismiss` | Hides it for 30 days (manage) |
| POST / DELETE | `/api/employees/{slug}/lessons[/{id}]` | Add `{text}` or remove a lesson (manage) |
| GET | `/api/tasks/{id}` | Task with its events (timeline) and decision requests |
| POST | `/api/tasks/{id}/cancel` | The requester or someone who manages the employee |
| POST | `/api/tasks/{id}/quality` | `{score: 1-5, note}`: the manager's score for a finished task (replaces the judge's) |
| GET | `/api/workforce/dashboard` | `?days=30&team=`: manager dashboard (per employee and team, bottlenecks) |
| GET | `/api/decisions` | Open decisions you can take (yours first) |
| POST | `/api/decisions/{id}` | `{decision, edit?, reason?}`: `approve`, `approve_edited`, `reject`, `instruct`, `answer`, `ack`; for a missing capability, `attach` (`edit={"specialist": slug}`) or `build` (returns `build_prompt`) |
| POST | `/api/decisions/batch` | `{ids, decision, reason?}`; errors are returned per item |
| GET | `/api/admin/workforce` | Admin or auditor: every employee, decisions by age, approvals by type, expired, unclassified tools |
| POST | `/api/admin/workforce/stop-all` | Admin: pauses every employee on probation or active |
| GET / PUT | `/api/admin/action-catalog` | Classification of each tool (`{tool_ref, action_type, risk, reversible}`) |
| GET / PUT | `/api/admin/authority-floor` | Company floor (`{items: [{action_type, min_mode, separation}]}`) |

| GET / PUT | `/api/admin/notifications` | Admin: Slack app (`slack_bot_token`, `slack_signing_secret`, stored encrypted and never returned), `digest_hour` (-1 = off), `expiry_warn_min`, `due_soon_min`, `overdue_escalate_min`; also shows the e-mail status, the interactivity URL and the Slack manifest |
| GET / PUT | `/api/me/notifications` | Where the caller gets decisions: `{channel: auto\|slack\|teams\|email\|off, teams_webhook, digest}`; returns `available` and the effective `route` |
| POST | `/api/me/notifications/test` | Sends a test message through the effective channel |
| POST | `/hooks/slack/interactions` | Slack buttons and forms. No session: authenticated by the Slack signature |
| POST | `/hooks/slack/events` | Slack Events API (app mention, direct message): becomes a task. Slack signature |
| POST | `/hooks/teams/messages` | Teams outgoing webhook: becomes a task. HMAC with the security token |
| GET / POST | `/hooks/decide/{token}` | Signed decision link (Teams, e-mail). GET shows the action; only POST (`decision`, `reason`, `edit`) decides |

Tasks have `due_state` (`""`, `soon`, `overdue`, `escalated`), `plan`, `plan_state`, `progress`, `parent_task_id` and
`waiting_on`; the task detail adds `parent` and `children` (handoffs). Employees have `overdue_tasks`; the detail adds
`colleagues`, `plan_policy`, `recall` and `inbox`. `PATCH /api/employees/{slug}` accepts `colleagues` (slugs),
`plan_policy` (`off` | `auto` | `approve`) and `recall`.

`GET /api/me` also returns `decisions`: how many decisions wait for you (notices excluded).

**Inbound webhook** (outside `/api`, no session): `POST /hooks/employees/{slug}` with `Authorization: Bearer <token>`
(or `X-Hangar-Token`) and `{title, body?, dedupe_key?, priority?, due_at?, source?}`. It returns `{id, status,
duplicate}`; `401` for a bad token or a closed channel, `409` when the employee does not take tasks now.

## Plugins (`/api/plugins`)

See [plugins.md](plugins.md).

| Method | Path | Description |
|---|---|---|
| GET / POST | `/api/plugins` | List: approved plugins for everyone, drafts for their owners and admins. Create a draft from `{manifest (YAML, JSON or object), team?, source?}` (developers and admins) |
| POST | `/api/plugins/import-openapi` | `{document, name?, base_url?, only?}` returns a draft manifest from an OpenAPI 3 document (not saved) |
| GET / PUT / DELETE | `/api/plugins/{name}` | Detail: draft and approved manifests, permissions, installs, `can_review`. Update the draft (owner team). Delete (admin; owner only before the first approval) |
| POST | `/api/plugins/{name}/submit` | Send the draft for review |
| POST | `/api/plugins/{name}/review` | Admin who did not create or submit it: `{decision: approve\|reject, note}`. Approving classifies each tool in the action catalog |
| POST | `/api/plugins/{name}/disable` · `/enable` | Admin: turn the plugin off or back on |
| PUT / DELETE | `/api/plugins/{name}/install` | Team maintainer (or admin, also with no team = whole company): `{team, settings, secrets, credential, enabled}`. Secrets are never returned. DELETE takes `?team=` |
| POST | `/api/plugins/{name}/install/test` | `{team}`: calls the manifest's `test` tool |
| POST | `/api/plugins/{name}/install/connect` | `{team}`: OAuth2. Returns `authorize_url` for the browser and sets the signed state cookie |
| GET | `/api/plugins/oauth/callback` | Provider redirect: exchanges the code (PKCE), stores the tokens, returns to the plugin page |
| GET | `/api/plugins/{name}/install/logs` | `?team=`: last lines of the install's container (plugins with code), secrets masked |
| POST | `/api/plugins/{name}/tests` | Builders: runs the manifest's `tests` against the stage install; the report is pinned to the manifest's digest |
| POST | `/api/plugins/{name}/feature` | Admin: `{on}` features the plugin in the gallery |
| POST / DELETE | `/api/plugins/{name}/requests` | A team member asks for the plugin `{team, note}` (maintainers are notified). DELETE `?team=` dismisses (maintainer) |
| GET | `/api/plugins/{name}/export` | The approved manifest as YAML, without secrets (`?draft=true` for builders) |
| POST | `/api/plugins/{name}/install/hook-token` | `{team, stage}`: new token for unsigned triggers (shown once) |
| POST | `/hooks/plugins/{install}/{trigger}` | A system event: HMAC signature (manifest) or the install token. Creates the linked employee's task, deduplicated. On the stage install it only returns the preview |
| POST | `/api/plugins/{name}/triggers/{trigger}/test` | Builders: `{event}` (empty = the manifest's `example`): the task the event would create, nothing is created |
| GET | `/api/plugins/{name}/calls` | `?team=&stage=&tool=&status=&limit=&before=`: call log of an install (maintainers, admins, auditors) |

The install routes take `stage: true` for the stage install, a test credential for the draft. Only builders use it,
and it never serves agents. `PUT /api/plugins/{name}/install` also takes `oauth_client_id`, `oauth_client_secret` and
`triggers: {name: {employee, enabled, priority}}` and `rate_limit` (calls per minute; 0 = the default).

## Day-to-day work

Goals, projects, the task's conversation, budgets, the inbox and search: see [work.md](work.md#api).

## User portal

| Method | Path | Description |
|---|---|---|
| — | — | Issues (kanban board): see [issues.md](issues.md#api) |
| — | — | Member workspace (policy, sign-ins, activation codes, personal agents, notifications): see [workspace.md](workspace.md#api) |
| GET | `/api/me/home` | The portal's former Início page (now *Painel completo*): `attention`, `agents`, `counts`, `to_decide`, `my_requests`, `teams` (budget), `my_usage` (30 days), `schedules`, `keys`, `memory`, `catalog_news`. Filtered by the caller's permissions |

## Agent memory

| Method | Path | Description |
|---|---|---|
| GET | `/api/memory/status` | Backend, models, write queue (`pending`, `processed`, `failed`). Admin or auditor |
| GET | `/api/agents/{slug}/memory?env=prod\|stage&q=&limit=` | Facts of the agent's memory group (search with `q`). Needs `view_spec` |
| DELETE | `/api/agents/{slug}/memory?env=prod\|stage` | Delete the group's memory. Needs `manage`; org memory is admin only |

Agents reach the memory at `/internal/memory/mcp` with their internal token and per-environment token. See
[memory.md](memory.md).

## SCIM 2.0 (`/scim/v2`, scope `scim`)

`Users`, `Groups`, `ServiceProviderConfig`, `ResourceTypes` and `Schemas`, with filters (`userName eq`,
`externalId eq`, `displayName eq`) and PATCH in the Entra ID and Okta formats. See [access.md](access.md#scim-20-provisioning).

## Agents and platform (`/api`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Open. `{status, version}` |
| GET | `/api/overview` | KPIs, 14-day series, top agents, cost |
| GET | `/api/spec/schema` | JSON Schema of the agent spec |
| GET / POST | `/api/agents` | List (with `team`, `visibility`, `access`, `permissions`) / register `{name, objective, final_output, owner?, slug?, team?, visibility?}` (developer+) |
| GET | `/api/agents/{slug}` | Detail: spec, versions, tests, deployments, jobs, endpoints, usage |
| PATCH | `/api/agents/{slug}` | JSON Merge Patch on spec (and name/objective/final_output/owner) |
| PUT | `/api/agents/{slug}/spec` | Replace the spec |
| POST | `/api/agents/{slug}/rollback` | `{version}` → new version with that spec |
| POST | `/api/agents/{slug}/edit` | Edit later: `{changes, test, promote, note}`. Creates a new version, runs the tests in stage (`test`) and, if they pass, promotes to production or asks for approval (`promote`). Returns what changed |
| GET | `/api/agents/{slug}/diff?from_version=&to_version=` | What changed in the spec between two versions |
| POST | `/api/agents/{slug}/test` | Deploy stage + run tests |
| POST | `/api/agents/{slug}/deploy` | `{env: stage\|prod}`. Prod is gated by tests; without the right to promote it returns `status: approval_pending` and a request |
| POST | `/api/agents/{slug}/ship` | Sub-agents → stage → tests → prod. The last step can be `approval_pending` |
| POST | `/api/agents/{slug}/stop` | `{env}` |
| POST | `/api/agents/{slug}/chat` | `{message, env}` |
| GET | `/api/agents/{slug}/logs?env=` | Container logs |
| DELETE | `/api/agents/{slug}` | Delete agent and containers |
| POST | `/api/agents/{slug}/job` | `{task, env, timeout_s?, wait}`; `wait=false` returns immediately |
| GET | `/api/jobs/{id}` | Job state |
| POST | `/api/jobs/{id}/cancel` | Cancel |
| GET | `/api/jobs/{id}/events` | **SSE**: `status`, `log`, `done` |
| GET | `/api/catalog` | Skills, MCP servers, connections (keys masked) |
| POST | `/api/catalog/skills` · `/api/catalog/mcps` · `/api/catalog/llm` | Upsert. For `/llm`, `model_name` is optional (the default comes from the provider's model list, fetched on save) and `provider` names the card (`openai`, `openrouter`, `litellm`…) |
| DELETE | `/api/catalog/llm/{name}` | Refused while an agent uses it |
| POST | `/api/catalog/llm/{name}/test` | Real, minimal call to the connection's LLM (checks URL, key and model): `{ok, latency_ms, detail}`. Admin |
| PATCH | `/api/catalog/llm/{name}/code` | `{allow_code}`: approve a connection to receive code (coding mode, code evaluations) when `code_policy` is `approved`. Admin |
| GET | `/api/catalog/providers` | Known LLM providers (logo, base URL, protocol, usual model types) and the model types |
| GET | `/api/catalog/models?kind=&connection=&all=` | Models of the company's connections: type, features, context, price, `available`, `default`. `all=true` includes models gone from the provider |
| POST | `/api/catalog/llm/{name}/models/sync` | Refresh the connection's model list now: `{ok, total, added, lost, error}`. Admin |
| POST | `/api/catalog/llm/{name}/models` | `{model_id, kind?, price_in_per_mtok?, price_out_per_mtok?}`: add a model by hand (providers without a list). Admin |
| PATCH | `/api/catalog/llm/{name}/models/{model_id}` | `{kind}` fixes the type, `{set_price, price_in_per_mtok, price_out_per_mtok}` the price (refreshes keep it), `{default: true}` makes it the connection's default. Admin |
| GET | `/api/catalog/models/recommend?role=&caps=&connection=&limit=` | Best models for a role (`planner`, `coder`, `analyst`, `writer`, `support`, `extractor`, `classifier`, `general`): score, confidence, reasons, real performance here. `caps`: `tools,vision` |
| GET | `/api/agents/{slug}/execution?env=prod` | Execution profile: what the spec asks (`execution`), what is actually sent to the provider or the harness CLI (`sends`), where the effort came from, and warnings |
| GET | `/api/agents/{slug}/execution/tuning` | Measurement by profile (model and effort: calls, share without errors, time, cost, test cases), the latest profile comparison and the suggestion |
| POST | `/api/agents/{slug}/execution/trials` | `{levels?}`: compare effort levels on the test cases in stage, in the background (costs model calls) |
| GET | `/api/agents/{slug}/execution/trials/{id}` | One profile comparison, with partial results while it runs |
| GET | `/api/agents/{slug}/model-recommendation?role=` | The agent's role, current model and suggestion (a multi-agent returns the orchestrator and each specialist). Nothing is changed |
| GET · PUT | `/api/catalog/model-profiles/settings` | How profiles are researched: `connection`, `model`, `auto` (`off`/`used`/`all`), `monthly_budget_usd`, `per_run`, `max_age_days`, `web` (`brave`/`tavily`/`searxng`), `search_key` and `aa_key` as `secret://name`, `search_url`. PUT is admin |
| POST | `/api/catalog/model-profiles/refresh` | Fill profiles from the free sources now. Admin |
| GET | `/api/catalog/model-profiles/{model}` | A model's profile: summary, strengths, weaknesses, best_for, scores `{area: {value, source, basis}}`, metrics, sources |
| POST | `/api/catalog/model-profiles/{model}/research` | Research the profile now (spends from the monthly budget). Admin |
| PATCH | `/api/catalog/model-profiles/{model}` | Fix `summary`, `strengths`, `weaknesses`, `best_for`, `scores` (0–100, `null` clears); fixed fields are locked, `unlock: [fields]` releases them. Admin |
| DELETE | `/api/catalog/llm/{name}/models/{model_id}` | Only models added by hand or gone from the provider, and not used by an agent. Admin |
| POST | `/api/apply` | GitOps document `{skills, mcp_servers, agents}` (idempotent) |
| GET | `/api/templates`, `/api/templates/{id}` | Gallery |
| POST | `/api/templates/{id}/apply` | `{connection?, harness_connection?}` |
| GET / POST | `/api/keys` | Your keys (admin: all) / create `{name, scopes: [user\|invoke\|admin\|scim], agents: []}`; the key is returned once |
| DELETE | `/api/keys/{id}` | Revoke (also disconnects a tool connection) |
| GET | `/api/connect/clients` | Tools that can be connected and the modes each supports (`mcp`, `model`) |
| GET | `/api/agents/{slug}/connections` | Active tool connections of an agent |
| GET | `/api/agents/{slug}/connections/snippet?client=&mode=` | Config preview with a placeholder key; creates nothing |
| POST | `/api/agents/{slug}/connections` | `{client, mode}` → per-tool `invoke` key (returned once) + `{language, file, content, steps}` |
| POST | `/api/agents/{slug}/connections/test` | `{env}` → end-to-end test of the coding-agent path (client tools over streaming): `{ok, steps[], reply}`. Prod needs `consume`, stage needs `edit` |
| GET | `/api/tests`, `/api/deployments`, `/api/usage`, `/api/audit` | Recent records |

## Gateway (`/gw/<slug>`: invoke key for that agent, a user who can use it, or admin)

`/gw/<slug>/…` = prod, `/gw-stage/<slug>/…` = stage.

| Protocol | Path |
|---|---|
| OpenAI-compatible | `POST v1/chat/completions` (`stream: true` supported), `GET v1/models` |
| A2A | `GET .well-known/agent.json` (also `agent-card.json`), `POST a2a` (`message/send`) |
| ACP | `GET acp/agents`, `POST acp/runs` |
| MCP | `POST mcp` (Streamable HTTP, stateless, JSON responses) |
| AG-UI | `POST agui` (a `RunAgentInput`; the answer is an SSE stream of AG-UI events). See [clients.md](clients.md#ag-ui-the-agent-inside-your-app) |

A remote agent (spec `remote`) answers the same paths: the hangar turns each call into an AG-UI run on the agent.
`POST /api/agents/<slug>/remote/test {"env": "stage"}` checks the address, the vault and one full run.

Optional header `X-Channel: <name>` tags usage for per-channel metrics. Without it, a key created by a tool
connection tags usage with its tool (`claude-code`, `open-webui`, …); other keys fall back to `api`.

## Platform MCP (`/mcp`, admin key, personal token or OAuth token)

The client acts with the key owner's roles. The tools are:

- **Guidance:** `platform_guide`, `get_spec_schema`, `whoami`.
- **Access:** `request_agent_access`, `list_approvals`, `decide_approval`.
- **Schedules:** `schedule_agent`, `list_schedules`, `update_schedule`, `delete_schedule`, `run_schedule_now`, `schedule_runs`.
- **Catalog:** `list_catalog`, `list_llm_models`, `get_model_profile`, `check_model_needs`, `recommend_models`, `suggest_agent_models`, `get_execution_profile`, `get_execution_tuning`, `compare_execution_profiles`, `register_llm_connection`, `register_skill`, `register_mcp_server`.
- **Templates:** `list_templates`, `apply_template`.
- **Build:** `register_agent`, `design_agent` (merge patch plus `remove`), `rollback_agent`, `build_multi_agent`, `list_avatars`.
- **Inspect:** `list_agents`, `get_agent`, `get_agent_spec` (spec, plus which version runs in prod and in stage).
- **Edit later:** `edit_agent` (changes, then stage and tests, then publish when `promote=True`), `diff_agent_versions`, `update_agent_access`.
- **Test and deploy:** `run_tests`, `deploy_stage`, `promote_to_production`, `ship_agent`, `stop_agent`.
- **Use:** `chat_with_agent`, `run_harness_job` (`wait`), `get_job`, `cancel_job`, `create_consumer_key`,
  `connect_agent` (per-tool key + ready-to-paste config).

## Internal (`/internal`, called from containers only)

These routes are:

- `POST /internal/jobs/{id}/callback`, authenticated by the job token.
- `/internal/gw/…`, used for agent → sub-agent calls.
- `GET /internal/dashboard/*`, a read-only view for agents with `platform_dashboard`.
- `POST /internal/gate`, called by a Digital employee's runtime before every tool call: `{task_id, tool, ref, args,
  rationale, kind}` returns `allowed`, `waiting` (with `request_id`) or `denied`.

- `POST /internal/plugins/{name}/mcp`, the MCP server (stateless, JSON responses) that serves an installed plugin to
  agents whose spec lists it. It injects the team's credential.

The last four are authenticated by `X-Agent-Slug` plus that agent's HMAC token.
