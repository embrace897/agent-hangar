"""Arquivos das tarefas: o que a pessoa anexa (input) e o que o Digital employee entrega (output, o "entregável").

- Guardados no banco (cabem várias réplicas sem volume compartilhado), até FILES_MAX_MB por arquivo e
  FILES_TASK_MAX_MB por tarefa. O mesmo nome vira uma versão nova; as anteriores ficam.
- Quem lê a tarefa lê os arquivos; quem pode escrever na tarefa anexa. O agente usa três ferramentas da plataforma:
  list_files, read_file (texto) e save_file (o entregável), sem alçada: ficam dentro da plataforma.
- Visualizar no navegador só tipos seguros (texto, imagens comuns, PDF); o resto (HTML, SVG…) só baixa.
- A biblioteca lista os entregáveis de todas as tarefas que a pessoa pode ler.
"""
import base64
import hashlib
import mimetypes
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config
from ..models import Agent, Employee, EmployeeTask, Project, TaskFile, TaskMessage, now
from . import employee_tasks as tasksmod
from .access import Access, Forbidden
from .common import PlatformError, audit, iso

INLINE = ("text/plain", "text/markdown", "text/csv", "application/json", "image/png", "image/jpeg", "image/gif",
          "image/webp", "application/pdf")
TEXTUAL = ("text/", "application/json", "application/xml", "application/x-yaml", "application/yaml")
READ_MAX = 100_000
_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,200}$")


def _ctype(name: str, given: str = "") -> str:
    if given and given != "application/octet-stream":
        return given.split(";")[0].strip()[:120]
    guess = mimetypes.guess_type(name)[0]
    if name.lower().endswith(".md"):
        guess = "text/markdown"
    return guess or "application/octet-stream"


def _clean(name: str) -> str:
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not _NAME.match(name) or name in (".", ".."):
        raise PlatformError("nome de arquivo inválido")
    return name


def file_dict(f: TaskFile, history: list | None = None) -> dict:
    d = {"id": f.id, "task_id": f.task_id, "name": f.name, "version": f.version, "kind": f.kind,
         "content_type": f.content_type, "size": f.size, "sha256": f.sha256, "uploaded_by": f.uploaded_by, "note": f.note,
         "at": iso(tasksmod._aware(f.created_at)), "inline": f.content_type in INLINE}
    if history is not None:
        d["history"] = history
    return d


def store(db: Session, t: EmployeeTask, name: str, data: bytes, kind: str, who: str, content_type: str = "",
          note: str = "") -> TaskFile:
    name = _clean(name)
    if len(data) > config.FILES_MAX_MB * 1024 * 1024:
        raise PlatformError(f"arquivo grande demais (máx. {config.FILES_MAX_MB} MB)")
    used = db.scalar(select(func.coalesce(func.sum(TaskFile.size), 0)).where(TaskFile.task_id == t.id)) or 0
    if used + len(data) > config.FILES_TASK_MAX_MB * 1024 * 1024:
        raise PlatformError(f"a tarefa já tem {used // (1024 * 1024)} MB de arquivos (máx. {config.FILES_TASK_MAX_MB} MB)")
    last = db.scalar(select(func.max(TaskFile.version)).where(TaskFile.task_id == t.id, TaskFile.name == name)) or 0
    f = TaskFile(task_id=t.id, name=name, version=last + 1, kind=kind, content_type=_ctype(name, content_type),
                 size=len(data), sha256=hashlib.sha256(data).hexdigest(), data=data, uploaded_by=who,
                 note=(note or "")[:1000], created_at=now())
    db.add(f)
    db.flush()
    tasksmod.event(db, t, "file", {"name": name, "version": f.version, "kind": kind, "by": who})
    return f


def _readable_task(db: Session, acc: Access, task_id: int, write: bool = False):
    from .task_thread import _task
    return _task(db, acc, task_id, write=write)


def upload(db: Session, acc: Access, task_id: int, name: str, data: bytes, content_type: str = "", note: str = "") -> dict:
    t, e, a = _readable_task(db, acc, task_id, write=True)
    f = store(db, t, name, data, "input", acc.p.name, content_type, note)
    if t.status not in tasksmod.TERMINAL:  # o funcionário fica sabendo pela conversa (entra na próxima rodada)
        db.add(TaskMessage(task_id=t.id, author=acc.p.name, user_id=acc.p.user_id, status="queued", created_at=now(),
                           body=f"[arquivo anexado: {f.name} v{f.version} ({f.content_type}, {f.size} bytes) — use read_file]"))
    db.commit()
    audit(db, acc.p.name, "employee.task.file", a.slug, f"#{t.id} {f.name} v{f.version}")
    return file_dict(f)


def list_files(db: Session, acc: Access, task_id: int) -> list[dict]:
    t, _, _ = _readable_task(db, acc, task_id)
    return _grouped(db, t.id)


