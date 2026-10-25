"""Login (OAuth2), sessão, empresa, times, usuários, pedidos de acesso, aprovações e configuração de SSO."""
import secrets
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from .. import auth, config, sso
from .. import services as svc
from ..services import org
from .deps import acc, actor, db_dep, guard, member_route, principal, require_admin

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ cookies
def _set_session(resp, raw: str):
    kw = {"secure": config.COOKIE_SECURE, "samesite": "lax", "path": "/", "max_age": config.SESSION_TTL_HOURS * 3600}
    resp.set_cookie(auth.SESSION_COOKIE, raw, httponly=True, **kw)
    # double-submit: a UI lê este cookie e manda no header X-CSRF-Token em toda escrita
    resp.set_cookie(auth.CSRF_COOKIE, secrets.token_urlsafe(24), httponly=False, **kw)


def _clear(resp):
    for c in (auth.SESSION_COOKIE, auth.CSRF_COOKIE):
        resp.delete_cookie(c, path="/")
    resp.delete_cookie(sso.STATE_COOKIE, path="/api/auth")


# ------------------------------------------------------------------ login
class TokenLogin(BaseModel):
    token: str


class VscodeAuthorize(BaseModel):
    challenge: str
    state: str
    device: str = ""


class VscodeToken(BaseModel):
    code: str
    verifier: str


class PasswordLogin(BaseModel):
    username: str  # usuário ou e-mail
    password: str


class PasswordChange(BaseModel):
    current: str
    new: str


@router.get("/auth/providers")
def providers(db=Depends(db_dep)):
    return {"providers": sso.public_providers(db), "token_login": True, "password_login": config.LOCAL_LOGIN}


def _client(request: Request) -> dict:
    return {"user_agent": request.headers.get("user-agent", ""), "ip": request.client.host if request.client else ""}


@router.post("/auth/password")
def password_login(body: PasswordLogin, request: Request, db=Depends(db_dep)):
    """Conta local: usuário (ou e-mail) e senha -> sessão em cookie."""
    user = guard(lambda: org.password_login(db, body.username, body.password))
    guard(lambda: svc.workspace.check_login(db, user, "password"))
    resp = JSONResponse({"ok": True})
    _set_session(resp, auth.create_session(db, user, method="password", **_client(request)))
    return resp


class ActivateBody(BaseModel):
    code: str


@router.post("/auth/activate")
def activate(body: ActivateBody, request: Request, db=Depends(db_dep)):
    """Código de ativação (a "chave virtual"): uma entrada, sem senha."""
    try:
        user = svc.workspace.redeem(db, body.code)
    except svc.access.Forbidden as e:
        raise HTTPException(401, str(e)) from None
    except svc.PlatformError as e:
        raise HTTPException(403, str(e)) from None
    org.audit(db, user.email, "auth.activation", user.email, "código de ativação")
    resp = JSONResponse({"ok": True})
    _set_session(resp, auth.create_session(db, user, method="activation", **_client(request)))
    return resp


@router.post("/me/password")
def change_password(body: PasswordChange, request: Request, db=Depends(db_dep)):
    guard(lambda: org.change_password(db, acc(request, db), body.current, body.new))
    return {"ok": True}


@router.get("/auth/login/{provider}")
def login(provider: str, next: str = "#/", db=Depends(db_dep)):
    try:
        url, state = sso.start(db, provider, next)
    except sso.AuthError as e:
        return RedirectResponse("/ui/#/login?error=" + urllib.parse.quote(str(e)), 303)
    resp = RedirectResponse(url, 303)
    resp.set_cookie(sso.STATE_COOKIE, state, max_age=600, httponly=True, secure=config.COOKIE_SECURE,
                    samesite="lax", path="/api/auth")
    return resp


