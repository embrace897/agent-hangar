"""Ponte stdio -> Streamable HTTP para um servidor MCP local (npx/uvx), num container isolado.

Sobe o processo do servidor (MCP_COMMAND, uma lista JSON; sem shell) com as variáveis de MCP_SERVER_ENV, mantém uma
sessão MCP por stdio com ele e atende a central em POST /mcp (JSON-RPC sem estado, respostas JSON): initialize,
ping, tools/list, tools/call, resources/list e prompts/list. Só a central fala com a ponte (X-Runner-Token).
Se o processo do servidor morrer, a ponte sai: o container reinicia sozinho.
"""
import asyncio
import contextlib
import hmac
import json
import logging
import os
import sys

import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger("stdio-runner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
TOKEN = os.environ.get("RUNNER_TOKEN", "")
COMMAND = json.loads(os.environ.get("MCP_COMMAND") or "[]")
SERVER_ENV = json.loads(os.environ.get("MCP_SERVER_ENV") or "{}")
KEEP = ("PATH", "HOME", "LANG", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy",
        "NODE_USE_ENV_PROXY", "NPM_CONFIG_CACHE", "npm_config_update_notifier", "UV_CACHE_DIR", "UV_TOOL_DIR",
        "UV_PYTHON_DOWNLOADS", "XDG_CACHE_HOME", "PYTHONUNBUFFERED")
STATE: dict = {"session": None, "init": None, "error": "", "ready": asyncio.Event()}


async def run_server():
    """Mantém a sessão stdio viva; quando o processo termina, encerra a ponte (o container reinicia)."""
    if not COMMAND:
        STATE["error"] = "MCP_COMMAND vazio"
        STATE["ready"].set()
        return
    env = {k: os.environ[k] for k in KEEP if k in os.environ} | {k: str(v) for k, v in SERVER_ENV.items()}
    params = StdioServerParameters(command=COMMAND[0], args=COMMAND[1:], env=env)
    try:
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            STATE["init"] = await asyncio.wait_for(s.initialize(), timeout=float(os.environ.get("START_TIMEOUT", "240")))
            STATE["session"] = s
            STATE["ready"].set()
            log.info("servidor MCP pronto: %s", STATE["init"].serverInfo.name)
            await asyncio.Event().wait()  # fica aqui até o processo cair (a sessão levanta erro)
    except BaseException as e:  # noqa: BLE001
        STATE["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        log.error("o servidor MCP parou: %s", STATE["error"])
        STATE["ready"].set()
        await asyncio.sleep(2)
        os._exit(1)


def rpc(id_, result=None, error=None):
    return {"jsonrpc": "2.0", "id": id_, **({"error": error} if error else {"result": result})}


async def handle(msg: dict):
    s, init = STATE["session"], STATE["init"]
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return rpc(mid, {"protocolVersion": params.get("protocolVersion") or str(init.protocolVersion),
                         "capabilities": init.capabilities.model_dump(by_alias=True, exclude_none=True, mode="json"),
                         "serverInfo": init.serverInfo.model_dump(by_alias=True, exclude_none=True, mode="json")})
    if method == "ping":
        return rpc(mid, {})
    calls = {"tools/list": lambda: s.list_tools(), "tools/call": lambda: s.call_tool(params.get("name", ""), params.get("arguments") or {}),
             "resources/list": lambda: s.list_resources(), "resources/templates/list": lambda: s.list_resource_templates(),
             "prompts/list": lambda: s.list_prompts()}
    if method not in calls:
        return rpc(mid, error={"code": -32601, "message": f"método não suportado: {method}"})
    try:
        res = await calls[method]()
        return rpc(mid, res.model_dump(by_alias=True, exclude_none=True, mode="json"))
    except Exception as e:  # noqa: BLE001
        return rpc(mid, error={"code": -32000, "message": f"{type(e).__name__}: {str(e)[:300]}"})


async def mcp(request: Request):
    if not TOKEN or not hmac.compare_digest(request.headers.get("x-runner-token", ""), TOKEN):
        return JSONResponse({"error": "não autorizado"}, 401)
    if request.method != "POST":
        return Response(status_code=405)
    await asyncio.wait_for(STATE["ready"].wait(), timeout=300)
    if STATE["session"] is None:
        return JSONResponse(rpc(None, error={"code": -32000, "message": f"servidor MCP não subiu: {STATE['error']}"}), 503)
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(rpc(None, error={"code": -32700, "message": "JSON inválido"}), 400)
    msgs = body if isinstance(body, list) else [body]
    out = [await handle(m) for m in msgs if isinstance(m, dict) and m.get("id") is not None and m.get("method")]
    if not out:
        return Response(status_code=202)
    return JSONResponse(out if isinstance(body, list) else out[0])


async def health(_):
    if STATE["session"] is not None:
        return JSONResponse({"ok": True, "server": STATE["init"].serverInfo.name})
    return JSONResponse({"ok": False, "starting": not STATE["ready"].is_set(), "error": STATE["error"]}, 503)


@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(run_server())
    yield
    task.cancel()


app = Starlette(routes=[Route("/mcp", mcp, methods=["GET", "POST", "DELETE"]), Route("/health", health)], lifespan=lifespan)

if __name__ == "__main__":
    if not TOKEN:
        sys.exit("RUNNER_TOKEN é obrigatório")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="warning")
