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


# ================================================================== fase G
# ------------------------------------------------------------------ base de conhecimento
class SourceBody(BaseModel):
    name: str
    kind: str = "upload"
    title: str = ""
    acl_mode: str = "team"
    teams: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    review_days: int = 365
    classification: str = "internal"


class DocBody(BaseModel):
    title: str
    text: str = ""
    file_name: str = ""
    content_base64: str = ""
    content_type: str = ""
    url: str = ""


@router.get("/knowledge/sources")
def k_sources(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.list_sources(db, acc(request, db)))


@router.put("/knowledge/sources")
def k_source_save(body: SourceBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.save_source(db, acc(request, db), body.name, body.kind, body.title, body.acl_mode,
                                                   body.teams, body.config, body.review_days, body.classification))


@router.delete("/knowledge/sources/{name}")
def k_source_delete(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.delete_source(db, acc(request, db), name))


@router.post("/knowledge/sources/{name}/sync")
def k_sync(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.sync_now(db, acc(request, db), name))


@router.post("/knowledge/sources/{name}/documents")
def k_doc(name: str, body: DocBody, request: Request, db=Depends(db_dep)):
    import base64

    def run():
        data = base64.b64decode(body.content_base64) if body.content_base64 else b""
        return svc.knowledge.add_document(db, acc(request, db), name, body.title, body.text, body.file_name, data,
                                          body.content_type, body.url)
    return guard(run)


@router.get("/knowledge/search")
def k_search(request: Request, q: str = "", db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.people_search(db, acc(request, db), q))


@router.get("/knowledge/gaps")
def k_gaps(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.gaps(db, acc(request, db)))


@router.post("/knowledge/gaps/{gap_id}/resolve")
def k_gap_resolve(gap_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.resolve_gap(db, acc(request, db), gap_id))


@router.get("/knowledge/stale")
def k_stale(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.knowledge.stale_docs(db, acc(request, db)))


# ------------------------------------------------------------------ navegador governado
class ProcedureBody(BaseModel):
    name: str
    title: str = ""
    domains: list[str] = Field(default_factory=list)
    steps: list[dict] = Field(default_factory=list)
    team: str = ""
    start_url: str = ""


class FromTaskBody(BaseModel):
    task_id: int
    name: str
    title: str = ""
    team: str = ""


class DomainsBody(BaseModel):
    domains: list[str] = Field(default_factory=list)


@router.get("/procedures")
def procedures(request: Request, db=Depends(db_dep)):
    return guard(lambda: {"procedures": svc.browser.list_procedures(db, acc(request, db)), "ready": svc.browser.ready()})


@router.put("/procedures")
def procedure_save(body: ProcedureBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.browser.save_procedure(db, acc(request, db), body.name, body.title, body.domains, body.steps,
                                                    body.team, body.start_url))


@router.post("/procedures/from-task")
def procedure_from_task(body: FromTaskBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.browser.from_task(db, acc(request, db), body.task_id, body.name, body.title, body.team))


@router.put("/employees/{slug}/browser-domains")
def browser_domains(slug: str, body: DomainsBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.browser.set_domains(db, acc(request, db), slug, body.domains))


class AppsBody(BaseModel):
    apps: list[str] = Field(default_factory=list)


@router.put("/employees/{slug}/desktop-apps")
def desktop_apps(slug: str, body: AppsBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.desktop.set_apps(db, acc(request, db), slug, body.apps))


# ------------------------------------------------------------------ governança de dados
class PolicyBody(BaseModel):
    dlp: dict | None = None
    retention: dict | None = None


class ClassifyBody(BaseModel):
    classification: str | None = None
    legal_hold: bool | None = None


class ProjectGovBody(BaseModel):
    classification: str | None = None
    legal_hold: bool | None = None
    confidential: bool | None = None
    members: list[str] | None = None


class SubjectBody(BaseModel):
    subject: str
    kind: str = "access"
    note: str = ""


class EraseBody(BaseModel):
    approve: bool = True


