"""Desktop governado (experimental): o portão entre a central e o desktop isolado da Cua.

O cua-spacesd (no mesmo container) serve ~60 ferramentas de desktop por MCP, com token. Este portão:
- guarda o token (lido de /run/cua/env-token; nunca sai do container);
- mantém UMA conexão aberta por tarefa (x-session-id): a Cua só aceita clique por pixel numa tela capturada na
  mesma sessão, o que garante que o modelo clica na tela que viu;
- expõe à central só o necessário — abrir um app, olhar (captura + elementos de acessibilidade), clicar, digitar,
  teclas e rolar —, sempre na janela do app da tarefa e na lista de apps liberados que a central manda.
A alçada, os segredos e a auditoria ficam na central.
"""
import asyncio
import contextlib
import logging
import os
import re
import time

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("desktop")
CUA_URL = os.environ.get("CUA_URL", "http://127.0.0.1:3211/mcp")
TOKEN_FILE = os.environ.get("CUA_ENV_TOKEN_FILE", "/run/cua/env-token")
IDLE_MIN = float(os.environ.get("DESKTOP_IDLE_MIN", "20"))
MAX_IMG = int(os.environ.get("DESKTOP_MAX_IMAGE", "1280"))
MAX_ELEMENTS = int(os.environ.get("DESKTOP_MAX_ELEMENTS", "80"))  # quantos vão ao modelo
MAX_WALK = int(os.environ.get("DESKTOP_MAX_WALK", "400"))  # quantos se leem da árvore de acessibilidade
# papéis exatos (AT-SPI): "tab" casava com "table cell", e as células de uma lista de arquivos empurravam o botão
# Salvar para fora da lista
ACTIONABLE = {"push button", "button", "toggle button", "text", "entry", "password text", "menu", "menu item",
              "check box", "check menu item", "radio button", "radio menu item", "combo box", "spin button",
              "page tab", "link", "list item"}
SKIP = {"xfdesktop", "xfce4-panel", "desktop"}

mcp = FastMCP("desktop", stateless_http=True, json_response=True, streamable_http_path="/mcp",
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
_sessions: dict[str, dict] = {}
_lock = asyncio.Lock()


def _token() -> str:
    try:
        return open(TOKEN_FILE).read().strip()
    except OSError:
        return os.environ.get("CUA_ENV_TOKEN", "")


def _sid(ctx: Context) -> str:
    req = getattr(ctx.request_context, "request", None)
    sid = (req.headers.get("x-session-id") if req is not None else "") or ""
    if not re.fullmatch(r"[\w.-]{1,120}", sid):
        raise ToolError("sessão inválida (x-session-id)")
    return sid


async def _runner(q: asyncio.Queue, ready: asyncio.Future):
    """Dono da conexão com a Cua: o anyio exige que ela abra e feche na mesma task, então cada sessão tem a sua e
    recebe as chamadas por uma fila."""
    try:
        async with streamablehttp_client(CUA_URL, headers={"Authorization": f"Bearer {_token()}"},
                                         timeout=60, sse_read_timeout=300) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                ready.set_result(True)
                while True:
                    item = await q.get()
                    if item is None:
                        return
                    name, args, fut = item
                    try:
                        fut.set_result(await s.call_tool(name, args))
                    except Exception as e:  # noqa: BLE001 — devolve o erro a quem chamou
                        fut.set_exception(e)
    except Exception as e:  # noqa: BLE001
        if not ready.done():
            ready.set_exception(e)


async def _open(sid: str) -> dict:
    """Uma conexão MCP com a Cua que fica aberta enquanto a tarefa usa o desktop."""
    q: asyncio.Queue = asyncio.Queue()
    ready = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_runner(q, ready))
    await asyncio.wait_for(ready, 30)
    return {"q": q, "task": task, "used": time.time(), "pid": None, "window": None, "elements": [], "lock": asyncio.Lock()}


async def _session(sid: str) -> dict:
    async with _lock:
        for k in [k for k, v in _sessions.items() if time.time() - v["used"] > IDLE_MIN * 60]:
            await _close(k)
        if sid not in _sessions:
            _sessions[sid] = await _open(sid)
        _sessions[sid]["used"] = time.time()
        return _sessions[sid]


async def _close(sid: str):
    s = _sessions.pop(sid, None)
    if s:
        await s["q"].put(None)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(s["task"], 5)


