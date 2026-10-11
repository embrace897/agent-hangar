"""O trabalho do dia a dia: metas, projetos, a conversa da tarefa, orçamentos, a caixa de entrada e a busca."""
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import services as svc
from ..db import SessionLocal
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


class DepBody(BaseModel):
    on: int


class HoldBody(BaseModel):
    on: bool = True


class ContinueBody(BaseModel):
    confirm: bool = False
    note: str = ""


class SecretBody(BaseModel):
    name: str
    value: str = ""
    team: str | None = None
    description: str | None = None
    note: str = ""


class DecideBody(BaseModel):
    approve: bool


class SkillStudioBody(BaseModel):
    description: str = ""
    content: str = ""
    examples: str = ""
    test: dict | None = None
    note: str = ""


class CasesBody(BaseModel):
    cases: list[dict] = Field(default_factory=list)


class SkillRunBody(BaseModel):
    connection: str | None = None


class StatusBody(BaseModel):
    status: str


class PackageBody(BaseModel):
    package: dict | str | None = None
    catalog: str = ""  # id de um time pronto do catálogo, em vez do pacote
    team: str
    manager: str = ""


class ExportBody(BaseModel):
    team: str
    name: str = ""
    description: str = ""


class OrgBody(BaseModel):
    name: str = ""
    slug: str = ""
    admin_email: str = ""
    admin_name: str = ""
    admin_username: str = ""
    admin_password: str = ""
    domains: list[str] = Field(default_factory=list)


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


# ------------------------------------------------------------------ organograma
@router.get("/org-chart")
def org_chart(request: Request, team: str | None = None, db=Depends(db_dep)):
    return guard(lambda: svc.org_chart.chart(db, acc(request, db), team))


# ------------------------------------------------------------------ dependências, pausa e continuar
@router.post("/tasks/{task_id}/dependencies")
def dep_add(task_id: int, body: DepBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.task_flow.add_dependency(db, acc(request, db), task_id, body.on))


@router.delete("/tasks/{task_id}/dependencies/{on_id}")
def dep_remove(task_id: int, on_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.task_flow.remove_dependency(db, acc(request, db), task_id, on_id))


@router.post("/tasks/{task_id}/hold")
def hold(task_id: int, body: HoldBody, request: Request, db=Depends(db_dep)):
    """Pausa (ou retoma) a tarefa e as que ela repassou."""
    return guard(lambda: svc.task_flow.hold(db, acc(request, db), task_id, body.on))


@router.post("/tasks/{task_id}/continue")
def continue_task(task_id: int, body: ContinueBody, request: Request, db=Depends(db_dep)):
    """Tarefa com falha: mais rodadas, do checkpoint. Se a última rodada fez algo fora da plataforma, exige confirm."""
    return guard(lambda: svc.task_flow.continue_task(db, acc(request, db), task_id, body.confirm, body.note))


# ------------------------------------------------------------------ arquivos (anexos e entregáveis)
@router.post("/tasks/{task_id}/files")
async def file_upload(task_id: int, request: Request, name: str = "", note: str = ""):
    """O corpo é o arquivo; o nome vai em ?name= (e a nota em ?note=)."""
    data = await request.body()
    ctype = request.headers.get("content-type", "")

    def go():
        with SessionLocal() as db:
            return guard(lambda: svc.task_files.upload(db, acc(request, db), task_id, name, data, ctype, note))
    return await run_in_threadpool(go)


@router.get("/tasks/{task_id}/files")
def files(task_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.task_files.list_files(db, acc(request, db), task_id))


@router.get("/files")
def file_library(request: Request, q: str = "", kind: str = "output", project: str = "", employee: str = "",
                 db=Depends(db_dep)):
    return guard(lambda: svc.task_files.library(db, acc(request, db), q, kind, project, employee))


@router.get("/files/{file_id}")
def file_get(file_id: int, request: Request, inline: bool = False, db=Depends(db_dep)):
    f = guard(lambda: svc.task_files.get(db, acc(request, db), file_id))
    show = inline and f.content_type in svc.task_files.INLINE
    disp = "inline" if show else "attachment"
    headers = {"Content-Disposition": f"{disp}; filename*=UTF-8''{urllib.parse.quote(f.name)}",
               "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'",
               "Cache-Control": "private, no-store"}
    return Response(f.data, media_type=f.content_type if show else "application/octet-stream", headers=headers)


