# The analyst's day to day

What a Digital employee needs to cover the recurring work of analysts in any area and at any level: data and
documents, numbers that must be right, long processes, shared queues, conversations with people outside the company,
meetings, the company's knowledge, systems without an API, data governance, judgement calls and proof of value.

Everything here runs **in the central**, as *platform tools* the employee calls like any other tool. The runtime
asks the central which ones are on for that employee (`/internal/platform-tools`) and registers them as
`platform:<name>`; the call goes through the gate, so **every action that changes something outside still passes
through the employee's authority**, with the same decisions, shadow mode and audit log. The manager turns tool groups
on or off in *Settings → Platform tools*.

## Data analysis and formatted deliverables

With the Data Studio running (`docker compose --profile data-studio up -d`), each task gets its own Data Studio
session (its own workspace and kernel, isolated by uid or by container):

| Tool | What it does |
|---|---|
| `analyze_data` `{code}` | Python (pandas, numpy, duckdb, matplotlib, openpyxl, python-pptx, python-docx…) over the task's files; state is kept between calls of the same task |
| `query_data` `{sql, save_as}` | DuckDB SQL over the files (each CSV/Parquet is a table, each Excel sheet is `<file>_<sheet>`); `save_as` writes the full result as a deliverable |
| `list_data_tables`, `inspect_data_file` `{name, ocr}` | What's in the files; `ocr=true` reads scanned PDFs and images (Portuguese and English) |
| `create_deliverable` `{kind, title, …}` | A PPTX (`slides`), DOCX (`markdown`, with `[[table: SELECT …]]`) or interactive HTML dashboard |

Before each call the task's files go into the workspace; afterwards, what the analysis produced comes back as a
versioned **deliverable of the task**, and the code goes on the timeline (event *Analysis*) so whoever reviews can
reproduce it. Presentations and dashboards use the company's **branding** (theme, accent colour, footer), set by
an admin in *Console → Digital employees → Deliverables*.

Tasks can be created with their files attached (`files: [{name, content_base64, content_type}]` on
`POST /api/employees/{slug}/tasks`): the task only enters the queue once the files are in.

## Document extraction with field review

An **extraction schema** (*Build → Document extraction*) lists the fields to take from a kind of document — name,
label, type (`text, number, money, date, cpf, cnpj, email, bool, enum`), required, a rule (regex) or options — and the
minimum confidence. Schemas are per company; admins and the developers of the owning team edit them.

