"""Empresa, times, membros, usuários, pedidos de acesso, aprovação de produção e orçamento por time."""
import re
import time
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, config
from ..models import (
    AccessRequest,
    Agent,
    GroupMapping,
    Organization,
    PromotionRequest,
    Team,
    TeamMember,
    UsageEvent,
    User,
    now,
)
from ..tenancy import current_org, unscoped
from .access import (
    ORG_ROLES,
    RANK,
    TEAM_ROLES,
    VISIBILITIES,
    Access,
    Forbidden,
    find_team,
    reconcile_keys,
    user_by_email,
)
from .common import PlatformError, audit, get_agent, iso, slugify


def _month_start() -> datetime:
    n = now()
    return datetime(n.year, n.month, 1, tzinfo=UTC)


# ------------------------------------------------------------------ empresa
def org_dict(db: Session) -> dict:
    o = db.get(Organization, current_org())
    return {"id": o.id, "name": o.name, "default_visibility": o.default_visibility, "timezone": o.timezone,
            "code_policy": o.code_policy or "any", "setup_done": bool((o.access_policy or {}).get("setup_done_at"))}


def update_org(db: Session, acc: Access, name: str | None, default_visibility: str | None,
               timezone: str | None = None, code_policy: str | None = None) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("Só admins alteram a empresa")
    o = db.get(Organization, current_org())
    if name:
        o.name = name[:200]
    if default_visibility:
        if default_visibility not in VISIBILITIES:
            raise PlatformError(f"visibilidade inválida; opções: {VISIBILITIES}")
        o.default_visibility = default_visibility
    if timezone:
        from ..cron import CronError, zone
        try:
            zone(timezone)
        except CronError as e:
            raise PlatformError(str(e)) from None
        o.timezone = timezone
    if code_policy:
        if code_policy not in ("any", "approved"):
            raise PlatformError("code_policy: 'any' (qualquer conexão) ou 'approved' (só conexões aprovadas para código)")
        o.code_policy = code_policy
    db.commit()
    audit(db, acc.p.name, "org.update", o.name, f"visibilidade padrão={o.default_visibility}, código={o.code_policy}")
    return org_dict(db)


# ------------------------------------------------------------------ orçamento
def month_spend(db: Session, team_id: int) -> float:
    ids = select(Agent.id).where(Agent.team_id == team_id)
    return float(db.scalar(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0))
                           .where(UsageEvent.agent_id.in_(ids), UsageEvent.created_at >= _month_start())) or 0)


def budget_state(spent: float, budget: float | None) -> str:
    if not budget:
        return "none"
    return "over" if spent >= budget else "warn" if spent >= 0.8 * budget else "ok"


_BUDGET_CACHE: dict[int, tuple[float, bool]] = {}


def budget_blocked(db: Session, team_id: int | None) -> bool:
    """Gateway: o time estourou o orçamento do mês e ligou o bloqueio? (cache de 60s por time)"""
    if not team_id:
        return False
    hit = _BUDGET_CACHE.get(team_id)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    t = db.get(Team, team_id)
    blocked = False
    if t and t.budget_usd_month:
        spent = month_spend(db, t.id)
        month = _month_start().strftime("%Y-%m")
        if spent >= t.budget_usd_month and t.budget_alerted != month:
            t.budget_alerted = month
            db.commit()
            audit(db, "sistema", "budget.exceeded", t.slug, f"US$ {spent:.2f} de US$ {t.budget_usd_month:.2f} no mês")
        blocked = bool(t.budget_enforce and spent >= t.budget_usd_month)
    _BUDGET_CACHE[team_id] = (time.monotonic(), blocked)
    return blocked


