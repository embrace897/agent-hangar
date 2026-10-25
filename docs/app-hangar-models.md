# Contrato App Hangar ↔ Agent Hangar — Modelos de token sincronizados

Fonte única (copiado para `central_agents/docs/app-hangar-models.md`). Depende da Fase 1 (`/api/app`, escopo `app`,
`AppDevice`). Mudanças só pelo chief.

## Objetivo

O admin decide, na página **Provedores de LLM** do console (`/ui`), quais conexões têm os modelos **sincronizados com o
Hangar desktop**. No app, cada pessoa escolhe por sessão entre os modelos da **assinatura** (login do Claude Code) e os
**modelos de token** da plataforma. **A chave da conexão nunca sai da plataforma**: o app fala com um proxy da plataforma,
que injeta a credencial do cofre, aplica as regras da empresa e registra uso e custo.

## Regras

- Só conexões com `protocol = "anthropic"` (API da Anthropic ou gateway compatível com `/v1/messages`, ex.: LiteLLM/
  OpenRouter no formato Anthropic) podem ser sincronizadas — é o único formato que o Claude Code fala. As outras aparecem
  no console com a opção desabilitada e a explicação.
- O Hangar manda código e saídas de terminal: se a empresa usa `code_policy = "approved"`, a conexão precisa de
  `allow_code = true` para ser sincronizada (o console explica e o proxy recusa).
- Só modelos `kind = "text"` e `available = true` da conexão. O admin pode **esconder** modelos específicos do app.
- Orçamento do time com `budget_enforce` estourado → o proxy responde 429 (mensagem em português).

## Plataforma

### Modelo de dados (migração)

- `LlmConnection.sync_desktop: bool = False` — o botão da página de provedores.
- `LlmConnection.desktop_hidden_models: list[str] = []` — modelos escondidos do app.
- Chave de LLM por dispositivo: `AppDevice.llm_key_hash`, `llm_key_prefix`, `llm_key_created_at` (só o hash; o valor
  aparece uma vez). Prefixo do valor: `ahl_`. Revogar o dispositivo, desligar a pessoa ou revogar o grant invalida a chave.

### Console (página Provedores de LLM, `static/llm.js`)

Em cada conexão (cartão e detalhe), um botão/alternador **"Sincronizar modelos de token com o Hangar desktop"**
(admin), com: estado, quantos modelos vão para o app, lista dos modelos com "mostrar/esconder no app", e o motivo
quando não pode (protocolo diferente de `anthropic`, ou `allow_code` exigido). Na aba App Hangar → Visão geral, um
resumo "Modelos sincronizados: N conexões, M modelos".

### API do app (`/api/app`, Bearer OAuth escopo `app`)

