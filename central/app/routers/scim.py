"""SCIM 2.0 (/scim/v2): o diretório da empresa (Entra ID, Okta, Google via conector…) cria, atualiza e desliga
usuários e grupos. Grupos viram times pelos mapeamentos do provedor "scim" (página SSO e SCIM). Desligar um
usuário (active=false ou DELETE) encerra as sessões e revoga as chaves dele na hora.

Autenticação: chave de API com escopo "scim" (Bearer)."""
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from .. import config, sso
from ..models import ScimGroup, User
from ..services import org
from ..services.common import PlatformError, audit, iso
from ..services.workspace import check_email
from .deps import actor, db_dep

router = APIRouter(prefix="/scim/v2")

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
MEDIA = "application/scim+json"
_FILTER = re.compile(r'^\s*([\w.]+)\s+eq\s+"([^"]*)"\s*$', re.I)


def _resp(data, status=200):
    return JSONResponse(data, status, media_type=MEDIA)


def _error(status: int, detail: str, scim_type: str | None = None):
    body = {"schemas": [ERROR_SCHEMA], "status": str(status), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return _resp(body, status)


def _base() -> str:
    return f"{config.PUBLIC_BASE_URL}/scim/v2"


def _truthy(v) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1", "yes")


# ------------------------------------------------------------------ recursos
def user_resource(u: User) -> dict:
    return {"schemas": [USER_SCHEMA], "id": str(u.id), "externalId": u.external_id, "userName": u.email,
            "displayName": u.name, "name": {"formatted": u.name},
            "emails": [{"value": u.email, "primary": True, "type": "work"}], "active": u.active,
            "meta": {"resourceType": "User", "created": iso(u.created_at), "location": f"{_base()}/Users/{u.id}"}}


def group_resource(db, g: ScimGroup) -> dict:
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(g.members or [])))}
    return {"schemas": [GROUP_SCHEMA], "id": str(g.id), "externalId": g.external_id, "displayName": g.display_name,
            "members": [{"value": str(i), "display": users[i].email} for i in g.members or [] if i in users],
            "meta": {"resourceType": "Group", "created": iso(g.created_at), "location": f"{_base()}/Groups/{g.id}"}}


def _list(items: list, start: int, count: int) -> dict:
    start = max(start, 1)
    page = items[start - 1:start - 1 + max(count, 0)]
    return {"schemas": [LIST_SCHEMA], "totalResults": len(items), "startIndex": start, "itemsPerPage": len(page),
            "Resources": page}


def _corporate(db, email: str):
    """None se o e-mail serve à empresa; senão a resposta SCIM de recusa (a empresa exige e-mail corporativo)."""
    try:
        check_email(db, email)
    except PlatformError as e:
        return _error(400, str(e), "invalidValue")
    return None


def _email_of(body: dict) -> str:
    emails = body.get("emails") or []
    primary = next((e.get("value") for e in emails if e.get("primary")), None)
    return (body.get("userName") or primary or (emails[0].get("value") if emails else "") or "").strip().lower()


def _name_of(body: dict) -> str:
    n = body.get("name") or {}
    return body.get("displayName") or n.get("formatted") or " ".join(
        x for x in (n.get("givenName"), n.get("familyName")) if x) or ""


def sync_user_groups(db, user_id: int):
    """Times do usuário a partir dos grupos SCIM em que ele está (mapeamentos provider=scim)."""
    u = db.get(User, user_id)
    if not u:
        return
    names = [g.display_name for g in db.scalars(select(ScimGroup)) if user_id in (g.members or [])]
    names += [str(g.id) for g in db.scalars(select(ScimGroup)) if user_id in (g.members or [])]
    sso.sync_groups(db, u, "scim", "scim", names)


def _set_active(db, u: User, active: bool, who: str):
    if active and not u.active:
        u.active = True
        db.commit()
        audit(db, who, "scim.user.activate", u.email)
    elif not active and u.active:
        org.deactivate(db, u, who)
        audit(db, who, "scim.user.deactivate", u.email)


# ------------------------------------------------------------------ descoberta
@router.get("/ServiceProviderConfig")
def service_provider_config():
    return _resp({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                  "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                  "filter": {"supported": True, "maxResults": 200}, "changePassword": {"supported": False},
                  "sort": {"supported": False}, "etag": {"supported": False},
                  "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer",
                                             "description": "Chave de API do Agent Hangar com escopo scim"}]})


@router.get("/ResourceTypes")
def resource_types():
    return _resp(_list([
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User",
         "endpoint": "/Users", "schema": USER_SCHEMA},
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "Group", "name": "Group",
         "endpoint": "/Groups", "schema": GROUP_SCHEMA}], 1, 10))


