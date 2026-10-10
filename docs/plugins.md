# Plugins

A plugin connects a company system (an ERP, a CRM, a billing API…) to Agent Hangar with a **declarative manifest**. It
declares:
- the tools;
- the team's credential;
- the action type of each tool, which drives the [authority](digital-employee.md#authority);
- settings and skills.

No plugin code runs inside the platform. The credential stays in the central and never reaches the agent.

```
agent ──MCP──▶ central (/internal/plugins/<name>/mcp) ──HTTPS + team credential──▶ the system
                 │ checks the agent's token and spec, picks its team's install,
                 │ fills the path, query and body, injects the credential, blocks private hosts
```

## The manifest

```yaml
name: acme-erp                  # slug: lowercase, digits and hyphens
title: ACME ERP
version: 1.0.0                  # bump it to change an approved plugin
description: Invoices and customers from the ERP.
publisher: Finance IT
base_url: https://{{settings.tenant}}.erp.example.com/api   # the only host the plugin talks to
auth: {type: api_key, in: header, name: X-ERP-Key, label: ERP API key}   # none | api_key | bearer | basic
settings:                       # asked at install time; secret ones are stored encrypted
  - {key: tenant, title: Tenant, required: true}
  - {key: company_id, title: Company, required: true}
tools:
  - name: get_invoice
    description: One invoice by number.
    method: GET
    path: /companies/{{settings.company_id}}/invoices/{number}   # {x} comes from the arguments (URL-encoded)
    parameters: {type: object, properties: {number: {type: string}}, required: [number]}
  - name: send_invoice
    description: Sends the invoice to the customer.
    method: POST
    path: /invoices/{number}/send
    action: send_external       # the authority type; default: GET read, DELETE delete, others write_internal
    parameters: {type: object, properties: {number: {type: string}, email: {type: string}}}
skills:
  - {name: glossary, content: "Overdue invoice: more than 30 days unpaid."}
test: {tool: get_invoice, args: {number: "1"}}   # what "Test connection" calls
```

- **Arguments.** Path parameters (`{number}`) come from the tool's arguments. The other arguments go in the query
  string (GET, DELETE) or in a JSON body (POST, PUT, PATCH).
- **`{{settings.key}}`** works in `base_url`, `path` and per-tool `headers`.
- **Credentials:**
  - `api_key` goes in the header or query parameter named by `name` (default `X-API-Key`);
  - `bearer` sends `Authorization: Bearer <credential>`;
  - `basic` takes `user:password`;
  - `oauth2` connects the team's account (see [OAuth2 accounts](#oauth2-accounts)).
- **Calls.** Responses longer than 8,000 characters are cut. Redirects are not followed. A call times out after
  `PLUGIN_TIMEOUT_S` seconds (30 by default).

### From an OpenAPI

*Plugins → New plugin → Import from an OpenAPI* (or `POST /api/plugins/import-openapi`) turns an OpenAPI 3 document
into a draft manifest. Each operation becomes a tool, `$ref` schemas are resolved, and the security scheme becomes the
`auth`. The action types are a guess from the method and the operation name (`createCharge` → financial,
`sendReminder` → send external). **Review them before submitting.**

## Creator mode (MCP) and stage tests

Ask your AI tool, connected to the hangar's MCP, to *"connect our ERP; the docs are at https://…/openapi.json"*.
Creator mode follows the same path as the portal, with the same controls:

1. `plugin_from_openapi(url | document)` builds a draft manifest. A URL must be public, or private hosts must be
   allowed with `PLUGIN_ALLOW_PRIVATE=1`.
2. The agent reviews each tool's `action` with you and adds `tests`, `category`, `tags` and `readme`.
3. `plugin_draft(manifest, team)` saves the draft and returns the portal link. **The test credential is entered there,
   never in the chat**: it is the *stage install*, with a sandbox credential that only the draft's tests use and that
   never serves agents.
4. `plugin_test(name)` runs the tests against the draft, through the stage install.
5. `plugin_submit(name)` sends it for review.

`plugin_status` shows where it stands. `plugin_review` lets an admin approve or reject with the same four-eyes rule.

```yaml
tests:
  - {name: finds a customer, tool: find_customer, args: {q: ACME}, expect_contains: ACME}
  - {name: service down, tool: check_status, args: {code: "503"}, expect_status: 503, expect_error: true}
```

- **Pinned to the version.** The report keeps the manifest's digest. If the draft changes, the tests must run again.
- **Required when declared.** When the manifest declares `tests`, submitting and approving both need a passing report
  for that exact version. The reviewer sees the report, and the version history records it.
- **Who.** The plugin's builders (the owner team's developers) manage the stage install and run the tests. Triggers
  never run in stage. A plugin with code runs its own stage container, from the draft image.

