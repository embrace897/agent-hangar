"""Base de conhecimento da empresa: políticas, procedimentos, contratos-modelo, histórico — com as permissões de quem
pergunta e a fonte citada.

- **Fontes:** arquivos enviados, páginas (URL), Confluence (espaço), SharePoint (pasta de uma biblioteca, pela
  credencial de aplicativo do Microsoft 365) e Google Drive (pasta, pela conta de serviço). A regra de acesso é por
  fonte: herdar as permissões do documento original (`inherit`: restrições da página no Confluence, compartilhamentos
  no SharePoint e no Drive), ou valer para os times da fonte, ou para a empresa toda.
- **Busca com as permissões de quem pediu:** o Digital employee busca (`search_knowledge`) com o acesso da pessoa que
  pediu a tarefa — nunca traz o que ela não poderia ler. Sem uma pessoa (rotina, webhook), só o que é da empresa toda
  e do time do funcionário.
- **Citação obrigatória:** cada trecho volta numerado com título, link e data; o funcionário cita [1], [2].
- **Atualidade:** documento sem revisão há mais que o prazo da fonte aparece como vencido (e o dono da fonte vê a
  lista). **Lacunas:** pergunta sem resposta boa vira uma lacuna para quem cuida do conteúdo.
"""
import hashlib
import html
import re
import unicodedata
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import delete, or_, select

from .. import crypto
from ..models import (
    EmployeeTask,
    KnowledgeChunk,
    KnowledgeDoc,
    KnowledgeGap,
    KnowledgeSource,
    ScimGroup,
    Team,
    TeamMember,
    User,
    now,
)
from . import docx_lite, notify
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso, slugify
from .platform_tools import Ctx, tool

HTTP = lambda: httpx.Client(timeout=30, follow_redirects=False)  # noqa: E731  (os testes trocam)
KINDS = ("upload", "url", "confluence", "sharepoint", "gdrive")
STOP = set("a o e de da do das dos em no na nos nas um uma para por com que se ao aos como mais ou the of and to in is for".split())
CHUNK = 1200


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _terms(q: str) -> list[str]:
    return [t for t in dict.fromkeys(norm(q).split()) if len(t) > 2 and t not in STOP][:12]


# ------------------------------------------------------------------ fontes
def source_dict(db, s: KnowledgeSource, acc: Access | None = None) -> dict:
    n = db.query(KnowledgeDoc).filter(KnowledgeDoc.source_id == s.id).count()
    today = date.today().isoformat()
    stale = db.query(KnowledgeDoc).filter(KnowledgeDoc.source_id == s.id, KnowledgeDoc.review_by < today).count()
    cfg = dict(s.config or {})
    for k in ("token", "api_token"):
        if cfg.get(k):
            cfg[k] = True
    return {"name": s.name, "title": s.title or s.name, "kind": s.kind, "acl_mode": s.acl_mode, "teams": s.team_ids or [],
            "config": cfg, "review_days": s.review_days, "classification": s.classification, "docs": n, "stale": stale,
            "last_sync_at": iso(s.last_sync_at) if s.last_sync_at else None, "last_error": s.last_error,
            "can_manage": bool(acc and _can_manage(acc, s))}


def _can_manage(acc: Access, s: KnowledgeSource) -> bool:
    return acc.p.is_admin or any(RANK.get(acc.teams.get(t, ""), 0) >= RANK["maintainer"] for t in (s.team_ids or []))


def _get(db, name: str) -> KnowledgeSource:
    s = db.scalar(select(KnowledgeSource).where(KnowledgeSource.name == slugify(name or "").replace("-", "_")))
    if s is None:
        raise PlatformError(f"fonte '{name}' não encontrada")
    return s


def list_sources(db, acc: Access) -> list[dict]:
    return [source_dict(db, s, acc) for s in db.scalars(select(KnowledgeSource).order_by(KnowledgeSource.name))
            if acc.p.is_admin or acc.p.is_auditor or set(s.team_ids or []) & set(acc.teams) or s.acl_mode == "org"]


