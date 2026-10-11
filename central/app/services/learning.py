"""Aprender junto: o modo par (o Digital employee orienta, revisa e explica — quem faz é a pessoa), o pedido de revisão
do trabalho de um analista contra o critério, e trilhas de perguntas tiradas dos playbooks e procedimentos.

- **Modo par** (`mode=copilot` na tarefa): o funcionário só lê e consulta; qualquer ação que mude algo é recusada pela
  alçada com a orientação de descrever para a pessoa o que fazer.
- **Revisão do meu trabalho:** num item de *Meu trabalho*, "pedir revisão" cria uma tarefa em modo par com o pedido,
  o resultado e os arquivos do item; o funcionário devolve o checklist do que confere e do que falta.
- **Trilhas:** perguntas de múltipla escolha geradas do playbook (quem faz cada etapa, o que vem antes, a evidência,
  o prazo) ou do procedimento (a ordem dos passos, o domínio); cada pessoa responde e quem mantém o time vê as notas.
"""
import random

from sqlalchemy import func, select

from ..models import LearningAttempt, LearningTrack, Playbook, Procedure, Team, TeamMember, User, WorkItemFile, now
from . import employee_tasks as tasksmod
from . import task_files
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso

EVIDENCE = {"none": "nenhuma", "file": "um arquivo", "approval": "aprovação de outra pessoa"}


def review_request(db, acc: Access, item_id: int, employee: str) -> dict:
    """O trabalho da pessoa revisado por um Digital employee (modo par)."""
    from . import work_queues
    from .employees import find
    i = work_queues._item(db, acc, item_id)
    e, a = find(db, acc, employee, "assign")
    body = (f"Revise o trabalho da pessoa neste item contra o critério e aponte o que está certo, o que falta e o que "
            f"corrigir — em forma de checklist. Não refaça o trabalho.\n\nItem #{i.id}: {i.title}\n\nPedido:\n{i.body[:6000]}"
            + (f"\n\nO que a pessoa fez:\n{i.result[:6000]}" if i.result else "")
            + (f"\n\nO que o Digital employee tinha deixado pronto:\n{i.prepared[:3000]}" if i.prepared else ""))
    t = tasksmod.create_task(db, e, f"Revisar: {i.title}"[:300], body, "review", acc.p.name, acc.p.user_id, 2, status="draft")
    t.mode = "copilot"
    for f in db.scalars(select(WorkItemFile).where(WorkItemFile.item_id == i.id)):
        task_files.store(db, t, f.name, f.data, "input", acc.p.name, f.content_type, f"do item #{i.id}")
    t.status = "new"
    db.commit()
    audit(db, acc.p.name, "learning.review_request", a.slug, f"item #{i.id} → tarefa #{t.id}")
    return {"task": t.id}


# ------------------------------------------------------------------ trilhas
def _mc(rng, question: str, right: str, wrong: list[str], explain: str) -> dict | None:
    opts = list(dict.fromkeys([right, *[w for w in wrong if w and w != right]]))[:4]
    if len(opts) < 2:
        return None
    rng.shuffle(opts)
    return {"q": question, "options": opts, "answer": opts.index(right), "explain": explain}


def _from_playbook(p: Playbook, rng) -> list[dict]:
    steps = p.steps or []
    owners = [s["assignee"]["ref"] for s in steps]
    titles = [s["title"] for s in steps]
    qs = []
    for s in steps:
        qs.append(_mc(rng, f"Quem faz a etapa “{s['title']}”?", s["assignee"]["ref"], owners,
                      f"No playbook, “{s['title']}” é de {s['assignee']['ref']}."))
        if s.get("depends_on"):
            prev = next((x["title"] for x in steps if x["key"] == s["depends_on"][0]), "")
            qs.append(_mc(rng, f"Que etapa precisa terminar antes de “{s['title']}”?", prev, titles,
                          f"“{s['title']}” depende de “{prev}”."))
        qs.append(_mc(rng, f"Que evidência a etapa “{s['title']}” exige para ser concluída?", EVIDENCE[s.get("evidence", "none")],
                      list(EVIDENCE.values()), f"A etapa exige {EVIDENCE[s.get('evidence', 'none')]}."))
        n = s["due"]["business_days"]
        base = "o início" if s["due"]["from"] == "start" else f"“{next((x['title'] for x in steps if x['key'] == s['due']['from']), '')}”"
        qs.append(_mc(rng, f"Quantos dias úteis depois de {base} vence “{s['title']}”?", str(n),
                      [str(n + 1), str(n + 2), str(max(0, n - 1))], f"O prazo é {n} dia(s) útil(eis) depois de {base}."))
    return [q for q in qs if q]


