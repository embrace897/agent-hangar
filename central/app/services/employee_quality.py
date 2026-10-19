"""Qualidade do resultado: uma amostra das tarefas concluídas (Employee.quality_sample, em %) é avaliada por um LLM juiz
(nota de 1 a 5 e o porquê), como os testes dos agentes já fazem. O gestor também avalia na tela da tarefa, e a nota
dele vale mais (substitui a do juiz). A média dos últimos 30 dias entra no plano de carreira.

O juiz usa a conexão do próprio funcionário (spec.judge.connection, ou a conexão do LLM dele). Em modo mock (sem
conexão), não há juiz: só a nota do gestor.
"""
import hashlib
import json
import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import select

from .. import crypto
from ..models import Agent, Employee, EmployeeTask, now
from . import employee_tasks as tasksmod
from .access import Access, Forbidden
from .common import PlatformError, audit, iso, spec_of

log = logging.getLogger("hangar.quality")
MIN_RATED = 5  # tarefas avaliadas (30 dias) para a média contar na carreira
SYSTEM = {
    "pt": ("Você avalia o resultado de uma tarefa feita por um funcionário digital (um agente de IA com um cargo). "
           "Critérios: atende ao pedido; está correto e completo; é claro e útil para quem pediu; fica dentro do cargo. "
           "Nota de 1 (inútil ou errado) a 5 (excelente, pronto para usar). Seja rigoroso. Responda SOMENTE com um JSON: "
           "{\"score\": 1-5, \"reason\": \"<uma ou duas frases>\"}."),
    "en": ("You grade the result of a task done by a digital employee (an AI agent with a job). Criteria: it does what "
           "was asked; it is correct and complete; it is clear and useful to the requester; it stays within the job. "
           "Score from 1 (useless or wrong) to 5 (excellent, ready to use). Be strict. Reply ONLY with JSON: "
           "{\"score\": 1-5, \"reason\": \"<one or two sentences>\"}."),
}


def sampled(task: EmployeeTask, pct: int) -> bool:
    """Amostra determinística (pelo id): a mesma tarefa sempre cai do mesmo lado."""
    if pct <= 0:
        return False
    return int(hashlib.sha256(f"quality:{task.id}".encode()).hexdigest()[:8], 16) % 100 < min(pct, 100)


def _target(db, spec: dict):
    from .catalog import get_connection
    llm = spec.get("llm") or {}
    name = ((spec.get("judge") or {}).get("connection") or (llm.get("prod") or {}).get("connection")
            or llm.get("connection") or (llm.get("stage") or {}).get("connection"))
    if not name:
        return None
    conn = get_connection(db, name)
    if conn.protocol != "openai":
        return None
    return conn.base_url, crypto.decrypt(conn.api_key), (spec.get("judge") or {}).get("model") or conn.model_name


def _silence_note(db, task: EmployeeTask, lang: str) -> str:
    """Laço "sem achados": o juiz vê o que foi conferido e avalia se bastava para concluir que não havia nada (o silêncio
    também é auditado; services/task_findings.py)."""
    if task.finding is not False:
        return ""
    from ..models import TaskEvent
    tools = [str((ev.payload or {}).get("tool") or "") for ev in db.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.kind == "gate").order_by(TaskEvent.id))]
    checked = ", ".join(dict.fromkeys(t for t in tools if t)) or ("nada" if lang == "pt" else "nothing")
    if lang == "pt":
        return (f"\n\nO agente concluiu SEM ACHADOS. Ferramentas que ele usou para conferir: {checked}. Avalie se essa "
                "verificação bastava para concluir que não havia nada a reportar (nota baixa se ele não conferiu o que o pedido exige).")
    return (f"\n\nThe agent finished with NO FINDINGS. Tools it used to check: {checked}. Judge whether that check was enough "
            "to conclude there was nothing to report (low score if it did not check what the request requires).")


