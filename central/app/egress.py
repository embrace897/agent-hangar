"""Proxy de saída dos plugins com código (runtime: server): só libera os hosts declarados em `server.egress`.

O container do plugin roda numa rede sem saída para a internet (Docker: rede `internal`; Kubernetes: NetworkPolicy) e
recebe HTTP(S)_PROXY apontando para cá, com um usuário e senha próprios da instalação. Cada conexão (CONNECT para
HTTPS, ou HTTP simples) é conferida contra a lista da versão do manifesto daquela instalação. O host é resolvido aqui e a
conexão vai para o IP já conferido (sem troca de DNS no meio); redes internas só com PLUGIN_ALLOW_PRIVATE=1. O que for
recusado fica registrado na instalação, para o mantenedor ver o motivo na tela do plugin.
"""
import asyncio
import base64
import fnmatch
import hashlib
import hmac
import ipaddress
import logging
import re
import socket
import time

from . import config

log = logging.getLogger("hangar.egress")
HEAD_MAX = 16_384
CACHE_S = 30
_cache: dict[int, tuple[float, list[str]]] = {}
_server: asyncio.base_events.Server | None = None
_TPL = re.compile(r"\{\{\s*settings\.([a-zA-Z0-9_]+)\s*\}\}")


def token(install_id: int) -> str:
    """Senha do proxy da instalação: derivada do segredo interno (nada a guardar; muda se o segredo mudar)."""
    return hmac.new(config.INTERNAL_SECRET.encode(), f"egress:{install_id}".encode(), hashlib.sha256).hexdigest()[:40]


def proxy_url(install_id: int) -> str:
    return f"http://i{install_id}:{token(install_id)}@{config.PLUGIN_EGRESS_HOST}:{config.PLUGIN_EGRESS_PORT}"


def env(install_id: int) -> dict:
    """Variáveis que levam o servidor do plugin a sair pelo proxy (Python, Go, curl; Node 24+ com NODE_USE_ENV_PROXY)."""
    if config.PLUGIN_EGRESS != "proxy":
        return {}
    url = proxy_url(install_id)
    return {"HTTP_PROXY": url, "HTTPS_PROXY": url, "http_proxy": url, "https_proxy": url, "NO_PROXY": "",
            "no_proxy": "", "NODE_USE_ENV_PROXY": "1"}


def allowed(patterns: list[str], host: str, port: int) -> bool:
    """`api.x.com` e `*.x.com` liberam as portas 443 e 80; `host:porta` libera só aquela porta."""
    host = host.lower().rstrip(".")
    for p in patterns:
        p = p.strip().lower()
        if not p:
            continue
        name, _, want = p.partition(":")
        if want and (not want.isdigit() or int(want) != port):
            continue
        if not want and port not in (443, 80):
            continue
        if host == name or (name.startswith("*.") and fnmatch.fnmatch(host, name) and host.count(".") >= name.count(".")):
            return True
    return False


def _patterns_from_db(install_id: int) -> list[str] | None:
    from . import db as dbmod
    from .models import Plugin, PluginInstall
    from .services import plugins as pl
    with dbmod.SessionLocal() as s:
        i = s.get(PluginInstall, install_id)
        p = s.get(Plugin, i.plugin_id) if i else None
        if i is None or p is None or not i.enabled:
            return None
        m = pl.mof(p, i) or {}
        values = pl.setting_values(m, i)
        out = []
        for e in (m.get("server") or {}).get("egress") or []:
            out.append(_TPL.sub(lambda mt: str(values.get(mt.group(1)) or ""), e))
        return out


def _note_denied(install_id: int, host: str, port: int, why: str):
    from . import db as dbmod
    from .models import PluginInstall, now
    from .services.common import iso
    with dbmod.SessionLocal() as s:
        i = s.get(PluginInstall, install_id)
        if i is None:
            return
        st = dict(i.stats or {})
        st["egress_denied"] = int(st.get("egress_denied", 0)) + 1
        st["egress_last_denied"] = {"host": f"{host}:{port}", "why": why, "at": iso(now())}
        i.stats = st
        s.commit()


async def _patterns(install_id: int) -> list[str] | None:
    hit = _cache.get(install_id)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    pats = await asyncio.to_thread(_patterns_from_db, install_id)
    _cache[install_id] = (time.monotonic(), pats)
    return pats


