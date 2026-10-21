# Harnesses

A harness agent hands each task to a real coding-agent CLI in a **new container per call**. The container gets:

- `TASK`: the message.
- `SYSTEM_PROMPT`: the agent's instructions plus its skills.
- `MCP_CONFIG_JSON`: the agent's MCP servers.
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`: from the connection.
- A single-use callback URL and token.

The CLI runs in an empty git workspace (`/workspace`). The container then posts back the final answer, the
`git diff`, logs and token usage, and it is removed.

| id | CLI (pinned) | Connection protocol | MCP | Usage reported |
|---|---|---|---|---|
| `claude-code` | `@anthropic-ai/claude-code@2.1.283` | `anthropic` | ✓ (`--mcp-config`) | tokens + cost |
| `codex` | `@openai/codex@0.157.1` | `openai` (Responses API) | ✓ (`codex mcp add`) | tokens |
| `hermes` | Hermes Agent @ `8c9fe9640` | `openai` | ✓ (`hermes mcp add`) | tokens + cost (`--usage-file`) |
| `deepseek-harness` | `@deepseek-ai/dsh@0.1.5-rc.3` | `deepseek` | ✗ | — |

Each job also gets `LLM_EXEC_JSON`, the agent's [execution profile](execution.md) already in the harness's own
vocabulary. The entrypoint turns it into the CLI's flags:

| id | Reasoning effort | Output limit | Max rounds | Prompt cache |
|---|---|---|---|---|
| `claude-code` | `--effort` (low · medium · high · max) | `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | `--max-turns` | on; `DISABLE_PROMPT_CACHING=1` when off |
| `codex` | `model_reasoning_effort` (low · medium · high) | — | — | the provider's |
| `hermes` | `--reasoning` (minimal … max) | — | — | the provider's |
| `deepseek-harness` | — | — | — | the provider's |

`harness.model` picks a model from the connection's list (empty = the connection's default).

Notes:

- **Codex** ignores `OPENAI_BASE_URL` for the built-in provider. The entrypoint therefore defines a custom
  `model_provider` with `wire_api="responses"`, so your endpoint must support the OpenAI **Responses** API
  (OpenAI, OpenRouter and recent LiteLLM releases do).
- **DeepSeek Harness** telemetry is disabled in the image (`DSH_TELEMETRY_MODE=DISABLED`). Only the official
  DeepSeek API has been tested.
- **Hermes** is installed with its official installer, pinned to a commit. Building it takes several minutes, so it
  sits behind its own compose profile.

## Paying with a personal subscription

Coding jobs use many tokens. Someone who already pays for a **Claude (Pro/Max)** or **ChatGPT (Plus/Pro)**
subscription can run **the jobs they start** on it, instead of on the company's API connection.

Consumer subscriptions are individual licenses, so the platform follows these rules:

- **Only the person's own jobs use it.** That means jobs started from the portal or console, from `hangar jobs run`
  (personal token), or from that person's AI tool through the platform's MCP.
- **Other jobs always use the API.** Digital employees, schedules, stage tests and app keys serve other people.
- **The company decides.** It's off by default. An admin turns it on in *Members → How they sign in → Personal
  subscriptions for code jobs* (`PUT /api/workspace/policy {"subscriptions": {"enabled": true, "max_concurrent": 2}}`).
  Check Anthropic's and OpenAI's current terms of use first.
- **Supported harnesses:** `claude-code` and `codex`.

**Connecting a subscription.** Each person connects their own in the portal, under *My sign-ins → My subscription for
code jobs*, or with `PUT /api/me/subscriptions/{anthropic|openai}`:

| Harness | On your computer | Paste |
|---|---|---|
| `claude-code` | `claude setup-token`, then sign in with the Claude account | the token it prints |
| `codex` | `codex login`, then sign in with the ChatGPT account | the contents of `~/.codex/auth.json` |

- API keys are refused here; they belong in an LLM connection.
- The credential is encrypted and never comes back through the API, which only shows its last 4 characters.
- An admin sees who connected a subscription, with the jobs and estimated savings of the last 30 days, and can revoke
  it.

**Who pays: `harness.billing` in the agent's spec**

```yaml
harness: {id: claude-code, connection: anthropic-prod, billing: subscription_then_api}
```

