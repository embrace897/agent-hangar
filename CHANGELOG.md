# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.28.0] — 2026-10-12

### Added
- **Ticket systems as on-ramps** (Jira Cloud and Server/Data Center, Linear, Asana, ServiceNow): tickets in a filter
  become queue items (category by rules, priority mapped, by polling and by webhook); progress, the result and the
  deliverables go back to the ticket as comments, status changes and attachments; new comments on the ticket reach
  the Digital employee's conversation; a ticket closed outside cancels the item. The credential is a vault secret and
  a connector goes live only after someone else approves it.
- **Organizational learning:** a task that went well proposes one artifact — a knowledge-base article when it
  answered a gap, a browser procedure when it walked a path through a system — kept after a person approves (with
  edits); reuses are counted (*What the company learned*).
- **Self-organization:** routines for recurring requests, queue routes for categories people solve by hand, and
  colleagues for repeated denied hand-offs, proposed with numbers; employees propose changes too
  (`propose_org_change`); nothing changes before a person approves, rejections don't come back for 30 days, role
  changes run the golden tasks.
- **Maximizer mode per project:** more rounds and steps, the instruction to finish and split work across the org
  chart, automatic retry on technical failures, a periodic report and a supervision panel; authority, budget and
  audit unchanged.

## [0.27.0] — 2026-10-12

### Added
- **Governed desktop:** Digital employees operate desktop applications with no API (legacy ERP, spreadsheet) in an
  isolated Linux desktop built on Cua (compose profile `desktop`), looking at the screen: allowed apps per employee,
  the window's screenshot sent to the model as an image, accessibility elements when the app has them, authority with
  the screenshot on clicks and shortcuts that confirm something, vault secrets when typing, block typing (`\t` and
  `\n` fill several cells or fields in one call). Approvals show what will be confirmed and what already was; a
  repeated confirmation with the same fields is held back; the "Yes/OK" of the confirmation dialog right after an
  approved click, and retries of an approved confirmation that had no effect in the same dialog, are covered by that
  approval. Platform tools can now return images to the model; the runtime keeps only the latest screenshots in the
  conversation (`KEEP_TOOL_IMAGES`).
- **Releases publish the governed browser (`mcp-browser`) and governed desktop (`desktop`) images**, for amd64 and
  arm64, signed like the others.
- **Long tasks continue across rounds:** a round that uses all its steps no longer ends the task — the central keeps
  the log of what was done and starts a new round, up to the round limit and the budget.
- **Pilot with a real model for the desktop:** `tests/e2e/pilot_llm.py desk_*`, 8 cases (login with a vault password,
  validation errors, a nonexistent record, a confirmation dialog, menu + second window + scrolling, a spreadsheet with
  a formula saved through "Save as", opening a file, two postings in one task).