def save_source(db, acc: Access, name: str, kind: str, title: str = "", acl_mode: str = "team", teams=None, config=None,
                review_days: int = 365, classification: str = "internal") -> dict:
    if kind not in KINDS:
        raise PlatformError(f"tipo: {', '.join(KINDS)}")
    if acl_mode not in ("inherit", "team", "org"):
        raise PlatformError("acl_mode: inherit | team | org")
    if acl_mode == "inherit" and kind in ("upload", "url"):
        raise PlatformError("herdar permissões só existe para Confluence, SharePoint e Google Drive")
    from .governance import LEVELS
    if classification not in LEVELS:
        raise PlatformError(f"classificação: {', '.join(LEVELS)}")
    team_ids = []
    for t in teams or []:
        row = db.scalar(select(Team).where(Team.slug == t))
        if row is None:
            raise PlatformError(f"time '{t}' não encontrado")
        team_ids.append(row.id)
    if acl_mode == "team" and not team_ids:
        raise PlatformError("diga de que time(s) é a fonte")
    key = slugify(name or title).replace("-", "_")[:80]
    s = db.scalar(select(KnowledgeSource).where(KnowledgeSource.name == key))
    probe = s or KnowledgeSource(team_ids=team_ids)
    if not (acc.p.is_admin or (team_ids and all(RANK.get(acc.teams.get(t, ""), 0) >= RANK["maintainer"] for t in team_ids))
            and (s is None or _can_manage(acc, probe))):
        raise Forbidden("cria fontes quem mantém os times dela (ou o admin); da empresa toda, só o admin")
    if acl_mode == "org" and not acc.p.is_admin:
        raise Forbidden("fonte para a empresa toda é com o admin")
    cfg = dict((s.config if s else None) or {})
    for k, v in (config or {}).items():
        if k in ("token", "api_token"):
            if v:
                cfg[k] = crypto.encrypt(str(v))
        else:
            cfg[k] = v
    if s is None:
        s = KnowledgeSource(name=key, created_by=acc.p.name, created_at=now(), owner_user_id=acc.p.user_id)
        db.add(s)
    s.title, s.kind, s.acl_mode, s.team_ids, s.config = (title or key)[:200], kind, acl_mode, team_ids, cfg
    s.review_days, s.classification = max(30, int(review_days or 365)), classification
    db.commit()
    audit(db, acc.p.name, "knowledge.source", key, f"{kind} {acl_mode}")
    return source_dict(db, s, acc)


# ------------------------------------------------------------------ documentos
def _chunks(text: str) -> list[str]:
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text or ""):
        para = para.strip()
        if not para:
            continue
        if cur and len(cur) + len(para) > CHUNK:
            out.append(cur)
            cur = ""
        while len(para) > CHUNK * 2:
            out.append(para[:CHUNK])
            para = para[CHUNK:]
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def _default_acl(s: KnowledgeSource) -> list[str]:
    return ["org"] if s.acl_mode == "org" else [f"team:{t}" for t in s.team_ids or []]


def upsert(db, s: KnowledgeSource, external_id: str, title: str, text: str, url: str = "", acl: list[str] | None = None,
           updated: datetime | None = None, meta: dict | None = None) -> KnowledgeDoc:
    h = hashlib.sha256((text or "").encode()).hexdigest()
    d = db.scalar(select(KnowledgeDoc).where(KnowledgeDoc.source_id == s.id, KnowledgeDoc.external_id == external_id))
    acl = acl if (s.acl_mode == "inherit" and acl) else _default_acl(s)
    upd = updated or now()
    review = (upd.date() + timedelta(days=s.review_days)).isoformat()
    if d is None:
        d = KnowledgeDoc(source_id=s.id, external_id=external_id[:300], title=title[:300])
        db.add(d)
    elif d.content_hash == h:
        d.acl, d.title, d.url, d.review_by, d.meta = acl, title[:300], url[:1000], review, meta or d.meta
        return d
    d.title, d.url, d.acl, d.updated_at, d.review_by, d.content_hash, d.text, d.meta = (
        title[:300], url[:1000], acl, upd, review, h, text[:2_000_000], meta)
    db.flush()
    db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == d.id).delete()
    for i, c in enumerate(_chunks(text)):
        db.add(KnowledgeChunk(doc_id=d.id, idx=i, text=c, norm=norm(f"{title} {c}")))
    return d