def _who(headers: dict) -> int | None:
    raw = headers.get("proxy-authorization", "")
    if not raw.lower().startswith("basic "):
        return None
    try:
        user, _, pw = base64.b64decode(raw[6:].strip()).decode().partition(":")
        iid = int(user.removeprefix("i"))
    except (ValueError, UnicodeDecodeError):
        return None
    return iid if hmac.compare_digest(pw, token(iid)) else None


async def _resolve(host: str, port: int) -> str:
    """O IP para conectar; recusa redes internas (a menos que liberadas)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    addrs = [x[4][0] for x in infos]
    if not addrs:
        raise OSError("sem endereço")
    if not config.PLUGIN_ALLOW_PRIVATE:
        for a in addrs:
            ip = ipaddress.ip_address(a.split("%")[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise PermissionError("rede interna (libere com PLUGIN_ALLOW_PRIVATE=1)")
    return addrs[0]


async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass


async def _reply(w: asyncio.StreamWriter, code: int, text: str, extra: str = ""):
    body = text.encode()
    w.write(f"HTTP/1.1 {code} {text[:40]}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
            f"Connection: close\r\n{extra}\r\n".encode() + body)
    try:
        await w.drain()
    finally:
        w.close()


async def _handle(r: asyncio.StreamReader, w: asyncio.StreamWriter):
    try:
        head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), timeout=15)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        w.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        return await _reply(w, 400, "bad request")
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    iid = _who(headers)
    if iid is None:
        return await _reply(w, 407, "proxy authentication required", 'Proxy-Authenticate: Basic realm="hangar-egress"\r\n')
    if method.upper() == "CONNECT":
        host, _, port_s = target.rpartition(":")
        rest = b""
    else:
        m = re.match(r"^http://([^/:?#]+)(?::(\d+))?(.*)$", target, re.I)
        if not m:
            return await _reply(w, 400, "use CONNECT (https) ou uma URL http:// absoluta")
        host, port_s, path = m.group(1), m.group(2) or "80", m.group(3) or "/"
        keep = [ln for ln in lines[1:] if ln and not ln.lower().startswith(("proxy-", "connection:"))]
        rest = "\r\n".join([f"{method} {path} {version}", *keep, "Connection: close", "", ""]).encode("latin-1")
    host = host.strip("[]")
    try:
        port = int(port_s)
    except ValueError:
        return await _reply(w, 400, "porta inválida")
    pats = await _patterns(iid)
    if pats is None:
        return await _reply(w, 403, "instalação desligada ou removida")
    if not allowed(pats, host, port):
        why = "host não declarado em server.egress"
        log.info("egress: instalação %s recusada para %s:%s (%s)", iid, host, port, why)
        await asyncio.to_thread(_note_denied, iid, host, port, why)
        return await _reply(w, 403, f"egress recusado: {host}:{port} não está em server.egress")
    try:
        ip = await _resolve(host, port)
        ur, uw = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=15)
    except PermissionError as e:
        await asyncio.to_thread(_note_denied, iid, host, port, str(e))
        return await _reply(w, 403, f"egress recusado: {host} está numa {e}")
    except (TimeoutError, OSError) as e:
        return await _reply(w, 502, f"não conectou em {host}:{port}: {type(e).__name__}")
    if method.upper() == "CONNECT":
        w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await w.drain()
    else:
        uw.write(rest)
        await uw.drain()
    await asyncio.gather(_pipe(r, uw), _pipe(ur, w))


async def start():
    """Sobe o proxy (no processo da central). PLUGIN_EGRESS=open desliga: o plugin sai direto (sem lista)."""
    global _server
    if config.PLUGIN_EGRESS != "proxy" or _server is not None:
        return
    try:
        _server = await asyncio.start_server(_handle, "0.0.0.0", config.PLUGIN_EGRESS_PORT, limit=HEAD_MAX,
                                             reuse_address=True)
        log.info("proxy de saída dos plugins na porta %s", config.PLUGIN_EGRESS_PORT)
    except OSError as e:
        log.warning("proxy de saída dos plugins não subiu na porta %s: %s", config.PLUGIN_EGRESS_PORT, e)


async def stop():
    global _server
    if _server is not None:
        _server.close()
        _server = None