@router.get("/auth/callback/{provider}")
def callback(provider: str, request: Request, code: str = "", state: str = "", error: str = "",
             error_description: str = "", db=Depends(db_dep)):
    try:
        if error:
            raise sso.AuthError(f"o provedor recusou o login ({error_description or error})")
        user, nxt = sso.finish(db, provider, code, state, request.cookies.get(sso.STATE_COOKIE, ""))
        try:
            svc.workspace.check_login(db, user, "sso")
        except svc.PlatformError as e:
            raise sso.AuthError(str(e)) from None
    except sso.AuthError as e:
        resp = RedirectResponse("/ui/#/login?error=" + urllib.parse.quote(str(e)[:300]), 303)
        resp.delete_cookie(sso.STATE_COOKIE, path="/api/auth")
        return resp
    resp = RedirectResponse("/ui/" + sso.safe_next(nxt), 303)
    resp.delete_cookie(sso.STATE_COOKIE, path="/api/auth")
    _set_session(resp, auth.create_session(db, user, method="sso", **_client(request)))
    return resp


@router.post("/auth/token")
def token_login(body: TokenLogin, request: Request, db=Depends(db_dep)):
    """Entrar com token: ADMIN_TOKEN (emergência), chave admin ou token pessoal ("user"). Abre uma sessão em
    cookie — o token não fica guardado no navegador."""
    p = auth.authenticate(db, body.token.strip())
    if p is None:
        raise HTTPException(401, "token inválido")
    if p.is_user:
        from ..models import User
        u = db.get(User, p.user_id)
        guard(lambda: svc.workspace.check_login(db, u, "key"))
        raw = auth.create_session(db, u, method="key", **_client(request))
    elif "admin" in p.scopes:
        raw = auth.create_session(db, None, admin=True, label=p.name)
        org.audit(db, p.name, "auth.token_login", "admin", "sessão de emergência")
    else:
        raise HTTPException(403, "esta chave não dá acesso à UI (use uma chave admin ou um token pessoal)")
    resp = JSONResponse({"ok": True})
    _set_session(resp, raw)
    return resp


@router.post("/auth/logout")
def logout(request: Request, db=Depends(db_dep)):
    auth.end_session(db, request.cookies.get(auth.SESSION_COOKIE, ""))
    resp = JSONResponse({"ok": True})
    _clear(resp)
    return resp


@router.get("/me")
def me(request: Request, db=Depends(db_dep)):
    return org.me(db, acc(request, db))


@router.post("/vscode/authorize")
def vscode_authorize(body: VscodeAuthorize, request: Request, db=Depends(db_dep)):
    """Portal (logado): confirma a conexão do VS Code e devolve o código de uso único para o redirect vscode://."""
    from .. import services as svc
    return guard(lambda: svc.vscode.authorize(acc(request, db), body.challenge, body.state, body.device))


@router.post("/auth/vscode/token")
def vscode_token(body: VscodeToken, db=Depends(db_dep)):
    """Extensão: troca código + verifier (PKCE) pelo token pessoal. Aberta: o código assinado é a credencial."""
    from .. import services as svc
    return guard(lambda: svc.vscode.exchange(db, body.code, body.verifier))


@router.get("/me/home")
def me_home(request: Request, db=Depends(db_dep)):
    """Página Início do portal do usuário (/app): atenção, agentes, pedidos, uso, agendamentos, conexões."""
    from .. import services as svc
    return guard(lambda: svc.home.home(db, acc(request, db)))


# ------------------------------------------------------------------ empresa
class OrgBody(BaseModel):
    name: str | None = None
    default_visibility: str | None = None
    timezone: str | None = None  # fuso padrão dos agendamentos (ex.: America/Sao_Paulo)
    code_policy: str | None = None  # any | approved (conexões de LLM aprovadas para receber código)


@router.get("/org")
def get_org(db=Depends(db_dep)):
    return org.org_dict(db)


@router.patch("/org")
def patch_org(body: OrgBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: org.update_org(db, acc(request, db), body.name, body.default_visibility,
                                                    body.timezone, body.code_policy))


