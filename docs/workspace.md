# Member workspace

The company account is the **workspace**. The admin (the company's owner) sets how members sign in and what they
can create on their own. Each member gets a simple portal at `/app` for their own agents.

There is no desktop app to install. The portal installs as an app (PWA) on Windows, macOS, Linux, Android and
iPhone, and it can show decision requests as device notifications. The member's own AI (Claude, ChatGPT, Codex,
Cursor, Gemini, VS Code, OpenCode) keeps connecting through the platform MCP, exactly as before.

## What the member sees

The portal shows only what belongs to the person, even when that person is an admin:
- The menu has nine items: Início, Minhas issues, Issues, Meus agentes, Organograma, Decisões, Caixa de entrada, Conectar minha IA, Meus acessos.
- Everything else (building agents, catalog, plugins, queues, budgets, teams, governance…) is in the console (`/ui`), for admins.
- In the portal, other addresses go back to Início. Projects, agents, Digital employees and tasks open only as a detail page, from a link.
- The global search only returns issues, Digital employees and tasks.
- "Primeiros passos" are the person's own: connect your AI, create a personal agent, create the first issue, choose where you get decisions.

| Page | What it does |
|---|---|
| **Início** | Shows decisions waiting for you, your agents working right now (and on what), the tasks you asked for, and this month's cost. |
| **Meus agentes** | Lists your personal agents and the team agents you manage. Each one shows its model, harness, level and specialists. From here you create a personal agent, ask your AI to create one, or ask to promote one to the team. |
| **Organograma** | Your own structure: you at the top (CEO if you are the company admin, Staff otherwise) and, below, your agents: the personal ones and the ones you manage or own. Each card shows model, harness, level, status and the current task, and who reports to whom is kept. Drag an agent onto another to change who it reports to; drop it on your own card to have it report directly to you. The company-wide org chart, with every team, is in the console. |
| **Decisões** | Unchanged. |
| **Caixa de entrada** | Unchanged. |
| **Conectar minha IA** | Generates a personal key and gives the ready-made config for Claude Code, Claude Desktop, Codex, Cursor, Gemini CLI, VS Code and OpenCode (copy or download). Claude.ai and ChatGPT use OAuth, with no key. |
| **Meus acessos** | Shows where your account is signed in (browser and system, how you signed in, last use, IP). You can end one sign-in or all the others. Also: an activation code for another browser, installing the app, and turning notifications on. |

Dragging on the org chart:
- If you manage the agent, the change is saved.
- If you don't, it becomes an `org_change` proposal. The agent's manager decides it in *Decisões*.


## Signing in

