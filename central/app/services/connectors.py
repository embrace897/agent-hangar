"""Conectores: servidores MCP para os agentes trabalharem com as ferramentas da empresa.

Quatro origens, uma tela (como o catálogo de plugins do ChatGPT):
- curados: servidores MCP oficiais de SaaS conhecidos (Notion, Linear, Atlassian, Stripe…), com o endereço publicado
  pelo fornecedor — o teste de conexão confirma;
- catálogo Docker MCP: 237 servidores que rodam em container (ativados pelo gateway) e ~30 remotos;
- registro MCP do GitHub (services/mcp_registry.py): centenas de remotos (OAuth, abertos ou com token) e pacotes
  npm/PyPI, já com o jeito mais simples de conectar cada um; o que já está nas origens acima não se repete;
- personalizados: qualquer servidor MCP remoto que o admin (ou o time, ou a pessoa) cadastrar.

Todo conector remoto passa pela PONTE da central (/internal/mcp-proxy/<nome>): o agente fala Streamable HTTP com a
central, e a central fala com o servidor (Streamable HTTP ou SSE legado) com a credencial do cofre — o agente nunca vê
a chave. Na ponte:
- só passam as ferramentas aprovadas; ferramenta nova ou com descrição/parâmetros alterados fica bloqueada até um admin
  aprovar (troca silenciosa é o vetor do "tool poisoning") e aparece na caixa de entrada;
- o admin desliga ferramentas e define o tipo de ação (leitura, escrita, envio, exclusão…), que vale nas alçadas dos
  Digital employees;
- cada conector tem escopo (empresa, time ou pessoal) e status (testado de tempos em tempos; 401 vira pedido de
  reautenticação).
"""
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import urllib.parse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import ActionCatalog, Agent, GatewayServer, IconCache, McpServer, now
from ..tenancy import current_org
from .access import Access, Forbidden
from .common import PlatformError, audit, iso, spec_of

log = logging.getLogger("hangar.connectors")
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,60}$")
TRANSPORTS = ("auto", "http", "sse")
AUTHS = ("none", "bearer", "headers", "oauth")
STATES = ("approved", "pending", "changed", "disabled")
ACTION_TYPES = ("read", "write_internal", "send_external", "publish", "delete", "financial")
MAX_ICON = 64 * 1024

# Seções da tela, na ordem (categorias do catálogo Docker mapeadas para elas)
SECTIONS = [("popular", "Populares"), ("productivity", "Produtividade"), ("communication", "Comunicação"),
            ("sales", "Vendas e CRM"), ("finance", "Finanças e pagamentos"), ("data", "Dados e análises"),
            ("dev", "Ferramentas para desenvolvedores"), ("ai", "IA e pesquisa"), ("monitoring", "Monitoramento e custos"),
            ("security", "Segurança"), ("other", "Outros")]
CATEGORY = {"productivity": "productivity", "documentation": "productivity", "knowledge": "productivity",
            "automation": "productivity", "communication": "communication", "messaging": "communication",
            "crm": "sales", "finance": "finance", "payments": "finance", "blockchain": "finance",
            "database": "data", "data-analytics": "data", "data-visualization": "data",
            "devops": "dev", "development": "dev", "developer-tools": "dev", "code": "dev", "tools": "dev",
            "ai": "ai", "ai-ml": "ai", "search": "ai", "monitoring": "monitoring", "cost-management": "monitoring",
            "security": "security"}

# Servidores MCP oficiais (endereço publicado pelo fornecedor). auth: oauth (a central faz o OAuth uma vez) ou bearer
# (chave/token do fornecedor no cofre). O botão Testar confirma que o endereço responde.
C = dict
CURATED = [
    C(id="notion", name="Notion", domain="notion.so", url="https://mcp.notion.com/mcp", auth="oauth", section="productivity",
      description="Páginas, bases de dados e documentos do Notion"),
    C(id="linear", name="Linear", domain="linear.app", url="https://mcp.linear.app/mcp", auth="oauth", section="dev",
      description="Issues, projetos e ciclos do Linear"),
    C(id="atlassian", name="Atlassian (Jira e Confluence)", domain="atlassian.com", url="https://mcp.atlassian.com/v1/sse",
      auth="oauth", transport="sse", section="productivity", description="Issues do Jira e páginas do Confluence"),
    C(id="asana", name="Asana", domain="asana.com", url="https://mcp.asana.com/sse", auth="oauth", transport="sse",
      section="productivity", description="Tarefas, projetos e prazos do Asana"),
    C(id="github", name="GitHub", domain="github.com", url="https://api.githubcopilot.com/mcp/", auth="bearer",
      section="dev", description="Repositórios, issues e pull requests (token pessoal ou de app)"),
    C(id="sentry", name="Sentry", domain="sentry.io", url="https://mcp.sentry.dev/mcp", auth="oauth", section="monitoring",
      description="Erros, problemas e desempenho das aplicações"),
    C(id="stripe", name="Stripe", domain="stripe.com", url="https://mcp.stripe.com", auth="bearer", section="finance",
      description="Pagamentos, clientes e assinaturas (use uma chave restrita)"),
    C(id="paypal", name="PayPal", domain="paypal.com", url="https://mcp.paypal.com/mcp", auth="oauth", section="finance",
      description="Faturas, pedidos e pagamentos do PayPal"),
    C(id="square", name="Square", domain="squareup.com", url="https://mcp.squareup.com/sse", auth="oauth", transport="sse",
      section="finance", description="Vendas, catálogo e clientes do Square"),
    C(id="supabase", name="Supabase", domain="supabase.com", url="https://mcp.supabase.com/mcp", auth="oauth", section="data",
      description="Bancos Postgres, tabelas e projetos do Supabase"),
    C(id="neon", name="Neon", domain="neon.tech", url="https://mcp.neon.tech/mcp", auth="oauth", section="data",
      description="Bancos Postgres serverless do Neon"),
    C(id="canva", name="Canva", domain="canva.com", url="https://mcp.canva.com/mcp", auth="oauth", section="productivity",
      description="Criar, revisar e editar designs"),
    C(id="figma", name="Figma", domain="figma.com", url="https://mcp.figma.com/mcp", auth="oauth", section="productivity",
      description="Arquivos de design e o contexto deles para código"),
    C(id="intercom", name="Intercom", domain="intercom.com", url="https://mcp.intercom.com/mcp", auth="oauth",
      section="communication", description="Conversas e contatos do atendimento"),
    C(id="monday", name="monday.com", domain="monday.com", url="https://mcp.monday.com/sse", auth="oauth", transport="sse",
      section="productivity", description="Quadros, itens e atualizações do monday"),
    C(id="webflow", name="Webflow", domain="webflow.com", url="https://mcp.webflow.com/sse", auth="oauth", transport="sse",
      section="productivity", description="Sites, páginas e coleções do Webflow"),
    C(id="vercel", name="Vercel", domain="vercel.com", url="https://mcp.vercel.com", auth="oauth", section="dev",
      description="Projetos, deploys e logs da Vercel"),
]
POPULAR = ("notion", "linear", "atlassian", "github", "stripe", "hubspot", "supabase", "sentry", "asana", "canva",
           "figma", "slack", "postgres", "playwright", "brave", "grafana")