def _from_procedure(p: Procedure, rng) -> list[dict]:
    steps = p.steps or []
    labels = [f"{s['action']} {s['target']}".strip() for s in steps]
    qs = []
    for i in range(1, len(steps)):
        qs.append(_mc(rng, f"No procedimento “{p.title}”, o que vem logo depois de “{labels[i - 1]}”?", labels[i], labels,
                      f"A ordem é: {' → '.join(labels)}."))
    if p.domains:
        qs.append(_mc(rng, f"Em que domínio o procedimento “{p.title}” trabalha?", p.domains[0],
                      [*p.domains[1:], "google.com", "exemplo.com.br"], "O navegador só abre os domínios do procedimento."))
    return [q for q in qs if q]


def _key(source: str) -> str:
    from .common import slugify
    return slugify(source.split(":", 1)[1]).replace("-", "_")


def track_dict(db, t: LearningTrack, acc: Access | None = None, with_answers: bool = False) -> dict:
    qs = t.questions if with_answers else [{"q": q["q"], "options": q["options"]} for q in t.questions]
    mine = db.scalar(select(func.max(LearningAttempt.score)).where(LearningAttempt.track_id == t.id,
                                                                   LearningAttempt.user_id == (acc.p.user_id if acc else -1)))
    return {"id": t.id, "name": t.name, "source": t.source, "team": t.team_id, "questions": qs, "count": len(t.questions),
            "my_best": mine, "created_at": iso(t.created_at)}


def create(db, acc: Access, source: str, name: str = "", team: str = "", questions: list | None = None) -> dict:
    tm = db.scalar(select(Team).where(Team.slug == team)) if team else None
    if team and tm is None:
        raise PlatformError("time não encontrado")
    if not (acc.p.is_admin or (tm and RANK.get(acc.teams.get(tm.id, ""), 0) >= RANK["developer"])):
        raise Forbidden("só quem desenvolve no time cria trilhas")
    rng = random.Random(f"{source}:{name}")
    if source.startswith("playbook:"):
        p = db.scalar(select(Playbook).where(Playbook.name == _key(source)))
        if p is None:
            raise PlatformError("playbook não encontrado")
        qs, name = _from_playbook(p, rng), name or f"Trilha: {p.title}"
    elif source.startswith("procedure:"):
        p = db.scalar(select(Procedure).where(Procedure.name == _key(source)))
        if p is None:
            raise PlatformError("procedimento não encontrado")
        qs, name = _from_procedure(p, rng), name or f"Trilha: {p.title}"
    else:
        qs = [{"q": str(q["q"]), "options": [str(o) for o in q["options"]], "answer": int(q["answer"]),
               "explain": str(q.get("explain") or "")} for q in questions or []]
        source = "manual"
    if not qs:
        raise PlatformError("não deu para tirar perguntas desta fonte")
    t = LearningTrack(name=name[:200], team_id=tm.id if tm else None, source=source, questions=qs, created_by=acc.p.name,
                      created_at=now())
    db.add(t)
    db.commit()
    return track_dict(db, t, acc)


def list_tracks(db, acc: Access) -> list[dict]:
    return [track_dict(db, t, acc) for t in db.scalars(select(LearningTrack).order_by(LearningTrack.id.desc()))
            if acc.p.is_admin or t.team_id is None or t.team_id in acc.teams]


def answer(db, acc: Access, track_id: int, answers: list[int]) -> dict:
    t = db.get(LearningTrack, track_id)
    if t is None or not acc.p.user_id:
        raise PlatformError("trilha não encontrada")
    res = []
    for i, q in enumerate(t.questions):
        got = answers[i] if i < len(answers) else -1
        res.append({"q": q["q"], "right": got == q["answer"], "answer": q["options"][q["answer"]], "explain": q["explain"]})
    score = round(sum(r["right"] for r in res) / len(res), 2) if res else 0.0
    db.add(LearningAttempt(track_id=t.id, user_id=acc.p.user_id, answers=list(answers), score=score, at=now()))
    db.commit()
    return {"score": score, "results": res}


def results(db, acc: Access, track_id: int) -> list[dict]:
    t = db.get(LearningTrack, track_id)
    if t is None:
        raise PlatformError("trilha não encontrada")
    if not (acc.p.is_admin or (t.team_id and RANK.get(acc.teams.get(t.team_id, ""), 0) >= RANK["maintainer"])):
        raise Forbidden("as notas são para quem mantém o time")
    rows = db.execute(select(LearningAttempt.user_id, func.max(LearningAttempt.score), func.count())
                      .where(LearningAttempt.track_id == t.id).group_by(LearningAttempt.user_id)).all()
    out = [{"user": (db.get(User, uid).email if db.get(User, uid) else uid), "best": best, "attempts": n} for uid, best, n in rows]
    if t.team_id:  # quem é do time e ainda não fez
        done = {r[0] for r in rows}
        for uid in db.scalars(select(TeamMember.user_id).where(TeamMember.team_id == t.team_id)):
            if uid not in done:
                u = db.get(User, uid)
                out.append({"user": u.email if u else uid, "best": None, "attempts": 0})
    return out

