# Contributing to Agent Hangar

Thanks for helping! Small, focused PRs are the easiest to review.

## Good first contributions
- **Templates** (`templates/*.yaml`): see [docs/templates.md](docs/templates.md).
- **Docs**: clearer guides, client setup for tools we don't cover yet, translations.
- **Harness adapters**: see "Adding a harness" in [docs/harnesses.md](docs/harnesses.md).
- Issues labeled `good first issue`.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r central/requirements.txt pytest ruff -e ./cli
pytest                                                # unit + API tests (SQLite, Docker mocked)
ruff check .
```

Full stack: `./scripts/setup.sh && docker compose up -d --build`. Then run
`HANGAR_TOKEN=<admin> python tests/e2e/smoke.py` for the end-to-end smoke test (mock mode, no LLM key needed).
The CI also runs `tests/e2e/workforce.py` (Digital employees and plugins, against the echo system in
`tests/e2e/echo_server.py`) and `tests/e2e/i18n_crawl.py`, which opens every screen in English and fails if Portuguese
is left on it. New UI text goes in the dictionary of `central/app/static/i18n.js`; when the same Portuguese text needs a
different translation depending on where it appears, use `window.t(text, context)` and add the entry to `DC`. Releases
are published by the *Release images* workflow; see `release-notes/README.md`.

## Code layout

| Path | What |
|---|---|
| `central/app/main.py` | FastAPI app, auth middleware, mounts |
| `central/app/routers/` | `admin` (`/api`), `gateway` (`/gw`), `internal` (`/internal`) |
| `central/app/services/` | Business logic: registry, runtime (deploy/ship), testing, jobs, catalog, usage |
| `central/app/mcp_tools.py` | The platform MCP server (what Claude/ChatGPT/Codex call) |
| `central/app/spec.py` | Agent spec schema and merge patch |
| `central/migrations/` | Alembic migrations. Add one for every model change: `alembic revision -m "..."` |
| `runtime/` | Image of a chat agent (OpenAI, A2A, ACP, MCP and AG-UI endpoints + tool loop) |
| `harness/` | Job images and `entrypoint.js` (one runner per harness) |
| `cli/` | `hangar` CLI |

## Guidelines
- Keep the style of the surrounding code. Comments explain *why*, not *what*.
- Add or update tests for behavior changes. `pytest` must pass and `ruff check .` must be clean.
- Never commit secrets, `.env` files or real customer data (templates use invented examples).
- Security-sensitive changes (auth, tokens, containers) need a note in `docs/security.md`.
- Commit messages: imperative mood ("Add Slack adapter"), with a body explaining the motivation when it isn't obvious.

## Code of Conduct
This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
