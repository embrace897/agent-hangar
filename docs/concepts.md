# Concepts

## Agent

An agent is a registry entry with four human-facing fields (**name**, **objective**, **final output**, **owner**) and
a versioned **spec** that defines its behavior (see [spec.md](spec.md)). The slug (`doc-qa`) is its stable ID in
URLs, API calls and sub-agent references.

Every spec change creates a new **immutable version** (v1, v2, …). Saving an identical spec is a no-op. Rolling
back copies an old spec into a *new* version, so history is never rewritten.

Every agent also has a **guide** for the people who will use it: what it is, what it does, what it does not do,
how to use it and its limits. The AI tool that builds the agent writes it as a step of the build, before the ship.
The guide is versioned with the agent, but it lives outside the spec, so fixing it needs no new version or tests.
See [guide.md](guide.md).

## Agent types

| Type | Runs as | Good for |
|---|---|---|
| **Chat** (default) | A long-running container with an LLM tool-calling loop (max 6 steps) | Q&A, analysis, writing, classification, calling tools/MCPs, orchestration |
| **Harness** (`spec.harness`) | A new ephemeral container **per call**, running a real coding agent CLI | Tasks that need to *execute*: edit files, run code, produce a diff |
| **Multi-agent** (`spec.sub_agents`) | A chat agent whose tools include `ask_<sub-agent>` | Objectives that split into specialist roles |

A harness agent cannot have sub-agents, but it can *be* a sub-agent of a chat orchestrator. When building through
MCP, the model picks the type from the objective (the hangar's instructions explain the criteria); users can always
override it.

## Environments and the promotion gate

Each agent has two environments, **stage** and **prod**, each with its own container (`agent-<slug>-<env>`).
`ship` runs the full pipeline:

1. Ship sub-agents first (recursively).
2. Deploy the current version to **stage**.
3. Run tests: protocol smoke tests (health, Agent Card, OpenAI, A2A, ACP, MCP) plus the spec's test cases.
4. Record the test run against that version.
5. Promote to **prod** only if the run passed. `promote`/`deploy prod` enforce the same gate.

Agents without an LLM connection run in **mock** mode: they echo, content checks are skipped (marked `(mock)`), and
the smoke tests still validate the plumbing.

## Connections

A connection is an external LLM endpoint: `base_url` + default `model_name` + API key (stored encrypted with
`HANGAR_SECRET_KEY`) + `protocol` + optional prices per 1M tokens. The hangar never hosts a model.

### Providers and their models

Admins add connections in **Configurações → Provedores de LLM**. There is one card per known provider (OpenAI,
Anthropic, Gemini, OpenRouter, LiteLLM, DeepSeek, Groq, Mistral and others, plus Ollama), and one for any endpoint
compatible with OpenAI or Anthropic.

When a connection is saved, the central asks the provider for its model list. It refreshes the list every
`LLM_MODELS_REFRESH_MIN` minutes (default 60; 0 turns it off). Only one replica refreshes. Each model is stored with:

- **output type:** text, embedding, rerank, image, video, tts, stt or moderation.
  - It comes from the provider when the provider says it (OpenRouter, LiteLLM `/model/info`, Together, Mistral,
    Cohere).
  - Otherwise it comes from LiteLLM's public model table (`MODEL_CATALOG_URL`; empty for air-gapped installs).
  - Otherwise it is inferred from the model name.
  - An admin can correct it, and refreshes don't undo the correction.
- **features:** reads images, tools, reasoning.
- **context length**.
- **price per 1M tokens:** the provider's price, or the public table's price only when the name matches that
  provider.

How the list is kept:

- **Model gone from the provider:** it stays in the list, marked unavailable. If an agent uses it, admins see it in
  their inbox.
- **Provider without a list** (Perplexity, Voyage, Jina): the admin adds models by hand.

How agents use the list:

- **Choosing a model:** an agent picks any model of its connection (`llm.model`). A model that isn't in the
  connection's list is refused.
- **Cost:** it uses the price of the model the agent actually uses. A price typed on the connection applies only to
  its default model.

The builder reads the list through `list_llm_models` (by type, with the missing types). Before building an agent
that needs, say, embeddings or speech-to-text, it checks that the company has such a model. If not, it asks for a
provider that has one.

| Protocol | Used by |
|---|---|
| `openai` | chat agents, `codex`, `hermes`, LLM-as-judge |
| `anthropic` | `claude-code` |
| `deepseek` | `deepseek-harness` |

`llm.stage` / `llm.prod` (and `harness.stage` / `harness.prod`) override the connection or model per
environment, for example a cheaper model in stage.


### Model profiles

Each model also gets a **profile**: what it is good for, 0–100 scores per area and where each piece came from. The same
model reached through two connections (OpenRouter and LiteLLM, say) shares one profile.

Free sources, refreshed every hour:

- **Artificial Analysis:** measured intelligence and coding indexes and output speed. It needs the company's key, kept in
  the vault and referenced as `secret://name`. Each score is the model's position among the models they measured
  (90 = better than 90% of them).
- **OpenRouter's public description**, used as the summary.

Optional research, with a monthly budget:

- One of the company's own models reads the sources and fills in summary, strengths, limitations and typical uses. The
  sources are the Hugging Face card (open models) and web search (Brave, Tavily or a self-hosted SearXNG).
- The page text goes to the model as data. The model is told to ignore any instruction inside it, and only the profile
  is kept. Pages on the internal network are never fetched.
- A score from research is marked as an estimate and needs benchmark evidence in the sources; a measured score
  always wins over it.
- What the research model already knows from the platform (type, context, price, features) goes along as trusted data.
- Automatic mode researches the models agents use and the connections' defaults first (`used`), all text models
  (`all`) or nothing (`off`). It runs at most `per_run` per hour and stops when the month's budget runs out.

Admins can fix any field. A fixed field is locked, so research no longer changes it.

The **cost** score is computed live, from the model's price compared with the company's other text models. The price
is a blend of 3 parts input to 1 part output.

`MODEL_PUBLIC_SOURCES=0` turns off every public source, for air-gapped installs.

The builder reads profiles through `list_llm_models` and `get_model_profile`. It calls `check_model_needs(kinds,
caps)` before building. If the company lacks a model type the agent needs, it shows the user which one is missing and
where to add it.

## Gateway and channels

Clients reach agents through the hangar at `/gw/<slug>/…` (prod) and `/gw-stage/<slug>/…`, authenticated with an
API key. The gateway proxies to the agent container, streams SSE responses, and records usage (tokens, cost,
latency, errors) per agent, environment, **channel** (the `X-Channel` header, e.g. `slack`) and protocol.

## Jobs

A call to a harness agent becomes a **job**: queued → running → passed/failed/timeout/error/cancelled. The
container receives the task, the agent's instructions and skills, its MCP servers and the connection's credentials.
It runs the CLI in an empty git workspace and posts the result, the `git diff` and the token usage back through a
single-use callback token. Jobs can be followed live over SSE (`/api/jobs/<id>/events`) and cancelled.

## Usage and cost

Chat agents compute cost from the connection's prices and the LLM's reported usage. Jobs use the harness's reported
tokens: Claude Code and Codex give token counts, and Hermes also reports its own cost estimate, which is used when
the connection has no prices. DeepSeek Harness does not report usage yet.