class BreakGlassBody(BaseModel):
    reason: str


class LlmClassBody(BaseModel):
    max_classification: str


@router.get("/governance")
def gov(request: Request, db=Depends(db_dep)):
    def run():
        a_ = acc(request, db)
        if not (a_.p.is_admin or a_.p.is_auditor):
            raise svc.access.Forbidden("só admins e auditores")
        from sqlalchemy import select

        from ..models import LlmConnection
        out = svc.governance.view(db)
        out["llm"] = [{"name": c.name, "model": c.model_name, "max_classification": c.max_classification or "confidential"}
                      for c in db.scalars(select(LlmConnection).order_by(LlmConnection.name))]
        return out
    return guard(run)


@router.put("/governance")
def gov_set(body: PolicyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.set_policy(db, acc(request, db), body.model_dump()))


@router.put("/governance/llm/{name}")
def gov_llm(name: str, body: LlmClassBody, request: Request, db=Depends(db_dep)):
    def run():
        a_ = acc(request, db)
        if not a_.p.is_admin:
            raise svc.access.Forbidden("só o admin")
        if body.max_classification not in svc.governance.LEVELS:
            raise svc.PlatformError(f"classificação: {', '.join(svc.governance.LEVELS)}")
        c = svc.catalog.get_connection(db, name)
        c.max_classification = body.max_classification
        db.commit()
        svc.common.audit(db, a_.p.name, "governance.llm", name, body.max_classification)
        return {"name": c.name, "max_classification": c.max_classification}
    return guard(run)


@router.put("/tasks/{task_id}/classification")
def task_class(task_id: int, body: ClassifyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.classify(db, acc(request, db), "task", task_id, body.classification, body.legal_hold))


@router.put("/files/{file_id}/classification")
def file_class(file_id: int, body: ClassifyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.classify(db, acc(request, db), "file", file_id, body.classification))


@router.put("/projects/{slug}/governance")
def project_gov(slug: str, body: ProjectGovBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.project_governance(db, acc(request, db), slug, body.model_dump()))


@router.post("/projects/{slug}/break-glass")
def break_glass(slug: str, body: BreakGlassBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.break_glass(db, acc(request, db), slug, body.reason))


@router.get("/subject-requests")
def subject_requests(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.list_subject_requests(db, acc(request, db)))


@router.post("/subject-requests")
def subject_request(body: SubjectBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.subject_request(db, acc(request, db), body.subject, body.kind, body.note))


@router.post("/subject-requests/{rid}/erase")
def subject_erase(rid: int, body: EraseBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.governance.erase(db, acc(request, db), rid, body.approve))


# ================================================================== fase H
# ------------------------------------------------------------------ memorandos de decisão
class MemoDecideBody(BaseModel):
    option: str
    note: str = ""
    follow_up_days: int = 30


class MemoOutcomeBody(BaseModel):
    outcome: str  # good | mixed | bad
    note: str = ""


class CriticBody(BaseModel):
    mode: str  # off | auto


@router.get("/memos")
def memos(request: Request, pending: bool = False, db=Depends(db_dep)):
    return guard(lambda: svc.decision_memos.list_memos(db, acc(request, db), pending))


@router.get("/memos/{mid}")
def memo(mid: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.decision_memos.get(db, acc(request, db), mid))


@router.post("/memos/{mid}/decide")
def memo_decide(mid: int, body: MemoDecideBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.decision_memos.decide(db, acc(request, db), mid, body.option, body.note, body.follow_up_days))


@router.post("/memos/{mid}/outcome")
def memo_outcome(mid: int, body: MemoOutcomeBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.decision_memos.outcome(db, acc(request, db), mid, body.outcome, body.note))


@router.put("/employees/{slug}/critic")
def critic_mode(slug: str, body: CriticBody, request: Request, db=Depends(db_dep)):
    def run():
        a_ = acc(request, db)
        if body.mode not in ("off", "auto"):
            raise svc.PlatformError("mode: off | auto")
        e, a = svc.employees.find(db, a_, slug, "manage")
        e.critic_mode = body.mode
        db.commit()
        svc.common.audit(db, a_.p.name, "employee.critic", a.slug, body.mode)
        return {"critic_mode": e.critic_mode}
    return guard(run)


