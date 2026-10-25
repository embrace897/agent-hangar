# Contrato M1 — Workspace no Hangar Desktop (Início, Meus agentes, eventos, deep links)

Parte do plano `docs/platform/portal-migration-plan.md` (cópia na plataforma: `docs/app-hangar-workspace.md`).
Decisões do dono do produto: política padrão do portal **completo**; seção **Agent Hangar na mesma sidebar** das
sessões; no Desktop o **dono pode testar e parar** agentes (M3). Mudanças neste contrato só pelo chief.

## 1. Token do app nas rotas do membro (plataforma)

- Uma dependência única marca as rotas do **membro** que aceitam o token OAuth com escopo `app`
  (ex.: `member_route(allow_app=True)`), sempre com o acesso da própria pessoa (times e papéis dela).
- **Lista da M1** (só leitura): `GET /api/me/workspace`, `GET /api/decisions`, `GET /api/org-chart` (apenas com
  `mine=1`), `GET /api/inbox` (lista e contagem). As fases seguintes ampliam a lista explicitamente.
- **Nunca** aceitam o token `app`: rotas de admin, auditor, configuração, chaves, segredos, OAuth/SSO, e qualquer
  rota fora da lista.
- O agente da plataforma documenta aqui, na seção 4, o formato da resposta de cada rota da lista (o que o Desktop
  pode ler), com exemplos.

## 2. Eventos (`GET /api/app/events`, SSE, Bearer `app`)

- `event: <tipo>` + `data: <json>`; `id:` crescente; o cliente reconecta com `Last-Event-ID`; comentário de
  keepalive a cada 25 s.
- Tipos da M1: `inbox.changed` `{unread}`, `decision.created` `{id, title, agent}`, `decision.decided` `{id}`,
  `agent.status` `{slug, status}`, `models.changed` `{version}`. Só eventos que a pessoa pode ver.
- Implementação simples e segura para várias réplicas (ex.: tabela/fila de eventos por pessoa com retenção curta,
  ou o barramento que a plataforma já tiver); documente a escolha.

## 3. Desktop

- Feature **`workspace`**: seção **Agent Hangar** na sidebar (acima das sessões; recolhível) com **Início** e
  **Meus agentes** (Decisões e Caixa de entrada aparecem com badge e abrem o portal no navegador até a M2).
- **Início**: cartões "Para você decidir", "Meus agentes", "Trabalhando agora", "Custo no mês", "Decisões" e
  "Vistos recentemente" (o equivalente nativo do Início do portal), com o tema da plataforma.
- **Meus agentes**: lista com status, tipo (agente / digital employee), último uso; detalhe abre o portal até a M3.
- Eventos SSE com reconexão (e fallback de polling de 60 s se o SSE cair); badges ao vivo.
- **Deep links `hangar://`**: `hangar://open/<rota>` (ex.: `hangar://open/workspace/home`,
  `hangar://open/workspace/agents`, `hangar://open/decisions/<id>` → abre o portal no navegador até a M2).
  Registro do protocolo em tempo de execução no main (Windows/Linux via `setAsDefaultProtocolClient` +
  `second-instance`, macOS via `open-url`) e, no instalador, a declaração do protocolo (`electron-builder.yml`,
  feita pelo agente release).
- Sem conexão com a plataforma: a seção mostra "Conecte à plataforma" (modo híbrido).

## 4. Respostas das rotas do membro (preenchido pelo agente da plataforma)

Exemplos reais da instância de desenvolvimento (`http://localhost:8091`, branch `feat/app-hangar-workspace`), com o
token do app (escopo `app mcp`) do `dev-membro`; listas encurtadas com `…`. Campos não citados podem aparecer e devem
ser ignorados pelo Desktop. Datas em ISO 8601; quando faltar o fuso (ex.: `my_tasks[].created_at`), é UTC.