def _grouped(db: Session, task_id: int) -> list[dict]:
    rows = list(db.scalars(select(TaskFile).where(TaskFile.task_id == task_id).order_by(TaskFile.name, TaskFile.version.desc())))
    out, seen = [], {}
    for f in rows:
        if f.name in seen:
            seen[f.name]["history"].append({"id": f.id, "version": f.version, "at": iso(tasksmod._aware(f.created_at)),
                                            "by": f.uploaded_by, "size": f.size})
            continue
        d = file_dict(f, [])
        seen[f.name] = d
        out.append(d)
    return out


def get(db: Session, acc: Access, file_id: int) -> TaskFile:
    f = db.get(TaskFile, file_id)
    if f is None:
        raise Forbidden("arquivo não encontrado ou sem acesso")
    _readable_task(db, acc, f.task_id)
    return f


def delete(db: Session, acc: Access, file_id: int):
    f = db.get(TaskFile, file_id)
    if f is None:
        raise Forbidden("arquivo não encontrado ou sem acesso")
    t, e, a = _readable_task(db, acc, f.task_id, write=True)
    if f.uploaded_by != acc.p.name:
        from .employees import find
        find(db, acc, a.slug, "manage")
    db.delete(f)
    tasksmod.event(db, t, "file", {"name": f.name, "version": f.version, "deleted_by": acc.p.name})
    db.commit()
    audit(db, acc.p.name, "employee.task.file.delete", a.slug, f"#{t.id} {f.name} v{f.version}")


def library(db: Session, acc: Access, q: str = "", kind: str = "output", project: str = "", employee: str = "",
            limit: int = 200) -> list[dict]:
    """Os entregáveis (ou anexos) de todas as tarefas que a pessoa pode ler, os mais novos primeiro (última versão)."""
    from .employees import can_read_task
    query = select(TaskFile).order_by(TaskFile.id.desc()).limit(2000)
    if kind in ("input", "output"):
        query = query.where(TaskFile.kind == kind)
    if q:
        query = query.where(TaskFile.name.ilike(f"%{q.strip()[:80]}%"))
    pid = None
    if project:
        p = db.scalar(select(Project).where(Project.slug == project))
        pid = p.id if p else -1
    out, seen, ok_task = [], set(), {}
    for f in db.scalars(query):
        if (f.task_id, f.name) in seen:
            continue
        if f.task_id not in ok_task:
            t = db.get(EmployeeTask, f.task_id)
            e = db.get(Employee, t.employee_id) if t else None
            a = db.get(Agent, e.agent_id) if e else None
            good = t is not None and e is not None and can_read_task(acc, t, e)
            good = good and (pid is None or t.project_id == pid) and (not employee or (a and a.slug == employee))
            ok_task[f.task_id] = (good, t, a)
        good, t, a = ok_task[f.task_id]
        if not good:
            continue
        seen.add((f.task_id, f.name))
        out.append({**file_dict(f), "task_title": t.title, "employee": a.slug if a else "", "employee_name": a.name if a else ""})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ ferramentas do agente (pelo gate)
def agent_tool(db: Session, e: Employee, t: EmployeeTask, tool: str, args: dict) -> dict:
    """list_files | read_file | save_file, dentro da tarefa do próprio funcionário."""
    a = db.get(Agent, e.agent_id)
    if tool == "list_files":
        files = _grouped(db, t.id)
        text = "\n".join(f"- {f['name']} v{f['version']} ({f['kind']}, {f['content_type']}, {f['size']} bytes)" for f in files)
        return {"status": "allowed", "result": text or "(nenhum arquivo nesta tarefa)"}
    if tool == "read_file":
        name = _clean(str(args.get("name") or ""))
        q = select(TaskFile).where(TaskFile.task_id == t.id, TaskFile.name == name)
        if args.get("version"):
            q = q.where(TaskFile.version == int(args["version"]))
        f = db.scalar(q.order_by(TaskFile.version.desc()))
        if f is None:
            return {"status": "allowed", "result": f"arquivo '{name}' não existe nesta tarefa (use list_files)"}
        if not f.content_type.startswith(TEXTUAL):
            return {"status": "allowed", "result": f"'{name}' é binário ({f.content_type}, {f.size} bytes): não dá para ler como texto"}
        text = f.data.decode("utf-8", errors="replace")
        return {"status": "allowed", "result": text[:READ_MAX] + (f"\n… [truncado: {len(text)} caracteres]" if len(text) > READ_MAX else "")}
    if tool == "save_file":
        name = str(args.get("name") or "")
        if args.get("content_base64"):
            try:
                data = base64.b64decode(str(args["content_base64"]), validate=True)
            except ValueError:
                return {"status": "allowed", "result": "content_base64 inválido"}
        else:
            data = str(args.get("content") or "").encode("utf-8")
        try:
            f = store(db, t, name, data, "output", f"agent:{a.slug}", str(args.get("content_type") or ""), str(args.get("note") or ""))
        except PlatformError as ex:
            return {"status": "allowed", "result": f"não salvei: {ex}"}
        db.commit()
        return {"status": "allowed", "result": f"salvo: {f.name} v{f.version} ({f.size} bytes) — aparece como entregável da tarefa"}
    return {"status": "denied", "message": f"ferramenta de arquivo desconhecida: {tool}"}

