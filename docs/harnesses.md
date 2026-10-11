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

Notes:

- **Codex** ignores `OPENAI_BASE_URL` for the built-in provider. The entrypoint therefore defines a custom
  `model_provider` with `wire_api="responses"`, so your endpoint must support the OpenAI **Responses** API
  (OpenAI, OpenRouter and recent LiteLLM releases do).
- **DeepSeek Harness** telemetry is disabled in the image (`DSH_TELEMETRY_MODE=DISABLED`). Only the official
  DeepSeek API has been tested.
- **Hermes** is installed with its official installer, pinned to a commit. Building it takes several minutes, so it
  sits behind its own compose profile.

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
  docker build --target claude-code -t registry.acme.com/agent-hangar/harness-claude-code:0.26.0 harness/
  docker push registry.acme.com/agent-hangar/harness-claude-code:0.26.0
  ```
  Either mirror all images there (`image.registry`), or set only this one with
  `central.extraEnv: {HARNESS_IMAGE_CLAUDE_CODE: registry.acme.com/agent-hangar/harness-claude-code:0.26.0}`.
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
