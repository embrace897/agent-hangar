# Execution profile

The execution profile says how the model works on each call:

- **Reasoning effort:** how much the model thinks before it answers.
- **Output limit:** the most tokens it may write.
- **Parallel tools:** whether tool calls from different servers run at the same time.
- **Prompt cache:** whether the fixed part of the prompt is cached.
- **Max rounds:** for a harness, how many rounds the CLI may take.
- **Long context:** what a chat agent does when the conversation no longer fits (see [Long context](#long-context)).
- **Model per response:** a cheaper model for simple requests (see [Model per response](#model-per-response)).

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

## Long context

A chat agent compacts the conversation when it goes over its context budget. Compaction is on by default and does
nothing until the budget is reached. The budget is `context_budget` in tokens. Without it, the budget is 60% of the
model's window, from the provider's model list, or 48,000 tokens when the connection has no list. The rest of the
window is left for the answer.

Before each model call the runtime estimates the conversation's size. When it is over budget:

1. **Large tool results leave the conversation.** Each result over 2,000 characters is replaced by its first 800
   characters and an id. Older results go first; the latest round's results only go if that is still not enough.
   The agent gets the `hangar_read_result` tool to read a result back, 6,000 characters at a time. This tool only
   re-reads what a tool already returned, so a Digital employee's authority rules don't apply to it.
2. **If it still doesn't fit, the old history becomes a summary.** The last messages stay whole: at least 6, and up
   to half the budget. A tool result is never kept without the call that asked for it. Everything older is summarized
   by one model call into requests, decisions, facts, files and what is pending. The summary goes into the system
   prompt, and the conversation text is passed to the model as data. The summary uses the light model when there is
   one, and its tokens are billed like any other call. If the summary call fails, the old messages are cut with a note,
   and the answer still goes out.

Each compaction is counted in the call's `usage` (`compactions`, `context_tokens`) and shows as a progress line in
streaming chats. Set `compact: false` to turn it off.

## Model per response

With `light_model`, simple requests go to a cheaper model and the rest go to the agent's model. The light model must
be on **the same connection**, so the agent's container needs no other key.

- `light_model: auto` picks among the connection's models that cost at most 40% of the agent's model and have tool
  calling when the agent uses tools. Free models (price 0) are left out: their rate limits are tight and they have
  their own data-use terms. You can still choose one by its id. It takes the one with the best reasoning score in the
  [model profiles](concepts.md), or the most expensive of them when no profile has a score. It needs the prices from
  the provider's model list.
- `light_model: <model id>` uses that model. It must be on the connection's list when the connection has one.

The runtime decides per response. The agent's model takes the request when there is an attachment, the request is
over 1,200 characters, the conversation has over 14 messages, or the text asks for analysis, code, planning, a
comparison or a report. It also takes every Digital employee task and every request that brings the client's own
tools (coding agents). Everything else goes to the light model.

The agent's model takes over mid-response when the light model:

- fails (a provider error),
- returns an empty answer,
- writes a tool call that isn't valid JSON, or
- is still calling tools after 3 rounds.

Each call is billed at the price of the model that served it. The usage event records the model that answered, and
`usage.route` says why (`leve: …`, `principal: …`, `escalado: …`). The agent's tests run with the same routing in
stage, so a light model that hurts quality shows up before production.

## Warnings

The profile lists what does not take effect, so nobody assumes it does:

- an effort set on a model without a reasoning mode, or on a provider or harness without that control;
- a model whose list says it has no tool calling, while the agent uses tools (or runs in a harness, which always
  does);
- an output limit or max rounds that the harness ignores.

Long context and model per response also warn when:

- the context budget is over 90% of the model's window;
- `light_model: auto` finds no cheaper model with tools on the connection, or the agent's model has no price;
- the chosen light model is not on the connection's list.

## Measurement and suggestions

Is the effort worth what it costs? Every call records the agent version, the model and the effort level that served
it. Two sources of measurement come from that.

**Real usage.** The last 30 days are grouped by model and effort. For each group the measurement shows:

- calls, and the share without errors;
- median time and cost per call;
- the test cases of the versions that used that profile.

Different efforts of the same model are compared when each has at least 20 calls.

**Profile comparison.** *Comparar perfis* runs the agent's test cases in stage with the current level and its
neighbors (one below and one above):

- each level is measured on cases passed, average cost and average time;
- levels that send the same value to the model count once (on OpenRouter, minimal and low are the same);
- up to 10 cases per level are run (cases with `workspace` are left out), and the comparison costs model calls;
- chat agents get the other level only on that request, through a header that only the central can sign;
- harness agents get one job per case and level.

**The suggestion:**

1. **Raise the effort** when a higher level passes at least 10 points more (or, in real usage, errs much less while
   the current one errs on more than 10% of calls).
2. Otherwise, **lower the effort** when a lower level passes the same and costs or takes at least 15% less.

A comparison counts only for the agent's current version and model. Otherwise the suggestion comes from real usage.
The suggestion shows on the agent page (*Medição do perfil*), in the admins' inbox and over MCP. Nothing changes by
itself: applying it creates a new version, which goes through the tests before production.

## Where to see and change it

- **Agent page → Execução:** the profile in production or stage, what is sent and through which parameter, where
  the effort came from, and the warnings. Whoever can edit the agent changes it there; saving creates a new
  version, which goes through the tests before production.
- **API:** `GET /api/agents/{slug}/execution?env=prod`; change it with `PATCH /api/agents/{slug}`
  `{"execution": {...}}` (`null` in a field goes back to automatic).
- **MCP:** `get_execution_profile` and `design_agent(execution={...})`; measurement with `get_execution_tuning`, comparison with `compare_execution_profiles`.
- **Measurement API:** `GET /api/agents/{slug}/execution/tuning`, `POST /api/agents/{slug}/execution/trials` (`{levels?}`), `GET /api/agents/{slug}/execution/trials/{id}`.
