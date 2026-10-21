"""Várias empresas numa instalação.

Cada requisição age dentro de uma empresa (o contexto): a de quem chama (usuário, chave ou agente). O token admin e os
admins da empresa principal (id 1) podem escolher a empresa pelo cabeçalho X-Hangar-Org (id ou slug).

- Toda consulta ORM a uma tabela da empresa (as que têm org_id) ganha o filtro `org_id = contexto`, inclusive
  `db.get` por id: um endpoint que esquece de checar não vaza dado de outra empresa.
- Todo registro novo recebe o org_id do contexto; sem contexto (os laços em segundo plano), o do registro pai (a
  tarefa, o funcionário, o agente…), e só em último caso a empresa 1.
- Sem contexto não há filtro: o executor de tarefas, os agendamentos e as varreduras trabalham com todas as empresas e
  usam a empresa do próprio registro.
- `unscoped(stmt)` desliga o filtro de propósito (por exemplo, para checar se um slug global já está em uso).

Slugs de agentes, times e projetos continuam únicos na instalação (viram nomes de container e de URL); nomes de skills,
conexões de LLM, MCPs, plugins e o catálogo de ações são únicos por empresa.
"""
import contextlib
import contextvars

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

_org: contextvars.ContextVar[int | None] = contextvars.ContextVar("hangar_org", default=None)
# quem chama sempre enxerga a própria pessoa (quem administra a instalação age dentro de outra empresa)
_me: contextvars.ContextVar[int | None] = contextvars.ContextVar("hangar_me", default=None)
# tabelas da instalação (não de uma empresa): a própria empresa, o efêmero, o login e a infraestrutura compartilhada
GLOBAL_TABLES = {"organizations", "ephemeral", "icon_cache", "oauth_clients", "gateway_servers", "sso_providers", "group_mappings",
                 "scim_groups", "user_sessions"}
# de onde tirar a empresa de um registro novo criado sem contexto: a primeira coluna que apontar para um pai
PARENTS = ("task_id", "employee_id", "agent_id", "plugin_id", "install_id", "skill_id", "policy_id", "team_id",
           "user_id", "routine_id")


def current() -> int | None:
    return _org.get()


def current_user() -> int | None:
    """A pessoa da requisição (sessão, token pessoal, OAuth do MCP); None fora de uma requisição ou com o token do admin."""
    return _me.get()


def current_org() -> int:
    """A empresa do contexto, ou a principal (1) fora de uma requisição."""
    return _org.get() or 1


def set_org(org_id: int | None, user_id: int | None = None):
    _org.set(org_id)
    _me.set(user_id)


@contextlib.contextmanager
def using(org_id: int | None):
    token = _org.set(org_id)
    try:
        yield
    finally:
        _org.reset(token)


def unscoped(stmt):
    return stmt.execution_options(all_orgs=True)


def _tenant_classes():
    from .models import Base
    out = []
    for m in Base.registry.mappers:
        cls = m.class_
        if getattr(cls, "__tablename__", "") not in GLOBAL_TABLES and "org_id" in m.columns:
            out.append(cls)
    return out


_TENANT: list = []


def _classes():
    if not _TENANT:
        _TENANT.extend(_tenant_classes())
    return _TENANT


@event.listens_for(Session, "do_orm_execute")
def _scope(state):
    org = _org.get()
    if org is None or not state.is_select or state.execution_options.get("all_orgs"):
        return
    from sqlalchemy import or_

    from .models import User
    me = _me.get() or 0
    state.statement = state.statement.options(
        *[with_loader_criteria(c, lambda cls: cls.org_id == org, include_aliases=True) for c in _classes() if c is not User],
        with_loader_criteria(User, lambda cls: or_(cls.org_id == org, cls.id == me), include_aliases=True))


def _parent_model(col: str):
    from . import models
    return {"task_id": models.EmployeeTask, "employee_id": models.Employee, "agent_id": models.Agent,
            "plugin_id": models.Plugin, "install_id": models.PluginInstall, "skill_id": models.Skill,
            "policy_id": models.BudgetPolicy, "team_id": models.Team, "user_id": models.User,
            "routine_id": models.EmployeeRoutine}.get(col)


@event.listens_for(Session, "before_flush")
def _stamp(session, flush_context, instances):
    org = _org.get()
    for obj in list(session.new):
        if getattr(obj, "__tablename__", "") in GLOBAL_TABLES or not hasattr(obj, "org_id") or getattr(obj, "org_id", None):
            continue
        value = org
        if value is None:
            with session.no_autoflush:
                for col in PARENTS:
                    pid = getattr(obj, col, None)
                    model = _parent_model(col) if pid else None
                    if model is None:
                        continue
                    parent = session.get(model, pid, execution_options={"all_orgs": True})
                    if parent is not None and getattr(parent, "org_id", None):
                        value = parent.org_id
                        break
        obj.org_id = value or 1


def active_orgs() -> list[int]:
    """As empresas ativas (os laços em segundo plano passam por cada uma)."""
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Organization
    with SessionLocal() as s:
        ids = list(s.scalars(select(Organization.id).where(Organization.status != "suspended").order_by(Organization.id)))
    return ids or [1]


def org_of(model, row_id) -> int:
    """A empresa de um registro (sem filtro): para rodar o trabalho dele no contexto certo."""
    from .db import SessionLocal
    with SessionLocal() as s:
        row = s.get(model, row_id, execution_options={"all_orgs": True})
        return (getattr(row, "org_id", None) or 1) if row is not None else 1
