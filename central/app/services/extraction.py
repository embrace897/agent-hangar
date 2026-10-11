"""Extração de campos de documentos (contratos, notas fiscais, boletos, currículos…) com esquema e revisão humana.

O esquema diz os campos (tipo, obrigatório, regra) e a confiança mínima. O Digital employee extrai (texto do arquivo;
PDF escaneado e imagem por OCR, no Data Studio), cada campo volta com valor, confiança e o trecho de onde saiu; o que
ficar abaixo do limiar, ou não passar na regra, vira um pedido `review_fields`: a pessoa confere só o que está em
dúvida, ao lado do trecho, e a tarefa segue com os valores conferidos. As correções contam na qualidade do funcionário.

Lote: `POST /api/employees/{slug}/batch` cria uma tarefa por arquivo e uma tarefa de consolidação que espera todas
(e gera a planilha com tudo).
"""
import csv
import io
import json
import re
from datetime import date, datetime

from sqlalchemy import select

from ..models import Employee, EmployeeTask, Extraction, ExtractionSchema, HumanRequest, TaskFile, Team, now
from . import employee_tasks as tasksmod
from . import llm, task_files
from .access import Access, Forbidden
from .common import PlatformError, audit, iso, slugify
from .decision_kinds import close, handler
from .platform_tools import Ctx, tool

TYPES = ("text", "number", "money", "date", "cpf", "cnpj", "email", "bool", "enum")
TEXT_TYPES = ("text/", "application/json", "application/xml")
SYSTEM = ("Você extrai campos de documentos com precisão. Responda SOMENTE um objeto JSON: {\"fields\": {<campo>: "
          "{\"value\": <valor ou null>, \"confidence\": <0 a 1>, \"evidence\": <trecho exato do documento de onde saiu>, "
          "\"page\": <número da página ou null>}}}. Não invente: se o campo não estiver no documento, value=null e "
          "confidence=0. Datas em AAAA-MM-DD; valores numéricos com ponto decimal, sem símbolo de moeda.")


# ------------------------------------------------------------------ validação
def _cpf_ok(v: str) -> bool:
    d = re.sub(r"\D", "", v)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (s * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def _cnpj_ok(v: str) -> bool:
    d = re.sub(r"\D", "", v)
    if len(d) != 14 or d == d[0] * 14:
        return False
    for n in (12, 13):
        w = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][13 - n:]
        s = sum(int(d[i]) * w[i] for i in range(n))
        if (11 - s % 11 if s % 11 >= 2 else 0) != int(d[n]):
            return False
    return True