**Autorização.** As quatro rotas respondem com o acesso da própria pessoa (times e papéis dela; uma pessoa que é admin
da empresa vê como admin). Recusas: `401` sem token ou com token vencido (renove e tente uma vez); `403` com
`{"error": "forbidden: token OAuth só vale em /mcp (escopo mcp), /api/app e nas rotas do membro liberadas para o app (escopo app)"}`
em qualquer rota fora da lista (ou com um token sem o escopo `app`); `403` com
`{"detail": "forbidden: pelo app Hangar, só o organograma da pessoa (mine=1)"}` em `/api/org-chart` sem `mine=1`.
Na plataforma, a lista é marcada com `member_route(allow_app=True)` (`central/app/routers/deps.py`), e um teste garante
que só estas quatro rotas têm a marca.

### 4.1 `GET /api/me/workspace` — o Início

| Campo                                                                                                    | Tipo                  | O que é                                                                                                                                     |
| -------------------------------------------------------------------------------------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `workspace`                                                                                              | string                | Nome da empresa                                                                                                                             |
| `llm_ready`                                                                                              | bool                  | A empresa tem pelo menos uma conexão de LLM                                                                                                 |
| `to_decide`                                                                                              | int                   | Decisões abertas **atribuídas** à pessoa (responsável ou substituta): "Para você decidir"                                                   |
| `agents`                                                                                                 | lista                 | Digital employees da pessoa (pessoais, gerenciados por ela ou de que ela é dona): "Meus agentes" e "Trabalhando agora"                      |
| `agents[].slug`, `name`, `title`, `mission`, `avatar_url`                                                | string                | Identidade; `avatar_url` é relativa à plataforma                                                                                            |
| `agents[].status`                                                                                        | string                | `draft`, `onboarding`, `probation`, `active`, `paused`… (o mesmo valor de `agent.status`)                                                   |
| `agents[].personal`                                                                                      | bool                  | Agente pessoal da pessoa ou Digital employee do time                                                                                        |
| `agents[].working_on`                                                                                    | `{id, title}` ou null | Tarefa em andamento: "Trabalhando agora"                                                                                                    |
| `agents[].open_decisions`, `overdue_tasks`, `cost_30d`, `tasks`                                          | int, float, objeto    | Contadores do cartão (`tasks`: status → quantidade)                                                                                         |
| `agents[].manager`, `owner`, `team`, `autonomy_level`, `runtime`, `can_manage`, `created_at`, `hired_at` |                       | Detalhes                                                                                                                                    |
| `built_agents`                                                                                           | lista                 | Agentes comuns (não Digital employees) da pessoa: `{slug, name, status, why: personal, maintainer ou creator, …}`, também em "Meus agentes" |
| `my_tasks`                                                                                               | lista                 | Últimas 8 tarefas que a pessoa pediu, `{id, title, status, created_at}`: "Vistos recentemente"                                              |
| `month_cost_usd`                                                                                         | float                 | Custo do mês dos agentes da pessoa: "Custo no mês"                                                                                          |
| `personal_spent_usd`, `personal_cap_usd`, `personal_allowed`, `personal_count`, `personal_max`           |                       | Regras dos agentes pessoais (cap 0 = sem limite)                                                                                            |