# ------------------------------------------------------------------ times
def team_dict(db: Session, t: Team, acc: Access | None = None) -> dict:
    members = db.scalar(select(func.count(TeamMember.id)).where(TeamMember.team_id == t.id))
    agents = db.scalar(select(func.count(Agent.id)).where(Agent.team_id == t.id))
    spent = month_spend(db, t.id)
    return {"id": t.id, "slug": t.slug, "name": t.name, "description": t.description,
            "require_approval": t.require_approval, "budget_usd_month": t.budget_usd_month,
            "budget_enforce": t.budget_enforce, "spent_month": round(spent, 4),
            "budget_state": budget_state(spent, t.budget_usd_month), "members": members, "agents": agents,
            "my_role": acc.team_role(t.id) if acc else None}


def list_teams(db: Session, acc: Access) -> list[dict]:
    return [team_dict(db, t, acc) for t in db.scalars(select(Team).order_by(Team.name))]


RESERVED_TEAM_SLUGS = {"new"}  # rotas da UI (#/teams/new)


def create_team(db: Session, acc: Access, name: str, slug: str = "", description: str = "",
                require_approval: bool = True, maintainer: str | None = None) -> Team:
    if not acc.p.is_admin:
        raise Forbidden("Só admins criam times")
    if not (name or "").strip():
        raise PlatformError("nome do time é obrigatório")
    slug = slugify(slug or name)
    if slug in RESERVED_TEAM_SLUGS:
        raise PlatformError(f"'{slug}' é um nome reservado; escolha outro")
    if db.scalar(unscoped(select(Team.id).where(Team.slug == slug))):  # slug único na instalação
        raise PlatformError(f"já existe um time '{slug}'")
    owner = None
    if maintainer:  # valida antes de criar: um e-mail ou usuário inválido não deixa time pela metade
        m = maintainer.strip().lower()
        owner = db.scalar(select(User).where(User.username == m)) or (ensure_user(db, m) if "@" in m else None)
        if owner is None:
            raise PlatformError(f"usuário '{maintainer}' não encontrado (use o e-mail ou o nome de usuário)")
    t = Team(slug=slug, name=name.strip(), description=description, require_approval=require_approval)
    db.add(t)
    db.commit()
    audit(db, acc.p.name, "team.create", slug, name)
    if owner:
        set_member(db, t.id, owner.id, "maintainer")
        audit(db, acc.p.name, "team.member.set", slug, f"{owner.email}=maintainer")
    return t


def update_team(db: Session, acc: Access, team: str | int, fields: dict) -> Team:
    t = find_team(db, team)
    acc.require_team(t.id, "maintainer")
    admin_only = {"require_approval", "budget_usd_month", "budget_enforce"}
    if admin_only & set(fields) and not acc.p.is_admin:
        raise Forbidden("Aprovação obrigatória e orçamento do time são definidos por admins")
    for k in ("name", "description", *admin_only):
        if k in fields and fields[k] is not None:
            setattr(t, k, fields[k])
    if fields.get("budget_usd_month") == 0:
        t.budget_usd_month = None
    db.commit()
    _BUDGET_CACHE.pop(t.id, None)
    audit(db, acc.p.name, "team.update", t.slug, str({k: v for k, v in fields.items() if v is not None}))
    return t


def delete_team(db: Session, acc: Access, team: str | int):
    t = find_team(db, team)
    if not acc.p.is_admin:
        raise Forbidden("Só admins excluem times")
    if t.id == 1:
        raise PlatformError("o time Plataforma é o padrão e não pode ser excluído")
    if db.scalar(select(func.count(Agent.id)).where(Agent.team_id == t.id)):
        raise PlatformError("o time ainda tem agentes: transfira-os antes")
    users = [m.user_id for m in db.scalars(select(TeamMember).where(TeamMember.team_id == t.id))]
    db.query(TeamMember).filter(TeamMember.team_id == t.id).delete()
    db.query(GroupMapping).filter(GroupMapping.team_id == t.id).delete()
    db.delete(t)
    db.commit()
    reconcile_keys(db, users, acc.p.name)
    audit(db, acc.p.name, "team.delete", t.slug)


# ------------------------------------------------------------------ membros
def member_dict(m: TeamMember, u: User) -> dict:
    return {"user_id": u.id, "email": u.email, "name": u.name, "avatar_url": u.avatar_url, "role": m.role,
            "source": m.source, "active": u.active, "since": iso(m.created_at)}


