"""Memorando de decisão: para o trabalho de julgamento ("renovar com desconto ou deixar ir?", "qual dos três
fornecedores?"), o Digital employee entrega opções, critérios com peso, notas, recomendação com confiança, riscos e
fontes — não uma resposta solta.

- **Revisor crítico** (por funcionário, `critic_mode=auto`): antes de chegar à pessoa, outro olhar (um LLM, ou as
  regras abaixo sem LLM) aponta furos — recomendação que não é a de maior nota, confiança alta sem fontes, risco não
  tratado, critério sem peso. Furo grave volta para o funcionário revisar uma vez.
- **Registro da decisão:** quem decide marca a opção escolhida; depois de N dias (30, por padrão) a plataforma pergunta
  "como foi?" (um item em *Meu trabalho*). Recomendação seguida e resultado bom alimentam a taxa de acerto do
  funcionário — qualidade medida pelo resultado real, não só pelo texto.
"""
import json
from datetime import timedelta

from sqlalchemy import select

from ..models import Agent, DecisionMemo, Employee, EmployeeTask, User, WorkItem, now
from . import employee_tasks as tasksmod
from . import llm, task_files
from .access import Access, Forbidden
from .common import PlatformError, audit, iso
from .platform_tools import Ctx, tool

CRITIC = ("Você é um revisor crítico. Leia o memorando de decisão e aponte furos: recomendação que contradiz as notas, "
          "afirmações sem fonte, riscos ignorados, critérios importantes ausentes, confiança exagerada. Responda só JSON: "
          "{\"severity\": \"low|medium|high\", \"notes\": [\"…\"]}.")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise PlatformError(f"nota inválida: {v!r}") from None


def totals(options: list[dict], criteria: list[dict], scores: dict) -> dict[str, float]:
    wsum = sum(_num(c.get("weight", 1)) for c in criteria) or 1
    return {o["name"]: round(sum(_num((scores.get(o["name"]) or {}).get(c["name"], 0)) * _num(c.get("weight", 1)) for c in criteria) / wsum, 2)
            for o in options}


def rules_critic(m: DecisionMemo) -> dict:
    """O revisor sem LLM: as checagens objetivas."""
    notes = []
    best = max(m.totals, key=m.totals.get) if m.totals else ""
    if best and m.recommendation != best and m.totals.get(best, 0) - m.totals.get(m.recommendation, 0) >= 1:
        notes.append(f"A recomendação ({m.recommendation}) não é a de maior nota ponderada ({best}): explique por quê ou revise.")
    if m.confidence >= 0.8 and not m.citations:
        notes.append("Confiança alta sem nenhuma fonte citada.")
    if not m.risks:
        notes.append("Nenhum risco listado para a opção recomendada.")
    rec = next((o for o in m.options if o["name"] == m.recommendation), {})
    if rec and not rec.get("cons"):
        notes.append("A opção recomendada não tem nenhum contra listado.")
    if len(m.criteria) < 2:
        notes.append("Só um critério: decisões reais costumam pesar mais de um.")
    sev = "high" if notes and notes[0].startswith("A recomendação") else ("medium" if len(notes) >= 2 else ("low" if notes else "none"))
    return {"severity": sev, "notes": notes, "by": "regras"}


def critic(db, agent: Agent, m: DecisionMemo) -> dict:
    tgt = llm.target(db, agent)
    if tgt is None:
        return rules_critic(m)
    got = llm.complete_json(tgt, CRITIC, json.dumps(memo_dict(m), ensure_ascii=False)[:20000]) or {}
    base = rules_critic(m)
    notes = [*base["notes"], *[str(n) for n in got.get("notes") or []]][:10]
    order = ["none", "low", "medium", "high"]
    sev = max(base["severity"], str(got.get("severity") or "low"), key=lambda x: order.index(x) if x in order else 1)
    return {"severity": sev, "notes": notes, "by": "llm"}


def memo_dict(m: DecisionMemo) -> dict:
    return {"id": m.id, "task": m.task_id, "question": m.question, "options": m.options, "criteria": m.criteria, "scores": m.scores,
            "totals": m.totals, "recommendation": m.recommendation, "confidence": m.confidence, "risks": m.risks,
            "citations": m.citations, "critic": m.critic, "revision": m.revision, "decided_option": m.decided_option,
            "decided_by": m.decided_by, "decided_note": m.decided_note, "decided_at": iso(m.decided_at) if m.decided_at else None,
            "follow_up_at": iso(m.follow_up_at) if m.follow_up_at else None, "outcome": m.outcome, "outcome_note": m.outcome_note,
            "created_at": iso(m.created_at)}


