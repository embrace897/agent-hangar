"""MCPs remotos com OAuth: conectar (admin, pela UI) e o proxy interno que os agentes usam."""
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import config
from .. import services as svc
from ..db import SessionLocal
from .deps import acc, db_dep, guard
from .internal import caller_agent, caller_specs

router = APIRouter()
COOKIE = "hangar_remote_mcp"
FORWARD = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
BACK = ("content-type", "mcp-session-id", "cache-control")


class RemoteBody(BaseModel):
    name: str
    url: str
    description: str = ""
    browser_base: str = ""  # quando o navegador alcança o servidor por outra origem (ex.: http://localhost:8098)


@router.get("/api/remote-mcps")
def remote_list(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.remote_mcp.list_all(db, acc(request, db)))


@router.post("/api/remote-mcps")
def remote_connect(body: RemoteBody, request: Request, db=Depends(db_dep)):
    """Começa (ou refaz) a conexão: devolve a URL de autorização para abrir no navegador."""
    r = guard(lambda: svc.remote_mcp.start(db, acc(request, db), body.name, body.url, body.description,
                                           body.browser_base))
    resp = Response(content=f'{{"authorize_url": "{r["authorize_url"]}"}}', media_type="application/json")
    resp.set_cookie(COOKIE, r["cookie"], max_age=600, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                    path="/api/remote-mcps")
    return resp


@router.get("/api/remote-mcps/callback")
def remote_callback(request: Request, code: str = "", state: str = "", error: str = "",
                    error_description: str = "", db=Depends(db_dep)):
    def back(msg: str | None = None, name: str = "") -> RedirectResponse:
        q = urllib.parse.urlencode({"remote_error": msg} if msg else {"remote_ok": name})
        resp = RedirectResponse(f"/ui/#/connectors{'/' + urllib.parse.quote(name) if name else ''}?{q}", 303)
        resp.delete_cookie(COOKIE, path="/api/remote-mcps")
        return resp
    if error:
        return back(f"o servidor recusou a autorização ({error_description or error})")
    try:
        r = svc.remote_mcp.finish(db, acc(request, db), code, state, request.cookies.get(COOKIE, ""))
    except svc.PlatformError as e:
        return back(str(e)[:300])
    return back(name=r.name)


@router.delete("/api/remote-mcps/{name}")
def remote_remove(name: str, request: Request, db=Depends(db_dep)):
    guard(lambda: svc.remote_mcp.remove(db, acc(request, db), name))
    return {"ok": True}


@router.post("/api/remote-mcps/{name}/test")
def remote_test(name: str, request: Request, db=Depends(db_dep)):
    """Lista as ferramentas do MCP remoto com o token guardado (confere que a conexão funciona)."""
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    def go():
        svc.remote_mcp._admin(acc(request, db))
        r, tok = svc.remote_mcp.token(db, name)

        async def probe():
            async with streamablehttp_client(r.url, headers={"Authorization": f"Bearer {tok}"}, timeout=20) as (rd, wr, _):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    return [t.name for t in (await s.list_tools()).tools]
        tools = asyncio.run(probe())
        return {"name": name, "tools": tools, "count": len(tools)}
    try:
        return guard(go)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(502, f"falha ao falar com o MCP remoto: {type(e).__name__}: {str(e)[:200]}") from None


# ------------------------------------------------------------------ proxy para os agentes
def _authorize(request: Request, name: str):
    """Token interno do agente + o MCP precisa estar na spec dele (versão atual ou uma que esteja no ar)."""
    with SessionLocal() as db:
        a = caller_agent(request, db)
        names = {(m if isinstance(m, str) else m.get("name")) for s in caller_specs(a) for m in (s.get("mcps") or [])}
        if name not in names:
            raise HTTPException(403, f"'{a.slug}' não tem o MCP '{name}' na spec")
        try:
            r, tok = svc.remote_mcp.token(db, name)
        except svc.PlatformError as e:
            raise HTTPException(503, str(e)) from None
        return r.url, tok


def _refresh(name: str) -> str:
    with SessionLocal() as db:
        return svc.remote_mcp.token(db, name, force=True)[1]


@router.api_route("/internal/mcp-remote/{name}", methods=["GET", "POST", "DELETE"])
async def remote_proxy(name: str, request: Request):
    url, tok = await run_in_threadpool(_authorize, request, name)
    body = await request.body()
    fwd = {h: request.headers[h] for h in FORWARD if h in request.headers}
    client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10))
    try:
        up = await client.send(client.build_request(request.method, url, content=body,
                                                    headers={**fwd, "Authorization": f"Bearer {tok}"}), stream=True)
        if up.status_code == 401:  # token revogado/rotacionado fora de hora: renova uma vez e repete
            await up.aclose()
            tok = await run_in_threadpool(_refresh, name)
            up = await client.send(client.build_request(request.method, url, content=body,
                                                        headers={**fwd, "Authorization": f"Bearer {tok}"}), stream=True)
    except Exception as e:
        await client.aclose()
        return Response(f'{{"error": "MCP remoto indisponível: {type(e).__name__}"}}', 502, media_type="application/json")
    headers = {k: v for k, v in up.headers.items() if k.lower() in BACK}

    async def close():
        await up.aclose()
        await client.aclose()
    return StreamingResponse(up.aiter_bytes(), status_code=up.status_code, headers=headers, background=BackgroundTask(close))
