"""OAuth 2.1 do MCP da plataforma (ver services/mcp_oauth.py): metadados, registro dinâmico, autorização pelo portal,
tokens e revogação; e a gestão dos apps conectados (pessoa e admin)."""
import html
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..services import mcp_oauth as oa
from .deps import acc, db_dep, guard

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _err(e: oa.OAuthError) -> JSONResponse:
    return JSONResponse(e.body(), e.status, headers=NO_STORE)


# ------------------------------------------------------------------ metadados (abertos)
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource():
    return oa.protected_resource_metadata()


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
@router.get("/.well-known/openid-configuration")
def authorization_server():
    return oa.server_metadata()


# ------------------------------------------------------------------ registro, autorização, token, revogação (abertos)
@router.post("/oauth/register")
async def register(request: Request, db=Depends(db_dep)):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return _err(oa.OAuthError("invalid_client_metadata", "corpo JSON esperado"))
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    try:
        return JSONResponse(oa.register(db, body, ip), 201, headers=NO_STORE)
    except oa.OAuthError as e:
        return _err(e)


def _error_page(msg: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Hangar · conexão recusada</title>
<body style="font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px;line-height:1.5">
<h2>Não foi possível conectar o app</h2><p>{html.escape(msg)}</p>
<p style="color:#666">Comece de novo pelo app (Claude, ChatGPT…). Se o erro continuar, fale com o administrador do Agent Hangar.</p>""",
                        status)


@router.get("/oauth/authorize")
def authorize_start(request: Request, db=Depends(db_dep)):
    """Valida o pedido e leva a pessoa à tela de consentimento do portal (que pede login antes, se preciso)."""
    q = dict(request.query_params)
    try:
        c, clean = oa.check_request(db, q)
    except oa.OAuthError as e:
        if e.error in ("invalid_client", "access_denied") or "redirect_uri" in e.description:
            return _error_page(e.description, e.status)
        c = db.get(oa.OAuthClient, q.get("client_id", ""))
        redirect = q.get("redirect_uri", "")
        if c and oa.redirect_registered(c, redirect):  # app e redirect válidos: o erro volta para o app (RFC 6749 4.1.2.1)
            return RedirectResponse(oa.redirect_with(redirect, {"error": e.error, "error_description": e.description,
                                                                "state": q.get("state", "")}), 302)
        return _error_page(e.description, e.status)
    keep = {k: q[k] for k in ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method",
                              "response_type", "resource", "scope") if q.get(k)}
    return RedirectResponse("/app/#/oauth?" + urllib.parse.urlencode(keep), 302)


@router.post("/oauth/token")
async def token(request: Request, db=Depends(db_dep)):
    form = dict(await request.form())
    try:
        return JSONResponse(oa.token(db, form), headers=NO_STORE)
    except oa.OAuthError as e:
        return _err(e)


@router.post("/oauth/revoke")
async def revoke(request: Request, db=Depends(db_dep)):
    form = dict(await request.form())
    oa.revoke_token(db, form.get("token", ""))
    return JSONResponse({}, headers=NO_STORE)


# ------------------------------------------------------------------ portal (logado): consentimento e apps conectados
class Consent(BaseModel):
    params: dict
    approve: bool = True


@router.get("/api/oauth/consent")
def consent_info(request: Request, db=Depends(db_dep)):
    try:
        return oa.consent_info(db, acc(request, db), dict(request.query_params))
    except oa.OAuthError as e:
        raise HTTPException(400, e.description) from None


@router.post("/api/oauth/consent")
def consent(body: Consent, request: Request, db=Depends(db_dep)):
    try:
        return guard(lambda: oa.authorize(db, acc(request, db), {k: str(v) for k, v in body.params.items()}, body.approve))
    except oa.OAuthError as e:
        raise HTTPException(400, e.description) from None


@router.get("/api/me/oauth")
def my_apps(request: Request, db=Depends(db_dep)):
    return guard(lambda: oa.my_grants(db, acc(request, db)))


@router.delete("/api/me/oauth/{grant_id}")
def revoke_app(grant_id: int, request: Request, db=Depends(db_dep)):
    guard(lambda: oa.revoke_grant(db, acc(request, db), grant_id))
    return {"ok": True}


@router.get("/api/oauth/clients")
def clients(request: Request, db=Depends(db_dep)):
    return guard(lambda: oa.clients(db, acc(request, db)))


@router.delete("/api/oauth/clients/{client_id}")
def block(client_id: str, request: Request, db=Depends(db_dep)):
    guard(lambda: oa.block_client(db, acc(request, db), client_id))
    return {"ok": True}
