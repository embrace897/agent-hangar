"""Navegador governado: um MCP (Playwright/Chromium) que a central usa para os Digital employees operarem sistemas sem
API — portais de banco, sites de governo, ERPs antigos.

Uma sessão (x-session-id) é uma tarefa: contexto de navegador próprio, que some depois de BROWSER_IDLE_MIN sem uso.
Cada chamada traz a lista de domínios liberados (`allowed`): navegação para fora dela é bloqueada (a página nem
carrega). A alçada, os segredos e a auditoria ficam na central: aqui só se executa o passo pedido, e cada resposta
diz se a página pede CAPTCHA ou verificação em duas etapas (nunca contornadas: uma pessoa resolve).
"""
import asyncio
import base64
import logging
import os
import re
import time
from urllib.parse import urlparse

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from playwright.async_api import async_playwright
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("browser")
IDLE_MIN = float(os.environ.get("BROWSER_IDLE_MIN", "15"))
MAX_SESSIONS = int(os.environ.get("BROWSER_MAX_SESSIONS", "8"))
TIMEOUT_MS = int(os.environ.get("BROWSER_TIMEOUT_MS", "20000"))
CAPTCHA = re.compile(r"recaptcha|hcaptcha|turnstile|captcha|não sou um robô|nao sou um robo|i'm not a robot|"
                     r"código de verificação|codigo de verificacao|verification code|two-factor|autenticação em duas", re.I)

