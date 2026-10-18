"""CORS por agente: um site (origem) listado na spec pode chamar o agente pelo gateway direto do navegador.

Vale a versão que está rodando em cada ambiente (/gw = produção, /gw-stage = stage): mudar as origens é uma versão
nova, que passa por stage e pela promoção como qualquer mudança. Sem curinga e sem cookies
(Access-Control-Allow-Credentials nunca vai): a página chama com uma chave no cabeçalho Authorization.
"""
import re
import time

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Agent
from .common import spec_of
from .runtime import active_deployment

GW_PATH = re.compile(r"^/gw(-stage)?/([^/]+)/")
TTL_S = 15  # uma mudança de origens (deploy, ship, parada) vale em até 15 s
ALLOW_HEADERS = ("authorization, content-type, accept, last-event-id, x-channel, x-conversation-id, x-session-id, "
                 "mcp-protocol-version, mcp-session-id")
_cache: dict[tuple[str, str], tuple[float, frozenset]] = {}


def target(path: str) -> tuple[str, str] | None:
    """(slug, env) de um caminho do gateway público, ou None."""
    m = GW_PATH.match(path)
    return (m.group(2), "stage" if m.group(1) else "prod") if m else None


def _load(slug: str, env: str) -> frozenset:
    with SessionLocal() as db:
        a = db.scalar(select(Agent).where(Agent.slug == slug))
        d = active_deployment(a, env) if a else None
        if d is None:
            return frozenset()
        try:
            spec = spec_of(a, d.version)
        except Exception:  # versão apagada: sem origens
            return frozenset()
        return frozenset((spec.get("cors") or {}).get("origins") or [])


def origins(slug: str, env: str) -> frozenset:
    now = time.monotonic()
    hit = _cache.get((slug, env))
    if hit and hit[0] > now:
        return hit[1]
    value = _load(slug, env)
    if len(_cache) > 5000:
        _cache.clear()
    _cache[(slug, env)] = (now + TTL_S, value)
    return value


def forget(slug: str):
    """Depois de um deploy, ship ou parada: as origens novas valem já, sem esperar o cache."""
    for env in ("stage", "prod"):
        _cache.pop((slug, env), None)


def headers(origin: str, preflight: bool) -> list[tuple[bytes, bytes]]:
    h = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin"),
         (b"access-control-expose-headers", b"mcp-session-id")]
    if preflight:
        h += [(b"access-control-allow-methods", b"GET, POST, OPTIONS"),
              (b"access-control-allow-headers", ALLOW_HEADERS.encode()), (b"access-control-max-age", b"600")]
    return h
