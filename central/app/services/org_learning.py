"""Aprendizado da organização: o trabalho que deu certo vira conhecimento reaproveitável.

Depois que uma tarefa termina bem (nota do juiz ≥ 4, ou sem nota e sem revisão recusada), a plataforma propõe **um**
artefato a partir dela:
- **artigo** para a base de conhecimento, quando a tarefa procurou algo que a base não respondia (uma lacuna) e
  encontrou a resposta — a pergunta e a resposta, reescritas como artigo (pelo LLM do funcionário, se houver);
- **procedimento** do navegador, quando a tarefa percorreu um caminho num sistema sem API.

A proposta vai para quem responde pelo Digital employee como uma decisão: aprova, ajusta o texto ou recusa. Aprovado,
o artigo entra na fonte "Aprendizado — <time>" (e a lacuna se fecha), o procedimento vira versão; cada vez que uma
tarefa usa o artigo numa busca ou roda o procedimento, conta como reaproveitamento.
"""
import json
from datetime import timedelta

from sqlalchemy import select

from .. import config
from ..models import (
    Agent,
    Employee,
    EmployeeTask,
    HumanRequest,
    KnowledgeGap,
    KnowledgeSource,
    LearnedArtifact,
    Procedure,
    TaskEvent,
    Team,
    now,
)
from . import employee_tasks as tasksmod
from .access import Access, Forbidden
from .common import PlatformError, audit, iso, slugify
from .decision_kinds import close, handler

MIN_ANSWER = 80  # resposta curta demais não vira artigo
SKIP_SOURCES = ("regression", "learning", "watcher")


def artifact_dict(db, a: LearnedArtifact) -> dict:
    t = db.get(Team, a.team_id) if a.team_id else None
    e = db.get(Employee, a.employee_id) if a.employee_id else None
    ag = db.get(Agent, e.agent_id) if e else None
    return {"id": a.id, "kind": a.kind, "title": a.title, "content": a.content, "status": a.status, "team": t.slug if t else None,
            "employee": ag.slug if ag else None, "employee_name": ag.name if ag else None, "task": a.task_id,
            "request": a.request_id, "target": a.target, "uses": a.uses, "decided_by": a.decided_by,
            "created_at": iso(a.created_at), "decided_at": iso(a.decided_at) if a.decided_at else None}


def list_artifacts(db, acc: Access, status: str = "") -> list[dict]:
    q = select(LearnedArtifact).order_by(LearnedArtifact.id.desc()).limit(300)
    if status:
        q = q.where(LearnedArtifact.status == status)
    return [artifact_dict(db, a) for a in db.scalars(q)
            if acc.p.is_admin or acc.p.is_auditor or a.team_id is None or a.team_id in acc.teams]


def summary(db, acc: Access) -> dict:
    rows = [a for a in list_artifacts(db, acc)]
    ok = [a for a in rows if a["status"] == "approved"]
    return {"approved": len(ok), "proposed": sum(a["status"] == "proposed" for a in rows),
            "uses": sum(a["uses"] for a in ok), "reused": sum(1 for a in ok if a["uses"])}


# ------------------------------------------------------------------ o que propor
def _good(db, t: EmployeeTask) -> bool:
    """Terminou bem: nota ≥ 4 do juiz (quando houve nota) e nenhuma revisão recusada."""
    if t.status != "done" or t.probation or (t.source or "") in SKIP_SOURCES or len((t.result or "").strip()) < 20:
        return False
    q = db.scalar(select(TaskEvent).where(TaskEvent.task_id == t.id, TaskEvent.kind == "quality").order_by(TaskEvent.id.desc()))
    if q is not None and float((q.payload or {}).get("score") or 0) < 4:
        return False
    return not db.scalar(select(HumanRequest.id).where(HumanRequest.task_id == t.id, HumanRequest.kind == "review",
                                                       HumanRequest.decision == "reject"))


def _browser_steps(db, t: EmployeeTask) -> tuple[list[dict], list[str]]:
    from .browser import STEP_ACTIONS
    steps, doms = [], set()
    for ev in db.scalars(select(TaskEvent).where(TaskEvent.task_id == t.id, TaskEvent.kind == "browser").order_by(TaskEvent.id)):
        p = ev.payload or {}
        doms |= set(p.get("domains") or [])
        if p.get("action") == "procedure":
            return [], []  # já usou um procedimento: nada novo para aprender
        if p.get("action") in STEP_ACTIONS:
            steps.append({"action": p["action"], "target": p.get("target", ""),
                          "value": "" if p.get("value") == "[segredo]" else p.get("value", "")})
    return steps, sorted(doms)


