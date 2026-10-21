"""Catálogo Docker MCP (profile `mcp-gateway`): 230+ servidores MCP prontos, cada um num container isolado.

Admins escolhem os servidores na página Catálogo; a central guarda a escolha (segredos criptografados) e escreve
no volume compartilhado os arquivos que o Docker MCP Gateway observa (--watch): registry.yaml (quais servidores),
config.yaml (configuração de cada um) e secrets.env. Cada servidor ativado vira um item do catálogo do Hangar
(`docker:<nome>`) com prefixo de ferramentas — um agente que usa `docker:fetch` não enxerga as ferramentas dos
outros servidores do mesmo gateway."""
import asyncio
import json
import logging
import os
import re
import time

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, crypto
from ..models import GatewayServer, McpServer, now
from .access import Access, Forbidden
from .catalog import upsert_mcp
from .common import PlatformError, audit

log = logging.getLogger("hangar.mcp_gateway")
PREFIX = "docker:"
_CACHE: dict = {"at": 0.0, "servers": {}}
HTTP = lambda: httpx.Client(timeout=30, follow_redirects=True)  # noqa: E731  (os testes trocam)
NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def _admin(acc: Access):
    if not acc.p.is_admin:
        raise Forbidden("só admins ativam servidores do catálogo Docker MCP")


# ------------------------------------------------------------------ catálogo da Docker
def _parse(doc: dict) -> dict[str, dict]:
    out = {}
    for name, v in ((doc or {}).get("registry") or {}).items():
        if v.get("type") != "server" or not v.get("image"):
            continue  # "remote" (quase sempre OAuth) e "poci" ficam de fora nesta versão
        meta = v.get("metadata") or {}
        cfg = v.get("config") or []
        props = (cfg[0] or {}).get("properties", {}) if cfg else {}
        out[name] = {
            "name": name, "title": v.get("title") or name, "description": v.get("description", ""),
            "icon": v.get("icon", ""), "image": v["image"], "category": meta.get("category", ""),
            "tags": meta.get("tags") or [],
            "secrets": [{"name": s["name"], "env": s.get("env", ""), "description": s.get("description", ""),
                         "example": s.get("example", "")} for s in v.get("secrets") or []],
            "config": [{"name": k, "type": (p or {}).get("type", "string"),
                        "description": (p or {}).get("description", "")} for k, p in props.items()],
            "oauth": bool(v.get("oauth")),
            "tools": [t.get("name") for t in v.get("tools") or [] if isinstance(t, dict)],
            "source": v.get("source", ""),
        }
    return out


def _parse_remotes(doc: dict) -> dict[str, dict]:
    """Os servidores "remote" do catálogo (HTTP, rodam no fornecedor): viram conectores pela ponte da central."""
    out = {}
    for name, v in ((doc or {}).get("registry") or {}).items():
        if v.get("type") != "remote":
            continue
        rem = v.get("remote") if isinstance(v.get("remote"), dict) else {}
        url = rem.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        meta = v.get("metadata") or {}
        out[name] = {"name": name, "title": v.get("title") or name, "description": v.get("description", ""),
                     "icon": v.get("icon", ""), "url": url, "category": meta.get("category", ""),
                     "transport": "sse" if (rem.get("transport_type") == "sse" or url.rstrip("/").endswith("/sse")) else "http",
                     "oauth": bool(v.get("oauth")), "tags": meta.get("tags") or []}
    return out


def remotes() -> dict[str, dict]:
    catalog()
    return _CACHE.get("remotes") or {}


def catalog(force: bool = False) -> dict[str, dict]:
    if not force and _CACHE["servers"] and time.time() - _CACHE["at"] < config.MCP_GATEWAY_CATALOG_TTL_S:
        return _CACHE["servers"]
    try:
        with HTTP() as http:
            r = http.get(config.MCP_GATEWAY_CATALOG_URL)
            r.raise_for_status()
        doc = yaml.safe_load(r.text)
        _CACHE.update(at=time.time(), servers=_parse(doc), remotes=_parse_remotes(doc))
    except Exception as e:
        if not _CACHE["servers"]:
            raise PlatformError(f"não foi possível ler o catálogo Docker MCP ({type(e).__name__}): {e}") from None
        log.warning("catálogo Docker MCP: usando o cache (%s)", e)
    return _CACHE["servers"]


def search(db: Session, acc: Access, q: str = "", limit: int = 60) -> dict:
    _admin(acc)
    cat = catalog()
    enabled = {g.name for g in db.scalars(select(GatewayServer))}
    q = (q or "").strip().lower()
    items = [s for s in cat.values()
             if not q or q in " ".join([s["name"], s["title"], s["description"], *s["tags"]]).lower()]
    items.sort(key=lambda s: (s["name"] not in enabled, bool(s["secrets"] or s["config"]), s["title"].lower()))
    return {"total": len(cat), "matches": len(items), "enabled": sorted(enabled),
            "servers": [{**s, "enabled": s["name"] in enabled} for s in items[:limit]]}


