# Contrato App Hangar ↔ Agent Hangar — Fase 1 (conexão, dispositivos, design)

Fonte única deste contrato (copiado para `central_agents/docs/app-hangar.md`). Mudanças só pelo chief.

Decisões do dono do produto: **login OAuth padrão com loopback (RFC 8252)**, **modo híbrido** (o app funciona sem
plataforma; política pode exigir), **provedor de LLM escolhido pela política** (Fase 3), **o código do projeto nunca
sobe** (só metadados, harness e artefatos publicados — fases futuras).

## 1. Autenticação (OAuth 2.1 já existente na plataforma, estendido)

Fluxo do app (cliente público nativo):

1. Descoberta: `GET {base}/.well-known/oauth-authorization-server`.
2. Registro dinâmico (RFC 7591), uma vez por plataforma (o app guarda o `client_id`):
   `POST {base}/oauth/register` com
   `{"client_name": "Hangar Desktop", "software_id": "hangar-desktop", "software_version": "<versão>",
     "redirect_uris": ["http://127.0.0.1/callback"], "grant_types": ["authorization_code", "refresh_token"],
     "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": "app mcp"}`.
3. Autorização: o app sobe um servidor HTTP efêmero em `127.0.0.1:<porta aleatória>` e abre no navegador do sistema
   `GET {base}/oauth/authorize?response_type=code&client_id=…&redirect_uri=http://127.0.0.1:<porta>/callback`
   `&state=…&code_challenge=…&code_challenge_method=S256&scope=app%20mcp&resource={base}`.
4. Troca: `POST {base}/oauth/token` (form) `grant_type=authorization_code&code=…&code_verifier=…&client_id=…`
   `&redirect_uri=…` → `{access_token, token_type, expires_in, refresh_token, scope}`. Refresh rotativo como hoje.
5. Saída: `POST {base}/oauth/revoke` (token) + limpeza local.

**Mudanças na plataforma:**
- **Loopback com porta livre** (RFC 8252 §7.3): para redirects em `127.0.0.1`/`[::1]`/`localhost`, a comparação entre
  o `redirect_uri` do pedido e o registrado ignora a porta (esquema, host e caminho continuam exatos).
- **Escopos `app` e `mcp`**: o grant guarda os escopos concedidos (migração). Pedido sem `scope` continua `mcp`
  (compatibilidade com Claude.ai/ChatGPT). Token com `app` vale nas rotas `/api/app/*` (exceto `/api/app/admin/*`);
  token com `mcp` vale no `/mcp`. A tela de consentimento mostra o que cada escopo permite.
- `software_id`/`software_version` do registro ficam guardados no cliente e aparecem para o admin.

## 2. API do app (`/api/app`, Bearer OAuth com escopo `app`)

Erros: JSON `{"detail": "..."}`; 401 = token inválido/expirado (o app faz refresh e repete uma vez);
403 = dispositivo revogado ou usuário sem acesso.

| Método e rota | Corpo | Resposta |
|---|---|---|
| `GET /api/app/me` | — | `{user:{id,email,name,avatar_url,org_role,teams:[{id,slug,name}]}, org:{id,name,slug,brand}, platform:{version,features:{}}, design_tokens:{version}}` |
| `POST /api/app/devices` | `{device_id (uuid gerado e guardado pelo app), name, os, os_version, app_version, channel}` | `{device:{id,device_id,name,os,app_version,status,created_at,last_seen_at}}` (idempotente por usuário+device_id) |
| `POST /api/app/devices/{device_id}/heartbeat` | `{app_version}` | `{status:"active"|"revoked"}` |
| `GET /api/app/design-tokens` | — | `{version, light:{token:valor…}, dark:{…}, fonts:{ui,sign,mono}, radius:{r,r_lg}}` |

`features` existe para o app saber o que a plataforma suporta nas próximas fases (Fase 1: objeto vazio).

## 3. Admin (`/api/app/admin`, sessão do console, `require_admin`)

| Método e rota | Resposta |
|---|---|
| `GET /api/app/admin/overview` | `{devices_active, members_connected, versions:[{version,count}], last_seen_at, platform_url}` |
| `GET /api/app/admin/devices` | `[{id,device_id,user:{id,email,name},name,os,os_version,app_version,channel,status,created_at,last_seen_at}]` |
| `POST /api/app/admin/devices/{id}/revoke` | `{ok:true}` — revoga também o grant OAuth do dispositivo |

Auditoria: `app.device.register`, `app.device.revoke`, `app.login`.

## 4. Modelo `AppDevice` (OrgScoped, migração Alembic)

