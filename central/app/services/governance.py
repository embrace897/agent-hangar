"""Governança de dados: classificação, DLP, retenção, pedidos do titular (LGPD) e barreiras de informação.

- **Classificação** (public < internal < confidential < restricted) na tarefa, no arquivo, no projeto e na fonte de
  conhecimento; a da tarefa é a maior delas. Cada conexão de LLM diz o mais sensível que pode receber
  (`max_classification`): uma tarefa acima disso não vai para aquele modelo.
- **DLP:** CPF, CNPJ, cartão (Luhn), e-mail, telefone e CID mascarados ([CPF_1]) no que a plataforma manda ao modelo
  — o pedido, a conversa da tarefa, os arquivos e o resultado das ferramentas da plataforma. Quem executa a ação recebe
  o valor real (o gate devolve os argumentos sem máscara) e o resultado guardado para as pessoas também. O que o
  runtime do agente busca direto numa ferramenta dele não passa por aqui: para isso valem a alçada e a política do
  plugin. `block_outbound` impede que um dado desses saia numa mensagem para fora.
- **Retenção** por classificação: o conteúdo de tarefas encerradas há mais de N dias é apagado (o registro e a
  auditoria ficam), salvo retenção legal (`legal_hold`) na tarefa ou no projeto.
- **Titular (LGPD):** busca os dados de uma pessoa (CPF, e-mail ou nome) em tarefas, conversas, arquivos de texto,
  extrações, conversas com gente de fora e na base de conhecimento; a remoção exige a aprovação de outro admin.
- **Barreira de informação:** projeto confidencial só é lido pelos membros (e por quem pediu a tarefa) — nem admins e
  auditores entram sem o acesso de emergência, que pede justificativa, vale uma hora, fica na auditoria e avisa o dono.
"""
import re
from datetime import timedelta

from sqlalchemy import select

from .. import config, crypto, shared, tenancy
from ..models import (
    DlpToken,
    EmployeeTask,
    ExternalMessage,
    ExternalThread,
    Extraction,
    KnowledgeChunk,
    KnowledgeDoc,
    Organization,
    Project,
    SubjectRequest,
    TaskCheckpoint,
    TaskFile,
    TaskMessage,
    User,
    now,
)
from .access import Access, Forbidden
from .common import PlatformError, audit, iso

LEVELS = ("public", "internal", "confidential", "restricted")
RANK = {c: i for i, c in enumerate(LEVELS)}
REDACTED = "[apagado pela retenção]"


def _luhn(d: str) -> bool:
    s, alt = 0, False
    for ch in reversed(d):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        s, alt = s + n, not alt
    return s % 10 == 0


def _cpf(m: str) -> bool:
    from .extraction import _cpf_ok
    return _cpf_ok(m)


def _cnpj(m: str) -> bool:
    from .extraction import _cnpj_ok
    return _cnpj_ok(m)


DETECTORS = {
    "cpf": (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), _cpf),
    "cnpj": (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), _cnpj),
    "card": (re.compile(r"\b(?:\d[ -]?){13,19}\b"), lambda m: 13 <= len(re.sub(r"\D", "", m)) <= 19 and _luhn(re.sub(r"\D", "", m))),
    "email": (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), lambda m: True),
    "phone": (re.compile(r"(?:\+?55\s?)?\(?\b\d{2}\)?\s?9\d{4}[-\s]?\d{4}\b"), lambda m: True),
    "health": (re.compile(r"\bCID[-\s]?(?:10)?[:\s]*[A-TV-Z]\d{2}(?:\.\d)?\b", re.I), lambda m: True),
}
DEFAULT = {"dlp": {"mask": [], "block_outbound": []},
           "retention": {"public": None, "internal": None, "confidential": None, "restricted": None}}


def policy(db) -> dict:
    o = db.get(Organization, tenancy.current_org())
    g = (o.governance if o else None) or {}
    return {"dlp": {**DEFAULT["dlp"], **(g.get("dlp") or {})}, "retention": {**DEFAULT["retention"], **(g.get("retention") or {})}}