# ------------------------------------------------------------------ estado -> arquivos do gateway
def _secrets(g: GatewayServer) -> dict:
    return json.loads(crypto.decrypt(g.secrets)) if g.secrets else {}


def write_files(db: Session) -> str:
    """Reescreve registry/config/secrets no volume do gateway. Escreve no MESMO arquivo (não troca por outro com
    rename): o --watch do gateway segue o arquivo, e depois de um rename ele passa a vigiar um arquivo que não existe
    mais — só a primeira mudança era percebida, e um servidor ativado depois só subia quando o gateway reiniciava."""
    d = config.MCP_GATEWAY_CONFIG_DIR
    if not d:
        return ""
    os.makedirs(d, exist_ok=True)
    rows = db.scalars(select(GatewayServer).order_by(GatewayServer.name)).all()
    cfg = {g.name: g.config for g in rows if g.config}
    files = {
        "registry.yaml": yaml.safe_dump({"registry": {g.name: {"ref": ""} for g in rows}}, sort_keys=True),
        "config.yaml": yaml.safe_dump(cfg, sort_keys=True) if cfg else "{}\n",
        "secrets.env": "".join(f"{k}={v}\n" for g in rows for k, v in sorted(_secrets(g).items())),
    }
    for name, content in files.items():
        path = os.path.join(d, name)
        if name == "secrets.env" and not os.path.exists(path):
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o640))  # nasce sem leitura para os outros
        with open(path, "r+" if os.path.exists(path) else "w", encoding="utf-8") as f:
            f.seek(0)
            f.write(content)
            f.truncate()
        if name == "secrets.env":
            os.chmod(path, 0o640)
    return d


def enable(db: Session, acc: Access, name: str, secrets: dict | None = None, cfg: dict | None = None) -> dict:
    """Ativa (ou atualiza) um servidor do catálogo e o publica no catálogo do Hangar como `docker:<nome>`."""
    _admin(acc)
    if not NAME.match(name or ""):
        raise PlatformError("nome de servidor inválido")
    s = catalog().get(name)
    if not s:
        raise PlatformError(f"'{name}' não está no catálogo Docker MCP (ou é remoto/OAuth, ainda não suportado)")
    g = db.scalar(select(GatewayServer).where(GatewayServer.name == name)) or GatewayServer(name=name)
    current = _secrets(g) if g.id else {}
    wanted = {x["name"] for x in s["secrets"]}
    unknown = set(secrets or {}) - wanted
    if unknown:
        raise PlatformError(f"segredos desconhecidos para {name}: {sorted(unknown)}")
    merged = {**current, **{k: v for k, v in (secrets or {}).items() if v}}  # vazio mantém o valor salvo
    missing = sorted(wanted - set(merged))
    if missing:
        raise PlatformError(f"{name} precisa dos segredos: {', '.join(missing)}")
    g.secrets = crypto.encrypt(json.dumps(merged)) if merged else ""
    g.config = {k: v for k, v in (cfg if cfg is not None else (g.config or {})).items() if v not in ("", None)}
    g.enabled_by, g.enabled_at = acc.p.name, now()
    db.add(g)
    db.commit()
    write_files(db)
    upsert_mcp(db, PREFIX + name, config.MCP_GATEWAY_URL,
               f"{s['title']} (catálogo Docker MCP). {s['description']}"[:1000], acc.p.name, tool_prefix=f"{name}__")
    audit(db, acc.p.name, "mcp_gateway.enable", name, f"segredos={sorted(merged)} config={sorted(g.config)}")
    return {"name": name, "catalog_mcp": PREFIX + name, "tool_prefix": f"{name}__", "secrets": sorted(merged),
            "config": g.config}


def disable(db: Session, acc: Access, name: str):
    _admin(acc)
    g = db.scalar(select(GatewayServer).where(GatewayServer.name == name))
    if not g:
        raise PlatformError(f"'{name}' não está ativo")
    db.delete(g)
    m = db.scalar(select(McpServer).where(McpServer.name == PREFIX + name))
    if m:
        db.delete(m)  # agentes que o usavam deixam de enxergá-lo no próximo deploy
    db.commit()
    write_files(db)
    audit(db, acc.p.name, "mcp_gateway.disable", name)


async def _probe() -> list[str]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(config.MCP_GATEWAY_URL, timeout=15) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]


def status(db: Session, acc: Access) -> dict:
    """Gateway no ar? Quais ferramentas cada servidor ativo expõe (lista via MCP, o mesmo caminho dos agentes)."""
    _admin(acc)
    enabled = [g.name for g in db.scalars(select(GatewayServer).order_by(GatewayServer.name))]
    try:
        tools = asyncio.run(_probe())
    except Exception as e:
        return {"online": False, "url": config.MCP_GATEWAY_URL, "enabled": enabled,
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "hint": "suba o gateway: docker compose --profile mcp-gateway up -d"}
    return {"online": True, "url": config.MCP_GATEWAY_URL, "enabled": enabled, "total_tools": len(tools),
            "tools": {n: [t[len(n) + 2:] for t in tools if t.startswith(n + "__")] for n in enabled}}
