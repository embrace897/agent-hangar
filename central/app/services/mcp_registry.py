"""Registro MCP do GitHub (github.com/mcp): mais uma origem do catálogo de conectores, ao lado dos curados, do catálogo
Docker e dos personalizados.

A lista vem da API pública do registro (MCP_REGISTRY_URL) e é renovada em segundo plano a cada MCP_REGISTRY_TTL_S, por
uma réplica só. Cada servidor vira um item com o jeito mais simples de conectar:

- remoto com OAuth e registro dinâmico de cliente (DCR): um clique (a mesma descoberta que a instalação usa);
- remoto aberto (o `initialize` responde sem credencial): um clique;
- remoto que pede cabeçalho (o registro diz o nome e a descrição de cada variável) ou token (401 sem OAuth);
- remoto com endereço por cliente ({variável} na URL): pede o dado da conta;
- pacote npm/PyPI com versão fixa: roda no runner isolado (só admins instalam, como todo servidor local).

Fica de fora o que não dá para conectar assim: fora do ar, OAuth sem DCR, pacote docker/binário, versão solta ou
argumento obrigatório. A sondagem só fala com endereços https públicos (nada de rede interna) e não segue
redirecionamentos. O que já existe no catálogo (curado ou Docker) não aparece de novo: o filtro é em connectors.catalog.
"""
import asyncio
import logging
import re
import threading
import time
import urllib.parse

import httpx

from .. import config, shared

log = logging.getLogger("hangar.mcp_registry")
KEY = "mcp_registry:snapshot"
KEEP_S = 7 * 86400  # se a renovação falhar, o retrato anterior vale por uma semana
CONCURRENCY = 24
PROBE_TIMEOUT_S = 10
_MEM: dict = {"at": 0.0, "snap": None}
_RUNNING = threading.Lock()
VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "agent-hangar-catalog", "version": config.VERSION}}}
# tópicos do repositório -> seções da tela de conectores
TOPIC_SECTION = [
    ("data", r"database|postgres|mysql|sql|sqlite|mongo|redis|analytics|bigquery|snowflake|warehouse|\bdata\b"),
    ("communication", r"slack|email|gmail|discord|telegram|whatsapp|chat|sms|messag"),
    ("sales", r"crm|sales|hubspot|salesforce|marketing|seo|ecommerce|shopify"),
    ("finance", r"financ|payment|stripe|crypto|blockchain|solana|ethereum|trading|accounting|invoice"),
    ("monitoring", r"monitor|observab|metrics|logging|grafana|datadog|alert"),
    ("security", r"secur|pentest|vulnerab|auth0|identity"),
    ("dev", r"devops|developer|github|gitlab|testing|browser|playwright|kubernetes|docker|cloud|azure|aws|code"),
    ("ai", r"search|\bai\b|llm|rag|embedding|research|scrap|crawl"),
    ("productivity", r"productivity|project|notes|docs|documentation|calendar|task|notion|knowledge|diagram"),
]


def _github(entry: dict) -> dict:
    meta = (entry.get("_meta") or {}).get("io.modelcontextprotocol.registry/publisher-provided") or {}
    return meta.get("github") or {}


