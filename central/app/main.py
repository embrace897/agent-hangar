"""Agent Hangar — app FastAPI: API admin, gateway dos agentes, MCP da plataforma e UI."""
import hashlib
import hmac
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, config, crypto, db, egress, observability, shared, tenancy
from . import services as svc
from .mcp_tools import mcp
from .routers import (
    access,
    admin,
    analyst,
    connectors,
    employees,
    gateway,
    internal,
    issues,
    memory,
    notify,
    oauth,
    plugins,
    remote_mcp,
    schedules,
    scim,
    work,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
observability.setup_logging()
log = logging.getLogger("hangar")

mcp_app = mcp.streamable_http_app()


def _write_gateway_files():
    try:
        with db.SessionLocal() as s:
            svc.mcp_gateway.write_files(s)
    except OSError as e:
        log.warning("catálogo Docker MCP: não foi possível escrever em %s (%s)", config.MCP_GATEWAY_CONFIG_DIR, e)


@asynccontextmanager
async def lifespan(app):
    crypto.check_key()
    if not config.INTERNAL_SECRET:
        raise RuntimeError("INTERNAL_SECRET não definido (rode scripts/setup.sh ou veja .env.example)")
    if not config.ADMIN_TOKEN or config.ADMIN_TOKEN in ("changeme", "change-me"):
        log.warning("ADMIN_TOKEN vazio ou padrão — defina um valor forte antes de expor a central")
    await run_in_threadpool(db.migrate)
    await run_in_threadpool(shared.beat)
    await run_in_threadpool(svc.recover_orphans)
    await run_in_threadpool(_write_gateway_files)  # o gateway Docker MCP precisa dos arquivos mesmo sem servidor ativo
    svc.schedules.start()
    svc.employee_tasks.start()
    await egress.start()  # plugins com código saem para a internet só por aqui (hosts de server.egress)
    shared.start_heartbeat(housekeeping=svc.jobs.housekeeping)
    observability.start_metrics_server()
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        svc.schedules.stop()
        svc.employee_tasks.stop()
        await egress.stop()
        shared.stop_heartbeat()


app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan)

_GW = re.compile(r"^/gw(?:-stage)?/([^/]+)(?:/|$)")
OPEN_PREFIXES = ("/ui", "/app", "/ds/f/", "/downloads/", "/api/health", "/internal", "/api/auth/", "/login", "/favicon.ico",
                 "/.well-known/", "/oauth/",
                 "/hooks/")  # webhook de entrada do Digital employee: autenticado pelo token do próprio funcionário