def set_policy(db, acc: Access, changes: dict) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin muda a política de dados")
    o = db.get(Organization, tenancy.current_org())
    g = policy(db)
    dlp = changes.get("dlp") or {}
    for k in ("mask", "block_outbound"):
        if dlp.get(k) is not None:
            bad = [x for x in dlp[k] if x not in DETECTORS]
            if bad:
                raise PlatformError(f"detectores: {', '.join(DETECTORS)}")
            g["dlp"][k] = sorted(set(dlp[k]))
    for lvl, days in (changes.get("retention") or {}).items():
        if lvl not in LEVELS:
            raise PlatformError(f"classificação: {', '.join(LEVELS)}")
        g["retention"][lvl] = None if days in (None, "", 0) else max(30, int(days))
    o.governance = g
    db.commit()
    audit(db, acc.p.name, "governance.policy", "org", "")
    return view(db)


def view(db) -> dict:
    return {**policy(db), "detectors": list(DETECTORS), "levels": list(LEVELS)}


def detect(text: str, kinds=None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, (rx, ok) in DETECTORS.items():
        if kinds is not None and k not in kinds:
            continue
        hits = [m.group(0) for m in rx.finditer(text or "") if ok(m.group(0))]
        if hits:
            out[k] = hits
    return out


# ------------------------------------------------------------------ classificação
def effective(db, t: EmployeeTask) -> str:
    lv = [t.classification or "internal"]
    if t.project_id:
        p = db.get(Project, t.project_id)
        if p is not None:
            lv.append(p.classification or "internal")
    lv += [c for c in db.scalars(select(TaskFile.classification).where(TaskFile.task_id == t.id)) if c]
    return max(lv, key=lambda x: RANK.get(x, 1))


def llm_blocked(db, t: EmployeeTask, agent) -> str:
    """Motivo para a tarefa NÃO ir ao modelo do agente (vazio = pode)."""
    from .catalog import get_connection
    from .common import spec_of
    try:
        llm = spec_of(agent).get("llm") or {}
    except PlatformError:
        return ""
    name = (llm.get("prod") or {}).get("connection") or llm.get("connection") or (llm.get("stage") or {}).get("connection")
    if not name:
        return ""
    try:
        conn = get_connection(db, name)
    except PlatformError:
        return ""
    lvl = effective(db, t)
    if RANK.get(lvl, 1) > RANK.get(conn.max_classification or "confidential", 2):
        return (f"a tarefa é '{lvl}' e a conexão de LLM '{conn.name}' só recebe até '{conn.max_classification}': "
                "use um modelo liberado para esse nível ou reclassifique")
    return ""


def classify(db, acc: Access, kind: str, ref, classification: str | None = None, legal_hold: bool | None = None) -> dict:
    if classification is not None and classification not in LEVELS:
        raise PlatformError(f"classificação: {', '.join(LEVELS)}")
    if kind == "task":
        from .task_thread import _task
        t, _, _ = _task(db, acc, int(ref), write=True)
        obj = t
    elif kind == "file":
        f = db.get(TaskFile, int(ref))
        if f is None:
            raise PlatformError("arquivo não encontrado")
        from .task_thread import _task
        _task(db, acc, f.task_id, write=True)
        obj = f
    else:
        raise PlatformError("kind: task | file")
    if classification is not None:
        if RANK[classification] < RANK.get(obj.classification or "internal", 1) and not acc.p.is_admin:
            raise Forbidden("baixar a classificação é com o admin")
        obj.classification = classification
    if legal_hold is not None and hasattr(obj, "legal_hold"):
        if not acc.p.is_admin:
            raise Forbidden("retenção legal é com o admin")
        obj.legal_hold = bool(legal_hold)
    db.commit()
    audit(db, acc.p.name, f"governance.classify.{kind}", str(ref), f"{classification or ''} {'' if legal_hold is None else 'hold=' + str(legal_hold)}")
    return {"kind": kind, "id": int(ref), "classification": obj.classification, "legal_hold": getattr(obj, "legal_hold", None)}


def project_governance(db, acc: Access, slug: str, changes: dict) -> dict:
    p = db.scalar(select(Project).where(Project.slug == slug))
    if p is None:
        raise PlatformError("projeto não encontrado")
    from .access import RANK as TEAM_RANK
    if not (acc.p.is_admin or TEAM_RANK.get(acc.teams.get(p.team_id, ""), 0) >= TEAM_RANK["maintainer"]):
        raise Forbidden("quem mantém o time (ou o admin) define a governança do projeto")
    if changes.get("classification") is not None:
        if changes["classification"] not in LEVELS:
            raise PlatformError(f"classificação: {', '.join(LEVELS)}")
        p.classification = changes["classification"]
    if changes.get("legal_hold") is not None:
        if not acc.p.is_admin:
            raise Forbidden("retenção legal é com o admin")
        p.legal_hold = bool(changes["legal_hold"])
    if changes.get("confidential") is not None:
        p.confidential = bool(changes["confidential"])
    if changes.get("members") is not None:
        p.members = sorted({str(m).strip().lower() for m in changes["members"] if str(m).strip()})
    db.commit()
    audit(db, acc.p.name, "governance.project", slug, ", ".join(sorted(k for k, v in changes.items() if v is not None)))
    return {"slug": p.slug, "classification": p.classification, "legal_hold": p.legal_hold, "confidential": p.confidential,
            "members": p.members or []}


# ------------------------------------------------------------------ DLP
def _tokens(db, task_id: int) -> dict[str, str]:
    return {r.token: crypto.decrypt(r.value) for r in db.scalars(select(DlpToken).where(DlpToken.task_id == task_id))}


def mask(db, task: EmployeeTask, text: str) -> str:
    kinds = policy(db)["dlp"]["mask"]
    if not kinds or not text:
        return text
    found = detect(text, kinds)
    if not found:
        return text
    have = _tokens(db, task.id)
    by_value = {v: k for k, v in have.items()}
    counts: dict[str, int] = {}
    for tok in have:
        kind = tok.strip("[]").rsplit("_", 1)[0].lower()
        counts[kind] = counts.get(kind, 0) + 1
    for kind, values in found.items():
        for v in sorted(set(values), key=len, reverse=True):
            tok = by_value.get(v)
            if tok is None:
                counts[kind] = counts.get(kind, 0) + 1
                tok = f"[{kind.upper()}_{counts[kind]}]"
                db.add(DlpToken(task_id=task.id, token=tok, value=crypto.encrypt(v)))
                by_value[v] = tok
            text = text.replace(v, tok)
    db.flush()
    return text


def unmask(db, task_id: int | None, obj):
    if not task_id:
        return obj
    toks = _tokens(db, task_id)
    if not toks:
        return obj

    def go(x):
        if isinstance(x, str):
            for k, v in toks.items():
                x = x.replace(k, v)
            return x
        if isinstance(x, list):
            return [go(i) for i in x]
        if isinstance(x, dict):
            return {k: go(v) for k, v in x.items()}
        return x
    return go(obj)


def mask_messages(db, task: EmployeeTask, messages: list[dict]) -> list[dict]:
    if not policy(db)["dlp"]["mask"]:
        return messages
    out = [{**m, "content": mask(db, task, m["content"])} if m.get("role") == "user" and isinstance(m.get("content"), str)
           else m for m in messages]
    db.commit()
    return out


def check_outbound(db, text: str):
    kinds = policy(db)["dlp"]["block_outbound"]
    hits = detect(text, kinds) if kinds else {}
    if hits:
        raise PlatformError(f"dados que a empresa não deixa sair numa mensagem: {', '.join(hits)}. Tire do texto ou peça "
                            "a uma pessoa para enviar.")


# ------------------------------------------------------------------ retenção
def purge(db, t: EmployeeTask, who: str = "retenção"):
    t.body, t.result, t.error = REDACTED, REDACTED if t.result else "", ""
    for m in db.scalars(select(TaskMessage).where(TaskMessage.task_id == t.id)):
        m.body = REDACTED
    for f in db.scalars(select(TaskFile).where(TaskFile.task_id == t.id)):
        db.delete(f)
    for c in db.scalars(select(TaskCheckpoint).where(TaskCheckpoint.task_id == t.id)):
        c.messages = []
    for x in db.scalars(select(Extraction).where(Extraction.task_id == t.id)):
        x.values = {}
    for th in db.scalars(select(ExternalThread).where(ExternalThread.task_id == t.id)):
        for m in db.scalars(select(ExternalMessage).where(ExternalMessage.thread_id == th.id)):
            m.body = REDACTED
    for d in db.scalars(select(DlpToken).where(DlpToken.task_id == t.id)):
        db.delete(d)
    t.purged_at = now()


def retention_sweep(db, at=None) -> int:
    at, n = at or now(), 0
    rules = policy(db)["retention"]
    from .employee_tasks import TERMINAL, _aware
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.status.in_(TERMINAL), EmployeeTask.purged_at.is_(None),
                                                   EmployeeTask.legal_hold.is_(False), EmployeeTask.finished_at.is_not(None))).all():
        days = rules.get(effective(db, t))
        if not days or _aware(t.finished_at) > at - timedelta(days=int(days)):
            continue
        if t.project_id:
            p = db.get(Project, t.project_id)
            if p is not None and p.legal_hold:
                continue
        purge(db, t)
        n += 1
    if n:
        db.commit()
        audit(db, "retenção", "governance.retention", "", f"{n} tarefa(s)")
    return n