def _file_text(name: str, data: bytes, ctype: str = "") -> str:
    low = name.lower()
    if low.endswith(".docx"):
        return "\n\n".join(p for p in docx_lite.paragraphs(data) if p.strip())
    if low.endswith((".html", ".htm")) or "html" in ctype:
        return _html_text(data.decode("utf-8", "replace"))
    if low.endswith((".txt", ".md", ".csv", ".json")) or ctype.startswith("text/"):
        return data.decode("utf-8", "replace")
    from . import analysis
    if not analysis.ready():
        raise PlatformError(f"{name}: para PDF e imagens, ligue o Data Studio (OCR)")
    import base64
    sid = f"knowledge-{hashlib.sha256(name.encode()).hexdigest()[:12]}"
    analysis.call_session(sid, "ingest_file", {"filename": name, "data_base64": base64.b64encode(data).decode()})
    return str(analysis.call_session(sid, "inspect_file", {"path": name, "ocr": True, "pages": 50}))


def _html_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>|</tr>", "\n\n", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h)).replace("\xa0", " ")


def add_document(db, acc: Access, name: str, title: str, text: str = "", file_name: str = "", data: bytes = b"",
                 content_type: str = "", url: str = "") -> dict:
    s = _get(db, name)
    if not _can_manage(acc, s):
        raise Forbidden("só quem mantém a fonte envia documentos")
    if s.kind != "upload":
        raise PlatformError("esta fonte é sincronizada; envie documentos numa fonte do tipo upload")
    body = text or (_file_text(file_name or title, data, content_type) if data else "")
    if not body.strip():
        raise PlatformError("o documento está vazio")
    d = upsert(db, s, f"upload:{slugify(title or file_name)}", title or file_name, body, url)
    db.commit()
    audit(db, acc.p.name, "knowledge.document", s.name, d.title[:120])
    return {"id": d.id, "title": d.title, "chunks": db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == d.id).count()}


# ------------------------------------------------------------------ sincronização
def sync(db, s: KnowledgeSource) -> dict:
    seen, n = set(), 0
    try:
        for ext_id, title, text, url, acl, updated, meta in _pull(db, s):
            upsert(db, s, ext_id, title, text, url, acl, updated, meta)
            seen.add(ext_id)
            n += 1
        if s.kind != "upload":  # o que sumiu da fonte sai da base
            for d in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.source_id == s.id)):
                if d.external_id not in seen:
                    db.delete(d)
        s.last_sync_at, s.last_error = now(), ""
    except (PlatformError, httpx.HTTPError, ValueError, KeyError) as e:
        s.last_error = str(e)[:300]
    db.commit()
    return {"source": s.name, "docs": n, "error": s.last_error}


def sync_now(db, acc: Access, name: str) -> dict:
    s = _get(db, name)
    if not _can_manage(acc, s):
        raise Forbidden("só quem mantém a fonte sincroniza")
    out = sync(db, s)
    audit(db, acc.p.name, "knowledge.sync", s.name, f"{out['docs']} doc(s)")
    return out


def delete_source(db, acc: Access, name: str) -> dict:
    """Remove a fonte e o que foi copiado dela (documentos e trechos); o original, se for externo, não é tocado."""
    s = _get(db, name)
    if not _can_manage(acc, s):
        raise Forbidden("só quem mantém a fonte a remove")
    docs = db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.source_id == s.id)).all()
    ids = [d.id for d in docs]
    if ids:
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(ids)))
        db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id.in_(ids)))
    db.delete(s)
    db.commit()
    audit(db, acc.p.name, "knowledge.delete", name, f"{len(ids)} doc(s)")
    return {"deleted": name, "docs": len(ids)}


def _check_url(url: str) -> str:
    from .schedules import check_webhook
    try:
        return check_webhook(url)
    except PlatformError as e:
        raise PlatformError(str(e).replace("notify_url", "a URL")) from None


def _pull(db, s: KnowledgeSource):
    cfg = s.config or {}
    if s.kind == "upload":
        return
    if s.kind == "url":
        for u in cfg.get("urls") or []:
            with HTTP() as h:
                r = h.get(_check_url(u))
            r.raise_for_status()
            t = re.search(r"(?is)<title>(.*?)</title>", r.text)
            yield u, html.unescape(t.group(1).strip()) if t else u, _html_text(r.text), u, None, None, None
        return
    if s.kind == "confluence":
        yield from _confluence(cfg)
        return
    if s.kind == "sharepoint":
        yield from _sharepoint(db, cfg)
        return
    if s.kind == "gdrive":
        yield from _gdrive(db, cfg)