`id, org_id, user_id, device_id (único por usuário), name, os, os_version, app_version, channel,
grant_id (OAuthGrant), status (active|revoked), created_at, last_seen_at, revoked_at, revoked_by`.
Desligar o usuário (inativo/SCIM) ou revogar o grant → dispositivos dele ficam `revoked`.

## 5. Console (`/ui`)

Novo hub em `HUBS_CONSOLE` (`static/member.js`): `{ id: 'app', tabs: [['app', 'Visão geral', 'admin'],
['app/devices', 'Dispositivos', 'admin']] }`, rótulo "App Hangar", com a mesma linguagem visual do console.
Visão geral: números, versões em uso, a URL da plataforma para colar no app e o passo a passo de conexão.

## 6. Design tokens

`central/app/static/design-tokens.json` é a fonte única dos tokens do console (cores claro/escuro, fontes, raios);
um teste garante que `style.css` e o JSON não divergem. Servido em `/api/app/design-tokens`. O app embute uma cópia
(Fase 1); a personalização por empresa (`Organization.brand`) chega pela política na Fase 3.

## 7. App (Hangar)

- Configuração: `platform.url` e `platform.mode` (`off | optional | required`). Política do Windows tem precedência:
  `HKLM\Software\Policies\Hangar` e depois `HKCU\Software\Policies\Hangar`, valores `PlatformUrl` (REG_SZ) e
  `Mode` (REG_SZ). `required` sem conexão → o app não cria sessões e mostra "Conectar à plataforma".
- Tokens cifrados com `safeStorage`; refresh automático; heartbeat a cada 15 min e ao iniciar; `revoked` → sai e avisa.
- UI: Configurações → **Plataforma** (URL, estado, usuário, empresa, dispositivo, conectar/desconectar); passo
  **Conectar à plataforma** no onboarding (pulável no modo híbrido); indicador de conexão na barra lateral.

## Desenvolvimento

Para testar o app de ponta a ponta sem tocar na instalação em uso (`agent-hangar-central-1`, o Postgres dela, o `.env`
e o `docker compose`), suba a central a partir do código numa instância separada: porta **8091**, SQLite numa pasta
temporária e login local ligado.

```powershell
# uma vez: o ambiente Python do repositório
python -m venv .venv; .venv\Scripts\pip install -r central\requirements.txt

$dir = "$env:TEMP\hangar-dev-8091"; New-Item -ItemType Directory -Force $dir | Out-Null
$env:DATABASE_URL = "sqlite:///$($dir -replace '\','/')/hangar.db"
$env:PUBLIC_BASE_URL = 'http://localhost:8091'
$env:LOCAL_LOGIN = '1'
$env:ADMIN_TOKEN = 'dev-admin-<algo aleatório>'
$env:HANGAR_SECRET_KEY = (.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
$env:INTERNAL_SECRET = (.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:INTERNAL_BASE_URL = 'http://localhost:8091'
$env:DOCKER_HOST = 'tcp://127.0.0.1:1'   # nunca o Docker do host: o startup remove containers de job que sobraram
$env:PLUGIN_EGRESS = 'open'               # sem o proxy de saída dos plugins (porta 3128)
$env:SCHEDULER_ENABLED = '0'; $env:GUIDE_AUTOGEN = '0'; $env:MODEL_CATALOG_URL = ''; $env:MCP_REGISTRY_URL = ''
cd central; ..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8091
```

As migrações rodam sozinhas na subida. O admin de teste é criado como o `hangar setup` faz (API com o `ADMIN_TOKEN`):

```powershell
$h = @{ Authorization = "Bearer $env:ADMIN_TOKEN" }
Invoke-RestMethod -Method Post http://localhost:8091/api/users -Headers $h -ContentType 'application/json' `
  -Body '{"username":"dev-admin","password":"<senha>","org_role":"admin","email":"dev-admin@hangar.test"}'
```

- `DOCKER_HOST` inválido é obrigatório numa máquina com o Docker da instalação em uso: sem ele, a subida da instância de
  desenvolvimento limparia os containers de job do host.
- `localhost` no Windows costuma resolver primeiro para `::1`; com `--host 127.0.0.1` só o IPv4 responde. Para o app
  (Node/Electron) não depender da ordem, sirva nos dois loopbacks com `uvicorn.Server(...).serve(sockets=[…])`
  (um socket em `127.0.0.1:8091` e outro em `[::1]:8091`) — é o que a instância da Fase 1 usa.
- Console: `http://localhost:8091/ui/` → **App Hangar** (visão geral e dispositivos). Consentimento OAuth:
  `http://localhost:8091/app/#/oauth`.