# ------------------------------------------------------------------ titular (LGPD)
def _find_subject(db, subject: str) -> dict:
    q = subject.strip()
    if not q:
        raise PlatformError("informe o CPF, e-mail ou nome")
    variants = {q, q.lower()}
    digits = re.sub(r"\D", "", q)
    if len(digits) == 11:  # CPF com e sem máscara
        variants |= {digits, f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"}

    def has(text):
        t = text or ""
        return any(v and v.lower() in t.lower() for v in variants)
    found = {"tasks": [], "messages": [], "files": [], "extractions": [], "conversations": [], "knowledge": []}
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.purged_at.is_(None))):
        if has(t.title) or has(t.body) or has(t.result):
            found["tasks"].append({"id": t.id, "title": t.title[:120]})
    for m in db.scalars(select(TaskMessage)):
        if has(m.body):
            found["messages"].append({"id": m.id, "task": m.task_id})
    for f in db.scalars(select(TaskFile)):
        if f.content_type.startswith(("text/", "application/json")) and has(f.data.decode("utf-8", "replace")):
            found["files"].append({"id": f.id, "task": f.task_id, "name": f.name})
    for x in db.scalars(select(Extraction)):
        if has(str(x.values)):
            found["extractions"].append({"id": x.id, "task": x.task_id, "file": x.file_name})
    for th in db.scalars(select(ExternalThread)):
        if has(th.contact) or has(th.contact_name) or any(has(m.body) for m in db.scalars(
                select(ExternalMessage).where(ExternalMessage.thread_id == th.id))):
            found["conversations"].append({"id": th.id, "task": th.task_id, "contact": th.contact})
    for d in db.scalars(select(KnowledgeDoc)):
        if has(d.text):
            found["knowledge"].append({"id": d.id, "title": d.title})
    return {"subject": q, "found": found, "total": sum(len(v) for v in found.values())}