def _confluence(cfg: dict):
    base, space = cfg.get("base_url", "").rstrip("/"), cfg.get("space", "")
    if not base or not space:
        raise PlatformError("Confluence: base_url e space")
    auth = (cfg.get("email", ""), crypto.decrypt(cfg["api_token"]) if cfg.get("api_token") else "")
    start = 0
    while True:
        with HTTP() as h:
            r = h.get(f"{base}/rest/api/content", auth=auth, params={
                "spaceKey": space, "type": "page", "limit": 50, "start": start,
                "expand": "body.storage,version,restrictions.read.restrictions.group,restrictions.read.restrictions.user"})
        r.raise_for_status()
        d = r.json()
        for p in d.get("results") or []:
            rr = (((p.get("restrictions") or {}).get("read") or {}).get("restrictions") or {})
            acl = [f"group:{g.get('name')}" for g in (rr.get("group") or {}).get("results") or []] + \
                  [f"user:{u.get('email').lower()}" for u in (rr.get("user") or {}).get("results") or [] if u.get("email")]
            ver = p.get("version") or {}
            upd = datetime.fromisoformat(ver["when"].replace("Z", "+00:00")) if ver.get("when") else None
            yield (f"confluence:{p['id']}", p.get("title", ""), _html_text(((p.get("body") or {}).get("storage") or {}).get("value", "")),
                   f"{base}{(p.get('_links') or {}).get('webui', '')}", acl or None, upd,
                   {"confluence_id": p["id"], "version": ver.get("number", 1)})
        if len(d.get("results") or []) < 50:
            break
        start += 50


def _graph(db) -> tuple[dict, str]:
    from . import calendar_api
    cfg = notify.org_cfg(db)
    if not (cfg.get("ms_tenant") and cfg.get("ms_client_id") and cfg.get("ms_client_secret")):
        raise PlatformError("SharePoint usa a credencial de aplicativo do Microsoft 365 (Decisões → canais → Agenda)")
    return cfg, calendar_api._ms_token(cfg)


