"""Resultado "sem achados" de um laço (rotina, gatilho, fila): só o que importa chega a uma pessoa.

O agente encerra com a ferramenta da plataforma finish_task(findings, summary), ou o worker externo manda findings no
resultado. A plataforma garante a coerência, não o prompt:

- **sem achados** (findings=false) só vale se a tarefa não mudou nada: nenhuma ação que muda algo executada, nada
  proposto no modo sombra, nenhuma pessoa envolvida (pedido de decisão, pergunta) e nada repassado. A tarefa conclui em
  silêncio: fica fora da caixa de entrada de quem pediu, recolhida nas listas, e conta como "N execuções, M achados"
  nos relatórios. Se algo mudou depois da declaração, ela perde o efeito na conclusão.
- **achado** (findings=true) exige um resumo curto (até SUMMARY_MAX caracteres). A ação proposta passa pela alçada
  como sempre.

Execuções sem achados não contam como "tarefas concluídas" no plano de carreira (uma rotina de hora em hora que nunca
acha nada não promove ninguém), e entram na amostra do juiz de qualidade, que vê o que foi conferido (o silêncio
também é auditado).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EmployeeTask, HumanRequest, TaskEvent

SUMMARY_MAX = 400
LOOP_SOURCES = ("routine", "webhook", "plugin", "watcher")  # tarefas de laço: o prompt orienta o uso de finish_task
CHANGES = ("auto", "notify")  # modos em que a ação foi executada na hora

TXT = {
    "pt": {
        "none_ok": "Registrado: sem achados. Encerre agora com uma resposta curta, sem chamar outras ferramentas.",
        "found_ok": ("Registrado: achado. Se houver uma ação a propor, chame a ferramenta dela agora (ela passa pela alçada); "
                     "senão, encerre com o resumo."),
        "changed": ("Esta tarefa já {what}: isso é um achado, não 'sem achados'. Chame finish_task(findings=true, summary=…) "
                    "com um resumo de até {n} caracteres."),
        "need_summary": "Um achado precisa de um resumo curto (até {n} caracteres): o que foi encontrado e a correção proposta.",
        "too_long": "Resumo longo demais ({got} caracteres): use até {n}, em uma ou duas frases.",
        "label": "Sem achados.",
        "acted": "executou uma ação que muda algo", "shadow": "propôs uma ação (modo sombra)",
        "person": "envolveu uma pessoa (decisão ou pergunta)", "handoff": "repassou trabalho a um colega",
    },
    "en": {
        "none_ok": "Recorded: no findings. Finish now with a short answer, without calling other tools.",
        "found_ok": ("Recorded: finding. If there is an action to propose, call its tool now (it goes through the authority); "
                     "otherwise, finish with the summary."),
        "changed": ("This task already {what}: that is a finding, not 'no findings'. Call finish_task(findings=true, summary=…) "
                    "with a summary of up to {n} characters."),
        "need_summary": "A finding needs a short summary (up to {n} characters): what was found and the proposed fix.",
        "too_long": "Summary too long ({got} characters): use up to {n}, in one or two sentences.",
        "label": "No findings.",
        "acted": "ran an action that changes something", "shadow": "proposed an action (shadow mode)",
        "person": "involved a person (decision or question)", "handoff": "handed work to a colleague",
    },
}


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "1", "0", "yes", "no", "sim", "não", "nao"):
        return v.strip().lower() in ("true", "1", "yes", "sim")
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def changes(db: Session, task: EmployeeTask) -> list[str]:
    """O que a tarefa já fez que não combina com "sem achados" (chaves de TXT)."""
    out = []
    for ev in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.kind.in_(("gate", "shadow")))):
        p = ev.payload or {}
        if ev.kind == "shadow":
            out.append("shadow")
        elif p.get("action_type") == "delegate" and p.get("mode") in CHANGES:
            out.append("handoff")
        elif p.get("action_type") not in (None, "read") and p.get("mode") in CHANGES:
            out.append("acted")
    if db.scalar(select(HumanRequest.id).where(HumanRequest.task_id == task.id, HumanRequest.kind.not_in(("notice", "shadow"))).limit(1)):
        out.append("person")
    if db.scalar(select(EmployeeTask.id).where(EmployeeTask.parent_task_id == task.id).limit(1)):
        out.append("handoff")
    return list(dict.fromkeys(out))


def declare(db: Session, task: EmployeeTask, findings, summary: str, lang: str = "pt") -> dict:
    """A declaração do agente (finish_task) ou do worker externo. Devolve {status, result|message}."""
    from .employee_tasks import event
    t = TXT["en" if lang == "en" else "pt"]
    found = _bool(findings)
    summary = str(summary or "").strip()
    if found is None:
        return {"status": "denied", "message": "findings: true | false"}
    if not found:
        ch = changes(db, task)
        if ch:
            return {"status": "denied", "message": t["changed"].format(what="; ".join(t[c] for c in ch), n=SUMMARY_MAX)}
        task.finding, task.finding_summary = False, summary[:SUMMARY_MAX]
        event(db, task, "finding", {"findings": False})
        db.commit()
        return {"status": "allowed", "result": t["none_ok"]}
    if not summary:
        return {"status": "denied", "message": t["need_summary"].format(n=SUMMARY_MAX)}
    if len(summary) > SUMMARY_MAX:
        return {"status": "denied", "message": t["too_long"].format(got=len(summary), n=SUMMARY_MAX)}
    task.finding, task.finding_summary = True, summary
    event(db, task, "finding", {"findings": True, "summary": summary})
    db.commit()
    return {"status": "allowed", "result": t["found_ok"]}


def on_finish(db: Session, task: EmployeeTask, result: str, lang: str = "pt") -> str:
    """Na conclusão: "sem achados" só se mantém se nada mudou depois da declaração; o resultado vira o rótulo curto."""
    if task.finding is not False:
        return result
    from .employee_tasks import event
    ch = changes(db, task)
    if ch:  # algo mudou depois da declaração: vira uma tarefa comum, que aparece para as pessoas
        task.finding = None
        event(db, task, "finding", {"findings": None, "void": ch})
        return result
    t = TXT["en" if lang == "en" else "pt"]
    return task.finding_summary or t["label"]


def silent(task: EmployeeTask) -> bool:
    return task.finding is False and task.status == "done"