A member signs in with one of:
- **SSO:** Microsoft Entra ID, Google, GitHub or generic OAuth2 (see [access.md](access.md#signing-in)).
- **Username and password**, for a local account.
- **Activation code** (the "virtual key"): `XXXX-XXXX-XXXX`, single use, valid for 24 hours. Only its hash is stored. It comes from:
  - the admin's invite (*Membros*, in the console), valid for 7 days;
  - the member (*Meus acessos*), to open another browser. This creates a link like `/app/#/login?code=…`.
- **Personal token** on the sign-in screen.

The workspace **policy** turns each method on or off. At least one method always stays on. The policy covers
signing in to the portal only: the MCP keeps accepting personal keys and OAuth tokens.

Each sign-in records how it happened, the browser and system, the IP and the last use. The admin sees every open
sign-in in the workspace and can end one, or all of a member's.

## Personal agents

A personal agent is a Digital employee that belongs to one member:
- Only its owner sees and manages it. Admins and auditors also see it; the rest of the team does not.
- Its manager is the owner, and it lives in the owner's team.
- It follows the workspace rules. The admin sets in the policy:
  - who may create one (any member, or developers and maintainers);
  - how many each member may have;
  - which LLM connections they may use;
  - a monthly budget per member. When it's reached, the member's personal agents stop with the scope *agentes pessoais do membro*.
- Authority, the company floor, audit and shadow mode are the same as for any Digital employee.

**Promotion to the team:**
1. The owner asks (*Promover para o time*).
2. Another maintainer of the team decides. The owner can't approve their own request.
3. On approval, the agent stops being personal and takes the company's default visibility.

From your AI, through the platform MCP:
- `hire_employee` with `personal=true` creates a personal agent;
- `promote_personal_agent` asks for the promotion;
- `my_workspace` returns the summary from *Início*.

## App and notifications (PWA + Web Push)

The portal has:
- a manifest;
- icons;
- a service worker scoped to `/app/`. It shows notifications and, offline, a simple page. It caches no API data.

To install it:
- in Chrome or Edge, use the install icon in the address bar;
- on iPhone, use *Share → Add to Home Screen*. iOS only delivers notifications to the installed app.

**Notifications:**
1. The member turns them on in *Meus acessos*.
2. The browser subscribes, and the subscription is stored per member. The browser's auth key is encrypted.
3. Every new, escalated or expiring decision request goes to all of the member's subscribed browsers. This is in addition to Slack, Teams or e-mail.
4. Clicking the notification opens the decision.
5. A subscription the push service reports as gone (404/410) is deleted.

**How it's built:**
- Web Push uses VAPID (RFC 8292) and the `aes128gcm` content encoding (RFC 8291), implemented with `cryptography`. There is no new dependency.
- The VAPID key pair belongs to the installation. It is generated on first use and stored encrypted with `HANGAR_SECRET_KEY`. You can set it instead with `WEBPUSH_VAPID_PRIVATE_KEY` (PEM).
- `WEBPUSH_SUBJECT` (a `mailto:`) identifies the sender to the push services.
- Browsers only allow service workers and notifications over HTTPS. `localhost` also works, for testing.

## Admin: members

Only the admin manages members, in the console (`/ui/#/members`). The portal shows each member only what belongs to
their own account. **Membros** has three tabs:
- **Pessoas:**
  - **Convidar um membro:** e-mail, name, company role, team and team role. The person gets an e-mail with the link to `/app`. The link signs in once, without a password, and is valid for 7 days. After that they sign in with SSO, or with a code from *Meus acessos*.
  - Without SMTP configured, the invite is still created and the admin gets the link to send. *Reenviar* issues a new link, and the old one stops working.
  - Below the invite form are the pending invites and the company's people: role, teams, last sign-in, local account, deactivate. This replaces the former *Usuários* page.
- **Acessos abertos:** every open sign-in in the workspace, with *Encerrar* and *Todos deste membro*.
- **Como entram:** the sign-in methods and the personal-agent rules. When activation codes are turned off, invite links go to the sign-in page (SSO).

E-mail goes out through the installation's SMTP (`SMTP_HOST`, `SMTP_FROM`…; see [digital-employee.md](digital-employee.md#decisions-outside-the-portal)).

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/activate` | `{code}` → session cookie (activation code, single use) |
| GET / PUT | `/api/workspace/policy` | `{login: {password, sso, activation, key}, personal_agents: {enabled, who, max_per_member, llm_connections, monthly_budget_usd}}`. PUT is admin only |
| GET | `/api/workspace/sessions?user_id=` | Open sign-ins in the workspace (admin, auditor) |
| DELETE | `/api/workspace/sessions/{id}` | Ends one sign-in (admin) |
| POST | `/api/workspace/users/{id}/sessions/revoke` | Ends all of a member's sign-ins (admin) |
| POST | `/api/workspace/activation-codes` | `{email}` → `{code, user, expires_at}` (admin) |
| POST | `/api/workspace/invites` | `{email, name, team, role, org_role}` → creates the member (and the team membership) and e-mails the link to `/app`; returns `{link, sent, email_ready, expires_at}` (admin) |
| GET | `/api/workspace/invites` | Invites waiting for the person to sign in (admin, auditor) |
| POST | `/api/workspace/invites/{user_id}/resend` | A new link (the previous one stops working) |
| GET | `/api/me/workspace` | The member's home: `to_decide`, `agents` (with `personal`, `working_on`), `my_tasks`, `month_cost_usd`, personal-agent counters and caps |
| GET | `/api/me/sessions` | My sign-ins (`device`, `method`, `ip`, `last_seen_at`, `current`) |
| DELETE | `/api/me/sessions/{id}` | Ends one of my sign-ins |
| POST | `/api/me/sessions/revoke-others` | Ends all my sign-ins except this one |
| POST | `/api/me/activation-code` | A code for another browser of mine |
| POST | `/api/employees` | With `personal: true`: a personal agent (`manager` optional, the owner) |
| POST | `/api/employees/{slug}/promote-to-team` | `{note}` → a `promote_personal` request for another maintainer |
| POST | `/api/employees/{slug}/propose-reports-to` | `{to, why}` → an `org_change` proposal (for whoever doesn't manage the agent) |
| GET | `/api/org-chart` | Each node also has `model`, `connection`, `harness`, `specialists`, `personal`, `owner`, `can_manage`, `working_on` |
| GET | `/api/me/push` | `{public_key, subscriptions}` |
| POST | `/api/me/push` | The browser's `PushSubscription.toJSON()` |
| POST | `/api/me/push/remove` | `{endpoint}` |
| POST | `/api/me/push/test` | Sends a test notification to my browsers |
| GET | `/app/manifest.webmanifest`, `/app/sw.js`, `/app/icon-192.png`, `/app/icon-512.png` | The PWA |