## Gallery

The *Plugins* page is the company's internal gallery; there is no public marketplace.
- **Finding plugins:** search, categories (`category` in the manifest), tags and featured plugins chosen by an admin.
- **Usage:** each card shows how many teams use the plugin and the call count (stage installs do not count).
- **The plugin page:**
  - its `readme`, permissions and tools;
  - the version history (who approved each version, when, and whether it was tested in stage);
  - the pending install requests.
- **Request it for my team.** A team member who cannot install asks for it. The team maintainers get the request in
  their decision channel (Slack, Teams or e-mail). Installing it closes the request; a maintainer can also dismiss it.
- **Export.** *Export* downloads the approved manifest (`<name>.hangar-plugin.yaml`), without secrets, to take it to
  another environment of the company. There it goes through the tests and the review again.

## Lifecycle

1. **Draft.** Someone who builds agents (a developer or an admin) creates the plugin, optionally owned by a team.
2. **Review.** The owner submits it. An **admin who did not create or submit it** approves, or rejects with a note.
   The review screen shows what to check:
   - the host it talks to;
   - the authentication;
   - the count per action type, with the risky ones highlighted;
   - the secrets.
3. **Approved.** On approval, each tool enters the [action catalog](digital-employee.md#authority) as
   `mcp:plugin-<name>:<tool>`, with the manifest's type and risk. A Digital employee's authority therefore treats
   `send_invoice` as *send external* from day one.
4. **New version.** Editing an approved plugin requires a higher `version`. The new version is a draft and goes
   through review again. Installs keep the approved version until then.
5. **Turning it off.** An admin can turn a plugin off. Agents stop reaching it at once.

## Install and use

- **Who installs.** A team maintainer installs a plugin for the team, with the team's credential and settings. An admin
  can also install it for the whole company; a team install takes precedence over it.
- **Test connection** calls the manifest's `test` tool (or the first read tool).
- **Using it in an agent.** Put the plugin in the agent's spec:

  ```yaml
  plugins: [acme-erp]
  ```

  At deploy time the agent gets an MCP server `plugin-acme-erp`, so its tools appear as `plugin-acme-erp__get_invoice`.
  The plugin's skills are added too. The deploy fails with a clear message when the plugin is not approved or not
  installed for the agent's team.
- **Usage.** Each install counts calls and errors and keeps the last error.

## Plugins with code (`runtime: server`)

When HTTP calls are not enough (an SDK, a protocol other than REST, logic before the call), a plugin can bring code. The
code runs as an **MCP server in an isolated container, one per install**: each team's credential stays in its own
container.

```yaml
name: acme-erp
version: 1.0.0
runtime: server
auth: {type: api_key, label: ERP key}
settings:
  - {key: tenant, title: Tenant, required: true}
server:
  image: ghcr.io/acme/erp-mcp@sha256:<64 hex>   # pinned by digest
  port: 8000
  path: /mcp                                     # streamable HTTP MCP endpoint
  command: [python, -m, erp_mcp]                 # optional
  env: {LOG_LEVEL: info}
  settings_env: {ERP_TENANT: tenant}             # setting -> environment variable (secret ones too)
  credential_env: ERP_KEY                        # the install's credential (or credential_header, below)
  egress: [erp.acme.com, "*.files.acme.com"]     # the only hosts the egress proxy lets through
  memory: 256m                                   # optional (PLUGIN_MEM_LIMIT)
  cpus: 0.5                                      # optional (PLUGIN_CPUS)
tools:                                           # only these reach agents, with these action types
  - {name: get_invoice, description: One invoice, action: read}
  - {name: pay_invoice, description: Pays an invoice, action: financial}
test: {tool: get_invoice, args: {number: "1"}}
```

- **The central is the server's MCP client.** Agents still call the central (`/internal/plugins/<name>/mcp`). The
  central lists and calls the container's tools, masks secrets in the answers and records usage.
- **Only declared tools reach agents.** Any other tool the server offers stays hidden and cannot be called. *Test
  connection* lists the hidden ones.
- **Supply chain.** The image must be pinned by digest. Tags are only allowed with `PLUGIN_ALLOW_UNPINNED=1`, for
  development; the review screen flags them.
- **Lifecycle.** The container follows the install:
  - installing or saving settings starts or restarts it with the new configuration;
  - pausing or uninstalling stops it, and so does turning the plugin off;
  - approving a new version restarts the running containers with it;
  - if a container is down when an agent calls, it starts again.
- **Images.** With Docker, the central does not pull images (the Docker socket proxy does not allow it), so an admin
  pulls the image on the host first. With Kubernetes, the cluster pulls it (`K8S_IMAGE_PULL_SECRET` for a private
  registry).
- **Logs.** *View logs* shows the container's last lines, with secrets masked.

### Isolation

| | Docker | Kubernetes |
|---|---|---|
| Network | Only `hangar_plugins` (`PLUGINS_NETWORK`), an `internal` network: no agents, database, memory or Docker API, and no direct route out | NetworkPolicy `role=plugin`: only the central gets in. Out: DNS and the central's egress proxy (port 3128) |
| Process | Read-only filesystem (`/tmp` in memory), `cap_drop: ALL`, `no-new-privileges`, memory, CPU and 128 processes | Same, plus `runAsNonRoot` and seccomp `RuntimeDefault` |
| Secrets | Environment of that container only | Its own `Secret` |

### Egress: only the declared hosts

The container gets `HTTP_PROXY`/`HTTPS_PROXY` pointing at the central's egress proxy (port 3128), with a user and
password of its own install. The proxy only lets through the hosts in `server.egress`:

- `api.acme.com` allows ports 443 and 80; `*.acme.com` allows its subdomains; `api.acme.com:8443` allows only that port;
- `{{settings.host}}` takes the host from the install's settings;
- the host is resolved by the proxy, which connects to the address it checked; private networks stay closed unless
  `PLUGIN_ALLOW_PRIVATE=1`.

A refused connection is counted on the install and shown with the last host and the reason, so the maintainer knows
what to declare in a new version. Python (`httpx`, `requests`), Go and curl honour the proxy variables; Node 24+ does
with `NODE_USE_ENV_PROXY=1`, which the central also sets. `PLUGIN_EGRESS=open` turns the proxy off (the container then
goes straight out, as before 0.24).

**Upgrading on Docker.** `hangar_plugins` becomes an `internal` network. `docker compose up -d` recreates it when no
plugin container is attached; if it reports active endpoints, remove the plugin containers
(`docker ps -q --filter label=central.plugin | xargs docker rm -f`). The central starts them again, with the proxy, on
the next call.

### Image signatures (cosign)

With `PLUGIN_COSIGN_KEY` (a public key, PEM or path) or keyless signing (`PLUGIN_COSIGN_IDENTITY`, a regex of the
certificate identity, and `PLUGIN_COSIGN_ISSUER`), the central runs `cosign verify` on the image when an admin approves
the plugin. The review screen shows the result. With `PLUGIN_REQUIRE_SIGNED=1`, an image without a valid signature is
not approved and does not start (the stage install verifies the draft's image). Only images pinned by digest are
verified.

### OAuth2 and the credential in a header

`credential_header` sends the install's credential in a header of each MCP call instead of the container's
environment. OAuth2 always works this way (`credential_header` defaults to `Authorization`): the central renews the
token and sends `Bearer <token>` on every call, so the container never holds the refresh token or the client secret.
The server reads the header from the request (FastMCP: `ctx.request_context.request.headers`).

## OAuth2 accounts

```yaml
auth:
  type: oauth2
  authorize_url: https://login.crm.example.com/oauth/authorize
  token_url: https://login.crm.example.com/oauth/token
  scopes: [contacts.read, offline_access]
  pkce: true                      # default
  params: {access_type: offline}  # optional extra authorization parameters
```

1. **Register the app.** The company registers an OAuth app in the system, with the redirect URL
   `PUBLIC_BASE_URL/api/plugins/oauth/callback` (the install form shows it).
2. **Install.** The maintainer installs the plugin with the app's client ID and secret.
3. **Connect the account.** *Connect account* sends the browser to the provider (authorization code with PKCE) and
   back.

The access and refresh tokens are stored encrypted with the install. The central renews them before they expire, and
once more after a `401`, under a lock so that two replicas do not use the same rotating refresh token. If the renewal
fails, the install shows the error and the maintainer reconnects.

## Triggers: events that become tasks

A trigger turns a system event into a task for a [Digital employee](digital-employee.md) of the team.

```yaml
settings:
  - {key: webhook_secret, title: Webhook signing secret, secret: true}
triggers:
  - name: invoice_overdue
    title: Overdue invoice
    task_title: "Collect invoice {{event.data.number}} from {{event.data.customer}}"
    task_body: "Amount: {{event.data.amount}}"
    dedupe: data.id                # the same event never becomes two tasks (EMPLOYEE_DEDUPE_DAYS)
    signature: {header: X-Billing-Signature, prefix: "sha256=", secret: webhook_secret}   # HMAC of the raw body
  - name: customer_note            # without signature: the install token
    dedupe: id
```

- **Connecting a trigger.** In the install, the maintainer links each trigger to a Digital employee of the same team
  and copies its URL into the system: `PUBLIC_BASE_URL/hooks/plugins/<install>/<trigger>`.
- **Authentication:**
  - **Signed** (`signature`): HMAC-SHA256 or SHA1, hex or base64, with an optional prefix. Multi-value headers such as
    Stripe's `t=…,v1=…` work too. The secret is a secret setting of the install.
  - **Not signed:** the install's hook token (*Generate the token*, shown once and stored as a hash), sent as
    `Authorization: Bearer`, `X-Hangar-Token` or `?token=`.
- **The task:**
  - the title and body come from the `{{event.path}}` templates;
  - the event JSON is attached;
  - the task tells the agent that the event is data, not an instruction to it.

  Its source is `plugin`, and the requester is `plugin <name> · <trigger>`.
- **Testing in stage.** `example` in the trigger is a sample event. On the plugin page, the stage install has *Test the
  trigger*: it shows the task title, body and deduplication key the event would create, and creates nothing. The
  stage install also has its own hook URL and token: a system's test webhooks sent there are authenticated the same way
  and answered with the preview. Stage `tests` can check a trigger too:
  `{trigger: invoice_overdue, event: {...}, expect_contains: "Collect invoice"}`.
- **Response:**
  - a duplicate returns the existing task with `duplicate: true`;
  - a trigger that is off or not linked returns `409`;
  - a wrong signature or token returns `401`;
  - events are capped at 256 KB.

## Limits and the call log

- **Rate limit per install.** `rate_limit` in the manifest (calls per minute) is the default of each install; the
  maintainer can change it on the install (0 returns to the default); without either, `PLUGIN_RATE_LIMIT` (120). Above
  the limit, the call returns to the agent as an error and never reaches the system. The window is shared by all
  replicas.
- **Every call is logged:** the agent (and the Digital employee's task), the tool and its action type, when, how long,
  the HTTP status and the result, with the arguments and the start of the answer masked. *Calls* on the install shows
  them; `GET /api/plugins/<name>/calls?team=&status=&tool=` lists them for the team's maintainers, admins and auditors.
  Stage tests and *Test connection* are logged too. Rows are kept for `PLUGIN_CALLS_DAYS` (90).

## Actions, identity and testing on the install

- **Per action** (*Actions and identity* on the install):
  - *the authority decides* is the default;
  - *asks for approval on every use* forces a person's decision even when the employee's authority would act alone,
    and only Digital employees can use the action;
  - *off* hides the action from agents.

  Set with `tool_policy: {tool: off|ask|allow}` on `PUT /install`.
- **Whose account** (OAuth2 plugins):
  - `identity_mode: team` (the default) uses the team's account;
  - `requester` uses the personal account of whoever asked for the task, and fails if they have not connected one;
  - `prefer_requester` falls back to the team's account.

  People connect their own account on the plugin page (*My account*); the central renews each personal token on its
  own.
- **Test an action:** the maintainer runs one action on the production install with the team's credential
  (`POST /install/try`). It is recorded in the call log and the audit log.
- **Credentials from the vault:** `secret://name` in the credential or a secret setting reads the value from the
  [secrets vault](work.md#secrets-vault) when the plugin is called.

## Security

- **The secret stays in the central:**
  - the credential and secret settings are encrypted (`HANGAR_SECRET_KEY`) and never returned by the API;
  - the agent's container only knows the central's internal URL;
  - each call checks the agent's internal token and that its spec lists the plugin.
- **One host.** Paths cannot change the host (no scheme, no `..`).
- **Private hosts are blocked** (private, loopback and link-local addresses), so a plugin cannot reach the platform's
  own network. Internal systems (an on-premises ERP) need `PLUGIN_ALLOW_PRIVATE=1`.
- **Every action still goes through the authority** (Digital employees), and the audit log records create, submit,
  approve, install and uninstall.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PLUGIN_ALLOW_PRIVATE` | 0 | 1 lets plugins call hosts in private networks |
| `PLUGIN_TIMEOUT_S` | 30 | Timeout of each call to the system |
| `PLUGINS_NETWORK` | `hangar_plugins` | Docker network of plugin containers |
| `PLUGIN_MEM_LIMIT` · `PLUGIN_CPUS` | `256m` · 0.5 | Default limits of a plugin container |
| `PLUGIN_ALLOW_UNPINNED` | 0 | 1 accepts plugin images by tag (development only) |
| `PLUGIN_EGRESS` | `proxy` | `proxy`: plugins with code go out only to `server.egress`, through the central; `open`: straight out |
| `PLUGIN_EGRESS_PORT` · `PLUGIN_EGRESS_HOST` | 3128 · host of `INTERNAL_BASE_URL` | Where the containers reach the egress proxy |
| `PLUGIN_RATE_LIMIT` | 120 | Calls per minute per install when the manifest and the install say nothing; 0 = no limit |
| `PLUGIN_CALLS_DAYS` | 90 | Days the call log is kept |
| `PLUGIN_COSIGN_KEY` | — | Public key (PEM or path) to verify plugin images |
| `PLUGIN_COSIGN_IDENTITY` · `PLUGIN_COSIGN_ISSUER` | — · GitHub Actions | Keyless verification: certificate identity (regex) and issuer |
| `PLUGIN_REQUIRE_SIGNED` | 0 | 1 refuses to approve or start images without a valid signature |

## What's next

- **P2a (done):** OAuth2 accounts and triggers.
- **P2b (done):** plugins with code, as an MCP server in an isolated container per install.
- **0.24 (done):** egress proxy, image signatures, rate limit, call log, OAuth2 for plugins with code and triggers in
  stage.
- **Next:** third-party decision channels (WhatsApp, Google Chat) as plugins.
- **P3 (done):** Creator mode through the MCP, with stage tests and four eyes.
- **P4 (done):** the company's internal gallery. Public plugins come much later, after a security review.