```json
{
  "llm_ready": true,
  "workspace": "Minha empresa",
  "to_decide": 1,
  "agents": [
    {
      "slug": "assistente-da-semana",
      "name": "Assistente da Semana",
      "title": "Assistente pessoal",
      "mission": "Organizar a semana do dev-membro: caixa de entrada, rascunhos e lembretes.",
      "status": "onboarding",
      "avatar_url": "/ui/avatars/concierge.webp",
      "autonomy_level": "intern",
      "team": 2,
      "manager": "Membro de teste",
      "owner": "Membro de teste",
      "backups": [],
      "tasks": { "draft": 3 },
      "open_decisions": 0,
      "overdue_tasks": 0,
      "cost_30d": 0.0,
      "hired_at": null,
      "shadow": false,
      "created_at": "2026-10-25T16:43:39.036542+00:00",
      "runtime": "hangar",
      "can_manage": true,
      "personal": true,
      "working_on": null
    },
    {
      "slug": "renata-renovacoes",
      "name": "Renata Renovações",
      "title": "Analista de renovações (dev)",
      "status": "onboarding",
      "avatar_url": "/ui/avatars/vendedora.webp",
      "manager": "Membro de teste",
      "owner": null,
      "tasks": { "draft": 3 },
      "open_decisions": 1,
      "personal": false,
      "working_on": null,
      "…": "…"
    }
  ],
  "built_agents": [],
  "my_tasks": [{ "id": 3, "title": "Teste 2", "status": "draft", "created_at": "2026-10-25T16:43:39.057317" }, "…"],
  "month_cost_usd": 0.0,
  "personal_spent_usd": 0.0,
  "personal_cap_usd": 0,
  "personal_allowed": true,
  "personal_count": 1,
  "personal_max": 5
}
```

### 4.2 `GET /api/decisions` — "Para você decidir" e "Decisões"

Lista (sem envelope) das decisões abertas que a pessoa pode tomar, as atribuídas a ela primeiro (`mine: true`).
`kind`: `approval`, `question`, `admission`, `notice`, `org_change`, `promote_personal`, `plan`, `review`… O título a
mostrar é `question` ou, se vazio, `task.title` ou `tool`. `employee` é o slug do agente. Decidir
(`POST /api/decisions/{id}`) fica para a M2; na M1, abra `#/decisions` no portal.

```json
[
  {
    "id": 1,
    "kind": "org_change",
    "employee": "renata-renovacoes",
    "employee_title": "Analista de renovações (dev)",
    "employee_name": "Renata Renovações",
    "task": null,
    "action_type": "prod_change",
    "mode": "approve",
    "tool": "",
    "tool_ref": "",
    "payload": {
      "change": "reports_to",
      "key": "reports_to:-",
      "source": "person",
      "to": "",
      "evidence": { "by": "admin-token" }
    },
    "rationale": "",
    "risk": 3,
    "reversible": false,
    "question": "admin-token propõe que Renata Renovações passe a reportar a ninguém. Dado de teste da M1: decida pelo portal ou pelo Desktop.",
    "assigned_to": "Membro de teste",
    "fallback_to": null,
    "escalated": false,
    "expires_at": null,
    "status": "open",
    "approvals": [],
    "decision": "",
    "edited_payload": null,
    "reason": "",
    "decided_by": "",
    "decided_at": null,
    "created_at": "2026-10-25T16:43:39.386055+00:00",
    "review": null,
    "mine": true
  }
]
```

### 4.3 `GET /api/org-chart?mine=1` — os agentes da pessoa no organograma

`nodes`: os Digital employees da pessoa, com modelo, conexão e harness (útil no detalhe de "Meus agentes");
`agents`: agentes comuns que aparecem no organograma; `me`: a pessoa.

```json
{
  "nodes": [
    {
      "slug": "assistente-da-semana",
      "name": "Assistente da Semana",
      "title": "Assistente pessoal",
      "status": "onboarding",
      "level": "intern",
      "team": "operacoes-dev",
      "manager": "Membro de teste",
      "reports_to": null,
      "model": "claude-haiku-4-5",
      "connection": "mock-anthropic",
      "harness": "hangar",
      "specialists": [],
      "personal": true,
      "owner": "Membro de teste",
      "can_manage": true,
      "working_on": null,
      "avatar_url": "/ui/avatars/concierge.webp"
    },
    { "slug": "renata-renovacoes", "model": "claude-sonnet-4-5", "personal": false, "owner": "", "…": "…" }
  ],
  "agents": [],
  "me": { "name": "Membro de teste", "email": "dev-membro@hangar.test", "avatar_url": "", "role": "Staff" }
}
```

