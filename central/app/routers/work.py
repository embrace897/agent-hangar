"""O trabalho do dia a dia: metas, projetos, a conversa da tarefa, orçamentos, a caixa de entrada e a busca."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import services as svc
from .deps import acc, db_dep, guard

router = APIRouter(prefix="/api")


class GoalBody(BaseModel):
    title: str = ""
    team: str | None = None
    parent_id: int | None = None
    description: str = ""
    owner: str | None = None
    status: str = "active"
    target_date: str = ""


class ProjectBody(BaseModel):
    name: str = ""
    team: str = ""
    slug: str = ""
    description: str = ""
    instructions: str = ""
    goal_id: int | None = None
    default_employee: str | None = None


class MessageBody(BaseModel):
    body: str = Field(default="", max_length=20000)


class BudgetBody(BaseModel):
    scope_type: str = "team"
    scope: str | None = None
    amount_usd: float = 0
    window: str = "month"
    warn_percent: int = 80
    hard_stop: bool = True


class ReleaseBody(BaseModel):
    note: str = ""


class MarkBody(BaseModel):
    keys: list[str]
    read: bool | None = None
    archived: bool | None = None


# ------------------------------------------------------------------ metas
@router.get("/goals")
def goals(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.goals.list_goals(db, acc(request, db)))


@router.post("/goals")
def goal_create(body: GoalBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.goals.create_goal(db, acc(request, db), body.title, body.team, body.parent_id, body.description,
                                               body.owner, body.status, body.target_date))


@router.patch("/goals/{goal_id}")
def goal_update(goal_id: int, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.goals.update_goal(db, acc(request, db), goal_id, body))


@router.delete("/goals/{goal_id}")
def goal_delete(goal_id: int, request: Request, db=Depends(db_dep)):
    guard(lambda: svc.goals.delete_goal(db, acc(request, db), goal_id))
    return {"deleted": goal_id}


# ------------------------------------------------------------------ projetos
@router.get("/projects")
def projects(request: Request, archived: bool = False, db=Depends(db_dep)):
    return guard(lambda: svc.goals.list_projects(db, acc(request, db), archived))


@router.post("/projects")
def project_create(body: ProjectBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.goals.create_project(db, acc(request, db), body.name, body.team, body.description,
                                                  body.instructions, body.goal_id, body.default_employee, body.slug))


@router.get("/projects/{ref}")
def project(ref: str, request: Request, db=Depends(db_dep)):
    def go():
        a = acc(request, db)
        return svc.goals.project_dict(db, svc.goals.find_project(db, a, ref), a, True)
    return guard(go)


@router.patch("/projects/{ref}")
def project_update(ref: str, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.goals.update_project(db, acc(request, db), ref, body))


# ------------------------------------------------------------------ a conversa da tarefa
@router.get("/tasks/{task_id}/messages")
def messages(task_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.task_thread.list_messages(db, acc(request, db), task_id))


@router.post("/tasks/{task_id}/messages")
def message_post(task_id: int, body: MessageBody, request: Request, db=Depends(db_dep)):
    """Enquanto o agente trabalha, a mensagem fica na fila e entra na próxima rodada; numa tarefa concluída, reabre."""
    return guard(lambda: svc.task_thread.post(db, acc(request, db), task_id, body.body))


@router.patch("/tasks/{task_id}/messages/{message_id}")
def message_edit(task_id: int, message_id: int, body: MessageBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.task_thread.edit(db, acc(request, db), task_id, message_id, body.body))


@router.delete("/tasks/{task_id}/messages/{message_id}")
def message_discard(task_id: int, message_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.task_thread.discard(db, acc(request, db), task_id, message_id))


# ------------------------------------------------------------------ orçamentos
@router.get("/budgets")
def budgets(request: Request, db=Depends(db_dep)):
    def go():
        a = acc(request, db)
        return {"policies": svc.budgets.list_policies(db, a), "incidents": svc.budgets.list_incidents(db, a)}
    return guard(go)


@router.post("/budgets")
def budget_create(body: BudgetBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.budgets.create_policy(db, acc(request, db), body.scope_type, body.scope, body.amount_usd,
                                                   body.window, body.warn_percent, body.hard_stop))


@router.patch("/budgets/{policy_id}")
def budget_update(policy_id: int, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.budgets.update_policy(db, acc(request, db), policy_id, body))


@router.delete("/budgets/{policy_id}")
def budget_delete(policy_id: int, request: Request, db=Depends(db_dep)):
    guard(lambda: svc.budgets.delete_policy(db, acc(request, db), policy_id))
    return {"deleted": policy_id}


@router.post("/budgets/incidents/{incident_id}/release")
def budget_release(incident_id: int, body: ReleaseBody, request: Request, db=Depends(db_dep)):
    """Libera o resto da janela (a parada não volta até a próxima); as tarefas paradas voltam para a fila."""
    return guard(lambda: svc.budgets.resolve(db, acc(request, db), incident_id, body.note))


# ------------------------------------------------------------------ caixa de entrada e busca
@router.get("/inbox")
def inbox(request: Request, view: str = "all", db=Depends(db_dep)):
    return guard(lambda: svc.inbox.items(db, acc(request, db), view))


@router.post("/inbox/mark")
def inbox_mark(body: MarkBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.inbox.mark(db, acc(request, db), body.keys, body.read, body.archived))


@router.get("/search")
def search(request: Request, q: str = "", kinds: str = "", limit: int = 30, db=Depends(db_dep)):
    return guard(lambda: svc.inbox.search(db, acc(request, db), q, kinds, limit))