`extract_fields {file, schema}` reads the file (text directly; PDF and images through the Data Studio's OCR), and
every field comes back with **value, confidence and the exact snippet it came from**. Values are normalised (dates to
ISO, Brazilian amounts to numbers, CPF/CNPJ with check digits). A field below the minimum confidence, failing its rule
or missing when required turns into a **field check** decision: the person sees only the doubtful fields first, next
to their snippets, corrects them and confirms — or rejects the document. The task waits and resumes with the checked
values (the corrections count in the employee's quality). The result is saved as `extracao-<file>.json`.

**Batches:** `POST /api/employees/{slug}/batch {title, schema, files}` (or *Document batch* on the same page) creates
one task per file and a consolidation task that waits for all of them and builds `<schema>-consolidado.csv`
(`export_extractions`), flagging documents still being checked.

## Certified calculators

Numbers that must be right — provisions, taxes, commissions, penalties, adjustments — come from a **calculator**
(*Build → Calculators*), not from the model's head:

- **Inputs**, **steps** (`name = formula`) and **outputs**. Formulas use a safe language: arithmetic in `Decimal`,
  comparisons, `a if condition else b`, lists, and `round(x, places)` (half up), `pct`, `min`, `max`, `abs`, `sum`,
  `len`, `floor`, `ceil`, `days_between`, `months_between`. No `eval`, imports or attributes; sizes and exponents are
  limited.
- **Test cases** (inputs → expected outputs, with tolerance) run on every save.
- **Certification needs four eyes:** whoever edited last can't certify, and every case must pass. Editing creates a
  new draft; the certified version keeps being used until the new one is certified.
- The employee calls `list_calculators` and `calculate {calculator, inputs}`. Each run keeps its **calculation
  record** (inputs, every step with its formula and value, version, who certified) in the task as
  `memoria-de-calculo-<name>.json` and in the calculator's history. A calculator can require **human review** of any
  task that used it.

## Business calendar

The company's working days, hours and holidays (*Console → Digital employees → Calendar*). Brazil's national holidays
— including Carnival, Good Friday and Corpus Christi — come built in; add state and city holidays, or mark national
ones the company works. Playbook deadlines count business days, and routines and playbooks accept business-day
schedules on top of cron:

- `@bd 1 09:00` — the 1st business day of each month at 9:00; `@bd -1 17:00` — the last one.
- `@bdaily 08:30` — every business day.

## Playbooks

A **playbook** (*Digital employees → Playbooks*) is a recurring process — month-end closing, payroll, customer
onboarding, audit — as steps. Each step has an owner (a Digital employee, a person, or a team queue), the steps it
depends on, a deadline in business days counted from the start or from another step (`extrair+2`), done criteria and
the **evidence** it needs (`file`: can't be completed without an attachment; `approval`: someone else approves).

Each **run** (opened by hand with an anchor date, or by the playbook's schedule, e.g. `@bd 1 08:00` with the title
`Closing {mes}/{ano}`) creates the tasks and items. A step starts only when the steps it depends on are done, and it
receives what they delivered. The run board shows owner, deadline, status and lateness per step; the playbook page
shows **where it usually runs late** across the last runs.

## Team queues and work for people

A **queue** (*Digital employees → Queues*) is a team's single entry point: portal, webhook
(`POST /hooks/queues/<slug>` with the queue's token) or anything that calls the API. Each request gets a **category**
(keywords), an **SLA** and a **destination**: a Digital employee — the least busy of a list — or the team's people.

- People see the queue as a board and **take the next one** (breached SLA first, then priority).
- A Digital employee can finish the item alone, or prepare what it can and **hand it to a person**
  (`hand_to_person {summary, draft, assignee}`): the item goes to the team (or straight to someone) with the draft.
- Items can require **evidence** (a file, or approval by a maintainer other than whoever did it).
- *My work* lists what is with you, from queues, playbooks and meetings.
- The queue shows how much was **automatic, assisted and human**, the average time and breached SLAs (flagged on the
  item and in the audit log).

## Conversations with people outside the company

`send_external_message` writes to a customer, supplier or candidate by **e-mail** (SMTP of the installation, with
`Reply-To` set to the employee's mailbox) or **WhatsApp**, and the task keeps the conversation.

- **Approved templates** (*Build → Message templates*), with `{{variables}}`, are written by the team's developers and
  approved by a maintainer — someone other than the author. Every outbound message goes through the authority as
  `send_external`, with `template` filled in when it came from an approved template, so a rule like *send_external
  → notify when `template != ""`* lets those go out with just a notice while free text keeps asking for approval.
  The company floor applies on top: by default it asks for approval on `send_external`, so lowering it is an
  explicit admin decision.
- **Waiting for the reply:** with `wait_reply`, the task waits in `waiting_external` (shown in *Why it stopped*) and
  resumes when the reply arrives — by e-mail, matched by the `[#H-xxxx]` code in the subject, or by WhatsApp.
- **Reminders:** `follow_up_days` sends a reminder after N business days without a reply (also through the
  authority), up to `max_follow_ups`; after that the task wakes up with *no reply*.
- **Promises:** "I'll send it by Friday", "paid by 20/10" in a reply become deadlines on the conversation; when one
  passes, the task wakes up to follow up.
- **WhatsApp Business (Cloud API):** the admin pastes the phone number ID, access token, app secret and a verify
  token in *Decision channels*; the webhook (`/hooks/whatsapp/<company>`) checks the app signature on every call.
  Outside the 24-hour window since the person's last message, only a template approved in WhatsApp itself can be
  sent. People in the company can also ask for work over WhatsApp after linking their number (`link CODE`).

## Calendar and meetings

With the **company calendar** set up once by the admin — Microsoft 365 (a tenant app with calendar permission:
tenant ID, client ID and secret) or Google Workspace (a service account with domain-wide delegation) — Digital
employees find times when everyone is free, within business days and hours (`find_meeting_times`), and send invites
(`schedule_meeting`; an invite to people outside the company is `send_external` for the authority).

**Meetings** (*Digital employees → Meetings*) record attendees, agenda and series:

- **Briefing:** `prep_hours` before the start, the chosen Digital employee prepares the pack (goal, agenda, numbers,
  open questions and what was left open at the previous meeting of the series).
- **Minutes:** paste the transcript (or have Teams, Meet or Zoom post it to `/hooks/meetings/<company>` with the
  company's transcripts token) and the employee writes the minutes and records decisions and actions
  (`record_minutes`). Each action becomes work with a deadline — an item for a person, or a task for a Digital
  employee — and the next meeting of the series lists the ones still open.

## Knowledge base

*Build → Knowledge base* gathers the company's policies, procedures, templates and history from **sources**:
uploaded files (DOCX, TXT, MD, HTML; PDF and images through the Data Studio's OCR), web pages, a **Confluence**
space, a **SharePoint** folder (through the company's Microsoft 365 app credential) or a **Google Drive** folder
(through the Google Workspace service account). Non-upload sources sync once a day (or on demand).

- **Permissions:** per source — the source's teams, the whole company (admin), or **inherited from the original**:
  Confluence page restrictions, SharePoint sharing (people, groups, organisation links) and Drive permissions.
  Groups match the SCIM groups synced from the identity provider.
- **Searching with the requester's access:** `search_knowledge` searches with the access of **the person who asked
  for the task** — never returning what they couldn't read. Tasks without a person (routines, webhooks) see only what
  belongs to the whole company and the employee's team. People search the same way on the page.
- **Citations:** each passage comes back numbered with title, link and date, and the employee cites `[1]`, `[2]`.
- **Freshness:** a document without review for longer than the source's review period shows as *outdated* (in the
  results and in the source owner's list). **Gaps:** questions the base doesn't answer well are listed for whoever
  maintains the content.

## Governed browser

For systems without an API, a Digital employee can drive an isolated browser (`docker compose --profile browser up
-d`; Playwright/Chromium, its own network, reachable only by the central):

- **Allowed domains:** the employee only opens pages on the domains set in *Settings → Platform tools* (or those of
  the procedure it runs); any other navigation is blocked inside the browser itself.
- **Clicks that confirm something** — send, save, pay, transfer, approve, sign — go through the authority with a
  **screenshot** in the decision: paying or transferring is `financial` (by default the company floor asks for two
  people), sending is `send_external`, the rest `write_internal`. Plain navigation doesn't need authority.
- **Secrets:** `value: "secret://name"` fills a field from the vault; the model never sees the value and the timeline
  records only *[secret]*.
- **CAPTCHA and two-factor checks are never bypassed:** they become a question for a person.
- **Procedures** (*Build → Browser procedures*): versioned step-by-steps (`open`, `fill`, `select`, `click`, `read`,
  `screenshot`) with `{{variables}}`, run by `run_procedure`. A task that went well can be **saved as a procedure**
  from its page (secrets stay as `secret://`).

## Governed desktop (experimental)

For desktop applications with no API and no web page — a legacy ERP, a spreadsheet, an installed system — the
employee works in an isolated Linux desktop (compose profile `desktop`, built on [Cua](https://github.com/trycua/cua),
MIT) and **looks at the screen**: `desktop_open`, `desktop_look`, `desktop_click`, `desktop_type`, `desktop_key`,
`desktop_scroll`, `desktop_screenshot`.

- **Allowed apps** per employee (*Settings → Platform tools*): it only opens and operates those.
- **The model sees the window:** each action returns the window's screenshot (sent to the model as an image, never
  stored on the timeline) and the accessibility elements with a number. Apps without accessibility (Tk, painted
  screens) are operated by `x,y` on the screenshot — the model needs vision.
- **Clicks and shortcuts that confirm something** (save, confirm, post, send, pay; `ctrl+s`; `Return` in a save or
  confirmation dialog) go through the authority with the screenshot in the request. The approval covers the action
  (label and window) and the click happens where the person approved; after approval the employee returns to the
  same window, with what it had filled in. Items that only open a dialog ("Save as…") don't ask; the "Yes/OK" of the
  confirmation dialog that appears right after an approved click is covered by that approval.
- **What will be confirmed, and what already was:** each request lists the fields typed since the last
  confirmation and the confirmations already made in the task. Confirming again with the same fields isn't sent to a
  person: the employee is told it looks like a repeat and to check the screen (`repeat: true` when it is on purpose).
- The employee works on the **top window** of the app — a dialog, an open menu, a report — and the elements it can
  click or fill come first in the list.
- **Secrets:** `text: "secret://name"` types the vault's value; the model never sees it.
- The Cua token never leaves the desktop container: the central talks to a small gateway (`mcp-servers/desktop`)
  that keeps one desktop session per task. The desktop network has no internet access.

`docker compose --profile desktop up -d desktop` starts it with two sample apps (Gnumeric and a Tk "legacy ERP" with
login, menus, a confirmation dialog and a long report); add your own applications in `mcp-servers/desktop/Dockerfile`.

**Battery with deepseek-v4.1-flash** (`tests/e2e/pilot_llm.py desk_*`, 8 cases on the Tk ERP — vision only — and on
Gnumeric): login with a vault password, a date in another format, a nonexistent invoice (nothing written), a
confirmation dialog, menu + second window + scrolling, a spreadsheet with a formula saved through the "Save as"
dialog, opening a CSV and reading it, and two payments in one task without duplicates.

## Data governance

*Organization → Data governance* (admins and auditors):

- **Classification** — public, internal, confidential, restricted — on tasks, files, projects and knowledge sources;
  a task's level is the highest of them. Each **LLM connection** has the most sensitive level it may receive: a task
  above it doesn't go to that model (it fails with the reason). Lowering a task's level is for admins.
- **DLP:** CPF, CNPJ, card numbers (Luhn), e-mail, phone and ICD codes can be **masked** in what the platform sends to
  the model — the request, the task's conversation, files and platform-tool results. The model sees `[CPF_1]`; the
  gate hands the **real values** to whatever executes the action, and the stored result shows them to people. What an
  agent fetches directly from its own tools isn't masked (authority and plugin policies cover that).
  **Block outbound** stops those data from leaving in a message to someone outside.
- **Retention** per classification: the content of tasks closed more than N days ago is erased (the record and the
  audit log stay), unless the task or its project is under **legal hold** (admins).
- **Data subject requests (LGPD):** locate a person's data (CPF with or without punctuation, e-mail or name) across
  tasks, conversations, text files, extractions, outside conversations and the knowledge base; **erasure** needs the
  approval of another admin and is logged.
- **Information barriers:** a **confidential project** is readable only by its members and whoever asked for the task
  — not even admins and auditors, unless they open **emergency access**: a justification, one hour, the audit log and
  a notice to the project's owner.

## Document change proposals

Digital employees never edit a document directly: `propose_document_change` (a task file, `file:<name>`, or a
knowledge-base document, `kb:<id>`, from Confluence or SharePoint) turns the change into a decision showing each
passage **before and after**. The person accepts all, some, or adjusts the text; only then is it applied — a new
version of the task file, a new version of the Confluence page, or a new version of the SharePoint file. **DOCX comes
back as a redline**: accepted passages appear as tracked deletions and insertions signed by whoever approved (the
original formatting isn't kept in that version). If the document changed after the proposal, nothing is applied.

## Decision memos

When the request is a choice ("renew with a discount or let it go?", "which supplier?"), the employee calls
`submit_decision_memo`: the question, the options with pros and cons, weighted criteria, a 0–10 score per option and
criterion, the recommendation with its confidence, the risks and the sources. The platform computes the **weighted
totals** — the employee doesn't add them up.

- **Critical reviewer** (*Settings → Golden tasks*, per employee): checks the memo before it's delivered — a
  recommendation that isn't the highest score without an explanation, high confidence without sources, no risks, no
  cons on the chosen option, a single criterion. With an LLM connection, a second model reviews it too. A serious
  problem on the first version sends the memo back to the employee once, with the notes.
- *Digital employees → Decision memos* shows the matrix, the critic's notes and the task. Whoever can read the task
  **records the decision** (the recommended option, another one, or "other", with a note) and when to ask how it
  went; on that day the platform opens a **"How did the decision go?"** item for that person. The **outcome** (worked,
  partly, didn't) feeds the employee's **decision record**: how often its recommendation was followed and, when
  followed, how often it worked.

## Watchers

*Digital employees → Watchers*: look at a source on a schedule (at least every 15 minutes) and act only when
something happens.

- **Sources:** a web page (its text), a JSON value (`path` like `data.0.price`), a knowledge-base search, or a platform
  number — `open_tasks`, `failed_tasks_24h`, `queue_open:<queue>`, `queue_breached:<queue>`. Addresses go through the
  same check as knowledge-base URLs.
- **Conditions:** *changed* (the first look only records the baseline; then a diff of what changed), *crossed a
  threshold* (`> 100`) or *started to contain* a phrase — these fire **on the transition**, not on every look while
  the condition holds.
- **Actions:** a task for a Digital employee with what changed, a notice to whoever created the watcher, or just the
  log. Each watcher shows how many times it fired, the last change and the last error.

## Pair mode and learning tracks

- **Pair mode:** when assigning a task, tick *Pair mode*: the employee explains, reviews and drafts, but **doesn't act**
  — any tool that changes something is refused ("MODO PAR") without opening a request. Good for someone learning the
  job, or for work the person must do themselves.
- **Ask for a review of my work:** on an item in progress, in review or done (*My work*), choose a Digital employee: it
  receives the request, what the person did and the attached files, in pair mode, and returns a checklist.
- **Learning tracks** (*Digital employees → Learning tracks*): multiple-choice questions generated from a playbook
  or a browser procedure — who does each step, what comes first, the evidence, the deadline, the domains. The answers
  never go to the screen; each attempt is scored with the explanation of what was wrong, and maintainers see the
  team's best scores and attempts.

## Value and opportunities

*Digital employees → Value and opportunities*:

- **Baselines** (admins): how long a person would take and what an hour costs — `default`, per employee
  (`employee:<slug>`) or per queue category (`queue:<queue>:<category>`).
- **ROI** per employee over 7, 30, 90 or 365 days: tasks done, hours saved, equivalent human cost, LLM cost, net value
  and rework rate (rejected reviews and failed tasks); the queues' automation rate. *Report* downloads it as Markdown, ready
  for a monthly review.
- **Opportunities:** requests that repeat and are still handled by people (queue categories, playbook steps done by
  people), with how many per month and how many hours — candidates for a Digital employee.

## Golden tasks and regression

*Employee → Settings → Golden tasks*: what the employee must keep getting right — a request and the expected answer
(or bring in the probation tasks that had one). Every change to the role becomes a version in the history; with
*Run on every role change* on, the golden tasks **run again** with the production configuration, but **nothing that
changes something is executed** (and no requests are opened). Each one is scored 1–5 by the quality judge (or, without
an LLM, by how much of the expected answer is in the result) and compared with the previous run. If any task drops 2
points, or the average drops 1, the manager gets a **regression** decision showing before → after: keep the change,
or go back to the previous version of the role.

## API

| Method | Path | Description |
|---|---|---|
| GET / PUT | `/api/employees/{slug}/platform-tools` | Tool groups and whether each is on / `{disabled: [groups]}` |
| GET / PUT | `/api/brand` | Deliverables branding (`theme, accent, footer`) and whether the Data Studio is up |
| GET / PUT | `/api/extraction/schemas` | Schemas / create or change (`name, title, fields, min_confidence, team`) |
| DELETE | `/api/extraction/schemas/{name}` | Archive |
| GET | `/api/tasks/{id}/extractions` | The task's extractions with values, confidence and corrections |
| POST | `/api/employees/{slug}/batch` | `{title, schema, files}`: one task per file plus consolidation |
| GET / PUT | `/api/calculators`, `/api/calculators/{name}` | List / detail with runs; create or change |
| POST | `/api/calculators/{name}/test` | Run the cases, or `{inputs}` to simulate |
| POST | `/api/calculators/{name}/certify` · `/deprecate` | Certify (four eyes) · stop using |
| GET / PUT | `/api/calendar` | Business days, hours and holidays |
| GET / PUT / DELETE | `/api/queues`, `/api/queues/{slug}` | Queues (with stats) / create or change (`name, team, categories, default_category`) / remove |
| POST | `/api/queues/{slug}/items` · `/next` · `/intake-token` | New request · take the next one · webhook token |
| GET | `/api/work-items` | `?queue=&status=open&mine=true` |
| GET / POST | `/api/work-items/{id}` | Detail / `{action: assign, start, done, approve, reject, reopen, cancel, release, note, assignee}` |
| POST | `/api/work-items/{id}/files?name=` | Attach (body = file) |
| GET / PUT / DELETE | `/api/playbooks`, `/api/playbooks/{name}` | Playbooks / create or change (`name, team, steps, schedule, run_title`) / archive |
| POST | `/api/playbooks/{name}/runs` | `{anchor, title}`: open a run |
| GET | `/api/playbook-runs/{id}` | The run board |
| GET / PUT | `/api/message-templates` | Templates / create or change (back to draft when the text changes) |
| POST | `/api/message-templates/{name}/approve` | Approve (a maintainer, not the author) |
| GET | `/api/tasks/{id}/conversations` | The task's conversations with messages and promises |
| GET / POST / DELETE | `/api/meetings`, `/api/meetings/{id}` | Meetings / create (`schedule: true` books it in the company calendar) / remove |
| POST | `/api/meetings/{id}/transcript` · `/api/meetings-token` | Send the transcript · token for `/hooks/meetings/<company>` |
| GET / PUT | `/api/knowledge/sources` | Sources / create or change (`name, kind, acl_mode, teams, config, review_days, classification`) |
| DELETE | `/api/knowledge/sources/{name}` | Remove the source and the documents copied from it (the original isn't touched) |
| POST | `/api/knowledge/sources/{name}/sync` · `/documents` | Sync now · upload a document (`title, text` or `file_name, content_base64`) |
| GET | `/api/knowledge/search?q=` · `/gaps` · `/stale` | Search with your access · unanswered questions · outdated documents |
| GET / PUT | `/api/procedures` | Browser procedures (and whether the browser is up) / create or change |
| POST | `/api/procedures/from-task` | `{task_id, name}`: save the path a task took |
| PUT | `/api/employees/{slug}/browser-domains` | `{domains}`: what the browser may open |
| PUT | `/api/employees/{slug}/desktop-apps` | `{apps}`: the desktop apps it may open (experimental) |
| GET / PUT | `/api/governance` | DLP and retention policy (and LLM connections' levels) |
| PUT | `/api/governance/llm/{name}` · `/api/tasks/{id}/classification` · `/api/files/{id}/classification` | Classification levels |
| PUT | `/api/projects/{slug}/governance` · POST `/api/projects/{slug}/break-glass` | Confidential project, members, legal hold · emergency access |
| GET / POST | `/api/subject-requests` · POST `/{id}/erase` | Data subject requests · approve or reject an erasure |
| GET | `/api/memos` · `/api/memos/{id}` | Decision memos you can read (`?pending=true`) · detail |
| POST | `/api/memos/{id}/decide` · `/outcome` | `{option, note, follow_up_days}` · `{outcome: good, mixed, bad, note}` |
| PUT | `/api/employees/{slug}/critic` | `{mode: off, auto}`: critical reviewer |
| GET / POST / PUT / DELETE | `/api/watchers`, `/api/watchers/{id}` | Watchers / create or change (`name, team, source, condition, action, schedule, employee`) / remove |
| POST | `/api/watchers/{id}/check` | Look now |
| POST | `/api/work-items/{id}/review-request` | `{employee}`: a pair-mode review task |
| GET / POST | `/api/learning` | Tracks / create (`source: playbook:<name>, procedure:<name> or manual, team, questions`) |
| POST / GET | `/api/learning/{id}/answer` · `/results` | `{answers}` · the team's scores (maintainers) |
| GET / PUT | `/api/value/baselines` | Baselines / `{items: [{scope, minutes, hourly_cost, remove}]}` (admins) |
| GET | `/api/value/roi` · `/report` · `/opportunities` | `?days=&team=` · Markdown · repeated human work |
| GET / POST | `/api/employees/{slug}/golden` | Golden tasks and the last runs / add (`title, body, expected`) |
| POST / PUT | `/api/employees/{slug}/golden/from-probation` · `/run` · `/mode` | Bring in probation tasks · run now · `{mode: auto, off}` |
| DELETE | `/api/golden/{id}` | Retire a golden task |

Tasks are created with `mode: copilot` for pair mode (`POST /api/employees/{slug}/tasks`).