def members(db: Session, acc: Access, team: str | int) -> list[dict]:
    t = find_team(db, team)
    if not (acc.p.is_auditor or acc.team_role(t.id)):
        raise Forbidden("Só membros do time veem a lista de membros")
    rows = db.execute(select(TeamMember, User).join(User, User.id == TeamMember.user_id)
                      .where(TeamMember.team_id == t.id).order_by(User.email)).all()
    return [member_dict(m, u) for m, u in rows]


def ensure_user(db: Session, email: str, name: str = "") -> User:
    """Usuário pelo e-mail; cria um pré-cadastro se ainda não existir (o login depois casa pelo e-mail)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        raise PlatformError(f"e-mail inválido: '{email}'")
    u = user_by_email(db, email)
    if not u:
        if db.scalar(unscoped(select(User.id).where(User.email == email))):  # e-mail único na instalação
            raise PlatformError(f"'{email}' é uma pessoa de outra empresa")
        from .workspace import check_email
        check_email(db, email)  # a empresa pode exigir e-mail corporativo
        u = User(email=email, name=name or email.split("@")[0])
        db.add(u)
        db.commit()
    return u


def set_member(db: Session, team_id: int, user_id: int, role: str, source: str = "manual") -> TeamMember:
    if role not in TEAM_ROLES:
        raise PlatformError(f"papel inválido; opções: {TEAM_ROLES}")
    m = db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id))
    if m:
        m.role = role
        if source == "manual":
            m.source = "manual"  # quem ajusta à mão assume: a sincronização de grupos não mexe mais
    else:
        m = TeamMember(team_id=team_id, user_id=user_id, role=role, source=source)
        db.add(m)
    db.commit()
    return m


def add_member(db: Session, acc: Access, team: str | int, email: str, role: str) -> dict:
    t = find_team(db, team)
    acc.require_team(t.id, "maintainer")
    u = ensure_user(db, email)
    m = set_member(db, t.id, u.id, role)
    audit(db, acc.p.name, "team.member.set", t.slug, f"{u.email}={role}")
    reconcile_keys(db, [u.id], acc.p.name)
    return member_dict(m, u)


def remove_member(db: Session, acc: Access, team: str | int, user_id: int):
    t = find_team(db, team)
    acc.require_team(t.id, "maintainer")
    m = db.scalar(select(TeamMember).where(TeamMember.team_id == t.id, TeamMember.user_id == user_id))
    if not m:
        raise PlatformError("não é membro deste time")
    u = db.get(User, user_id)
    db.delete(m)
    db.commit()
    audit(db, acc.p.name, "team.member.remove", t.slug, u.email if u else str(user_id))
    reconcile_keys(db, [user_id], acc.p.name)


# ------------------------------------------------------------------ usuários
def user_dict(db: Session, u: User, with_teams=True) -> dict:
    d = {"id": u.id, "email": u.email, "name": u.name, "avatar_url": u.avatar_url, "org_role": u.org_role,
         "provider": u.provider, "active": u.active, "created_at": iso(u.created_at),
         "last_login_at": iso(u.last_login_at), "scim": bool(u.external_id), "username": u.username,
         "has_password": bool(u.password_hash)}
    if with_teams:
        d["teams"] = [{"id": t.id, "slug": t.slug, "name": t.name, "role": m.role, "source": m.source}
                      for m, t in db.execute(select(TeamMember, Team).join(Team, Team.id == TeamMember.team_id)
                                             .where(TeamMember.user_id == u.id)).all()]
    return d


def list_users(db: Session, acc: Access) -> list[dict]:
    if not acc.p.is_auditor:
        raise Forbidden("Só admins e auditores listam usuários")
    return [user_dict(db, u) for u in db.scalars(select(User).order_by(User.email))]


USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


def _check_username(db: Session, username: str, user_id: int | None = None) -> str:
    username = (username or "").strip().lower()
    if not USERNAME.match(username):
        raise PlatformError("usuário: 3 a 80 caracteres, letras minúsculas, números, ponto, _ ou -")
    other = db.scalar(unscoped(select(User).where(User.username == username)))  # único na instalação
    if other and other.id != user_id:
        raise PlatformError(f"o usuário '{username}' já existe")
    return username


def create_user(db: Session, acc: Access, email: str = "", name: str = "", org_role: str = "member",
                username: str | None = None, password: str | None = None) -> User:
    """Pré-cadastro (entra depois por SSO) ou conta local, quando vem com usuário e senha. Sem e-mail, a conta
    local recebe <usuário>@local (troque depois para o e-mail real, e o SSO passa a reconhecê-la)."""
    if not acc.p.is_admin:
        raise Forbidden("Só admins cadastram usuários")
    if username:
        username = _check_username(db, username)
        if not config.LOCAL_LOGIN:
            raise PlatformError("contas locais estão desligadas (LOCAL_LOGIN=0)")
        if not password:
            raise PlatformError("informe a senha da conta local")
    email = (email or (f"{username}@local" if username else "")).strip().lower()
    if user_by_email(db, email):
        raise PlatformError("já existe um usuário com esse e-mail")
    u = ensure_user(db, email, name or username or "")
    if username:
        u.username, u.password_hash = username, auth.hash_password(password)
        db.commit()
    if org_role != "member":
        update_user(db, acc, u.id, org_role=org_role)
    audit(db, acc.p.name, "user.create", u.email, f"{org_role}{' (conta local ' + username + ')' if username else ''}")
    return u


def update_user(db: Session, acc: Access, user_id: int, org_role: str | None = None, active: bool | None = None,
                name: str | None = None, username: str | None = None, password: str | None = None,
                email: str | None = None) -> User:
    if not acc.p.is_admin:
        raise Forbidden("Só admins alteram usuários")
    u = db.get(User, user_id)
    if not u:
        raise PlatformError("usuário não encontrado")
    if username:
        u.username = _check_username(db, username, u.id)
    if password:
        if not u.username:
            raise PlatformError("defina um nome de usuário para a conta local antes da senha")
        u.password_hash = auth.hash_password(password)
        auth.revoke_user_sessions(db, u.id)  # senha redefinida: sessões antigas caem
        audit(db, acc.p.name, "user.password_reset", u.email)
    if email and email.strip().lower() != u.email:
        email = email.strip().lower()
        if "@" not in email or user_by_email(db, email):
            raise PlatformError("e-mail inválido ou já usado por outro usuário")
        from .workspace import check_email
        check_email(db, email)
        u.email = email
    if org_role is not None:
        if org_role not in ORG_ROLES:
            raise PlatformError(f"papel inválido; opções: {ORG_ROLES}")
        u.org_role = org_role
    if name:
        u.name = name
    if active is not None:
        u.active = active
    db.commit()
    auth.clear_cache()
    if active is False:
        deactivate(db, u, acc.p.name)
    else:
        reconcile_keys(db, [u.id], acc.p.name)
    audit(db, acc.p.name, "user.update", u.email, f"role={u.org_role} active={u.active}")
    return u


# senha falsa para igualar o tempo de resposta quando o usuário não existe (não revela quem existe)
_DUMMY_HASH = None


def password_login(db: Session, login: str, password: str) -> User:
    """Login com conta local. Mesmo erro para usuário inexistente e senha errada; 5 erros bloqueiam 15 min."""
    global _DUMMY_HASH
    if not config.LOCAL_LOGIN:
        raise Forbidden("login com usuário e senha está desligado nesta instalação")
    login = (login or "").strip().lower()
    key = f"pw:{login}"
    if auth.locked_out(key):
        raise Forbidden("muitas tentativas erradas — espere 15 minutos")
    u = db.scalar(select(User).where((User.username == login) | (User.email == login)))
    _DUMMY_HASH = _DUMMY_HASH or auth.hash_password("x" * 12)
    ok = auth.verify_password(password, u.password_hash if u and u.password_hash else _DUMMY_HASH)
    if not (u and u.password_hash and ok):
        auth.register_fail(key)
        audit(db, login[:80] or "?", "auth.login_failed", "password")
        raise Forbidden("usuário ou senha inválidos")
    if not u.active:
        raise Forbidden("usuário desativado — fale com um administrador")
    auth.clear_fails(key)
    u.last_login_at = now()
    db.commit()
    audit(db, u.email, "auth.login", "password")
    return u


def change_password(db: Session, acc: Access, current: str, new: str):
    if not acc.p.is_user:
        raise Forbidden("só usuários trocam a própria senha")
    u = db.get(User, acc.p.user_id)
    if not u.password_hash or not auth.verify_password(current, u.password_hash):
        raise Forbidden("senha atual incorreta")
    u.password_hash = auth.hash_password(new)
    db.commit()
    audit(db, u.email, "user.password_change", u.email)


def deactivate(db: Session, u: User, actor: str):
    """Desligamento: sessões encerradas e todas as chaves do usuário revogadas na hora."""
    u.active = False
    db.commit()
    auth.revoke_user_sessions(db, u.id)
    reconcile_keys(db, [u.id], actor)
    for r in db.scalars(select(AccessRequest).where(AccessRequest.user_id == u.id, AccessRequest.status == "pending")):
        r.status, r.decided_by, r.decided_at = "rejected", actor, now()
    db.commit()


def me(db: Session, acc: Access) -> dict:
    p = acc.p
    out = {"name": p.name, "via": p.via, "is_admin": p.is_admin, "is_auditor": p.is_auditor, "user": None,
           "teams": [], "org": org_dict(db), "pending": len(pending_for(db, acc))}
    from .orgs import is_instance_admin
    if is_instance_admin(acc):  # quem administra a instalação escolhe a empresa no portal (X-Hangar-Org)
        out["instance_admin"] = True
        out["orgs"] = [{"id": o.id, "name": o.name, "slug": o.slug or ""} for o in db.scalars(select(Organization).order_by(Organization.id))]
    from .employee_tasks import pending_for as decisions_for
    out["decisions"] = sum(1 for d in decisions_for(db, acc) if d["kind"] != "notice")
    if p.is_user:
        u = db.get(User, p.user_id)
        out["user"] = user_dict(db, u)
        out["teams"] = out["user"]["teams"]
    out["can_create_agents"] = p.is_admin or any(RANK[r] >= RANK["developer"] for r in acc.teams.values())
    return out


# ------------------------------------------------------------------ acesso a agentes de outros times
def request_dict(db: Session, r: AccessRequest) -> dict:
    a, u = db.get(Agent, r.agent_id), db.get(User, r.user_id)
    return {"id": r.id, "agent": a.slug if a else None, "agent_name": a.name if a else None,
            "user": u.email if u else None, "user_name": u.name if u else None, "reason": r.reason,
            "status": r.status, "decided_by": r.decided_by, "decided_at": iso(r.decided_at),
            "created_at": iso(r.created_at)}


def request_access(db: Session, acc: Access, slug: str, reason: str = "") -> dict:
    a = get_agent(db, slug)
    acc.require("view", a)
    if not acc.p.is_user:
        raise Forbidden("Pedidos de acesso são feitos por usuários (login)")
    if acc.can("consume", a):
        raise PlatformError("você já pode usar este agente")
    dup = db.scalar(select(AccessRequest).where(AccessRequest.agent_id == a.id, AccessRequest.user_id == acc.p.user_id,
                                                AccessRequest.status == "pending"))
    if dup:
        return request_dict(db, dup)
    r = AccessRequest(agent_id=a.id, user_id=acc.p.user_id, reason=reason[:2000])
    db.add(r)
    db.commit()
    audit(db, acc.p.name, "access.request", a.slug, reason[:300])
    return request_dict(db, r)


def decide_access(db: Session, acc: Access, request_id: int, approve: bool) -> dict:
    r = db.get(AccessRequest, request_id)
    if not r or r.status != "pending":
        raise PlatformError("pedido não encontrado ou já decidido")
    a = db.get(Agent, r.agent_id)
    acc.require("manage", a)
    r.status, r.decided_by, r.decided_at = ("approved" if approve else "rejected"), acc.p.name, now()
    db.commit()
    audit(db, acc.p.name, "access.approve" if approve else "access.reject", a.slug, f"pedido #{r.id}")
    return request_dict(db, r)


def revoke_access(db: Session, acc: Access, request_id: int) -> dict:
    r = db.get(AccessRequest, request_id)
    if not r or r.status != "approved":
        raise PlatformError("concessão não encontrada")
    a = db.get(Agent, r.agent_id)
    if acc.p.user_id != r.user_id:
        acc.require("manage", a)
    r.status, r.decided_by, r.decided_at = "revoked", acc.p.name, now()
    db.commit()
    audit(db, acc.p.name, "access.revoke", a.slug, f"pedido #{r.id}")
    reconcile_keys(db, [r.user_id], acc.p.name)
    return request_dict(db, r)


def agent_grants(db: Session, acc: Access, slug: str) -> list[dict]:
    a = get_agent(db, slug)
    acc.require("manage", a)
    return [request_dict(db, r) for r in db.scalars(select(AccessRequest).where(
        AccessRequest.agent_id == a.id, AccessRequest.status.in_(("pending", "approved")))
        .order_by(AccessRequest.id.desc()))]


def set_agent_access(db: Session, acc: Access, slug: str, visibility: str | None = None,
                     expose_spec: bool | None = None, team: str | int | None = None) -> Agent:
    a = get_agent(db, slug)
    acc.require("manage", a)
    changes = []
    if visibility is not None:
        if visibility not in VISIBILITIES:
            raise PlatformError(f"visibilidade inválida; opções: {VISIBILITIES}")
        a.visibility = visibility
        changes.append(f"visibility={visibility}")
    if expose_spec is not None:
        a.expose_spec = bool(expose_spec)
        changes.append(f"expose_spec={a.expose_spec}")
    if team not in (None, ""):
        t = find_team(db, team)
        if not acc.p.is_admin:
            acc.require_team(t.id, "maintainer")  # transferir: mantenedor dos dois times
        a.team_id = t.id
        changes.append(f"team={t.slug}")
    db.commit()
    audit(db, acc.p.name, "agent.access", a.slug, ", ".join(changes))
    reconcile_keys(db, None, acc.p.name)  # quem perdeu o acesso (ficou privado, mudou de time) perde as chaves
    return a


# ------------------------------------------------------------------ produção com aprovação (quatro olhos)
def promotion_dict(db: Session, r: PromotionRequest) -> dict:
    a = db.get(Agent, r.agent_id)
    return {"id": r.id, "agent": a.slug if a else None, "agent_name": a.name if a else None, "version": r.version,
            "requested_by": r.requested_by, "note": r.note, "status": r.status, "decided_by": r.decided_by,
            "decided_at": iso(r.decided_at), "created_at": iso(r.created_at),
            "stale": bool(a and r.status == "pending" and a.current_version != r.version)}


def request_promotion(db: Session, acc: Access, a: Agent, note: str = "") -> PromotionRequest:
    from .runtime import latest_test

    acc.require("edit", a)
    gate = latest_test(a, a.current_version)
    if not gate or gate.status != "passed":
        raise PlatformError(f"v{a.current_version} não tem teste aprovado em stage: rode os testes antes de pedir")
    for old in db.scalars(select(PromotionRequest).where(PromotionRequest.agent_id == a.id,
                                                         PromotionRequest.status == "pending")):
        if old.version == a.current_version:
            return old
        old.status = "superseded"
    r = PromotionRequest(agent_id=a.id, version=a.current_version, requested_by=acc.p.name,
                         requested_user_id=acc.p.user_id, note=note[:2000])
    db.add(r)
    db.commit()
    audit(db, acc.p.name, "promotion.request", a.slug, f"v{r.version}")
    return r


def promote(db: Session, acc: Access, slug: str, note: str = "") -> dict:
    """Produção: quem pode promover direto promove; developer (ou maintainer de time com aprovação
    obrigatória) gera um pedido para outro mantenedor aprovar."""
    from .runtime import promote as do_promote
    from .views import dep_dict

    a = get_agent(db, slug)
    if acc.can("promote", a):
        return {"status": "deployed", "deployment": dep_dict(do_promote(db, slug, acc.p.name))}
    r = request_promotion(db, acc, a, note)
    return {"status": "approval_pending", "request": promotion_dict(db, r)}


def ship(db: Session, acc: Access, slug: str, note: str = "") -> dict:
    from .runtime import ship as do_ship

    a = get_agent(db, slug)
    acc.require("edit", a)
    if acc.can("promote", a):
        return {"status": "deployed", "steps": do_ship(db, slug, acc.p.name)}
    steps = do_ship(db, slug, acc.p.name, promote_prod=False)
    r = request_promotion(db, acc, a, note)
    steps.append({"agent": slug, "step": "prod", "status": "approval_pending", "request": r.id})
    return {"status": "approval_pending", "steps": steps, "request": promotion_dict(db, r)}


def decide_promotion(db: Session, acc: Access, request_id: int, approve: bool, note: str = "") -> dict:
    from .runtime import promote as do_promote

    r = db.get(PromotionRequest, request_id)
    if not r or r.status != "pending":
        raise PlatformError("pedido não encontrado ou já decidido")
    a = db.get(Agent, r.agent_id)
    acc.require("approve", a)
    if acc.p.user_id and acc.p.user_id == r.requested_user_id and not acc.p.is_admin:
        raise Forbidden("Quatro olhos: quem pediu a promoção não pode aprová-la")
    if approve and a.current_version != r.version:
        r.status, r.decided_by, r.decided_at = "superseded", acc.p.name, now()
        db.commit()
        raise PlatformError(f"o agente mudou (v{a.current_version}) depois do pedido (v{r.version}): peça de novo")
    if approve:
        do_promote(db, a.slug, acc.p.name)
    r.status, r.decided_by, r.decided_at = ("approved" if approve else "rejected"), acc.p.name, now()
    if note:
        r.note = (r.note + "\n— " + note).strip()[:2000]
    db.commit()
    if approve:
        from .employees import after_promotion
        after_promotion(db, a, acc.p.name)
    audit(db, acc.p.name, "promotion.approve" if approve else "promotion.reject", a.slug, f"v{r.version}")
    return promotion_dict(db, r)


def pending_for(db: Session, acc: Access) -> list[dict]:
    """O que esta pessoa pode decidir agora (promoções e pedidos de acesso)."""
    out = []
    agents = {a.id: a for a in db.scalars(select(Agent))}
    for r in db.scalars(select(PromotionRequest).where(PromotionRequest.status == "pending")):
        a = agents.get(r.agent_id)
        if a and acc.can("approve", a) and (acc.p.is_admin or r.requested_user_id != acc.p.user_id):
            out.append({"kind": "promotion", **promotion_dict(db, r)})
    for r in db.scalars(select(AccessRequest).where(AccessRequest.status == "pending")):
        a = agents.get(r.agent_id)
        if a and acc.can("manage", a):
            out.append({"kind": "access", **request_dict(db, r)})
    return out


def approvals(db: Session, acc: Access) -> dict:
    mine_access, mine_promo = [], []
    if acc.p.is_user:
        mine_access = [request_dict(db, r) for r in db.scalars(select(AccessRequest).where(
            AccessRequest.user_id == acc.p.user_id).order_by(AccessRequest.id.desc()).limit(50))]
        mine_promo = [promotion_dict(db, r) for r in db.scalars(select(PromotionRequest).where(
            PromotionRequest.requested_user_id == acc.p.user_id).order_by(PromotionRequest.id.desc()).limit(50))]
    return {"to_decide": pending_for(db, acc), "my_access": mine_access, "my_promotions": mine_promo}


# ------------------------------------------------------------------ editar depois: stage -> testes -> produção
def where_running(a: Agent) -> dict:
    from .runtime import active_deployment
    prod, stage = active_deployment(a, "prod"), active_deployment(a, "stage")
    return {"prod_version": prod.version if prod else None, "stage_version": stage.version if stage else None}


def agent_spec(db: Session, acc: Access, slug: str, version: int | None = None) -> dict:
    """Spec (atual ou de uma versão) + metadados + onde cada versão está rodando: o ponto de partida para editar."""
    from .common import spec_of
    from .runtime import latest_test, refresh_deployments

    a = get_agent(db, slug)
    acc.require("view_spec", a)
    refresh_deployments(db, [a])
    v = version or a.current_version
    t = latest_test(a, v)
    team = db.get(Team, a.team_id) if a.team_id else None
    return {"slug": a.slug, "name": a.name, "objective": a.objective, "final_output": a.final_output,
            "owner": a.owner, "team": team.slug if team else None, "visibility": a.visibility,
            "current_version": a.current_version, "version": v, **where_running(a),
            "test": {"status": t.status, "summary": t.summary} if t else None,
            "versions": [x.version for x in a.versions], "spec": spec_of(a, v)}


def edit_agent(db: Session, acc: Access, slug: str, patch: dict, test: bool = True, promote: bool = False,
               note: str = "") -> dict:
    """Edita (spec e/ou nome, objetivo, saída final, contato) como nova versão; a produção segue na versão
    anterior. test=True: deploy em stage + testes. promote=True: se os testes passarem, publica (ou pede
    aprovação, conforme o papel e o time)."""
    import copy

    from .common import spec_of
    from .registry import META_KEYS, design_agent, spec_diff
    from .runtime import latest_test
    from .testing import run_tests

    a = get_agent(db, slug)
    acc.require("edit", a)
    before = a.current_version
    old_spec = copy.deepcopy(spec_of(a))
    old_meta = {k: getattr(a, k) for k in META_KEYS}
    if patch:
        design_agent(db, slug, patch, acc.p.name, acc=acc)
    changes = [{"path": k, "change": "changed", "from": old_meta[k], "to": getattr(a, k)}
               for k in META_KEYS if getattr(a, k) != old_meta[k]]
    changes += spec_diff(old_spec, spec_of(a))
    out = {"slug": a.slug, "version_before": before, "version": a.current_version, "changes": changes,
           **where_running(a)}
    if not changes:
        out["note"] = "nada mudou (os valores enviados já eram os atuais)"
    if a.current_version != before and out["prod_version"]:
        out["note"] = f"produção continua na v{out['prod_version']} até você promover a v{a.current_version}"
    passed = bool((t := latest_test(a, a.current_version)) and t.status == "passed")
    if test or (promote and not passed):
        run = run_tests(db, slug, acc.p.name)
        failed = [{"name": r.get("name"), "detail": r.get("detail", "")[:300]} for r in run.results if not r.get("passed")]
        out["tests"] = {"status": run.status, "summary": run.summary, "failed": failed}
        passed = run.status == "passed"
        out.update(where_running(a))
    if promote:
        if passed:
            out["production"] = promote_fn(db, acc, slug, note)
            db.refresh(a)  # a lista de deployments mudou: prod_version passa a refletir a publicação
            out.update(where_running(a))
        else:
            out["production"] = {"status": "not_promoted", "reason": "os testes não passaram"}
    out["next"] = ("corrija com edit_agent e teste de novo" if (test or promote) and not passed
                   else "publique com edit_agent(promote=True) ou promote_to_production" if not promote
                   else "pronto")
    if promote and out.get("production", {}).get("status") == "approval_pending":
        out["next"] = "um mantenedor do time precisa aprovar em Aprovações (ou decide_approval)"
    return out


promote_fn = promote  # nome estável para edit_agent (promote é também o nome de um parâmetro)
