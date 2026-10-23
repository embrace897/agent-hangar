# Agent memory (Graphiti on Neo4j or FalkorDB, or Mem0 on pgvector)

Agents can keep **long-term memory** across conversations: customers, decisions, preferences, status. The memory
is a **temporal knowledge graph** built by [Graphiti](https://github.com/getzep/graphiti) (Apache-2.0):
- each fact has a validity window;
- when a new fact contradicts an old one, the old fact is **not deleted**. It is marked as replaced from the date
  of the change.

So the agent can answer both "what is ACME's plan today?" and "since when were they a customer?".

```text
agent ── /internal/memory/mcp ──► central ── MEMORY_TOKEN + groups ──► memory (Graphiti) ──► Neo4j | FalkorDB
          (agent token)            decides the groups                   extraction LLM, local embeddings
```

## Turning it on

```bash
sh scripts/setup.sh        # generates MEMORY_TOKEN, NEO4J_PASSWORD and FALKORDB_PASSWORD in .env (setup.ps1 on Windows)
```

Add to `.env`:

```bash
MEMORY_URL=http://memory:8000
MEMORY_LLM_CONNECTION=<name of an LLM connection with protocol openai>   # extracts entities and facts
```

```bash
docker compose --profile memory up -d --build
```

Then give an agent a `memory` block. You can do it in three places:
- the agent's **Memory** tab ("Ligar memória");
- the spec;
- the platform MCP, with `design_agent(slug, memory={"scope": "agent"})`.

```json
{"memory": {"scope": "agent", "write": true}}
```

| Field | Values | Meaning |
|---|---|---|
| `scope` | `agent` (default) | Only this agent |
| | `team` | Shared by the agents of the team that owns it |
| | `org` | Shared by the whole company |
| `write` | `true` (default) / `false` | `false` = the agent can only read |

The agent gets two tools. Say in its instructions when to use them.
- **`memory__recall(query, limit?, include_history?)`** is a hybrid search: semantic, BM25 and graph
  neighborhood, combined with RRF. It uses no LLM when reading. It returns:
  - dated **facts** (only current ones by default);
  - **entities** with a summary of what is known about them;
  - the original **sources**.
- **`memory__remember(content, about?)`** queues a new fact and returns immediately. A worker extracts entities
  and facts with the LLM (a few calls per episode, about 10 seconds) and invalidates the facts it contradicts.

## Isolation and security

- **The agent never chooses where it reads or writes.** The central derives the group from the spec:
  - `agent-<slug>`;
  - `team-<team slug>`;
  - `org-<id>`.

  It then sends the group to the memory service. A prompt injection can't reach another team's memory.
- **Stage vs production:**
  - Production reads and writes the base group.
  - Stage reads production's memory but writes to `<group>__stage`, so tests never pollute production.
  - The environment is proven by a per-environment token that the central injects into each container
    (`INTERNAL_ENV_TOKEN`). A stage container can't pass as production.
- **Only the central talks to the memory service,** through the `hangar_memory` network and `MEMORY_TOKEN`. The
  graph database sits on an internal network (`memory_db`) with no internet access.
- **The extraction LLM key stays encrypted in the central's database.** The memory service fetches it from
  `/internal/memory/config` with `MEMORY_TOKEN`.
- **Embeddings are local by default** (fastembed/ONNX, `paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions,
  baked into the image). No memory text leaves the network to become a vector. The extraction LLM is the only
  outbound call.
- **Specs that disagree:** if the running versions of an agent have different `memory` settings, the narrowest
  scope wins, and the agent writes only if every version allows writing.

## Administration

The agent's **Memory** tab:
- lists the facts of production or stage, showing whether each one is current or replaced, and since when;
- searches them;
- **deletes** the memory of a group (right to erasure, or resetting an agent).

| Action | Who |
|---|---|
| Read | Admin, auditor, team maintainer or developer |
| Delete agent or team memory | Admin or team maintainer |
| Delete org memory | Admin only |

Deleting an agent also deletes its `agent`-scope memory. Team and org memory stays with the other agents.

## Backends

| | Neo4j Community (default) | FalkorDB |
|---|---|---|
| Profile | `memory` | `memory-falkordb`, with `MEMORY_BACKEND=falkordb` |
| License | GPLv3 (runs as a separate service) | SSPL (not OSI-approved; check with your legal team) |
| Isolation | By `group_id`, enforced by the central | One graph per group (physical isolation) |
| Storage | Disk | RAM, with RDB persistence |
| Inspect | Neo4j Browser at http://localhost:7474 (localhost only) | `redis-cli GRAPH.LIST` |
| Grow | Neo4j Enterprise or Aura (HA, online backup, RBAC) without code changes | Replication |

Both were tested end to end with the same agent: record, recall in a new conversation, and invalidation of a
changed fact.

### Mem0 on pgvector (experimental)

A lighter backend: [Mem0](https://github.com/mem0ai/mem0) (Apache-2.0) keeps each fact as a vector in PostgreSQL
with [pgvector](https://github.com/pgvector/pgvector), with no graph database. When a new fact arrives, the LLM
decides whether it adds, updates or deletes an older one. Agents, tools, groups, stage vs production and the Memory
tab work the same.

```bash
MEMORY_BACKEND=mem0                          # in .env (scripts/setup.* generate MEM0_PG_PASSWORD)
docker compose --profile memory-mem0 up -d --build
```

The `memory-mem0` profile brings up its own Postgres with pgvector (`pgvector/pgvector:0.8.6-pg17`) on the internal
`memory_db` network. To use your company's Postgres instead (RDS, Cloud SQL and Azure Database all offer pgvector),
set `MEM0_PG_URL=postgresql://user:password@host:5432/db`. The central's own Postgres image doesn't include pgvector.

- **Groups:** the group the central imposes becomes Mem0's `user_id`, and every read and write filters by it. All
  groups share one table, as `group_id` does in Neo4j.
- **Embeddings:** the same local model (fastembed), or `MEMORY_EMBEDDER=openai`.
- **History:** each fact's change log is a SQLite file in the `memory_data` volume.
- **Telemetry:** Mem0's telemetry is off in the image (`MEM0_TELEMETRY=False`), as Graphiti's is.

Measured on the same install, with the same extraction LLM (DeepSeek V4.1 Flash through OpenRouter) and the same
three facts, then a plan change:

| | Graphiti + Neo4j | Mem0 + pgvector |
|---|---|---|
| Write 3 facts | 149 s | 10 s |
| Write one change | 18 s | 2 s |
| Search | 40–70 ms | 25–30 ms |
| Found "how many employees" | No | Yes |
| Plan changed from Pro to Enterprise | The old fact is marked as replaced, with the date | Both facts stay current |

Choose by what the agents need. Mem0 is much faster and cheaper to write and runs with no graph database. Graphiti
keeps the history of each fact with validity dates, which matters when an agent must answer "since when" or "what
was it before". `include_history` returns only facts with an expiration date that has passed in Mem0; there are no
entities or source excerpts in its results.

## Tuning

| Variable | Default | |
|---|---|---|
| `MEMORY_BACKEND` | `neo4j` | `falkordb` or `mem0` |
| `MEM0_PG_URL` | the `memory-mem0` Postgres | Mem0: any PostgreSQL with pgvector |
| `MEMORY_LLM_MODEL` | model of the connection | Extraction model (use one that is good at JSON) |
| `MEMORY_LLM_OUTPUT_MODE` | `json_object` | `json_schema` (constrained decoding) is more reliable where the provider supports it. It worked with DeepSeek through OpenRouter; `json_object` sometimes echoed the schema back |
| `MEMORY_EXTRACTION_RETRIES` | `3` | Retries per episode when the LLM returns invalid JSON |
| `MEMORY_EXTRACTION_INSTRUCTIONS` | built in | Extra guidance for extraction. The default turns plans, products, preferences and decisions into entities, and uses dates like "since January 2026" as the fact's start |
| `MEMORY_EMBEDDER` | `local` | `openai`: embeddings from `MEMORY_EMBEDDING_CONNECTION` / `MEMORY_EMBEDDING_MODEL`, with `MEMORY_EMBEDDING_DIM` |
| `NEO4J_HEAP`, `NEO4J_PAGECACHE` | `1G`, `512M` | Neo4j memory |

## Limits of this version (PoC)

- **The write queue is in memory.** Episodes still queued are lost if the memory service restarts. Writes are
  reported in `/api/memory/status` (`pending`, `processed`, `failed`, `last_error`).
- **Extraction cost is not counted in team budgets yet.** Each episode makes a few calls to the extraction LLM.
- **One extraction LLM per installation,** not per team.
- **Chat agents only.** Harness agents don't get memory yet.
- **Memory is per agent, team or org, not per end user.** A `user` scope is a natural next step.
- **Mem0 is experimental:** it is not in the Kubernetes chart or in `hangar setup` yet, and its results have no
  entities or source excerpts.