def _number(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if "," in s and (s.rfind(",") > s.rfind(".")):  # 1.234,56 (padrão brasileiro)
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    return float(s)


def _date(v) -> str:
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("data inválida")


def normalize(field: dict, value):
    """(valor normalizado, erro). Valor vazio num campo obrigatório é erro."""
    if value in (None, ""):
        return None, ("obrigatório" if field.get("required") else "")
    t = field.get("type") or "text"
    try:
        if t in ("number", "money"):
            value = round(_number(value), 2) if t == "money" else _number(value)
        elif t == "date":
            value = _date(value)
        elif t == "cpf":
            if not _cpf_ok(str(value)):
                return value, "CPF inválido"
            d = re.sub(r"\D", "", str(value))
            value = f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
        elif t == "cnpj":
            if not _cnpj_ok(str(value)):
                return value, "CNPJ inválido"
            d = re.sub(r"\D", "", str(value))
            value = f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        elif t == "email":
            value = str(value).strip().lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                return value, "e-mail inválido"
        elif t == "bool":
            value = str(value).strip().lower() in ("true", "sim", "yes", "1", "s", "x")
        elif t == "enum":
            opts = [str(o) for o in field.get("options") or []]
            match = next((o for o in opts if o.lower() == str(value).strip().lower()), None)
            if match is None:
                return value, f"fora das opções ({', '.join(opts)})"
            value = match
        else:
            value = str(value).strip()
    except (ValueError, TypeError):
        return value, f"não é um {t} válido"
    if field.get("pattern") and not re.fullmatch(field["pattern"], str(value)):
        return value, "fora do formato esperado"
    return value, ""


# ------------------------------------------------------------------ esquemas
def _check_fields(fields) -> list[dict]:
    out, seen = [], set()
    for f in fields or []:
        name = slugify(str(f.get("name") or f.get("label") or "")).replace("-", "_")
        if not name or name in seen:
            raise PlatformError(f"campo sem nome ou repetido: {f}")
        if (f.get("type") or "text") not in TYPES:
            raise PlatformError(f"tipo do campo {name}: {', '.join(TYPES)}")
        if f.get("pattern"):
            try:
                re.compile(f["pattern"])
            except re.error as e:
                raise PlatformError(f"regra (pattern) do campo {name} inválida: {e}") from None
        if f.get("type") == "enum" and not f.get("options"):
            raise PlatformError(f"o campo {name} (enum) precisa das opções")
        seen.add(name)
        out.append({"name": name, "label": str(f.get("label") or name)[:120], "type": f.get("type") or "text",
                    "required": bool(f.get("required")), "pattern": f.get("pattern") or "",
                    "options": [str(o) for o in f.get("options") or []], "hint": str(f.get("hint") or "")[:300]})
    if not out:
        raise PlatformError("o esquema precisa de pelo menos um campo")
    return out


def schema_dict(s: ExtractionSchema) -> dict:
    return {"id": s.id, "name": s.name, "title": s.title or s.name, "description": s.description, "fields": s.fields,
            "min_confidence": s.min_confidence, "version": s.version, "status": s.status, "team": s.team_id,
            "created_by": s.created_by, "updated_at": iso(s.updated_at) if s.updated_at else None}


def _can_edit(acc: Access, s_team: int | None) -> bool:
    from .access import RANK
    return acc.p.is_admin or (s_team is not None and RANK.get(acc.teams.get(s_team, ""), 0) >= RANK["developer"])


def list_schemas(db, acc: Access) -> list[dict]:
    return [schema_dict(s) for s in db.scalars(select(ExtractionSchema).where(ExtractionSchema.status == "active")
                                               .order_by(ExtractionSchema.name))]


def save_schema(db, acc: Access, name: str, title: str = "", description: str = "", fields=None,
                min_confidence: float | None = None, team: str = "") -> dict:
    key = slugify(name or title).replace("-", "_")[:80]
    if not key:
        raise PlatformError("dê um nome ao esquema")
    team_id = None
    if team:
        t = db.scalar(select(Team).where(Team.slug == team))
        if t is None:
            raise PlatformError(f"time '{team}' não encontrado")
        team_id = t.id
    s = db.scalar(select(ExtractionSchema).where(ExtractionSchema.name == key))
    if not _can_edit(acc, team_id if s is None else s.team_id):
        raise Forbidden("só admins e quem desenvolve no time editam esquemas de extração")
    clean = _check_fields(fields)
    conf = 0.8 if min_confidence is None else float(min_confidence)
    if not 0 <= conf <= 1:
        raise PlatformError("min_confidence entre 0 e 1")
    if s is None:
        s = ExtractionSchema(name=key, created_by=acc.p.name, team_id=team_id, version=0)
        db.add(s)
    s.title, s.description, s.fields, s.min_confidence = (title or key)[:200], (description or "")[:2000], clean, conf
    s.version, s.status, s.updated_at = (s.version or 0) + 1, "active", now()
    db.commit()
    audit(db, acc.p.name, "extraction.schema", key, f"v{s.version} {len(clean)} campos")
    return schema_dict(s)


def archive_schema(db, acc: Access, name: str) -> dict:
    s = db.scalar(select(ExtractionSchema).where(ExtractionSchema.name == name))
    if s is None:
        raise PlatformError(f"esquema '{name}' não encontrado")
    if not _can_edit(acc, s.team_id):
        raise Forbidden("sem permissão para este esquema")
    s.status = "archived"
    db.commit()
    return schema_dict(s)


# ------------------------------------------------------------------ extração
def _file(db, task: EmployeeTask, name: str) -> TaskFile:
    f = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.name == name)
                  .order_by(TaskFile.version.desc()))
    if f is None:
        names = sorted({x.name for x in db.scalars(select(TaskFile).where(TaskFile.task_id == task.id))})
        raise PlatformError(f"'{name}' não está na tarefa (arquivos: {', '.join(names) or 'nenhum'})")
    return f