def slug_of(name: str) -> str:
    """io.github.netdata/mcp-server -> gh-netdata-mcp-server (vale como nome de conector: 2 a 61 caracteres)."""
    s = re.sub(r"^io\.github\.", "", (name or "").lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return ("gh-" + s)[:61].rstrip("-")


def _title(name: str, gh: dict) -> str:
    last = (name or "").split("/")[-1]
    pretty = re.sub(r"[-_]+", " ", re.sub(r"(^|[-_])(mcp|server)(?=$|[-_])", " ", last, flags=re.I)).strip()
    shown = gh.get("display_name") or ""
    if pretty and shown and pretty.lower() not in shown.lower() and shown.lower() not in pretty.lower():
        return f"{shown} · {pretty.title()}"[:80]
    return (shown or pretty.title() or last)[:80]


def _section(entry: dict, gh: dict) -> str:
    # só a última parte do nome: "io.github.fulano/..." casaria com "github" e jogaria tudo em desenvolvimento
    text = " ".join([*(gh.get("topics") or []), (entry.get("name") or "").split("/")[-1],
                     entry.get("description", "")]).lower()
    return next((sec for sec, rx in TOPIC_SECTION if re.search(rx, text)), "other")


def _remote(entry: dict) -> dict | None:
    rems = [r for r in entry.get("remotes") or [] if str(r.get("url", "")).startswith(("https://", "http://", "{"))]
    rem = next((r for r in rems if (r.get("transport_type") or r.get("type")) == "streamable-http"), rems[0] if rems else None)
    if rem is None:
        return None
    url = rem["url"]
    variables = rem.get("variables") or {}
    url_vars = [{"name": v, "description": (variables.get(v) or {}).get("description", ""),
                 "secret": bool((variables.get(v) or {}).get("is_secret"))} for v in dict.fromkeys(VAR.findall(url))]
    headers = []
    for h in rem.get("headers") or []:
        if not (h.get("is_required") or h.get("isRequired")):
            continue
        value = h.get("value") or "{" + re.sub(r"\W", "_", h.get("name", "TOKEN")).upper() + "}"
        hv = h.get("variables") or {}
        names = list(dict.fromkeys(VAR.findall(value)))
        headers.append({"name": h.get("name", ""), "value": value, "secret": bool(h.get("is_secret") or h.get("isSecret")),
                        "description": h.get("description", ""),
                        "variables": [{"name": v, "description": (hv.get(v) or {}).get("description", "") or h.get("description", ""),
                                       "secret": bool((hv.get(v) or {}).get("is_secret", h.get("is_secret")))}
                                      for v in names]})
    return {"url": url, "transport": "sse" if (rem.get("transport_type") or rem.get("type")) == "sse" else "http",
            "url_vars": url_vars, "headers": headers}


def _package(entry: dict) -> dict | None:
    """O primeiro pacote que o runner do Hangar roda: npm (npx) ou PyPI (uvx), com versão fixa e sem argumento
    obrigatório (o registro não diz o valor)."""
    for p in entry.get("packages") or []:
        ident, version = p.get("identifier") or p.get("name") or "", p.get("version") or ""
        hint, base = (p.get("runtime_hint") or "").lower(), (p.get("registry_base_url") or "").lower()
        runner = ("npx" if hint == "npx" or "npmjs" in base or (not hint and ident.startswith("@"))
                  else "uvx" if hint == "uvx" or "pypi" in base else "")
        if not runner or not ident or "://" in ident or not re.match(r"^\d", version):
            continue
        if any(a.get("is_required") or a.get("isRequired") for a in p.get("package_arguments") or []):
            continue
        env = [{"name": e.get("name", ""), "description": e.get("description", ""),
                "secret": bool(e.get("is_secret") or e.get("isSecret")),
                "required": bool(e.get("is_required") or e.get("isRequired"))}
               for e in p.get("environment_variables") or p.get("environmentVariables") or [] if e.get("name")]
        package = f"{ident}@{version}" if runner == "npx" else f"{ident}=={version}"
        return {"runner": runner, "package": package, "env": env}
    return None


def parse(entry: dict) -> dict | None:
    """Um servidor do registro -> item do catálogo (sem a sondagem). None quando não há jeito de conectar."""
    name = entry.get("name") or ""
    if not name or (entry.get("status") or "active") != "active":
        return None
    gh = _github(entry)
    rem, pkg = _remote(entry), _package(entry)
    if rem is None and pkg is None:
        return None
    return {"id": slug_of(name), "registry_name": name, "name": _title(name, gh),
            "description": (entry.get("description") or "")[:300], "section": _section(entry, gh),
            "publisher": name.split("/")[0], "repo": (entry.get("repository") or {}).get("url", ""),
            "stars": int(gh.get("stargazer_count") or 0), "license": gh.get("license") or "",
            "org": bool(gh.get("is_in_organization")), "avatar": gh.get("owner_avatar_url") or "",
            "homepage": gh.get("homepage_url") or "", "version": entry.get("version") or "",
            "remote": rem, "package": pkg, "auth": "", "probe": ""}


# ------------------------------------------------------------------ sondagem (o jeito mais simples de conectar)
def PUBLIC(url: str) -> bool:  # noqa: N802  (os testes trocam)
    from .model_profiles import _public_host
    return url.startswith("https://") and _public_host(url)


def OAUTH(url: str) -> dict:  # noqa: N802  (os testes trocam)
    from . import remote_mcp
    return remote_mcp.discover(url)


async def _probe(c: httpx.AsyncClient, url: str, transport: str = "http") -> str:
    """oauth | open | token | down. Só endereços https públicos; sem seguir redirecionamento. Quem se declara SSE e
    recusa o POST é tentado pelo GET do SSE antigo."""
    if not await asyncio.to_thread(PUBLIC, url):
        return "down"
    try:
        r = await c.post(url, json=INIT, headers={"Accept": "application/json, text/event-stream"})
        if r.status_code in (404, 405) and transport == "sse":
            async with c.stream("GET", url, headers={"Accept": "text/event-stream"}) as s:
                r = s
    except Exception:  # noqa: BLE001
        return "down"
    if r.status_code == 401:
        try:
            meta = await asyncio.to_thread(OAUTH, url)
        except Exception:  # noqa: BLE001 — 401 sem OAuth descobrível: pede token
            return "token"
        return "oauth" if meta.get("registration_endpoint") else "oauth_manual"
    if r.status_code == 403:
        return "token"
    return "open" if r.status_code < 300 else "down"


async def classify(items: list[dict]) -> list[dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, follow_redirects=False) as c:
        async def one(it):
            rem = it["remote"]
            if rem and not rem["url_vars"] and not rem["headers"]:
                async with sem:
                    it["probe"] = await _probe(c, rem["url"], rem["transport"])
            it["auth"] = _auth(it)
        await asyncio.gather(*(one(it) for it in items))
    return [it for it in items if it["auth"]]


def _auth(it: dict) -> str:
    """O jeito de conectar que entra no catálogo ("" = fica de fora)."""
    rem = it["remote"]
    if rem:
        if rem["url_vars"]:
            return "url"
        if rem["headers"]:
            return "headers"
        p = it["probe"]
        if p in ("oauth", "open", "token"):
            return {"oauth": "oauth", "open": "none", "token": "bearer"}[p]
    return "stdio" if it["package"] else ""


# ------------------------------------------------------------------ leitura do registro e o retrato
def FETCH(url: str, params: dict) -> dict:  # noqa: N802  (os testes trocam)
    r = httpx.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_all(url: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(200):  # 100 por página: até 20 mil servidores
        d = FETCH(url, {"limit": 100, **({"cursor": cursor} if cursor else {})})
        out += [s.get("server", s) if isinstance(s.get("server"), dict) else s for s in d.get("servers") or []]
        meta = d.get("metadata") or {}
        cursor = meta.get("next_cursor") or meta.get("nextCursor")
        if not cursor:
            break
    return out


def sync() -> dict:
    """Lê o registro inteiro, sonda e grava o retrato (compartilhado entre réplicas)."""
    if not config.MCP_REGISTRY_URL:
        return {"items": [], "at": time.time(), "disabled": True}
    t0 = time.time()
    items, seen = [], set()
    for entry in fetch_all(config.MCP_REGISTRY_URL):
        it = parse(entry)
        if it is None:
            continue
        base, n = it["id"], 2
        while it["id"] in seen:  # dois nomes que viram o mesmo slug
            it["id"], n = f"{base[:58]}-{n}", n + 1
        seen.add(it["id"])
        items.append(it)
    items = asyncio.run(classify(items))
    snap = {"at": time.time(), "items": items, "source": config.MCP_REGISTRY_URL}
    shared.put(KEY, snap, KEEP_S)
    _MEM.update(at=time.time(), snap=snap)
    log.info("registro MCP do GitHub: %d servidores no catálogo (%.0fs)", len(items), time.time() - t0)
    return snap


def _run_sync():
    if not _RUNNING.acquire(blocking=False):
        return
    try:
        if shared.take_once("mcp_registry:sync", 1800):  # uma réplica só renova por vez
            sync()
    except Exception as e:  # noqa: BLE001 — sem internet: o catálogo segue com o retrato anterior (ou sem o registro)
        log.warning("registro MCP do GitHub: renovação falhou (%s)", e)
    finally:
        _RUNNING.release()


def snapshot() -> dict | None:
    if _MEM["snap"] is None or time.time() - _MEM["at"] > 60:
        try:
            snap = shared.get(KEY)
        except Exception:  # noqa: BLE001
            snap = None
        if snap is not None:
            _MEM.update(at=time.time(), snap=snap)
    return _MEM["snap"]


def items() -> list[dict]:
    """Os itens do registro para o catálogo. Retrato velho ou ausente: renova em segundo plano e devolve o que tem."""
    if not config.MCP_REGISTRY_URL:
        return []
    snap = snapshot()
    if snap is None or time.time() - snap.get("at", 0) > config.MCP_REGISTRY_TTL_S:
        threading.Thread(target=_run_sync, name="mcp-registry-sync", daemon=True).start()
    return (snap or {}).get("items") or []


def find(item_id: str) -> dict | None:
    return next((it for it in items() if it["id"] == item_id), None)


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url or "").hostname or "").lower()