# descoberta e endpoints OAuth do MCP: clientes que rodam no navegador (ex.: MCP Inspector) precisam de CORS
CORS_PREFIXES = ("/.well-known/", "/oauth/register", "/oauth/token", "/oauth/revoke")
CORS_HEADERS = [(b"access-control-allow-origin", b"*"), (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                (b"access-control-allow-headers", b"authorization, content-type, mcp-protocol-version"),
                (b"access-control-max-age", b"86400")]
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _adding_headers(send, extra: list[tuple[bytes, bytes]]):
    async def wrapped(msg):
        if msg["type"] == "http.response.start":
            msg["headers"] = list(msg.get("headers", [])) + extra
        await send(msg)
    return wrapped


def _may_consume(principal, slug: str) -> bool:
    with db.SessionLocal() as s:
        a = svc.find_agent(s, slug)
        return a is None or svc.access.of(s, principal).can("consume", a)  # inexistente: o gateway responde 404


class AuthMiddleware:
    """Autentica tudo, menos a UI estática, o health, o login e /internal (que tem autenticação própria por
    token de job ou de agente). Credencial: header (Bearer/X-API-Key) ou cookie de sessão da UI — este exige
    o header X-CSRF-Token igual ao cookie hangar_csrf em toda escrita (double-submit).
    /gw/<slug> exige poder usar aquele agente; /scim, chave scim; o resto (/api, /mcp), admin ou usuário —
    e cada rota confere a permissão sobre o recurso."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path.startswith(CORS_PREFIXES):
            if scope["method"] == "OPTIONS":
                return await Response(status_code=204, headers={k.decode(): v.decode() for k, v in CORS_HEADERS})(
                    scope, receive, send)

            async def send_cors(msg):
                if msg["type"] == "http.response.start":
                    msg["headers"] = list(msg.get("headers", [])) + CORS_HEADERS
                await send(msg)
            return await self.app(scope, receive, send_cors)
        gw = svc.cors.target(path)
        origin = next((v.decode() for k, v in scope["headers"] if k == b"origin"), "") if gw else ""
        if origin:
            # CORS por agente (spec.cors.origins da versão rodando naquele ambiente); o preflight vem sem credencial
            allowed = origin.lower() in await run_in_threadpool(svc.cors.origins, *gw)
            if scope["method"] == "OPTIONS":
                if not allowed:
                    return await JSONResponse({"error": f"a origem {origin} não está liberada para o agente '{gw[0]}' "
                                                        f"em {gw[1]} (spec.cors.origins)"}, 403)(scope, receive, send)
                return await Response(status_code=204, headers={k.decode(): v.decode() for k, v in
                                                                svc.cors.headers(origin, True)})(scope, receive, send)
            if allowed:  # inclusive nos erros: a página consegue ler o 401, o 429…
                send = _adding_headers(send, svc.cors.headers(origin, False))
        if path == "/" or path.startswith(OPEN_PREFIXES):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        token = auth.token_from_headers(headers)
        via_cookie = False
        if not token:
            token = auth.cookie(headers, auth.SESSION_COOKIE)
            via_cookie = bool(token)

        def _auth():
            with db.SessionLocal() as s:
                return auth.authenticate(s, token)

        principal = await run_in_threadpool(_auth) if token else None
        if principal is None:
            headers = {}
            if path.startswith("/mcp") and config.OAUTH_ENABLED:  # descoberta OAuth do MCP (RFC 9728)
                headers["WWW-Authenticate"] = f'Bearer resource_metadata="{svc.mcp_oauth.resource_metadata_url()}"'
            return await JSONResponse({"error": "unauthorized"}, 401, headers=headers)(scope, receive, send)
        if principal.via == "oauth" and not path.startswith("/mcp"):
            return await JSONResponse({"error": "forbidden: token OAuth do MCP só vale em /mcp"}, 403)(scope, receive, send)
        if via_cookie and scope["method"] not in SAFE_METHODS:
            csrf = auth.cookie(headers, auth.CSRF_COOKIE)
            if not csrf or not hmac.compare_digest(csrf, headers.get("x-csrf-token", "")):
                return await JSONResponse({"error": "csrf: recarregue a página"}, 403)(scope, receive, send)
        m = _GW.match(path)
        if m:
            slug = m.group(1)
            allowed = (await run_in_threadpool(_may_consume, principal, slug)) if principal.is_user \
                and not principal.is_admin else principal.can_invoke(slug)
        elif path.startswith("/scim"):
            allowed = "scim" in principal.scopes or principal.is_admin
        else:
            allowed = principal.can_use_api
        if not allowed:
            return await JSONResponse({"error": "forbidden: a credencial não tem permissão para este recurso"},
                                      403)(scope, receive, send)
        scope.setdefault("state", {})["principal"] = principal
        org = await run_in_threadpool(_org_of, principal, headers.get("x-hangar-org", ""))
        if not headers.get("x-hangar-org") and auth.cookie(headers, "hangar_org"):  # o seletor de empresa do portal
            org = await run_in_threadpool(_org_of, principal, auth.cookie(headers, "hangar_org")) or org
        if org is None:
            return await JSONResponse({"error": "empresa desconhecida (X-Hangar-Org)"}, 404)(scope, receive, send)
        if org == 0:
            return await JSONResponse({"error": "empresa suspensa: fale com quem administra a instalação"}, 403)(scope, receive, send)
        tenancy.set_org(org, principal.user_id)  # tudo daqui em diante (e nas threads do FastAPI) age dentro desta empresa
        if path.startswith("/mcp"):
            return await _mcp_logging(self.app, scope, receive, send, headers)
        return await self.app(scope, receive, send)


def _org_of(principal, wanted: str) -> int | None:
    """A empresa da requisição: a de quem chama. O token admin e os admins da empresa principal escolhem pelo
    X-Hangar-Org (id ou slug); sem ele, a empresa principal."""
    instance_admin = principal.via == "admin-token" or ("admin" in principal.scopes and principal.org_id is None) or (
        principal.org_role == "admin" and (principal.org_id or 1) == 1)
    if not wanted or not instance_admin:
        org = principal.org_id or 1
        with db.SessionLocal() as s:  # empresa suspensa: as pessoas dela não entram (quem administra a instalação, sim)
            from .models import Organization
            o = s.get(Organization, org)
            if o is not None and o.status == "suspended" and not instance_admin:
                return 0
        return org
    with db.SessionLocal() as s:
        from sqlalchemy import select

        from .models import Organization
        o = s.get(Organization, int(wanted)) if wanted.isdigit() else s.scalar(
            select(Organization).where(Organization.slug == wanted))
        return o.id if o else None


_RPC_METHOD = re.compile(rb'"method"\s*:\s*"([\w/.-]{1,60})"')


async def _mcp_logging(app_, scope, receive, send, headers):
    """Registra o motivo das respostas 4xx do /mcp (método JSON-RPC, versão do protocolo, Accept e o erro
    devolvido), para diagnosticar clientes (Claude.ai, ChatGPT…) sem precisar reproduzir. Nunca o token nem o
    corpo da requisição."""
    seen = {"method": b"", "status": 0}

    async def recv():
        msg = await receive()
        if msg.get("type") == "http.request" and not seen["method"]:
            m = _RPC_METHOD.search(msg.get("body", b"")[:2000])
            seen["method"] = m.group(1) if m else b"?"
        return msg

    async def snd(msg):
        if msg["type"] == "http.response.start":
            seen["status"] = msg["status"]
        elif msg["type"] == "http.response.body" and 400 <= seen["status"] < 500 and seen["status"] != 401:
            log.warning("mcp %s %s: method=%s protocol=%s accept=%s error=%s", scope["method"], seen["status"],
                        seen["method"].decode(), headers.get("mcp-protocol-version", "-"), headers.get("accept", "-"),
                        msg.get("body", b"")[:200].decode(errors="replace"))
            seen["status"] = 0
        await send(msg)
    return await app_(scope, recv, snd)


app.add_middleware(AuthMiddleware)
app.add_middleware(observability.MetricsMiddleware)  # por fora: mede também as respostas 401/403
observability.setup_tracing(app)


@app.get("/api/metrics", include_in_schema=False)
def metrics(request: Request):
    """Métricas Prometheus desta réplica, para quem não tem um Prometheus raspando a porta METRICS_PORT."""
    p = request.scope.get("state", {}).get("principal")
    if not p or not p.is_auditor:
        return JSONResponse({"error": "forbidden: só admins e auditores"}, 403)
    body, ctype = observability.metrics_text()
    return Response(body, media_type=ctype)
app.include_router(access.router)
app.include_router(admin.router)
app.include_router(employees.router)
app.include_router(work.router)  # metas, projetos, conversa da tarefa, orçamentos, caixa de entrada e busca
app.include_router(issues.router)  # o quadro de issues (kanban) com as rodadas dos Digital employees
app.include_router(analyst.router)  # o dia a dia do analista: dados, documentos, números, processos…
app.include_router(analyst.hooks)  # entrada da fila, WhatsApp e transcrições: cada um com o próprio token
app.include_router(employees.hooks)
app.include_router(notify.router)
app.include_router(plugins.router)
app.include_router(plugins.hooks)  # gatilhos dos plugins: assinatura HMAC ou token da instalação
app.include_router(notify.hooks)  # Slack e link assinado: autenticados pela assinatura
app.include_router(scim.router)
app.include_router(schedules.router)
app.include_router(remote_mcp.router)
app.include_router(connectors.router)  # conectores (MCP): catálogo, cadastro e a ponte dos agentes
app.include_router(oauth.router)
app.include_router(memory.router)
app.include_router(internal.router)
app.include_router(gateway.router)


STATIC = os.path.join(os.path.dirname(__file__), "static")
# carimbo de versão dos arquivos da UI: muda a cada atualização, então o navegador nunca mistura versões
UI_VERSION = hashlib.sha256(b"".join(open(os.path.join(STATIC, f), "rb").read()
                                     for f in sorted(os.listdir(STATIC))
                                     if os.path.isfile(os.path.join(STATIC, f)))).hexdigest()[:12]
_INDEX = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read().replace("__V__", UI_VERSION)
# portal do usuário (/app): mesma base de código da UI, com a página Início e só o que o dia a dia de quem não
# é admin precisa. O console /ui é só de admins.
_PORTAL = open(os.path.join(STATIC, "portal.html"), encoding="utf-8").read().replace("__V__", UI_VERSION)


def _ui_user(request: Request):
    """Quem abriu a página (pelo cookie de sessão), ou None. Só decide o redirecionamento: os dados continuam
    protegidos pela API, que confere a permissão de cada chamada."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token:
        return None
    with db.SessionLocal() as s:
        return auth.authenticate(s, token)


def _home(request: Request) -> str:
    p = _ui_user(request)
    return f"/app/?v={UI_VERSION}" if p is not None and not p.is_admin else f"/ui/?v={UI_VERSION}"


@app.get("/")
@app.get("/ui")  # sem a barra final: sem esta rota, o MCP montado em "/" respondia 404
@app.get("/login")
def root(request: Request):
    return RedirectResponse(_home(request))  # URL nova a cada versão: fura até um index.html em cache


@app.get("/app")
def portal_root():
    return RedirectResponse(f"/app/?v={UI_VERSION}")


@app.get("/app/")
def portal_index():
    return HTMLResponse(_PORTAL, headers={"Cache-Control": "no-cache"})


PWA = {"manifest.webmanifest": "application/manifest+json", "sw.js": "text/javascript", "icon-192.png": "image/png",
       "icon-512.png": "image/png"}


@app.get("/app/{name}")
def portal_pwa(name: str):
    """O portal instalável (PWA): manifesto, ícones e o service worker no escopo /app/ (notificações dos pedidos)."""
    if name not in PWA:
        return JSONResponse({"error": "não encontrado"}, 404)
    with open(os.path.join(STATIC, name), "rb") as f:
        return Response(f.read(), media_type=PWA[name], headers={"Cache-Control": "no-cache"})


DOWNLOADS = os.environ.get("DOWNLOADS_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloads"))