@router.delete("/files/{file_id}")
def file_delete(file_id: int, request: Request, db=Depends(db_dep)):
    guard(lambda: svc.task_files.delete(db, acc(request, db), file_id))
    return {"deleted": file_id}


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


# ------------------------------------------------------------------ Skill Studio
@router.get("/skills")
def skills(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.skill_studio.list_skills(db, acc(request, db)))


@router.get("/skills/{name}")
def skill(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.skill_studio.detail(db, acc(request, db), name))


@router.put("/skills/{name}")
def skill_save(name: str, body: SkillStudioBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.skill_studio.save(db, acc(request, db), name, body.description, body.content, body.examples,
                                               body.test, body.note))


@router.put("/skills/{name}/cases")
def skill_cases(name: str, body: CasesBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.skill_studio.set_cases(db, acc(request, db), name, body.cases))


@router.post("/skills/{name}/test")
def skill_test(name: str, body: SkillRunBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.skill_studio.run_tests(db, acc(request, db), name, body.connection))


@router.post("/skills/{name}/restore/{version}")
def skill_restore(name: str, version: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.skill_studio.restore(db, acc(request, db), name, version))


@router.post("/skills/{name}/status")
def skill_status(name: str, body: StatusBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.skill_studio.set_status(db, acc(request, db), name, body.status))


# ------------------------------------------------------------------ pacotes de time
@router.get("/packages/catalog")
def package_catalog(request: Request, db=Depends(db_dep)):
    acc(request, db)
    return svc.packages.catalog()


@router.post("/packages/export")
def package_export(body: ExportBody, request: Request, db=Depends(db_dep)):
    """O pacote do time em JSON (o portal baixa como YAML). Sem segredos, instalações de plugins nem orçamentos."""
    return guard(lambda: svc.packages.export(db, acc(request, db), body.team, body.name, body.description))


def _pkg(body: PackageBody):
    return svc.packages.catalog_doc(body.catalog) if body.catalog else body.package


@router.post("/packages/preview")
def package_preview(body: PackageBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.packages.preview(db, acc(request, db), _pkg(body), body.team, body.manager))


@router.post("/packages/install")
def package_install(body: PackageBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.packages.install(db, acc(request, db), _pkg(body), body.team, body.manager))


# ------------------------------------------------------------------ empresas (quem administra a instalação)
@router.get("/orgs")
def orgs(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.orgs.list_orgs(db, acc(request, db)))


@router.post("/orgs")
def org_create(body: OrgBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.orgs.create(db, acc(request, db), body.name, body.slug, body.admin_email, body.admin_name,
                                         body.admin_username, body.admin_password, body.domains))


@router.patch("/orgs/{org_id}")
def org_update(org_id: int, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.orgs.update(db, acc(request, db), org_id, body))


# ------------------------------------------------------------------ cofre de segredos
@router.get("/secrets")
def secrets(request: Request, db=Depends(db_dep)):
    """Nomes, escopo, versão e uso — nunca os valores."""
    return guard(lambda: svc.secrets_vault.list_secrets(db, acc(request, db)))


@router.put("/secrets")
def secret_put(body: SecretBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.secrets_vault.put(db, acc(request, db), body.name, body.value, body.team, body.description))


@router.delete("/secrets/{secret_id}")
def secret_delete(secret_id: int, request: Request, db=Depends(db_dep)):
    guard(lambda: svc.secrets_vault.delete(db, acc(request, db), secret_id))
    return {"deleted": secret_id}


@router.post("/secrets/proposals")
def secret_propose(body: SecretBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.secrets_vault.propose(db, acc(request, db), body.name, body.value, body.team, body.note))


@router.post("/secrets/proposals/{proposal_id}/decide")
def secret_decide(proposal_id: int, body: DecideBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.secrets_vault.decide(db, acc(request, db), proposal_id, body.approve))


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
