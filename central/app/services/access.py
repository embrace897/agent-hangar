"""Autorização: o que cada pessoa pode fazer com cada agente.

Papéis
- Empresa: admin (tudo), auditor (lê tudo: uso, custo, auditoria, specs), member.
- Time: maintainer (membros, aprova produção e pedidos de acesso), developer (cria/edita/testa agentes do
  time), consumer (usa os agentes do time: conectar, playground, uso).

Visibilidade de um agente
- private: só o time vê.  org: aparece no catálogo da empresa; usar exige pedido aprovado.
- open: qualquer pessoa da empresa usa direto (com chave própria).
Instruções/spec de outros times ficam ocultas, a menos que o time ligue `expose_spec`.
"""
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..models import AccessRequest, Agent, ApiKey, Team, TeamMember, User, now
from .common import PlatformError, audit

ORG_ROLES = ("admin", "auditor", "member")
TEAM_ROLES = ("maintainer", "developer", "consumer")
VISIBILITIES = ("private", "org", "open")
RANK = {"consumer": 1, "developer": 2, "maintainer": 3}

_PERMS = {
    "view_spec": {"admin", "auditor", "maintainer", "developer"},
    "usage": {"admin", "auditor", "maintainer", "developer", "consumer"},
    "consume": {"admin", "maintainer", "developer", "consumer", "granted"},
    "edit": {"admin", "maintainer", "developer"},
    "manage": {"admin", "maintainer"},  # visibilidade, excluir, decidir pedidos de acesso
    "approve": {"admin", "maintainer"},  # aprovar promoção para produção
}
ACTION_LABEL = {"view": "ver", "view_spec": "ver a spec de", "usage": "ver o uso de", "consume": "usar",
                "edit": "editar", "manage": "administrar", "approve": "aprovar", "promote": "promover"}


class Forbidden(PlatformError):
    """Sem permissão — vira HTTP 403 na API e erro de tool no MCP."""


class Access:
    """Permissões de um principal, com times e concessões carregados uma vez (listas não fazem N consultas)."""

    def __init__(self, db: Session, p: auth.Principal):
        self.db, self.p = db, p

    @cached_property
    def teams(self) -> dict[int, str]:
        if not self.p.user_id or not self.p.is_user:
            return {}
        return {m.team_id: m.role for m in self.db.scalars(select(TeamMember).where(TeamMember.user_id == self.p.user_id))}

    @cached_property
    def grants(self) -> set[int]:
        if not self.p.is_user:
            return set()
        return set(self.db.scalars(select(AccessRequest.agent_id).where(
            AccessRequest.user_id == self.p.user_id, AccessRequest.status == "approved")))

    @cached_property
    def team_rows(self) -> dict[int, Team]:
        return {t.id: t for t in self.db.scalars(select(Team))}

    def level(self, a: Agent) -> str | None:
        """admin | auditor | maintainer | developer | consumer | granted | viewer | None (não vê)."""
        if self.p.is_admin:
            return "admin"
        if not self.p.is_user:
            return None
        if a.personal_owner_id:  # agente pessoal: o dono cuida; o time não vê (o auditor vê)
            if a.personal_owner_id == self.p.user_id:
                return "maintainer"
            return "auditor" if self.p.is_auditor else None
        role = self.teams.get(a.team_id)
        if role:
            return role
        if self.p.is_auditor:
            return "auditor"
        if a.visibility == "private":
            return None
        if a.visibility == "open" or a.id in self.grants:
            return "granted"
        return "viewer"

    def permissions(self, a: Agent) -> set[str]:
        lv = self.level(a)
        if lv is None:
            return set()
        out = {"view"} | {k for k, roles in _PERMS.items() if lv in roles}
        if a.expose_spec:
            out.add("view_spec")
        team = self.team_rows.get(a.team_id)
        if lv == "admin" or (lv == "maintainer" and not (team and team.require_approval)):
            out.add("promote")  # direto para produção; os outros geram pedido de aprovação
        if lv == "viewer" and self.p.is_user:
            out.add("request_access")
        return out

    def can(self, action: str, a: Agent) -> bool:
        return action in self.permissions(a)

    def require(self, action: str, a: Agent):
        if not self.can(action, a):
            if "view" not in self.permissions(a):
                raise Forbidden(f"Agente '{a.slug}' não encontrado ou sem acesso")
            raise Forbidden(f"Sem permissão para {ACTION_LABEL.get(action, action)} '{a.slug}'")

    def visible(self, agents) -> list[Agent]:
        return [a for a in agents if self.level(a) is not None]

    def team_role(self, team_id: int | None) -> str | None:
        if self.p.is_admin:
            return "maintainer"
        return self.teams.get(team_id)

    def require_team(self, team_id: int, min_role: str = "developer"):
        role = self.team_role(team_id)
        if not role or RANK[role] < RANK[min_role]:
            t = self.team_rows.get(team_id)
            raise Forbidden(f"É preciso ser {min_role} do time '{t.slug if t else team_id}'")

    def team_for_new_agent(self, team: str | int | None, any_role: bool = False) -> Team:
        """Time de um agente novo: o pedido explícito, senão o único onde a pessoa é developer+ (admin sem time
        → Plataforma). Só developers e maintainers criam agentes."""
        if team not in (None, "", 0):
            t = find_team(self.db, team)
            self.require_team(t.id, "consumer" if any_role else "developer")
            return t
        if self.p.is_admin:
            return self.team_rows.get(1) or next(iter(self.team_rows.values()))
        mine = [tid for tid, r in self.teams.items() if RANK[r] >= RANK["developer"]]
        if len(mine) == 1:
            return self.team_rows[mine[0]]
        if not mine:
            raise Forbidden("Só developers e maintainers de um time criam agentes — peça acesso a um mantenedor")
        raise PlatformError("Você está em mais de um time: informe `team` (" +
                            ", ".join(self.team_rows[t].slug for t in mine) + ")")