async def _call(sess: dict, name: str, args: dict):
    fut = asyncio.get_running_loop().create_future()
    await sess["q"].put((name, args, fut))
    res = await asyncio.wait_for(fut, 120)
    text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
    imgs = [c.data for c in res.content if getattr(c, "type", "") == "image"]
    if res.isError:
        raise ToolError(text[:500] or f"{name} falhou")
    return text, imgs, (res.structuredContent or {})


def _app_ok(name: str, allowed: list[str]) -> bool:
    n = (name or "").strip().lower()
    return bool(n) and any(a.strip() and (a.strip().lower() in n or n in a.strip().lower()) for a in allowed)


async def _windows(sess: dict) -> list[dict]:
    _, _, sc = await _call(sess, "list_windows", {"on_screen_only": True})
    return [w for w in sc.get("windows", []) if (w.get("app_name") or "").lower() not in SKIP
            and w.get("width", 0) > 50 and w.get("height", 0) > 50]


async def _target(sess: dict, allowed: list[str], window: str = "") -> dict:
    wins = [w for w in await _windows(sess) if _app_ok(w.get("app_name", "") + " " + w.get("title", ""), allowed)
            or w.get("pid") == sess["pid"]]
    if window:
        wins = [w for w in wins if window.lower() in (w.get("title") or "").lower()] or wins
    # a janela de cima do app: um diálogo ("Confirma?", "Salvar como"), um menu aberto ou um relatório ficam por cima
    # da principal, e é neles que o próximo clique tem de cair
    top = lambda ws: sorted(ws, key=lambda w: -(w.get("z_index") or 0))  # noqa: E731
    mine = [w for w in wins if w.get("pid") == sess["pid"]]
    pick = (top(mine) or top(wins) or [None])[0]
    if pick is None:
        raise ToolError("nenhuma janela de um app liberado está aberta: use open primeiro")
    sess["pid"], sess["window"] = pick["pid"], pick["window_id"]
    return pick


async def _look(sess: dict, allowed: list[str], window: str = "") -> dict:
    w = await _target(sess, allowed, window)
    _, imgs, sc = await _call(sess, "get_window_state", {"pid": w["pid"], "window_id": w["window_id"], "include_screenshot": True,
                                                         "max_image_dimension": MAX_IMG, "max_elements": MAX_WALK})
    els = [e for e in sc.get("elements") or [] if e.get("label") or e.get("role") in ("button", "text", "entry", "check box")]
    sess["elements"] = els
    # o que se clica e preenche vem antes (botões, campos, menus); células de tabela e o resto, depois — num diálogo
    # "Salvar como" a lista de arquivos tem dezenas de células, e o botão Salvar não pode ficar de fora
    els = sorted((e for e in els if e.get("element_index") is not None),
                 key=lambda e: (0 if (e.get("role") or "").lower() in ACTIONABLE else 1, e["element_index"]))
    rows = [f"[{e['element_index']}] {e.get('role', '')} \"{(e.get('label') or '')[:60]}\""
            + (f" = \"{str(e.get('value'))[:40]}\"" if e.get("value") not in (None, "") else "")
            for e in els]
    return {"window": w.get("title"), "app": w.get("app_name"), "size": [w.get("width"), w.get("height")],
            "elements": rows[:MAX_ELEMENTS],
            "dica": ("sem árvore de acessibilidade: clique por x,y na captura (pixels da janela)" if len(rows) <= 1
                     else "prefira element (o número entre colchetes); x,y só quando o elemento não aparecer"),
            "screenshot": imgs[0] if imgs else ""}


def _where(sess: dict, element, x, y) -> dict:
    a = {"pid": sess["pid"], "window_id": sess["window"], "delivery_mode": "foreground"}
    if element is not None and str(element) != "":
        e = next((e for e in sess["elements"] if str(e.get("element_index")) == str(element)), None)
        if e is None:
            raise ToolError(f"elemento {element} não está na última captura: use look de novo")
        return {**a, "element_token": e["element_token"]}
    if x is None or y is None:
        raise ToolError("diga element ou x e y")
    return {**a, "x": float(x), "y": float(y)}