# ------------------------------------------------------------------ vigias
@router.get("/watchers")
def watchers(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.watchers.list_watchers(db, acc(request, db)))


@router.post("/watchers")
def watcher_create(body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.watchers.save(db, acc(request, db), body))


@router.put("/watchers/{wid}")
def watcher_update(wid: int, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.watchers.save(db, acc(request, db), body, wid))


@router.delete("/watchers/{wid}")
def watcher_delete(wid: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.watchers.delete(db, acc(request, db), wid))


@router.post("/watchers/{wid}/check")
def watcher_check(wid: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.watchers.run_now(db, acc(request, db), wid))


# ------------------------------------------------------------------ modo par e trilhas
class ReviewReqBody(BaseModel):
    employee: str


class TrackBody(BaseModel):
    source: str = "manual"  # playbook:<nome> | procedure:<nome> | manual
    name: str = ""
    team: str = ""
    questions: list[dict] = Field(default_factory=list)


class AnswerBody(BaseModel):
    answers: list[int] = Field(default_factory=list)


@router.post("/work-items/{item_id}/review-request")
def review_request(item_id: int, body: ReviewReqBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.learning.review_request(db, acc(request, db), item_id, body.employee))


@router.get("/learning")
def tracks(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.learning.list_tracks(db, acc(request, db)))


@router.post("/learning")
def track_create(body: TrackBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.learning.create(db, acc(request, db), body.source, body.name, body.team, body.questions))


@router.post("/learning/{track_id}/answer")
def track_answer(track_id: int, body: AnswerBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.learning.answer(db, acc(request, db), track_id, body.answers))


@router.get("/learning/{track_id}/results")
def track_results(track_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.learning.results(db, acc(request, db), track_id))


# ------------------------------------------------------------------ valor e oportunidades
class BaselinesBody(BaseModel):
    items: list[dict] = Field(default_factory=list)


@router.get("/value/baselines")
def value_baselines(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.value.baselines(db, acc(request, db)))


@router.put("/value/baselines")
def value_baselines_set(body: BaselinesBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.value.set_baselines(db, acc(request, db), body.items))


@router.get("/value/roi")
def value_roi(request: Request, days: int = 30, team: str = "", db=Depends(db_dep)):
    return guard(lambda: svc.value.roi(db, acc(request, db), days, team))


@router.get("/value/report")
def value_report(request: Request, days: int = 30, db=Depends(db_dep)):
    text = guard(lambda: svc.value.report(db, acc(request, db), days))
    return Response(text, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="valor-digital-employees.md"'})


@router.get("/value/opportunities")
def value_opportunities(request: Request, days: int = 90, db=Depends(db_dep)):
    return guard(lambda: svc.value.opportunities(db, acc(request, db), days))


# ------------------------------------------------------------------ tarefas de ouro e regressão
class GoldenBody(BaseModel):
    title: str
    body: str = ""
    expected: str


class ModeBody(BaseModel):
    mode: str


@router.get("/employees/{slug}/golden")
def golden(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.regression.view(db, acc(request, db), slug))


@router.post("/employees/{slug}/golden")
def golden_add(slug: str, body: GoldenBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.regression.add(db, acc(request, db), slug, body.title, body.body, body.expected))


@router.post("/employees/{slug}/golden/from-probation")
def golden_from_probation(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.regression.from_probation(db, acc(request, db), slug))


@router.post("/employees/{slug}/golden/run")
def golden_run(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.regression.run_now(db, acc(request, db), slug))


@router.put("/employees/{slug}/golden/mode")
def golden_mode(slug: str, body: ModeBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.regression.set_mode(db, acc(request, db), slug, body.mode))


@router.delete("/golden/{golden_id}")
def golden_remove(golden_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.regression.remove(db, acc(request, db), golden_id))