@router.get("/Schemas")
def schemas():
    return _resp(_list([{"id": USER_SCHEMA, "name": "User"}, {"id": GROUP_SCHEMA, "name": "Group"}], 1, 10))


# ------------------------------------------------------------------ usuários
@router.get("/Users")
def list_users(filter: str = "", startIndex: int = 1, count: int = 100, db=Depends(db_dep)):
    users = db.scalars(select(User).order_by(User.id)).all()
    if filter:
        m = _FILTER.match(filter)
        if not m:
            return _error(400, "filtro não suportado (use: atributo eq \"valor\")", "invalidFilter")
        attr, val = m.group(1).lower(), m.group(2)
        pick = {"username": lambda u: u.email == val.lower(), "emails.value": lambda u: u.email == val.lower(),
                "externalid": lambda u: u.external_id == val, "id": lambda u: str(u.id) == val}.get(attr)
        if not pick:
            return _error(400, f"filtro por {attr} não suportado", "invalidFilter")
        users = [u for u in users if pick(u)]
    return _resp(_list([user_resource(u) for u in users], startIndex, count))


@router.get("/Users/{uid}")
def get_user(uid: int, db=Depends(db_dep)):
    u = db.get(User, uid)
    return _resp(user_resource(u)) if u else _error(404, "usuário não encontrado")


@router.post("/Users")
async def create_user(request: Request, db=Depends(db_dep)):
    body = await request.json()
    email = _email_of(body)
    if "@" not in email:
        return _error(400, "userName/emails precisa ser um e-mail", "invalidValue")
    u = org.user_by_email(db, email)
    if u and u.external_id:
        return _error(409, "usuário já existe", "uniqueness")
    if not u:
        if (refused := _corporate(db, email)) is not None:
            return refused
        u = User(email=email)
        db.add(u)
    u.name = _name_of(body) or u.name or email.split("@")[0]
    u.external_id = body.get("externalId") or u.external_id or email
    db.commit()
    who = actor(request)
    audit(db, who, "scim.user.create", u.email)
    if "active" in body:
        _set_active(db, u, _truthy(body["active"]), who)
    return _resp(user_resource(u), 201)


@router.put("/Users/{uid}")
async def replace_user(uid: int, request: Request, db=Depends(db_dep)):
    u = db.get(User, uid)
    if not u:
        return _error(404, "usuário não encontrado")
    body = await request.json()
    email = _email_of(body)
    if email and email != u.email:
        if org.user_by_email(db, email):
            return _error(409, "e-mail já usado por outro usuário", "uniqueness")
        if (refused := _corporate(db, email)) is not None:
            return refused
        u.email = email
    u.name = _name_of(body) or u.name
    u.external_id = body.get("externalId") or u.external_id
    db.commit()
    _set_active(db, u, _truthy(body.get("active", True)), actor(request))
    return _resp(user_resource(u))


@router.patch("/Users/{uid}")
async def patch_user(uid: int, request: Request, db=Depends(db_dep)):
    u = db.get(User, uid)
    if not u:
        return _error(404, "usuário não encontrado")
    body = await request.json()
    who = actor(request)
    for op in body.get("Operations", []):
        kind, path, value = (op.get("op") or "").lower(), (op.get("path") or ""), op.get("value")
        if kind not in ("add", "replace", "remove"):
            return _error(400, f"operação não suportada: {kind}", "invalidSyntax")
        changes = value if not path and isinstance(value, dict) else {path: value}
        for key, v in changes.items():
            k = key.lower()
            if k == "active":
                _set_active(db, u, _truthy(v) if kind != "remove" else False, who)
            elif k in ("username", 'emails[type eq "work"].value', "emails"):
                v = v[0].get("value") if isinstance(v, list) and v else v
                if isinstance(v, str) and "@" in v and v.lower() != u.email:
                    if org.user_by_email(db, v):
                        return _error(409, "e-mail já usado por outro usuário", "uniqueness")
                    if (refused := _corporate(db, v.lower())) is not None:
                        return refused
                    u.email = v.lower()
            elif k in ("displayname", "name.formatted"):
                u.name = v or u.name
            elif k == "name" and isinstance(v, dict):
                u.name = _name_of({"name": v}) or u.name
            elif k == "externalid":
                u.external_id = v
            # givenName/familyName isolados e atributos desconhecidos: ignorados (o Entra manda vários)
    db.commit()
    return _resp(user_resource(u))


@router.delete("/Users/{uid}")
def delete_user(uid: int, request: Request, db=Depends(db_dep)):
    """Desliga (a conta fica para a auditoria): sessões encerradas e chaves revogadas."""
    u = db.get(User, uid)
    if not u:
        return _error(404, "usuário não encontrado")
    _set_active(db, u, False, actor(request))
    for g in db.scalars(select(ScimGroup)):
        if uid in (g.members or []):
            g.members = [m for m in g.members if m != uid]
    db.commit()
    sync_user_groups(db, uid)
    return Response(status_code=204)