def _draft_article(db, e: Employee, question: str, answer: str) -> dict:
    """Pergunta + resposta → artigo curto. Com LLM, reescrito; sem, a resposta como veio."""
    from . import llm
    tgt = llm.target_for_employee(db, e)
    if tgt is not None:
        try:
            out = llm.complete_json(tgt, "Você escreve artigos curtos para a base de conhecimento de uma empresa. A partir de "
                                         "uma pergunta e da resposta que um analista encontrou, escreva um artigo que "
                                         "responda essa pergunta para os próximos: título claro e texto objetivo, sem "
                                         "dados pessoais nem números de documentos de clientes. Responda só com JSON: "
                                         '{"title": "...", "text": "..."}',
                                    f"PERGUNTA: {question}\n\nRESPOSTA ENCONTRADA:\n{answer[:6000]}")
            if isinstance(out, dict) and str(out.get("text") or "").strip():
                return {"title": str(out.get("title") or question)[:300], "text": str(out["text"])[:20000]}
        except Exception:  # noqa: BLE001 — o rascunho é ajuda; sem ele, vale a resposta como veio
            pass
    return {"title": question[:300], "text": answer[:20000]}


def _propose(db, t: EmployeeTask, e: Employee, kind: str, title: str, content: dict, question: str):
    a = LearnedArtifact(kind=kind, team_id=db.get(Agent, e.agent_id).team_id, employee_id=e.id, task_id=t.id,
                        title=title[:300], content=content, created_at=now())
    db.add(a)
    db.flush()
    r = HumanRequest(employee_id=e.id, task_id=t.id, kind="learning", action_type="write_internal", mode="approve",
                     question=question, action_payload={"artifact": a.id, "kind": kind, "title": a.title, "content": content},
                     assigned_user_id=e.manager_user_id, fallback_user_id=(e.backup_user_ids or [None])[0], approvals=[],
                     created_at=now())
    db.add(r)
    db.flush()
    a.request_id = r.id
    tasksmod.event(db, t, "learning", {"artifact": a.id, "kind": kind})
    db.commit()
    tasksmod._notify_request(db, e, r)
    return a


def consider(db, t: EmployeeTask) -> LearnedArtifact | None:
    """Uma tarefa terminada: propõe no máximo um artefato (e marca que já olhou)."""
    tasksmod.event(db, t, "learning_checked", {})
    db.commit()
    if not _good(db, t):
        return None
    e = db.get(Employee, t.employee_id)
    if e is None or (e.learning_mode or "on") == "off":
        return None
    gap = db.scalar(select(KnowledgeGap).where(KnowledgeGap.task_id == t.id, KnowledgeGap.resolved.is_(False)))
    if gap is not None and len((t.result or "").strip()) >= MIN_ANSWER:
        art = _draft_article(db, e, gap.query or t.title, t.result or "")
        return _propose(db, t, e, "article", art["title"], {"question": gap.query, "text": art["text"], "gap": gap.id},
                        f"A base de conhecimento não respondia \"{gap.query[:200]}\" e a tarefa #{t.id} encontrou a "
                        "resposta. Virar um artigo para as próximas?")
    steps, doms = _browser_steps(db, t)
    if len(steps) >= 2 and any(s["action"] in ("click", "fill") for s in steps):
        return _propose(db, t, e, "procedure", t.title, {"steps": steps, "domains": doms},
                        f"A tarefa #{t.id} percorreu {len(steps)} passos em {', '.join(doms) or 'um sistema'} e deu certo. "
                        "Salvar o caminho como procedimento?")
    return None


def sweep(db, at=None, limit: int = 20) -> int:
    """Olha as tarefas terminadas há pelo menos 2 minutos (o juiz já deu nota) e ainda não olhadas."""
    at = at or now()
    since = at - timedelta(days=7)
    checked = select(TaskEvent.task_id).where(TaskEvent.kind == "learning_checked")
    n = 0
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.status == "done", EmployeeTask.finished_at >= since,
                                                   EmployeeTask.finished_at <= at - timedelta(minutes=config.LEARN_AFTER_MIN),
                                                   EmployeeTask.id.not_in(checked))
                        .order_by(EmployeeTask.id).limit(limit)).all():
        n += consider(db, t) is not None
    return n