def _markdown(m: DecisionMemo) -> str:
    lines = [f"# Memorando de decisão #{m.id}", "", f"**Pergunta:** {m.question}", "",
             f"**Recomendação:** {m.recommendation} (confiança {round(m.confidence * 100)}%)", "", "## Opções"]
    for o in m.options:
        lines += [f"### {o['name']} — nota {m.totals.get(o['name'], 0)}", o.get("summary", ""),
                  *[f"- (+) {p}" for p in o.get("pros", [])], *[f"- (−) {c}" for c in o.get("cons", [])], ""]
    lines += ["## Critérios", *[f"- {c['name']} (peso {c.get('weight', 1)})" for c in m.criteria], "", "## Riscos",
              *[f"- {r}" for r in m.risks], "", "## Fontes", *[f"[{i}] {c}" for i, c in enumerate(m.citations, 1)]]
    if m.critic and m.critic.get("notes"):
        lines += ["", f"## Revisor crítico ({m.critic.get('severity')})", *[f"- {n}" for n in m.critic["notes"]]]
    return "\n".join(lines)


@tool("submit_decision_memo", "Entrega um memorando de decisão (para pedidos de escolha, priorização ou recomendação): "
      "opções com prós e contras, critérios com peso, notas de 0 a 10 por opção e critério, a recomendação, a confiança "
      "(0 a 1), os riscos e as fontes. A plataforma calcula as notas ponderadas e um revisor crítico pode pedir revisão.",
      {"properties": {"question": {"type": "string"},
                      "options": {"type": "array", "items": {"type": "object", "properties": {
                          "name": {"type": "string"}, "summary": {"type": "string"},
                          "pros": {"type": "array", "items": {"type": "string"}}, "cons": {"type": "array", "items": {"type": "string"}}},
                          "required": ["name"]}},
                      "criteria": {"type": "array", "items": {"type": "object", "properties": {
                          "name": {"type": "string"}, "weight": {"type": "number"}}, "required": ["name"]}},
                      "scores": {"type": "object", "description": "{opção: {critério: nota 0-10}}"},
                      "recommendation": {"type": "string"}, "confidence": {"type": "number"},
                      "risks": {"type": "array", "items": {"type": "string"}},
                      "citations": {"type": "array", "items": {"type": "string"}}},
       "required": ["question", "options", "criteria", "scores", "recommendation"]}, "memo")
def submit_decision_memo(ctx: Ctx, args: dict):
    options = [{"name": str(o.get("name") or "").strip()[:200], "summary": str(o.get("summary") or "")[:2000],
                "pros": [str(x)[:500] for x in o.get("pros") or []], "cons": [str(x)[:500] for x in o.get("cons") or []]}
               for o in args.get("options") or [] if str(o.get("name") or "").strip()]
    if len(options) < 2:
        raise PlatformError("um memorando compara pelo menos duas opções")
    criteria = [{"name": str(c.get("name") or "").strip()[:200], "weight": _num(c.get("weight", 1))}
                for c in args.get("criteria") or [] if str(c.get("name") or "").strip()]
    if not criteria:
        raise PlatformError("diga os critérios")
    scores = args.get("scores") or {}
    for o in options:
        for c in criteria:
            v = _num((scores.get(o["name"]) or {}).get(c["name"], 0))
            if not 0 <= v <= 10:
                raise PlatformError("notas de 0 a 10")
    rec = str(args.get("recommendation") or "")
    if rec not in [o["name"] for o in options]:
        raise PlatformError("a recomendação precisa ser uma das opções")
    prev = ctx.db.scalar(select(DecisionMemo).where(DecisionMemo.task_id == ctx.task.id).order_by(DecisionMemo.id.desc()))
    m = DecisionMemo(task_id=ctx.task.id, employee_id=ctx.e.id, question=str(args.get("question") or "")[:4000], options=options,
                     criteria=criteria, scores=scores, recommendation=rec, confidence=max(0.0, min(_num(args.get("confidence", 0.5)), 1.0)),
                     risks=[str(x)[:500] for x in args.get("risks") or []], citations=[str(x)[:500] for x in args.get("citations") or []],
                     revision=(prev.revision + 1) if prev else 0, created_at=now())
    m.totals = totals(options, criteria, scores)
    ctx.db.add(m)
    ctx.db.flush()
    if (ctx.e.critic_mode or "off") == "auto":
        m.critic = critic(ctx.db, ctx.agent, m)
    task_files.store(ctx.db, ctx.task, f"memorando-{m.id}.md", _markdown(m).encode(), "output", f"agent:{ctx.agent.slug}",
                     "text/markdown", "memorando de decisão")
    tasksmod.event(ctx.db, ctx.task, "memo", {"id": m.id, "recommendation": rec, "critic": (m.critic or {}).get("severity")})
    ctx.db.commit()
    if m.critic and m.critic["severity"] == "high" and m.revision == 0:
        return ("REVISOR CRÍTICO: furos graves no memorando — " + " ".join(m.critic["notes"]) +
                " Revise (notas, recomendação ou justificativa) e mande o memorando de novo com submit_decision_memo.")
    return {"memo": m.id, "totals": m.totals, "critic": m.critic,
            "instruction": "Responda com a recomendação, a nota ponderada de cada opção, os riscos e as fontes."}