# ------------------------------------------------------------------ grupos
def _member_ids(value) -> list[int]:
    items = value if isinstance(value, list) else [value]
    out = []
    for it in items:
        v = it.get("value") if isinstance(it, dict) else it
        if str(v).isdigit():
            out.append(int(v))
    return out


@router.get("/Groups")
def list_groups(filter: str = "", startIndex: int = 1, count: int = 100, db=Depends(db_dep)):
    groups = db.scalars(select(ScimGroup).order_by(ScimGroup.id)).all()
    if filter:
        m = _FILTER.match(filter)
        if not m:
            return _error(400, "filtro não suportado", "invalidFilter")
        attr, val = m.group(1).lower(), m.group(2)
        pick = {"displayname": lambda g: g.display_name == val, "externalid": lambda g: g.external_id == val,
                "id": lambda g: str(g.id) == val}.get(attr)
        if not pick:
            return _error(400, f"filtro por {attr} não suportado", "invalidFilter")
        groups = [g for g in groups if pick(g)]
    return _resp(_list([group_resource(db, g) for g in groups], startIndex, count))


@router.get("/Groups/{gid}")
def get_group(gid: int, db=Depends(db_dep)):
    g = db.get(ScimGroup, gid)
    return _resp(group_resource(db, g)) if g else _error(404, "grupo não encontrado")


@router.post("/Groups")
async def create_group(request: Request, db=Depends(db_dep)):
    body = await request.json()
    if not body.get("displayName"):
        return _error(400, "displayName obrigatório", "invalidValue")
    if db.scalar(select(ScimGroup).where(ScimGroup.display_name == body["displayName"])):
        return _error(409, "grupo já existe", "uniqueness")
    g = ScimGroup(display_name=body["displayName"], external_id=body.get("externalId"),
                  members=sorted(set(_member_ids(body.get("members") or []))))
    db.add(g)
    db.commit()
    audit(db, actor(request), "scim.group.create", g.display_name)
    for uid in g.members:
        sync_user_groups(db, uid)
    return _resp(group_resource(db, g), 201)


@router.put("/Groups/{gid}")
async def replace_group(gid: int, request: Request, db=Depends(db_dep)):
    g = db.get(ScimGroup, gid)
    if not g:
        return _error(404, "grupo não encontrado")
    body = await request.json()
    before = set(g.members or [])
    g.display_name = body.get("displayName") or g.display_name
    g.external_id = body.get("externalId") or g.external_id
    g.members = sorted(set(_member_ids(body.get("members") or [])))
    db.commit()
    for uid in before | set(g.members):
        sync_user_groups(db, uid)
    return _resp(group_resource(db, g))


_MEMBER_PATH = re.compile(r'^members\[value eq "?(\d+)"?\]$', re.I)


@router.patch("/Groups/{gid}")
async def patch_group(gid: int, request: Request, db=Depends(db_dep)):
    g = db.get(ScimGroup, gid)
    if not g:
        return _error(404, "grupo não encontrado")
    body = await request.json()
    before = set(g.members or [])
    members = set(before)
    renamed = False
    for op in body.get("Operations", []):
        kind, path, value = (op.get("op") or "").lower(), (op.get("path") or ""), op.get("value")
        m = _MEMBER_PATH.match(path)
        if path.lower() == "members" or m or (not path and isinstance(value, dict) and "members" in value):
            ids = [int(m.group(1))] if m else _member_ids(value.get("members") if isinstance(value, dict)
                                                          and not path else value or [])
            if kind == "add":
                members |= set(ids)
            elif kind == "remove":
                members -= set(ids) if ids else members
            elif kind == "replace":
                members = set(ids)
        elif path.lower() == "displayname" or (not path and isinstance(value, dict) and "displayName" in value):
            g.display_name = value["displayName"] if isinstance(value, dict) else value
            renamed = True
        elif path.lower() == "externalid":
            g.external_id = value
    g.members = sorted(members)
    db.commit()
    for uid in (before | members) if renamed else (before ^ members):
        sync_user_groups(db, uid)
    return Response(status_code=204)


@router.delete("/Groups/{gid}")
def delete_group(gid: int, request: Request, db=Depends(db_dep)):
    g = db.get(ScimGroup, gid)
    if not g:
        return _error(404, "grupo não encontrado")
    members = list(g.members or [])
    db.delete(g)
    db.commit()
    audit(db, actor(request), "scim.group.delete", g.display_name)
    for uid in members:
        sync_user_groups(db, uid)
    return Response(status_code=204)