# ------------------------------------------------------------------ times e membros
class TeamBody(BaseModel):
    name: str
    slug: str = ""
    description: str = ""
    require_approval: bool = True
    maintainer: str | None = None  # e-mail ou usuário: já entra como mantenedor


class TeamPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    require_approval: bool | None = None
    budget_usd_month: float | None = None
    budget_enforce: bool | None = None


class MemberBody(BaseModel):
    email: str
    role: str = "consumer"


class RoleBody(BaseModel):
    role: str


@router.get("/teams")
def teams(request: Request, db=Depends(db_dep)):
    return org.list_teams(db, acc(request, db))


@router.post("/teams")
def create_team(body: TeamBody, request: Request, db=Depends(db_dep)):
    a = acc(request, db)
    return guard(lambda: org.team_dict(db, org.create_team(db, a, body.name, body.slug, body.description,
                                                           body.require_approval, body.maintainer), a))


@router.get("/teams/{team}")
def team(team: str, request: Request, db=Depends(db_dep)):
    a = acc(request, db)
    return guard(lambda: org.team_dict(db, org.find_team(db, team), a))


@router.patch("/teams/{team}")
def patch_team(team: str, body: TeamPatch, request: Request, db=Depends(db_dep)):
    a = acc(request, db)
    return guard(lambda: org.team_dict(db, org.update_team(db, a, team, body.model_dump(exclude_unset=True)), a))


@router.delete("/teams/{team}")
def delete_team(team: str, request: Request, db=Depends(db_dep)):
    guard(lambda: org.delete_team(db, acc(request, db), team))
    return {"ok": True}


@router.get("/teams/{team}/members")
def team_members(team: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: org.members(db, acc(request, db), team))


@router.post("/teams/{team}/members")
def add_member(team: str, body: MemberBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: org.add_member(db, acc(request, db), team, body.email, body.role))


@router.patch("/teams/{team}/members/{user_id}")
def set_member_role(team: str, user_id: int, body: RoleBody, request: Request, db=Depends(db_dep)):
    def go():
        a = acc(request, db)
        from ..models import User
        u = db.get(User, user_id)
        if not u:
            raise org.PlatformError("usuário não encontrado")
        return org.add_member(db, a, team, u.email, body.role)
    return guard(go)


@router.delete("/teams/{team}/members/{user_id}")
def remove_member(team: str, user_id: int, request: Request, db=Depends(db_dep)):
    guard(lambda: org.remove_member(db, acc(request, db), team, user_id))
    return {"ok": True}


# ------------------------------------------------------------------ usuários
class UserBody(BaseModel):
    email: str = ""
    name: str = ""
    org_role: str = "member"
    username: str | None = None  # com senha: conta local
    password: str | None = None


class UserPatch(BaseModel):
    org_role: str | None = None
    active: bool | None = None
    name: str | None = None
    username: str | None = None
    password: str | None = None  # redefine (e encerra as sessões da pessoa)
    email: str | None = None


@router.get("/users")
def users(request: Request, db=Depends(db_dep)):
    return guard(lambda: org.list_users(db, acc(request, db)))


@router.post("/users")
def create_user(body: UserBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: org.user_dict(db, org.create_user(db, acc(request, db), body.email, body.name, body.org_role,
                                                           body.username, body.password)))


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, request: Request, db=Depends(db_dep)):
    return guard(lambda: org.user_dict(db, org.update_user(db, acc(request, db), user_id, body.org_role, body.active,
                                                           body.name, body.username, body.password, body.email)))


# ------------------------------------------------------------------ acesso a agentes e aprovações
class ReasonBody(BaseModel):
    reason: str = ""


class NoteBody(BaseModel):
    note: str = ""


class AgentAccessBody(BaseModel):
    visibility: str | None = None
    expose_spec: bool | None = None
    team: str | None = None


@router.get("/approvals")
def approvals(request: Request, db=Depends(db_dep)):
    return org.approvals(db, acc(request, db))


@router.post("/agents/{slug}/access-requests")
def request_access(slug: str, body: ReasonBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: org.request_access(db, acc(request, db), slug, body.reason))


