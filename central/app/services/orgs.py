"""Empresas da instalação (multiempresa).

Quem administra a instalação — o token admin e os admins da empresa principal (id 1) — cria empresas, define os
domínios de e-mail que entram em cada uma pelo SSO e suspende uma empresa (as pessoas dela deixam de entrar). Pelo
cabeçalho X-Hangar-Org (ou o seletor no portal), age dentro de qualquer empresa.

Cada empresa tem as próprias pessoas, times, agentes, Digital employees, plugins, skills, conexões de LLM, orçamentos,
segredos, canais (Slack, Teams) e auditoria; ninguém de uma vê nada da outra.
"""
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, tenancy
from ..models import Agent, Employee, Organization, Team, User, now
from .access import Access, Forbidden
from .common import PlatformError, audit, iso, slugify

_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def is_instance_admin(acc: Access) -> bool:
    p = acc.p
    return p.via == "admin-token" or ("admin" in p.scopes and p.org_id is None) or (
        p.org_role == "admin" and (p.org_id or 1) == 1)


def _require(acc: Access):
    if not is_instance_admin(acc):
        raise Forbidden("só quem administra a instalação gerencia empresas")


def _count(db: Session, model, org_id: int) -> int:
    return db.scalar(tenancy.unscoped(select(func.count()).select_from(model).where(model.org_id == org_id))) or 0


def org_dict(db: Session, o: Organization, counts: bool = True) -> dict:
    d = {"id": o.id, "name": o.name, "slug": o.slug or "", "status": o.status or "active", "domains": o.domains or [],
         "created_at": iso(o.created_at) if o.created_at else None}
    if counts:
        d.update(users=_count(db, User, o.id), teams=_count(db, Team, o.id), agents=_count(db, Agent, o.id),
                 employees=_count(db, Employee, o.id))
    return d


def list_orgs(db: Session, acc: Access) -> list[dict]:
    _require(acc)
    return [org_dict(db, o) for o in db.scalars(select(Organization).order_by(Organization.id))]


def _domains(raw) -> list[str]:
    out = []
    for d in raw or []:
        d = str(d).strip().lower().lstrip("@")
        if not _DOMAIN.match(d):
            raise PlatformError(f"domínio inválido: {d}")
        out.append(d)
    return out


def create(db: Session, acc: Access, name: str, slug: str = "", admin_email: str = "", admin_name: str = "",
           admin_username: str = "", admin_password: str = "", domains: list | None = None) -> dict:
    """Cria a empresa com um time inicial e a primeira pessoa admin (que entra pelo SSO, pelo domínio, ou com usuário e
    senha locais, se informados)."""
    from .access import user_by_email
    _require(acc)
    if not (name or "").strip():
        raise PlatformError("a empresa precisa de um nome")
    s = slugify(slug or name)
    if db.scalar(select(Organization.id).where(Organization.slug == s)):
        raise PlatformError(f"já existe uma empresa '{s}'")
    email = (admin_email or "").strip().lower()
    if not email or "@" not in email:
        raise PlatformError("informe o e-mail da primeira pessoa admin da empresa")
    if user_by_email(db, email) is not None or db.scalar(tenancy.unscoped(select(User.id).where(User.email == email))):
        raise PlatformError(f"'{email}' já é uma pessoa da plataforma (uma pessoa pertence a uma empresa)")
    doms = _domains(domains)
    taken = [d for o in db.scalars(select(Organization)) for d in (o.domains or []) if d in doms]
    if taken:
        raise PlatformError(f"domínio já é de outra empresa: {', '.join(taken)}")
    o = Organization(name=name.strip()[:200], slug=s, domains=doms, status="active", created_at=now())
    db.add(o)
    db.commit()
    with tenancy.using(o.id):  # tudo o que nasce agora é da empresa nova
        team_slug, n = slugify(f"{s}-geral"), 2
        while db.scalar(tenancy.unscoped(select(Team.id).where(Team.slug == team_slug))):
            team_slug, n = f"{slugify(s + '-geral')}-{n}", n + 1
        t = Team(name="Geral", slug=team_slug, description=f"Time inicial de {o.name}", org_id=o.id)
        db.add(t)
        u = User(email=email, name=admin_name or email.split("@")[0], org_role="admin", active=True, org_id=o.id)
        if admin_username and admin_password:
            from .org import _check_username
            u.username, u.password_hash = _check_username(db, admin_username), auth.hash_password(admin_password)
        db.add(u)
        db.commit()
        from ..models import TeamMember
        db.add(TeamMember(team_id=t.id, user_id=u.id, role="maintainer", org_id=o.id))
        db.commit()
    audit(db, acc.p.name, "org.create", s, f"admin {email}")
    return {**org_dict(db, o), "admin": email, "team": team_slug}


def update(db: Session, acc: Access, org_id: int, changes: dict) -> dict:
    _require(acc)
    o = db.get(Organization, org_id)
    if o is None:
        raise PlatformError("empresa não encontrada")
    if changes.get("name"):
        o.name = str(changes["name"]).strip()[:200]
    if "domains" in changes:
        doms = _domains(changes["domains"])
        taken = [d for x in db.scalars(select(Organization).where(Organization.id != o.id)) for d in (x.domains or []) if d in doms]
        if taken:
            raise PlatformError(f"domínio já é de outra empresa: {', '.join(taken)}")
        o.domains = doms
    if changes.get("status") is not None:
        if changes["status"] not in ("active", "suspended"):
            raise PlatformError("status: active | suspended")
        if o.id == 1 and changes["status"] == "suspended":
            raise PlatformError("a empresa principal não é suspensa")
        o.status = changes["status"]
    db.commit()
    auth.clear_cache()
    audit(db, acc.p.name, "org.update", o.slug or str(o.id), ", ".join(sorted(k for k, v in changes.items() if v is not None)))
    return org_dict(db, o)


def org_for_email(db: Session, email: str) -> int:
    """A empresa de quem entra pela primeira vez pelo SSO: a dona do domínio do e-mail, ou a principal."""
    dom = (email or "").rsplit("@", 1)[-1].lower()
    for o in db.scalars(select(Organization).where(Organization.status != "suspended")):
        if dom in (o.domains or []):
            return o.id
    return 1


def suspended(db: Session, org_id: int) -> bool:
    o = db.get(Organization, org_id)
    return o is not None and o.status == "suspended"
