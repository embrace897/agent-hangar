# Day-to-day work

How people and Digital employees work together day to day: goals and projects give each task its *why*, the task
has a conversation, budgets stop runaway spend, and the inbox and search show what needs you.

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

`POST /api/employees/{slug}/tasks` also takes `project`.
