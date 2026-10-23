# Feature map

Everything Agent Hangar does, grouped by who uses it, with where to find it. **Portal** is `/app` (everyone);
**Console** is `/ui` (admins). Each line links to the page that explains it in depth.

## Everyone (portal)

| Feature | Where | Docs |
|---|---|---|
| Issues (kanban): board, list, table, swimlanes and timeline; assign to a Digital employee and it works on the issue (with authority and budget); comments with @mentions, sub-issues in stages, relations, labels, custom statuses | Portal → Minhas issues (each person sees their own) · Console → Issues (the company's view, read-only) | [issues.md](issues.md) |
| Member workspace: simple menu, home (decisions, agents working now, my tasks, month cost), personal agents, visual org chart with drag-and-drop, *Connect my AI*, my sign-ins, installable app with notifications | Portal → Início, Meus agentes, Organograma, Conectar minha IA, Meus acessos | [workspace.md](workspace.md) |
| Members: invite by e-mail (link to the portal), people and roles, open sign-ins, sign-in methods and personal-agent rules | Console → Membros | [workspace.md](workspace.md#admin-members) |
| Full dashboard: what needs attention, my agents, usage, requests, schedules, connections, getting started, recently viewed | Portal → Home | [work.md](work.md#getting-started-announcements-favorites-and-live-view) |
| Inbox: decisions, tasks I asked for, stuck work I manage, budget stops (read / archived) | Portal → Inbox | [work.md](work.md#inbox-and-search) |
| Search everything I can see (`Ctrl+K`); in the portal: agents, Digital employees, tasks, issues and projects | Top of every page | [work.md](work.md#inbox-and-search) |
| Favorites (☆ on a title) and recently viewed | Sidebar, Home | [work.md](work.md#getting-started-announcements-favorites-and-live-view) |
| Company catalog and access requests to other teams' agents | Portal → Company catalog | [access.md](access.md) |
| Connect AI tools (Claude, ChatGPT, Codex, Cursor, LibreChat, VS Code…) and personal keys | Portal → Connect tools, My keys | [clients.md](clients.md) |
| Usage and cost | Portal → Usage and cost | [observability.md](observability.md) |
| Where decisions reach me (Slack, Teams, e-mail) and the daily digest | Portal → Decisions | [digital-employee.md](digital-employee.md#decisions-outside-the-portal) |
| Link Telegram / Discord to ask for work in chat | Portal → Decisions | [digital-employee.md](digital-employee.md#tasks-from-chat) |
| Company announcements | Banner at the top | [work.md](work.md#getting-started-announcements-favorites-and-live-view) |
| Phone-width layout | Any page | [work.md](work.md#getting-started-announcements-favorites-and-live-view) |

## Working with Digital employees

| Feature | Where | Docs |
|---|---|---|
| Hire (guided, or by talking to an AI tool over MCP), probation, admission, pause, offboard | Portal → My Digital employees | [digital-employee.md](digital-employee.md#lifecycle) |
| Assign tasks (portal, MCP, Slack, Teams, Telegram, Discord, e-mail inbox, inbound webhook, routines, plugin triggers) | Employee page, chat, systems | [digital-employee.md](digital-employee.md#tasks) |
| Authority: what each employee may do alone, with notice, with one or two approvals, never | Employee → Authority | [digital-employee.md](digital-employee.md#authority) |
| Decisions: approve, edit, reject, answer — in the portal, Slack buttons, Teams cards, e-mail links | Portal → Decisions | [digital-employee.md](digital-employee.md#human-in-the-loop) |
| The task's conversation (write while it works; reopen a finished task) | Task page | [work.md](work.md#the-tasks-conversation) |
| Dependencies, review before done, hold a task tree | Task page | [work.md](work.md#dependencies-review-and-hold) |
| Files and deliverables (attach, versioned outputs, library) | Task page, Portal → Deliverables | [work.md](work.md#files-and-deliverables) |
| Why it stopped / continue a failed task | Task page | [work.md](work.md#recovery) |
| Live view of what the employees are doing | Portal → Live | [work.md](work.md#getting-started-announcements-favorites-and-live-view) |
| Shadow mode, career plan, result quality (judge + manager) | Employee → Shadow, Overview | [digital-employee.md](digital-employee.md#shadow-mode) |
| Handoffs between employees, plans for long tasks, memory of its own work | Automatic; task page | [digital-employee.md](digital-employee.md#handoff-between-employees) |
| Routines (cron), inbound webhook, e-mail inbox | Employee → Routines, Settings | [digital-employee.md](digital-employee.md#routines) |
| External employee: the work runs in your own agent via heartbeat | Employee → Routines → Where it works | [digital-employee.md](digital-employee.md#external-employee-bring-your-own-agent) |
| Daily and weekly reports, deadlines and escalation | Employee → Reports; Decision channels | [digital-employee.md](digital-employee.md#reports) |
| Manager dashboard: cost, decisions, deadlines, bottlenecks | Portal → Manager | [digital-employee.md](digital-employee.md#manager-dashboard) |
| Job history (every change is a version that can be restored) | Employee → Settings | [work.md](work.md#job-history) |
| Org chart (who reports to whom); in the portal, you on top with your Digital employees and your regular agents | Portal → Org chart | [work.md](work.md#org-chart) |
| Review by a model from another vendor before an action runs; disagreement goes to a person | Employee → Authority | [digital-employee.md](digital-employee.md#review-by-a-model-from-another-vendor) |
| Loops: each run says whether it found something; quiet runs are counted apart | Routines, tasks | [digital-employee.md](digital-employee.md#loops-only-what-matters-reaches-a-person) |

## The analyst's day to day

| Feature | Where | Docs |
|---|---|---|
| Platform tools per employee (on/off by group) | Employee → Settings | [analyst.md](analyst.md) |
| Data analysis over the task's files and formatted deliverables (PPTX, DOCX, dashboards) with the company's branding | Automatic (Data Studio); Console → Digital employees → Deliverables | [analyst.md](analyst.md#data-analysis-and-formatted-deliverables) |
| Document extraction with confidence, source snippet and field check; batches | Build → Document extraction; Decisions | [analyst.md](analyst.md#document-extraction-with-field-review) |
| Certified calculators with test cases, four eyes and calculation record | Build → Calculators | [analyst.md](analyst.md#certified-calculators) |
| Business calendar and business-day schedules | Console → Digital employees → Calendar | [analyst.md](analyst.md#business-calendar) |
| Playbooks: recurring processes with people and employees, deadlines, evidence, run board | Digital employees → Playbooks | [analyst.md](analyst.md#playbooks) |
| Team queues, SLA, take the next one, hand-off to a person, My work | Digital employees → Queues, My work | [analyst.md](analyst.md#team-queues-and-work-for-people) |
| Jira, Linear, Asana and ServiceNow as on-ramps for queues, with progress and results back on the ticket | Build → Ticket systems | [analyst.md](analyst.md#ticket-systems-as-on-ramps) |
| Organizational learning: articles and procedures proposed from work that went well, with reuse counted | Digital employees → What the company learned; Decisions | [analyst.md](analyst.md#organizational-learning) |
| Self-organization: routines, queue routes and colleagues proposed with numbers; the employee proposes too | Decisions | [analyst.md](analyst.md#self-organization) |
| Maximizer mode per project: more rounds and steps, retries, delegation, report and supervision | Project page | [analyst.md](analyst.md#maximizer-mode) |
| Conversations with people outside (e-mail, WhatsApp), approved templates, reminders, promises | Task page; Build → Message templates | [analyst.md](analyst.md#conversations-with-people-outside-the-company) |
| Company calendar, invites, briefings, minutes and actions | Digital employees → Meetings | [analyst.md](analyst.md#calendar-and-meetings) |
| Knowledge base with inherited permissions, citations, outdated documents and gaps | Build → Knowledge base | [analyst.md](analyst.md#knowledge-base) |
| Governed browser and procedures for systems without an API | Build → Browser procedures; employee settings | [analyst.md](analyst.md#governed-browser) |
| Governed desktop: desktop apps operated by looking at the screen | Employee settings; compose profile `desktop` | [analyst.md](analyst.md#governed-desktop) |
| Classification, DLP, retention, data subject requests, information barriers | Organization → Data governance | [analyst.md](analyst.md#data-governance) |
| Document change proposals with per-passage acceptance and DOCX redlines | Decisions | [analyst.md](analyst.md#document-change-proposals) |
| Decision memos with weighted criteria, critical reviewer, decision and outcome | Digital employees → Decision memos | [analyst.md](analyst.md#decision-memos) |
| Watchers on pages, JSON, knowledge and platform numbers | Digital employees → Watchers | [analyst.md](analyst.md#watchers) |
| Pair mode, review of a person's work, learning tracks | Assign task; My work; Digital employees → Learning tracks | [analyst.md](analyst.md#pair-mode-and-learning-tracks) |
| ROI, baselines, report and automation opportunities | Digital employees → Value and opportunities | [analyst.md](analyst.md#value-and-opportunities) |
| Golden tasks and regression after role changes | Employee → Settings; Decisions | [analyst.md](analyst.md#golden-tasks-and-regression) |

## Planning and money

| Feature | Where | Docs |
|---|---|---|
| Goals (company and team tree) and projects (instructions, goal, default employee) | Portal → Goals, Projects | [work.md](work.md#goals-and-projects) |
| Budgets per company, team, agent or project, by day or month, with warning and hard stop | Portal → Budgets | [work.md](work.md#budgets) |
| Team monthly budget and per-task budget | Team page; employee settings | [access.md](access.md) |

## Building agents

| Feature | Where | Docs |
|---|---|---|
| New agent (form or spec), templates, multi-agent composer | Portal → New agent, Templates | [guide.md](guide.md), [composer.md](composer.md) |
| Skills and MCP servers catalog | Portal → Skills and MCPs | [mcp-catalog.md](mcp-catalog.md) |
| Connectors: official servers, the Docker MCP catalog and the GitHub MCP Registry, each with the simplest way to connect (OAuth, open, token, package) | Console → Capacidades → Conectores | [connectors.md](connectors.md) |
| Agent avatar: a character picked by role when the agent is built; the owner swaps it | Agent page → click the avatar | [api.md](api.md#agent-avatar) |
| Skill Studio: versions, saved test cases, runs, restore | Portal → Skill Studio | [work.md](work.md#skill-studio) |
| Plugins (HTTP / server), per-action policy, personal OAuth identity, triggers, egress proxy | Portal → Plugins | [plugins.md](plugins.md) |
| Code work through harnesses (Claude Code, Codex, OpenCode…) | Agent → Jobs | [harnesses.md](harnesses.md) |
| Reasoning effort, output limit, parallel tools, prompt cache, long-context compaction and a cheaper model per response | Agent → Visão geral → Execução | [execution.md](execution.md) |
| Memory (agent, team, company) with review and deletion: Graphiti on Neo4j or FalkorDB, or Mem0 on pgvector (experimental) | Agent → Memory | [memory.md](memory.md) |
| Stage tests, promotion to production (four eyes optional), rollback, schedules | Agent page | [concepts.md](concepts.md) |
| Team packages: export, preview, install; ready-made teams | Portal → Team packages | [work.md](work.md#team-packages) |
| Remote MCP gateway | Agent → Connect | [remote-mcp.md](remote-mcp.md) |
| AG-UI: the agent inside your app (CopilotKit and other clients), with the app's tools and a Digital employee's approvals as interrupts | Agent → Connect | [clients.md](clients.md#ag-ui-the-agent-inside-your-app) |
| Remote agents: an agent from LangGraph, Google ADK, Mastra or another AG-UI framework, governed by the hangar and exposed over every protocol | Agent spec `remote`; Agent → Overview → Testar conexão | [clients.md](clients.md#agents-from-other-frameworks-remote-ag-ui-agents) |

## Administration (console)

| Feature | Where | Docs |
|---|---|---|
| Companies: create, SSO domains, suspend; act inside one (selector / `X-Hangar-Org`) | Console → Companies | [work.md](work.md#companies-multi-company) |
| Members, teams, roles, SSO (OIDC/SAML) and SCIM | Console → Membros, Times, SSO e SCIM | [access.md](access.md) |
| LLM providers and connections | Console → LLM providers | [install.md](install.md) |
| Digital workforce: overview, action catalog, authority floor, decision channels (Slack, Teams, Telegram, Discord, e-mail) | Console → Digital employees | [digital-employee.md](digital-employee.md) |
| Secrets vault (`secret://`), rotation, proposals approved by someone else | Console → Secrets | [work.md](work.md#secrets-vault) |
| Announcements | Console → Announcements | [work.md](work.md#getting-started-announcements-favorites-and-live-view) |
| Deployments, tests, usage, audit log | Console → Monitoring | [observability.md](observability.md) |
| Security model, supply chain (signed images, SBOM), egress controls | — | [security.md](security.md), [supply-chain.md](supply-chain.md) |

## Operating the installation

| Feature | Where | Docs |
|---|---|---|
| Guided install (Docker or Kubernetes), unattended answers file | `hangar setup` | [install.md](install.md), [kubernetes.md](kubernetes.md) |
| Diagnose | `hangar doctor` | [cli.md](cli.md) |
| Backup, restore, upgrade | `hangar backup / restore / upgrade` | [backup.md](backup.md) |
| Try the whole path with the test model | `hangar test-drive` | [cli.md](cli.md) |
| GitOps: agents, skills and MCPs from a file; CI | `hangar apply` | [cli.md](cli.md#agents-in-ci) |
| Cloud VM recipes | — | [cloud-vm.md](cloud-vm.md) |
| Documentation site, built from `docs/` | [valteresj2.github.io/agent-hangar](https://valteresj2.github.io/agent-hangar/) | — |
| Metrics, traces, logs | Prometheus, OpenTelemetry | [observability.md](observability.md) |

## For developers

| Feature | Where | Docs |
|---|---|---|
| REST API (everything the portal does) | `/api` | [api.md](api.md) |
| MCP server per agent and the platform's own MCP | `/mcp` | [clients.md](clients.md) |
| Agent spec | YAML | [spec.md](spec.md) |
| External worker protocol (heartbeat, gate, progress, result) | `/hooks/worker` | [digital-employee.md](digital-employee.md#external-employee-bring-your-own-agent) |
| Inbound webhook, Slack, Teams, Telegram and Discord endpoints | `/hooks` | [digital-employee.md](digital-employee.md#tasks-from-chat) |