| `billing` | The job runs on |
|---|---|
| `api` (default) | the connection |
| `subscription` | the subscription of the person who started it. With no subscription, or when it is at its limit, the job is refused or fails, with the reason |
| `subscription_then_api` | the subscription when it can, the connection otherwise |

- On a subscription, the container gets no API key or base URL:
  - Claude Code runs with `CLAUDE_CODE_OAUTH_TOKEN`;
  - Codex runs with the `auth.json` written to its home and its built-in provider.
- `harness.subscription_model` picks the model by the CLI's own name. Empty means the plan's default.
- A harness agent with no connection and `billing: subscription` is not a mock: it runs the real CLI on the
  subscription.

**Plan limits**

- The entrypoint recognizes the CLI's "usage limit reached" error. The subscription then pauses until the reset time
  the vendor gives, or for one hour.
- What happens to the job depends on `billing`:
  - `subscription`: the job fails;
  - `subscription_then_api`: the same job runs again on the connection.
- The job's `billing_note` records what happened.
- A subscription runs at most `max_concurrent` jobs at a time; beyond that, the job uses the API.
- A credential the vendor rejects is marked *rejected* until the person connects it again.
- When Codex refreshes its sign-in during a job, the new `auth.json` is saved for the next job.

**Metering.** A job paid by a subscription costs US$ 0 in metering. It also records how much it would have cost on the
API (`api_equiv_cost_usd`, from the connection's prices or the cost the CLI reports), so the savings are visible.

## Images

Each harness is its own image built from `harness/Dockerfile` (multi-stage):

```bash
docker compose build                     # harness-base (mock mode, always)
docker compose --profile harness build   # harness-claude-code, harness-codex, harness-deepseek-harness
docker compose --profile hermes build    # harness-hermes
```

Image names are `${HANGAR_REGISTRY:-agent-hangar}/harness-<id>:${HANGAR_VERSION:-latest}`. To use a custom image
for one harness, set `HARNESS_IMAGE_<ID>` (e.g. `HARNESS_IMAGE_CLAUDE_CODE=myorg/claude:1`).

### The Claude Code image is not published

The public images on `ghcr.io/valteresj2` include `harness-base`, `harness-codex` (Codex CLI, Apache-2.0),
`harness-deepseek-harness` (MIT) and `harness-hermes` (MIT). **`harness-claude-code` is not published.** Claude Code
(`@anthropic-ai/claude-code`) is proprietary software from Anthropic: its license does not allow redistributing it
inside a public image. You build that image yourself, from this repository:

```bash
docker compose --profile harness build harness-claude-code
```

- **Docker:** `hangar setup` does this for you when you choose the coding-harness extra with published images. The
  other harnesses are pulled from ghcr.io.
- **Kubernetes:** build the image and push it to your own registry, then point the hangar at it:
  ```bash
  docker build --target claude-code -t registry.acme.com/agent-hangar/harness-claude-code:0.46.0 harness/
  docker push registry.acme.com/agent-hangar/harness-claude-code:0.46.0
  ```
  Either mirror all images there (`image.registry`), or set only this one with
  `central.extraEnv: {HARNESS_IMAGE_CLAUDE_CODE: registry.acme.com/agent-hangar/harness-claude-code:0.46.0}`.
- Using Claude Code is subject to Anthropic's terms, and its calls need an Anthropic-compatible connection.

A harness without a connection runs on `harness-base` in **mock** mode: it writes a `JOB_NOTES.md` and returns.
No CLI or key is needed.

## Container limits

The limits are memory `JOB_MEM_LIMIT` (1g), `JOB_CPUS` (1.0) and 512 PIDs, with all capabilities dropped and
`no-new-privileges`. Containers run as a non-root user with no Docker socket. They are attached only to the
`hangar_jobs` network: they can reach the hangar callback and the internet, but not agent containers. The default
timeout is `JOB_TIMEOUT_S` (180s); you can raise it per call up to `JOB_MAX_TIMEOUT_S` (900s).

## Adding a harness

1. Add a stage to `harness/Dockerfile` that installs the CLI (pin the version).
2. Add a `runX()` function in `harness/entrypoint.js` that maps the generic `LLM_*` variables and returns
   `{status, result, logs, usage}`. Register it in `RUNNERS`.
3. Add the id and protocol to `HarnessId` (`central/app/spec.py`) and `HARNESS_PROTOCOL`
   (`central/app/services/catalog.py`).
4. Add a compose build service under the `harness` profile and document it here.
