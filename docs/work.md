# Day-to-day work

How people and Digital employees work together day to day: goals and projects give each task its *why*, the task
has a conversation, budgets stop runaway spend, and the inbox and search show what needs you. One installation can
also serve several companies, each fully isolated. The full list of features, by who uses them, is in
[feature-map.md](feature-map.md).

## Goals and projects

- **Goals** form a tree. A company goal has no team and only an admin creates it. A team goal is created by the
  team's maintainer and can sit under a company goal or a goal of the same team. Each goal has an owner, a status
  (planned, active, achieved, dropped) and an optional target date. Everyone on the platform can see the goals; only
  the owner, the team's maintainer or an admin edits them.
- **Projects** belong to a team. They group tasks around an outcome and carry:
  - a goal;
  - instructions, which go into the prompt of every task in the project;
  - a default Digital employee.

  Developers and maintainers of the team create projects; team members see them.
- **The task knows why.** A task created in a project gets this in its prompt:
  - the project and its instructions;
  - the chain of goals up to the company's.

  The task page shows the same chain. Assign in a project from the project page, from *Deliver a task* on the
  employee page (the *Project* field), or through the API with `project`.

## The task's conversation

The requester, or anyone who can assign tasks to the employee, writes on the task.

- **While the employee works,** the message waits in a queue. Its author can edit or discard it until it is
  delivered. The next round starts by delivering the queue, in order. If a round would finish the task but a message
  arrived during it, the task goes for one more round instead of closing.
- **On a finished or failed task,** a message reopens it as a follow-up ("now send it to the manager"). Probation
  tasks do not reopen.
- **What the employee says** at the end of each round also goes into the conversation.

## Budgets