### Fixed
- A task that resumed after an approval only remembered the model's final text, not what it had done in the round,
  and could redo actions (a payment posted twice in a multi-step task). Each round now leaves a log of the actions
  taken (and the model's own plan) in the conversation.
- When a model finished a round with no answer after using tools, the task ended with an empty result; it now gets
  one nudge to report.

## [0.26.0] — 2026-10-11

### Added
- **Platform tools** ([docs/analyst.md](docs/analyst.md)): Digital employees use the platform itself through tools that
  run in the central and still go through their authority; the manager turns groups on or off per employee.
- **Data analysis and formatted deliverables:** Python and SQL over the task's files in the Data Studio, with what the
  analysis produces coming back as versioned deliverables (and the code on the timeline); PPTX, DOCX and dashboards
  with the company's branding. Tasks can be created with their files attached.
- **Document extraction with field review:** extraction schemas, per-field value, confidence and source snippet, a
  field-check decision for whatever is in doubt, and batches (one task per file plus a consolidated spreadsheet).
- **Certified calculators:** formulas in a safe Decimal language, test cases, four-eyes certification, a calculation
  record for every run and optional human review of tasks that use them.

- **Business calendar:** national holidays built in, company holidays, working hours; business-day schedules
  (`@bd 1 09:00`, `@bdaily 08:30`) for routines and playbooks.
- **Playbooks:** recurring processes with steps for people and Digital employees, dependencies, business-day
  deadlines, evidence, scheduled runs, a run board and where each step usually runs late.
- **Team queues and work for people:** categories by keyword, SLA, routing to the least busy Digital employee or to
  people (take the next one), hand-off from an employee to a person with a draft, evidence and four-eyes approval,
  automatic / assisted / human metrics, webhook intake.
- **Conversations with people outside the company:** e-mail and WhatsApp threads on the task, approved message
  templates, waiting for the reply, automatic reminders, promises turned into deadlines, signed WhatsApp webhook.
- **Calendar and meetings:** free times and invites through Microsoft 365 or Google Workspace, briefings before
  meetings, minutes from transcripts, actions turned into work and followed up at the next meeting of the series.

- **Knowledge base:** uploads, web pages, Confluence, SharePoint and Google Drive with inherited permissions; search
  with the requester's access, numbered citations, outdated documents and unanswered questions.
- **Governed browser:** an isolated Playwright browser for systems without an API, with allowed domains, authority
  (and a screenshot) on clicks that confirm something, vault secrets in fields, CAPTCHA handed to a person, and
  versioned procedures that can be saved from a task.
- **Data governance:** classification with per-LLM-connection limits, DLP masking with real values for execution,
  outbound blocking, retention with legal hold, data subject requests with four-eyes erasure, and information
  barriers with audited emergency access.
- **Document change proposals:** before/after per passage, partial acceptance and edits, DOCX redlines, new versions in
  Confluence and SharePoint, refused when the document changed meanwhile.

- **Decision memos:** options, weighted criteria and scores with totals computed by the platform, recommendation with
  confidence, risks and sources; an optional critical reviewer; the decision and its real outcome recorded, with a
  follow-up item and the employee's decision record.
- **Watchers:** pages, JSON values, knowledge-base searches and platform numbers checked on a schedule, firing on
  change or on crossing a threshold, turning into a task, a notice or a log entry.
- **Pair mode and learning tracks:** tasks where the employee guides but doesn't act, review of a person's work by a
  Digital employee, and quizzes generated from playbooks and procedures with the team's scores.
- **Value and opportunities:** baselines, ROI per employee (hours, human cost, LLM cost, rework), a Markdown report,
  and repeated human work worth automating.
- **Golden tasks and regression:** every change to the role re-runs the golden tasks without acting for real; if the
  scores drop, the manager keeps the change or restores the previous version.
- **Pilot with a real model:** `tests/e2e/pilot_llm.py` runs the analyst cases in plain language against an LLM
  connection (`LLM_CONNECTION=…`) and reports which tools the model chose; it answers the employee's questions like a
  manager would.

### Fixed
- A task blocked by dependencies that had never run lost its own request when it was unblocked.
- Probation stage tests sent only the task's body to the model, dropping the question when it was in the title.
- Platform tools that call an LLM or the Data Studio could take longer than the runtime's 30 s gate wait: the model
  heard "not executed" while the central finished the action. Platform tools now wait up to `GATE_TIMEOUT_PLATFORM`
  (600 s).
- Decision memos accept the recommendation as the model writes it ("C", "b)", a prefix of the option's name); the
  critical reviewer's message says it is not an authority block.
- `propose_document_change` accepts a single `find`/`replace`, a JSON-encoded `edits`, and explains with an example
  what is missing.
- Knowledge sources can be removed (`DELETE /api/knowledge/sources/{name}` and a button), with the documents copied
  from them.

## [0.25.0] — 2026-10-11

### Added
- **Goals and projects** ([docs/work.md](docs/work.md)): company and team goals as a tree; projects per team with
  instructions, a goal and a default Digital employee. A task in a project gets the project, its instructions and the
  chain of goals in its prompt.
- **The task's conversation:** write on a task while the employee works (queued, editable, delivered in the next
  round); a message reopens a finished task as a follow-up; the employee's replies are kept in the thread.
- **Budgets with scope and incidents:** company, team, agent or project, per day or month, with a warning and a hard
  stop (the gateway answers 429 and tasks wait in `waiting_budget`) until someone raises the limit or releases the rest
  of the window.
- **Inbox and search:** a personal inbox (decisions, tasks I asked for, stuck work I manage, budget stops) with read and
  archived state, and company search (`Ctrl+K`) filtered by access.
- **Dependencies and review:** a task can wait for others (and gets their results); a result can go through review
  (the manager or a named reviewer) before the task finishes; a task tree can be paused and resumed.
- **Files and deliverables:** attach files to a task; the employee reads them (`read_file`) and saves versioned
  deliverables (`save_file`); a deliverables library; safe preview for text, images and PDF.
- **Recovery:** *Why it stopped* explains the state, the cause and who acts next; *Continue the task* resumes a failed
  task from its checkpoint and asks to confirm when the last round may have had side effects.
- **Job history:** every change to a Digital employee's job, authority or settings is a version that can be restored.
- **Org chart:** a Digital employee can report to another one; handoffs flow down to reports and up to the chief, and
  the prompt says who is who.
- **Skill Studio:** edit skills with versions, saved test cases, runs against an LLM connection (mock without one),
  restore a version, deprecate.
- **Team packages:** export a team (scrubbed of secrets), preview and install it elsewhere (employees arrive as drafts),
  and a catalog of ready-made teams (the *Renewals team*).
- **Plugins:** per-action policy on each install (off / ask on every use / authority decides), the requester's
  personal OAuth2 account (`identity_mode`), and *Test an action* on the production install.
- **Secrets vault:** company and team secrets referenced as `secret://name` (plugin credentials and secret settings),
  rotation, usage counts and proposals approved by someone else.
- **Companies (multi-company)** ([docs/work.md](docs/work.md#companies-multi-company)): one installation serves several
  fully isolated companies. Instance admins create them (with a first team and admin), set the e-mail domains that join
  each one through SSO, suspend them, and act inside any of them from the sidebar selector or `X-Hangar-Org`.
- **External Digital employee** ([docs/digital-employee.md](docs/digital-employee.md#external-employee-bring-your-own-agent)):
  the work runs in your own agent, which fetches tasks through a heartbeat with a per-employee token, asks the same
  authority before acting and posts the result; the queue, decisions, budget and history stay in Agent Hangar.
  Reference worker in `examples/external-worker/`.
- **Telegram and Discord** as task channels: a message to the bot (or `/hangar` on Discord) becomes a task and the
  result comes back to the same chat. Each person links their account once with a one-time code.
- **Getting started, announcements, favorites and live view:** a checklist of next steps on the home page, company
  announcements as a banner, favorites and recently viewed, and a live view of what the Digital employees are doing.
- **`hangar test-drive`:** the whole path (team, Digital employee, probation, admission, a task) against your
  installation with the test model, cleaning up at the end.
- **Feature map** ([docs/feature-map.md](docs/feature-map.md)): every feature by who uses it and where it is.

### Changed
- Reading a Digital employee's task (request, result, conversation) now needs the requester, the people who answer for
  the employee, the employee's team, an auditor or an admin. Seeing the agent in the catalog is no longer enough.
- Installing a team package needs the team's maintainer (it creates goals, projects and employees at once).
- The portal and the console work at phone width: wide tables and tab bars scroll inside their box.

### Upgrading
- Migrations `0026`–`0028` add `org_id` to every company table (existing rows belong to the main company), the
  external-worker columns on Digital employees and the announcements table. Names of skills, LLM connections, MCP
  servers, plugins, the action catalog and the authority floor become unique per company instead of per installation.

## [0.24.0] — 2026-10-10

### Added
- **Plugins:**
  - egress proxy: plugins with code reach only the hosts in `server.egress`, through the central (the plugin network
    has no direct route out on Docker; NetworkPolicy on Kubernetes). Refused hosts are shown on the install;
  - image signatures with cosign (public key or keyless), checked on approval and, with `PLUGIN_REQUIRE_SIGNED=1`,
    required to approve and start;
  - rate limit per install (`rate_limit` in the manifest or on the install; `PLUGIN_RATE_LIMIT` by default);
  - call log: agent, task, tool, action, duration, status and result of every call, with secrets masked
    (`GET /api/plugins/<name>/calls`, *Calls* on the install, `PLUGIN_CALLS_DAYS`);
  - OAuth2 for plugins with code: the token goes in a header of each call (`server.credential_header`), renewed by the
    central;
  - triggers testable in stage: `example` events, *Test the trigger*, stage webhooks that answer with the preview, and
    trigger cases in `tests`.
- **Digital employees:**
  - tasks from chat: an app mention or DM in Slack, or a Teams outgoing webhook, becomes a task; the result comes back
    in the Slack thread or through the person's channel;
  - result quality: an LLM judge scores a sample of finished tasks (1 to 5); the manager's score replaces it; the
    average counts in the career plan;
  - manager dashboard: cost, decision time, % on time, quality and bottlenecks per employee and team.
- `release-notes/channels-live-test.md`: checklist for a run against real Slack, Teams and IMAP accounts.

### Upgrading
- On Docker, `hangar_plugins` becomes an `internal` network. If `docker compose up -d` reports active endpoints, remove
  the plugin containers (`docker ps -q --filter label=central.plugin | xargs docker rm -f`); the central starts them
  again with the proxy. Plugins with code that call hosts outside `server.egress` need a new version declaring them,
  or `PLUGIN_EGRESS=open`.
- The Slack app needs the new scopes and events: update it with the manifest shown in *Decision channels*.

### Changed
- Base images (Python, Node, Postgres, docker-socket-proxy, Trivy, BuildKit) are mirrored weekly to
  `ghcr.io/<owner>/hangar-mirror`. CI and the release build use the mirror and fall back to Docker Hub when a tag is
  missing, so a Docker Hub outage no longer breaks a release. Dockerfiles take the base image as a build argument.
- The GitHub release is created by the *Release images* workflow after the images and chart are published, with the
  VS Code extension attached. Notes come from `release-notes/<tag>.md` or the CHANGELOG section.
- The Digital employees and plugins end-to-end tests now live in `tests/e2e/` and run in CI, plus a crawler that fails
  when Portuguese is left on the English interface.

### Fixed
- "Test connection" on a plugin without a `test` block uses its first stage test case instead of calling a tool
  without its arguments.
- "just now ago" in activity lists; leftover Portuguese on the plugin and inbox screens; a JavaScript error when
  leaving the Docker MCP catalog before it loaded.

## [0.23.0] — 2026-10-09

### Added
- **Plugins P3: Creator mode through the MCP**, with stage tests and four eyes.
  - **Tools:** `plugin_from_openapi` (by URL or document), `plugin_draft`, `plugin_test`, `plugin_submit`,
    `plugin_status` and `plugin_review`, plus a step-by-step guide in the MCP instructions.
  - **Credentials stay out of the chat:** the test credential goes in a *stage install* in the portal. It runs the draft
    and never serves agents.
  - **Tests in the manifest:** `tests` take a tool, arguments, `expect_contains`, `expect_status` and `expect_error`.
    The report is pinned to the manifest's digest. When tests are declared, submitting and approving need a passing
    report for that exact version.
- **Plugins P4: the company's internal gallery.**
  - Search, categories, tags and admin-featured plugins.
  - Usage per plugin (teams and calls), the `readme` and the version history.
  - *Request it for my team*, which notifies the maintainers in their decision channel.
  - *Export* of the approved manifest, without secrets.
  - The manifest is shown and exported without empty fields.
- Migration `0021_plugin_creator_gallery`.

## [0.22.0] — 2026-10-09

### Added
- **Plugins with code (P2b).** With `runtime: server`, a plugin is an MCP server in an isolated container, one per
  install.
  - **Image:** pinned by digest (`PLUGIN_ALLOW_UNPINNED=1` only for development).
  - **Configuration:** settings and the credential become that container's environment.
  - **Tools:** the central is the container's MCP client. Only the tools declared in the manifest reach agents, with
    their action types; others stay hidden.
  - **Lifecycle:** the container follows the install. It starts on install or save, stops on pause, uninstall or turning
    the plugin off, restarts when a new version is approved, and comes back up if it is down when an agent calls.
  - **Isolation:**
    - Docker: the `hangar_plugins` network, read-only filesystem, no capabilities, `no-new-privileges`, and memory, CPU
      and process limits.
    - Kubernetes: Deployment, Service and Secret, plus a `role=plugin` NetworkPolicy (only the central gets in; private
      ranges blocked on the way out).
  - **Logs:** the container's logs, with secrets masked.
- **Plugins (P2a): OAuth2 accounts.** `auth: {type: oauth2, authorize_url, token_url, scopes}`.
  - The install takes the company app's client ID and secret.
  - *Connect account* runs the authorization code flow with PKCE.
  - Tokens are encrypted and renewed before they expire, and once more after a `401`, under a cluster lock.
- **Plugin triggers.** System events become tasks for a Digital employee of the team.
  - The manifest declares `triggers` with title and body templates (`{{event.path}}`), a `dedupe` field and an
    optional HMAC `signature` (Stripe-, GitHub- and Shopify-style).
  - The maintainer links each trigger to an employee. The system posts to `/hooks/plugins/<install>/<trigger>`, signed
    or with the install token (stored as a hash).
- Migration `0020_plugin_oauth_triggers`.

## [0.21.0] — 2026-10-09

### Added
- **Plugins (P1): connect any system with a declarative manifest** (`hangar-plugin.yaml`).
  - **What a manifest declares:** one `base_url`, the authentication (API key, Bearer or Basic), HTTP tools (method,
    path, JSON Schema parameters, the action type for the authority), settings (secret ones encrypted) and skills.
  - **Import from OpenAPI 3:** each operation becomes a tool, with a guessed action type to review.
  - **Four-eyes review:** an admin who did not create or submit the plugin approves it. Approval classifies each tool
    in the action catalog. A new version needs a version bump and a new review, while installs keep the approved
    version.
  - **Install per team:** the team maintainer installs it with the team's credential; an admin can also install it for
    the whole company.
  - **Use:** agents add it with `plugins: [name]` in the spec. The central serves the plugin as an internal MCP server
    and injects the credential, which never reaches the agent's container.
  - **Safety:** private hosts are blocked unless `PLUGIN_ALLOW_PRIVATE=1`, redirects are not followed and responses
    are cut at 8,000 characters.
  - **Portal and API:** a new *Plugins* page (catalog, new or import, review, install, test, usage), `/api/plugins`,
    and approved plugins in the MCP `list_catalog`.
- Migration `0019_plugins`.

### Added
- **Handoff between Digital employees (F4).** With the `handoff_task` tool, an employee hands part of a task to a
  colleague its manager allowed.
  - The handoff goes through the authority as `delegate` and is tracked in both tasks.
  - With `wait=true`, the original task waits and resumes with the colleague's result.
- **Visible plan for long tasks.** With `update_plan`, the employee records its steps and their status, and the task
  page shows the progress.
  - With the plan policy `approve`, or a task with `plan_approval`, work starts only after the manager approves the
    plan. The manager can approve, edit, reject or instruct, also from Slack, Teams or e-mail.
- **Memory of its own work.** The most similar finished tasks go into the prompt of a new task as a reference.
  Similarity uses words and, with the memory service, embeddings. The manager can turn it off.
- **E-mail inbox as an input channel (IMAP).**
  - Unread e-mails from allowed senders become tasks, deduplicated by `Message-ID`. Without a list, only company
    people may write.
  - The content is marked as data, not an instruction, and actions still go through the authority.
  - Company senders get the result by e-mail when the task ends.
- Migration `0018_employee_teamwork`.

## [0.20.0] — 2026-10-09

### Added
- **Decisions outside the portal (F3).** The person who has a request gets it where they work. Every channel is
  optional.
  - **Slack:** a direct message from the company app, with Approve / Edit / Reject (or Answer) in the message. Edit and
    Reject open a form in Slack. Clicks are verified with the Slack signature and decided as the hangar user with that
    e-mail, under the same rules as the portal. The message then shows the outcome, whatever channel decided it.
  - **Microsoft Teams** (a personal Workflows webhook) and **e-mail** (`SMTP_*`): the same buttons as signed links, for
    one request and one person. The links expire (`DECISION_LINK_TTL_H`), and opening one never decides: only the
    confirmation does.
  - Each person picks a channel on the *Decisions* page; admins set up Slack in *Digital employees → Decision
    channels*.
- **Daily digest** of pending decisions, notices and overdue tasks, per person, at the hour the admin sets.
- **Warning before a decision expires**, sent to the person who has it.
- **Task deadlines with consequences:**
  - Due soon: the priority goes up and the manager is told.
  - Overdue: the manager and the requester are told.
  - Still overdue after a set time: the task escalates to the backups and team maintainers.
  - The overdue count shows on the employee's card and in the reports.
  - "% on time" counts open tasks past their deadline and is now a career criterion and warning sign.
- Migration `0017_decision_channels`.

## [0.19.0] — 2026-10-08

### Added
- **Shadow mode for Digital employees.** The employee works for real, but every action that changes something (other
  than read and delegate) is simulated.
  - The gate logs the exact action, with what the authority would have done, and the task goes on.
  - The manager reviews each action in the new *Shadow* tab (*would approve* / *would not*). Agreement counts toward the
    career plan, and disagreements become lessons.
  - `never` still denies.
  - Hire with `shadow=true` (suggested in the portal), or switch it with `POST .../shadow` / `set_shadow`. Only the
    manager or an admin can switch it.
- **Career plan.** The platform measures the track record and suggests promotions (intern → junior → pleno → senior),
  a step back on warning signs, and leaving shadow mode. The manager applies or dismisses each one.
  - The track record covers tasks done, failure rate, approvals without edits, rejections, expirations, goals and
    shadow agreement.
  - The *Overview* shows the criteria with each value, plus the history; the weekly report shows the progress.
  - API: `GET .../career`; MCP: `employee_career`. Migration `0016_employee_shadow_career`.

## [0.18.0] — 2026-10-07

### Added
- **Missing capability flow for Digital employees** ([docs](docs/digital-employee.md#missing-capability)). When a task
  needs a tool or specialist the employee does not have, it calls `request_capability` instead of improvising, and the
  task pauses.
  - The manager gets a decision with catalog options, seen with the manager's access.
  - The manager can **attach** a specialist. That makes a new version with tests and the team's four-eyes, and the task
    resumes after production approval.
  - Or **build**: a ready self-mode request for the manager's AI tool, with the decision kept open for 7 days.
  - Or **reject** / **instruct**.
  - An agent never attaches or builds agents by itself.

### Fixed
- English portal: learning suggestions showed "recusada/editada", and test results showed "9/9 checks aprovados".
- Runtime mock mode: the task prompt's mention of `request_capability` no longer triggers it.

### Changed
- Launch videos (60 s 16:9 and 30 s 9:16) are now code: `scripts/demo/motion` (Remotion, real captures, music
  synthesized on the beat grid, scored review rounds).

## [0.17.1] — 2026-10-06

### Added
- **Digital employees delegate code work to a harness** ([docs/digital-employee.md](docs/digital-employee.md#code-work-harness)).
  The employee stays a chat agent (the gate sees every tool call); an agent with a harness (Claude Code, Codex…) in
  its `specialists` runs the code jobs.
  - The call is the new action type **`run_code`**. It needs approval by default, on the company floor too
    (migration `0015_employee_run_code`), so the manager approves the exact instruction before a job runs.
  - The call waits up to `JOB_MAX_TIMEOUT_S` instead of the 3 minutes of a chat delegation.
  - The job number and diff come back in the tool result and the task's timeline.

### Fixed
- Runtime mock mode (`mock/*` models, used in tests and demos): a Digital employee no longer delegates to its
  sub-agents without going through the gate, and tool names with hyphens are recognised. The real LLM loop always
  went through the gate.

## [0.17.0] — 2026-10-06

### Added
- **Digital employee, phase F2** ([docs/digital-employee.md](docs/digital-employee.md)).
  - **Routines:** recurring work from a cron. It runs only while the employee is active, never two at once (a run is
    skipped while the previous task is open), and is safe with several central replicas. Routines can be created at
    hire, over MCP (`set_routine`, `list_routines`, `delete_routine`) or in the new *Routines* tab.
  - **Inbound webhook:** `POST /hooks/employees/{slug}` with a per-employee token (shown once, stored hashed,
    rotatable). A `dedupe_key` seen in the last 7 days does not create a second task.
  - **Measured goals:** each KPI can point to a platform metric (`done_rate`, `on_time_rate`,
    `approved_unedited_rate`, `avg_decision_min`, `cost_per_task`, `tasks_done`, `expired_decisions`) with a target.
    The *Overview* shows on/off target.
  - **Weekly report** on a chosen weekday, with each goal compared with its target.
  - **Learning from decisions:**
    - suggests moving an action type from *approve* to *notify* after repeated approvals without edits, never below
      the company floor;
    - turns repeated corrections on one tool into a lesson that goes into every new task's prompt;
    - the manager applies or dismisses each suggestion (*Authority* tab, or `employee_suggestions` /
      `apply_suggestion`).
  - **Growing waits between retries** when a round fails (30 s, 60 s, 120 s…). Migration `0014_employee_f2`.
  - Settings: `EMPLOYEE_RETRY_BASE_S`, `EMPLOYEE_DEDUPE_DAYS`, `LEARN_MIN_APPROVALS`, `LEARN_MIN_CORRECTIONS`.

## [0.16.1] — 2026-10-05

### Fixed
- **Digital employee admission respects the team's four-eyes rule.** Before, the manager's admission published to
  production directly. Now it goes through the normal ship flow: when the team requires approval and the manager
  cannot publish, it opens a promotion request and the employee becomes active only after someone else approves it.
  If you admitted a Digital employee on 0.16.0 in a team that requires approval, check its production version in
  the audit log (`employee.admission.approved`).
- Digital employees hired in English get the task prompt, the runtime instructions and the admission summary in
  English (they followed Portuguese before and sometimes mixed languages in their answers).
- Portal: results of tasks and probation render Markdown, and the action to approve shows each argument on its own
  line (an e-mail reads as it will be sent).

### Changed
- Interactive demo: three new steps for the Digital employee (probation, a decision on the exact e-mail, authority),
  recorded live with a real LLM.

## [0.16.0] — 2026-10-05

### Added
- **Digital employee (phase F1)**: agents hired for a job, with a human manager, an authority level and a task queue
  ([docs/digital-employee.md](docs/digital-employee.md)).
  - Hiring in self mode over MCP: `plan_employee` returns the missing fields with the question for the owner, and
    `hire_employee` creates nothing until the job is complete. The portal has the same flow as a guided form.
  - Lifecycle: onboarding → probation (real tasks in stage) → admission by the manager (ships to production) →
    active ↔ paused → offboarded.
  - Authority enforced by the platform: the runtime calls `POST /internal/gate` before every tool call and fails
    closed. Modes are auto, notify, approve, approve_2 and never; job rules with conditions sit under the autonomy
    level's default and the company floor, and the strictest wins. Tools are classified into an action catalog.
  - Human in the loop: tasks pause on the exact action. Decisions are approve, approve with edits, reject, instruct,
    answer and acknowledge, with separation of duties, two approvers, escalation to a backup and expiry (the action
    is not done). Approvals are one-time grants for that exact payload.
  - Tasks run from checkpoints with a timeline, a budget, a time limit and a round limit.
  - Daily reports in the portal and on a webhook (Slack or Teams, whichever the company uses).
  - Portal: *Digital employees* (list, hire, per-employee tabs) and *Decisions* (inbox with batch decide, badge, home
    card). Console: workforce overview with *Stop all now*, the action catalog and the company floor.
  - API under `/api/employees`, `/api/tasks`, `/api/decisions` and `/api/admin/{workforce,action-catalog,authority-floor}`;
    MCP tools for hiring, tasks, decisions and the lifecycle. Migration `0013_digital_employees`.
  - Settings: `EMPLOYEE_WORKERS`, `EMPLOYEE_MAX_RUNS`, `DECISION_EXPIRES_MIN`.

## [0.15.0] — 2026-10-04

### Added
- **Guide tab for every agent**, between Usage and Playground ([docs/guide.md](docs/guide.md)). It replaces the
  Multi-agent tab.
  - A flow diagram built from the spec. It shows which skills, MCPs, tools, memory, specialists (linked) and
    harness job the agent uses, never their content.
  - A fact sheet. Example requests come from the passed test cases; endpoints are shown only to people who can use
    the agent.
  - A Markdown guide for users: what it is, what it does, what it does not do, how to use it and its limits.
  - The guide text is versioned per agent version but kept outside the spec, so fixing documentation needs no new
    version or tests. The tab warns when the text was written for an older version.
  - When a version reaches production without a guide, the agent's LLM writes a draft from the spec and the passed
    tests only. It is marked "not reviewed" until a maintainer approves it, and it never replaces human text.
    `GUIDE_AUTOGEN=0` turns this off.
  - The first paragraph of a reviewed guide becomes the A2A Agent Card description.
  - API: `GET/PUT /api/agents/{slug}/guide`, `POST .../guide/approve`, `POST .../guide/generate`.
  - MCP: `get_agent_guide`, `set_agent_guide`.
- **The guide is a step of building an agent over MCP.**
  - It is step 7 of the platform instructions, before `ship_agent`.
  - `design_agent`, `compose_agent`, `build_multi_agent` (orchestrator and each member) and `edit_agent` accept
    `guide=` in the same call.
  - Every build tool and `ship_agent` return `guide.written`, with the next step when it is missing.
    `build_multi_agent` lists `guides_missing`.
  - The guide is saved for the version being built. When you edit a live agent, the new text waits for its version
    to be published; the tab shows "a guide for vN is ready".
  - The automatic draft runs only for agents that never had a guide written by a person.
- Comparison with CrewAI AMP, LangSmith Deployment, Dify, AWS Bedrock AgentCore and Microsoft Foundry + Agent 365,
  in both READMEs; details and sources in [docs/comparison.md](docs/comparison.md)
  ([pt-BR](docs/comparison.pt-BR.md)).
- [docs/cloud-vm.md](docs/cloud-vm.md): install on a cloud VM with Docker on AWS (EC2), Azure (Virtual Machines)
  and Google Cloud (Compute Engine). It covers the VM with SSH open only to you, Docker Engine, the installer and a
  tunnel for HTTPS. Each step links to the provider's documentation.

## [0.14.1] — 2026-10-03

### Added
- **ARM64 images.** Every published image is now multi-architecture (amd64 and arm64), built on native runners:
  Apple Silicon Macs run without emulation, and Kubernetes clusters with ARM nodes (AWS Graviton, Azure Ampere,
  GKE T2A) work.
- `list_agents` (MCP) returns `stage_version` and `prod_version`: the version running in each environment. `status`
  and `version` are the newest version, which can be a draft while production keeps running an older one.
- CI:
  - the installer and the CLI are tested on macOS;
  - a regression check runs a harness job with a workspace owned by another user, as on Kubernetes;
  - responses with a 4xx status on `/mcp` are logged with the JSON-RPC method, the protocol version and the error,
    to diagnose clients without reproducing.

### Fixed
- **Harness jobs on Kubernetes returned no diff.** The workspace volume belongs to root there, and git refused it
  ("dubious ownership"). The harness entrypoint now trusts the workspace for git and for the harness CLIs.

### Verified
- A clean install from the published 0.14.0 images (`install.sh --answers`, inside an isolated Docker) passed the
  end-to-end smoke test (19 checks). This includes the coding-harness extra, which builds the Claude Code image
  locally.
- The 0.14.0 chart, installed from `oci://ghcr.io/valteresj2/charts` on k3s with images pulled anonymously from
  ghcr.io, passed the same smoke test once the fix above was applied.

## [0.14.0] — 2026-10-03

### Added
- **English portal and console.** Both web apps are now available in English and Portuguese.
  - The first visit follows the browser's language; the **PT · EN** switch (sidebar footer and sign-in page) changes
    it, and the choice is kept in that browser.
  - Content written by people and agents (names, descriptions, conversations, test outputs, audit details) is shown
    as written.
  - Messages generated by the server (API errors, MCP instructions, the composer's recommendation) are still in
    Portuguese.

### Fixed
- The MCP gateway no longer restarts in a loop after Docker stops without a clean shutdown: its proxy removes the
  stale socket left on the volume (`unlink-early`).

### Changed
- Issue forms ask for the install method and the client involved, and a new form covers integration requests
  (clients, harnesses, LLM providers, MCP servers, templates).
- The published images on ghcr.io are now public, except `harness-claude-code`: Claude Code's license does not allow
  redistributing it. `hangar setup` builds that image locally when the coding-harness extra is chosen with published
  images. See [docs/harnesses.md](docs/harnesses.md#the-claude-code-image-is-not-published).

## [0.13.0] — 2026-10-02

### Added
- **Reuse before you build** ([docs/composer.md](docs/composer.md)): new agents are built from existing ones, and
  the existing ones are never changed.
  - `plan_agent` (MCP) / `POST /api/compose/plan` searches the catalog for similar agents, skills, MCPs and templates.
    - It only shows what the person can see, and only agents with a version in production.
    - It says how each agent can enter the new one (`use_as_is`, `specialist` or `base` copy) and whether access is
      needed.
    - It lists the missing skills, with at most 3 questions for the user.
    - Similarity uses the memory service's local multilingual embeddings (cached in the database), with a lexical
      fallback when memory is off.
  - `compose_agent` / `POST /api/compose` creates the new agent from the chosen pieces.
    - Pieces: specialists called as they are, a base copied into the new agent, catalog skills and MCPs, and new
      skills created from the user's answers.
    - The instructions and tests carry only what is new; tests come in from the base and from skills.
    - It records the lineage (`agent_lineage`) and an estimate of tokens reused.
  - Portal: **Novo agente → Reusar antes de construir**, plus "Construído a partir de" and "Usado por" on the agent
    page.
  - Metrics: `GET /api/compose/stats`.
- **Skills as building blocks:** skills now have `examples`, a `test` that runs in the stage tests of every agent
  composed with them, a `version` (raised on every change), a team and an author. Developers can create new skills
  (MCP `register_skill`); only admins change an existing one.
- The memory service exposes `POST /admin/embed` (central only) for the catalog search.

### Fixed
- **Shipping a multi-agent no longer changes its specialists when they are pieces of a composed agent.** Before,
  `ship_agent` re-ran the sub-agents' tests and could publish a sub-agent's unreleased version. Specialists of a
  composed agent are now read-only: they are called on their production version, and they are never tested or
  deployed by the composed agent.
- **Adding a sub-agent requires being allowed to use it** (`design_agent`, `edit_agent`, `PATCH/PUT` on the API).
  Before, a developer could add any visible agent of another team without an approved access request.
- Smoke checks for multi-agents allow 300 s per call; composed agents answer simple questions without calling their
  specialists.

### Upgrading
- Migration `0011_composer` runs on start: new table `agent_lineage`, new columns on `skills`.

## [0.12.0] — 2026-10-01

### Upgrading from 0.11.x
- No migration. New optional chart values (`observability`, `backup`, `externalSecrets`, `serviceAccount`, `postgresql.cloudSqlProxy`); defaults keep the current behavior, except JSON logs and the metrics port in the chart.

### Added
- **Observability** ([docs/observability.md](docs/observability.md)):
  - Prometheus metrics: agent calls, latency, tokens, LLM cost, jobs, HTTP by declared route, one series per replica.
    Served on a separate port (`METRICS_PORT`; `:9100` in the chart) and at `GET /api/metrics` for admins and auditors.
  - JSON logs (`LOG_FORMAT=json`, the chart default) with `trace_id`.
  - OpenTelemetry traces over OTLP (`OTEL_EXPORTER_OTLP_ENDPOINT`): requests, outgoing HTTP calls, and `traceparent`
    propagated to agents.
  - Chart: metrics port, ServiceMonitor, and a Grafana dashboard (ConfigMap for the sidecar).
- **Backup and upgrade** ([docs/backup.md](docs/backup.md)):
  - `hangar backup` and `hangar restore` (Docker, or the chart's Postgres); restore saves the current state first.
  - `hangar upgrade --version`: backup, new images, start, `hangar doctor`.
  - Chart: a daily `pg_dump` CronJob to a volume kept on uninstall, plus a **pre-upgrade backup hook**; if the backup
    fails, the upgrade does not happen.
- **Vault secrets:** `externalSecrets` in the chart (External Secrets Operator: AWS Secrets Manager, GCP Secret
  Manager, Azure Key Vault, Vault). The chart then generates nothing.
- **Cloud identity:** `serviceAccount.annotations` and `central.podLabels` for GKE Workload Identity, EKS IRSA and
  AKS Workload Identity; the **Cloud SQL Auth Proxy** as a native sidecar (`postgresql.cloudSqlProxy`).
- **Terraform** for GKE, AKS and EKS ([deploy/terraform](deploy/terraform/README.md)): cluster with NetworkPolicy,
  managed Postgres 16 (private, HA, 14-day backups), cloud identity, ingress with TLS, and the chart with 2 replicas.
  `hangar setup --configure-only` finishes the setup without touching the Helm release.
- **Supply chain** ([docs/supply-chain.md](docs/supply-chain.md)):
  - images and chart signed with cosign (keyless), with SBOM and SLSA provenance;
  - CI runs Trivy (image CVEs and chart misconfiguration; the chart scan blocks on CRITICAL/HIGH) and validates the
    Terraform.

### Changed
- Python images apply Debian security updates at build time (the central image goes from 7 HIGH CVEs to 0).
- The chart's Postgres runs with `runAsNonRoot` and a read-only root filesystem.

## [0.11.0] — 2026-10-01

### Added
- **OAuth for the platform MCP** ([docs/clients.md](docs/clients.md#claudeai-and-chatgpt-web-oauth-no-key-to-paste)):
  Claude.ai, ChatGPT and other clients connect by pasting only `<base>/mcp`.
  - Implements the MCP authorization spec: RFC 9728 protected-resource metadata and `WWW-Authenticate` on 401,
    RFC 8414 server metadata, dynamic client registration (RFC 7591), PKCE S256, revocation (RFC 7009).
  - Consent page in the portal (`/app/#/oauth`), after SSO or local sign-in.
  - Tokens: 1-hour access tokens valid only on `/mcp`; rotating refresh tokens with reuse detection.
  - **Minhas chaves → Apps conectados** to revoke; admins see every app and can block one; deactivating a person
    disconnects their apps.
  - Redirect hosts limited by `OAUTH_REDIRECT_HOSTS`; `OAUTH_ENABLED=0` turns it off.
- **High availability on Kubernetes** ([docs/kubernetes.md](docs/kubernetes.md#high-availability)): run 2+ central
  replicas.
  - State that lived in one process now lives in Postgres (table `ephemeral`): job and evaluation callbacks,
    single-use codes, the login lockout; remote-MCP token refreshes are serialized with a database lock.
  - Migrations run under an advisory lock.
  - Jobs record their replica (`worker`); a heartbeat lets surviving replicas close jobs of a replica that died.
  - Chart: `RollingUpdate` with `preStop`, PodDisruptionBudget, anti-affinity, `REPLICA_ID` from the pod name;
    installer question for the number of replicas.

### Fixed
- Image builds retry slow PyPI downloads (`PIP_RETRIES`, `PIP_DEFAULT_TIMEOUT`): the Data Studio CI job failed on a
  read timeout.

### Upgrading
- Migration `0010_oauth_ha` runs on start (new tables `ephemeral`, `oauth_clients`, `oauth_grants`; column
  `jobs.worker`). Nothing changes for existing clients.

## [0.10.0] — 2026-10-01

### Upgrading from 0.9.x
- No migration. Docker installs keep working as before; `hangar setup` can now take over an existing `.env` (secrets are kept, the old file is backed up).

### Added
- **Guided installer: `hangar setup`** ([docs/install.md](docs/install.md)), started by `scripts/install.sh` /
  `install.ps1`, which install the CLI in an isolated venv. It asks seven questions:
  - images: published or built from this checkout;
  - exposure: local, Cloudflare Tunnel, ngrok or Tailscale Funnel;
  - the admin account;
  - the LLM: a direct provider or a corporate gateway;
  - memory: Neo4j, FalkorDB or none;
  - sign-in: local accounts, Google, Entra ID, GitHub or OAuth2;
  - extras, the code policy and the AI tools to configure.

  Then it writes `.env` (secrets kept, previous file backed up), turns on the profiles via `COMPOSE_PROFILES`, starts
  the stack, registers and tests the LLM, creates the admin, and writes the AI tools' configs to `.hangar/clients/`
  with a personal token.
  - Unattended mode: `--answers setup.yaml` (see `setup.example.yaml`). Re-running it is safe.
- **`hangar doctor`**: checks Docker and containers, the central, admin access, each LLM connection (real call),
  memory, the public address and TLS, sign-in, the platform MCP, the VS Code extension and agents. Each failure comes
  with the fix; it exits with `1` on failure, and `--json` is available.
- **Tunnels as Compose profiles:** `tunnel-cloudflare` (cloudflared), `tunnel-ngrok` and `tunnel-tailscale` (Funnel).
- **LLM connection test:** `POST /api/catalog/llm/{name}/test` and a **Testar** button in the catalog.
- The generic OAuth2 login variables (`OAUTH_OAUTH2_*`) are now passed to the central by `docker-compose.yml`.

- **Kubernetes** ([docs/kubernetes.md](docs/kubernetes.md)):
  - **runtime driver** (`RUNTIME_BACKEND=kubernetes`): each agent is a `Deployment` + `Service` + `Secret`; harness
    runs and code evaluations are `Job`s whose `Secret` is owned by the Job. Pods run as non-root, read-only root
    filesystem (agents), all capabilities dropped, `seccomp: RuntimeDefault`, no service-account token;
  - **Helm chart** `charts/agent-hangar`: namespace-scoped RBAC, NetworkPolicies (agents and jobs reach only the
    central, DNS and the internet minus private ranges), Postgres in the cluster or managed, memory with Neo4j or
    FalkorDB, Ingress (GKE managed certificate, AKS app routing, ALB, cert-manager), Cloudflare Tunnel, secrets
    generated once and kept on upgrades or taken from an existing Secret;
  - **presets** `values-gke.yaml`, `values-aks.yaml`, `values-eks.yaml`, `values-local.yaml`;
  - **`hangar setup --target kubernetes`** writes the values (secrets in a separate 600 file), runs Helm and configures
    the installation through a temporary port-forward; **`hangar doctor --namespace`** checks the pods too;
  - the chart is published as OCI (`oci://ghcr.io/valteresj2/charts/agent-hangar`) with each release; CI lints it and
    renders every preset.

### Fixed
- `/downloads/agent-hangar-vscode.vsix` answers `HEAD` too.

## [0.9.0] — 2026-09-30

### Upgrading from 0.8.x
- **Migration `0009_code_policy` runs on start.** Nothing changes by default: the company policy starts as `any`.
- **Rebuild or pull `central`, `agent-runtime` and `harness-base`,** which now includes Python 3, pytest and the code
  evaluation runner.
- **Re-ship coding agents** to get secret masking.
- **To use code evaluations,** add test cases with `workspace` (see the `code-assistant` template).

### Added
- **Code evaluations in stage** (test cases with `workspace`, [docs/spec.md](docs/spec.md#code-evaluations-workspace)).
  - A coding agent gets a real mini-project in a throwaway sandbox, with file and terminal tools like a coding
    client. It passes only if `check` exits with 0 and no `protected` file (the tests) changed.
  - Promotion stays blocked until the agent really solves the tasks. In a live run, an agent that edited the test to
    "pass" was failed.
  - The `harness-base` image gains Python 3, pytest and the runner (`eval_runner.js`).
  - The platform MCP tells builders to include these cases for coding agents.
- **Governance for code** ([docs/clients.md](docs/clients.md#coding-agents-in-vs-code-client-tools)).
  - A company `code_policy` (`any` or `approved`) and LLM connections **approved for code**, managed in **Catálogo →
    Conexões de LLM**.
  - With `approved`, the gateway refuses coding mode (`403 code_policy`) on unapproved connections, live and with no
    redeploy. The connection test and code evaluations say why. Plain chat is not affected.
  - Migration `0009_code_policy`.
- **Secret masking in coding mode.** Keys, tokens, private keys, `.env` values, password literals and database URL
  passwords are masked in client tool results before they reach the LLM. Only the value is replaced.
  `llm.redact_secrets: false` turns it off.
- **Template `code-assistant`**: a coding agent with standards and two code evaluations (a bug fix and a feature).

### Fixed
- The runtime answers `400` (not `500`) to a request body that is not UTF-8 JSON.

## [0.8.0] — 2026-09-30

### Upgrading from 0.7.x
- **No database migrations.**
- **Rebuild or pull `central` and `agent-runtime`, then re-ship the agents** that should work as coding agents. The
  runtime now handles client tools.
- **The central image build needs Node** (a build stage that packages the VS Code extension). The published images
  already include it.

### Added
- **Agents as coding agents in VS Code, Cline, Roo Code and Continue** (client tools,
  [docs/clients.md](docs/clients.md#coding-agents-in-vs-code-client-tools)).
  - The OpenAI-compatible endpoint takes the client's `tools` and returns `tool_calls` for the client to run: files
    and the terminal stay on the developer's machine. The agent's own tools (MCPs, memory, sub-agents) still run on
    the server.
  - If both come in one round, the server part runs first and its context is put back when the client returns its
    results.
  - Works with and without streaming. `llm.client_tools: false` turns it off per agent.
- **Testar conexão** in the Connect tab (`POST /api/agents/{slug}/connections/test`, and the MCP tool
  `test_agent_connection`): an end-to-end check of the coding-agent path, over streaming, with a probe tool.
- New Connect options in model mode: VS Code (coding agent), Cline / Roo Code and Continue.
- **VS Code extension "Agent Hangar"** ([extensions/vscode](extensions/vscode/),
  [docs](docs/clients.md#vs-code-extension-agent-hangar)).
  - The agents you can use appear in the chat's model picker. In Agent mode they edit and test your project with
    VS Code's tools.
  - The extension also registers the platform MCP.
  - One-click sign-in through the portal with PKCE (`/api/vscode/authorize`, `/api/auth/vscode/token`, single-use
    code). Signing out revokes the key.
  - A connection test command, and a `stage` setting to try agents before publishing.
  - The central image builds and tests the `.vsix` and serves it at `/downloads/agent-hangar-vscode.vsix`. The portal
    gets **Baixar extensão** and **Abrir no VS Code** buttons.

## [0.7.0] — 2026-09-30

### Upgrading from 0.6.x
- **No database migrations.** Rebuild or pull `central` (and `mcp-memory`, if you use memory).
- **Non-admins now land in `/app/`.** Bookmarks to `/ui/` still work: the page redirects them.

### Added
- **User portal (`/app/`) with an Início page** ([docs/access.md](docs/access.md#where-each-person-lands)). One
  screen for everyone who is not an admin:
  - what needs attention (failed tests, a production agent down, failed schedules, recent errors, budgets at
    80%+, requests to decide);
  - your agents, your requests, team budgets and your own usage, upcoming schedules;
  - your connections (keys, with revoke), agent memory, and what's new in the catalog.
  - Backed by `GET /api/me/home`.

### Changed
- **The `/ui/` console is admin-only.** Signed-in non-admins are redirected to the portal.
- The UI's stale-cache reload now runs at most once, so a script error can no longer cause a reload loop.
- **Agent replies render as markdown** (bold, lists, headings) in the Playground and in schedule history. The text
  is escaped first, so no HTML from the model reaches the page.
- **README rewritten.** It has a portal tour (GIF and MP4) and a real end-to-end example: a user asks Claude for an
  agent, it is built, tested, shipped and scheduled through the MCP, and the team uses it.

### Fixed
- **Memory: facts with a future end date count as current.** "On trial until 15/10" still holds today. Graphiti
  marks such facts as expired at ingestion, and they were shown as replaced.
- **Memory: `recall` returns today's date.** The agent can then tell whether a validity window has passed; without
  it, the model guessed.

## [0.6.0] — 2026-09-30

### Upgrading from 0.5.x
- **No database migrations.** Memory lives in its own graph database.
- **Rebuild or pull `central` and `agent-runtime`, then re-ship agents.** Containers now get a per-environment
  token (`INTERNAL_ENV_TOKEN`). Agents that aren't re-shipped are treated as stage when they use memory.
- **Optional profile `memory`:**
  - Run `scripts/setup.sh` or `setup.ps1` again to generate `MEMORY_TOKEN` and `NEO4J_PASSWORD`.
  - Set `MEMORY_URL=http://memory:8000` and `MEMORY_LLM_CONNECTION` in `.env`.
  - Run `docker compose --profile memory up -d`.

  See [docs/memory.md](docs/memory.md).

### Added
- **Agent memory (PoC)** ([docs/memory.md](docs/memory.md)): long-term memory as a temporal knowledge graph
  (Graphiti), with Neo4j Community by default (profile `memory`) or FalkorDB (profile `memory-falkordb`).
  - A spec field `memory: {scope: agent|team|org, write}` gives the agent `memory__recall` and `memory__remember`.
  - Writes are asynchronous. Changed facts are invalidated with their dates, not deleted.
  - The central decides the memory groups. Stage reads production memory but writes to its own group, proven by
    a new per-environment container token.
  - Local embeddings (fastembed), so no memory text leaves the network to be vectorized. The extraction LLM
    comes from an encrypted LLM connection.
  - An agent **Memory** tab to browse, search and delete memory, plus a `memory` argument on `design_agent`.
    Deleting an agent deletes its own memory.
  - New image `mcp-memory`.

## [0.5.0] — 2026-09-30

### Added
- **Remote MCPs with OAuth** ([docs/remote-mcp.md](docs/remote-mcp.md)).
  - An admin connects once: discovery (RFC 9728/8414), dynamic client registration, and authorization code with
    PKCE.
  - Tokens are stored encrypted and refreshed automatically.
  - Agents use the MCP through the central's internal proxy (`/internal/mcp-remote/<name>`). Only agents that
    list the MCP in their spec get through, and the provider token never reaches them.
- **Activepieces** (compose profile `activepieces`): 280+ business integrations through its MCP (`ap_run_action`,
  tables, flows). Tested end to end.
- **Docker MCP catalog** (compose profile `mcp-gateway`, [docs/mcp-catalog.md](docs/mcp-catalog.md)): 230+
  prebuilt MCP servers from Docker's official catalog, served by the open-source Docker MCP Gateway (MIT).
  - Admins search and enable servers in **Catálogo → Catálogo Docker MCP**. Credentials are stored encrypted.
  - Each enabled server becomes the catalog MCP `docker:<name>`, and an agent sees only that server's tools
    (new `tool_prefix` on catalog MCPs).
  - Safeguards: signed images; the LLM cannot add servers by itself (`dynamic-tools` disabled); servers run on
    their own network, without agents or the database; Docker is reached only through a restricted proxy.
  - API: `/api/mcp-gateway/catalog`, `/status` and `/servers/{name}`.

### Upgrading from 0.4.x
- **Migrations 0007 and 0008 run on start.** They add the Docker MCP catalog tables and remote MCPs.
- **Rebuild or pull `central` and `agent-runtime`, then re-ship agents** that should use the new MCPs. The
  runtime now filters gateway tools by `tool_prefix` and authenticates to the central's MCP proxy.
- **Optional profiles:**
  - `mcp-gateway` (Docker MCP catalog);
  - `activepieces`. Run `scripts/setup.sh` or `setup.ps1` again to generate its secrets.

## [0.4.0] — 2026-09-29

### Added
- **Agent schedules.** An agent in production runs by itself at the day and time the user asks for.
  - **Recurrence and time zone:** a cron expression or a one-off date and time, in the company's time zone
    (`PATCH /api/org {"timezone": …}`) or the schedule's own.
  - **MCP:** `schedule_agent`, `list_schedules`, `update_schedule`, `delete_schedule`, `run_schedule_now` and
    `schedule_runs`. The platform guide tells the assistant to schedule when the request includes days and times.
  - **Before production:** a schedule created before the agent goes live waits and starts once the agent is in
    production.
  - **Results:** a run history with the answer, tokens and cost, and an optional webhook (Slack, Teams or HTTP).
  - **Where to find it:** a **Schedules** tab on each agent, *Upcoming scheduled runs* on the dashboard, the API
    (`/api/agents/{slug}/schedules`, `/api/schedules/…`) and the `hangar schedules` CLI.
  - **Safeguards:** only editors can schedule, runs are at least 5 minutes apart, the team budget is respected,
    and webhooks to the internal network are blocked. The scheduler reserves each run with a conditional update,
    so it is safe with several replicas. A run missed while the central was down runs once, marked as `late`.
- **Edit agents later through the MCP** (and the API):
  - `get_agent_spec` shows the current spec and which version runs in production and in stage;
  - `edit_agent` applies changes to the spec and to name, objective, final output and contact as a new
    version, deploys it to stage and runs the tests. Production keeps the previous version. With
    `promote=True` it publishes only if the tests pass, or asks for approval when the role or team requires it;
  - `diff_agent_versions` shows what changed before publishing;
  - `update_agent_access` changes visibility, owner team and exposed spec.
- API: `POST /api/agents/{slug}/edit` and `GET /api/agents/{slug}/diff`.

### Fixed
- Deleting an agent that had schedules, access requests or promotion requests failed on Postgres.
- Elements with the `hidden` attribute could still show when a component set `display`.

### Upgrading from 0.3.x
- **Migration 0006 runs on start.** It adds the schedules and run history, and the company time zone.
- **Set the company time zone** used by default in schedules: `PATCH /api/org {"timezone": "America/Sao_Paulo"}`.
  The default is UTC.
- **The scheduler runs inside the central** (`SCHEDULER_ENABLED=1`). With more than one replica, each run is
  claimed once.

## [0.3.1] — 2026-09-29

### Fixed
- Text fields that send with Enter (Playground, token login, new team name) blocked typing: only accented letters
  got through. The key handler returned `false` for every other key, which cancels the keystroke. A test now
  guards against this pattern.
- Playground:
  - the send button is disabled while the agent answers, and focus returns to the field;
  - latency, tokens, cost and tools used appear under each answer;
  - errors are highlighted.

## [0.3.0] — 2026-09-28

### Added
- **Users, teams and roles.**
  - Company roles: admin, auditor and member.
  - Team roles: maintainer, developer and consumer.
  - Every agent belongs to a team. Existing agents move to the default **Plataforma** team.
- **Agent visibility** (`private`, `org`, `open`), with a **company catalog** and **access requests** that a
  maintainer approves. Instructions and spec stay hidden from other teams unless `expose_spec` is on.
- **Production approval (four eyes).**
  - When a developer ships, or when the team requires approval, a pinned promotion request is created.
  - Another maintainer or an admin approves it.
- **Login with OAuth2** (authorization code + PKCE): Google, Microsoft Entra ID, GitHub, and a generic OAuth2
  provider (Keycloak, Okta, Auth0; use Keycloak as the bridge for SAML).
  - Allowed domains, a fixed tenant, and required GitHub organizations.
  - Groups map to teams.
  - `BOOTSTRAP_ADMIN_EMAILS`.
- **Local accounts** (username and password, optional with `LOCAL_LOGIN`).
  - Passwords are hashed with scrypt; accounts lock out after 5 failures.
  - Users change their own password; admins reset passwords.
- **SCIM 2.0** (`/scim/v2`): the directory provisions and deactivates users and syncs groups to teams.
- **Sessions.** The UI uses a `HttpOnly` session cookie with CSRF protection instead of a token in
  `localStorage`. "Sign in with token" remains as break-glass access.
- **Keys belong to people.**
  - New scopes `user` (personal token for the CLI and MCP) and `scim`.
  - Keys are revoked automatically when the owner loses access or is deactivated.
- **Monthly budget per team**, with an alert and an optional block (`429` at the gateway).
- Creating a team: a visible **+ Novo time** button (list and team detail) and an optional first maintainer (`maintainer` on `POST /api/teams`).
- UI pages: Aprovações, Times, Usuários, SSO e SCIM; an agent **Acesso** tab; role-aware buttons and tabs.
- CLI: `whoami`, `teams`, `approvals`, `request-access`, `agents ls --mine`, `templates apply --team`.
- Platform MCP: tools act as the key owner. New tools: `whoami`, `request_agent_access`, `list_approvals` and
  `decide_approval`. Catalog writes are admin-only.
- **Plug-and-play tool connections.** A new **Connect** tab on each agent, `hangar connect`, the
  `connect_agent` platform MCP tool and `/api/agents/{slug}/connections`:
  - MCP mode is offered for every tool: Claude Code, Claude Desktop, Codex, OpenCode, Cursor, VS Code, LibreChat,
    Open WebUI, and any other MCP client;
  - model mode is offered for chat tools: LibreChat, Open WebUI, OpenCode, and OpenAI SDKs;
  - each connection is a separate `invoke` key limited to that agent and tagged with the tool, so it can be
    revoked on its own;
  - the config comes ready to paste.
- The gateway tags usage with the key's tool when `X-Channel` is absent.
- Runtime: Open WebUI chat/user headers (`X-OpenWebUI-Chat-Id`) set the session.

### Changed
- **New UI design** following the *Frontend Design* skill (aitmpl.com), with the pre-delivery checklist of
  *UI/UX Pro Max*.
  - **Hangar look:** a tarmac-dark sidebar with grouped navigation (Operação, Construção, Monitoramento,
    Acesso) and a single safety-yellow accent for primary actions, the active item and focus.
  - **Type:** Barlow Semi Condensed for headings, IBM Plex Sans for text, IBM Plex Mono for code.
  - **Screens:** KPIs as one status board, agent rows as flight strips colored by status, and a two-column
    login.
  - **Icons:** SVG icons instead of emojis.
  - **Accessibility:** visible keyboard focus, 44px touch targets, `prefers-reduced-motion`, and dark mode.

### Fixed
- `http://…/ui` without the trailing slash returned 404; `/ui` and `/login` now redirect to the UI, and `/favicon.ico` exists.
- A page left in the browser cache by an older version reloads itself at the current version instead of failing.
- The UI is served with `Cache-Control: no-cache`, and its assets carry a content hash (`app.js?v=…`), so
  browsers never keep an old version after an upgrade.
- Calls through an agent's MCP endpoint now record tokens and cost (the tool result carries `_meta.usage`).
- MCP handshakes, `tools/list` and pings are no longer counted as requests.

### Upgrading from 0.2.x
- **Migrations run on start.** Migration 0004 adds the users, teams and access tables, and 0005 adds local
  accounts. Existing agents move to the default **Plataforma** team with visibility `org`.
- **The UI now opens on a login page.** The first time, use **Entrar com token** with your `ADMIN_TOKEN`. Then
  create people in **Usuários** (SSO, or a local account with username and password) and teams in **Times**.
  You can also set `BOOTSTRAP_ADMIN_EMAILS` to get admin on the first SSO login.
- **Existing keys keep working.** Admin keys and invoke keys, such as the one in LibreChat, are unchanged.
- **Personal tokens for builders.** People who build agents through the platform MCP should switch to a personal
  token (scope `user`), so the client acts with their team roles.
- **Re-ship chat agents** (`hangar ship <slug>`) to get the new runtime: MCP usage with tokens and Open WebUI
  session headers.

## [0.2.1] — 2026-09-27

### Fixed
- `docker compose up data-studio` now also starts its Docker proxy (`data-studio-docker`); before, container-sandbox
  mode failed on the first kernel start.

## [0.2.0] — 2026-09-27

### Added
- **Data Studio MCP server** (`mcp-servers/data-studio`, compose profile `data-studio`):
  - a per-conversation workspace, with its own uid and its own stateful Python kernel;
  - DuckDB SQL, and file inspection with OCR;
  - PPTX (native charts) and HTML decks, interactive HTML dashboards, DOCX/HTML reports;
  - URL fetch with an SSRF guard;
  - signed download links.
- **`data-studio` template** and **LibreChat integration kit** (`integrations/librechat`), which uses the agent as
  LibreChat's engine through the OpenAI-compatible gateway.
- Runtime:
  - OpenAI-format attachments (`image_url`, `file`) are saved to the agent's MCP workspace via `ingest_file`;
  - the client conversation id is forwarded to MCPs as `X-Session-Id`;
  - client system prompts are kept;
  - SSE keepalive while tools run;
  - currency `$` is escaped for LaTeX-rendering channels.
- Spec: `llm.max_steps` and `llm.vision`.
- **Live tool progress** streamed as `reasoning_content` (a collapsible "Thoughts" block in LibreChat).
- **Lite mode** (`X-Hangar-Mode: lite`): one short LLM call without tools, used for conversation titles (about 20x
  cheaper). The LibreChat kit routes titles there via `titleEndpoint`.
- Images returned by MCP tools are passed to vision models, so an agent can look at its own output.
- Data Studio:
  - `preview_file` renders PPTX/DOCX/XLSX/PDF with LibreOffice and returns the images to the agent for visual QA;
  - `offline=true` embeds the chart libraries in HTML;
  - compact KPI formats (`R$ 6,26 mi`);
  - workspace retention (`RETENTION_DAYS`);
  - optional **one sandbox container per conversation** (`DATA_STUDIO_SANDBOX=container`).
- The UI "Connect" page shows the full LibreChat config (per-conversation sessions, attachments, titles).
- E2E: attachments flow through the gateway into the MCP workspace; streamed usage is recorded. CI also runs the Data
  Studio smoke test in container-sandbox mode, plus the retention test.
- README demo GIF and screenshots, recorded by `scripts/demo/record_demo.py`.

### Changed
- Postgres moved to an internal `db_net` network that agents can't reach.
- The gateway forwards `X-Conversation-Id`, `X-Session-Id` and `X-User-Id`, allows up to 900 s between stream chunks,
  and records token usage and cost from streamed responses.

## [0.1.0] — 2026-09-27

First public release (previously an internal prototype called "Central de Agents").

### Added
- Scoped API keys (`admin`, `invoke` limited to specific agents); keys stored as SHA-256 and shown once.
- Per-agent internal tokens (HMAC of the slug): agents can only call their own sub-agents and, if allowed, the
  read-only dashboard. The admin token is never injected into containers.
- Connection API keys encrypted at rest (Fernet, `HANGAR_SECRET_KEY`); existing plaintext keys are migrated on startup.
- Docker access through `docker-socket-proxy` (no socket mounted in the hangar).
- Typed agent spec (Pydantic, JSON Schema at `/api/spec/schema`), JSON Merge Patch updates (`null` deletes),
  full-spec replace, rollback to any version.
- Async harness jobs: queue, cancel, live status/logs over SSE, orphan recovery after restart.
- One Docker image per harness (base/mock, claude-code, codex, deepseek-harness, hermes) with pinned versions;
  DeepSeek Harness telemetry disabled.
- Token usage from harness jobs (Claude Code, Codex, Hermes) and cost in US$ from per-connection prices.
- LLM-as-judge test cases (`judge` rubric); smoke tests include MCP `tools/list`; mock agents skip content checks.
- GitOps: `POST /api/apply` and `hangar apply -f`; template gallery (6 templates) in UI, API, CLI and MCP.
- `hangar` CLI; UI pages for creating agents, spec editor, rollback, templates, API keys, costs, async job playground.
- Alembic migrations (pre-Alembic databases are stamped automatically), pytest suite, E2E smoke script, CI.

### Changed
- Project renamed to **Agent Hangar**; compose project `agent-hangar`, networks `hangar_agents` / `hangar_jobs`.
- The gateway streams SSE responses instead of buffering them; the Docker state of all agents is read in a single call.

### Upgrading from "central-agents"
Stop the old stack without removing volumes, then add these to `.env`:
- `HANGAR_SECRET_KEY` and `INTERNAL_SECRET` (run `scripts/setup.sh`).
- `PGDATA_VOLUME=central-agents_pgdata`.
- `POSTGRES_USER=central` and `POSTGRES_DB=central`.

Then run `docker compose up -d --build` and re-ship your agents (`hangar ship <slug>`) so their containers get
per-agent tokens.
