"""Workspace do membro: a conta da empresa é o workspace; o admin (dono) define como os membros entram e o que
podem criar sozinhos; cada membro vê e cuida dos próprios acessos e dos próprios agentes.

- **Política de acesso** (admin): por quais caminhos se entra — senha, SSO (Entra ID, Google…), código de ativação,
  token pessoal — e as regras dos agentes pessoais (quem cria, quantos, com quais conexões de LLM, até quanto por mês).
- **Acessos** ("aparelhos"): cada sessão guarda como entrou, o navegador e o sistema, o IP e o último uso. O membro
  vê e encerra as dele; o admin vê e encerra as de qualquer membro.
- **Código de ativação** (a "chave virtual"): entra uma vez, sem senha — o admin gera para um membro, ou o membro gera
  para abrir em outro navegador. Uso único, 24 horas, só o hash guardado.
- **Agentes pessoais**: de um membro só (o time não vê; admin e auditor veem), dentro das regras da empresa — alçada e
  piso, orçamento por pessoa, conexões de LLM liberadas. Viram do time quando quem mantém o time aprova.
"""
import hashlib
import re
import secrets
from datetime import timedelta

from sqlalchemy import func, select

from .. import tenancy
from ..models import (
    ActivationCode,
    Agent,
    Employee,
    HumanRequest,
    Organization,
    TeamMember,
    UsageEvent,
    User,
    UserSession,
    now,
)
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso

METHODS = ("password", "sso", "activation", "key")
DEFAULT = {"login": {m: True for m in METHODS},
           "personal_agents": {"enabled": True, "who": "members", "max_per_member": 5, "llm_connections": [],
                               "monthly_budget_usd": 0}}
CODE_HOURS = 24


# ------------------------------------------------------------------ política
def policy(db, org_id: int | None = None) -> dict:
    o = db.get(Organization, org_id or tenancy.current_org())
    raw = (o.access_policy if o else None) or {}
    return {"login": {**DEFAULT["login"], **(raw.get("login") or {})},
            "personal_agents": {**DEFAULT["personal_agents"], **(raw.get("personal_agents") or {})}}


def set_policy(db, acc: Access, changes: dict) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin do workspace define a política de acesso")
    cur = policy(db)
    login = {**cur["login"], **{k: bool(v) for k, v in (changes.get("login") or {}).items() if k in METHODS}}
    if not any(login.values()):
        raise PlatformError("deixe pelo menos um jeito de entrar")
    pa = dict(cur["personal_agents"])
    for k, v in (changes.get("personal_agents") or {}).items():
        if k == "enabled":
            pa[k] = bool(v)
        elif k == "who":
            if v not in ("members", "developers"):
                raise PlatformError("personal_agents.who: members | developers")
            pa[k] = v
        elif k == "max_per_member":
            pa[k] = max(0, min(int(v), 50))
        elif k == "monthly_budget_usd":
            pa[k] = max(0.0, float(v))
        elif k == "llm_connections":
            pa[k] = sorted({str(x).strip() for x in v or [] if str(x).strip()})
    o = db.get(Organization, tenancy.current_org())
    o.access_policy = {"login": login, "personal_agents": pa}
    db.commit()
    audit(db, acc.p.name, "workspace.policy", o.slug or str(o.id), f"login={[m for m, on in login.items() if on]} personal={pa}")
    return policy(db)


def check_login(db, user: User, method: str):
    """A política do workspace do usuário deixa entrar por este caminho?"""
    if not policy(db, user.org_id)["login"].get(method, True):
        names = {"password": "senha", "sso": "SSO", "activation": "código de ativação", "key": "token pessoal"}
        raise PlatformError(f"este workspace não permite entrar por {names.get(method, method)}")


# ------------------------------------------------------------------ acessos (sessões)
def device(ua: str) -> str:
    """'Chrome · Windows' a partir do User-Agent (o suficiente para reconhecer o acesso)."""
    ua = ua or ""
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                   ("Safari/", "Safari")) if k in ua), "Navegador")
    system = next((n for k, n in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                  ("Mac OS X", "macOS"), ("Macintosh", "macOS"), ("Linux", "Linux")) if k in ua), "")
    if "python-httpx" in ua or "curl" in ua:
        browser = "Cliente de API"
    return f"{browser} · {system}" if system else browser


