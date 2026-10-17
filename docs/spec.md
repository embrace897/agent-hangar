# Agent spec reference

The spec is validated by a strict schema (unknown fields are rejected). The full JSON Schema is served at
`GET /api/spec/schema` and by the MCP tool `get_spec_schema`.

```yaml
llm:                      # chat agents only (mutually exclusive with harness.connection)
  connection: my-litellm  # name of a connection in the catalog (protocol openai); empty = mock
  model: ""               # empty = the connection's default model
  temperature: 0.2
  max_steps: 6            # tool-calling rounds per message (1–40); data agents need 20–30
  vision: true            # false: attached images go only to the workspace (MCP), not to the LLM
  client_tools: true      # false: ignore the `tools` a client sends (default: coding clients' file/terminal tools are used)
  stage: {model: cheap-model}         # optional per-environment overrides: connection / model / temperature
  prod: {connection: prod-gateway}
harness:                  # harness agents only
  id: claude-code         # claude-code | codex | hermes | deepseek-harness
  connection: anthropic   # protocol must match the harness; empty = mock
  model: ""               # empty = the connection's default model
  stage: {connection: ...}
remote:                   # remote agents only: the agent runs elsewhere and speaks AG-UI (see clients.md)
  url: https://agents.example.com/support/agui
  headers: {Authorization: "secret://support-agent-key"}   # credentials only from the vault
  timeout_s: 120          # time limit of one run (5–900)
  stage: {url: https://staging.example.com/support/agui}    # optional: another address in stage
execution:                # optional; absent = automatic (see execution.md)
  effort: auto            # auto | minimal | low | medium | high | max (reasoning effort)
  max_output_tokens: 8000 # output limit (256–128000); absent = the provider's default
  parallel_tools: true    # chat: tools from different servers and sub-agents run at the same time
  prompt_cache: true      # false: no prompt caching
  max_turns: 40           # harness: the CLI's maximum rounds (claude-code)
judge:                    # connection used by `judge` test cases (defaults to llm.connection)
  connection: my-litellm
  model: ""
instructions: |
  Free text system prompt.
skills:                   # catalog names or inline {name, description, content}
  - pdf-checklist
  - {name: tone, content: "Be concise."}
mcps:                     # catalog names or inline {name, url} (Streamable HTTP)
  - crm
tools:
  - {type: builtin, name: calculator}          # calculator | current_time | platform_dashboard
  - type: http
    name: get_weather
    description: Current weather for a city
    url: https://api.example.com/weather
    method: GET                                # GET sends args as query; others as JSON body
    parameters: {type: object, properties: {city: {type: string}}, required: [city]}
sub_agents: [researcher, writer]               # chat agents only; each becomes an ask_<slug> tool
tests:
  - name: cites the section
    input: What is the hotel limit?
    expect_contains: "180"       # case-insensitive substring
    expect_regex: "\\d+"         # Python regex
    judge: "Must cite the section it used and not invent rules."   # LLM-as-judge rubric
    timeout_s: 300               # harness tests and code evaluations
  - name: fixes the statistics bug          # code evaluation: a real mini-project in a sandbox
    input: The tests of calc.py fail. Fix calc.py and run the tests. Do not change the tests.
    workspace:
      files: {calc.py: "...", test_calc.py: "..."}
      check: python3 -m unittest            # passes only with exit 0…
      protected: [test_calc.py]             # …and without touching these files
      max_rounds: 30
channels: [slack, librechat]     # informational tags
```

## Code evaluations (`workspace`)

A test case with `workspace` checks that a coding agent really solves a task in a real project, not only that it
answers well. For each case, `run_tests` does the following:
1. It starts a throwaway sandbox container from the `harness-base` image, isolated like harness jobs: no Docker
   socket, memory/CPU/PID limits, the jobs network.
2. The sandbox runner writes `files` and talks to the **stage** version of the agent through a single-use internal
   proxy. It offers the tools a coding client offers: `list_files`, `read_file`, `write_file` and `run_command`
   (bash, 120 s per command).
3. It runs those calls **inside the sandbox**, round by round, up to `max_rounds`.
4. When the agent answers, it runs `check`.

The case passes only if `check` exits with 0 **and** no file in `protected` changed. Protecting the tests means the
agent cannot pass by editing them, and in a live run an agent tempted to do exactly that was failed.

The result lists the rounds, the tools called, the files changed and the tail of the `check` output. It shows up in
the **Testes** tab like any other case. A failed case blocks promotion to production, as usual.

- **Sandbox:** Node 22, Python 3 with pytest, git. `files` can hold up to 200 KB.
- **Where it applies:** chat agents with an LLM connection. Without a connection (mock), the case is reported and
  not run. Harness agents are evaluated by their job result instead.
- **What it costs:** the LLM calls of each case, like a real task.
- **Limitation:** the central tracks running evaluations in memory, so it must run as a single process, as the
  default Compose setup does.

## Updating a spec

- **Merge patch**: `PATCH /api/agents/<slug>` or the MCP tool `design_agent`. This is JSON Merge Patch
  ([RFC 7396](https://www.rfc-editor.org/rfc/rfc7396)): objects merge, lists and scalars replace, and `null`
  deletes. Examples:
  - switch connection and go back to its default model: `{"llm": {"connection": "b", "model": null}}`
  - turn a harness agent into a chat agent: `{"harness": null, "llm": {"connection": "x"}}`
- **Replace**: `PUT /api/agents/<slug>/spec` (the UI's *Spec* editor, and `hangar apply`).
- **Rollback**: `POST /api/agents/<slug>/rollback {"version": 3}`.

## Rules enforced by validation

- A spec cannot set both `llm.connection` and `harness`.
- A harness agent cannot have `sub_agents`.
- A remote agent (`remote`) cannot have `llm.connection`, `harness`, `skills`, `mcps`, `tools`, `sub_agents`,
  `memory`, `plugins` or `instructions`: those belong to the agent, which runs outside the hangar.
- In `remote.headers`, a credential header (a name with auth, token, key, secret, cookie or password) only takes a
  `secret://name` value, so the credential never sits in the spec.
- Sub-agents must exist, and an agent cannot list itself.
- Catalog references (skills, MCPs, connections) are resolved at deploy time; a missing one fails the deploy
  with a clear message.