def _sharepoint(db, cfg: dict):
    _, tok = _graph(db)
    drive, folder = cfg.get("drive_id", ""), (cfg.get("folder") or "").strip("/")
    if not drive:
        raise PlatformError("SharePoint: drive_id (a biblioteca) e, opcional, folder")
    g = "https://graph.microsoft.com/v1.0"
    path = f"{g}/drives/{drive}/root:/{folder}:/children" if folder else f"{g}/drives/{drive}/root/children"
    with HTTP() as h:
        r = h.get(path, headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        for it in r.json().get("value") or []:
            name = it.get("name", "")
            if "file" not in it or not name.lower().endswith((".docx", ".txt", ".md", ".pdf", ".html")):
                continue
            c = h.get(f"{g}/drives/{drive}/items/{it['id']}/content", headers={"Authorization": f"Bearer {tok}"})
            if c.status_code in (301, 302):
                c = httpx.get(c.headers["location"], timeout=60)
            c.raise_for_status()
            perms = h.get(f"{g}/drives/{drive}/items/{it['id']}/permissions", headers={"Authorization": f"Bearer {tok}"})
            acl = []
            for pm in (perms.json().get("value") or []) if perms.status_code == 200 else []:
                link = pm.get("link") or {}
                if link.get("scope") in ("anonymous", "organization"):
                    acl.append("org")
                for ident in [pm.get("grantedToV2") or {}, *(pm.get("grantedToIdentitiesV2") or [])]:
                    u, grp = ident.get("user") or {}, ident.get("group") or ident.get("siteGroup") or {}
                    if u.get("email"):
                        acl.append(f"user:{u['email'].lower()}")
                    if grp.get("displayName"):
                        acl.append(f"group:{grp['displayName']}")
            upd = datetime.fromisoformat(it["lastModifiedDateTime"].replace("Z", "+00:00")) if it.get("lastModifiedDateTime") else None
            yield (f"sharepoint:{it['id']}", name, _file_text(name, c.content), it.get("webUrl", ""), sorted(set(acl)) or None, upd,
                   {"drive_id": drive, "item_id": it["id"], "name": name})


def _gdrive(db, cfg: dict):
    from . import calendar_api
    ocfg = notify.org_cfg(db)
    if not ocfg.get("google_sa"):
        raise PlatformError("Google Drive usa a conta de serviço do Google Workspace (Decisões → canais → Agenda)")
    folder, subject = cfg.get("folder_id", ""), cfg.get("subject", "")
    if not folder or not subject:
        raise PlatformError("Google Drive: folder_id e subject (o e-mail de quem a conta de serviço representa)")
    tok = calendar_api._google_token(ocfg, subject)
    api = "https://www.googleapis.com/drive/v3"
    with HTTP() as h:
        r = h.get(f"{api}/files", headers={"Authorization": f"Bearer {tok}"}, params={
            "q": f"'{folder}' in parents and trashed = false", "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"})
        r.raise_for_status()
        for f in r.json().get("files") or []:
            if f["mimeType"] == "application/vnd.google-apps.document":
                c = h.get(f"{api}/files/{f['id']}/export", params={"mimeType": "text/plain"}, headers={"Authorization": f"Bearer {tok}"})
                text = c.text
            elif f["name"].lower().endswith((".txt", ".md", ".docx", ".pdf")):
                c = h.get(f"{api}/files/{f['id']}", params={"alt": "media"}, headers={"Authorization": f"Bearer {tok}"})
                text = _file_text(f["name"], c.content)
            else:
                continue
            perms = h.get(f"{api}/files/{f['id']}/permissions", headers={"Authorization": f"Bearer {tok}"},
                          params={"fields": "permissions(type,emailAddress,domain)"})
            acl = []
            for pm in (perms.json().get("permissions") or []) if perms.status_code == 200 else []:
                if pm.get("type") in ("domain", "anyone"):
                    acl.append("org")
                elif pm.get("emailAddress"):
                    acl.append(f"{'group' if pm.get('type') == 'group' else 'user'}:{pm['emailAddress'].lower()}")
            upd = datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")) if f.get("modifiedTime") else None
            yield (f"gdrive:{f['id']}", f["name"], text, f.get("webViewLink", ""), sorted(set(acl)) or None, upd,
                   {"file_id": f["id"], "name": f["name"], "mime": f["mimeType"]})


def sweep(db, at=None) -> int:
    """Sincroniza uma vez por dia as fontes que não são de upload."""
    at, n = at or now(), 0
    for s in db.scalars(select(KnowledgeSource).where(KnowledgeSource.kind != "upload", KnowledgeSource.status == "active")).all():
        from .employee_tasks import _aware
        if s.last_sync_at and _aware(s.last_sync_at) > at - timedelta(hours=24):
            continue
        sync(db, s)
        n += 1
        break  # uma fonte por ciclo: a sincronização não segura o executor
    return n


# ------------------------------------------------------------------ busca
def principals(db, user: User | None, team_ids: list[int] | None = None) -> set[str]:
    out = {"org"}
    if user is not None:
        out.add(f"user:{user.email.lower()}")
        out |= {f"team:{t}" for t in db.scalars(select(TeamMember.team_id).where(TeamMember.user_id == user.id))}
        out |= {f"group:{g.display_name}" for g in db.scalars(select(ScimGroup)) if user.id in (g.members or [])}
        out |= {f"group:{g.display_name.lower()}" for g in db.scalars(select(ScimGroup)) if user.id in (g.members or [])}
    out |= {f"team:{t}" for t in team_ids or []}
    return out


def search(db, query: str, who: set[str], k: int = 5) -> list[dict]:
    terms = _terms(query)
    if not terms:
        return []
    rows = list(db.execute(select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
                           .where(or_(*[KnowledgeChunk.norm.like(f"%{t}%") for t in terms])).limit(800)).all())
    df = {t: sum(1 for c, _ in rows if t in c.norm) for t in terms}
    today = date.today().isoformat()
    scored = []
    for c, d in rows:
        if not (set(d.acl or []) & who):
            continue
        score = sum((c.norm.count(t)) * (1 + 3 / (1 + df[t])) for t in terms) + sum(2 for t in terms if t in norm(d.title))
        scored.append((score, c, d))
    scored.sort(key=lambda x: -x[0])
    out, used = [], set()
    for score, c, d in scored:
        if len(out) >= k:
            break
        if (d.id, c.idx) in used:
            continue
        used.add((d.id, c.idx))
        seen_terms = norm(f"{d.title} {c.text}")
        cover = sum(1 for t in terms if t in seen_terms) / len(terms)
        out.append({"n": len(out) + 1, "title": d.title, "url": d.url, "doc": d.id, "score": round(score, 2),
                    "coverage": round(cover, 2),
                    "updated": d.updated_at.date().isoformat() if d.updated_at else None,
                    "stale": bool(d.review_by and d.review_by < today), "snippet": c.text[:900]})
    return out


def _gap(db, query: str, results: list[dict], task_id: int | None, who: str):
    """Sem resultado, ou nenhum trecho cobre a maior parte da pergunta: a base não responde bem."""
    best = results[0]["score"] if results else 0.0
    cover = max((r.get("coverage", 0) for r in results), default=0.0)
    if not results or cover < 0.6:
        db.add(KnowledgeGap(query=query[:500], task_id=task_id, asked_by=who[:254], best_score=best, at=now()))
        db.commit()


SEARCH_LIMIT = 6  # buscas por tarefa: depois disso, mais variações da mesma pergunta só gastam passos e tokens


@tool("search_knowledge", "Busca na base de conhecimento da empresa (políticas, procedimentos, contratos-modelo, histórico) "
      "com o acesso de quem pediu a tarefa. Responda só com o que os trechos dizem e cite as fontes como [1], [2]; se a "
      "fonte estiver vencida, avise. Sem trecho bom, diga que a base não cobre — não invente.",
      {"properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}, "knowledge")
def search_knowledge(ctx: Ctx, args: dict):
    t: EmployeeTask = ctx.task
    from ..models import TaskEvent
    done = ctx.db.query(TaskEvent).filter(TaskEvent.task_id == t.id, TaskEvent.kind == "knowledge").count()
    if done >= SEARCH_LIMIT:
        return (f"Você já buscou {done} vezes na base nesta tarefa. Não busque de novo: responda com o que encontrou "
                "(citando as fontes) ou diga que a base não cobre — e, se a resposta depender disso, pergunte a uma pessoa "
                "(ask_human).")
    user = ctx.db.get(User, t.requester_user_id) if t.requester_user_id else None
    who = principals(ctx.db, user, [ctx.agent.team_id] if (user is None and ctx.agent.team_id) else None)
    q = str(args.get("query") or "")
    res = search(ctx.db, q, who, max(1, min(int(args.get("k") or 5), 8)))
    _gap(ctx.db, q, res, t.id, t.requester or "")
    from . import employee_tasks as tasksmod
    tasksmod.event(ctx.db, t, "knowledge", {"query": q[:200], "results": [r["doc"] for r in res]})
    from . import org_learning
    org_learning.used_docs(ctx.db, [r["doc"] for r in res])  # um artigo aprendido foi reaproveitado
    ctx.db.commit()
    if not res:
        return "Nada na base de conhecimento que você (e quem pediu) pode ler cobre isso. Diga que não encontrou; não invente."
    return {"results": [{k: r[k] for k in ("n", "title", "url", "updated", "stale", "snippet")} for r in res],
            "instruction": "Cite cada afirmação com o número da fonte ([1], [2]). Fonte com stale=true: avise que pode estar desatualizada."}


def people_search(db, acc: Access, q: str) -> list[dict]:
    user = db.get(User, acc.p.user_id) if acc.p.user_id else None
    who = principals(db, user) if user else ({"org"} | {f"team:{t}" for t in acc.teams})
    if acc.p.is_admin and user is None:
        who |= {a for d in db.scalars(select(KnowledgeDoc)) for a in (d.acl or [])}
    res = search(db, q, who, 8)
    _gap(db, q, res, None, acc.p.name)
    return res


def gaps(db, acc: Access) -> list[dict]:
    if not (acc.p.is_admin or any(r == "maintainer" for r in acc.teams.values())):
        raise Forbidden("as lacunas são para quem cuida do conteúdo")
    return [{"id": g.id, "query": g.query, "task": g.task_id, "asked_by": g.asked_by, "best_score": g.best_score,
             "at": iso(g.at)} for g in db.scalars(select(KnowledgeGap).where(KnowledgeGap.resolved.is_(False))
                                                   .order_by(KnowledgeGap.id.desc()).limit(200))]


def resolve_gap(db, acc: Access, gap_id: int) -> dict:
    g = db.get(KnowledgeGap, gap_id)
    if g is None:
        raise PlatformError("lacuna não encontrada")
    if not (acc.p.is_admin or any(r == "maintainer" for r in acc.teams.values())):
        raise Forbidden("sem permissão")
    g.resolved = True
    db.commit()
    return {"id": g.id, "resolved": True}


def stale_docs(db, acc: Access) -> list[dict]:
    today = date.today().isoformat()
    out = []
    for d in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.review_by < today).order_by(KnowledgeDoc.review_by).limit(200)):
        s = db.get(KnowledgeSource, d.source_id)
        if s is not None and (acc.p.is_admin or _can_manage(acc, s)):
            out.append({"id": d.id, "title": d.title, "url": d.url, "source": s.name, "review_by": d.review_by})
    return out