def document_text(ctx: Ctx, f: TaskFile) -> str:
    if f.content_type.startswith(TEXT_TYPES):
        return f.data.decode("utf-8", "replace")
    from . import analysis
    if not analysis.ready():
        raise PlatformError(f"{f.name} ({f.content_type}) precisa do Data Studio para ler PDF e imagens (OCR)")
    analysis.sync(ctx.db, ctx.task)
    return str(analysis.call(ctx.task, "inspect_file", {"path": f.name, "ocr": True, "pages": 30}))


def _fallback(text: str, fields: list[dict]) -> dict:
    """Sem LLM (modelo de teste): "Rótulo: valor" numa linha do documento."""
    out = {}
    for f in fields:
        names = [re.escape(x) for x in {f["label"], f["name"], f["name"].replace("_", " ")} if x]
        m = re.search(rf"(?im)^\s*(?:{'|'.join(names)})\s*[:=\-–]\s*(.+?)\s*$", text)
        out[f["name"]] = {"value": m.group(1) if m else None, "confidence": 0.95 if m else 0.0,
                          "evidence": m.group(0).strip()[:300] if m else "", "page": None}
    return out


def extract(ctx: Ctx, s: ExtractionSchema, f: TaskFile) -> Extraction:
    text = document_text(ctx, f)
    tgt = llm.target(ctx.db, ctx.agent)
    raw = None
    if tgt is not None:
        spec = "\n".join(f"- {x['name']} ({x['type']}{', obrigatório' if x['required'] else ''}): {x['label']}"
                         + (f" — opções: {', '.join(x['options'])}" if x["options"] else "") + (f" — {x['hint']}" if x["hint"] else "")
                         for x in s.fields)
        got = llm.complete_json(tgt, SYSTEM, f"Campos:\n{spec}\n\nDocumento ({f.name}):\n{text[:60000]}")
        raw = (got or {}).get("fields") if isinstance(got, dict) else None
    raw = raw if isinstance(raw, dict) else _fallback(text, s.fields)
    values, review = {}, False
    for fld in s.fields:
        item = raw.get(fld["name"]) or {}
        item = item if isinstance(item, dict) else {"value": item, "confidence": 0.5}
        value, err = normalize(fld, item.get("value"))
        try:
            conf = max(0.0, min(float(item.get("confidence") or 0), 1.0))
        except (TypeError, ValueError):
            conf = 0.0
        evidence = str(item.get("evidence") or "")[:500]
        if evidence and value not in (None, "") and evidence[:40].lower() not in text.lower():
            conf = min(conf, 0.5)  # trecho que não está no documento: não confia
        needs = bool(err) or (value not in (None, "") and conf < s.min_confidence) or (fld["required"] and value in (None, ""))
        review = review or needs
        values[fld["name"]] = {"value": value, "confidence": round(conf, 2), "evidence": evidence,
                               "page": item.get("page"), "error": err, "review": needs, "label": fld["label"]}
    ex = Extraction(task_id=ctx.task.id, schema_id=s.id, schema_version=s.version, file_name=f.name,
                    file_version=f.version, values=values, status="review" if review else "auto", created_at=now())
    ctx.db.add(ex)
    ctx.db.flush()
    return ex


def _plain(ex: Extraction) -> dict:
    return {k: v.get("value") for k, v in (ex.values or {}).items()}


