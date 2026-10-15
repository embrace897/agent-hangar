"""Conectores (MCP): a tela de catálogo, o cadastro personalizado, o ícone de cada um e a PONTE que os agentes usam.

Ponte (/internal/mcp-proxy/<nome>): um servidor MCP mínimo e sem estado (Streamable HTTP com respostas JSON) para o
agente; por trás, a central abre a sessão com o servidor de verdade (Streamable HTTP ou SSE legado) com a credencial do
cofre. Só as ferramentas aprovadas aparecem e podem ser chamadas."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .. import services as svc
from ..db import SessionLocal
from ..models import McpServer
from .deps import acc, db_dep, guard
from .internal import caller_agent, caller_specs

router = APIRouter()
PROTOCOL = "2025-06-18"


@router.get("/api/connectors")
def connectors_catalog(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.connectors.catalog(db, acc(request, db)))


@router.get("/api/connectors/icon")
def connector_icon(key: str, db=Depends(db_dep)):
    """O ícone de um conector (baixado uma vez da origem conhecida e guardado pela central)."""
    got = svc.connectors.get_icon(db, key)
    if got is None:
        return Response(status_code=404)
    ctype, data = got
    return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=604800",
                                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


class TestBody(BaseModel):
    url: str
    transport: str = "auto"
    auth: str = "none"
    token: str = ""
    headers: list[dict] = []
    timeout_s: int = 30
    stdio: dict | None = None  # servidor local (npx/uvx): testado num container temporário


@router.post("/api/connectors/test")
def connector_test(b: TestBody, request: Request, db=Depends(db_dep)):
    """Testar conexão (nada é salvo): handshake, protocolo, capacidades, ferramentas, resources e prompts."""
    return guard(lambda: svc.connectors.test_config(db, acc(request, db), b.model_dump()))


@router.post("/api/connectors")
def connector_create(b: dict, request: Request, db=Depends(db_dep)):
    def go():
        r = svc.connectors.create(db, acc(request, db), b)
        if r.get("authorize_url"):  # OAuth: o navegador do admin autoriza; o cookie guarda o state assinado
            from .. import config
            from .remote_mcp import COOKIE
            resp = JSONResponse({k: v for k, v in r.items() if k != "oauth_cookie"})
            resp.set_cookie(COOKIE, r["oauth_cookie"], max_age=600, httponly=True, samesite="lax",
                            secure=config.COOKIE_SECURE, path="/api/remote-mcps")
            return resp
        return r
    return guard(go)


@router.get("/api/connectors/{name}")
def connector_get(name: str, request: Request, db=Depends(db_dep)):
    def go():
        a = acc(request, db)
        m = svc.connectors._get(db, name)
        if not svc.connectors.visible(a, m):
            raise svc.PlatformError(f"conector '{name}' não encontrado")
        return svc.connectors.connector_dict(db, m, a)
    return guard(go)


@router.post("/api/connectors/{name}/check")
def connector_check(name: str, request: Request, db=Depends(db_dep)):
    """Reconectar: testa agora e atualiza status e ferramentas (as novas ou alteradas ficam esperando aprovação)."""
    def go():
        a = acc(request, db)
        m = svc.connectors._get(db, name)
        svc.connectors._can_manage(a, m, None, False)
        svc.connectors.check(db, m)
        return svc.connectors.connector_dict(db, m, a)
    return guard(go)


class ToolReview(BaseModel):
    state: str | None = None
    action_type: str | None = None


@router.patch("/api/connectors/{name}/tools/{tool}")
def connector_tool(name: str, tool: str, b: ToolReview, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.connectors.review_tool(db, acc(request, db), name, tool, b.state, b.action_type))


@router.delete("/api/connectors/{name}")
def connector_delete(name: str, request: Request, db=Depends(db_dep)):
    guard(lambda: svc.connectors.remove(db, acc(request, db), name))
    return {"ok": True}


class ImportBody(BaseModel):
    text: str


@router.post("/api/connectors/import/preview")
def connector_import_preview(b: ImportBody, request: Request, db=Depends(db_dep)):
    """Lê um JSON no formato mcpServers (Claude Desktop, Cursor, .mcp.json) e diz o que dá para importar."""
    acc(request, db)
    return guard(lambda: {"servers": svc.connectors.import_preview(b.text)})


# ------------------------------------------------------------------ a ponte
def _load(request: Request, name: str) -> McpServer:
    with SessionLocal() as db:
        a = caller_agent(request, db)
        names = {(m if isinstance(m, str) else m.get("name")) for s in caller_specs(a) for m in (s.get("mcps") or [])}
        if name not in names:
            raise HTTPException(403, f"'{a.slug}' não tem o conector '{name}' na spec")
        m = db.query(McpServer).filter(McpServer.name == name).one_or_none()
        if m is None or not m.upstream_url:
            raise HTTPException(404, f"conector '{name}' não encontrado")
        if not svc.connectors.can_use(a, m):
            raise HTTPException(403, f"o conector '{name}' é de outro time ou pessoal")
        headers = svc.connectors.resolve_headers(db, m)
        db.expunge(m)
        return m, headers


def _reconcile(name: str, live: list[dict]) -> set[str]:
    with SessionLocal() as db:
        m = db.query(McpServer).filter(McpServer.name == name).one()
        allowed, _ = svc.connectors.reconcile(m, live)
        db.commit()
        return allowed


def _rpc(id_, result=None, error=None):
    return {"jsonrpc": "2.0", "id": id_, **({"error": error} if error else {"result": result})}


@router.api_route("/internal/mcp-proxy/{name}", methods=["GET", "POST", "DELETE"])
async def bridge(name: str, request: Request):
    if request.method == "GET":
        return Response(status_code=405)  # sem stream de notificações do servidor (sem estado)
    if request.method == "DELETE":
        return Response(status_code=200)
    try:
        m, headers = await run_in_threadpool(_load, request, name)
    except svc.PlatformError as e:
        raise HTTPException(503, str(e)) from None
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc(None, error={"code": -32700, "message": "JSON inválido"}), 400)
    msgs = body if isinstance(body, list) else [body]
    out = []
    for msg in msgs:
        if not isinstance(msg, dict) or "method" not in msg:
            continue
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if mid is None:  # notificação (ex.: notifications/initialized): nada a responder
            continue
        if method == "initialize":
            out.append(_rpc(mid, {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                                  "capabilities": {"tools": {"listChanged": False}},
                                  "serverInfo": {"name": f"hangar:{name}", "version": "1"}}))
            continue
        if method == "ping":
            out.append(_rpc(mid, {}))
            continue
        if method not in ("tools/list", "tools/call"):
            out.append(_rpc(mid, error={"code": -32601, "message": f"método não suportado pela ponte: {method}"}))
            continue
        try:
            async with svc.connectors.session(m.upstream_url, m.transport, headers, float(m.timeout_s or 60)) as (s, _):
                tools = (await s.list_tools()).tools
                live = [{"name": t.name, "description": (t.description or "")[:500], "hash": svc.connectors._tool_hash(t)}
                        for t in tools]
                allowed = await run_in_threadpool(_reconcile, name, live)
                if method == "tools/list":
                    out.append(_rpc(mid, {"tools": [t.model_dump(by_alias=True, exclude_none=True, mode="json")
                                                    for t in tools if t.name in allowed]}))
                    continue
                tool = params.get("name", "")
                if tool not in allowed:
                    out.append(_rpc(mid, {"isError": True, "content": [{"type": "text", "text": (
                        f"A ferramenta '{tool}' do conector '{name}' está bloqueada: é nova, mudou de descrição ou foi "
                        "desligada. Um admin precisa aprovar em Conectores.")}]}))
                    continue
                res = await s.call_tool(tool, params.get("arguments") or {})
                out.append(_rpc(mid, res.model_dump(by_alias=True, exclude_none=True, mode="json")))
        except BaseException as e:  # noqa: BLE001 — vira erro JSON-RPC legível para o agente
            if isinstance(e, KeyboardInterrupt | SystemExit):
                raise
            _, why = svc.connectors.explain(e)
            out.append(_rpc(mid, error={"code": -32000, "message": f"conector '{name}': {why}"}))
    if not out:
        return Response(status_code=202)
    return JSONResponse(out if isinstance(body, list) else out[0])
