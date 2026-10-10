"""Plugins: catálogo, rascunho/importação de OpenAPI, revisão (quatro olhos), instalação por time e o servidor MCP
interno que os agentes chamam (/internal/plugins/<nome>/mcp, autenticado pelo token interno do agente)."""
import json
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from .. import config
from .. import services as svc
from ..db import SessionLocal
from .deps import acc, db_dep, guard
from .internal import caller_agent, caller_specs

router = APIRouter()
hooks = APIRouter(prefix="/hooks")


class ManifestBody(BaseModel):
    manifest: dict | str
    team: str | None = None
    source: str = "manual"


class OpenApiBody(BaseModel):
    document: dict | str
    name: str = ""
    base_url: str = ""
    only: list[str] | None = None


class ReviewBody(BaseModel):
    decision: str
    note: str = ""


class InstallBody(BaseModel):
    team: str | None = None
    settings: dict | None = None
    secrets: dict | None = None
    credential: str | None = None
    enabled: bool | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    triggers: dict | None = None
    stage: bool = False  # instalação de teste do rascunho (quem constrói o plugin)
    rate_limit: int | None = None  # chamadas por minuto (0 = o padrão)


class TeamBody(BaseModel):
    team: str | None = None
    stage: bool = False


class RequestBody(BaseModel):
    team: str
    note: str = ""


class FeatureBody(BaseModel):
    on: bool = True


class TriggerTestBody(BaseModel):
    event: dict | str | None = None  # vazio = o exemplo do manifesto
    team: str | None = None


@router.get("/api/plugins")
def plugins(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.list_plugins(db, acc(request, db)))


@router.post("/api/plugins")
def create(body: ManifestBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.create(db, acc(request, db), body.manifest, body.team, body.source))


@router.post("/api/plugins/import-openapi")
def import_openapi(body: OpenApiBody, request: Request, db=Depends(db_dep)):
    """Devolve um rascunho de manifesto (não grava): revise as ações e salve com POST /api/plugins."""
    acc(request, db)
    return guard(lambda: {"manifest": svc.plugins.from_openapi(body.document, body.name, body.base_url, body.only)})


@router.get("/api/plugins/{name}")
def detail(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.get_plugin(db, acc(request, db), name))


@router.put("/api/plugins/{name}")
def update(name: str, body: ManifestBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.update(db, acc(request, db), name, body.manifest))


@router.delete("/api/plugins/{name}")
def delete(name: str, request: Request, db=Depends(db_dep)):
    guard(lambda: svc.plugins.delete(db, acc(request, db), name))
    return {"deleted": name}


@router.post("/api/plugins/{name}/submit")
def submit(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.submit(db, acc(request, db), name))


@router.post("/api/plugins/{name}/review")
def review(name: str, body: ReviewBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.review(db, acc(request, db), name, body.decision, body.note))


@router.post("/api/plugins/{name}/disable")
def disable(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.set_disabled(db, acc(request, db), name, True))


@router.post("/api/plugins/{name}/enable")
def enable(name: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.set_disabled(db, acc(request, db), name, False))


@router.put("/api/plugins/{name}/install")
def install(name: str, body: InstallBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.install(db, acc(request, db), name, body.team, body.settings, body.secrets,
                                             body.credential, body.enabled, body.oauth_client_id,
                                             body.oauth_client_secret, body.triggers, body.stage, body.rate_limit))


@router.delete("/api/plugins/{name}/install")
def uninstall(name: str, request: Request, team: str | None = None, stage: bool = False, db=Depends(db_dep)):
    guard(lambda: svc.plugins.uninstall(db, acc(request, db), name, team, stage))
    return {"uninstalled": name, "team": team}


@router.post("/api/plugins/{name}/install/test")
def test(name: str, body: TeamBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.test_install(db, acc(request, db), name, body.team, body.stage))


@router.get("/api/plugins/{name}/install/logs")
def server_logs(name: str, request: Request, team: str | None = None, stage: bool = False, db=Depends(db_dep)):
    """Plugin com código: as últimas linhas do container da instalação (segredos mascarados)."""
    return guard(lambda: svc.plugins.server_logs(db, acc(request, db), name, team, stage))


@router.post("/api/plugins/{name}/tests")
def stage_tests(name: str, body: TeamBody, request: Request, db=Depends(db_dep)):
    """Roda os testes do rascunho contra a instalação de stage; o relatório fica preso a esta versão do manifesto."""
    return guard(lambda: svc.plugins.run_stage_tests(db, acc(request, db), name, body.team))


@router.post("/api/plugins/{name}/feature")
def feature(name: str, body: FeatureBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.set_featured(db, acc(request, db), name, body.on))


@router.post("/api/plugins/{name}/requests")
def request_install(name: str, body: RequestBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.request_install(db, acc(request, db), name, body.team, body.note))


@router.delete("/api/plugins/{name}/requests")
def dismiss_request(name: str, team: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.plugins.dismiss_request(db, acc(request, db), name, team))