def session_dict(s: UserSession, current_hash: str = "") -> dict:
    return {"id": s.id, "device": device(s.user_agent), "method": s.method or "—", "ip": s.ip, "created_at": iso(s.created_at),
            "last_seen_at": iso(s.last_seen_at) if s.last_seen_at else iso(s.created_at), "expires_at": iso(s.expires_at),
            "current": bool(current_hash) and s.token_hash == current_hash}


def _active(q):
    return q.where(UserSession.revoked_at.is_(None), UserSession.expires_at > now())


def my_sessions(db, acc: Access, current_hash: str = "") -> list[dict]:
    if not acc.p.user_id:
        return []
    rows = db.scalars(_active(select(UserSession).where(UserSession.user_id == acc.p.user_id)).order_by(UserSession.id.desc()))
    return [session_dict(s, current_hash) for s in rows]


def revoke_mine(db, acc: Access, session_id: int | None = None, others_than: str = "") -> dict:
    from .. import auth
    q = _active(select(UserSession).where(UserSession.user_id == acc.p.user_id))
    if session_id:
        q = q.where(UserSession.id == session_id)
    n = 0
    for s in db.scalars(q).all():
        if others_than and s.token_hash == others_than:
            continue
        s.revoked_at = now()
        n += 1
    db.commit()
    auth.clear_cache()
    audit(db, acc.p.name, "session.revoke", acc.p.name, f"{n} acesso(s)")
    return {"revoked": n}


def org_sessions(db, acc: Access, user_id: int | None = None) -> list[dict]:
    if not (acc.p.is_admin or acc.p.is_auditor):
        raise Forbidden("só o admin (ou o auditor) vê os acessos do workspace")
    users = {u.id: u for u in db.scalars(select(User).where(User.org_id == tenancy.current_org()))}
    q = _active(select(UserSession).where(UserSession.user_id.in_(list(users) or [-1])))
    if user_id:
        q = q.where(UserSession.user_id == user_id)
    out = []
    for s in db.scalars(q.order_by(UserSession.id.desc()).limit(500)):
        u = users.get(s.user_id)
        out.append({**session_dict(s), "user": u.email if u else None, "user_id": s.user_id, "name": (u.name or u.email) if u else ""})
    return out


def revoke_org(db, acc: Access, session_id: int | None = None, user_id: int | None = None) -> dict:
    from .. import auth
    if not acc.p.is_admin:
        raise Forbidden("só o admin encerra acessos de outras pessoas")
    users = set(db.scalars(select(User.id).where(User.org_id == tenancy.current_org())))
    q = _active(select(UserSession).where(UserSession.user_id.in_(list(users) or [-1])))
    if session_id:
        q = q.where(UserSession.id == session_id)
    if user_id:
        q = q.where(UserSession.user_id == user_id)
    if not (session_id or user_id):
        raise PlatformError("diga qual acesso (session) ou de quem (user)")
    n = 0
    for s in db.scalars(q).all():
        s.revoked_at = now()
        n += 1
    db.commit()
    auth.clear_cache()
    audit(db, acc.p.name, "session.revoke_admin", str(user_id or session_id), f"{n} acesso(s)")
    return {"revoked": n}


# ------------------------------------------------------------------ código de ativação
def _hash(code: str) -> str:
    return hashlib.sha256(re.sub(r"[^A-Z0-9]", "", (code or "").upper()).encode()).hexdigest()


def issue_code(db, acc: Access, email: str = "") -> dict:
    """O admin gera para um membro (email); sem email, o membro gera para ele mesmo (outro navegador)."""
    if email:
        if not acc.p.is_admin:
            raise Forbidden("só o admin gera código de ativação para outra pessoa")
        u = db.scalar(select(User).where(User.email == email.strip().lower(), User.org_id == tenancy.current_org()))
    else:
        u = db.get(User, acc.p.user_id) if acc.p.user_id else None
    if u is None or not u.active:
        raise PlatformError("membro não encontrado (ou desligado)")
    if not policy(db, u.org_id)["login"].get("activation", True):
        raise PlatformError("este workspace não permite entrar por código de ativação")
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/I/L: fácil de ditar
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    db.add(ActivationCode(user_id=u.id, code_hash=_hash(code), created_by=acc.p.name,
                          expires_at=now() + timedelta(hours=CODE_HOURS), created_at=now()))
    db.commit()
    audit(db, acc.p.name, "activation.issue", u.email, f"vale {CODE_HOURS}h")
    return {"code": code, "user": u.email, "expires_at": iso(now() + timedelta(hours=CODE_HOURS)),
            "note": "mostrado uma vez só; vale uma entrada, por 24 horas"}