### 4.4 `GET /api/inbox` — Caixa de entrada (lista e contagem)

`?view=` `all` (padrão), `unread`, `decisions`, `blocked`, `mine` ou `archived`. `counts` vem sempre (o badge usa
`counts.unread`, o mesmo número de `inbox.changed`). `items[].kind`: `decision`, `task`, `issue`, `budget` (e, para
admins, `llm_model`, `connector`, `model_suggestion` e `execution_suggestion`). `url` é a rota do portal (`#/…`): na M1
o Desktop abre `<plataforma>/app/<url>` no navegador. Ordem: não lidos primeiro, depois os mais recentes. Marcar como
lido (`POST /api/inbox/mark`) fica para a M2.

```json
{
  "view": "all",
  "counts": { "all": 4, "unread": 4, "decisions": 1, "blocked": 0 },
  "items": [
    {
      "key": "decision:1",
      "kind": "decision",
      "title": "admin-token propõe que Renata Renovações passe a reportar a ninguém. Dado de teste da M1: decida pelo portal ou pelo Desktop.",
      "at": "2026-10-25T16:43:39.386055+00:00",
      "url": "#/decisions",
      "unread": true,
      "archived": false,
      "employee": "renata-renovacoes",
      "decision_kind": "org_change",
      "mine": true,
      "task_id": null
    },
    {
      "key": "task:3",
      "kind": "task",
      "title": "Teste 2",
      "at": "2026-10-25T16:43:39.058504+00:00",
      "url": "#/tasks/3",
      "unread": true,
      "archived": false,
      "task_id": 3,
      "status": "draft",
      "employee": "assistente-da-semana",
      "employee_name": "Assistente da Semana",
      "requested": true,
      "blocked": false
    },
    "…"
  ]
}
```

### 4.5 `GET /api/app/events` — o que chega no stream

Cabeçalhos: `content-type: text/event-stream; charset=utf-8`, `cache-control: no-cache`, `x-accel-buffering: no`.
Ao conectar sem `Last-Event-ID` (o `id` da primeira mensagem é o cursor); aqui a pessoa marcou a decisão como lida e
depois como não lida no portal:

```text
retry: 5000

id: 12
event: inbox.changed
data: {"unread":4}

id: 13
event: inbox.changed
data: {"unread":3}

id: 14
event: inbox.changed
data: {"unread":4}

: keepalive
```

Reconectando com `Last-Event-ID: 0` (o que ainda está na retenção de 24 h, filtrado pela pessoa; várias mudanças da
caixa no mesmo lote viram um `inbox.changed` só):

```text
id: 2
event: agent.status
data: {"slug":"assistente-da-semana","status":"onboarding"}

id: 7
event: agent.status
data: {"slug":"renata-renovacoes","status":"onboarding"}

id: 11
event: decision.created
data: {"id":1,"title":"admin-token propõe que Renata Renovações passe a reportar a ninguém. Dado de teste da M1: decida pelo portal ou pelo Desktop.","agent":"renata-renovacoes"}

id: 14
event: inbox.changed
data: {"unread":4}
```

`decision.decided` é `{"id": 1}`, e `models.changed` é `{"version": "<a mesma de GET /api/app/models>"}`. Os ids não
são contíguos (cada pessoa só recebe o que pode ver). Uma mensagem sem `id:` é um evento que chegou atrasado (de uma
transação mais longa): trate normalmente, o cursor não muda. O stream fecha sozinho depois de 30 min, e na hora quando
a autorização acaba (o app reconecta e recebe 401).

**Várias réplicas (a escolha):** a mudança grava uma linha em `app_events` na mesma transação; cada stream lê a tabela
a partir do cursor a cada 2 s e decide na entrega, com o acesso atual da pessoa, se ela pode ver o evento. Não precisa
de infraestrutura nova (funciona com SQLite e Postgres). Detalhes em `central/app/services/app_events.py` e em
`docs/api.md`.
