"""O dia a dia do analista (fases E a H): dados e documentos, números certificados, processos, filas, conversas com
gente de fora, reuniões, conhecimento, navegador, governança de dados, decisões, vigias, modo par e valor."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from .. import services as svc
from .deps import acc, db_dep, guard

router = APIRouter(prefix="/api")
hooks = APIRouter(prefix="/hooks")


# ------------------------------------------------------------------ ferramentas da plataforma por funcionário
class ToolsBody(BaseModel):
    disabled: list[str] = Field(default_factory=list)  # grupos desligados


@router.get("/employees/{slug}/platform-tools")
def employee_tools(slug: str, request: Request, db=Depends(db_dep)):
    def run():
        e, _ = svc.employees.find(db, acc(request, db), slug)
        return svc.platform_tools.groups_view(db, e)
    return guard(run)


@router.put("/employees/{slug}/platform-tools")
def employee_tools_set(slug: str, body: ToolsBody, request: Request, db=Depends(db_dep)):
    def run():
        a_ = acc(request, db)
        e, a = svc.employees.find(db, a_, slug, "manage")
        known = set(svc.platform_tools.GROUPS)
        bad = [g for g in body.disabled if g not in known]
        if bad:
            raise svc.PlatformError(f"grupos desconhecidos: {', '.join(bad)}")
        e.disabled_tools = sorted(set(body.disabled))
        db.commit()
        svc.common.audit(db, a_.p.name, "employee.platform_tools", a.slug, ", ".join(e.disabled_tools) or "todas ligadas")
        return svc.platform_tools.groups_view(db, e)
    return guard(run)


# ------------------------------------------------------------------ identidade visual dos entregáveis
class BrandBody(BaseModel):
    theme: str | None = None
    accent: str | None = None
    footer: str | None = None


@router.get("/brand")
def brand(request: Request, db=Depends(db_dep)):
    return guard(lambda: {**svc.analysis.brand(db), "data_studio": svc.analysis.ready()})


@router.put("/brand")
def brand_set(body: BrandBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.analysis.set_brand(db, acc(request, db), body.model_dump()))


# ------------------------------------------------------------------ extração de documentos
class SchemaBody(BaseModel):
    name: str = ""
    title: str = ""
    description: str = ""
    fields: list[dict] = Field(default_factory=list)
    min_confidence: float | None = None
    team: str = ""


@router.get("/extraction/schemas")
def schemas(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.extraction.list_schemas(db, acc(request, db)))


@router.put("/extraction/schemas")
def schema_save(body: SchemaBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.extraction.save_schema(db, acc(request, db), body.name, body.title, body.description,
                                                    body.fields, body.min_confidence, body.team))


@router.delete("/extraction/schemas/{name}")
def schema_archive(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.extraction.archive_schema(db, acc(request, db), name))


@router.get("/tasks/{task_id}/extractions")
def task_extractions(task_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.extraction.task_extractions(db, acc(request, db), task_id))


class BatchBody(BaseModel):
    title: str
    schema_name: str = Field(alias="schema")
    files: list[dict] = Field(default_factory=list)  # [{name, content_base64, content_type}]
    priority: int = 2


@router.post("/employees/{slug}/batch")
def batch(slug: str, body: BatchBody, request: Request, db=Depends(db_dep)):
    """Lote de documentos: uma tarefa por arquivo (extração) e uma de consolidação que espera todas."""
    import base64
    import binascii

    def run():
        try:
            got = [(str(f.get("name") or "arquivo"), base64.b64decode(f.get("content_base64") or "", validate=True),
                    str(f.get("content_type") or "")) for f in body.files]
        except (binascii.Error, ValueError):
            raise svc.PlatformError("content_base64 inválido") from None
        return svc.extraction.batch(db, acc(request, db), slug, body.title, body.schema_name, got, body.priority)
    return guard(run)


# ------------------------------------------------------------------ calculadoras certificadas
class CalcBody(BaseModel):
    name: str = ""
    title: str = ""
    description: str = ""
    inputs: list[dict] = Field(default_factory=list)
    steps: list[dict] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    cases: list[dict] = Field(default_factory=list)
    requires_review: bool | None = None
    team: str = ""


class CalcTryBody(BaseModel):
    inputs: dict | None = None  # vazio: roda os casos de teste


@router.get("/calculators")
def calculators(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.calculators.list_calcs(db, acc(request, db)))


@router.get("/calculators/{name}")
def calculator(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.calculators.detail(db, acc(request, db), name))


@router.put("/calculators")
def calculator_save(body: CalcBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.calculators.save(db, acc(request, db), body.name, body.title, body.description, body.inputs,
                                              body.steps, body.outputs, body.cases, body.requires_review, body.team))


@router.post("/calculators/{name}/test")
def calculator_test(name: str, body: CalcTryBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.calculators.test(db, acc(request, db), name, body.inputs))


@router.post("/calculators/{name}/certify")
def calculator_certify(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.calculators.certify(db, acc(request, db), name))


@router.post("/calculators/{name}/deprecate")
def calculator_deprecate(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.calculators.deprecate(db, acc(request, db), name))


# ================================================================== fase F
# ------------------------------------------------------------------ calendário útil
class CalendarBody(BaseModel):
    workdays: list[int] | None = None
    start: str | None = None
    end: str | None = None
    holidays: list[dict] | None = None
    removed: list[str] | None = None


@router.get("/calendar")
def calendar(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.bizcal.view(db))


@router.put("/calendar")
def calendar_set(body: CalendarBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.bizcal.update(db, acc(request, db), body.model_dump()))


# ------------------------------------------------------------------ filas e itens
class QueueBody(BaseModel):
    name: str
    team: str
    slug: str = ""
    description: str = ""
    categories: list[dict] = Field(default_factory=list)
    default_category: str = ""


class ItemBody(BaseModel):
    title: str
    body: str = ""
    category: str = ""
    priority: int | None = None


class ItemAction(BaseModel):
    action: str  # assign | start | done | approve | reject | reopen | cancel | release
    note: str = ""
    assignee: str = ""


class OnOff(BaseModel):
    enabled: bool = True


@router.get("/queues")
def queues(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.list_queues(db, acc(request, db)))


@router.put("/queues")
def queue_save(body: QueueBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.save_queue(db, acc(request, db), body.name, body.team, body.slug, body.description,
                                                    body.categories, body.default_category))


@router.delete("/queues/{slug}")
def queue_delete(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.delete_queue(db, acc(request, db), slug))


@router.post("/queues/{slug}/intake-token")
def queue_token(slug: str, body: OnOff, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.intake_token(db, acc(request, db), slug, body.enabled))


@router.post("/queues/{slug}/items")
def queue_submit(slug: str, body: ItemBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.submit(db, acc(request, db), slug, body.title, body.body, body.category, body.priority))


@router.post("/queues/{slug}/next")
def queue_next(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.take_next(db, acc(request, db), slug))


@router.get("/work-items")
def work_items(request: Request, queue: str = "", status: str = "", mine: bool = False, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.items(db, acc(request, db), queue, status, mine))


@router.get("/work-items/{item_id}")
def work_item(item_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.detail(db, acc(request, db), item_id))


@router.post("/work-items/{item_id}")
def work_item_act(item_id: int, body: ItemAction, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.work_queues.act(db, acc(request, db), item_id, body.action, body.note, body.assignee))


@router.post("/work-items/{item_id}/files")
async def work_item_file(item_id: int, request: Request, name: str, db=Depends(db_dep)):
    data = await request.body()
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(lambda: guard(lambda: svc.work_queues.attach(
        db, acc(request, db), item_id, name, data, request.headers.get("content-type", ""))))


@router.get("/work-item-files/{file_id}")
def work_item_file_get(file_id: int, request: Request, db=Depends(db_dep)):
    f = guard(lambda: svc.work_queues.file(db, acc(request, db), file_id))
    return Response(f.data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{f.name}"', "X-Content-Type-Options": "nosniff"})


class IntakeBody(BaseModel):
    title: str
    body: str = ""
    category: str = ""
    source: str = ""


@hooks.post("/queues/{slug}")
def queue_hook(slug: str, body: IntakeBody, request: Request, db=Depends(db_dep)):
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else request.headers.get("x-hangar-token", "")
    try:
        return svc.work_queues.webhook(db, slug, token, body.title, body.body, body.category, body.source)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(409, str(e)) from None


# ------------------------------------------------------------------ playbooks
class PlaybookBody(BaseModel):
    name: str
    team: str
    title: str = ""
    description: str = ""
    steps: list[dict] = Field(default_factory=list)
    schedule: str = ""
    run_title: str = ""


class RunBody(BaseModel):
    anchor: str = ""  # AAAA-MM-DD (padrão: hoje)
    title: str = ""


@router.get("/playbooks")
def playbooks(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.playbooks.list_playbooks(db, acc(request, db)))


@router.get("/playbooks/{name}")
def playbook(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.playbooks.detail(db, acc(request, db), name))


@router.put("/playbooks")
def playbook_save(body: PlaybookBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.playbooks.save(db, acc(request, db), body.name, body.team, body.title, body.description,
                                            body.steps, body.schedule, body.run_title))


@router.delete("/playbooks/{name}")
def playbook_archive(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.playbooks.archive(db, acc(request, db), name))


@router.post("/playbooks/{name}/runs")
def playbook_run(name: str, body: RunBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.playbooks.run_now(db, acc(request, db), name, body.anchor, body.title))


@router.get("/playbook-runs/{run_id}")
def playbook_run_get(run_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.playbooks.run_detail(db, acc(request, db), run_id))


# ------------------------------------------------------------------ gente de fora
class TemplateBody(BaseModel):
    name: str
    body: str
    subject: str = ""
    channel: str = "any"
    wa_template: str = ""
    team: str = ""


@router.get("/message-templates")
def templates(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.correspondence.list_templates(db, acc(request, db)))


@router.put("/message-templates")
def template_save(body: TemplateBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.correspondence.save_template(db, acc(request, db), body.name, body.body, body.subject,
                                                          body.channel, body.wa_template, body.team))


@router.post("/message-templates/{name}/approve")
def template_approve(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.correspondence.approve_template(db, acc(request, db), name))


@router.get("/tasks/{task_id}/conversations")
def conversations(task_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.correspondence.task_threads(db, acc(request, db), task_id))


@hooks.get("/whatsapp/{org_id}")
def whatsapp_verify(org_id: int, request: Request, db=Depends(db_dep)):
    q = request.query_params
    try:
        return PlainTextResponse(svc.correspondence.whatsapp_verify(db, org_id, q.get("hub.mode", ""),
                                                                    q.get("hub.verify_token", ""), q.get("hub.challenge", "")))
    except svc.access.Forbidden as e:
        raise HTTPException(403, str(e)) from None


@hooks.post("/whatsapp/{org_id}")
async def whatsapp_event(org_id: int, request: Request, db=Depends(db_dep)):
    from fastapi.concurrency import run_in_threadpool
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        await run_in_threadpool(svc.correspondence.whatsapp_event, db, org_id, raw, headers)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    return Response(status_code=200)


# ------------------------------------------------------------------ reuniões
class MeetingBody(BaseModel):
    title: str
    starts_at: str = ""  # AAAA-MM-DD HH:MM (fuso da empresa)
    duration_min: int = 30
    attendees: list[str] = Field(default_factory=list)
    agenda: str = ""
    team: str = ""
    employee: str = ""  # quem escreve a ata e prepara o briefing
    prep_hours: int = 0
    series: str = ""
    schedule: bool = False  # marcar na agenda da empresa


class TranscriptBody(BaseModel):
    text: str


@router.get("/meetings")
def meetings(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.meetings.list_meetings(db, acc(request, db)))


@router.post("/meetings")
def meeting_create(body: MeetingBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.meetings.create(db, acc(request, db), body.title, body.starts_at, body.duration_min,
                                             body.attendees, body.agenda, body.team, body.employee, body.prep_hours,
                                             body.series, body.schedule))


@router.get("/meetings/{mid}")
def meeting(mid: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.meetings.get(db, acc(request, db), mid))


@router.delete("/meetings/{mid}")
def meeting_delete(mid: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.meetings.delete(db, acc(request, db), mid))


@router.post("/meetings/{mid}/transcript")
def meeting_transcript(mid: int, body: TranscriptBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.meetings.transcript(db, acc(request, db), mid, body.text))


@router.post("/meetings-token")
def meetings_token(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.meetings.hook_token(db, acc(request, db)))


@hooks.post("/meetings/{org_id}")
def meetings_hook(org_id: int, body: dict, request: Request, db=Depends(db_dep)):
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else request.headers.get("x-hangar-token", "")
    try:
        return svc.meetings.hook(db, org_id, token, body)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(409, str(e)) from None