def _save_result(db, task: EmployeeTask, ex: Extraction, who: str):
    data = json.dumps({"file": ex.file_name, "version": ex.file_version, "fields": _plain(ex), "status": ex.status},
                      ensure_ascii=False, indent=2).encode()
    base = ex.file_name.rsplit(".", 1)[0]
    task_files.store(db, task, f"extracao-{base}.json", data, "output", who, "application/json",
                     f"campos extraídos de {ex.file_name}")


def _schema(db, name: str) -> ExtractionSchema:
    s = db.scalar(select(ExtractionSchema).where(ExtractionSchema.name == slugify(name or "").replace("-", "_"),
                                                 ExtractionSchema.status == "active"))
    if s is None:
        names = [x.name for x in db.scalars(select(ExtractionSchema).where(ExtractionSchema.status == "active"))]
        raise PlatformError(f"esquema '{name}' não existe (esquemas: {', '.join(names) or 'nenhum'})")
    return s


@tool("list_extraction_schemas", "Os esquemas de extração da empresa: que campos tirar de cada tipo de documento.",
      {"properties": {}}, "extraction")
def list_extraction_schemas(ctx: Ctx, args: dict):
    return [{"name": s.name, "title": s.title, "fields": [f"{f['name']} ({f['type']})" for f in s.fields]}
            for s in ctx.db.scalars(select(ExtractionSchema).where(ExtractionSchema.status == "active"))]


@tool("extract_fields", "Extrai os campos de um esquema de um arquivo da tarefa (PDF, imagem, texto). Cada campo volta "
      "com valor, confiança e o trecho de origem. Campos em dúvida vão para uma pessoa conferir: a tarefa pausa e "
      "continua com os valores conferidos. Não reescreva os valores conferidos.",
      {"properties": {"file": {"type": "string", "description": "nome do arquivo da tarefa"},
                      "schema": {"type": "string", "description": "nome do esquema (list_extraction_schemas)"}},
       "required": ["file", "schema"]}, "extraction")
def extract_fields(ctx: Ctx, args: dict):
    s = _schema(ctx.db, str(args.get("schema") or ""))
    f = _file(ctx.db, ctx.task, str(args.get("file") or ""))
    done = ctx.db.scalar(select(Extraction).where(Extraction.task_id == ctx.task.id, Extraction.file_name == f.name,
                                                  Extraction.file_version == f.version, Extraction.schema_id == s.id,
                                                  Extraction.status.in_(("auto", "reviewed"))).order_by(Extraction.id.desc()))
    if done is not None:  # já extraído (e conferido): não refaz
        return {"file": f.name, "status": done.status, "fields": _plain(done)}
    ex = extract(ctx, s, f)
    who = f"agent:{ctx.agent.slug}"
    tasksmod.event(ctx.db, ctx.task, "extraction", {"id": ex.id, "file": f.name, "schema": s.name, "status": ex.status,
                                                    "review": [k for k, v in ex.values.items() if v["review"]]})
    if ex.status == "auto":
        _save_result(ctx.db, ctx.task, ex, who)
        ctx.db.commit()
        return {"file": f.name, "status": "auto", "fields": _plain(ex)}
    assigned, fallback = tasksmod._route(ctx.db, ctx.e, "manager")
    r = HumanRequest(employee_id=ctx.e.id, task_id=ctx.task.id, kind="review_fields", action_type="review", mode="approve",
                     question=f"Conferir {sum(v['review'] for v in ex.values.values())} campo(s) de {f.name}",
                     action_payload={"extraction": ex.id, "file": f.name, "file_version": f.version, "schema": s.name,
                                     "fields": ex.values},
                     assigned_user_id=assigned, fallback_user_id=fallback, approvals=[], created_at=now())
    ctx.db.add(r)
    ctx.db.flush()
    ex.request_id = r.id
    ctx.task.status = "waiting_human"
    tasksmod.event(ctx.db, ctx.task, "human_request", {"id": r.id, "kind": "review_fields", "file": f.name})
    ctx.db.commit()
    tasksmod._notify_request(ctx.db, ctx.e, r)
    return {"status": "waiting", "request_id": r.id,
            "message": f"Campos de {f.name} em dúvida: pedido #{r.id} para uma pessoa conferir; a tarefa continua depois."}


