# Users, teams and access

Agent Hangar is multi-user. People sign in with their company account (OAuth2: Google, Microsoft Entra ID,
GitHub, or any OAuth2 provider). Every agent belongs to a **team**, and what each person can do depends on their
role in the company and in the team.

```
Company (one per installation; org_id is on every table, ready for multi-company)
 ├── Teams ── members (user + role in the team)
 │     └── agents, jobs, keys and tool connections of the team
 └── Shared: LLM connections, skills, MCP servers (managed by platform admins; teams use them, never see keys)
```

## Roles

| Role | Scope | Can |
|---|---|---|
| **Admin** | company | Everything: teams, users, SSO/SCIM, LLM connections, audit, all agents. Promotes to production directly. |
| **Auditor** | company | Read everything: usage, cost, audit log, specs and logs. Changes nothing. |
| **Member** | company | Whatever their team roles allow; sees the company catalog. |
| **Maintainer** | team | Manage members, change an agent's visibility, approve production and access requests, stop production, delete agents. |
| **Developer** | team | Create, edit, test and deploy (stage) the team's agents; ask for production. |
| **Consumer** | team | Use the team's agents: Connect tab, Playground, usage. No spec, no edits. |

Only developers and maintainers create agents. When a person is in several teams, they choose the team (UI,
`register_agent(team=...)`, `hangar templates apply --team`, `team:` in GitOps files). Agents created with the
`ADMIN_TOKEN` go to the default **Plataforma** team. On upgrade, every agent that already exists goes there too.

## Agent visibility

| Visibility | Other teams |
|---|---|
| `private` | Don't see it at all. |
| `org` (default) | See it in the **company catalog** (name, objective, team, status, test result) and can **request access**. A maintainer approves; then they can connect it to their tools. |
| `open` | Anyone in the company can use it directly (Connect tab, gateway) with their own key. |

People outside the team never see instructions, spec, versions, jobs or logs, unless the team turns on
**expose spec** (read-only). Change the visibility, the owner team and the expose-spec setting in the agent's
**Access** tab; maintainers and admins only. The company default visibility is set with `PATCH /api/org`.

## Production approval (four eyes)

- A **developer's** ship tests everything in stage, then creates a **promotion request** instead of deploying.
  The same applies to `POST /deploy {env: prod}`, `promote_to_production` and `hangar ship`.
- In teams with **Production requires approval** turned on (the default), even a **maintainer** needs another
  maintainer or an admin to approve.
- The request is pinned to the tested version. If the spec changes, approving fails, and the request is marked
  superseded.
- **Admins** always promote directly, and that is audited.
- Decide requests on the **Approvals** page, with `hangar approvals approve promotion <id>`, or with the MCP tool
  `decide_approval`.

## Keys belong to people

| Scope | What it is |
|---|---|
| `user` | Personal token. It acts as you, with your teams and roles. Use it for the `hangar` CLI and for the platform MCP in Claude, Codex or OpenCode. |
| `invoke` | Calls only the listed agents through `/gw` (LibreChat, Slack bots, SDKs). The Connect tab creates one per tool. |
| `admin` | Everything. Created by admins only. |
| `scim` | Only `/scim/v2`, for the directory. Created by admins only. |

Keys never outlive access:
- When someone is removed from a team, or a grant is revoked, the invoke keys that no longer apply are revoked
  immediately.
- The same happens when an agent becomes `private` or moves to another team.
- **Deactivating** a user in the UI or through SCIM ends their sessions and revokes every key they own.

## Budgets

An admin can set a **monthly budget** (US$) per team. The spend is the sum of the recorded cost of the team's
agents.
- **Over 80% of the budget:** the team card turns yellow.
- **Over 100%:** it turns red, and an audit event is recorded (`budget.exceeded`).
- **Block calls when exceeded:** with this on, the gateway answers `429` until the next month.

## Where each person lands

| Address | For | What it has |
|---|---|---|
| `/app/`, the user portal | Everyone (admins too, when they open it) | Only what belongs to the person: Início, issues, their agents, org chart, decisions, inbox, connect their AI, sign-ins |
| `/ui/`, the admin console | Platform admins only | Everything, including LLM providers, SSO/SCIM, deployments and tests across the company |