| Método e rota                               | Corpo | Resposta                                                                                                                                                                                                                                   |
| ------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET /api/app/models`                       | —     | `{version, connections:[{id, name, provider, base_path:"/api/app/llm/<id>", models:[{model_id, name, context_length, caps, price_in_per_mtok, price_out_per_mtok}]}]}` (só sincronizadas e permitidas; `version` muda quando a lista muda) |
| `POST /api/app/devices/{device_id}/llm-key` | —     | `{key:"ahl_…", prefix, created_at}` — gera ou **rotaciona** a chave do dispositivo (a anterior deixa de valer)                                                                                                                             |

### Proxy de LLM (`/api/app/llm/{connection_id}/…`)

- Autenticação: `Authorization: Bearer ahl_…` **ou** `x-api-key: ahl_…` (o Claude Code usa um dos dois). A chave
  identifica pessoa + dispositivo; dispositivo `revoked` ou pessoa inativa → 401.
- `POST /v1/messages` (com e sem `stream`), `POST /v1/messages/count_tokens`, `GET /v1/models` (só os modelos
  visíveis da conexão). Repassa corpo e cabeçalhos relevantes (`anthropic-version`, `anthropic-beta`) para
  `connection.base_url`, com a credencial do cofre no formato que a conexão usa; streaming SSE repassado sem buffer.
- Recusa modelo fora da lista visível (400 com a lista), conexão não sincronizada (403), regra de código (403) e
  orçamento (429).
- Registra `UsageEvent` (pessoa, time, dispositivo, conexão, modelo, tokens de entrada/saída/cache, custo pelo preço do
  modelo ou da conexão, `client = "hangar-desktop"`), contando no orçamento do time. Auditoria só de metadados (nunca o
  conteúdo).
- Limites: timeout longo para streaming; tamanho de corpo compatível com contexto grande.

## App (Hangar)

- O feature `platform` busca `GET /api/app/models` ao conectar, a cada 15 min e ao voltar do sleep; gera a chave
  `ahl_…` do dispositivo e a guarda cifrada (é segredo).
- Para cada conexão sincronizada, mantém um **perfil de provedor gerenciado** (`src/shared/providers.ts`):
  `kind: "gateway"`, `name: "Plataforma · <conexão>"`, `baseUrl: <plataforma>/api/app/llm/<id>`, `authStyle: "bearer"`,
  segredo = chave do dispositivo, `models` = modelos visíveis (o primeiro como padrão), `managedBy: "platform"`,
  `platformConnectionId`. Perfis gerenciados são **somente leitura** em Configurações → Provedores e somem quando a
  conexão deixa de ser sincronizada ou a pessoa desconecta (sessões que os usavam caem para a assinatura com aviso).
- **Escolha no composer**: um seletor único de modelo, agrupado em **Assinatura** (opus, sonnet, haiku…) e
  **Modelos de token · <conexão>** (os modelos sincronizados, com contexto e preço), e os demais perfis do usuário.
  Escolher grava `providerId` + `model` na sessão. Nova sessão e Quick Entry usam o mesmo seletor; Configurações →
  Geral permite definir o padrão.
- Custo exibido como "estimado" no app; o oficial é o da plataforma.

## Desenvolvimento

O que a plataforma implementa (branch `feat/app-hangar-models`):

- **Migração `0053_app_hangar_models`:** `llm_connections.sync_desktop` e `desktop_hidden_models`;
  `app_devices.llm_key_hash` (índice único), `llm_key_prefix`, `llm_key_created_at`; em `usage_events`, `agent_id`
  passa a aceitar nulo (uso sem agente) e entram `team_id`, `device_id`, `connection_id`, `client`, `tokens_cache_read`
  e `tokens_cache_write`.
- **Regras:** `services/app_llm.py`. **Rotas:** `routers/app_hangar.py` (`/api/app/models`, `.../llm-key`),
  `routers/app_llm.py` (o proxy) e `PATCH /api/catalog/llm/{name}/desktop` (admin: `{sync}` ou `{model_id, hidden}`,
  auditado como `llm_connection.desktop`; recusa com o motivo em 400). O middleware deixa `/api/app/llm/` passar: a rota
  confere a chave `ahl_…`.
- **Console:** em Provedores de LLM, cada conexão tem o bloco **Hangar desktop**, com o botão, o estado, o motivo quando
  não pode e a lista de modelos (mostrar/esconder no app). O cartão do provedor mostra "Hangar desktop" quando há uma
  conexão sincronizada. App Hangar → Visão geral: "Modelos sincronizados: N conexões, M modelos".

Decisões de implementação (detalhes e pedidos no relatório `m-platform.md` do app):

- **Chave do dispositivo:** `ahl_` + 43 caracteres aleatórios; o banco guarda só o sha256 e o prefixo (12 caracteres).
  Revogar o dispositivo (admin, saída, autorização encerrada, pessoa desligada) apaga o hash: depois de um login novo no
  mesmo computador o app precisa gerar outra chave. Cada chamada do proxy confere o dispositivo, a pessoa e a
  autorização OAuth dele.
- **Erros do proxy:** no formato da API da Anthropic (`{"type":"error","error":{"type","message"}}`, mais `detail`),
  que é o que o Claude Code mostra. 401 `authentication_error`, 403 `permission_error`, 400 `invalid_request_error`,
  404 `not_found_error`, 413 `request_too_large` (corpo acima de 32 MB), 429 `rate_limit_error`, 502 `api_error`.
- **Credencial:** `x-api-key` com a chave do cofre. Para conexões que não são a API oficial (provedor diferente de
  `anthropic`), vai também `Authorization: Bearer`, que é o que os gateways no formato Anthropic aceitam.
- **Repasse:** `anthropic-version` (padrão `2023-06-01`), `anthropic-beta` e a query string (o Claude Code chama
  `/v1/messages?beta=true`). Os outros cabeçalhos do app (cookies, a chave `ahl_`) não seguem. A resposta volta sem
  `set-cookie` e sem os cabeçalhos hop-by-hop. O SSE é repassado pedaço a pedaço (`aiter_bytes`), com timeout de
  leitura de 900 s.
- **Uso e custo:** um `UsageEvent` por `/v1/messages` (`env="app"`, `channel` e `client` = `hangar-desktop`,
  `protocol="anthropic"`), com os tokens de `message_start` e do último `message_delta`. O custo usa o preço do modelo
  pedido (`llm_models.prices_for`), com o cache no preço da Anthropic: escrita a 1,25x e leitura a 0,1x da entrada.
  `count_tokens` e `/v1/models` não registram uso nem conferem orçamento.
- **Orçamento:** o time que paga é o primeiro time da pessoa (menor id, como nos agentes pessoais). O proxy confere
  `Team.budget_enforce` (gasto do mês ≥ `budget_usd_month`) e as políticas com parada (`BudgetPolicy` da empresa e do
  time). O gasto do time passou a somar o uso direto das pessoas dele (`usage_events.team_id`).
- **`GET /v1/models[/{id}]`:** respondido pela plataforma com os modelos visíveis da conexão, sem chamar o provedor.

### Testar de ponta a ponta

Use a instância de desenvolvimento da Fase 1 (seção "Desenvolvimento" de `app-hangar.md`, porta 8091) e o servidor
Anthropic **falso** do app. Nunca use chaves reais nem conexões da instalação em uso.

```powershell
# o upstream falso (Node 24 roda o TypeScript direto), numa porta livre
cd C:\Users\valteresj\OneDrive\Documentos\Harness\hangar
node tests/mock-anthropic/cli.ts 8790

