"""Análise de dados e entregáveis formatados, sobre os arquivos da própria tarefa (Data Studio).

Cada tarefa tem a sua sessão no Data Studio (workspace e kernel próprios, isolados por uid ou por container): antes de
cada chamada, os arquivos da tarefa entram no workspace; depois, o que a análise gerou (planilha, gráfico, PPTX, DOCX,
dashboard) volta como entregável versionado da tarefa, com o código na linha do tempo — quem revisar reproduz.
A identidade visual da empresa (tema, cor de destaque, rodapé) entra sozinha nas apresentações e dashboards.

Ligado quando o Data Studio responde (DATA_STUDIO_URL; `docker compose --profile data-studio up -d`).
"""
import base64
import json
import mimetypes
import time

import httpx
from sqlalchemy import select

from .. import config, tenancy
from ..models import Organization, TaskFile
from . import task_files
from .common import PlatformError
from .platform_tools import Ctx, tool

HTTP = lambda timeout=330: httpx.Client(timeout=timeout)  # noqa: E731  (os testes trocam)
_UP: tuple[float, bool] = (0.0, False)
OUT_MAX = 6000


def ready() -> bool:
    """O Data Studio está no ar? (consulta guardada por um minuto)"""
    global _UP
    if not config.DATA_STUDIO_URL:
        return False
    ts, ok = _UP
    if time.monotonic() - ts < 60:
        return ok
    try:
        with HTTP(5) as h:
            ok = h.get(config.DATA_STUDIO_FILES_URL.rstrip("/") + "/health").status_code == 200
    except httpx.HTTPError:
        ok = False
    _UP = (time.monotonic(), ok)
    return ok


def _sid(task) -> str:
    return f"hangar-{tenancy.current_org()}-task-{task.id}"


def call(task, name: str, args: dict, timeout: int = 330):
    """tools/call no MCP do Data Studio (stateless, resposta JSON), na sessão da tarefa."""
    return call_session(_sid(task), name, args, timeout)