A budget applies to the company, a team, an agent (including a Digital employee's agent) or a project, per day or per
month.

- **Spend:** the cost the platform records (`UsageEvent`). For projects, it is the cost of their tasks. Usage without a
  known price counts as zero, so this is not the provider's invoice.
- **At `warn_percent`,** a warning incident opens once per window, and the people who answer for the scope are
  notified through their decision channel:
  - company scope: admins;
  - team, agent or project: the team's maintainers, plus the employee's manager for an agent.
- **At the limit, with `hard_stop`:**
  - the gateway refuses calls to the agents in the scope (`429`);
  - Digital employee tasks wait in `waiting_budget` without losing their checkpoint.
- **Releasing.** Either way, the waiting tasks go back to the queue:
  - raise the limit, and the stop closes on its own once the spend is below it;
  - or release the rest of the window, so the policy does not stop again until the next window.

Admins manage company budgets; a team's maintainer manages the budgets of the team, its agents and its projects. The
monthly team budget on the team page still works as before.

## Inbox and search

The **inbox** (`#/inbox`) gathers, per person:
- the decisions they can take;
- the tasks they asked for, with news since they last opened them;
- tasks of the Digital employees they manage that failed, are overdue or are stopped by a budget;
- budget warnings and stops of the scopes they answer for.

Opening a task marks it as read. Archiving is personal: it does not finish or cancel anything.

**Search** (`Ctrl+K`, or `#/search`) looks across agents, Digital employees, tasks (title, request and result),
projects, goals, plugins and skills. It only returns what the person can see.

**Who reads a task.** Tasks are read by the requester, by whoever answers for the employee (manager, owner, backup),
by members of the employee's team, and by auditors and admins. Seeing an agent in the company catalog no longer gives
access to its work.

## Dependencies, review and hold

- **Dependencies.** A task can depend on others (`blocked_by` when assigning, or *Add dependency* on the task). It
  waits in `blocked` until they are all done, then joins the queue with each one's result in its prompt
  ("PREREQUISITE TASK #12 DONE: …"). A dependency that fails or is cancelled leaves the task stuck (it shows in the
  inbox) until someone continues the dependency or removes it. Cycles are refused.
- **Review before finishing.** With *The result goes through review* on the task (`review`, optional `reviewer`
  e-mail) or the job's *Review of results* set to the manager, a finished round does not close the task: it goes to
  `in_review` and the reviewer gets a decision with the result. *Approve and finish* closes it; *Request changes*
  sends the reason back to the employee as one more round.
- **Hold.** *Pause* holds the task and the tasks it handed off (`on_hold`) until someone resumes it. A task in the
  middle of a round is paused after the round, or cancelled.

## Files and deliverables

- **Attach** a file on the task page (the requester or anyone who can assign tasks to the employee). The employee
  hears about it in the conversation and reads it with `read_file`.
- **Deliverables.** The employee saves outputs with `save_file` (text or base64). The same name becomes a new version;
  older ones stay downloadable. `list_files`, `read_file` and `save_file` are platform tools inside the employee's own
  task, so they need no authority.
- **Viewing.** Text, common images and PDF open in the browser; anything else (HTML, SVG…) only downloads, and every
  response carries a sandboxing `Content-Security-Policy`.
- **Limits.** Files are stored in the database, which works with several replicas without a shared volume:
  `FILES_MAX_MB` (10) per file and `FILES_TASK_MAX_MB` (50) per task.
- **Deliverables page** (`#/files`): the latest version of every output from the tasks you can read.

## Recovery

A stopped task explains itself in *Why it stopped*:
- the state (failed, budget, decision, colleague, dependency, paused, retry scheduled);
- the cause;
- who has the next action, and the actions available.

**Continue the task** gives a failed task 3 more rounds from its checkpoint. If the last round may have done
something outside the platform (an action that is not just reading, allowed without approval), it asks you to
confirm first, and the employee is told not to repeat it.

## Job history

Every change to a Digital employee's job, authority or settings becomes a version:
- what the job looked like after the change;
- who changed it, and when;
- which fields changed.

*Restore* applies an old version as a new one, so nothing is lost. The autonomy level and shadow mode keep their own
history in the career plan.

## Org chart

A Digital employee can report to another one from the same team (*Settings → Teamwork → Reports to*). The chart is
at `#/org`. The manager of each employee is still a person; the chief is the line of work between agents:
- **Handoffs.** The chief can hand work down to whoever reports to it, and they can hand up to the chief what is not
  theirs, besides the colleagues the manager allowed. Handoffs still go through the authority (`delegate`).
- **Prompt.** The task prompt says who the employee reports to and who reports to it.
- **Rules.** Cycles and chiefs from another team are refused.

## Skill Studio

`#/skills` lists the company's skills with their version, test cases, last run and status.
- **Editing.** An admin or a developer of the skill's team edits it. Each save becomes a version, with a note.
- **Test cases.** Cases are spec test cases: `input` plus `expect_contains`, `expect_regex` or `judge`.
- **Runs.** A run sends each case to an LLM connection (protocol openai), with the skill as the system prompt. Without
  a connection, the run is a mock and the content is not evaluated. Runs are kept per version, so versions can be
  compared.
- **Restore and deprecate.** *Restore* applies an old version as a new one. *Deprecate* marks a skill not to be used
  in new agents.

## Skills tab (packages)

*Capacidades → Skills* lists the skills the way Claude does: search, *Todas* / *Minhas*, sort, and **+ Adicionar**
with two options.

- **Fazer upload de Skill:** a `.md` with `name` and `description` in its YAML header, or a `.zip`/`.skill` with a
  `SKILL.md`, several at once. Text files next to it (`references/`, `scripts/`, `assets/`…) come along; binaries are
  skipped.
- **Criar uma Skill:** name, description, the `SKILL.md` body and extra files (*Adicionar arquivo*). A draft can be
  kept in the browser.

**Security check** on every upload:

- **Blocks:** files outside the package, secrets in the text (API keys, private keys, passwords: use the vault,
  `secret://name`), files too large.
- **Warns:** text that tries to override instructions or send data to an external address, and scripts (agents do not
  run a skill's scripts).

**The popup.** Clicking a skill opens it in two tabs:

- **Visão Geral:** description, author, team, version, last test.
- **Conteúdo:** the file tree and each file formatted or as code, for any version in the history.

From the **⋮** menu you can download the package (`<name>/SKILL.md` plus files, any version), update it from a file
(the name in the header must match) or edit it. Every change is a new version in the Skill Studio, including a
change to the files only.

**What the agent receives:** the `SKILL.md`, plus the text files under `references/` up to 30,000 characters. The
other files are only listed.

## Team packages

`#/packages` moves a team's setup to another team or installation, or starts from a ready-made team. A package
(`kind: hangar-package`) has:
- the skills the employees use;
- the team's goals;
- the Digital employees: full job, authority, probation tasks, routines, chief and colleagues;
- the projects.

**Exporting** (a developer of the team) scrubs secrets. Credentials and webhooks are left out, and keys that look like
secrets (token, password, key, authorization) are emptied. Plugin installs and budgets are not included.

**Importing** (the team's maintainer) always starts with a preview: what will be created, what already exists and
will be reused or renamed, and what is missing (a manager that does not exist, a plugin that is not approved). The
Digital employees are created as drafts and still go through probation and the manager's admission.

Ready-made teams live in `templates/packages/`. The *Renewals team* comes with:
- a coordinator and an analyst who reports to it;
- a goal and a project;
- a weekly routine.

## Secrets vault

`#/secrets` keeps credentials in one place, for the company or a team.
- **Referencing.** Use `secret://name` in place of the value. For now this works in the credential and the secret
  settings of a plugin install. The value is looked up when used: the install's team first, then the company.
- **Changing a value** applies at once wherever it is used, and the version goes up.
- **Never shown.** The value is encrypted and never returned. Each use is counted: how many times, the last time and
  by whom.
- **Who manages:** admins for everything, a team's maintainer for the team's secrets. Anyone else proposes a value;
  someone who manages approves it (not the person who proposed it), and the proposed value is not kept after the
  decision.

## Companies (multi-company)

One installation can serve several companies. Each one has its own people, teams, agents, Digital employees, plugins,
skills, LLM connections, budgets, secrets, channels (Slack, Teams, Telegram, Discord) and audit log; no one in one
company sees anything from another — not in lists, not by id, not in search.

- **Who manages them:** the admin token and the admins of the main company (id 1). *Organization → Companies* creates a
  company (it starts with a *Geral* team and its first admin, who signs in through SSO or with a local username and
  password), sets the e-mail domains whose people join it on their first SSO sign-in, and suspends or reactivates it
  (a suspended company's people can't sign in; the main company can't be suspended).
- **Acting inside a company:** an instance admin picks it in the sidebar selector (or sends `X-Hangar-Org: <id or
  slug>` on the API). Everyone else always acts in their own company.
- **Names:** skills, LLM connections, MCP servers, plugins, the action catalog and the authority floor are unique per
  company. Slugs of agents, teams and projects stay unique across the installation (they name containers and URLs).
- **Under the hood:** every company table has `org_id`; every query is filtered by the request's company (including
  lookups by id) and every new row is stamped with it. Background work (task runner, schedules, sweeps, Slack, Teams,
  Telegram, Discord and webhooks) runs inside the company of the record it handles.
- **Upgrading:** migration `0026` adds `org_id` to the company tables; every existing row belongs to the main company,
  so a single-company installation keeps working as before.

## Getting started, announcements, favorites and live view

- **Getting started:** the home page shows the next steps — for admins, the company's (connect a model, create a team,
  invite people, the first agent, hire a Digital employee, turn on a channel, set a budget, see a finished task); for
  everyone else, their own (join a team, connect a tool, ask for a task, choose where decisions reach them). It goes
  away when everything is done, or with *Hide*.
- **Announcements:** admins publish a banner for the whole company (*Organization → Announcements*), as information or
  a warning, optionally for a number of days. Each person can hide it.
- **Favorites and recently viewed:** the ☆ next to the title of an agent, Digital employee, project, task, skill or
  plugin adds it to *Favorites* at the top of the sidebar; the home page lists what was viewed recently. Both live in
  the browser only.
- **Live:** what the Digital employees are doing now — tasks in progress (and whether an external worker has them) and
  a timeline of runs, tools, authority checks, decisions, results and errors — only for tasks the person can read. It
  updates every few seconds while the page is open.
- **Phones:** the portal and the console work at phone width; wide tables and tab bars scroll inside their box.
- **`hangar test-drive`:** the whole path against your installation with the test model — see [cli.md](cli.md).

## API

| Method | Path | Description |
|---|---|---|
| GET / POST | `/api/goals` | List / create (`title, team, parent_id, owner, status, target_date, description`) |
| PATCH / DELETE | `/api/goals/{id}` | Edit / delete (no child goals) |
| GET / POST | `/api/projects` | List (`?archived=true`) / create (`name, team, goal_id, instructions, default_employee`) |
| GET / PATCH | `/api/projects/{slug}` | Detail with the goal chain and tasks / edit (`status: archived` archives) |
| GET / POST | `/api/tasks/{id}/messages` | The conversation / write (queued while running; reopens a finished task) |
| PATCH / DELETE | `/api/tasks/{id}/messages/{mid}` | Edit / discard a queued message (its author) |
| GET / POST | `/api/budgets` | Policies and incidents / create (`scope_type, scope, amount_usd, window, warn_percent, hard_stop`) |
| PATCH / DELETE | `/api/budgets/{id}` | Change / remove a policy |
| POST | `/api/budgets/incidents/{id}/release` | Release the rest of the window (`note`) |
| GET | `/api/inbox` | `?view=all|unread|decisions|blocked|mine|archived` |
| POST | `/api/inbox/mark` | `{keys, read, archived}` |
| GET | `/api/search` | `?q=&kinds=agent,employee,task,project,goal,plugin,skill` |
| GET | `/api/org-chart` | `?team=`: the Digital employees and who each reports to |
| GET / PUT | `/api/skills`, `/api/skills/{name}` | Skill Studio list / detail; save a new version (`files: [{path, content}]` replaces the extra files) |
| POST | `/api/skills/upload` | `{files: [{name, data (base64)}], check_only}`: `.md`/`.zip`/`.skill` with the security check; each file becomes a skill or a new version |
| GET | `/api/skills/{name}/download?version=` | The package as a `.zip` (`<name>/SKILL.md` + files) |
| PUT | `/api/skills/{name}/cases` | Saved test cases |
| POST | `/api/skills/{name}/test` | `{connection}`: run the cases (mock without a connection) |
| POST | `/api/skills/{name}/restore/{v}` · `/status` | Restore a version · `{status: active|deprecated}` |
| GET | `/api/packages/catalog` | Ready-made teams |
| POST | `/api/packages/export` | `{team}`: the team's package (no secrets) |
| POST | `/api/packages/preview` · `/install` | `{package or catalog, team, manager}` |
| GET / PUT | `/api/secrets` | List (no values) / create or change a value (`name, team, value, description`) |
| DELETE | `/api/secrets/{id}` | Delete |
| POST | `/api/secrets/proposals` · `/proposals/{id}/decide` | Propose a value · `{approve}` |
| POST / DELETE | `/api/tasks/{id}/dependencies` | `{on}` / `/{on_id}`: add or remove a dependency |
| POST | `/api/tasks/{id}/hold` | `{on}`: pause or resume the task and the tasks it handed off |
| POST | `/api/tasks/{id}/continue` | `{confirm, note}`: continue a failed task (confirm when it may have side effects) |
| GET / POST | `/api/tasks/{id}/files` | List / upload (body = file, `?name=&note=`) |
| GET / DELETE | `/api/files/{id}` | Download (`?inline=1` for safe types) / delete one version |
| GET | `/api/files` | Deliverables library (`?kind=output|input&q=&project=&employee=`) |
| GET | `/api/employees/{slug}/revisions` | Job history |
| POST | `/api/employees/{slug}/revisions/{v}/restore` | Restore a version |
| GET / POST | `/api/orgs` | Companies (instance admins) / create (`name, slug, admin_email, admin_name, admin_username, admin_password, domains`) |
| PATCH | `/api/orgs/{id}` | `{name, domains, status: active|suspended}` |
| GET / PUT | `/api/onboarding` | Getting-started steps / `{hidden}` |
| GET / POST | `/api/announcements` | Active announcements (`?all=true` for admins) / publish (`text, level: info|warn, days`) |
| DELETE | `/api/announcements/{id}` | Remove |
| GET | `/api/live` | `?since=<last event id>`: recent events and tasks in progress |
| PUT | `/api/employees/{slug}/runtime` | `{runtime: hangar|external, rotate}`: the external worker token (shown once) |
| POST / DELETE | `/api/me/notifications/chat-link` | `{channel: telegram|discord}`: one-time link code / `/{channel}`: unlink |

`POST /api/employees/{slug}/tasks` also takes `project`, `review`, `reviewer` and `blocked_by`.