async def _act(ctx: Context, allowed: list[str], fn):
    sess = await _session(_sid(ctx))
    async with sess["lock"]:
        if sess["window"] is None:
            await _target(sess, allowed)
        msg = await fn(sess)
        await asyncio.sleep(0.8)
        return {"ok": True, "detail": msg[:300], **(await _look(sess, allowed))}


@mcp.tool()
async def apps(ctx: Context, allowed: list[str]) -> dict:
    """Os apps instalados que estão liberados."""
    sess = await _session(_sid(ctx))
    text, _, _ = await _call(sess, "list_apps", {})
    names = sorted({m.group(1).strip() for m in re.finditer(r"^- ([^(\n]+?)(?: \(|$)", text, re.M)})
    return {"apps": [n for n in names if _app_ok(n, allowed)]}


@mcp.tool(name="open")
async def open_app(ctx: Context, app: str, allowed: list[str]) -> dict:
    """Abre um app liberado e devolve a tela."""
    if not _app_ok(app, allowed):
        raise ToolError(f"o app '{app}' não está liberado para este funcionário")
    sess = await _session(_sid(ctx))
    async with sess["lock"]:
        # já aberto (antes de uma aprovação, por exemplo): volta à janela, com o que foi preenchido
        open_ = [w for w in await _windows(sess) if _app_ok(f"{w.get('app_name', '')} {w.get('title', '')}", [app])]
        if open_:
            w = sorted(open_, key=lambda w: -(w.get("z_index") or 0))[0]
            sess["pid"], sess["window"] = w["pid"], w["window_id"]
            return await _look(sess, allowed)
        text, _, sc = await _call(sess, "launch_app", {"name": app})
        m = re.search(r"pid (\d+)", text)
        sess["pid"], sess["window"] = (int(m.group(1)) if m else sc.get("pid")), None
        for _ in range(20):
            await asyncio.sleep(0.5)
            if any(w.get("pid") == sess["pid"] for w in await _windows(sess)):
                break
        return await _look(sess, allowed)


@mcp.tool()
async def look(ctx: Context, allowed: list[str], window: str = "") -> dict:
    """A tela do app da tarefa: captura e elementos (com número para clicar)."""
    sess = await _session(_sid(ctx))
    async with sess["lock"]:
        return await _look(sess, allowed, window)


@mcp.tool()
async def click(ctx: Context, allowed: list[str], element: str = "", x: float | None = None, y: float | None = None,
                double: bool = False) -> dict:
    async def go(sess):
        text, _, _ = await _call(sess, "double_click" if double else "click", _where(sess, element or None, x, y))
        return text
    return await _act(ctx, allowed, go)


@mcp.tool()
async def type_text(ctx: Context, allowed: list[str], text: str, element: str = "", x: float | None = None,
                    y: float | None = None) -> dict:
    async def go(sess):
        if element or (x is not None and y is not None):
            await _call(sess, "click", _where(sess, element or None, x, y))
            await asyncio.sleep(0.3)
        out, _, _ = await _call(sess, "type_text", {"pid": sess["pid"], "window_id": sess["window"], "text": text,
                                                    "delivery_mode": "foreground"})
        return re.sub(re.escape(text), "[texto]", out) if text else out
    return await _act(ctx, allowed, go)


@mcp.tool()
async def key(ctx: Context, allowed: list[str], key: str, modifiers: list[str] | None = None) -> dict:
    async def go(sess):
        args = {"pid": sess["pid"], "window_id": sess["window"], "delivery_mode": "foreground"}
        if modifiers:
            out, _, _ = await _call(sess, "hotkey", {**args, "keys": [*modifiers, key]})
        else:
            out, _, _ = await _call(sess, "press_key", {**args, "key": key})
        return out
    return await _act(ctx, allowed, go)


@mcp.tool()
async def scroll(ctx: Context, allowed: list[str], direction: str = "down", amount: int = 3) -> dict:
    async def go(sess):
        out, _, _ = await _call(sess, "scroll", {"pid": sess["pid"], "window_id": sess["window"], "direction": direction,
                                                 "amount": int(amount), "delivery_mode": "foreground"})
        return out
    return await _act(ctx, allowed, go)


@mcp.tool()
async def close(ctx: Context) -> dict:
    """Encerra a sessão da tarefa (os apps continuam abertos no desktop)."""
    await _close(_sid(ctx))
    return {"closed": True}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request):
    return JSONResponse({"ok": True, "sessions": len(_sessions)})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="warning")