# ------------------------------------------------------------------ decisão
def _learned_source(db, team_id: int | None) -> KnowledgeSource:
    t = db.get(Team, team_id) if team_id else None
    name = f"aprendizado_{slugify(t.slug if t else 'empresa').replace('-', '_')}"[:80]
    s = db.scalar(select(KnowledgeSource).where(KnowledgeSource.name == name))
    if s is None:
        s = KnowledgeSource(name=name, title=f"Aprendizado — {t.name if t else 'empresa'}", kind="upload",
                            acl_mode="team" if t else "org", team_ids=[t.id] if t else [], config={}, review_days=365,
                            classification="internal", status="active", created_by="aprendizado", created_at=now())
        db.add(s)
        db.flush()
    return s


@handler("learning")
def decide_learning(db, acc, r: HumanRequest, e: Employee, task, decision: str, edit: dict, reason: str, who: str):
    from . import knowledge
    from .employee_tasks import request_dict
    a = db.get(LearnedArtifact, (r.action_payload or {}).get("artifact"))
    if a is None:
        raise PlatformError("artefato não encontrado")
    if decision not in ("approve", "approve_edited", "reject"):
        raise PlatformError("aprendizado: approve | approve_edited (com edit: {title, text}) | reject")
    if decision == "reject":
        a.status = "rejected"
    else:
        content = dict(a.content or {})
        if decision == "approve_edited":
            a.title = str((edit or {}).get("title") or a.title)[:300]
            if a.kind == "article" and str((edit or {}).get("text") or "").strip():
                content["text"] = str(edit["text"])[:20000]
            if a.kind == "procedure" and isinstance((edit or {}).get("steps"), list):
                content["steps"] = edit["steps"]
        a.content = content
        if a.kind == "article":
            s = _learned_source(db, a.team_id)
            d = knowledge.upsert(db, s, f"learned:{a.id}", a.title, content["text"], "")
            a.target = f"kb:{d.id}"
            if content.get("gap"):
                g = db.get(KnowledgeGap, content["gap"])
                if g is not None:
                    g.resolved = True
        else:
            from .browser import _clean_steps
            name = f"aprendido_{a.task_id or a.id}"
            p = db.scalar(select(Procedure).where(Procedure.name == name))
            if p is None:
                p = Procedure(name=name, title=a.title, team_id=a.team_id, domains=content.get("domains") or [],
                              steps=_clean_steps(content.get("steps")), version=1, start_url="", created_by=who,
                              updated_at=now())
                db.add(p)
            a.target = f"procedure:{name}"
        a.status = "approved"
    a.decided_by, a.decided_at = who, now()
    close(r, decision, reason, who)
    db.commit()
    audit(db, who, f"learning.{a.status}", f"#{a.id}", a.title[:120])
    return request_dict(db, r, e)


# ------------------------------------------------------------------ reaproveitamento
def used_docs(db, doc_ids: list[int]):
    if not doc_ids:
        return
    for a in db.scalars(select(LearnedArtifact).where(LearnedArtifact.status == "approved",
                                                      LearnedArtifact.target.in_([f"kb:{d}" for d in doc_ids]))):
        a.uses += 1


def used_procedure(db, name: str):
    for a in db.scalars(select(LearnedArtifact).where(LearnedArtifact.status == "approved",
                                                      LearnedArtifact.target == f"procedure:{name}")):
        a.uses += 1


def set_mode(db, acc: Access, slug: str, mode: str) -> dict:
    from .employees import find
    if mode not in ("on", "off"):
        raise PlatformError("mode: on | off")
    e, a = find(db, acc, slug, "manage")
    e.learning_mode = mode
    db.commit()
    audit(db, acc.p.name, "employee.learning_mode", a.slug, mode)
    return {"learning_mode": mode}


def artifact_for(db, acc: Access, artifact_id: int) -> dict:
    a = db.get(LearnedArtifact, artifact_id)
    if a is None or not (acc.p.is_admin or acc.p.is_auditor or a.team_id is None or a.team_id in acc.teams):
        raise Forbidden("sem acesso")
    return {**artifact_dict(db, a), "payload": json.loads(json.dumps(a.content))}