@handler("review_fields")
def decide_fields(db, acc, r: HumanRequest, e: Employee, task, decision: str, edit: dict, reason: str, who: str):
    ex = db.get(Extraction, (r.action_payload or {}).get("extraction"))
    if ex is None:
        raise PlatformError("extração não encontrada")
    if decision not in ("approve", "approve_edited", "reject"):
        raise PlatformError("conferência: decision = approve (com os valores em edit.fields) | reject (documento não serve)")
    if decision == "reject":
        if not reason:
            raise PlatformError("diga por que o documento não serve (reason)")
        ex.status, ex.reviewed_by = "rejected", who
        close(r, "reject", reason, who)
        if task:
            tasksmod.event(db, task, "decision", {"id": r.id, "decision": "reject", "by": who})
            tasksmod._resume(db, task, f"CONFERÊNCIA #{r.id}: RECUSADO por {who} — {reason}. Não use os campos de "
                                       f"{ex.file_name}; siga sem eles ou encerre explicando.")
        db.commit()
        from .employee_tasks import request_dict
        return request_dict(db, r, e)
    s = db.get(ExtractionSchema, ex.schema_id)
    fixed = (edit or {}).get("fields") or {}
    values, changed = dict(ex.values), 0
    for fld in s.fields:
        cur = dict(values.get(fld["name"]) or {})
        if fld["name"] in fixed:
            new, err = normalize(fld, fixed[fld["name"]])
            if err:
                raise PlatformError(f"{fld['label']}: {err}")
            if new != cur.get("value"):
                changed += 1
                cur.update(value=new, corrected_by=who, corrected_from=cur.get("value"))
            cur.update(confidence=1.0, error="", review=False)
        elif cur.get("review"):
            if cur.get("error") or (fld["required"] and cur.get("value") in (None, "")):
                raise PlatformError(f"{fld['label']}: {cur.get('error') or 'obrigatório'} — corrija o valor")
            cur.update(confidence=1.0, review=False)  # conferido como está
        values[fld["name"]] = cur
    ex.values, ex.status, ex.corrections, ex.reviewed_by = values, "reviewed", changed, who
    close(r, "approve_edited" if changed else "approve", reason, who)
    if task:
        _save_result(db, task, ex, who)
        tasksmod.event(db, task, "decision", {"id": r.id, "decision": r.decision, "by": who, "corrected": changed})
        tasksmod._resume(db, task, f"CONFERÊNCIA #{r.id} por {who}: valores de {ex.file_name} conferidos"
                                   f"{f' ({changed} corrigido(s))' if changed else ''}:\n"
                                   f"{json.dumps(_plain(ex), ensure_ascii=False)}\nUse exatamente estes valores e continue.")
    db.commit()
    from .employee_tasks import request_dict
    return request_dict(db, r, e)


@tool("export_extractions", "Junta numa planilha (CSV) os campos extraídos nesta tarefa e nas tarefas de que ela depende "
      "(o lote), um arquivo por linha, e salva como entregável.",
      {"properties": {"schema": {"type": "string"}, "filename": {"type": "string"}}, "required": ["schema"]}, "extraction")
def export_extractions(ctx: Ctx, args: dict):
    from ..models import TaskDependency
    s = _schema(ctx.db, str(args.get("schema") or ""))
    ids = [ctx.task.id, *ctx.db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == ctx.task.id))]
    rows = list(ctx.db.scalars(select(Extraction).where(Extraction.task_id.in_(ids), Extraction.schema_id == s.id,
                                                        Extraction.status.in_(("auto", "reviewed"))).order_by(Extraction.id)))
    latest: dict[tuple, Extraction] = {}
    for ex in rows:
        latest[(ex.task_id, ex.file_name)] = ex
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["arquivo", "tarefa", "status", *[f["name"] for f in s.fields]])
    for ex in latest.values():
        w.writerow([ex.file_name, ex.task_id, ex.status, *[(ex.values.get(f["name"]) or {}).get("value") for f in s.fields]])
    name = str(args.get("filename") or f"{s.name}-consolidado.csv")
    f = task_files.store(ctx.db, ctx.task, name, buf.getvalue().encode("utf-8-sig"), "output", f"agent:{ctx.agent.slug}",
                         "text/csv", f"{len(latest)} documento(s), esquema {s.name}")
    ctx.db.commit()
    pending = len({(x.task_id, x.file_name) for x in ctx.db.scalars(select(Extraction).where(
        Extraction.task_id.in_(ids), Extraction.schema_id == s.id, Extraction.status == "review"))})
    return f"{f.name} v{f.version}: {len(latest)} documento(s)" + (f"; {pending} ainda em conferência" if pending else "")


