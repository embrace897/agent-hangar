"""Decisões fora do portal: configuração (admin e pessoa), botões do Slack e o link assinado (Teams, e-mail)."""
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import services as svc
from .deps import acc, db_dep, guard

router = APIRouter(prefix="/api")
hooks = APIRouter(prefix="/hooks")
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


class OrgNotifyBody(BaseModel):
    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    teams_outgoing_secret: str | None = None
    telegram_bot_token: str | None = None
    discord_app_id: str | None = None
    discord_public_key: str | None = None
    discord_bot_token: str | None = None
    digest_hour: int | None = None
    expiry_warn_min: int | None = None
    overdue_escalate_min: int | None = None
    due_soon_min: int | None = None


class MyNotifyBody(BaseModel):
    channel: str | None = None
    teams_webhook: str | None = None
    digest: bool | None = None


@router.get("/admin/notifications")
def org_get(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.notify.admin_view(db, acc(request, db)))


@router.put("/admin/notifications")
def org_set(body: OrgNotifyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.notify.set_admin(db, acc(request, db), body.model_dump()))


@router.get("/me/notifications")
def my_get(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.notify.my_view(db, acc(request, db)))


@router.put("/me/notifications")
def my_set(body: MyNotifyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.notify.set_mine(db, acc(request, db), body.model_dump()))


@router.post("/me/notifications/test")
def my_test(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.notify.test_mine(db, acc(request, db)))


# ------------------------------------------------------------------ Slack (assinado pelo Slack, não por sessão)
@hooks.post("/slack/interactions")
async def slack(request: Request, db=Depends(db_dep)):
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        out = await run_in_threadpool(svc.notify.slack_interaction, db, raw, headers)  # chama a API do Slack
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(409, str(e)) from None
    return JSONResponse(out) if out else Response(status_code=200)


@hooks.post("/slack/events")
async def slack_events(request: Request, db=Depends(db_dep)):
    """Events API: menção ao app ou mensagem direta vira tarefa de um Digital employee."""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        out = await run_in_threadpool(svc.chat_tasks.slack_event, db, raw, headers)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    return JSONResponse(out) if out else Response(status_code=200)


@hooks.post("/teams/messages")
async def teams_messages(request: Request, db=Depends(db_dep)):
    """Webhook de saída do Teams (canal): a menção vira tarefa; a resposta vai na hora, o resultado depois."""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        return JSONResponse(await run_in_threadpool(svc.chat_tasks.teams_message, db, raw, headers))
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None


# ------------------------------------------------------------------ Telegram e Discord (cada um com a própria assinatura)
class ChatLinkBody(BaseModel):
    channel: str = "telegram"  # telegram | discord


@router.post("/me/notifications/chat-link")
def chat_link(body: ChatLinkBody, request: Request, db=Depends(db_dep)):
    """Código de uso único para ligar a sua conta do Telegram ou do Discord (vale 15 minutos)."""
    return guard(lambda: svc.chat_channels.link_code(db, acc(request, db), body.channel))


@router.delete("/me/notifications/chat-link/{channel}")
def chat_unlink(channel: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.chat_channels.unlink(db, acc(request, db), channel))


@hooks.post("/telegram/{org_id}")
async def telegram(org_id: int, request: Request, db=Depends(db_dep)):
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        await run_in_threadpool(svc.chat_channels.telegram_update, db, org_id, raw, headers)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    return Response(status_code=200)


@hooks.post("/discord/{org_id}")
async def discord(org_id: int, request: Request, db=Depends(db_dep)):
    """Interactions endpoint do Discord: o comando /hangar vira tarefa; a resposta vai na hora."""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        return JSONResponse(await run_in_threadpool(svc.chat_channels.discord_interaction, db, org_id, raw, headers))
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None


# ------------------------------------------------------------------ link assinado: GET mostra, só POST decide
def _html(fn):
    try:
        return HTMLResponse(fn(), headers=NO_STORE)
    except svc.access.Forbidden as e:
        return HTMLResponse(svc.notify.PAGE.format(lang="pt-BR", rid="", body=f"<h1>⛔</h1><p>{e}</p>"), 403,
                            headers=NO_STORE)
    except svc.PlatformError as e:
        return HTMLResponse(svc.notify.PAGE.format(lang="pt-BR", rid="", body=f"<h1>⚠️</h1><p>{e}</p>"), 409,
                            headers=NO_STORE)


@hooks.get("/decide/{token}")
def link_get(token: str, d: str = "", db=Depends(db_dep)):
    return _html(lambda: svc.notify.link_page(db, token, d))


@hooks.post("/decide/{token}")
async def link_post(token: str, request: Request, db=Depends(db_dep)):
    form = {k: v[0] for k, v in urllib.parse.parse_qs((await request.body()).decode()).items()}
    return await run_in_threadpool(_html, lambda: svc.notify.link_post(db, token, form))