# conexão anthropic apontando para ele, com a lista de modelos buscada na hora, e a sincronização ligada
$h = @{ Authorization = "Bearer $env:ADMIN_TOKEN" }
Invoke-RestMethod -Method Post http://localhost:8091/api/catalog/llm -Headers $h -ContentType 'application/json' `
  -Body '{"name":"mock-anthropic","provider":"custom-anthropic","base_url":"http://127.0.0.1:8790","api_key":"mock-token","protocol":"anthropic"}'
Invoke-RestMethod -Method Patch http://localhost:8091/api/catalog/llm/mock-anthropic/desktop -Headers $h `
  -ContentType 'application/json' -Body '{"sync":true}'
```

Com o token OAuth do app (escopo `app`) e o dispositivo registrado, `POST /api/app/devices/<device_id>/llm-key`
devolve a chave. O Claude Code fala com o proxy assim:

```powershell
$env:ANTHROPIC_BASE_URL = 'http://localhost:8091/api/app/llm/<id da conexão>'
$env:ANTHROPIC_AUTH_TOKEN = '<ahl_…>'
$env:ANTHROPIC_MODEL = 'claude-sonnet-4-5'; $env:ANTHROPIC_SMALL_FAST_MODEL = 'claude-haiku-4-5'
claude -p "[[script:text]] oi"
```

O uso aparece em `usage_events` (`client = 'hangar-desktop'`).
