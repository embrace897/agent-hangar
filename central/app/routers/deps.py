"""Peças comuns dos routers: sessão de banco, quem está chamando, permissões e erros -> HTTP."""
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.routing import APIRoute

from .. import auth
from .. import services as svc
from ..db import SessionLocal
from ..services import access


def db_dep():
    with SessionLocal() as db:
        yield db


def principal(request: Request) -> auth.Principal:
    p = request.scope.get("state", {}).get("principal")
    if p is None:
        raise HTTPException(401, "não autenticado")
    return p


def actor(request: Request) -> str:
    p = request.scope.get("state", {}).get("principal")
    return p.name if p else "admin"


def acc(request: Request, db) -> access.Access:
    return access.of(db, principal(request))


def guard(fn):
    try:
        return fn()
    except access.Forbidden as e:
        raise HTTPException(403, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(400, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


def require_admin(request: Request):
    if not principal(request).is_admin:
        raise HTTPException(403, "só admins da plataforma")


def require_auditor(request: Request):
    if not principal(request).is_auditor:
        raise HTTPException(403, "só admins e auditores")


APP_DENIED = "forbidden: o token do app Hangar não vale nesta rota"


def member_route(allow_app: bool = False, when: Callable[[Request], bool] | None = None, why: str = ""):
    """Marca uma rota do membro. Com allow_app, o token OAuth de escopo `app` (o Hangar desktop) também entra, sempre
    com o acesso da própria pessoa; `when` restringe a que chamadas (ex.: org-chart só com mine=1). O middleware só
    deixa um token OAuth chegar a rotas /api com esta marca (main._app_route); a dependência confere de novo.
    Contrato e lista: docs/app-hangar-workspace.md, seção 1."""
    def member(request: Request):
        p = principal(request)
        if p.via != "oauth":
            return
        if not allow_app or "app" not in p.oauth_scopes or (when is not None and not when(request)):
            raise HTTPException(403, why or APP_DENIED)
    member.allow_app = allow_app
    return Depends(member)


def api_routes(routes):
    """As APIRoute de uma aplicação, na ordem em que o roteador as testa (o FastAPI 0.14x inclui routers de forma
    preguiçosa: as rotas ficam no router original)."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif getattr(r, "original_router", None) is not None:
            yield from api_routes(r.original_router.routes)


def app_allowed(route: APIRoute) -> bool:
    return any(getattr(d.call, "allow_app", False) for d in route.dependant.dependencies)


def query_flag(name: str) -> Callable[[Request], bool]:
    """`when` de member_route: o parâmetro de consulta ligado (1, true, yes, on), como o FastAPI lê um bool."""
    return lambda request: request.query_params.get(name, "").lower() in ("1", "true", "yes", "on")