@app.api_route("/downloads/agent-hangar-vscode.vsix", methods=["GET", "HEAD"])
def vscode_extension():
    """A extensão do VS Code, gerada no build da imagem (extensions/vscode). Instale com
    `code --install-extension agent-hangar-vscode.vsix` ou pelo menu Extensions → Install from VSIX."""
    from fastapi.responses import FileResponse
    path = os.path.join(DOWNLOADS, "agent-hangar-vscode.vsix")
    if not os.path.exists(path):
        return JSONResponse({"error": "extensão não incluída nesta imagem — gere com extensions/vscode (npm run package)"}, 404)
    return FileResponse(path, media_type="application/octet-stream", filename="agent-hangar-vscode.vsix")


@app.get("/ds/f/{path:path}")
def data_studio_file(path: str):
    """Downloads do Data Studio pela central (no cluster não há porta própria): o link já vem assinado por ele
    (sessão + token); aqui só repassamos o arquivo, sem sessão do Hangar."""
    import httpx

    from . import config
    if not config.DATA_STUDIO_FILES_URL or ".." in path:
        return JSONResponse({"error": "não encontrado"}, 404)
    try:
        r = httpx.get(f"{config.DATA_STUDIO_FILES_URL.rstrip('/')}/f/{path}", timeout=60)
    except httpx.HTTPError:
        return JSONResponse({"error": "Data Studio indisponível"}, 503)
    keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-disposition", "cache-control")}
    return Response(r.content, status_code=r.status_code, headers=keep)


@app.get("/favicon.ico")
def favicon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#F4B400" stroke-width="2.2" '
           'stroke-linejoin="round"><path d="M2.5 20.5V11L12 3.5 21.5 11v9.5"/><path d="M6.5 20.5v-6h11v6"/></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/ui/")
@app.get("/ui/index.html")
def ui_index(request: Request):
    """Console de administração: só admins. Logado sem ser admin -> portal do usuário (o fragmento #/… da URL
    é mantido pelo navegador no redirecionamento). Sem sessão, mostra o login (que leva cada um ao seu lugar)."""
    p = _ui_user(request)
    if p is not None and not p.is_admin:
        return RedirectResponse(f"/app/?v={UI_VERSION}", 303)
    return HTMLResponse(_INDEX, headers={"Cache-Control": "no-cache"})


class UIFiles(StaticFiles):
    """UI sem cache heurístico: o navegador sempre revalida (ETag), então uma atualização da central aparece no
    próximo carregamento — sem Ctrl+F5."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/ui", UIFiles(directory=STATIC, html=True), name="ui")
app.mount("/", mcp_app)
