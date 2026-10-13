# Issues (kanban board)

The board for the company's work, for people and Digital employees together, in the portal (`/app` → **Issues**,
**Minhas issues**) and in the console (`/ui` → **Issues**). It works like [Multica](https://github.com/multica-ai/multica)'s
board. The difference is governance: the work an agent does on an issue goes through the same authority, budget,
shadow mode and audit as any task.

## The issue

An issue is the persistent record of a piece of work. Its key looks like `HGR-123`; the admin can change the prefix.

| Field | What it is |
|---|---|
| Title and description | Markdown: the goal, context and acceptance criteria |
| Status | One of the board's columns; see below |
| Priority | Urgent, high, medium, low or none |
| Assignee | A person or a Digital employee |
| Team | Who sees the issue: the team, plus admins and auditors. Without a team, the whole company sees it |
| Project | It goes into the agent's context, along with the project's goals |
| Labels | Colored labels, shared across the company |
| Start, due date, estimate | For planning and the timeline; the due date also reaches the agent |
| Parent issue and stage | An issue can have sub-issues, numbered in stages: 1, 2, 3… |
| Relations | Blocks / blocked by, related, duplicate |
| Subscribers | They see the issue's activity in their inbox |

**Statuses** belong to four categories, and the category decides the behavior:

| Category | Built-in statuses |
|---|---|
| Not started | `backlog`, `todo` |
| In progress | `in_progress`, `in_review`, `blocked` |
| Done | `done` |
| Closed | `cancelled` |

Admins create more statuses in **Issues → ⚙ Configure the board**, each in one category, and can archive them. The
built-in statuses don't change.

## Digital employees on the board

- **Assigning starts the work.**
  - When you assign an issue to a Digital employee outside the backlog, it starts a **run**: a task with the issue as context.
  - The **Start now / Don't start yet** dialog lets you only assign, and *Ask to work now* starts the run later.
  - In the backlog, assigning doesn't start anything; moving the issue out of the backlog does.
- **Mentioning starts work too.** `@Name` in a comment starts a run for that Digital employee with the comment as the request, without changing the assignee. Mentioning a person subscribes them and notifies them.
- **Talking to the run.** A comment from a person on an issue whose run is in progress goes into the run's conversation. If no run is active, it starts a new one.
- **During the run**, the agent:
  - comments on its progress (`issue_comment`);
  - moves the issue to `in_progress` or `blocked` (`issue_set_status`);
  - splits the work into sub-issues (`create_sub_issue`).

  It can't move the issue to done or cancelled: whoever reviews decides.
- **When the run ends:**
  - The result becomes a comment from the agent, and the issue moves to **In review**.
  - If the run fails, the error becomes a comment and the issue goes back to **Todo**.
  - A run that's waiting for a decision shows **decision** on the card.
- **Delegating to another Digital employee** (a sub-issue assigned to a colleague) is the `delegate` action and goes through the authority rules. An intern delegates with a notice, and the company floor applies. The new run's requester is the person who asked for the original run, so it never has more access than that person.
- **Sub-issues in stages:** when every sub-issue in the earliest unfinished stage is done or cancelled, the parent issue is notified. If a Digital employee owns the parent, it wakes up to start the next stage.
- **A run is not the issue.** An issue can have several runs (the assignee's, mentions, follow-ups). The issue's status says whether the work is finished. The runs, with their cost, are on the issue page.
- **Routines that open issues:** a Digital employee's routine with `creates_issue: true` opens an issue on the board each time it fires, assigned to that employee.

## Views

| View | What it shows |
|---|---|
| **Board** | Columns by status. Drag between columns to change the status, and within a column to reorder. `+` creates an issue in that column |
| **List** | Grouped by status |
| **Table** | Sortable by key, status, priority, due date or last update |
| **Swimlanes** | A board per assignee, priority or project. Dragging between lanes changes that field |
| **Timeline** | Bars from start to due date, colored by status, with today marked |

All views share the same **filters**: team, project, assignee, priority, label, and a search over the title, description
or key. The board refreshes on its own every few seconds, without interrupting anyone who's dragging or typing.

**Shortcuts:**
- `C` creates an issue and `/` searches.
- In the new-issue window, `Ctrl+Enter` creates, and **Create another** keeps the window open.
- In a comment, `Ctrl+Enter` sends.

## The issue page

- **Editable properties** in the sidebar: status, priority, assignee, labels (with *New label…*), project, team, start and due dates, estimate, stage, parent issue, relations.
- **Sub-issues**, created inline with a stage and an assignee.
- **Activity:** a single timeline of comments and changes.
  - Comments are in markdown. Replies thread under the first comment of a discussion, and a discussion can be marked resolved; replying reopens it.
  - You can react with 👍 ❤️ 🎉 👀 🚀 😕.
  - The author (or an admin) edits and deletes a comment. An agent's comment can be deleted but not edited.
  - Deleting a comment that has replies leaves "Comentário apagado." in its place.
  - Typing `@` suggests people and Digital employees.
- **Subscription** to the issue, and the list of **runs** with status and cost.
- **Deleting** is for whoever created the issue, a team maintainer, or the admin. It takes the comments and history with it and cancels running runs. To keep the history, set the status to *Cancelled* instead.

Issues also appear in the **inbox** (activity from others on issues you follow) and in the **search** (`Ctrl+K`).

## From your AI (platform MCP)

| Tool | What it does |
|---|---|
| `list_issues` | Filters by assignee (`me`, `none`, `employee:<slug>`), status, team, project, search |
| `get_issue` | The issue with sub-issues, relations, runs and timeline |
| `create_issue` | Creates an issue; `assignee: employee:<slug>` puts a Digital employee to work |
| `update_issue` | Changes status, priority, assignee, title, description, labels, due date |
| `comment_issue` | Comments; `@[Name](employee:<slug>)` starts a run |

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/issues` | Filters `team`, `project`, `assignee`, `status`, `priority`, `label`, `q`, `parent` |
| POST | `/api/issues` | `{title, description, team, project, status, priority, assignee, labels, parent, stage, start_date, due_date, estimate, start}` |
| GET / PATCH / DELETE | `/api/issues/{key}` | Read (marks it read in the inbox), change any field (`start: false` = don't start yet), delete |
| POST | `/api/issues/{key}/run` | Starts (or nudges) the assignee Digital employee's run |
| POST | `/api/issues/{key}/comments` | `{body, parent_id}`; mentions `@[Name](employee:<slug>)` and `@[Name](user:<id>)` |
| PATCH / DELETE | `/api/issues/{key}/comments/{id}` | Edit / delete |
| POST | `/api/issues/{key}/comments/{id}/resolve` | `{resolved}` |
| POST | `/api/issues/{key}/comments/{id}/react` | `{emoji}` (toggles) |
| POST / DELETE | `/api/issues/{key}/relations[/{id}]` | `{kind: blocks\|blocked_by\|related\|duplicate, other}` |
| POST | `/api/issues/{key}/subscription` | `{subscribed}` |
| GET | `/api/issues/assignees?team=` | People and Digital employees that can receive an issue (and be mentioned) |
| GET | `/api/issues/settings` | Prefix, statuses, labels |
| PUT | `/api/issues/settings/prefix` | `{prefix}` (admin) |
| POST / PATCH | `/api/issues/statuses[/{key}]` | Create (`{name, category, color}`) / change or archive (admin) |
| POST / DELETE | `/api/issues/labels[/{id}]` | Create (any member) / delete (admin) |