def redeem(db, code: str) -> User:
    """Troca o código por uma sessão (uma vez só). Não diz se o código existe ou expirou."""
    c = db.scalar(tenancy.unscoped(select(ActivationCode).where(ActivationCode.code_hash == _hash(code))))
    from . import employee_tasks as tasksmod
    if c is None or c.used_at is not None or tasksmod._aware(c.expires_at) < now():
        raise Forbidden("código inválido ou já usado")
    u = db.get(User, c.user_id)
    if u is None or not u.active:
        raise Forbidden("código inválido ou já usado")
    check_login(db, u, "activation")
    c.used_at = now()
    db.commit()
    return u


# ------------------------------------------------------------------ agentes pessoais
def personal_team(db, acc: Access, team: str | None) -> str:
    """O time de um agente pessoal: o pedido (se a pessoa for do time) ou o primeiro time dela."""
    from .access import find_team
    if team:
        t = find_team(db, team)
        if not (acc.p.is_admin or t.id in acc.teams):
            raise Forbidden("você não é deste time")
        return t.slug
    mine = sorted(acc.teams)
    if not mine:
        raise PlatformError("entre num time antes de criar agentes pessoais (é ele que dá o orçamento e as regras)")
    return acc.team_rows[mine[0]].slug


def check_personal(db, acc: Access, llm: dict | None):
    pa = policy(db)["personal_agents"]
    if not pa["enabled"]:
        raise Forbidden("este workspace não permite agentes pessoais")
    if not acc.p.user_id:
        raise Forbidden("agente pessoal é de um membro (entre com a sua conta)")
    if pa["who"] == "developers" and not acc.p.is_admin and not any(RANK[r] >= RANK["developer"] for r in acc.teams.values()):
        raise Forbidden("neste workspace só developers criam agentes pessoais")
    n = db.scalar(select(func.count()).select_from(Agent).where(Agent.personal_owner_id == acc.p.user_id)) or 0
    if n >= pa["max_per_member"]:
        raise PlatformError(f"limite de {pa['max_per_member']} agentes pessoais por membro")
    conn = str((llm or {}).get("connection") or "")
    if pa["llm_connections"] and conn not in pa["llm_connections"]:
        raise PlatformError("nos agentes pessoais, use uma destas conexões de LLM: " + ", ".join(pa["llm_connections"]))


def make_personal(db, a: Agent, user_id: int):
    a.personal_owner_id, a.visibility = user_id, "private"
    db.commit()


def personal_spent(db, user_id: int) -> float:
    from .budgets import _start
    ids = select(Agent.id).where(Agent.personal_owner_id == user_id)
    q = select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(UsageEvent.created_at >= _start("month"),
                                                                        UsageEvent.agent_id.in_(ids))
    return float(db.scalar(q) or 0)


def personal_stop(db, a: Agent) -> dict | None:
    """O teto mensal por membro dos agentes pessoais (da política do workspace)."""
    if not a.personal_owner_id:
        return None
    cap = float(policy(db)["personal_agents"]["monthly_budget_usd"] or 0)
    if cap <= 0:
        return None
    spent = personal_spent(db, a.personal_owner_id)
    if spent < cap:
        return None
    return {"incident": None, "scope": "agentes pessoais do membro", "amount_usd": cap, "window": "month"}


