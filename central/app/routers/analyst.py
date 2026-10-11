"""O dia a dia do analista (fases E a H): dados e documentos, números certificados, processos, filas, conversas com
gente de fora, reuniões, conhecimento, navegador, governança de dados, decisões, vigias, modo par e valor."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import services as svc
from .deps import acc, db_dep, guard

router = APIRouter(prefix="/api")


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
