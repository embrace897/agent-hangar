"""Propor alterações em documentos — nunca editar direto: o Digital employee propõe, uma pessoa vê o diff trecho a
trecho, aceita tudo, parte ou ajusta, e só então a plataforma aplica.

Documentos: um arquivo da tarefa (`file:<nome>`) ou um documento da base de conhecimento (`kb:<id>`) vindo do
Confluence (vira uma nova versão da página) ou do SharePoint (nova versão do arquivo). DOCX volta como redline: os
trechos aceitos aparecem como exclusão + inclusão com controle de alterações, assinados por quem aprovou. Se o
documento mudou entre a proposta e a aprovação, nada é aplicado (peça uma proposta nova).
"""
import difflib
import hashlib
import re

import httpx
from sqlalchemy import select

from .. import crypto
from ..models import Employee, HumanRequest, KnowledgeDoc, KnowledgeSource, TaskFile, now
from . import docx_lite, task_files
from . import employee_tasks as tasksmod
from .common import PlatformError
from .decision_kinds import close, handler
from .platform_tools import Ctx, tool

HTTP = lambda: httpx.Client(timeout=30)  # noqa: E731  (os testes trocam)
MAX = 300_000


def _split(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))]
    return [p for p in parts if p]


def _hash(paras: list[str]) -> str:
    return hashlib.sha256("\n\n".join(paras).encode()).hexdigest()


def _current(db, task, ref: str) -> tuple[list[str], dict]:
    """(parágrafos, de onde vieram)."""
    if ref.startswith("file:"):
        name = ref[5:]
        f = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.name == name).order_by(TaskFile.version.desc()))
        if f is None:
            raise PlatformError(f"'{name}' não está na tarefa")
        if name.lower().endswith(".docx"):
            return [p for p in docx_lite.paragraphs(f.data) if p.strip()], {"kind": "file", "name": name, "format": "docx"}
        if not f.content_type.startswith(("text/", "application/json")):
            raise PlatformError("alterações só em DOCX ou texto (txt, md, html)")
        return _split(f.data.decode("utf-8", "replace")), {"kind": "file", "name": name, "format": "text"}
    if ref.startswith("kb:"):
        d = db.get(KnowledgeDoc, int(ref[3:]))
        if d is None:
            raise PlatformError("documento da base não encontrado")
        s = db.get(KnowledgeSource, d.source_id)
        if s.kind not in ("confluence", "sharepoint", "upload"):
            raise PlatformError(f"documentos de {s.kind} não recebem alterações pela plataforma")
        return _split(d.text), {"kind": "kb", "doc": d.id, "source": s.kind, "title": d.title}
    raise PlatformError("document: file:<nome do arquivo da tarefa> ou kb:<id do documento da base>")


@tool("propose_document_change", "Propõe uma alteração num documento (arquivo da tarefa: file:<nome>; documento da base "
      "de conhecimento: kb:<id>). Mande o texto novo inteiro (new_text) ou trocas exatas (edits: [{find, replace}]). "
      "Uma pessoa vê o diff, aceita tudo, parte ou ajusta; só então é aplicado. Você nunca edita direto.",
      {"properties": {"document": {"type": "string"}, "new_text": {"type": "string"},
                      "edits": {"type": "array", "items": {"type": "object", "properties": {
                          "find": {"type": "string"}, "replace": {"type": "string"}}, "required": ["find", "replace"]}},
                      "summary": {"type": "string", "description": "o que muda e por quê"}},
       "required": ["document", "summary"]}, "documents")
def propose_document_change(ctx: Ctx, args: dict):
    ref = str(args.get("document") or "").strip()
    old, origin = _current(ctx.db, ctx.task, ref)
    if args.get("new_text"):
        new = _split(str(args["new_text"]))
    elif args.get("edits"):
        text = "\n\n".join(old)
        for e in args["edits"]:
            find = str(e.get("find") or "")
            n = text.count(find)
            if not find or n != 1:
                raise PlatformError(f"o trecho {find[:60]!r} aparece {n} vez(es): precisa aparecer exatamente uma")
            text = text.replace(find, str(e.get("replace") or ""))
        new = _split(text)
    else:
        raise PlatformError("mande new_text ou edits")
    if len("\n\n".join(new)) > MAX:
        raise PlatformError("documento grande demais para uma proposta")
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == j2 - j1:  # parágrafo por parágrafo: um trecho cada, para aceitar só parte
            for k in range(i2 - i1):
                hunks.append({"id": len(hunks) + 1, "i1": i1 + k, "i2": i1 + k + 1, "before": [old[i1 + k]], "after": [new[j1 + k]]})
        elif tag != "equal":
            hunks.append({"id": len(hunks) + 1, "i1": i1, "i2": i2, "before": old[i1:i2], "after": new[j1:j2]})
    if not hunks:
        return "Nada muda: o texto proposto é igual ao atual."
    assigned, fallback = tasksmod._route(ctx.db, ctx.e, "manager")
    r = HumanRequest(employee_id=ctx.e.id, task_id=ctx.task.id, kind="doc_change", action_type="write_internal", mode="approve",
                     question=str(args.get("summary") or "")[:2000], rationale=ctx.rationale[:4000],
                     action_payload={"document": ref, "origin": origin, "summary": str(args.get("summary") or "")[:2000],
                                     "hunks": hunks, "base_hash": _hash(old), "old": old},
                     assigned_user_id=assigned, fallback_user_id=fallback, approvals=[], created_at=now())
    ctx.db.add(r)
    ctx.db.flush()
    ctx.task.status = "waiting_human"
    tasksmod.event(ctx.db, ctx.task, "human_request", {"id": r.id, "kind": "doc_change", "document": ref, "hunks": len(hunks)})
    ctx.db.commit()
    tasksmod._notify_request(ctx.db, ctx.e, r)
    return {"status": "waiting", "request_id": r.id,
            "message": f"Proposta #{r.id} com {len(hunks)} trecho(s) para uma pessoa revisar; a tarefa continua depois."}


