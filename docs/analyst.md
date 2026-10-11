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
