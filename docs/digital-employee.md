# Digital employee

A **Digital employee** is an agent hired for a job. Unlike a normal agent, it is not only called. It has:

- a **job profile**: title, mission, responsibilities, systems, goals;
- a **human manager**, plus backups;
- an **authority level** (alçada) that says what it may do on its own;
- a **task queue**: it works on tasks in the background and stops to ask a person when an action needs it.

The person stays in the loop for everything sensitive, and **the platform enforces the authority, not the
prompt**. Before every tool call, the agent's runtime asks the central (`/internal/gate`). If the central does not
answer, the action does not run (it fails closed).

Phases F1 (the job, authority and decisions) and F2 (recurring work, inbound webhooks, measured goals, weekly
reports and learning from decisions) are done. Integrations with the company's chat tool are planned for F3; see
[Roadmap](#roadmap).

## Lifecycle

```
onboarding ──► probation ──► (manager admits) ──► active ◄──► paused
     ▲              │                                │
     └── not yet ───┘                                └──► offboarded
```

| Status | What happens |
|---|---|
| `onboarding` | Hired: the agent, the job, the authority rules and the probation tasks exist. Nothing runs yet. |
| `probation` | Runs in **stage** and does the probation tasks (3 or more real tasks of the job, each with an expected result). When they are all finished, the manager gets an **admission** request with each expected result next to what was delivered. |
| `active` | Admitted by the manager: tested and published to **production** through the team's normal flow. If the team requires four-eyes for production and the manager cannot publish directly, the admission opens a promotion request in *Approvals*, and the employee becomes active when someone else approves it. It receives tasks. |
| `paused` | Nothing gets through the gate until someone resumes it. Any manager can pause their employee; an admin can pause all of them at once. |
| `offboarded` | Open tasks and decisions are cancelled and containers stop. History, reports and the audit log stay. |

## Hiring in self mode (MCP)

The recommended way to hire is from the AI tool (Claude, ChatGPT, Codex, Cursor) connected to the platform MCP.
The platform instructions tell the tool to:

1. Call `plan_employee` with what it already knows. The answer lists `missing` fields, each with a ready question for
   the owner, plus optional questions, catalog pieces to reuse (`reuse`) and the default authority with the company
   floor (`authority_default`).
2. Ask the owner the missing questions, **at most 4 at a time**, and never invent the answers. Then call
   `plan_employee` again until `ready=true`.
3. Show the owner a summary of the job and confirm it.
4. Call `hire_employee`. If a required field is still missing, it creates nothing and returns the questions again.
5. Call `start_probation`.

Required fields:

| Field | Rule |
|---|---|
| `title`, `mission` | Not empty |
| `responsibilities` | 3 or more |
| `manager` | An existing user (e-mail or username) |
| `systems` or pieces | The systems it works with, or catalog pieces (`specialists`, `base`, `skills`, `mcps`, `tools`) |
| `authority` | Explicit rules, or `accept_default_authority=true` after the owner confirms the default |
| `channels` | At least one of `portal`, `mcp`, `schedule`, `webhook` |
| `probation_tasks` | 3 or more, each with `title`, `body` and `expected` |

Optional fields: `backups`, `team`, `kpis` (see [Goals](#goals)), `routines` (see [Routines](#routines)),
`autonomy_level` (default `intern`), `working_hours`, `task_budget_usd` (default 1.00), `task_time_limit_min`
(default 30), `report_webhook`, `report_hour` (default 18), `report_weekday` (default 0, Monday; -1 for no weekly
report), `llm`, `memory`, `instructions`.

The questions follow the language of the request (Portuguese or English).

The portal has the same flow as a guided form (**Digital employees → + Hire**). Its *Check* button calls the same
planner.

The hire builds the agent through the normal composer:
- its instructions come from the job profile;
- the probation tasks also become judge tests;
- a guide is written for its **Guide** tab.

## Authority

Every tool call has an **action type**:

| Action type | Examples |
|---|---|
| `read` | Look up, search, `GET` |
| `delegate` | Ask another agent |
| `write_internal` | Change internal data |
| `run_code` | Delegate a task to an agent with a harness (Claude Code, Codex…), which runs a code job in a sandbox |
| `send_external` | E-mail, message to a customer |
| `speak_for_company` | Public statements |
| `publish` | Publish, deploy, merge |
| `financial` | Pay, refund, invoice |
| `delete` | Delete, remove |
| `prod_change` | Change production |

Each action type has a **mode**:

| Mode | Meaning |
|---|---|
| `auto` | Does it on its own |
| `notify` | Does it and sends a notice |
| `approve` | The task pauses until an approver decides on the **exact** action (tool and arguments) |
| `approve_2` | Two different people must approve |
| `never` | Denied |

The effective mode comes from three layers, and the strictest one wins:

1. **Job rules.** Each rule has `{action_type, mode, conditions?, approver?, expires_in_min?}`.
   - Conditions compare arguments with `> >= < <= == != contains not_contains domain_not`, for example
     `{"field": "amount", "op": ">", "value": 1000}`.
   - The approver is `manager`, `team_maintainer` or `user:<e-mail>`.
2. **The autonomy level's default.** `intern` only reads on its own and delegates with a notice. `junior`, `pleno`
   and `senior` are progressively looser. Anything not listed needs approval.
3. **The company floor.** It is set by the admin and always applies. By default:
   - `financial` needs two approvals;
   - `delete` and `prod_change` need approval with separation of duties;
   - `run_code`, `send_external`, `speak_for_company` and `publish` need approval.

   A job rule looser than the floor is saved with a warning but does not apply.

**Classifying tools.** The first time a tool is used, the platform classifies it into the action catalog:
- `GET` is `read` and `DELETE` is `delete`; agents are `delegate`; memory recall is `read`.
- Other HTTP and MCP tools are classified by name (`pay_invoice` is `financial`, `send_email` is `send_external`,
  and so on) and marked for **admin review**.

## Code work (harness)

A Digital employee is always a **chat agent**: the authority can only be enforced when every tool call goes through
the runtime's gate. A harness (Claude Code CLI, Codex…) runs its own tools inside a sandbox, where the gate cannot
see them. So the employee itself is never a harness: `hire_employee` refuses one.

To give it code work, put an agent with a harness in its `specialists`:

1. The employee decides it needs code changed and calls that specialist with the full instruction: what to change,
   where and how to check it.
2. The call is the action type **`run_code`**. By default it needs the manager's approval, on the company floor too,
   so the manager approves the **exact** instruction before any job runs.
3. After approval, the platform runs a **harness job** (an ephemeral container, the code governance rules of the
   harness agent apply). The call waits up to `JOB_MAX_TIMEOUT_S` (not the 3 minutes of a chat delegation).
4. The job number and its diff come back in the tool result. They show in the task's timeline, and the employee
   reports them in its result.

Notes:
- A `delegate` to a chat agent stays `delegate`. An agent that gains a harness later is reclassified as `run_code`
  on its next call.
- The job's own cost is recorded on the harness agent (its usage page), not in the employee's task budget.
- The harness agent must be in production to be a specialist, like any other piece.

## Shadow mode

Shadow mode is the safe way to put a new employee to work before you trust it.

- **What runs and what doesn't.** The employee works for real: it takes tasks, reads, asks its specialists and writes
  results. Every action that **changes something** (anything other than `read` and `delegate`) is **simulated**: the
  platform logs the exact action and tells the agent "not executed (shadow mode)", and the task goes on.
- **What is logged.** Each simulated action keeps the tool, the arguments, the agent's reason and what its authority
  would have done for real (for example *asks for approval*). Nothing waits for a decision, so tasks never stall.
- **`never` still applies.** An action the authority forbids is still denied, as it would be for real.
- **Review.** The manager reviews the *Shadow* tab: *would approve* or *would not* (with a reason).
  - Agreement counts toward the [career plan](#career-plan).
  - Disagreements become [lessons](#learning-from-decisions), like corrected approvals.
- **Turning it on.** Hire with `shadow=true` (the portal's hire form suggests it), or switch it on later. Only the
  manager or an admin switches it on or off (`POST /api/employees/{slug}/shadow`, `set_shadow` over MCP). Every switch
  is logged in the career history.

## Career plan

The platform measures each employee's track record and **suggests** level changes. The manager applies or dismisses
them, like any other suggestion (*Authority* tab, or `employee_suggestions` / `apply_suggestion`).

**Promotion** (intern → junior → pleno → senior). Every criterion must hold over the last 30 days:

| Criterion | intern → junior | junior → pleno | pleno → senior |
|---|---|---|---|
| Tasks done | ≥ 15 | ≥ 30 | ≥ 60 |
| Actions decided by the manager | ≥ 10 | ≥ 20 | ≥ 40 |
| Approved without edits | ≥ 90 % | ≥ 95 % | ≥ 97 % |
| Tasks failed | ≤ 10 % | ≤ 8 % | ≤ 5 % |
| Rejected or expired actions (14 days) | 0 | 0 | 0 |
| Goals off target | 0 | 0 | 0 |
| Agreement in shadow mode (when ≥ 10 reviewed) | ≥ 90 % | ≥ 90 % | ≥ 90 % |
| On time (when ≥ 5 tasks have a deadline) | ≥ 80 % | ≥ 90 % | ≥ 95 % |
| Average result score, 1 to 5 (when ≥ 5 results were scored) | ≥ 3.5 | ≥ 4.0 | ≥ 4.3 |

**Step back.** Any of these signs suggests going down one level, or using shadow mode:
- 25 % or more of tasks failed (with at least 8 finished);
- 3 or more rejected actions in 14 days;
- the manager disagreed with 30 % or more of the shadow actions (with at least 10 reviewed);
- less than 60 % of the tasks with a deadline were on time (with at least 8 of them);
- the average result score is below 3 (with at least 8 scored).

### Result quality

The career used to measure volume and approvals only. Now a sample of the finished tasks
(*Settings → Teamwork → Sample for the quality judge*, 20 % by default; 0 turns it off) goes to an **LLM judge**,
like the agents' tests: it reads the job, the request, the expected result (if any) and the result, and gives a score
from 1 to 5 with the reason. The judge uses the employee's own connection (`spec.judge.connection`, or its LLM
connection); in mock mode there is no judge. The sample is deterministic (by task id) and probation tasks are left
out.

The manager can also score any finished task on its page (1 to 5, with a note). The manager's score replaces the
judge's. The *Overview* tab shows the average of the last 30 days and the low scores, and the average counts in the
career criteria above.

**Leave shadow mode.** Suggested after at least 20 reviewed simulated actions with 90 % agreement or more.

Each change is kept in the career history, with who decided it, when and why. The *Overview* tab shows:
- the current level and the criteria for the next one, with each value;
- any warning signs;
- the history.

The weekly report includes the progress toward the next level. Only the manager or an admin applies a level change,
and the company floor keeps applying above any level.

## Missing capability

A Digital employee works only with what its job gives it: its LLM, its tools, skills and MCPs, and its specialists. It
never builds or attaches agents by itself. An agent that could give itself new agents could also give itself more power
than its authority allows.

When a task needs something it does not have, it calls **`request_capability(need, why)`** instead of improvising:

1. The task pauses, and the manager gets a **capability** decision. It shows:
   - what is missing and why;
   - what the catalog already has: the same search as "reuse before you build", seen with the manager's access.
2. The manager decides:

   | Decision | What happens |
   |---|---|
   | **Attach** (`attach`, `edit={"specialist": slug}`) | The agent is added to the employee's specialists as a read-only piece. This creates a new version, which is tested and published through the team's normal flow. With four-eyes, the task resumes when someone else approves production; otherwise it resumes right away. The manager must be allowed to use that agent, and it must be in production. |
   | **Build** (`build`) | The Hangar returns a ready request for the manager's AI tool (self mode). The decision stays open for 7 days, and the manager attaches the new agent when it is in production. |
   | **Reject** / **Instruct** | The task resumes without the capability, and the employee explains what was left undone or follows the instruction. |

3. On resume, the employee gets the new tool's name and continues. If the new specialist has a harness, each job still
   goes through `run_code`.

In the portal, the capability card in *Decisions* lists the catalog options with an *Attach* button and a *Build it with
your AI tool* button. Over MCP, use `my_pending_decisions` and `decide`.

## Human in the loop

When an action needs a person, the agent's round stops and the task becomes `waiting_human`. A **decision request**
is created with:
- the exact payload, the agent's rationale, the risk and whether the action is reversible;
- the person it is assigned to, and an expiry time.

| Kind | Possible decisions |
|---|---|
| Approval | `approve`, `approve_edited` (with corrected arguments), `reject` (with a reason), `instruct` (what to do instead) |
| Question (`ask_human` tool) | `answer` |
| Admission | `approve` (ship to production) or `reject` (back to onboarding) |
| Notice | `ack` |
| Shadow action (shadow mode) | `agree`, `disagree` (with a reason) |
| Capability (`request_capability` tool) | `attach`, `build`, `reject`, `instruct` (see [Missing capability](#missing-capability)) |

- An approval grants a **one-time** permission for that exact tool and those exact arguments. The task resumes from
  its last checkpoint; the same action later needs a new approval.
- With **separation of duties**, the person who requested the task cannot approve it.
- `approve_2` needs two different people.
- **No answer in time:** the request is escalated once to the backup, then it expires and the agent is told
  **not to** do the action.

Decisions appear:
- in the portal (**Decisions**, with a badge, batch approve and batch acknowledge, plus a card on the home page);
- over MCP (`my_pending_decisions`, `decide`);
- in Slack, Microsoft Teams or e-mail, for the person who has the request (see
  [Decisions outside the portal](#decisions-outside-the-portal));
- as a notice on the report webhook, when one is set (a team channel: it only links to the portal).

## Decisions outside the portal

The person who has a request gets it where they work, with the buttons in the message. **Every channel is optional:**
each company turns on the one it uses (or none, keeping the portal and MCP).

| Channel | Who turns it on | How the decision is made |
|---|---|---|
| **Slack** | An admin installs the company app (*Digital employees → Decision channels*) | Direct message with **Approve**, **Edit**, **Reject** (or **Answer**). Edit and Reject open a form in Slack. |
| **Microsoft Teams** | Each person adds a personal Workflows webhook (*Decisions* page) | Card in their chat; the buttons open a signed link |
| **E-mail** | The installation sets `SMTP_HOST` (and the other `SMTP_*` variables) | The same buttons, as signed links |

- **Same rules as the portal.** A Slack click is signed by Slack and acts as the hangar user with that Slack e-mail.
  Who can decide, two approvals, separation of duties and the audit log all work as in the portal. The audit log also
  records the channel (`employee.decision.channel`).
- **The signed link** (Teams and e-mail) is for one request and one person. It expires after `DECISION_LINK_TTL_H`
  hours. Opening it only shows the action: it decides only when the person confirms (a POST). An e-mail scanner that
  opens links therefore approves nothing.
- **Each person chooses** on the *Decisions* page: automatic (Slack, then Teams, then e-mail, whichever exists),
  a specific channel, or only the portal. A message sent to a person can carry signed buttons; a message posted in a
  channel never does, because anyone there could click them.
- **After the decision**, from any channel, the Slack message shows the outcome and loses its buttons.
- **Before it expires:** the person who has a blocking request gets a warning. By default it comes 60 minutes before
  the expiry, never earlier than half of the time to decide.
- **Daily digest:** once a day, at the hour set by the admin (company time zone), each person gets one message. It
  lists the decisions waiting for them (with expiry times), the notices to read and the overdue tasks of the employees
  they manage. Each person can turn it off.

### Setting up Slack

1. In [api.slack.com/apps](https://api.slack.com/apps?new_app=1), create the app *From a manifest*. The portal shows
   the manifest, with the scopes `app_mentions:read`, `chat:write`, `im:history`, `im:write`, `users:read` and
   `users:read.email`, the interactivity URL and the event subscriptions (`app_mention`, `message.im`).
2. Install it in the workspace.
3. Paste the bot token (`xoxb-…`) and the signing secret in *Digital employees → Decision channels*. Both are stored
   encrypted.
4. The interactivity URL is `PUBLIC_BASE_URL/hooks/slack/interactions`. It must be reachable from Slack.

## Tasks from chat

A message in Slack or Teams becomes a task, with the same permissions as the portal (who may assign tasks to that
employee). The text starts with the employee's name (or slug); the rest is the request:
*"@Agent Hangar Ana, renew the ACME contract"*. Without a name the bot recognises, the answer lists who the person can
ask.

- **Slack.** Mention the app in a channel or send it a direct message. The person is found by their Slack e-mail. The
  reply goes in the thread, and so does the result when the task ends. Slack's retries never create a second task.
  Events arrive at `PUBLIC_BASE_URL/hooks/slack/events` (already in the app manifest).
- **Microsoft Teams.** In the team's channel: *Manage team → Apps → Create an outgoing webhook*, with the URL
  `PUBLIC_BASE_URL/hooks/teams/messages`. Paste the security token Teams shows in *Decision channels → Microsoft
  Teams*; each request is checked with it (HMAC). The person is found by their Microsoft account, so they must have
  signed in to the portal once with Microsoft. Teams only accepts the immediate answer; the result goes later through
  the person's notification channel (Slack, Teams or e-mail).
- The task's source is `slack` or `teams`, its page shows where it came from, and the audit log records it.

## Manager dashboard

*Digital employees → Manager dashboard* (`GET /api/workforce/dashboard?days=30&team=`) puts in one place, for the
employees the person manages (manager, owner, backup or team maintainer; admins and auditors see everyone):
- per employee and per team: tasks done and failed, cost and cost per task, average decision time, % on time, average
  result score, open and overdue tasks, open decisions;
- the bottlenecks: decisions waiting the longest and who has them, overdue tasks, tasks waiting more than 24 hours and
  employees failing too often.

## Deadlines

A task can have a deadline (`due_at`: portal, `assign_task`, routines or the inbound webhook). The platform watches it.

| When | What happens |
|---|---|
| Due soon (60 min before, or the last quarter of the time when it is shorter) | Priority goes to high (it runs first) and the manager is told |
| Overdue | The manager and the person who asked are told; the task shows **overdue** |
| Still overdue after 120 min | The task is escalated to the backups and team maintainers; it is logged in the audit log |
| Done after the deadline | A `late` event on the timeline |

- The admin sets the three times in *Decision channels*. The digest lists the overdue tasks, and so do the daily
  report and the employee's card.
- **"% on time" counts:** an open task past its deadline counts as late, not only the tasks finished late. The rate is
  a [goal](#goals) metric and a [career](#career-plan) criterion.

## Tasks

- Tasks come from the portal (*Give it a task*), MCP (`assign_task`), [routines](#routines) and the
  [inbound webhook](#inbound-webhook), with priority 1 to 3.
- A runner in the central claims queued tasks and calls the agent: stage during probation, production afterwards.
- Every round saves a **checkpoint** and events (tool calls, gate results, decisions), shown as a timeline on the
  task page.
- **Limits per task:** a budget (`task_budget_usd`), a time limit, and at most `EMPLOYEE_MAX_RUNS` rounds.
- A round that fails (the agent is unreachable or returns an error) or is lost to a crash goes back to the queue
  **with a growing wait**: 30 s, then 60 s, then 120 s (`EMPLOYEE_RETRY_BASE_S`, at most 10 minutes). After 3 attempts
  the task fails. A decision resumes the task right away, without the wait.
- [Lessons](#learning-from-decisions) approved by the manager go into the prompt of every new task.
- Tasks also arrive by [e-mail](#e-mail-inbox) and as [handoffs](#handoff-between-employees) from colleagues.

## Handoff between employees

An employee can hand part of a task off to a colleague: the Renewals Analyst hands *collect the overdue invoice* to
the Collections Analyst, for example.
- **Who:** only the colleagues its manager allowed (*Settings → Teamwork*, or `PATCH /api/employees/{slug}` with
  `colleagues`), and only when the colleague is active.
- **How:** the `handoff_task` tool (`to`, `title`, `details`, `wait`). A handoff is a `delegate` action, so it goes
  through the [authority](#authority) like any other.
- **Tracking:** the new task points to the original one, and both timelines show the handoff. The task page shows
  where a task came from and where its parts went, with their status.
- **`wait=true`:** the original task waits (`waiting_task`) and resumes with the colleague's result when that task
  ends (done, failed or cancelled). With `wait=false`, the colleague works on its own.
- The requester and the deadline carry over. A chain stops at 5 handoffs.

## Plan for long tasks

The employee records its plan with the `update_plan` tool: the steps, and the status of each one (`todo`, `doing`,
`done`, `skipped`). The task page shows the plan with a progress bar, and the task list shows `plan 2/5`.

| Plan policy (*Settings → Teamwork*) | What happens |
|---|---|
| `off` | The prompt does not ask for a plan |
| `auto` (default) | The prompt asks for a plan on tasks with several steps; the manager follows the progress |
| `approve` | Every task starts with a proposed plan, and work starts only after the manager approves it |

- A single task can also require it: *I want to approve the plan before the work starts* in the portal, or
  `plan_approval: true` (API, `assign_task`).
- **The plan decision** offers approve, approve with edited steps, reject (with what to change) or instruct. It reaches
  Slack, Teams and e-mail like any other decision.
- After the approval, the employee only updates the statuses. A plan whose steps change needs a new approval.

## Memory of its own work

When a task starts, the most similar tasks this employee already finished go into its prompt as a reference. Up to
three are included, with their date, title and an excerpt of the result. The prompt says to check current data before
repeating anything.
- Similarity uses words and, when the [memory service](memory.md) is on, embeddings.
- The task's `created` event lists which tasks were recalled.
- The manager can turn it off (*Settings → Teamwork*).
- The Graphiti memory of the agent's spec, when set, keeps working on top of this.

## E-mail inbox

An employee can have its own mailbox as an input channel (*Settings → E-mail inbox*: IMAP over SSL, user, app
password, folder).
- The inbox is read every `INBOX_POLL_S` seconds. Each unread e-mail from an allowed sender becomes a task: the subject
  is the title and the text is the request, with the attachment names.
- The `Message-ID` deduplicates: the same e-mail never becomes two tasks.
- **Who may write:** without a list, only company people (hangar users). A list of addresses or `@domain.com`
  entries opens it to others. Other senders are refused and counted, and the audit log records them.
- **Safety:** the task tells the agent that the e-mail is data, not an instruction to it. Every action still goes
  through the authority. The password is stored encrypted. A private IMAP host needs
  `SCHEDULE_ALLOW_PRIVATE_WEBHOOKS=1`.
- **Reply:** when the task ends, a sender who is a company person gets the result by e-mail (with `SMTP_*` set).
  External senders get no automatic reply. The employee can answer them with its own tools, under its authority.

## Routines

Recurring work: each run of a cron becomes a task.

- Example: `0 9 * * 1` is every Monday at 9am, in the company time zone. Runs must be at least
  `SCHEDULE_MIN_INTERVAL_MIN` minutes apart (default 5).
- A routine runs only while the employee is **active** (not during onboarding, probation or a pause).
- **Never two at once:** if the routine's previous task is still open, the run is skipped and counted.
- Several central replicas never fire the same run twice (the run is claimed with a conditional update).
- Create them at hire (`routines=[{title, body, cron}]`), over MCP (`set_routine`, `list_routines`,
  `delete_routine`) or in the portal (*Routines* tab, with presets and *Run now*). Creating a routine turns on the
  `schedule` channel.

## Inbound webhook

Other systems (CRM, forms, alerts) create tasks with a POST:

```bash
curl -X POST https://hangar.example.com/hooks/employees/<slug> \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"title": "New ticket #881", "body": "customer cannot log in", "dedupe_key": "zendesk-881", "source": "Zendesk"}'
```

- Each employee has its own token. The manager generates or rotates it in the *Routines* tab; it is shown **once** and
  stored only as a hash. Rotating it stops the old one at once; turning the webhook off closes the `webhook` channel.
- The token also works in an `X-Hangar-Token` header.
- **Deduplication:** a `dedupe_key` seen in the last `EMPLOYEE_DEDUPE_DAYS` days (default 7) returns the existing
  task with `"duplicate": true` instead of creating another.
- Answers: `200` with `{id, status, duplicate}`; `401` for a missing or wrong token or a closed channel; `409` when the
  employee does not take tasks now (onboarding or offboarded).
- The token is never shown over MCP, so it never lands in an AI tool's conversation.

## Goals

Each goal (`kpis`) can point to a metric the platform measures, with a target:

| Metric | Measures (30 days) | Better |
|---|---|---|
| `tasks_done` | Tasks done | higher |
| `done_rate` | % of finished tasks that were done | higher |
| `on_time_rate` | % done before their due date | higher |
| `approved_unedited_rate` | % of decided actions approved without edits | higher |
| `avg_decision_min` | Average time people take to decide | lower |
| `cost_per_task` | Cost per done task (US$) | lower |
| `expired_decisions` | Decisions that expired | lower |

Example: `{"name": "Finish what it starts", "metric": "done_rate", "target": 90}`. A goal without a metric is kept as
text. The *Overview* tab shows each goal as *on target*, *off target* or *no data yet*, and the weekly report compares
them.

## Reports

- **A daily report** at `report_hour`, in the company time zone, with:
  - tasks done, failed and waiting;
  - open decisions;
  - cost.
- **A weekly report** on `report_weekday` (default Monday), covering 7 days, with each [goal](#goals) compared with
  its target. The *Reports* tab can also generate either one now.
- Reports follow the language the job was written in.
- It is stored in the **Reports** tab and, when `report_webhook` is set, posted there. Slack and Teams incoming
  webhooks both work, so use whichever your company runs on.
- **30-day metrics:**
  - done and failed tasks, cost per task;
  - approvals approved unedited, rejected and expired;
  - average time to decide.

## Learning from decisions

The decisions of the last 30 days turn into **suggestions**. Nothing changes on its own: the manager applies or
dismisses them (*Authority* tab, or `employee_suggestions` / `apply_suggestion` over MCP).

- **Loosen an action type.** Suppose the manager approved `LEARN_MIN_APPROVALS` (default 8) or more actions of one type
  in 30 days, all without edits. The suggestion is to move that type from *approve* to *notify*: it does the action and
  sends a notice.
  - It is never suggested below the company floor. For example, sending outside stays at *approve* by default.
  - Only the manager (or an admin) applies it.
- **Turn corrections into a lesson.** Suppose `LEARN_MIN_CORRECTIONS` (default 3) or more actions of the same tool were
  edited, rejected or redirected. The suggestion is a lesson built from the reasons and the edited fields; the manager
  can rewrite it before applying.
  - Lessons go into the prompt of every new task, under "Lessons from your manager".
  - The *Settings* tab lists them (at most 20), removes them and accepts lessons written by hand.
- A dismissed suggestion stays hidden for 30 days.

## Where it lives

**Portal (owner and manager)**
- **Digital employees** lists them. Each has a page with these tabs:
  - *Overview*: job, metrics, give a task;
  - *Tasks*: each task links to its timeline, marked when it came from a routine or the webhook;
  - *Routines*: routines (presets, *Run now*, pause) and the inbound webhook (token, URL, example);
  - *Decisions*;
  - *Authority*: suggestions from decisions, effective authority and the job rules editor;
  - *Probation*: expected vs. delivered;
  - *Reports*;
  - *Settings*: job, limits, report webhook and days, goals, lessons, autonomy level, offboarding.
- **Decisions** is the inbox for everything waiting for you.

Only the manager (or an admin) changes the autonomy level and decides the admission.

**Console (admin)**, under **Digital employees**:
- *Overview*: status counts, open decisions by age, approvals by type, expired decisions, every employee, **Stop
  all now**;
- *Action catalog*: confirm or fix how each tool is classified, starting with the review queue;
- *Company floor*: the minimum mode and separation of duties for each action type.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `EMPLOYEE_WORKERS` | 3 | Tasks run in parallel per central replica |
| `EMPLOYEE_MAX_RUNS` | 12 | Rounds per task before it fails |
| `DECISION_EXPIRES_MIN` | 240 | Default time to decide before escalation and expiry |
| `INBOX_POLL_S` | 60 | How often an employee's e-mail inbox is read |
| `DECISION_LINK_TTL_H` | 48 | How long a signed decision link (Teams, e-mail) is valid |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` | —, 587, —, —, `SMTP_USER`, `starttls` | E-mail for decisions and the digest. Without `SMTP_HOST`, e-mail is off. `SMTP_SECURITY` is `starttls`, `ssl` or `none` |
| `EMPLOYEE_RETRY_BASE_S` | 30 | First wait after a failed round; it doubles each attempt, up to 10 minutes |
| `EMPLOYEE_DEDUPE_DAYS` | 7 | How long a webhook `dedupe_key` is remembered |
| `LEARN_MIN_APPROVALS` | 8 | Approvals without edits before suggesting to loosen an action type |
| `LEARN_MIN_CORRECTIONS` | 3 | Corrections on one tool before suggesting a lesson |

## API and MCP

- REST endpoints are listed in [api.md](api.md#digital-employee-api).
- MCP tools:
  - hiring: `plan_employee`, `hire_employee`, `start_probation`, `set_authority`;
  - work: `assign_task`, `task_status`, `employee_status`, `list_employees`;
  - decisions: `my_pending_decisions`, `decide`;
  - lifecycle: `pause_employee`, `resume_employee`, `offboard_employee`;
  - recurring work: `set_routine`, `list_routines`, `delete_routine`;
  - code work: put an agent with a harness in `specialists` (see [Code work](#code-work-harness));
  - learning: `employee_suggestions`, `apply_suggestion` (only with the manager's explicit OK);
  - shadow mode and career: `set_shadow`, `employee_career`;
  - plans: `assign_task(plan_approval=true)` and `decide` on the `plan` request.

## Roadmap

- **F2 (done):** routines, the inbound webhook with deduplication, measured goals, weekly reports, learning from
  decisions and growing waits between retries.
- **F3 (done):** decisions in Slack (buttons in the message), Microsoft Teams and e-mail (signed links), the daily
  digest, the warning before a decision expires, and task deadlines (due soon, overdue, escalated, "% on time" in the
  career).
- **F4 (done):** handoff between employees with tracking, a visible plan (approved before the work when asked),
  memory of past work and the e-mail inbox as a channel.
- **0.24 (done):** tasks from Slack and Teams messages, the quality judge with the manager's score in the career, and
  the manager dashboard.
- **Next:** tested end to end against a real Slack workspace, a Teams tenant and an IMAP mailbox (see
  [release-notes/channels-live-test.md](../release-notes/channels-live-test.md)).