def subject_request(db, acc: Access, subject: str, kind: str = "access", note: str = "") -> dict:
    if not (acc.p.is_admin or acc.p.is_auditor):
        raise Forbidden("pedidos do titular são com o admin ou o auditor")
    if kind not in ("access", "erase"):
        raise PlatformError("kind: access | erase")
    rep = _find_subject(db, subject)
    r = SubjectRequest(subject=subject.strip()[:300], kind=kind, status="done" if kind == "access" else "open", report=rep,
                       requested_by=acc.p.name, note=(note or "")[:2000], created_at=now(),
                       done_at=now() if kind == "access" else None)
    db.add(r)
    db.commit()
    audit(db, acc.p.name, f"lgpd.{kind}", f"#{r.id}", f"{rep['total']} ocorrência(s)")
    return subject_dict(r)


def subject_dict(r: SubjectRequest) -> dict:
    return {"id": r.id, "subject": r.subject, "kind": r.kind, "status": r.status, "report": r.report, "requested_by": r.requested_by,
            "approved_by": r.approved_by, "note": r.note, "created_at": iso(r.created_at), "done_at": iso(r.done_at) if r.done_at else None}


def list_subject_requests(db, acc: Access) -> list[dict]:
    if not (acc.p.is_admin or acc.p.is_auditor):
        raise Forbidden("pedidos do titular são com o admin ou o auditor")
    return [subject_dict(r) for r in db.scalars(select(SubjectRequest).order_by(SubjectRequest.id.desc()).limit(100))]