@router.get("/api/plugins/{name}/export")
def export(name: str, request: Request, draft: bool = False, db=Depends(db_dep)):
    text = guard(lambda: svc.plugins.export(db, acc(request, db), name, draft))
    return Response(text, media_type="application/yaml",
                    headers={"Content-Disposition": f'attachment; filename="{name}.hangar-plugin.yaml"'})


@router.post("/api/plugins/{name}/install/hook-token")
def hook_token(name: str, body: TeamBody, request: Request, db=Depends(db_dep)):
    """Gera (ou troca) o token dos webhooks dos gatilhos: aparece uma vez só."""
    return guard(lambda: svc.plugins.new_hook_token(db, acc(request, db), name, body.team, body.stage))


@router.get("/api/plugins/{name}/calls")
def calls(name: str, request: Request, team: str | None = None, stage: bool = False, tool: str = "", status: str = "",
          limit: int = 100, before: int | None = None, db=Depends(db_dep)):
    """Auditoria por chamada da instalação: agente, ferramenta, quando, duração e resultado (segredos mascarados)."""
    return guard(lambda: svc.plugins.list_calls(db, acc(request, db), name, team, stage, tool, status, limit, before))


@router.post("/api/plugins/{name}/triggers/{trigger}/test")
def trigger_test(name: str, trigger: str, body: TriggerTestBody, request: Request, db=Depends(db_dep)):
    """Prévia do gatilho no rascunho: o título e o corpo da tarefa que o evento geraria (nada vira tarefa)."""
    return guard(lambda: svc.plugins.test_trigger(db, acc(request, db), name, trigger, body.event, body.team))


# ------------------------------------------------------------------ OAuth2: conectar a conta do time no sistema
@router.post("/api/plugins/{name}/install/connect")
def oauth_connect(name: str, body: TeamBody, request: Request, db=Depends(db_dep)):
    r = guard(lambda: svc.plugins.oauth_start(db, acc(request, db), name, body.team, body.stage))
    resp = JSONResponse({"authorize_url": r["authorize_url"]})
    resp.set_cookie(svc.plugins.OAUTH_COOKIE, r["cookie"], max_age=600, httponly=True, samesite="lax",
                    secure=config.COOKIE_SECURE, path="/api/plugins")
    return resp


@router.get("/api/plugins/oauth/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = "",
                   db=Depends(db_dep)):
    def back(name: str = "", msg: str = "") -> RedirectResponse:
        q = urllib.parse.urlencode({"oauth_error": msg} if msg else {"oauth_ok": "1"})
        resp = RedirectResponse(f"/app/#/plugins/{name}?{q}" if name else f"/app/#/plugins?{q}", 303)
        resp.delete_cookie(svc.plugins.OAUTH_COOKIE, path="/api/plugins")
        return resp
    if error:
        return back(msg=f"o provedor recusou a autorização ({error_description or error})"[:300])
    try:
        name = svc.plugins.oauth_finish(db, acc(request, db), code, state,
                                        request.cookies.get(svc.plugins.OAUTH_COOKIE, ""))
    except (svc.PlatformError, svc.access.Forbidden) as e:
        return back(msg=str(e)[:300])
    except Exception as e:  # noqa: BLE001 — state expirado (AuthError) e afins: volta para a tela com a mensagem
        return back(msg=str(e)[:300] or "falha ao conectar")
    return back(name)


# ------------------------------------------------------------------ servidor MCP interno (agente -> central -> sistema)
def _mcp(request: Request, name: str, msg: dict):
    with SessionLocal() as db:
        a = caller_agent(request, db)
        try:
            task = request.headers.get("x-hangar-task", "")
            return svc.plugins.mcp_handle(db, a, caller_specs(a), name, msg, int(task) if task.isdigit() else None)
        except svc.access.Forbidden as e:
            raise HTTPException(403, str(e)) from None
        except svc.PlatformError as e:
            return {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32000, "message": str(e)}}


@router.api_route("/internal/plugins/{name}/mcp", methods=["GET", "POST", "DELETE"])
async def plugin_mcp(name: str, request: Request):
    if request.method != "POST":
        return Response(status_code=405)  # sem stream SSE nem sessão: cada chamada é independente
    try:
        msg = json.loads(await request.body() or b"{}")
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}}, 400)
    if not isinstance(msg, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "lotes não suportados"}}, 400)
    out = await run_in_threadpool(_mcp, request, name, msg)
    return JSONResponse(out) if out is not None else Response(status_code=202)


# ------------------------------------------------------------------ gatilhos (webhook do sistema; autenticado pela
# assinatura HMAC declarada no manifesto ou pelo token da instalação)
@hooks.post("/plugins/{install_id}/{trigger}")
async def trigger(install_id: int, trigger: str, request: Request, token: str = ""):
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    def go():
        with SessionLocal() as db:
            return svc.plugins.receive(db, install_id, trigger, raw, headers, token)
    try:
        return await run_in_threadpool(go)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(409, str(e)) from None