# ------------------------------------------------------------------ ícones
# ícones do catálogo Docker cuja URL de origem quebrou (o fornecedor tirou a imagem do ar)
ICON_OVERRIDES = {"docker:gmail-mcp": "https://www.gstatic.com/images/branding/product/2x/gmail_2020q4_48dp.png"}


def favicon_of(url: str) -> str | None:
    host = urllib.parse.urlparse(url or "").hostname
    return f"https://www.google.com/s2/favicons?domain={host}&sz=128" if host else None


def icon_source(key: str) -> str | None:
    """De onde baixar o ícone (só origens conhecidas: nada de URL arbitrária vinda do navegador)."""
    if key in ICON_OVERRIDES:
        return ICON_OVERRIDES[key]
    kind, _, ref = key.partition(":")
    if kind == "curated":
        c = next((x for x in CURATED if x["id"] == ref), None)
        return f"https://www.google.com/s2/favicons?domain={c['domain']}&sz=128" if c else None
    if kind in ("docker", "remote"):
        from . import mcp_gateway
        src = (mcp_gateway.catalog() if kind == "docker" else mcp_gateway.remotes()).get(ref) or {}
        return src.get("icon") or None
    if kind == "github":  # o avatar da editora no GitHub (o registro informa), ou o ícone do site do servidor
        from . import mcp_registry
        it = mcp_registry.find(ref) or {}
        if str(it.get("avatar", "")).startswith("https://avatars.githubusercontent.com/"):
            return it["avatar"]
        return favicon_of((it.get("remote") or {}).get("url") or it.get("homepage") or "")
    if kind == "domain" and re.fullmatch(r"[a-z0-9.-]{3,120}", ref):
        return f"https://www.google.com/s2/favicons?domain={ref}&sz=128"
    return None


def ICON_GET(url: str):  # noqa: N802  (os testes trocam)
    return httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "AgentHangar/1.0"})


def get_icon(db: Session, key: str) -> tuple[str, bytes] | None:
    cache_key = ICON_OVERRIDES.get(key, key)  # a substituição tem cache próprio: trocar a URL não serve o ícone antigo
    row = db.get(IconCache, cache_key)
    if row is not None:
        return row.content_type, row.data
    url = icon_source(key)
    if not url:
        return None
    # a imagem do catálogo pode sumir na origem: aí vale o favicon do mesmo site (melhor que só a inicial)
    for candidate in dict.fromkeys(x for x in (url, favicon_of(url) if key.startswith(("docker:", "remote:")) else None) if x):
        try:
            r = ICON_GET(candidate)
        except httpx.HTTPError:
            continue
        ctype = r.headers.get("content-type", "").split(";")[0]
        if r.status_code == 200 and ctype.startswith("image/") and len(r.content) <= 256 * 1024:
            break
    else:
        return None
    db.merge(IconCache(key=cache_key, content_type=ctype, data=r.content, fetched_at=now()))
    db.commit()
    return ctype, r.content


def save_custom_icon(db: Session, org_id: int, name: str, data_url: str) -> str:
    """Ícone enviado pelo admin (PNG/JPEG/SVG/WebP em data URL, até 64 KB)."""
    m = re.fullmatch(r"data:(image/(?:png|jpeg|webp|svg\+xml));base64,([A-Za-z0-9+/=]+)", data_url or "")
    if not m:
        raise PlatformError("ícone: envie uma imagem PNG, JPEG, WebP ou SVG")
    raw = base64.b64decode(m.group(2))
    if len(raw) > MAX_ICON:
        raise PlatformError("ícone: até 64 KB")
    key = f"custom:{org_id}:{name}"
    db.merge(IconCache(key=key, content_type=m.group(1), data=raw, fetched_at=now()))
    return key


def icon_url(key: str) -> str:
    return f"/api/connectors/icon?key={urllib.parse.quote(key)}" if key else ""


# ------------------------------------------------------------------ escopo
def visible(acc: Access, m: McpServer) -> bool:
    if acc.p.is_admin:
        return True
    if m.owner_user_id:
        return m.owner_user_id == acc.p.user_id
    if m.team_id:
        return m.team_id in acc.teams
    return True


def can_use(agent: Agent, m: McpServer) -> bool:
    """O agente pode usar o conector? Pessoal: só agentes pessoais da pessoa. Do time: só agentes do time."""
    if m.owner_user_id:
        return agent.personal_owner_id == m.owner_user_id
    if m.team_id:
        return agent.team_id == m.team_id
    return True


def _can_manage(acc: Access, m: McpServer | None, team_id: int | None, personal: bool) -> None:
    if acc.p.is_admin:
        return
    if personal or (m is not None and m.owner_user_id):
        if m is not None and m.owner_user_id and m.owner_user_id != acc.p.user_id:
            raise Forbidden("conector pessoal de outra pessoa")
        if not acc.p.user_id:
            raise Forbidden("conector pessoal precisa de uma pessoa (entre com a sua conta)")
        return
    tid = team_id if m is None else m.team_id
    if tid and acc.teams.get(tid) == "maintainer":
        return
    raise Forbidden("só admins cadastram conectores da empresa; mantenedores, os do time; cada pessoa, os pessoais")


# ------------------------------------------------------------------ falar com o servidor MCP
def proxy_url(name: str) -> str:
    return f"{config.INTERNAL_BASE_URL}/internal/mcp-proxy/{name}"


def resolve_headers(db: Session, m_or_cfg, team_id: int | None = None) -> dict:
    """Headers para o servidor: valores secret:// vêm do cofre; OAuth usa o token renovado da central."""
    from . import remote_mcp, secrets_vault
    get = (lambda k, d=None: getattr(m_or_cfg, k, d)) if isinstance(m_or_cfg, McpServer) else m_or_cfg.get
    out = {}
    for h in get("headers") or []:
        v = h.get("value") or ""
        if v.startswith(secrets_vault.PREFIX):
            v = secrets_vault.resolve(v, team_id if team_id is not None else get("team_id"), f"conector {get('name')}", db)
        if h.get("name"):
            out[h["name"]] = v
    if get("auth") == "oauth":
        _, tok = remote_mcp.token(db, get("name"))
        out["Authorization"] = f"Bearer {tok}"
    if get("stdio"):  # servidor local: só a central fala com o container dele
        out["X-Runner-Token"] = runner_token(get("name"))
    return out


