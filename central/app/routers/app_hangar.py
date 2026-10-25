"""API do app Hangar (desktop): /api/app com o token OAuth de escopo `app`, e /api/app/admin para o console
(contrato em docs/app-hangar.md; regras em services/app_hangar.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import auth
from ..services import app_events, app_llm
from ..services import app_hangar as ah
from .deps import actor, db_dep, guard, principal, require_admin

router = APIRouter(prefix="/api/app")


def _guard(fn):
    def run():
        try:
            return fn()
        except ah.NotFound as e:
            raise HTTPException(404, str(e)) from None
    return guard(run)


class DeviceBody(BaseModel):
    device_id: str
    name: str = ""
    os: str = ""
    os_version: str = ""
    app_version: str = ""
    channel: str = ""


class HeartbeatBody(BaseModel):
    app_version: str = ""


@router.get("/me")
def me(request: Request, db=Depends(db_dep)):
    return guard(lambda: ah.me(db, principal(request)))


@router.post("/devices")
def register_device(body: DeviceBody, request: Request, db=Depends(db_dep)):
    return _guard(lambda: ah.register_device(db, principal(request), body.model_dump()))


@router.post("/devices/{device_id}/heartbeat")
def heartbeat(device_id: str, request: Request, body: HeartbeatBody | None = None, db=Depends(db_dep)):
    """Também atende o token de uma autorização já encerrada (o middleware deixa passar): responde `revoked`."""
    p = request.scope.get("state", {}).get("principal")
    token = auth.token_from_headers({k.lower(): v for k, v in request.headers.items()})
    try:
        return _guard(lambda: ah.heartbeat(db, p, token, device_id, body.app_version if body else ""))
    except ah.Unauthorized as e:
        raise HTTPException(401, str(e)) from None


@router.post("/devices/{device_id}/llm-key")
def llm_key(device_id: str, request: Request, db=Depends(db_dep)):
    """Gera ou troca a chave do proxy de LLM do dispositivo (ahl_…, aparece só aqui): a anterior deixa de valer."""
    return _guard(lambda: app_llm.rotate_key(db, principal(request), device_id))


@router.get("/models")
def models(request: Request, db=Depends(db_dep)):
    """Os modelos de token sincronizados com o Hangar desktop (docs/app-hangar-models.md)."""
    return guard(lambda: app_llm.models_for_app(db, principal(request)))


@router.get("/events")
async def events(request: Request):
    """Eventos da pessoa em SSE (docs/app-hangar-workspace.md, seção 2): retoma com Last-Event-ID, keepalive a cada
    25 s, fecha depois de 30 min (o cliente reconecta)."""
    p = principal(request)
    if not p.user_id:
        raise HTTPException(403, "os eventos são de uma pessoa (entre com a sua conta)")
    headers = {k.lower(): v for k, v in request.headers.items()}
    token = auth.token_from_headers(headers) or auth.cookie(headers, auth.SESSION_COOKIE)
    last = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    return StreamingResponse(app_events.stream(request, p, token, last), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/design-tokens")
def design_tokens():
    return ah.design_tokens()


# ------------------------------------------------------------------ console (admin)
@router.get("/admin/overview", dependencies=[Depends(require_admin)])
def admin_overview(db=Depends(db_dep)):
    return ah.admin_overview(db)


@router.get("/admin/devices", dependencies=[Depends(require_admin)])
def admin_devices(db=Depends(db_dep)):
    return ah.admin_devices(db)


@router.post("/admin/devices/{device_pk}/revoke", dependencies=[Depends(require_admin)])
def admin_revoke(device_pk: int, request: Request, db=Depends(db_dep)):
    return _guard(lambda: ah.admin_revoke(db, actor(request), device_pk))