mcp = FastMCP("browser", stateless_http=True, json_response=True, streamable_http_path="/mcp",
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
_pw = None
_browser = None
_sessions: dict[str, dict] = {}
_lock = asyncio.Lock()


def _sid(ctx: Context) -> str:
    req = getattr(ctx.request_context, "request", None)
    sid = (req.headers.get("x-session-id") if req is not None else "") or ""
    if not re.fullmatch(r"[\w.-]{1,120}", sid):
        raise ToolError("sessão inválida (x-session-id)")
    return sid


def _host_ok(url: str, allowed: list[str]) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return bool(h) and any(h == d or h.endswith("." + d) for d in allowed)


async def _session(sid: str, allowed: list[str]):
    global _pw, _browser
    allowed = [d.lower().strip() for d in allowed or [] if d.strip()]
    if not allowed:
        raise ToolError("nenhum domínio liberado para este navegador")
    async with _lock:
        if _browser is None:
            _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        s = _sessions.get(sid)
        if s is None:
            if len(_sessions) >= MAX_SESSIONS:
                oldest = min(_sessions, key=lambda k: _sessions[k]["used"])
                await _close(oldest)
            ctx = await _browser.new_context(accept_downloads=False, locale="pt-BR", viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            s = {"ctx": ctx, "page": page, "allowed": allowed, "used": time.time(), "blocked": []}

            async def guard(route):
                req = route.request
                if req.is_navigation_request() and not _host_ok(req.url, s["allowed"]):
                    s["blocked"].append(req.url)
                    return await route.abort("blockedbyclient")
                return await route.continue_()
            await ctx.route("**/*", guard)
            _sessions[sid] = s
        s["allowed"], s["used"] = allowed, time.time()
        return s


async def _close(sid: str):
    s = _sessions.pop(sid, None)
    if s is not None:
        try:
            await s["ctx"].close()
        except Exception:  # noqa: BLE001
            pass


async def _idle_loop():
    while True:
        await asyncio.sleep(60)
        for sid in [k for k, v in list(_sessions.items()) if time.time() - v["used"] > IDLE_MIN * 60]:
            await _close(sid)


async def _state(s, shot: bool = False) -> dict:
    page = s["page"]
    try:
        text = await page.inner_text("body", timeout=5000)
    except Exception:  # noqa: BLE001
        text = ""
    frames = " ".join(f.url for f in page.frames)
    out = {"url": page.url, "title": await page.title(), "captcha": bool(CAPTCHA.search(text[:20000]) or CAPTCHA.search(frames))}
    if s["blocked"]:
        out["blocked"], s["blocked"] = s["blocked"][-3:], []
    if shot:
        out["screenshot"] = base64.b64encode(await page.screenshot(full_page=False)).decode()
    return out


async def _locate(page, target: str):
    """Por texto visível (botão, link, rótulo) ou seletor CSS."""
    t = (target or "").strip()
    if not t:
        raise ToolError("diga o alvo (texto visível ou seletor CSS)")
    if t[0] in "#.[" or t.startswith(("css=", "xpath=")) or re.match(r"^[a-z]+\[", t):
        loc = page.locator(t.removeprefix("css="))
    else:
        loc = page.get_by_role("button", name=t)
        if await loc.count() == 0:
            loc = page.get_by_role("link", name=t)
        if await loc.count() == 0:
            loc = page.get_by_label(t)
        if await loc.count() == 0:
            loc = page.get_by_placeholder(t)
        if await loc.count() == 0:
            loc = page.get_by_text(t, exact=False)
    if await loc.count() == 0:
        raise ToolError(f"não achei '{t}' na página")
    return loc.first


@mcp.tool()
async def open(url: str, allowed: list[str], ctx: Context) -> dict:  # noqa: A001  (nome da ferramenta)
    """Abre uma página (só nos domínios liberados)."""
    if not _host_ok(url, allowed):
        raise ToolError(f"{urlparse(url).hostname} não está entre os domínios liberados")
    s = await _session(_sid(ctx), allowed)
    await s["page"].goto(url, wait_until="domcontentloaded")
    return await _state(s)


@mcp.tool()
async def read(allowed: list[str], ctx: Context, max_chars: int = 6000) -> dict:
    """O texto visível da página e os campos, botões e links que dá para usar."""
    s = await _session(_sid(ctx), allowed)
    page = s["page"]
    st = await _state(s)
    st["text"] = (await page.inner_text("body", timeout=5000))[:max(500, min(max_chars, 20000))]
    items = []
    for role in ("textbox", "combobox", "checkbox", "button", "link"):
        loc = page.get_by_role(role)
        for i in range(min(await loc.count(), 25)):
            el = loc.nth(i)
            try:
                name = (await el.get_attribute("aria-label")) or (await el.inner_text(timeout=500)) or (await el.get_attribute("name")) \
                    or (await el.get_attribute("placeholder")) or ""
            except Exception:  # noqa: BLE001
                name = ""
            if name.strip():
                items.append({"role": role, "name": name.strip()[:80]})
    st["elements"] = items[:80]
    return st


@mcp.tool()
async def click(target: str, allowed: list[str], ctx: Context) -> dict:
    """Clica num botão ou link (pelo texto visível ou seletor) e espera a página."""
    s = await _session(_sid(ctx), allowed)
    el = await _locate(s["page"], target)
    await el.click()
    try:
        await s["page"].wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    st = await _state(s)
    try:  # o efeito do clique: o texto da página que abriu
        st["text"] = (await s["page"].inner_text("body", timeout=5000))[:3000]
    except Exception:  # noqa: BLE001
        pass
    return st


@mcp.tool()
async def fill(target: str, value: str, allowed: list[str], ctx: Context) -> dict:
    """Preenche um campo (pelo rótulo, placeholder ou seletor). O valor não volta na resposta."""
    s = await _session(_sid(ctx), allowed)
    el = await _locate(s["page"], target)
    await el.fill(value)
    return await _state(s)


@mcp.tool()
async def select(target: str, value: str, allowed: list[str], ctx: Context) -> dict:
    """Escolhe uma opção numa lista."""
    s = await _session(_sid(ctx), allowed)
    el = await _locate(s["page"], target)
    await el.select_option(label=value)
    return await _state(s)


@mcp.tool()
async def screenshot(allowed: list[str], ctx: Context) -> dict:
    """Captura da tela atual (PNG em base64)."""
    s = await _session(_sid(ctx), allowed)
    return await _state(s, shot=True)


@mcp.tool()
async def close(ctx: Context) -> str:
    """Fecha o navegador desta sessão."""
    await _close(_sid(ctx))
    return "fechado"


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request):
    return JSONResponse({"status": "ok", "sessions": len(_sessions)})


async def main():
    logging.basicConfig(level=logging.INFO)
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), host="0.0.0.0", port=8000, log_level="info"))
    await asyncio.gather(server.serve(), _idle_loop())


if __name__ == "__main__":
    asyncio.run(main())
