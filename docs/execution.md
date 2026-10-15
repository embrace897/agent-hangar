# Execution profile

The execution profile says how the model works on each call:

- **Reasoning effort:** how much the model thinks before it answers.
- **Output limit:** the most tokens it may write.
- **Parallel tools:** whether tool calls from different servers run at the same time.
- **Prompt cache:** whether the fixed part of the prompt is cached.
- **Max rounds:** for a harness, how many rounds the CLI may take.

You rarely need to set it. Without an `execution` block in the spec, the central works it out from the agent's role
and from what the model supports. Then it translates the profile into what each provider and each harness
understands.

## Automatic effort

The effort comes from the agent's role (the same role used for [model selection](concepts.md)):

| Role | Effort |
|---|---|
| planner / orchestrator, coder | high |
| data analysis, general | medium |
| writer, support, data extraction | low |
| classification / triage | minimal |

Effort only goes to a model that has a reasoning mode, according to the provider's model list. A model without one
is left alone. A connection without a model list gets no automatic effort, because there is no way to confirm the
model reasons. An effort you set yourself is still sent there, with a warning.

## How it is sent

**Chat agents (the runtime)** get the profile at deploy time and add it to each `/chat/completions` call:

| Provider of the connection | Effort | Output limit |
|---|---|---|
| OpenRouter | `reasoning: {effort}` | `max_tokens` |
| OpenAI | `reasoning_effort` (`minimal` only here) | `max_completion_tokens` |
| LiteLLM, Gemini, Groq, xAI, Fireworks, Together, Nebius, Ollama, custom | `reasoning_effort` | `max_tokens` |
| Others (e.g. DeepSeek) | not sent (warning when set) | `max_tokens` |

- `max` becomes `high` on these APIs. Short calls (conversation titles) always use low effort.
- **Prompt cache:** Claude models behind LiteLLM or OpenRouter get the system prompt marked with `cache_control`.
  OpenAI, DeepSeek, Gemini, xAI and Groq cache by themselves.
- **Parallel tools:** when the model asks for several tools in one round, tools from different MCP servers and
  different sub-agents run at the same time. Tools of the same server (a browser, a desktop) and the platform's own
  tools stay in order. What goes back to the model keeps the order of its calls. Digital employees always run one
  at a time, because their authority decides each action. `parallel_tools: false` turns this off and also sends
  `parallel_tool_calls: false`.
- **Safety net:** if the provider refuses a call (400/422) that carried these options, the runtime repeats it with
  only the essentials, logs a warning, and goes on without the options in that container.

**Harness agents** get the profile in `LLM_EXEC_JSON`; the table in [harnesses.md](harnesses.md) shows each CLI's
flags. Whatever a harness does not support is dropped, with a warning.

## Warnings

The profile lists what does not take effect, so nobody assumes it does:

- an effort set on a model without a reasoning mode, or on a provider or harness without that control;
- a model whose list says it has no tool calling, while the agent uses tools (or runs in a harness, which always
  does);
- an output limit or max rounds that the harness ignores.

## Where to see and change it

- **Agent page → Execução:** the profile in production or stage, what is sent and through which parameter, where
  the effort came from, and the warnings. Whoever can edit the agent changes it there; saving creates a new
  version, which goes through the tests before production.
- **API:** `GET /api/agents/{slug}/execution?env=prod`; change it with `PATCH /api/agents/{slug}`
  `{"execution": {...}}` (`null` in a field goes back to automatic).
- **MCP:** `get_execution_profile` and `design_agent(execution={...})`.