def erase(db, acc: Access, request_id: int, approve: bool = True) -> dict:
    r = db.get(SubjectRequest, request_id)
    if r is None or r.kind != "erase" or r.status != "open":
        raise PlatformError("pedido de remoção não encontrado ou já decidido")
    if not acc.p.is_admin:
        raise Forbidden("a remoção é aprovada por um admin")
    if acc.p.name == r.requested_by:
        raise Forbidden("quatro olhos: quem pediu a remoção não aprova")
    if not approve:
        r.status, r.approved_by, r.done_at = "rejected", acc.p.name, now()
        db.commit()
        return subject_dict(r)
    q = r.subject
    digits = re.sub(r"\D", "", q)
    variants = sorted({q, *( [digits, f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"] if len(digits) == 11 else [])},
                      key=len, reverse=True)
    mark = "[REMOVIDO-LGPD]"

    def scrub(text):
        if not text:
            return text
        for v in variants:
            text = re.sub(re.escape(v), mark, text, flags=re.I)
        return text
    f = r.report["found"]
    for x in f["tasks"]:
        t = db.get(EmployeeTask, x["id"])
        if t:
            t.title, t.body, t.result = scrub(t.title), scrub(t.body), scrub(t.result)
    for x in f["messages"]:
        m = db.get(TaskMessage, x["id"])
        if m:
            m.body = scrub(m.body)
    for x in f["files"]:
        fl = db.get(TaskFile, x["id"])
        if fl:
            db.delete(fl)
    for x in f["extractions"]:
        e = db.get(Extraction, x["id"])
        if e:
            e.values = {k: {**v, "value": mark} if v.get("value") and any(str(v["value"]).lower() == s.lower() for s in variants) else v
                        for k, v in (e.values or {}).items()}
    for x in f["conversations"]:
        th = db.get(ExternalThread, x["id"])
        if th:
            th.contact_name = scrub(th.contact_name)
            for m in db.scalars(select(ExternalMessage).where(ExternalMessage.thread_id == th.id)):
                m.body = scrub(m.body)
    for x in f["knowledge"]:
        d = db.get(KnowledgeDoc, x["id"])
        if d:
            d.text = scrub(d.text)
            for c in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.doc_id == d.id)):
                c.text, c.norm = scrub(c.text), scrub(c.norm)
    r.status, r.approved_by, r.done_at = "done", acc.p.name, now()
    db.commit()
    audit(db, acc.p.name, "lgpd.erase", f"#{r.id}", f"aprovado; pedido por {r.requested_by}")
    return subject_dict(r)


# ------------------------------------------------------------------ barreira de informação
def barrier_blocks(db, acc: Access, t: EmployeeTask) -> bool:
    """True quando a tarefa é de um projeto confidencial e quem lê não é membro, nem quem pediu, nem tem acesso de
    emergência válido."""
    if not t.project_id:
        return False
    p = db.get(Project, t.project_id)
    if p is None or not p.confidential:
        return False
    who = (acc.p.name or "").lower()
    if acc.p.user_id and acc.p.user_id == t.requester_user_id:
        return False
    u = db.get(User, acc.p.user_id) if acc.p.user_id else None
    email = (u.email if u else who).lower()
    if email in (p.members or []):
        return False
    return not shared.get(f"breakglass:{p.id}:{email}")


def break_glass(db, acc: Access, slug: str, reason: str) -> dict:
    p = db.scalar(select(Project).where(Project.slug == slug))
    if p is None or not p.confidential:
        raise PlatformError("projeto confidencial não encontrado")
    if not (acc.p.is_admin or acc.p.is_auditor):
        raise Forbidden("acesso de emergência é para admins e auditores")
    if len((reason or "").strip()) < 10:
        raise PlatformError("explique o motivo (fica na auditoria e vai para o dono do projeto)")
    u = db.get(User, acc.p.user_id) if acc.p.user_id else None
    email = (u.email if u else acc.p.name).lower()
    shared.put(f"breakglass:{p.id}:{email}", {"reason": reason[:500]}, 3600)
    audit(db, acc.p.name, "governance.break_glass", slug, reason[:300])
    owner = db.scalar(select(User).where(User.email == (p.created_by or "").lower()))
    if owner is not None:
        from . import notify
        notify._bg(_tell_owner, owner.id, p.name, acc.p.name, reason[:300])
    return {"project": slug, "until": iso(now() + timedelta(hours=1))}


def _tell_owner(db, user_id: int, project: str, who: str, reason: str):
    from . import notify
    u = db.get(User, user_id)
    cfg = notify.org_cfg(db)
    via = notify.route(cfg, u) if u else None
    if via:
        notify._send_text(db, cfg, u, via, f"Acesso de emergência ao projeto {project}",
                          [f"{who} abriu o projeto confidencial por uma hora.", f"Motivo: {reason}"],
                          f"{config.PUBLIC_BASE_URL}/app/#/audit")
