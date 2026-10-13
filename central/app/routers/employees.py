"""API do Digital employee: contratação, ciclo de vida, alçada, tarefas, decisões (humano no circuito), relatórios e,
para o admin, a força de trabalho digital (catálogo de ações, piso da empresa e parada geral)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import services as svc
from .deps import acc, db_dep, guard

router = APIRouter(prefix="/api")
hooks = APIRouter(prefix="/hooks")


class HireBody(BaseModel):
    request: str = ""
    name: str = ""
    title: str = ""
    mission: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    manager: str = ""
    backups: list[str] = Field(default_factory=list)
    team: str = ""
    visibility: str = ""
    systems: list[str] = Field(default_factory=list)
    specialists: list[str] = Field(default_factory=list)
    base: str = ""
    skills: list[str] = Field(default_factory=list)
    new_skills: list[dict] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
    tools: list[dict] = Field(default_factory=list)
    instructions: str = ""
    llm: dict | None = None
    memory: dict | None = None
    channels: list[str] = Field(default_factory=list)
    authority: list[dict] = Field(default_factory=list)
    accept_default_authority: bool = False
    autonomy_level: str = "intern"
    probation_tasks: list[dict] = Field(default_factory=list)
    kpis: list[dict | str] = Field(default_factory=list)
    working_hours: str = ""
    task_budget_usd: float | None = None
    task_time_limit_min: int | None = None
    report_webhook: str = ""
    report_hour: int | None = None
    report_weekday: int | None = None
    routines: list[dict] = Field(default_factory=list)
    shadow: bool = False
    personal: bool = False  # agente pessoal do membro (as regras do workspace)
    plan_id: str = ""


class StatusBody(BaseModel):
    to: str
    reason: str = ""


class AuthorityBody(BaseModel):
    rules: list[dict]


class TaskBody(BaseModel):
    title: str = Field(max_length=300)
    body: str = ""
    priority: int = 2
    due_at: datetime | None = None
    plan_approval: bool = False  # o funcionário propõe o plano e só executa depois da aprovação do gestor
    project: str | None = None  # o projeto a que a tarefa serve (o agente recebe o contexto e a cadeia de metas)
    review: bool = False  # o resultado passa por revisão (do gestor, ou de reviewer) antes de concluir
    reviewer: str | None = None  # e-mail de quem revisa
    blocked_by: list[int] = Field(default_factory=list)  # tarefas que precisam terminar antes
    files: list[dict] = Field(default_factory=list)  # anexos já na criação: [{name, content_base64, content_type}]
    mode: str = "work"  # work | copilot (modo par: o funcionário orienta, a pessoa faz)


class InboxBody(BaseModel):
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    folder: str | None = None
    allowed: list[str] | None = None
    enabled: bool | None = None


class DecisionBody(BaseModel):
    decision: str
    edit: dict | None = None
    reason: str = ""


class BatchDecisionBody(BaseModel):
    ids: list[int]
    decision: str
    reason: str = ""


class ClassifyBody(BaseModel):
    tool_ref: str
    action_type: str
    risk: int | None = None
    reversible: bool = False


class FloorBody(BaseModel):
    items: list[dict]


class StopBody(BaseModel):
    reason: str = ""


class RoutineBody(BaseModel):
    title: str = ""
    body: str = ""
    cron: str = ""
    timezone: str = ""
    priority: int = 2
    enabled: bool = True
    creates_issue: bool | None = None  # cada disparo abre uma issue no quadro


class WebhookBody(BaseModel):
    enabled: bool = True


class ApplyBody(BaseModel):
    text: str = ""


class LessonBody(BaseModel):
    text: str


class ShadowBody(BaseModel):
    on: bool
    reason: str = ""


class InboundBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=20000)
    dedupe_key: str = Field(default="", max_length=200)
    priority: int = 2
    due_at: datetime | None = None
    source: str = Field(default="", max_length=60)


def _fields(b: HireBody) -> dict:
    return {k: v for k, v in b.model_dump().items() if k != "request"}


@router.get("/employees")
def employees(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.list_employees(db, acc(request, db)))


@router.post("/employees/plan")
def plan(body: HireBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.plan(db, acc(request, db), body.request, **_fields(body)))


@router.post("/employees")
def hire(body: HireBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.hire(db, acc(request, db), body.request, **_fields(body)))


@router.get("/employees/{slug}")
def detail(slug: str, request: Request, db=Depends(db_dep)):
    def go():
        a = acc(request, db)
        e, _ = svc.employees.find(db, a, slug)
        return {**svc.employees.employee_dict(db, e, a, detail=True), "metrics": svc.employees.metrics(db, e)}
    return guard(go)


@router.patch("/employees/{slug}")
def update(slug: str, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.update(db, acc(request, db), slug, body))


@router.post("/employees/{slug}/probation")
def probation(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.start_probation(db, acc(request, db), slug))


@router.post("/employees/{slug}/status")
def status(slug: str, body: StatusBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.set_status(db, acc(request, db), slug, body.to, body.reason))


@router.put("/employees/{slug}/authority")
def authority(slug: str, body: AuthorityBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.set_authority(db, acc(request, db), slug, body.rules))


@router.get("/employees/{slug}/tasks")
def tasks(slug: str, request: Request, status: str | None = None, db=Depends(db_dep)):
    return guard(lambda: svc.employees.tasks_of(db, acc(request, db), slug, status))


@router.post("/employees/{slug}/tasks")
def assign(slug: str, body: TaskBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.assign(db, acc(request, db), slug, body.title, body.body, body.priority,
                                              body.due_at, plan_approval=body.plan_approval, project=body.project,
                                              review=body.review, reviewer=body.reviewer, blocked_by=body.blocked_by,
                                              files=body.files, mode=body.mode))


@router.put("/employees/{slug}/inbox")
def inbox_set(slug: str, body: InboxBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_teamwork.set_inbox(db, acc(request, db), slug, body.model_dump()))


@router.post("/employees/{slug}/inbox/test")
def inbox_test(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_teamwork.test_inbox(db, acc(request, db), slug))


@router.get("/employees/{slug}/reports")
def reports(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.reports_of(db, acc(request, db), slug))


@router.post("/employees/{slug}/reports")
def report_now(slug: str, request: Request, period: str = "daily", db=Depends(db_dep)):
    def go():
        e, _ = svc.employees.find(db, acc(request, db), slug, "manage")
        if period not in ("daily", "weekly"):
            raise svc.PlatformError("period: daily | weekly")
        r = svc.employees.make_report(db, e, period)
        return {"id": r.id, "summary": r.summary, "delivered": r.delivered, "metrics": r.metrics}
    return guard(go)


# ------------------------------------------------------------------ F2: rotinas, webhook, aprendizado
@router.get("/employees/{slug}/routines")
def routines(slug: str, request: Request, db=Depends(db_dep)):
    def go():
        e, _ = svc.employees.find(db, acc(request, db), slug)
        return svc.employee_work.routines_of(db, e)
    return guard(go)


@router.post("/employees/{slug}/routines")
def routine_add(slug: str, body: RoutineBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.set_routine(db, acc(request, db), slug, **body.model_dump()))


@router.patch("/employees/{slug}/routines/{routine_id}")
def routine_edit(slug: str, routine_id: int, body: RoutineBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.set_routine(db, acc(request, db), slug, routine_id=routine_id,
                                                       **body.model_dump()))


@router.delete("/employees/{slug}/routines/{routine_id}")
def routine_del(slug: str, routine_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.delete_routine(db, acc(request, db), slug, routine_id))


@router.post("/employees/{slug}/routines/{routine_id}/run")
def routine_run(slug: str, routine_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.run_routine_now(db, acc(request, db), slug, routine_id))


@router.post("/employees/{slug}/webhook")
def webhook(slug: str, body: WebhookBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.set_webhook(db, acc(request, db), slug, body.enabled))


@router.get("/employees/{slug}/suggestions")
def suggestions(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.suggestions_for(db, acc(request, db), slug))


@router.post("/employees/{slug}/suggestions/{sid}/apply")
def suggestion_apply(slug: str, sid: str, body: ApplyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.apply_suggestion(db, acc(request, db), slug, sid, body.text))


@router.post("/employees/{slug}/suggestions/{sid}/dismiss")
def suggestion_dismiss(slug: str, sid: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.dismiss_suggestion(db, acc(request, db), slug, sid))


@router.get("/employees/{slug}/shadow")
def shadow_log(slug: str, request: Request, status: str | None = None, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.shadow_log(db, acc(request, db), slug, status))


@router.post("/employees/{slug}/shadow")
def shadow_set(slug: str, body: ShadowBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.set_shadow(db, acc(request, db), slug, body.on, body.reason))


@router.get("/employees/{slug}/career")
def career(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.career_for(db, acc(request, db), slug))


@router.post("/employees/{slug}/lessons")
def lesson_add(slug: str, body: LessonBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.lesson_manual(db, acc(request, db), slug, body.text))


@router.delete("/employees/{slug}/lessons/{lesson_id}")
def lesson_del(slug: str, lesson_id: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_work.remove_lesson(db, acc(request, db), slug, lesson_id))


@router.get("/tasks/{task_id}")
def task(task_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.task_detail(db, acc(request, db), task_id))


class QualityBody(BaseModel):
    score: int
    note: str = ""


@router.get("/employees/{slug}/revisions")
def revisions(slug: str, request: Request, db=Depends(db_dep)):
    """Histórico do cargo, da alçada e das configurações (o retrato de cada versão e o que mudou)."""
    return guard(lambda: svc.employee_revisions.list_revisions(db, acc(request, db), slug))


@router.post("/employees/{slug}/revisions/{version}/restore")
def restore(slug: str, version: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_revisions.restore(db, acc(request, db), slug, version))


@router.post("/tasks/{task_id}/quality")
def quality(task_id: int, body: QualityBody, request: Request, db=Depends(db_dep)):
    """A nota do gestor para o resultado (1 a 5): substitui a do juiz e entra na carreira."""
    return guard(lambda: svc.employee_quality.rate(db, acc(request, db), task_id, body.score, body.note))


@router.get("/workforce/dashboard")
def dashboard(request: Request, days: int = 30, team: str | None = None, db=Depends(db_dep)):
    """Painel do gestor: custo, tempo de decisão, % no prazo, qualidade e gargalos dos Digital employees que a pessoa
    gerencia (admins e auditores: todos)."""
    return guard(lambda: svc.employee_dashboard.dashboard(db, acc(request, db), days, team))


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.cancel_task(db, acc(request, db), task_id))


@router.get("/decisions")
def decisions(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_tasks.pending_for(db, acc(request, db)))


@router.post("/decisions/batch")
def decide_batch(body: BatchDecisionBody, request: Request, db=Depends(db_dep)):
    def go():
        a, out = acc(request, db), []
        for i in body.ids:
            try:
                out.append(svc.employee_tasks.decide(db, a, i, body.decision, None, body.reason))
            except svc.PlatformError as e:
                out.append({"id": i, "error": str(e)})
        return out
    return guard(go)


@router.post("/decisions/{request_id}")
def decide(request_id: int, body: DecisionBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_tasks.decide(db, acc(request, db), request_id, body.decision, body.edit,
                                                   body.reason))


# ------------------------------------------------------------------ admin
@router.get("/admin/workforce")
def workforce(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.workforce(db, acc(request, db)))


@router.post("/admin/workforce/stop-all")
def stop_all(body: StopBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.stop_all(db, acc(request, db), body.reason))


@router.get("/admin/action-catalog")
def action_catalog(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.catalog(db, acc(request, db)))


@router.put("/admin/action-catalog")
def classify(body: ClassifyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.classify_tool(db, acc(request, db), body.tool_ref, body.action_type, body.risk,
                                                     body.reversible))


@router.get("/admin/authority-floor")
def floor(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.floor(db, acc(request, db)))


@router.put("/admin/authority-floor")
def set_floor(body: FloorBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employees.set_floor(db, acc(request, db), body.items))


# ------------------------------------------------------------------ webhook de entrada (outros sistemas criam tarefas)
@hooks.post("/employees/{slug}")
def inbound(slug: str, body: InboundBody, request: Request, db=Depends(db_dep)):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-hangar-token", "")
    if not token:
        raise HTTPException(401, "token ausente: Authorization: Bearer <token do webhook>")
    try:
        return svc.employee_work.inbound(db, slug, token, body.title, body.body, body.dedupe_key, body.priority,
                                         body.due_at, body.source)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(409, str(e)) from None


# ------------------------------------------------------------------ Digital employee externo (worker seu, pelo heartbeat)
class RuntimeBody(BaseModel):
    runtime: str = "external"  # hangar | external
    rotate: bool = False  # gera um token novo (o anterior deixa de valer)


@router.put("/employees/{slug}/runtime")
def set_runtime(slug: str, body: RuntimeBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.employee_external.set_runtime(db, acc(request, db), slug, body.runtime, body.rotate))


class HeartbeatBody(BaseModel):
    worker: dict = Field(default_factory=dict)  # nome, versão, máquina — aparece na tela do funcionário
    want_task: bool = True


class WorkerGateBody(BaseModel):
    tool: str = ""
    args: dict = Field(default_factory=dict)
    rationale: str = ""
    kind: str = "tool"  # tool | ask_human (args.question) | capability (args.need)


class ProgressBody(BaseModel):
    note: str = ""
    cost_usd: float = 0
    tokens: int = 0


class ResultBody(BaseModel):
    status: str = "done"  # done | failed | waiting
    result: str = ""
    error: str = ""
    cost_usd: float = 0
    tokens: int = 0


def _worker(request: Request, fn):
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else request.headers.get("x-hangar-token", "")
    if not token:
        raise HTTPException(401, "token ausente: Authorization: Bearer <token do worker (ewk_…)>")
    try:
        return fn(token)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(409, str(e)) from None


@hooks.post("/worker/heartbeat")
def worker_heartbeat(body: HeartbeatBody, request: Request, db=Depends(db_dep)):
    return _worker(request, lambda t: svc.employee_external.heartbeat(db, t, body.worker, body.want_task))


@hooks.post("/worker/tasks/{task_id}/gate")
def worker_gate(task_id: int, body: WorkerGateBody, request: Request, db=Depends(db_dep)):
    return _worker(request, lambda t: svc.employee_external.gate(db, t, task_id, body.tool, body.args, body.rationale, body.kind))


@hooks.post("/worker/tasks/{task_id}/progress")
def worker_progress(task_id: int, body: ProgressBody, request: Request, db=Depends(db_dep)):
    return _worker(request, lambda t: svc.employee_external.progress(db, t, task_id, body.note, body.cost_usd, body.tokens))


@hooks.post("/worker/tasks/{task_id}/result")
def worker_result(task_id: int, body: ResultBody, request: Request, db=Depends(db_dep)):
    return _worker(request, lambda t: svc.employee_external.result(db, t, task_id, body.status, body.result, body.error,
                                                                   body.cost_usd, body.tokens))