def promote(db, acc: Access, slug: str, note: str = "") -> dict:
    """Pede para o agente pessoal virar do time: decide quem mantém o time (não o dono)."""
    from . import employee_tasks as tasksmod
    from .employees import find
    e, a = find(db, acc, slug, "manage")
    if not a.personal_owner_id:
        raise PlatformError("este agente já é do time")
    if acc.p.user_id != a.personal_owner_id and not acc.p.is_admin:
        raise Forbidden("só o dono pede a promoção")
    open_ = db.scalar(select(HumanRequest.id).where(HumanRequest.employee_id == e.id, HumanRequest.kind == "promote_personal",
                                                    HumanRequest.status == "open"))
    if open_:
        raise PlatformError("já existe um pedido aberto para este agente")
    mts = [m.user_id for m in db.scalars(select(TeamMember).where(TeamMember.team_id == a.team_id, TeamMember.role == "maintainer"))
           if m.user_id != a.personal_owner_id]
    if not mts:
        raise PlatformError("o time não tem outro mantenedor para aprovar")
    owner = db.get(User, a.personal_owner_id)
    r = HumanRequest(employee_id=e.id, kind="promote_personal", action_type="prod_change", mode="approve",
                     question=f"{owner.name or owner.email} pede que o agente pessoal {a.name} passe a ser do time. "
                              f"{note}".strip()[:4000],
                     action_payload={"agent": a.slug, "owner": owner.email}, assigned_user_id=mts[0],
                     fallback_user_id=mts[1] if len(mts) > 1 else None, approvals=[], created_at=now())
    db.add(r)
    db.commit()
    tasksmod._notify_request(db, e, r)
    audit(db, acc.p.name, "personal.promote_request", a.slug, f"#{r.id}")
    return {"request": r.id}


def _register_handler():
    from .decision_kinds import close, handler

    @handler("promote_personal")
    def decide_promote(db, acc, r: HumanRequest, e: Employee, task, decision: str, edit: dict, reason: str, who: str):
        from .employee_tasks import request_dict
        if decision not in ("approve", "reject"):
            raise PlatformError("promoção: approve | reject")
        a = db.get(Agent, e.agent_id)
        if acc.p.user_id and acc.p.user_id == a.personal_owner_id:
            raise Forbidden("quatro olhos: o dono não aprova a própria promoção")
        if decision == "approve":
            o = db.get(Organization, tenancy.current_org())
            a.personal_owner_id, a.visibility = None, (o.default_visibility if o else "org") or "org"
        close(r, decision, reason, who)
        db.commit()
        audit(db, who, f"personal.promote_{decision}", a.slug, f"#{r.id}")
        return request_dict(db, r, e)


_register_handler()


# ------------------------------------------------------------------ o painel do membro
def home(db, acc: Access) -> dict:
    """A página inicial do membro: decisões dele, tarefas pedidas por ele, os agentes dele trabalhando, custo do mês."""
    from ..models import EmployeeTask
    from .budgets import _start
    from .employees import employee_dict
    uid = acc.p.user_id
    if not uid:
        return {}
    mine = []
    for e, a in db.execute(select(Employee, Agent).join(Agent, Agent.id == Employee.agent_id).where(Employee.status != "offboarded")).all():
        if a.personal_owner_id == uid or e.manager_user_id == uid or e.owner_user_id == uid:
            d = employee_dict(db, e, acc)
            running = db.scalar(select(EmployeeTask).where(EmployeeTask.employee_id == e.id, EmployeeTask.status == "in_progress")
                                .order_by(EmployeeTask.id.desc()))
            mine.append({**d, "personal": bool(a.personal_owner_id), "working_on": {"id": running.id, "title": running.title} if running else None})
    decide = db.scalar(select(func.count()).select_from(HumanRequest).where(
        HumanRequest.status == "open", (HumanRequest.assigned_user_id == uid) | (HumanRequest.fallback_user_id == uid))) or 0
    tasks = list(db.scalars(select(EmployeeTask).where(EmployeeTask.requester_user_id == uid).order_by(EmployeeTask.id.desc()).limit(8)))
    since = _start("month")
    ids = [x["id"] for x in mine if x.get("id")]
    cost = float(db.scalar(select(func.coalesce(func.sum(EmployeeTask.cost_usd), 0.0)).where(
        EmployeeTask.created_at >= since, EmployeeTask.employee_id.in_(ids or [-1]))) or 0)
    pa = policy(db)["personal_agents"]
    return {"workspace": (db.get(Organization, tenancy.current_org()).name if db.get(Organization, tenancy.current_org()) else ""),
            "to_decide": decide, "agents": mine,
            "my_tasks": [{"id": t.id, "title": t.title, "status": t.status, "created_at": iso(t.created_at)} for t in tasks],
            "month_cost_usd": round(cost, 4), "personal_spent_usd": round(personal_spent(db, uid), 4),
            "personal_cap_usd": pa["monthly_budget_usd"], "personal_allowed": pa["enabled"],
            "personal_count": sum(1 for x in mine if x["personal"]), "personal_max": pa["max_per_member"]}