def JUDGE(db, e: Employee, a: Agent, task: EmployeeTask) -> tuple[int, str] | None:  # noqa: N802  (os testes trocam)
    target = _target(db, spec_of(a))
    if target is None:
        return None
    base, key, model = target
    lang = tasksmod.lang_of(e)
    head = "Cargo" if lang == "pt" else "Job"
    user = (f"{head}: {e.title}\n{e.mission[:1500]}\n\n"
            f"{'Pedido' if lang == 'pt' else 'Request'}: {task.title}\n{task.body[:6000]}\n\n"
            + (f"{'Resultado esperado' if lang == 'pt' else 'Expected result'}: {task.expected[:3000]}\n\n" if task.expected else "")
            + f"{'Resultado entregue' if lang == 'pt' else 'Delivered result'}:\n{task.result[:12000]}"
            + _silence_note(db, task, lang))
    r = httpx.post(f"{base}/chat/completions", timeout=120, headers={"Authorization": f"Bearer {key}"} if key else {},
                   json={"model": model, "temperature": 0,
                         "messages": [{"role": "system", "content": SYSTEM[lang]}, {"role": "user", "content": user}]})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise PlatformError(f"o juiz não devolveu JSON: {text[:150]}")
    v = json.loads(m.group(0))
    return max(1, min(int(v.get("score") or 0), 5)), str(v.get("reason", ""))[:600]


def task_finished(db, task: EmployeeTask):
    """Chamado ao concluir: sorteia (pela amostra) e manda avaliar em segundo plano."""
    if task.status != "done" or task.probation or task.quality is not None or not (task.result or "").strip():
        return
    e = db.get(Employee, task.employee_id)
    if e is None or not sampled(task, e.quality_sample or 0):
        return
    from . import notify
    notify._bg(_judge_bg, task.id)


def _judge_bg(db, task_id: int):
    task = db.get(EmployeeTask, task_id)
    e = db.get(Employee, task.employee_id) if task else None
    a = db.get(Agent, e.agent_id) if e else None
    if not task or not a or task.quality is not None:
        return
    try:
        out = JUDGE(db, e, a, task)
    except Exception as ex:  # noqa: BLE001 — o juiz fora do ar não atrapalha a tarefa
        log.warning("juiz de qualidade da tarefa #%s: %s", task_id, ex)
        return
    if out is None:
        return
    db.refresh(task)
    if task.quality is not None:  # o gestor avaliou enquanto o juiz pensava: vale a dele
        return
    task.quality, task.quality_note, task.quality_by = out[0], out[1], "judge"
    tasksmod.event(db, task, "quality", {"score": out[0], "by": "judge"})
    db.commit()


def rate(db, acc: Access, task_id: int, score: int, note: str = "") -> dict:
    """A nota do gestor (1 a 5): substitui a do juiz e vira exemplo do que é bom ou ruim para quem acompanha."""
    from .employees import find, task_dict
    task = db.get(EmployeeTask, task_id)
    if task is None:
        raise PlatformError("tarefa não encontrada")
    e = db.get(Employee, task.employee_id)
    a = db.get(Agent, e.agent_id)
    try:
        find(db, acc, a.slug, "manage")
    except Forbidden:
        raise Forbidden("avalia o resultado o gestor, o dono ou um mantenedor do time") from None
    if task.status != "done":
        raise PlatformError("só tarefas concluídas recebem nota")
    if not 1 <= int(score) <= 5:
        raise PlatformError("nota de 1 a 5")
    task.quality, task.quality_note, task.quality_by = int(score), (note or "").strip()[:600], acc.p.name
    tasksmod.event(db, task, "quality", {"score": int(score), "by": acc.p.name})
    db.commit()
    audit(db, acc.p.name, "employee.task.quality", a.slug, f"#{task.id} nota {score}")
    return task_dict(db, task, True)


def stats(db, e: Employee, days: int = 30) -> dict:
    since = now() - timedelta(days=days)
    rows = list(db.scalars(select(EmployeeTask).where(EmployeeTask.employee_id == e.id, EmployeeTask.status == "done",
                                                      EmployeeTask.finished_at >= since, EmployeeTask.quality.is_not(None))))
    avg = round(sum(t.quality for t in rows) / len(rows), 2) if rows else None
    low = sorted((t for t in rows if t.quality <= 2), key=lambda t: t.id, reverse=True)[:5]
    return {"rated": len(rows), "avg": avg, "by_manager": sum(t.quality_by != "judge" for t in rows),
            "sample": e.quality_sample or 0,
            "low": [{"id": t.id, "title": t.title, "score": t.quality, "note": t.quality_note,
                     "at": iso(tasksmod._aware(t.finished_at))} for t in low]}
