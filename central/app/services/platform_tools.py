"""Ferramentas da plataforma: o que o Digital employee faz com a própria plataforma (analisar os arquivos da tarefa,
extrair campos de documentos, usar uma calculadora certificada, buscar na base de conhecimento, escrever para fora,
marcar reuniões…).

O runtime pede a lista (`/internal/platform-tools`) e registra cada uma como `platform:<nome>`; a chamada passa pelo
gate (kind="platform") e roda AQUI, na central — o agente nunca vê credenciais, e cada efeito que muda algo fora passa
pela alçada (`side_effect`), com a mesma regra, decisão e auditoria das outras ferramentas.

Cada módulo registra as suas com `@tool(...)`; o grupo (`group`) é o que o gestor liga ou desliga no cargo.
"""
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from ..models import Agent, Employee, EmployeeTask
from .common import PlatformError

GROUPS = {
    "analysis": "Análise de dados e entregáveis (Data Studio)",
    "extraction": "Extração de campos de documentos",
    "calculators": "Calculadoras certificadas",
    "queues": "Filas e tarefas para pessoas",
    "correspondence": "Conversas com gente de fora (e-mail, WhatsApp)",
    "calendar": "Agenda e reuniões",
    "knowledge": "Base de conhecimento",
    "browser": "Navegador governado",
    "desktop": "Desktop governado",
    "org": "Propor mudanças na organização (rotina, instrução, colega, rota da fila)",
    "documents": "Propor alterações em documentos",
    "memo": "Memorando de decisão",
}


@dataclass
class Ctx:
    db: object
    e: Employee
    agent: Agent
    task: EmployeeTask
    rationale: str = ""


@dataclass
class PlatformTool:
    name: str
    description: str
    parameters: dict
    handler: Callable
    group: str
    available: Callable = field(default=lambda db, e: True)


REGISTRY: dict[str, PlatformTool] = {}
_LOADED = False


def tool(name: str, description: str, parameters: dict, group: str, available: Callable | None = None):
    def deco(fn):
        REGISTRY[name] = PlatformTool(name, description, {"type": "object", **parameters}, fn, group,
                                      available or (lambda db, e: True))
        return fn
    return deco


def _load():
    """Os módulos que registram ferramentas (importados uma vez, sob demanda, para não criar ciclos)."""
    global _LOADED
    if _LOADED:
        return
    from . import analysis, calculators, extraction  # noqa: F401
    for mod in ("work_queues", "correspondence", "meetings", "knowledge", "browser", "desktop", "doc_changes", "decision_memos", "org_learning", "self_org"):
        try:
            __import__(f"app.services.{mod}")
        except ModuleNotFoundError as ex:
            if ex.name != f"app.services.{mod}":
                raise
    _LOADED = True


def disabled(e: Employee) -> set[str]:
    return set(e.disabled_tools or [])


def tools_for(db, e: Employee) -> list[dict]:
    _load()
    off = disabled(e)
    return [{"name": t.name, "description": t.description, "parameters": t.parameters, "group": t.group}
            for t in REGISTRY.values() if t.group not in off and t.available(db, e)]


def groups_view(db, e: Employee) -> list[dict]:
    _load()
    off = disabled(e)
    return [{"group": g, "title": title, "enabled": g not in off,
             "tools": [t.name for t in REGISTRY.values() if t.group == g],
             "available": any(t.available(db, e) for t in REGISTRY.values() if t.group == g)}
            for g, title in GROUPS.items() if any(t.group == g for t in REGISTRY.values())]


def side_effect(ctx: Ctx, tool_name: str, ref: str, args: dict) -> dict | None:
    """Uma ação que muda algo fora: passa pela alçada. None = pode executar; senão, a resposta do gate (waiting,
    denied, simulated) que volta ao runtime como está."""
    from . import employee_tasks as tasksmod
    g = tasksmod._gate(ctx.db, ctx.agent, ctx.task.id, tool_name, ref, args, ctx.rationale, "tool")
    return None if g.get("status") == "allowed" else g


def run(db, agent: Agent, task_id: int | None, name: str, args: dict, rationale: str = "") -> dict:
    from . import employee_tasks as tasksmod
    _load()
    e = db.scalar(select(Employee).where(Employee.agent_id == agent.id))
    task = db.get(EmployeeTask, task_id) if task_id else None
    t = REGISTRY.get(name)
    if e is None or t is None:
        return {"status": "denied", "message": f"ferramenta da plataforma '{name}' inexistente"}
    if task is None or task.employee_id != e.id:
        return {"status": "denied", "message": f"'{name}' só funciona dentro de uma tarefa deste Digital employee"}
    if e.status in ("paused", "offboarded"):
        return {"status": "denied", "message": f"Digital employee {e.status}: nenhuma ação é executada."}
    if t.group in disabled(e) or not t.available(db, e):
        return {"status": "denied", "message": f"'{name}' está desligada para este Digital employee"}
    try:
        out = t.handler(Ctx(db, e, agent, task, rationale), args or {})
    except PlatformError as ex:
        tasksmod.event(db, task, "tool", {"tool": name, "platform": True, "error": str(ex)[:300]})
        db.commit()
        return {"status": "allowed", "result": f"erro: {ex}"}
    if isinstance(out, dict) and out.get("status") in ("waiting", "denied", "simulated"):
        return out  # a alçada segurou um efeito
    images = out.pop("_images", None) if isinstance(out, dict) else None  # capturas: vão ao modelo, não à linha do tempo
    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
    tasksmod.event(db, task, "tool", {"tool": name, "platform": True, "result": text[:300]})
    db.commit()
    return {"status": "allowed", "result": text, **({"images": images} if images else {})}