@router.get("/agents/{slug}/grants")
def grants(slug: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: org.agent_grants(db, acc(request, db), slug))


@router.patch("/agents/{slug}/access")
def agent_access(slug: str, body: AgentAccessBody, request: Request, db=Depends(db_dep)):
    def go():
        a = acc(request, db)
        agent = org.set_agent_access(db, a, slug, body.visibility, body.expose_spec, body.team)
        return {"slug": agent.slug, "visibility": agent.visibility, "expose_spec": agent.expose_spec,
                "team_id": agent.team_id}
    return guard(go)


@router.post("/access-requests/{rid}/{decision}")
def decide_access(rid: int, decision: str, request: Request, db=Depends(db_dep)):
    a = acc(request, db)
    if decision == "revoke":
        return guard(lambda: org.revoke_access(db, a, rid))
    if decision not in ("approve", "reject"):
        raise HTTPException(404, "use approve, reject ou revoke")
    return guard(lambda: org.decide_access(db, a, rid, decision == "approve"))


@router.post("/promotions/{rid}/{decision}")
def decide_promotion(rid: int, decision: str, body: NoteBody, request: Request, db=Depends(db_dep)):
    if decision not in ("approve", "reject"):
        raise HTTPException(404, "use approve ou reject")
    return guard(lambda: org.decide_promotion(db, acc(request, db), rid, decision == "approve", body.note))


# ------------------------------------------------------------------ SSO (OAuth2) e SCIM — só admin
class ProviderBody(BaseModel):
    enabled: bool = True
    client_id: str = ""
    client_secret: str = ""  # vazio mantém o atual
    settings: dict = {}


class MappingBody(BaseModel):
    provider: str
    external_group: str
    team: str
    role: str = "consumer"


@router.get("/sso", dependencies=[Depends(require_admin)])
def sso_config(db=Depends(db_dep)):
    return {"providers": sso.admin_view(db), "mappings": sso.mappings(db),
            "scim_url": f"{config.PUBLIC_BASE_URL}/scim/v2",
            "bootstrap_admins": sorted(config.BOOTSTRAP_ADMIN_EMAILS)}


@router.put("/sso/{provider}", dependencies=[Depends(require_admin)])
def sso_save(provider: str, body: ProviderBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: sso.save_provider(db, provider, body.enabled, body.client_id, body.client_secret,
                                           body.settings, actor(request)))


@router.post("/sso/mappings", dependencies=[Depends(require_admin)])
def sso_add_mapping(body: MappingBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: sso.add_mapping(db, body.provider, body.external_group, org.find_team(db, body.team).id,
                                         body.role, actor(request)))


@router.delete("/sso/mappings/{mid}", dependencies=[Depends(require_admin)])
def sso_delete_mapping(mid: int, request: Request, db=Depends(db_dep)):
    guard(lambda: sso.delete_mapping(db, mid, actor(request)))
    return {"ok": True}


__all__ = ["router", "principal"]


# ================================================================== workspace do membro
class PolicyBody(BaseModel):
    login: dict | None = None
    personal_agents: dict | None = None


class CodeBody(BaseModel):
    email: str = ""  # vazio: o membro gera para ele mesmo (outro navegador)


class PromoteBody(BaseModel):
    note: str = ""


def _current_hash(request: Request) -> str:
    raw = request.cookies.get(auth.SESSION_COOKIE, "")
    return auth.hash_key(raw) if raw else ""


@router.get("/workspace/policy")
def workspace_policy(request: Request, db=Depends(db_dep)):
    acc(request, db)
    return svc.workspace.policy(db)


@router.put("/workspace/policy")
def workspace_policy_set(body: PolicyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.set_policy(db, acc(request, db), body.model_dump(exclude_none=True)))