def call_session(sid: str, name: str, args: dict, timeout: int = 330):
    """tools/call numa sessão qualquer do Data Studio. Devolve o conteúdo estruturado ou o texto."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    try:
        with HTTP(timeout) as h:
            r = h.post(config.DATA_STUDIO_URL, json=body, headers={
                "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                "x-session-id": sid})
    except httpx.HTTPError as e:
        raise PlatformError(f"Data Studio indisponível: {e}") from None
    if r.status_code >= 400:
        raise PlatformError(f"Data Studio respondeu {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("error"):
        raise PlatformError(f"Data Studio: {data['error'].get('message', data['error'])}")
    res = data.get("result") or {}
    text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    if res.get("isError"):
        raise PlatformError(text[:500] or "erro no Data Studio")
    if res.get("structuredContent") is not None:
        sc = res["structuredContent"]
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    if text.lstrip().startswith(("{", "[")):  # ferramenta que devolve dict: o FastMCP manda o JSON como texto
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text


def _latest(db, task) -> list[TaskFile]:
    rows = db.scalars(select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.name, TaskFile.version.desc()))
    seen, out = set(), []
    for f in rows:
        if f.name not in seen:
            seen.add(f.name)
            out.append(f)
    return out


def sync(db, task):
    """Os arquivos da tarefa (a versão mais nova de cada um) no workspace; conteúdo repetido o Data Studio reaproveita."""
    for f in _latest(db, task):
        call(task, "ingest_file", {"filename": f.name, "data_base64": base64.b64encode(f.data).decode(),
                                   "mime_type": f.content_type})


def _fetch(url: str) -> bytes:
    """Os links do Data Studio saem pela porta pública; a central baixa pela rede interna."""
    path = url.split("/f/", 1)[1] if "/f/" in url else url
    with HTTP(120) as h:
        r = h.get(config.DATA_STUDIO_FILES_URL.rstrip("/") + "/f/" + path)
    if r.status_code >= 400:
        raise PlatformError(f"não consegui baixar {url.rsplit('/', 1)[-1]} do Data Studio ({r.status_code})")
    return r.content


def bring_back(ctx: Ctx, files: list, note: str) -> list[str]:
    """Os arquivos gerados viram entregáveis da tarefa (o mesmo nome vira versão nova)."""
    saved = []
    have = {f.sha256 for f in _latest(ctx.db, ctx.task)}
    for item in files or []:
        name = item.get("file") or item.get("path") or item.get("name") if isinstance(item, dict) else str(item)
        url = item.get("url") if isinstance(item, dict) else None
        if not name:
            continue
        if not url:
            url = call(ctx.task, "get_file_link", {"path": name})
        data = _fetch(str(url))
        import hashlib
        if hashlib.sha256(data).hexdigest() in have:
            continue  # não mudou (ou é um anexo que só foi lido)
        f = task_files.store(ctx.db, ctx.task, name.rsplit("/", 1)[-1], data, "output", f"agent:{ctx.agent.slug}",
                             mimetypes.guess_type(name)[0] or "", note)
        saved.append(f"{f.name} v{f.version}")
    ctx.db.commit()
    return saved


def brand(db) -> dict:
    o = db.get(Organization, tenancy.current_org())
    b = (o.brand if o is not None else None) or {}
    return {"theme": b.get("theme") or "light", "accent": b.get("accent") or None, "footer": b.get("footer") or None}


def _avail(db, e) -> bool:
    return ready()


def _cut(text: str) -> str:
    return text if len(text) <= OUT_MAX else text[:OUT_MAX] + f"\n… ({len(text) - OUT_MAX} caracteres cortados)"


# ------------------------------------------------------------------ ferramentas
@tool("analyze_data", "Roda Python (pandas, numpy, duckdb, matplotlib, openpyxl, python-pptx, python-docx…) sobre os "
      "arquivos da tarefa, num sandbox isolado e com estado entre as chamadas da mesma tarefa. Os arquivos estão no "
      "diretório atual pelo nome. O que você salvar (df.to_excel('conciliacao.xlsx'), plt.savefig, plt.show()) vira "
      "entregável da tarefa. Use print() para ver resultados; números sempre calculados, nunca estimados.",
      {"properties": {"code": {"type": "string"}, "note": {"type": "string", "description": "o que esta análise faz"}},
       "required": ["code"]}, "analysis", _avail)
def analyze_data(ctx: Ctx, args: dict):
    code = str(args.get("code") or "")
    if not code.strip():
        raise PlatformError("mande o código (code)")
    sync(ctx.db, ctx.task)
    r = call(ctx.task, "run_python", {"code": code, "timeout_s": 300})
    r = r if isinstance(r, dict) else {"stdout": str(r)}
    from . import employee_tasks as tasksmod
    tasksmod.event(ctx.db, ctx.task, "analysis", {"code": code[:4000], "note": str(args.get("note") or "")[:300],
                                                   "error": (r.get("error") or "")[:500]})
    saved = bring_back(ctx, r.get("files") or [], str(args.get("note") or "gerado pela análise"))
    out = []
    if r.get("stdout"):
        out.append(_cut(str(r["stdout"])))
    if r.get("result"):
        out.append(_cut(str(r["result"])))
    if r.get("error"):
        out.append("ERRO:\n" + _cut(str(r["error"])))
    if saved:
        out.append("Entregáveis salvos na tarefa: " + ", ".join(saved))
    return "\n".join(out) or "(sem saída — use print())"


@tool("query_data", "SQL analítico (DuckDB) sobre os arquivos da tarefa: cada CSV/Parquet/JSON vira uma tabela com o "
      "nome do arquivo, cada aba de Excel vira <arquivo>_<aba> (use list_data_tables antes). save_as='resultado.xlsx' "
      "grava o resultado completo como entregável.",
      {"properties": {"sql": {"type": "string"}, "save_as": {"type": "string"}, "max_rows": {"type": "integer"}},
       "required": ["sql"]}, "analysis", _avail)
def query_data(ctx: Ctx, args: dict):
    sync(ctx.db, ctx.task)
    save_as = (args.get("save_as") or None)
    text = call(ctx.task, "run_sql", {"query": str(args.get("sql") or ""), "max_rows": int(args.get("max_rows") or 50),
                                      "save_as": save_as})
    from . import employee_tasks as tasksmod
    tasksmod.event(ctx.db, ctx.task, "analysis", {"sql": str(args.get("sql"))[:4000]})
    saved = bring_back(ctx, [{"file": save_as}], "resultado de consulta") if save_as else []
    return _cut(str(text)) + (f"\nEntregável salvo: {', '.join(saved)}" if saved else "")


@tool("list_data_tables", "As tabelas que a consulta SQL enxerga nos arquivos da tarefa: colunas, tipos e linhas.",
      {"properties": {}}, "analysis", _avail)
def list_data_tables(ctx: Ctx, args: dict):
    sync(ctx.db, ctx.task)
    return _cut(str(call(ctx.task, "list_tables", {})))


@tool("inspect_data_file", "Examina um arquivo da tarefa: planilhas (todas as abas, tipos, nulos, amostra), PDF (texto "
      "e tabelas por página), DOCX, PPTX, imagens. ocr=true lê PDFs escaneados e imagens (português e inglês).",
      {"properties": {"name": {"type": "string"}, "ocr": {"type": "boolean"}, "pages": {"type": "integer"}},
       "required": ["name"]}, "analysis", _avail)
def inspect_data_file(ctx: Ctx, args: dict):
    sync(ctx.db, ctx.task)
    return _cut(str(call(ctx.task, "inspect_file", {"path": str(args.get("name") or ""), "ocr": bool(args.get("ocr")),
                                                     "pages": int(args.get("pages") or 5)})))


@tool("create_deliverable", "Gera um entregável formatado com a identidade visual da empresa e o salva na tarefa. "
      "kind=presentation: slides (PPTX editável; tipos title, section, bullets, two_columns, chart, table, kpis, image, "
      "text — chart/table/kpis aceitam sql sobre os arquivos). kind=document: markdown vira DOCX ([[table: SELECT …]] e "
      "[[image: grafico.png]] embutem resultados). kind=dashboard: HTML interativo (kpis, charts, tables, insights).",
      {"properties": {"kind": {"type": "string", "enum": ["presentation", "document", "dashboard"]},
                      "title": {"type": "string"}, "subtitle": {"type": "string"},
                      "slides": {"type": "array", "items": {"type": "object"}},
                      "markdown": {"type": "string"}, "format": {"type": "string", "description": "document: docx | html | md"},
                      "kpis": {"type": "array", "items": {"type": "object"}},
                      "charts": {"type": "array", "items": {"type": "object"}},
                      "tables": {"type": "array", "items": {"type": "object"}},
                      "insights": {"type": "array", "items": {"type": "string"}},
                      "filename": {"type": "string"}},
       "required": ["kind", "title"]}, "analysis", _avail)
def create_deliverable(ctx: Ctx, args: dict):
    kind, b = args.get("kind"), brand(ctx.db)
    sync(ctx.db, ctx.task)
    common = {"title": str(args.get("title") or ""), "filename": args.get("filename") or None}
    if kind == "presentation":
        r = call(ctx.task, "create_presentation", {**common, "slides": args.get("slides") or [], "subtitle": args.get("subtitle"),
                                                   "theme": b["theme"], "accent": b["accent"], "footer": b["footer"],
                                                   "formats": ["pptx"]})
        files = (r or {}).get("files") if isinstance(r, dict) else []
    elif kind == "document":
        r = call(ctx.task, "create_document", {**common, "markdown": str(args.get("markdown") or ""),
                                               "format": args.get("format") or "docx"})
        files = [r] if isinstance(r, dict) else []
    elif kind == "dashboard":
        r = call(ctx.task, "create_dashboard", {**common, "subtitle": args.get("subtitle"), "kpis": args.get("kpis"),
                                                "charts": args.get("charts"), "tables": args.get("tables"),
                                                "insights": args.get("insights"), "theme": b["theme"], "accent": b["accent"]})
        files = [r] if isinstance(r, dict) else []
    else:
        raise PlatformError("kind: presentation | document | dashboard")
    saved = bring_back(ctx, files, f"{kind}: {common['title'][:120]}")
    from . import employee_tasks as tasksmod
    tasksmod.event(ctx.db, ctx.task, "analysis", {"deliverable": kind, "title": common["title"][:200]})
    ctx.db.commit()
    return ("Entregável salvo na tarefa: " + ", ".join(saved)) if saved else json.dumps(r, ensure_ascii=False)[:1000]


# ------------------------------------------------------------------ identidade visual (admin)
def set_brand(db, acc, changes: dict) -> dict:
    import re

    from .access import Forbidden
    from .common import audit
    if not acc.p.is_admin:
        raise Forbidden("só o admin define a identidade visual da empresa")
    o = db.get(Organization, tenancy.current_org())
    b = dict(o.brand or {})
    if changes.get("theme") is not None:
        if changes["theme"] not in ("light", "dark", "corporate"):
            raise PlatformError("theme: light | dark | corporate")
        b["theme"] = changes["theme"]
    if changes.get("accent") is not None:
        a = str(changes["accent"]).strip()
        if a and not re.fullmatch(r"#[0-9a-fA-F]{6}", a):
            raise PlatformError("accent: cor hex, ex.: #0B5FFF")
        b["accent"] = a
    if changes.get("footer") is not None:
        b["footer"] = str(changes["footer"]).strip()[:120]
    o.brand = b
    db.commit()
    audit(db, acc.p.name, "org.brand", "org", ", ".join(sorted(k for k, v in changes.items() if v is not None)))
    return {**brand(db), "data_studio": ready()}