# ------------------------------------------------------------------ decisão e resultado
def _memo(db, acc: Access, mid: int) -> DecisionMemo:
    m = db.get(DecisionMemo, mid)
    if m is None:
        raise PlatformError("memorando não encontrado")
    t = db.get(EmployeeTask, m.task_id) if m.task_id else None
    e = db.get(Employee, t.employee_id) if t else None
    from .employees import can_read_task
    if t is None or e is None or not can_read_task(acc, t, e):
        raise PlatformError("memorando não encontrado")
    return m


def get(db, acc: Access, mid: int) -> dict:
    return memo_dict(_memo(db, acc, mid))


def list_memos(db, acc: Access, pending: bool = False) -> list[dict]:
    out = []
    for m in db.scalars(select(DecisionMemo).order_by(DecisionMemo.id.desc()).limit(300)):
        try:
            _memo(db, acc, m.id)
        except PlatformError:
            continue
        if pending and m.outcome:
            continue
        out.append(memo_dict(m))
    return out


def decide(db, acc: Access, mid: int, option: str, note: str = "", follow_up_days: int = 30) -> dict:
    m = _memo(db, acc, mid)
    t = db.get(EmployeeTask, m.task_id)
    e = db.get(Employee, t.employee_id)
    if not (acc.p.is_admin or acc.p.user_id in (t.requester_user_id, e.manager_user_id, e.owner_user_id)):
        raise Forbidden("decide quem pediu (ou quem responde pelo funcionário)")
    if option not in [o["name"] for o in m.options] and option != "outra":
        raise PlatformError("a opção decidida precisa ser uma das do memorando (ou 'outra')")
    m.decided_option, m.decided_by, m.decided_note, m.decided_at = option, acc.p.name, (note or "")[:4000], now()
    m.follow_up_at = now() + timedelta(days=max(1, min(int(follow_up_days or 30), 365)))
    db.commit()
    audit(db, acc.p.name, "memo.decide", f"#{m.id}", option)
    return memo_dict(m)


def outcome(db, acc: Access, mid: int, result: str, note: str = "") -> dict:
    m = _memo(db, acc, mid)
    if result not in ("good", "mixed", "bad"):
        raise PlatformError("resultado: good | mixed | bad")
    if not m.decided_option:
        raise PlatformError("registre antes o que foi decidido")
    m.outcome, m.outcome_note, m.outcome_at = result, (note or "")[:4000], now()
    if m.follow_up_item_id:
        i = db.get(WorkItem, m.follow_up_item_id)
        if i is not None and i.status not in ("done", "cancelled"):
            i.status, i.done_at, i.result = "done", now(), f"{result}: {note}"[:2000]
    db.commit()
    audit(db, acc.p.name, "memo.outcome", f"#{m.id}", result)
    return memo_dict(m)


def sweep(db, at=None) -> int:
    """Chegou a hora de perguntar "como foi?": um item em Meu trabalho para quem decidiu."""
    at, n = at or now(), 0
    for m in db.scalars(select(DecisionMemo).where(DecisionMemo.follow_up_at.is_not(None), DecisionMemo.follow_up_at <= at,
                                                   DecisionMemo.outcome == "", DecisionMemo.follow_up_item_id.is_(None))).all():
        u = db.scalar(select(User).where(User.email == (m.decided_by or "").lower()))
        t = db.get(EmployeeTask, m.task_id) if m.task_id else None
        e = db.get(Employee, t.employee_id) if t else None
        a = db.get(Agent, e.agent_id) if e else None
        i = WorkItem(team_id=a.team_id if a else None, title=f"Como foi a decisão: {m.question[:200]}",
                     body=f"Você decidiu \"{m.decided_option}\" (a recomendação era \"{m.recommendation}\"). Registre o resultado "
                          f"em #/memos/{m.id}.", status="in_progress" if u else "open", assignee_user_id=u.id if u else None,
                     source="memo", source_ref=f"memo:{m.id}", created_by="plataforma", created_at=now())
        db.add(i)
        db.flush()
        m.follow_up_item_id = i.id
        n += 1
    db.commit()
    return n


def record(db, e: Employee) -> dict:
    """A taxa de acerto do funcionário pelo resultado real das decisões."""
    rows = list(db.scalars(select(DecisionMemo).where(DecisionMemo.employee_id == e.id, DecisionMemo.outcome != "")))
    followed = [m for m in rows if m.decided_option == m.recommendation]
    good = [m for m in followed if m.outcome == "good"]
    return {"memos": db.query(DecisionMemo).filter(DecisionMemo.employee_id == e.id).count(), "with_outcome": len(rows),
            "followed": len(followed), "good_when_followed": len(good),
            "accuracy": round(len(good) / len(followed), 2) if followed else None}