@contextlib.asynccontextmanager
async def session(url: str, transport: str, headers: dict, timeout: float = 60):
    """Sessão MCP com o servidor (Streamable HTTP ou SSE legado)."""
    from mcp import ClientSession
    if transport == "sse":
        from mcp.client.sse import sse_client
        async with sse_client(url, headers=headers, timeout=timeout, sse_read_timeout=max(timeout, 60)) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                yield s, init
    else:
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(url, headers=headers, timeout=timeout, sse_read_timeout=max(timeout, 60)) as (r, w, _):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                yield s, init


def _root(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:  # o anyio embrulha os erros das tarefas
        e = e.exceptions[0]
    return e


def explain(e: BaseException) -> tuple[str, str]:
    """(status, mensagem clara) para o admin."""
    e = _root(e)
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 401:
            www = e.response.headers.get("www-authenticate", "")
            return "auth", ("o servidor pede autenticação (401)" + (" — ele usa OAuth: escolha OAuth na autenticação"
                                                                   if "resource_metadata" in www or "Bearer" in www else ""))
        if code == 403:
            return "auth", "a credencial não tem permissão (403)"
        if code == 410:
            return "error", "HTTP 410: o fornecedor desativou este endereço — confira a URL atual na documentação dele"
        if code in (404, 405):
            return "error", f"HTTP {code}: não há servidor MCP neste endereço (ou o transporte é outro)"
        return "error", f"HTTP {code} do servidor"
    if isinstance(e, httpx.TimeoutException | TimeoutError | asyncio.TimeoutError):
        return "error", "o servidor não respondeu a tempo (timeout)"
    if isinstance(e, httpx.ConnectError):
        return "error", "não consegui conectar (endereço, DNS ou rede)"
    if isinstance(e, PlatformError):
        return "error", str(e)
    name = type(e).__name__
    if name == "McpError":
        return "error", f"erro do protocolo MCP: {e}"
    return "error", f"{name}: {str(e)[:200]}"


def _tool_hash(t) -> str:
    raw = json.dumps({"n": t.name, "d": t.description or "", "s": t.inputSchema or {}}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _probe(url: str, transport: str, headers: dict, timeout: float) -> dict:
    async with session(url, transport, headers, timeout) as (s, init):
        caps = init.capabilities
        out = {"protocol_version": str(init.protocolVersion), "server": {"name": init.serverInfo.name,
                                                                          "version": init.serverInfo.version},
               "capabilities": {k: bool(getattr(caps, k, None)) for k in ("tools", "resources", "prompts", "logging",
                                                                            "completions", "experimental")},
               "tools": [], "resources": 0, "resource_templates": 0, "prompts": 0}
        if caps.tools is not None:
            tools = (await s.list_tools()).tools
            out["tools"] = [{"name": t.name, "description": (t.description or "")[:500], "hash": _tool_hash(t),
                             "read_only": bool(getattr(getattr(t, "annotations", None), "readOnlyHint", False)),
                             "destructive": bool(getattr(getattr(t, "annotations", None), "destructiveHint", False))}
                            for t in tools]
        for key, fn in (("resources", "list_resources"), ("resource_templates", "list_resource_templates"),
                        ("prompts", "list_prompts")):
            if (key == "prompts" and caps.prompts is None) or (key != "prompts" and caps.resources is None):
                continue
            with contextlib.suppress(Exception):
                res = await getattr(s, fn)()
                out[key] = len(getattr(res, {"resources": "resources", "resource_templates": "resourceTemplates",
                                             "prompts": "prompts"}[key]))
        return out


def probe(url: str, transport: str, headers: dict, timeout: float = 30) -> dict:
    """Testa a conexão: handshake, versão do protocolo, capacidades, ferramentas, resources e prompts. Transporte
    "auto": tenta Streamable HTTP e, se não houver servidor MCP ali, o SSE legado."""
    order = ["http", "sse"] if transport == "auto" else [transport]
    last = None
    for tr in order:
        try:
            res = asyncio.run(asyncio.wait_for(_probe(url, tr, headers, timeout), timeout + 15))
            return {"ok": True, "transport": tr, **res}
        except BaseException as e:  # noqa: BLE001 — o motivo vira mensagem para o admin
            if isinstance(e, KeyboardInterrupt | SystemExit):
                raise
            last = e
            status, _ = explain(e)
            if status == "auth":
                break  # o servidor existe: o problema é a credencial, outro transporte não muda nada
    status, msg = explain(last)
    return {"ok": False, "status": status, "error": msg, "transport": order[-1] if len(order) == 1 else "auto"}


# ------------------------------------------------------------------ governança das ferramentas
def snapshot(tools: list[dict], approve: set[str] | None = None) -> list[dict]:
    """Primeira lista (no cadastro): as escolhidas pelo admin entram aprovadas; as outras, desligadas."""
    approve = {t["name"] for t in tools} if approve is None else approve
    return [{"name": t["name"], "description": t.get("description", ""), "hash": t["hash"],
             "state": "approved" if t["name"] in approve else "disabled"} for t in tools]


def reconcile(m: McpServer, live: list[dict]) -> tuple[set[str], bool]:
    """Compara o que o servidor mostra agora com o aprovado. Devolve (ferramentas liberadas, mudou algo?).
    Nova → pendente; descrição/parâmetros diferentes do aprovado → alterada (bloqueada até aprovar)."""
    known = {t["name"]: dict(t) for t in (m.tools or [])}
    allowed, changed = set(), False
    for t in live:
        k = known.get(t["name"])
        if k is None:
            known[t["name"]] = {"name": t["name"], "description": t.get("description", ""), "hash": t["hash"], "state": "pending"}
            changed = True
            continue
        if k["state"] == "approved" and k["hash"] != t["hash"]:
            k.update(state="changed", new_hash=t["hash"], new_description=t.get("description", ""))
            changed = True
            continue
        if k["state"] == "approved":
            allowed.add(t["name"])
    if changed:
        m.tools = list(known.values())
    return allowed, changed


def review_tool(db: Session, acc: Access, name: str, tool: str, state: str | None = None,
                action_type: str | None = None) -> dict:
    """O admin aprova/desliga uma ferramenta e define o tipo de ação (vale nas alçadas dos Digital employees)."""
    m = _get(db, name)
    _can_manage(acc, m, None, False)
    tools = [dict(t) for t in (m.tools or [])]
    t = next((x for x in tools if x["name"] == tool), None)
    if t is None:
        raise PlatformError(f"o conector '{name}' não tem a ferramenta '{tool}'")
    if state is not None:
        if state not in ("approved", "disabled"):
            raise PlatformError("state: approved | disabled")
        if state == "approved" and t.get("new_hash"):  # aprovar a versão nova da descrição
            t["hash"], t["description"] = t.pop("new_hash"), t.pop("new_description", t["description"])
        t["state"] = state
    if action_type is not None:
        if action_type not in ACTION_TYPES:
            raise PlatformError(f"tipo de ação: {' | '.join(ACTION_TYPES)}")
        from .employee_gate import RISK
        ref = f"mcp:{name}:{tool}"
        row = db.scalar(select(ActionCatalog).where(ActionCatalog.tool_ref == ref)) or ActionCatalog(tool_ref=ref)
        row.action_type, row.risk, row.classified_by, row.updated_at = action_type, RISK[action_type], acc.p.name, now()
        db.add(row)
    m.tools = tools
    db.commit()
    audit(db, acc.p.name, "connector.tool", name, f"{tool}: {state or ''} {action_type or ''}".strip())
    return connector_dict(db, m, acc)


# ------------------------------------------------------------------ cadastro
def _get(db: Session, name: str) -> McpServer:
    m = db.scalar(select(McpServer).where(McpServer.name == name))
    if m is None:
        raise PlatformError(f"conector '{name}' não encontrado")
    return m


def _store_secrets(db: Session, acc: Access, name: str, headers: list[dict], team_id: int | None) -> list[dict]:
    """Valores marcados como segredo vão para o cofre; no conector fica só a referência secret://."""
    from . import secrets_vault
    out = []
    for h in headers or []:
        hn, val = (h.get("name") or "").strip(), h.get("value") or ""
        if not hn:
            continue
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", hn):
            raise PlatformError(f"header inválido: {hn}")
        if h.get("secret") and val and not val.startswith(secrets_vault.PREFIX):
            ref = f"mcp-{name}-{hn.lower()}"[:79].replace("_", "-")
            team = None
            if team_id:
                from ..models import Team
                tm = db.get(Team, team_id)
                team = tm.slug if tm else None
            secrets_vault.put(db, acc, ref, val, team=team, description=f"conector {name}: header {hn}")
            val = secrets_vault.PREFIX + ref
        out.append({"name": hn, "value": val})
    return out


def headers_for(auth: str, token: str = "", headers: list[dict] | None = None) -> list[dict]:
    if auth == "bearer":
        return [{"name": "Authorization", "value": token if token.startswith("secret://") else f"Bearer {token}",
                 "secret": not token.startswith("secret://")}]
    return headers or []


# ------------------------------------------------------------------ servidores locais (stdio) em container isolado
RUNNERS = ("npx", "uvx")
PKG_NPM = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
PKG_PY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,_-]+\])?==\d+(?:\.\d+)*(?:[A-Za-z0-9.+-]*)$")
HOST = re.compile(r"^(\*\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(:\d{1,5})?$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
REGISTRY_HOSTS = {"npx": ["registry.npmjs.org"], "uvx": ["pypi.org", "files.pythonhosted.org"]}
SECRET_ENV = re.compile(r"token|key|secret|password|passwd|credential|auth", re.I)


def stdio_spec(raw: dict) -> dict:
    """Valida o servidor local. Versão fixa é obrigatória (o pacote não muda sem você saber); sem shell: o comando
    é uma lista, e os argumentos vão como estão."""
    raw = raw or {}
    runner = raw.get("runner") or ""
    if runner not in RUNNERS:
        raise PlatformError("servidor local: escolha npx (Node) ou uvx (Python)")
    pkg = (raw.get("package") or "").strip()
    if runner == "npx" and not PKG_NPM.match(pkg):
        raise PlatformError("pacote npm com versão fixa: nome@1.2.3 (ex.: @modelcontextprotocol/server-filesystem@2025.8.21)")
    if runner == "uvx":
        pkg = re.sub(r"^([A-Za-z0-9._\[\],-]+)@(\d)", r"\1==\2", pkg)  # aceita nome@1.2.3 também
        if not PKG_PY.match(pkg):
            raise PlatformError("pacote Python com versão fixa: nome==1.2.3 (ex.: mcp-server-fetch==2025.4.7)")
    entry = (raw.get("entry") or "").strip() or (re.split(r"[\[=@]", pkg)[0] if runner == "uvx" else "")
    if entry and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", entry):
        raise PlatformError("comando do pacote inválido")
    args = [str(a) for a in (raw.get("args") or []) if str(a).strip() != ""]
    if len(args) > 40 or any(len(a) > 400 or "\n" in a for a in args):
        raise PlatformError("argumentos: até 40, uma linha cada")
    env = []
    for e in raw.get("env") or []:
        n = (e.get("name") or "").strip()
        if not n:
            continue
        if not ENV_NAME.match(n) or n.upper() in ("PATH", "HOME", "RUNNER_TOKEN", "MCP_COMMAND", "MCP_SERVER_ENV") \
                or n.upper().endswith("_PROXY"):
            raise PlatformError(f"variável de ambiente inválida ou reservada: {n}")
        env.append({"name": n, "value": str(e.get("value") or ""), "secret": bool(e.get("secret"))})
    egress = sorted({h.strip().lower() for h in raw.get("egress") or [] if h and h.strip()})
    bad = [h for h in egress if not HOST.match(h)]
    if bad or len(egress) > 30:
        raise PlatformError(f"domínios liberados inválidos: {', '.join(bad[:5]) or 'até 30'}")
    return {"runner": runner, "package": pkg, "entry": entry, "args": args, "env": env, "egress": egress}


def stdio_command(spec: dict) -> list[str]:
    if spec["runner"] == "npx":
        return ["npx", "-y", spec["package"], *spec["args"]]
    return ["uvx", "--from", spec["package"], spec["entry"], *spec["args"]]


def runner_name(name: str) -> str:
    return f"mcp-stdio-{name}"[:63]


def runner_token(name: str) -> str:
    return hmac.new(config.INTERNAL_SECRET.encode(), f"stdio-runner:{name}".encode(), hashlib.sha256).hexdigest()[:40]


def runner_url(name: str, path: str = "/mcp") -> str:
    from .. import deploy
    return deploy.plugin_url(runner_name(name), 8000, path)


def egress_patterns(connector_id: int) -> list[str] | None:
    """O proxy de saída pergunta: o registro do pacote e os domínios que o admin liberou (None = conector removido)."""
    from ..db import SessionLocal
    with SessionLocal() as db:
        m = db.get(McpServer, connector_id)
        if m is None or not m.stdio:
            return None
        return REGISTRY_HOSTS.get(m.stdio.get("runner"), []) + list(m.stdio.get("egress") or [])


def note_denied(connector_id: int, target: str, why: str):
    from ..db import SessionLocal
    with SessionLocal() as db:
        m = db.get(McpServer, connector_id)
        if m is None:
            return
        info = dict(m.server_info or {})
        info["egress_denied"] = {"host": target, "why": why, "at": iso(now()), "count": int((info.get("egress_denied") or {}).get("count", 0)) + 1}
        m.server_info = info
        db.commit()


def _store_env(db: Session, acc: Access, name: str, env: list[dict], team_id: int | None) -> list[dict]:
    """Variáveis marcadas como segredo vão para o cofre; no conector fica só secret://nome."""
    hdrs = _store_secrets(db, acc, f"{name}-env", [{"name": e["name"].replace("_", "-"), "value": e["value"],
                                                    "secret": e["secret"]} for e in env], team_id)
    return [{"name": e["name"], "value": h["value"]} for e, h in zip(env, hdrs, strict=True)]


def _env_values(db: Session, env: list[dict], team_id: int | None, who: str) -> dict:
    from . import secrets_vault
    out = {}
    for e in env:
        v = e.get("value") or ""
        out[e["name"]] = secrets_vault.resolve(v, team_id, who, db) if v.startswith(secrets_vault.PREFIX) else v
    return out


def _start(name: str, spec: dict, env: dict, egress_key: str):
    from .. import deploy, egress
    environment = {"MCP_COMMAND": json.dumps(stdio_command(spec)), "MCP_SERVER_ENV": json.dumps(env),
                   "RUNNER_TOKEN": runner_token(name), **egress.env(egress_key)}
    deploy.run_plugin(runner_name(name), config.MCP_STDIO_IMAGE, environment, config.MCP_STDIO_MEM, config.MCP_STDIO_CPUS,
                      labels={"central.connector": name}, tmp=config.MCP_STDIO_TMP)


def HEALTH_GET(url: str):  # noqa: N802  (os testes trocam)
    return httpx.get(url, timeout=5)


def wait_runner(name: str, timeout: float = 150) -> tuple[bool, str]:
    """Espera o container instalar o pacote e o servidor responder (npx/uvx baixam na primeira vez)."""
    end, last = time.monotonic() + timeout, "subindo o container"
    while time.monotonic() < end:
        try:
            r = HEALTH_GET(runner_url(name, "/health"))
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and j.get("ok"):
                return True, ""
            if j.get("error"):
                return False, f"o servidor não subiu: {j['error']}"
            last = "instalando o pacote"
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(3)
    return False, f"o servidor não respondeu em {int(timeout)} s ({last})"


def ensure_runner(db: Session, m: McpServer) -> tuple[bool, str]:
    """O container do conector está de pé? Se não estiver, sobe (com as variáveis do cofre) e espera."""
    from .. import deploy
    if deploy.plugin_state(runner_name(m.name)) != "running":
        _start(m.name, m.stdio, _env_values(db, m.stdio.get("env") or [], m.team_id, f"conector {m.name}"), f"c{m.id}")
    return wait_runner(m.name)


def test_stdio(db: Session, acc: Access, body: dict) -> dict:
    """Testa um servidor local num container temporário (some no fim). Leva o tempo de instalar o pacote."""
    from .. import deploy, shared
    if not acc.p.is_admin:
        raise Forbidden("servidores locais (stdio) executam código: só admins cadastram")
    spec = stdio_spec(body.get("stdio"))
    tag = secrets.token_hex(3)
    name, key = f"test-{tag}", f"t{tag}"
    shared.put(f"egress-test:{key}", {"patterns": REGISTRY_HOSTS[spec["runner"]] + spec["egress"]}, 900)
    env = {e["name"]: e["value"] for e in spec["env"]}
    try:
        _start(name, spec, env, key)
        ok, why = wait_runner(name, float(body.get("timeout_s") or 150))
        if not ok:
            return {"ok": False, "status": "error", "error": why, "transport": "stdio"}
        res = probe(runner_url(name), "http", {"X-Runner-Token": runner_token(name)}, 60)
        return {**res, "transport": "stdio"}
    finally:
        with contextlib.suppress(Exception):
            deploy.stop_plugin(runner_name(name))
        shared.pop(f"egress-test:{key}")


def test_config(db: Session, acc: Access, body: dict) -> dict:
    """Testar conexão antes de salvar (nada é gravado; segredos digitados não vão para o cofre ainda)."""
    from . import secrets_vault
    if body.get("stdio"):
        return test_stdio(db, acc, body)
    url = (body.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        raise PlatformError("a URL do servidor MCP deve ser http(s)")
    auth = body.get("auth") or "none"
    if auth not in AUTHS:
        raise PlatformError(f"autenticação: {' | '.join(AUTHS)}")
    if auth == "oauth":
        from . import remote_mcp
        try:
            meta = remote_mcp.discover(url)
        except PlatformError as e:
            return {"ok": False, "status": "error", "error": f"OAuth não encontrado neste servidor: {e}"}
        return {"ok": True, "oauth": {"issuer": meta.get("issuer", ""), "scope": meta.get("scope", ""),
                                      "registration": bool(meta.get("registration_endpoint"))},
                "note": "o servidor usa OAuth: ao criar, você autoriza no navegador e a central lista as ferramentas"}
    hdrs = {}
    for h in headers_for(auth, body.get("token") or "", body.get("headers")):
        v = h.get("value") or ""
        if v.startswith(secrets_vault.PREFIX):
            v = secrets_vault.resolve(v, None, "teste de conector", db)
        hdrs[h["name"]] = v
    return probe(url, body.get("transport") or "auto", hdrs, float(body.get("timeout_s") or 30))


def create(db: Session, acc: Access, body: dict) -> dict:
    """Cadastra o conector (personalizado, curado ou remoto do catálogo). Precisa do aceite do risco. Com OAuth, devolve
    a URL de autorização; nos outros, testa e grava a lista de ferramentas aprovadas."""
    from . import remote_mcp
    from .catalog import upsert_mcp
    from .common import slugify
    name = (body.get("name") or "").strip().lower()
    name = name if NAME.match(name) else slugify(name)[:60]
    if not NAME.match(name or ""):
        raise PlatformError("nome: 2 a 61 caracteres (letras minúsculas, números, ponto, _ ou -)")
    if not body.get("accept_risk"):
        raise PlatformError("confirme que entende os riscos de um servidor MCP personalizado")
    spec = stdio_spec(body["stdio"]) if body.get("stdio") else None
    if spec is not None and not acc.p.is_admin:
        raise Forbidden("servidores locais (stdio) executam código: só admins cadastram")
    url = runner_url(name) if spec else (body.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        raise PlatformError("a URL do servidor MCP deve ser http(s)")
    auth = "none" if spec else (body.get("auth") or "none")
    transport = "http" if spec else (body.get("transport") or "auto")
    if auth not in AUTHS or transport not in TRANSPORTS:
        raise PlatformError("autenticação ou transporte inválido")
    scope = body.get("scope") or "org"
    team_id = None
    if scope == "team":
        from .access import find_team
        team_id = find_team(db, body.get("team") or "").id
    personal = scope == "personal"
    existing = db.scalar(select(McpServer).where(McpServer.name == name))
    if existing is not None and existing.source in ("manual", "docker"):
        raise PlatformError(f"já existe um MCP '{name}' no catálogo")
    _can_manage(acc, existing, team_id, personal)
    headers = [] if spec else _store_secrets(db, acc, name, headers_for(auth, body.get("token") or "", body.get("headers")), team_id)
    if spec:
        spec["env"] = _store_env(db, acc, name, spec["env"], team_id)
    m = existing or McpServer(name=name)
    m.stdio = spec
    m.description = (body.get("description") or "")[:1000]
    m.source = body.get("source") if body.get("source") in ("custom", "curated", "remote", "github") else "custom"
    m.upstream_url, m.url, m.auth, m.headers = url, proxy_url(name), auth, headers
    m.timeout_s = max(5, min(int(body.get("timeout_s") or 60), 600))
    m.category = body.get("category") or m.category or ""
    m.team_id, m.owner_user_id = team_id, (acc.p.user_id if personal else None)
    m.created_by = m.created_by or acc.p.name
    if body.get("icon_data"):
        m.icon = save_custom_icon(db, m.org_id or current_org(), name, body["icon_data"])
    elif body.get("icon"):
        m.icon = body["icon"] if icon_source(body["icon"]) else ""
    elif spec is None:
        host = urllib.parse.urlparse(url).hostname or ""
        m.icon = m.icon or (f"domain:{'.'.join(host.split('.')[-2:])}" if "." in host else "")
    elif spec["runner"] == "npx":
        m.icon = m.icon or "domain:npmjs.com"
    else:
        m.icon = m.icon or "domain:pypi.org"
    m.transport = transport if transport != "auto" else "http"
    db.add(m)
    db.commit()
    upsert_mcp(db, name, m.url, m.description, acc.p.name)  # mantém o item do catálogo coerente (url = a ponte)
    if auth == "oauth":
        r = remote_mcp.start(db, acc, name, url, m.description)
        m.status, m.status_detail = "auth", "autorize no navegador para concluir"
        db.commit()
        audit(db, acc.p.name, "connector.create", name, f"{url} · oauth")
        return {**connector_dict(db, m, acc), "authorize_url": r["authorize_url"], "oauth_cookie": r["cookie"]}
    if spec:  # sobe o container (instala o pacote) antes de falar com o servidor
        ok, why = ensure_runner(db, m)
        res = probe(url, "http", resolve_headers(db, m), float(m.timeout_s)) if ok else {"ok": False, "status": "error", "error": why}
    else:
        res = probe(url, transport, resolve_headers(db, m), float(m.timeout_s))
    m.checked_at = now()
    if res["ok"]:
        m.transport = res["transport"]
        approve = set(body["tools"]) if isinstance(body.get("tools"), list) else None
        m.tools = snapshot(res["tools"], approve)
        m.server_info = {**{k: v for k, v in (m.server_info or {}).items() if k == "egress_denied"},  # o histórico do proxy fica
                         **{k: res.get(k) for k in ("protocol_version", "server", "capabilities", "resources",
                                                    "resource_templates", "prompts")}}
        m.status, m.status_detail = "ok", ""
        for t in res["tools"]:  # dica do próprio servidor (readOnlyHint) vira a classificação inicial
            if t.get("read_only"):
                ref = f"mcp:{name}:{t['name']}"
                if db.scalar(select(ActionCatalog).where(ActionCatalog.tool_ref == ref)) is None:
                    db.add(ActionCatalog(tool_ref=ref, action_type="read", risk=1, classified_by="auto:rule", updated_at=now()))
    else:
        m.status, m.status_detail = res["status"], res["error"]
    db.commit()
    if spec:
        m.transport = "http"
        db.commit()
    audit(db, acc.p.name, "connector.create", name, (f"stdio {spec['runner']} {spec['package']}" if spec else f"{url} · {m.transport} · {auth}")
          + f" · {m.status}")
    return connector_dict(db, m, acc)


def oauth_done(db: Session, name: str):
    """Depois do OAuth: o item do catálogo continua apontando para a ponte, e a lista de ferramentas é lida."""
    m = db.scalar(select(McpServer).where(McpServer.name == name))
    if m is None or m.auth != "oauth":
        return
    m.url = proxy_url(name)
    check(db, m, first=True)


def remove(db: Session, acc: Access, name: str):
    from . import remote_mcp
    m = _get(db, name)
    _can_manage(acc, m, None, False)
    users = [a.slug for a in db.scalars(select(Agent)) if name in {(x if isinstance(x, str) else x.get("name"))
                                                                    for x in (spec_of(a).get("mcps") or [])}]
    if users:
        raise PlatformError(f"conector em uso por: {', '.join(users)}. Tire dos agentes antes.")
    if m.auth == "oauth":
        with contextlib.suppress(Exception):
            remote_mcp.remove(db, acc, name)
    if m.stdio:
        from .. import deploy
        with contextlib.suppress(Exception):
            deploy.stop_plugin(runner_name(name))
    db.delete(m)
    db.commit()
    audit(db, acc.p.name, "connector.remove", name)


# ------------------------------------------------------------------ status (de tempos em tempos)
def _check_gateway(db: Session, m: McpServer) -> dict:
    """Conector do catálogo Docker (`docker:<nome>`): não tem endereço próprio, quem serve é o gateway. Está ok se o
    gateway responde e expõe as ferramentas dele (prefixo `<nome>__`)."""
    from .. import config
    name = m.name[len("docker:"):]
    res = probe(config.MCP_GATEWAY_URL, "http", {}, float(m.timeout_s or 60))
    m.checked_at = now()
    if not res["ok"]:
        m.status, m.status_detail = res["status"], f"gateway do catálogo Docker: {res['error']}"
    elif not any(t["name"].startswith(f"{name}__") for t in res["tools"]):
        res = {"ok": False, "status": "error", "error": f"o gateway está no ar, mas '{name}' não subiu: confira os "
                                                       "segredos e a configuração (docker compose logs mcp-gateway-core)"}
        m.status, m.status_detail = "error", res["error"]
    else:
        m.status, m.status_detail = "ok", ""
    db.commit()
    return res


def check(db: Session, m: McpServer, first: bool = False) -> dict:
    if not m.upstream_url and m.name.startswith("docker:"):
        return _check_gateway(db, m)
    if m.stdio:  # servidor local: o container precisa estar de pé (sobe de novo se caiu)
        try:
            ok, why = ensure_runner(db, m)
        except Exception as e:  # noqa: BLE001 — imagem ausente, Docker fora do ar…
            ok, why = False, f"não consegui subir o container: {str(e)[:200]}"
        if not ok:
            m.status, m.status_detail, m.checked_at = "error", why, now()
            db.commit()
            return {"ok": False, "status": "error", "error": why}
    try:
        headers = resolve_headers(db, m)
    except PlatformError as e:
        m.status, m.status_detail, m.checked_at = "auth", str(e)[:300], now()
        db.commit()
        return {"ok": False, "status": "auth", "error": str(e)}
    res = probe(m.upstream_url, m.transport, headers, float(m.timeout_s or 60))
    m.checked_at = now()
    if res["ok"]:
        m.status, m.status_detail = "ok", ""
        m.server_info = {**{k: v for k, v in (m.server_info or {}).items() if k == "egress_denied"},  # o histórico do proxy fica
                         **{k: res.get(k) for k in ("protocol_version", "server", "capabilities", "resources",
                                                    "resource_templates", "prompts")}}
        if first and not m.tools:
            m.tools = snapshot(res["tools"])
        else:
            reconcile(m, res["tools"])
    else:
        m.status, m.status_detail = res["status"], res["error"]
    db.commit()
    return res


def check_all() -> int:
    """O agendador chama (uma réplica a cada CONNECTOR_CHECK_MIN): o status de cada conector com ponte."""
    from .. import tenancy
    from ..db import SessionLocal
    n = 0
    with SessionLocal() as db:
        rows = [(m.id, m.org_id) for m in db.scalars(select(McpServer).where(McpServer.upstream_url != ""))]
    for mid, oid in rows:
        with tenancy.using(oid), SessionLocal() as db:
            m = db.get(McpServer, mid)
            if m is None:
                continue
            try:
                check(db, m)
                n += 1
            except Exception:
                db.rollback()
                log.exception("status do conector %s", m.name)
    return n


def attention(db: Session) -> list[dict]:
    """Conectores que precisam de um admin: ferramenta nova/alterada esperando aprovação ou autenticação vencida."""
    out = []
    for m in db.scalars(select(McpServer).where(McpServer.upstream_url != "")):
        waiting = [t["name"] for t in (m.tools or []) if t.get("state") in ("pending", "changed")]
        if waiting:
            out.append({"name": m.name, "kind": "tools", "tools": waiting, "at": m.checked_at})
        if m.status == "auth" and m.checked_at:
            out.append({"name": m.name, "kind": "auth", "detail": m.status_detail, "at": m.checked_at})
    return out


# ------------------------------------------------------------------ leitura
def connector_dict(db: Session, m: McpServer, acc: Access | None = None) -> dict:
    users = [a.slug for a in db.scalars(select(Agent)) if m.name in {(x if isinstance(x, str) else x.get("name"))
                                                                      for x in (spec_of(a).get("mcps") or [])}]
    kinds = {r.tool_ref.rsplit(":", 1)[-1]: r.action_type for r in db.scalars(
        select(ActionCatalog).where(ActionCatalog.tool_ref.like(f"mcp:{m.name}:%")))}
    return {"name": m.name, "description": m.description, "source": m.source, "url": m.upstream_url or m.url,
            "transport": m.transport, "auth": m.auth, "timeout_s": m.timeout_s,
            "headers": [{"name": h["name"], "secret": str(h.get("value", "")).startswith("secret://"),
                         "value": h["value"] if str(h.get("value", "")).startswith("secret://") else "····"}
                        for h in (m.headers or [])],
            "icon": icon_url(m.icon or (f"docker:{m.name[7:]}" if m.name.startswith("docker:") else "")), "category": m.category, "status": m.status or ("ok" if m.source in ("manual", "docker") else ""),
            "status_detail": m.status_detail, "checked_at": iso(m.checked_at), "server_info": m.server_info or {},
            "tools": [{**{k: v for k, v in t.items() if k != "hash"}, "action_type": kinds.get(t["name"])}
                      for t in (m.tools or [])],
            "scope": "personal" if m.owner_user_id else "team" if m.team_id else "org", "team_id": m.team_id,
            "used_by": users, "created_by": m.created_by, "bridged": bool(m.upstream_url),
            "can_manage": acc is not None and _manages(acc, m),
            "stdio": ({**{k: v for k, v in m.stdio.items() if k != "env"},
                       "env": [{"name": e["name"], "secret": str(e.get("value", "")).startswith("secret://"),
                                "value": e["value"] if str(e.get("value", "")).startswith("secret://") else ("····" if e.get("value") else "")}
                               for e in m.stdio.get("env") or []],
                       "registry": REGISTRY_HOSTS.get(m.stdio.get("runner"), [])} if m.stdio else None)}


def _manages(acc: Access, m: McpServer) -> bool:
    try:
        _can_manage(acc, m, None, False)
        return True
    except Forbidden:
        return False


def catalog(db: Session, acc: Access) -> dict:
    """A tela de Conectores: instalados + catálogo (curados, remotos e containers do catálogo Docker), por seção."""
    from . import mcp_gateway
    installed = [m for m in db.scalars(select(McpServer)) if visible(acc, m)]
    have = {m.name for m in installed}
    items = []
    for c in CURATED:
        items.append({"key": f"curated:{c['id']}", "id": c["id"], "name": c["name"], "description": c["description"],
                      "icon": icon_url(f"curated:{c['id']}"), "section": c["section"], "kind": "curated", "auth": c["auth"],
                      "url": c["url"], "transport": c.get("transport", "http"), "installed": c["id"] in have,
                      "popular": c["id"] in POPULAR, "official": True})
    curated_ids = {c["id"] for c in CURATED}
    try:
        remotes, servers = mcp_gateway.remotes(), mcp_gateway.catalog()
    except Exception:  # sem internet: só curados e instalados
        remotes, servers = {}, {}
    for r in remotes.values():
        if r["name"] in curated_ids:
            continue
        items.append({"key": f"remote:{r['name']}", "id": r["name"], "name": r["title"], "description": r["description"],
                      "icon": icon_url(f"remote:{r['name']}"), "section": CATEGORY.get(r["category"], "other"),
                      "kind": "remote", "auth": "oauth" if r["oauth"] else "none", "url": r["url"],
                      "transport": r["transport"], "installed": r["name"] in have, "popular": r["name"] in POPULAR})
    enabled = {g.name for g in db.scalars(select(GatewayServer))}
    for s in servers.values():
        if s["name"] in curated_ids:
            continue
        items.append({"key": f"docker:{s['name']}", "id": s["name"], "name": s["title"], "description": s["description"],
                      "icon": icon_url(f"docker:{s['name']}"), "section": CATEGORY.get(s["category"], "other"),
                      "kind": "docker", "auth": "secrets" if s["secrets"] else "none", "secrets": s["secrets"],
                      "config": s["config"], "installed": s["name"] in enabled or f"docker:{s['name']}" in have,
                      "popular": s["name"] in POPULAR, "tools_count": len(s["tools"])})
    items += _registry_items(items, have)
    return {"sections": SECTIONS, "items": items,
            "installed": [connector_dict(db, m, acc) | {"key": f"installed:{m.name}"} for m in installed]}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"mcp|server", "", (s or "").lower()))


def _registry_items(items: list[dict], have: set) -> list[dict]:
    """O registro MCP do GitHub, sem o que já está no catálogo (o curado ou o do Docker vale mais): mesmo host do
    endereço remoto ou mesmo nome."""
    from . import mcp_registry
    try:
        reg = mcp_registry.items()
    except Exception:  # noqa: BLE001 — sem o registro, o catálogo segue com as outras origens
        return []
    hosts = {mcp_registry.host_of(i.get("url", "")) for i in items if i.get("url")} - {""}
    names = ({_norm(i["id"]) for i in items} | {_norm(i["name"]) for i in items}) - {""}
    out = []
    for it in reg:
        rem = it.get("remote") or {}
        last = _norm(it["registry_name"].split("/")[-1])
        if mcp_registry.host_of(rem.get("url", "")) in hosts or (last and last in names) or _norm(it["name"]) in names:
            continue
        out.append({"key": f"github:{it['id']}", "id": it["id"], "name": it["name"], "description": it["description"],
                    "icon": icon_url(f"github:{it['id']}"), "section": it["section"], "kind": "github",
                    # "auto": o registro às vezes erra o transporte (declara SSE e fala HTTP); o teste de conexão decide
                    "auth": it["auth"], "url": rem.get("url", ""), "transport": "auto",
                    "headers": rem.get("headers") or [], "url_vars": rem.get("url_vars") or [],
                    "stdio": it["package"] if it["auth"] == "stdio" else None, "installed": it["id"] in have,
                    "popular": False, "publisher": it["publisher"], "stars": it["stars"], "license": it["license"],
                    "repo": it["repo"], "org": it["org"], "registry_name": it["registry_name"]})
    return sorted(out, key=lambda x: -x["stars"])  # em cada seção, os mais usados primeiro


# ------------------------------------------------------------------ importar mcpServers (Claude Desktop, Cursor…)
SECRET_HEADER = re.compile(r"authorization|token|key|secret|password|cookie", re.I)


def _import_stdio(name: str, v: dict) -> dict:
    """npx/uvx viram um servidor local; outros comandos (node, python, docker, binários) não entram."""
    cmd = os.path.basename(str(v.get("command") or "")).lower().removesuffix(".cmd").removesuffix(".exe")
    args = [str(a) for a in v.get("args") or []]
    env = [{"name": k, "value": str(val), "secret": bool(SECRET_ENV.search(k))} for k, val in (v.get("env") or {}).items()]
    base = {"name": name, "command": " ".join([str(v.get("command")), *args])[:300], "env": env}
    if cmd not in RUNNERS:
        return {**base, "importable": False, "why": f"só npx e uvx rodam como servidor local ('{cmd}' não)"}
    rest = list(args)
    if cmd == "npx":
        while rest and rest[0].startswith("-"):
            rest.pop(0)
        pkg, entry = (rest.pop(0) if rest else ""), ""
    else:
        if rest[:1] == ["--from"] and len(rest) >= 3:
            pkg, entry, rest = rest[1], rest[2], rest[3:]
        else:
            while rest and rest[0].startswith("-"):
                rest.pop(0)
            pkg = rest.pop(0) if rest else ""
            entry = ""
    raw = {"runner": cmd, "package": pkg, "entry": entry, "args": rest, "env": env}
    try:
        spec = stdio_spec(raw)
        return {**base, "stdio": {**spec, "env": env}, "importable": True}
    except PlatformError as e:
        return {**base, "stdio": raw, "importable": False, "why": str(e), "fixable": bool(pkg)}


def import_preview(text: str) -> list[dict]:
    """Lê o JSON no formato mcpServers e diz o que dá para importar agora (remotos) e o que é local (stdio, fase B)."""
    try:
        doc = json.loads(text or "")
    except ValueError as e:
        raise PlatformError(f"JSON inválido: {e}") from None
    servers = doc.get("mcpServers") or doc.get("servers") or (doc if all(isinstance(v, dict) for v in doc.values()) else {})
    if not isinstance(servers, dict) or not servers:
        raise PlatformError("não encontrei {\"mcpServers\": {...}} no JSON")
    out = []
    for name, v in servers.items():
        url = v.get("url") or v.get("serverUrl") or ""
        if url:
            hdrs = [{"name": k, "value": str(val), "secret": bool(SECRET_HEADER.search(k))}
                    for k, val in (v.get("headers") or {}).items()]
            t = (v.get("type") or v.get("transport") or "").lower()
            out.append({"name": re.sub(r"[^a-z0-9._-]", "-", name.lower())[:60], "url": url,
                        "transport": "sse" if t == "sse" or url.rstrip("/").endswith("/sse") else "auto",
                        "auth": "headers" if hdrs else "none", "headers": hdrs, "importable": True})
        elif v.get("command"):
            out.append(_import_stdio(re.sub(r"[^a-z0-9._-]", "-", name.lower())[:60], v))
        else:
            out.append({"name": name, "importable": False, "why": "sem url nem command"})
    return out
