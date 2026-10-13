# CLI (`hangar`)

```bash
pip install ./cli            # from a clone; requires Python 3.10+
hangar login http://localhost:8090 --token <personal token>   # UI → Chaves de API → scope "user"
```

Credentials are read from `HANGAR_URL` / `HANGAR_TOKEN`, or from `~/.config/hangar/config.json` (written by
`login`, mode 600). Every command accepts `--json`.

| Command | What it does |
|---|---|
| `hangar whoami` | Your company role and teams |
| `hangar agents ls [--mine]` | Agents you can see: team, your access, status, version, stage/prod, requests and cost (7d) |
| `hangar agents get <slug> [--spec]` | Detail, or just the spec as YAML |
| `hangar apply -f file.yaml [--ship]` | GitOps apply (`{skills, mcp_servers, agents}`; multiple YAML documents allowed; `-` = stdin). Template files are accepted too |
| `hangar export <slug>` | Print an agent as an applyable YAML document |
| `hangar test <slug>` | Run stage tests; exit code 1 if they fail (useful in CI) |
| `hangar ship <slug>` | Stage → tests → prod |
| `hangar deploy <slug> --env stage|prod` | Deploy one environment |
| `hangar rollback <slug> <version>` | Restore an old spec as a new version |
| `hangar chat <slug> <message…> [--env]` | Talk to an agent |
| `hangar logs <slug> [--env]` | Container logs |
| `hangar jobs run <slug> <task…> [--follow] [--timeout N]` | Harness job; `--follow` streams status and logs, Ctrl+C cancels |
| `hangar jobs get <id>` / `hangar jobs cancel <id>` | Inspect / cancel |
| `hangar templates ls` / `hangar templates apply <id> [--connection C] [--harness-connection H]` | Template gallery |
| `hangar connect <slug> [tool] [--mode mcp\|model] [--preview] [--list]` | Plug an agent into a tool: creates a per-tool key and prints the config (steps on stderr). No tool = list tools |
| `hangar keys ls` / `create <name> --scope invoke --agent <slug>` / `revoke <id>` | API keys (`create` prints the key on stdout). `--scope user` = personal token for the CLI and the platform MCP |
| `hangar teams ls` / `hangar teams add <team> <email> --role developer` | Teams (with spend) and members |
| `hangar approvals ls` / `approve <kind> <id>` / `reject <kind> <id>` | Production (`promotion`) and access (`access`) approvals |
| `hangar request-access <slug> <reason…>` | Ask a team to use its agent |
| `hangar schedules ls [slug]` / `add <slug> <message…> --cron '0 9 * * 1-5'` (or `--at 2026-10-05T09:00`) / `run <id>` / `rm <id>` | Schedules: the agent runs by itself in production |


## Install and diagnose

```bash
./scripts/install.sh                     # installs the CLI in .hangar/venv and runs `hangar setup`
hangar setup                             # guided install / reconfigure (Docker)
hangar setup --answers setup.yaml        # unattended (see setup.example.yaml); --no-start only writes .env
hangar doctor                            # checks the installation; --json for monitoring
hangar setup --target kubernetes         # install / upgrade on a cluster with Helm (docs/kubernetes.md)
hangar doctor --namespace agent-hangar   # pods + every check above, via a temporary port-forward
hangar setup --configure-only            # cluster installed by Terraform/GitOps: only LLM, admin and AI tools (no helm)
hangar backup                            # pg_dump to .hangar/backups/ (--namespace for the chart's Postgres)
hangar restore <file.dump>               # restores (saves the current state first)
hangar upgrade --version 0.12.0          # Docker: backup, new images, start, doctor (docs/backup.md)
```

`hangar test-drive` runs the whole path against your installation, with the test model (no LLM, no cost): a team, a
Digital employee, its probation, the admission, one task and the result, with the portal links. It deletes what it
created at the end; `--keep` leaves it there to look at, `--manager <e-mail>` picks the manager (default: you, or a
test person with the admin token).

```bash
hangar test-drive            # ✓ team · ✓ hired · ✓ probation · ✓ admitted · ✓ task done → links
hangar test-drive --keep     # keep the team and the Digital employee to open them in the portal
hangar test-drive --llm openrouter   # the same path with a real model (an LLM connection from the catalog)
```

`hangar demo` creates an example company so a new install doesn't open empty: the team *Exemplo · Renovações* with you
as manager, a goal, a project and three Digital employees in an org chart (the coordinator and two who report to her),
already admitted, and a first task. Without an LLM they use the test model; `--llm <connection>` uses a real one.
Running it again doesn't duplicate anything. `hangar setup` offers it at the end (`demo: true` in the answers file).

```bash
hangar demo                  # ✓ team · ✓ hired · ✓ org chart · ✓ admitted · ✓ project and first task → links
hangar demo --remove         # deletes the example (the project is archived)
```

See [install.md](install.md).

## Agents in CI

```yaml
# .github/workflows/agents.yml
- run: pip install ./cli
- run: hangar apply -f agents/ --ship
  env:
    HANGAR_URL: ${{ secrets.HANGAR_URL }}
    HANGAR_TOKEN: ${{ secrets.HANGAR_ADMIN_KEY }}
```

`apply` only creates versions for specs that changed, and `ship` refuses to promote a version whose tests fail.