def task_extractions(db, acc: Access, task_id: int) -> list[dict]:
    from .task_thread import _task
    t, _, _ = _task(db, acc, task_id)
    return [{"id": x.id, "file": x.file_name, "file_version": x.file_version, "status": x.status, "values": x.values,
             "corrections": x.corrections, "reviewed_by": x.reviewed_by, "request": x.request_id,
             "schema": (db.get(ExtractionSchema, x.schema_id) or ExtractionSchema(name="?")).name,
             "created_at": iso(x.created_at)} for x in db.scalars(select(Extraction).where(Extraction.task_id == t.id)
                                                                  .order_by(Extraction.id))]


# ------------------------------------------------------------------ lote: uma tarefa por arquivo + consolidação
def batch(db, acc: Access, slug: str, title: str, schema: str, files: list[tuple[str, bytes, str]], priority: int = 2) -> dict:
    from . import employees, task_flow
    s = _schema(db, schema)
    if not files:
        raise PlatformError("mande os arquivos do lote")
    if len(files) > 200:
        raise PlatformError("no máximo 200 arquivos por lote")
    e, a = employees.find(db, acc, slug, "assign")
    if e.status not in ("probation", "active", "paused"):
        raise PlatformError(f"'{slug}' ainda não trabalha (status {e.status})")
    children = []
    for name, data, ctype in files:  # nasce em rascunho: o executor só a pega depois do arquivo anexado
        t = tasksmod.create_task(db, e, f"{title}: {name}"[:300],
                                 f"Extraia os campos do esquema {s.name} do arquivo {name}:\nuse extract_fields "
                                 f"{json.dumps({'file': name, 'schema': s.name}, ensure_ascii=False)}", "batch",
                                 acc.p.name, acc.p.user_id, priority, None, status="draft")
        task_files.store(db, t, name, data, "input", acc.p.name, ctype, f"lote: {title}"[:200])
        children.append(t)
    pt = tasksmod.create_task(db, e, f"{title}: consolidação"[:300],
                              f"Junte numa planilha os campos extraídos no lote ({len(children)} arquivos), aponte os "
                              f"documentos com problema e resuma:\nuse export_extractions "
                              f"{json.dumps({'schema': s.name}, ensure_ascii=False)}", "batch", acc.p.name,
                              acc.p.user_id, priority, None, status="draft")
    task_flow.attach_blockers(db, pt, children, acc.p.name)  # a consolidação espera todas
    for t in children:
        t.status = "new"
    db.commit()
    audit(db, acc.p.name, "extraction.batch", a.slug, f"{len(children)} arquivo(s) · {s.name}")
    return {"parent": pt.id, "tasks": [c.id for c in children], "schema": s.name}


def quality(db, e: Employee, since: date | None = None) -> dict:
    """Extrações deste funcionário: quantas foram direto, quantas foram conferidas e quantos campos corrigidos."""
    q = select(Extraction).join(EmployeeTask, EmployeeTask.id == Extraction.task_id).where(EmployeeTask.employee_id == e.id)
    rows = list(db.scalars(q))
    return {"total": len(rows), "auto": sum(x.status == "auto" for x in rows),
            "reviewed": sum(x.status == "reviewed" for x in rows), "corrected_fields": sum(x.corrections or 0 for x in rows)}
