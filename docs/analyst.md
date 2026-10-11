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