def of(db: Session, p: auth.Principal) -> Access:
    return Access(db, p)


def find_team(db: Session, team: str | int) -> Team:
    t = db.get(Team, int(team)) if str(team).isdigit() else db.scalar(select(Team).where(Team.slug == str(team)))
    if not t:
        raise PlatformError(f"Time '{team}' não encontrado")
    return t


def user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == (email or "").strip().lower()))


def principal_for(user: User) -> auth.Principal:
    return auth.Principal(user.email, {"user"}, user_id=user.id, org_role=user.org_role, via="internal")


def reconcile_keys(db: Session, user_ids=None, actor: str = "sistema") -> int:
    """Revoga chaves invoke/user de quem perdeu o acesso (saiu do time, concessão revogada, agente ficou
    privado, usuário desligado). Chamado depois de qualquer mudança de acesso."""
    q = select(ApiKey).where(ApiKey.revoked_at.is_(None), ApiKey.user_id.is_not(None))
    if user_ids is not None:
        q = q.where(ApiKey.user_id.in_(list(user_ids)))
    agents = {a.slug: a for a in db.scalars(select(Agent))}
    revoked = 0
    cache: dict[int, Access | None] = {}
    for k in db.scalars(q).all():
        if k.user_id not in cache:
            u = db.get(User, k.user_id)
            cache[k.user_id] = of(db, principal_for(u)) if u and u.active else None
        acc = cache[k.user_id]
        dead = acc is None
        if not dead and "invoke" in (k.scopes or []) and "admin" not in (k.scopes or []):
            dead = not k.agents or any(s not in agents or not acc.can("consume", agents[s]) for s in k.agents)
        if not dead and "admin" in (k.scopes or []) and not acc.p.is_admin:
            dead = True
        if dead:
            k.revoked_at = now()
            revoked += 1
            audit(db, actor, "api_key.auto_revoke", str(k.id), f"{k.name}: dono sem acesso")
    if revoked:
        db.commit()
        auth.clear_cache()
    return revoked