class CompanyBody(BaseModel):
    name: str | None = None
    domains: list[str] | None = None  # @empresa.com.br, um ou mais
    enforce: bool | None = None  # só entram membros com e-mail desses domínios
    subdomains: bool | None = None  # vale também @br.empresa.com.br
    skip: bool = False  # primeiro acesso: "configurar depois"


@router.get("/workspace/company")
def workspace_company(request: Request, db=Depends(db_dep)):
    """O cadastro da empresa: nome, domínios, a regra de e-mail corporativo e os membros fora dela."""
    return guard(lambda: svc.workspace.company(db, acc(request, db)))


@router.put("/workspace/company")
def workspace_company_set(body: CompanyBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.set_company(db, acc(request, db), body.name, body.domains, body.enforce,
                                                   body.subdomains, body.skip))


@router.get("/workspace/sessions")
def workspace_sessions(request: Request, user_id: int | None = None, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.org_sessions(db, acc(request, db), user_id))


@router.delete("/workspace/sessions/{session_id}")
def workspace_session_revoke(session_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.revoke_org(db, acc(request, db), session_id=session_id))


@router.post("/workspace/users/{user_id}/sessions/revoke")
def workspace_user_revoke(user_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.revoke_org(db, acc(request, db), user_id=user_id))


@router.post("/workspace/activation-codes")
def workspace_activation_code(body: CodeBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.issue_code(db, acc(request, db), body.email))


@router.get("/me/sessions")
def my_sessions(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.my_sessions(db, acc(request, db), _current_hash(request)))


@router.delete("/me/sessions/{session_id}")
def my_session_revoke(session_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.revoke_mine(db, acc(request, db), session_id=session_id))


@router.post("/me/sessions/revoke-others")
def my_sessions_revoke_others(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.revoke_mine(db, acc(request, db), others_than=_current_hash(request)))


@router.post("/me/activation-code")
def my_activation_code(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.issue_code(db, acc(request, db)))


@router.get("/me/workspace", dependencies=[member_route(allow_app=True)])
def my_workspace(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.home(db, acc(request, db)))


@router.post("/employees/{slug}/promote-to-team")
def promote_personal(slug: str, body: PromoteBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.promote(db, acc(request, db), slug, body.note))



class ReportsToBody(BaseModel):
    to: str = ""
    why: str = ""


@router.post("/employees/{slug}/propose-reports-to")
def propose_reports_to(slug: str, body: ReportsToBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.self_org.propose_reports_to(db, acc(request, db), slug, body.to, body.why))



# ------------------------------------------------------------------ notificações do navegador (PWA + Web Push)
class PushBody(BaseModel):
    endpoint: str
    keys: dict = {}


@router.get("/me/push")
def my_push(request: Request, db=Depends(db_dep)):
    a = acc(request, db)
    return {"public_key": svc.webpush.public_key(db), "subscriptions": svc.webpush.mine(db, a)}


@router.post("/me/push")
def my_push_subscribe(body: PushBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.webpush.subscribe(db, acc(request, db), body.model_dump(), request.headers.get("user-agent", "")))


@router.post("/me/push/remove")
def my_push_remove(body: PushBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.webpush.unsubscribe(db, acc(request, db), body.endpoint))


@router.post("/me/push/test")
def my_push_test(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.webpush.test_mine(db, acc(request, db)))



class InviteBody(BaseModel):
    email: str
    name: str = ""
    team: str = ""
    role: str = "consumer"
    org_role: str = "member"


@router.post("/workspace/invites")
def workspace_invite(body: InviteBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.invite(db, acc(request, db), body.email, body.name, body.team, body.role, body.org_role))


@router.get("/workspace/invites")
def workspace_invites(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.workspace.invites(db, acc(request, db)))


@router.post("/workspace/invites/{user_id}/resend")
def workspace_invite_resend(user_id: int, request: Request, db=Depends(db_dep)):
    from ..models import User

    def go():
        u = db.get(User, user_id)
        if u is None:
            raise svc.PlatformError("membro não encontrado")
        return svc.workspace.invite(db, acc(request, db), u.email)
    return guard(go)