The **Início** page is one screen with, in this order:
1. **Needs attention:** failed tests, a production agent whose container is down, failed schedules, errors in
   the last 24 h, team budgets at 80% or more, requests waiting for your decision, and your rejected requests.
2. **Your agents,** with shortcuts to test, connect and edit.
3. **Requests:** what waits for you and what you asked for.
4. **Budget and usage:** each team's month against its budget, and your own last 30 days.
5. **Upcoming schedules.**
6. **Your connections:** active keys, last use, and a flag for keys unused in 60+ days. You can revoke them there.
7. **Agent memory:** which of your agents remember, with a link to see or delete what was kept.
8. **What's new** in the company catalog.

**The console's menu** has 13 subjects. Each one opens the last tab you used, and the tabs point to the usual
addresses, so links and favorites keep working:

| Menu | Tabs |
|---|---|
| Painel | Resumo · Ao vivo |
| Caixa de entrada | Tudo · Decisões · Aprovações |
| Issues | Quadro · Configurar o quadro |
| Agentes | Todos · Deployments · Testes · Templates (an agent's own tests and deployments are tabs on its page) |
| Digital employees | Visão geral (with its own tabs: action catalog, floor, decision channels, calendar, deliverables) · Organograma |
| Trabalho | Filas · Playbooks · Reuniões · Memorandos de decisão · Vigias · Sistemas de chamados · Comigo |
| Projetos | Projetos · Metas · Entregáveis |
| Capacidades | Skills e MCPs · Skill Studio · Plugins · Extração de documentos · Calculadoras · Modelos de mensagem · Procedimentos do navegador |
| Conhecimento | Base de conhecimento · O que a empresa aprendeu · Trilhas |
| Valor e custo | Valor e oportunidades · Uso e custo · Orçamentos |
| Auditoria | Auditoria · Governança de dados |
| Pessoas e times | Membros · Times · Pacotes de time · SSO e SCIM |
| Configurações | Provedores de LLM · Segredos · Chaves de API · Avisos · Empresas · Integrações |

*Portal do usuário* is a link at the bottom of the menu.

**The portal's menu** has only the person's own items: *Início, Minhas issues, Issues, Meus agentes, Organograma,
Decisões, Caixa de entrada, Conectar minha IA, Meus acessos* — the same for admins. Building agents, the catalog, plugins,
queues, budgets, teams, audit and **Membros** are in the console (`/ui`). See [workspace.md](workspace.md).

**How `/ui/` is kept admin-only:**
- A signed-in non-admin who opens `/ui/` is redirected to `/app/`, by the server and again by the page.
- The login page is shared, and each person ends up in their own place after signing in.
- The redirect is a convenience, not the protection: every API call still checks permissions.

Admins can open the portal from **Portal do usuário** in the console. The portal has no link back to the console,
so non-admins never see it; admins return at `/ui/`.

The data comes from `GET /api/me/home`, already filtered by the caller's permissions.

> Since the member workspace, the portal opens on a simpler **Início** (decisions, agents working now, my tasks, month
> cost), and the builder pages (this one included) live in the console. See [workspace.md](workspace.md).

## Signing in

The UI opens on a login page. The buttons are the configured providers, plus **Sign in with token**:
- the `ADMIN_TOKEN` (break-glass);
- an admin key;
- a personal token.

A token becomes a session cookie; it is not stored in the browser.

**Local accounts (username and password).** An admin can create them in **Membros → Pessoas → Cadastrar sem convite** by
setting a username and a password, or with `POST /api/users {username, password, org_role}`. They sign in with
the username/password form on the login page.
- Passwords are stored only as scrypt hashes, with a minimum of 8 characters.
- Five wrong attempts lock the account for 15 minutes.
- Users change their own password from the sidebar; an admin can reset it, which also ends that user's sessions.
- A local account has the e-mail `<username>@local` until you set the real one. With the real e-mail, a later
  SSO login with that verified e-mail signs in to the same account.
- Companies that require SSO only turn local accounts off with `LOCAL_LOGIN=0`.

Configure providers in the UI (**SSO e SCIM**, admins) or with `OAUTH_*` variables in `.env`; what is saved in
the UI wins. The redirect URI to register at the provider is `<PUBLIC_BASE_URL>/api/auth/callback/<provider>`.
Put the hangar behind HTTPS before real use; cookies get `Secure` automatically when `PUBLIC_BASE_URL` is
https.

**First admin.** List your e-mail in `BOOTSTRAP_ADMIN_EMAILS`. You can also sign in with the `ADMIN_TOKEN`, open
**Membros** and promote someone.

### Google
1. Google Cloud Console → APIs & Services → Credentials → **OAuth client ID** → Web application. Add the redirect
   URI.
2. OAuth consent screen: **Internal** restricts sign-in to your Workspace.
3. Set the client ID, the secret and **allowed domains** (for example `empresa.com.br`).

Scopes are `userinfo.email` and `userinfo.profile`, and the profile comes from the Google userinfo endpoint. The
e-mail must be verified. Google groups are not read: map people to teams manually, or use SCIM.

### Microsoft Entra ID
1. Entra ID → App registrations → **New registration**. Choose accounts in this organizational directory only,
   and set the redirect URI (Web).
2. Certificates & secrets → new client secret.
3. Set the client ID, the secret and the **tenant**. The tenant is its ID or its domain; `common` and
   `organizations` are refused.
4. Optional, to map **groups to teams**: turn on *Read groups*. This adds the delegated permission
   `GroupMember.Read.All`, which needs admin consent.

Then add mappings with the group ID or display name, a team and a role. The profile comes from Microsoft Graph
`/me`, and groups from `/me/memberOf`.

### GitHub
1. GitHub → Settings → Developer settings → **OAuth Apps**. Create the app, preferably owned by your
   organization, and set the callback URL.
2. Set the client ID, the secret and the **required organizations**. The user must be an active member.

Scopes are `read:user`, `user:email` and `read:org`. A verified e-mail is required. Optional: *Read teams*, then
map `org/team-slug` to Hangar teams.

### Any OAuth2 provider (Keycloak, Okta, Auth0…) and SAML
Use the **generic OAuth2** card:
- authorization, token and userinfo URLs;
- scopes;
- the name of the groups field;
- the button label.

The userinfo must include `email_verified: true`, unless you tick *trust e-mail* (do it only if the provider
guarantees e-mails). **SAML:** put Keycloak (or another broker) in front of your SAML IdP and connect Keycloak
here as a generic OAuth2 provider.

### Groups → teams
Mapped groups are synchronized at every login (OAuth2) and at every directory change (SCIM). The sync only
touches memberships it created itself (source `sso:<provider>` or `scim`). People added by hand stay as they
are. Changing someone's role by hand makes the membership manual.

## SCIM 2.0 (provisioning)

- **Endpoint:** `<PUBLIC_BASE_URL>/scim/v2`.
- **Authentication:** Bearer token with the `scim` scope (**SSO e SCIM → Generate SCIM token**).
- **Entra ID:** Enterprise applications → your app → Provisioning → Automatic. Use *Tenant URL* = the endpoint,
  and *Secret token* = the key.
- **Okta:** SCIM 2.0 app with the same values.

Supported:
- `Users`: list, filter `userName` / `externalId`, get, create, replace, patch, delete. `active=false` and DELETE
  deactivate the user (the account is kept for the audit log).
- `Groups`: list, filter `displayName`, create, replace, patch members, delete. Map group names to teams with
  provider `scim`.
- `ServiceProviderConfig`, `ResourceTypes`, `Schemas`.

## Where each piece lives

| Task | UI | CLI | API | MCP |
|---|---|---|---|---|
| Who am I | sidebar | `hangar whoami` | `GET /api/me` | `whoami` |
| Teams and members | Times | `hangar teams ls/add` | `/api/teams…` | — |
| My agents / company catalog | Agentes | `hangar agents ls [--mine]` | `GET /api/agents` (with `access`, `permissions`) | `list_agents` |
| Request access | catalog → Solicitar acesso | `hangar request-access <slug>` | `POST /api/agents/{slug}/access-requests` | `request_agent_access` |
| Approvals | Aprovações | `hangar approvals ls/approve/reject` | `GET /api/approvals`, `POST /api/promotions/{id}/approve` | `list_approvals`, `decide_approval` |
| Visibility / owner team | agent → Acesso | — | `PATCH /api/agents/{slug}/access` | — |
| Members (people, invites, sign-ins) | Membros (console) | — | `/api/users`, `/api/workspace/invites` | — |
| SSO and SCIM | SSO e SCIM | — | `/api/sso…`, `/scim/v2` | — |