def _apply(db, task, p: dict, final: list[str], rows: list[tuple[str, str | None]], who: str) -> str:
    origin = p["origin"]
    if origin["kind"] == "file":
        name = origin["name"]
        data = docx_lite.build(rows, f"Agent Hangar (aprovado por {who})") if origin["format"] == "docx" \
            else "\n\n".join(final).encode()
        f = task_files.store(db, task, name, data, "output", who, "", f"alteração aprovada por {who}")
        return f"{name} v{f.version}"
    d = db.get(KnowledgeDoc, origin["doc"])
    s = db.get(KnowledgeSource, d.source_id)
    meta, cfg = d.meta or {}, s.config or {}
    if s.kind == "confluence":
        base = cfg.get("base_url", "").rstrip("/")
        auth = (cfg.get("email", ""), crypto.decrypt(cfg["api_token"]) if cfg.get("api_token") else "")
        body = "".join(f"<p>{_esc(x)}</p>" for x in final)
        with HTTP() as h:
            r = h.put(f"{base}/rest/api/content/{meta['confluence_id']}", auth=auth, json={
                "id": meta["confluence_id"], "type": "page", "title": d.title, "version": {"number": int(meta.get("version", 1)) + 1,
                                                                                            "message": f"aprovado por {who}"},
                "body": {"storage": {"value": body, "representation": "storage"}}})
        if r.status_code >= 400:
            raise PlatformError(f"Confluence recusou a nova versão: {r.status_code} {r.text[:200]}")
        meta = {**meta, "version": int(meta.get("version", 1)) + 1}
    elif s.kind == "sharepoint":
        from .knowledge import _graph
        _, tok = _graph(db)
        name = meta.get("name", "")
        data = docx_lite.build(rows, f"Agent Hangar (aprovado por {who})") if name.lower().endswith(".docx") \
            else "\n\n".join(final).encode()
        with HTTP() as h:
            r = h.put(f"https://graph.microsoft.com/v1.0/drives/{meta['drive_id']}/items/{meta['item_id']}/content",
                      content=data, headers={"Authorization": f"Bearer {tok}"})
        if r.status_code >= 400:
            raise PlatformError(f"SharePoint recusou a nova versão: {r.status_code} {r.text[:200]}")
    from .knowledge import upsert
    upsert(db, s, d.external_id, d.title, "\n\n".join(final), d.url, d.acl, now(), meta)
    return d.title


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@handler("doc_change")
def decide_change(db, acc, r: HumanRequest, e: Employee, task, decision: str, edit: dict, reason: str, who: str):
    from .employee_tasks import request_dict
    p = r.action_payload or {}
    if decision == "reject":
        close(r, "reject", reason, who)
        if task:
            tasksmod.event(db, task, "decision", {"id": r.id, "decision": "reject", "by": who})
            tasksmod._resume(db, task, f"ALTERAÇÃO #{r.id}: RECUSADA por {who}" + (f" — {reason}" if reason else "")
                                       + ". Nada foi aplicado.")
        db.commit()
        return request_dict(db, r, e)
    if decision not in ("approve", "approve_edited"):
        raise PlatformError("alteração: decision = approve (edit.accept = trechos aceitos; edit.edits = {id: texto}) | reject")
    hunks = {h["id"]: h for h in p["hunks"]}
    accept = set(int(x) for x in (edit or {}).get("accept", list(hunks)))
    edits = {int(k): v for k, v in ((edit or {}).get("edits") or {}).items()}
    if not accept:
        raise PlatformError("aceite pelo menos um trecho (ou recuse a proposta)")
    current, _ = _current(db, task, p["document"])
    if _hash(current) != p["base_hash"]:
        raise PlatformError("o documento mudou desde a proposta: nada foi aplicado — peça uma proposta nova")
    old = p["old"]
    final, rows, k = [], [], 0
    ordered = sorted(hunks.values(), key=lambda h: h["i1"])
    i = 0
    for h in ordered:
        while i < h["i1"]:
            final.append(old[i])
            rows.append((old[i], None))
            i += 1
        if h["id"] in accept:
            after = _split(edits[h["id"]]) if h["id"] in edits else h["after"]
            final += after
            n = max(len(h["before"]), len(after))
            for j in range(n):
                rows.append((h["before"][j] if j < len(h["before"]) else "", after[j] if j < len(after) else ""))
            k += 1
        else:
            for x in h["before"]:
                final.append(x)
                rows.append((x, None))
        i = h["i2"]
    while i < len(old):
        final.append(old[i])
        rows.append((old[i], None))
        i += 1
    where = _apply(db, task, p, final, rows, who)
    close(r, "approve_edited" if (edits or k < len(hunks)) else "approve", reason, who)
    r.edited_payload = {"accepted": sorted(accept), "edits": {str(a): b for a, b in edits.items()}}
    if task:
        tasksmod.event(db, task, "decision", {"id": r.id, "decision": r.decision, "by": who, "accepted": k, "of": len(hunks)})
        tasksmod._resume(db, task, f"ALTERAÇÃO #{r.id} aprovada por {who}: {k} de {len(hunks)} trecho(s) aplicados em {where}."
                                   + (f" Comentário: {reason}" if reason else "") + " Continue a tarefa.")
    db.commit()
    return request_dict(db, r, e)
