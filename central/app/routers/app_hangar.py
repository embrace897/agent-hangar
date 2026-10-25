"""API do app Hangar (desktop): /api/app com o token OAuth de escopo `app`, e /api/app/admin para o console
(contrato em docs/app-hangar.md; regras em services/app_hangar.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import auth
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
