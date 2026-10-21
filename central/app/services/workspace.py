"""Workspace do membro: a conta da empresa é o workspace; o admin (dono) define como os membros entram e o que
podem criar sozinhos; cada membro vê e cuida dos próprios acessos e dos próprios agentes.

- **Política de acesso** (admin): por quais caminhos se entra — senha, SSO (Entra ID, Google…), código de ativação,
  token pessoal — e as regras dos agentes pessoais (quem cria, quantos, com quais conexões de LLM, até quanto por mês).
- **Acessos** ("aparelhos"): cada sessão guarda como entrou, o navegador e o sistema, o IP e o último uso. O membro
  vê e encerra as dele; o admin vê e encerra as de qualquer membro.
- **Código de ativação** (a "chave virtual"): entra uma vez, sem senha — o admin gera para um membro, ou o membro gera
  para abrir em outro navegador. Uso único, 24 horas, só o hash guardado.
- **E-mail corporativo** (opcional, no cadastro da empresa): o dono informa os domínios da empresa (@empresa.com.br,
  um ou mais) e pode exigir que todo membro tenha e-mail de um deles. Vale em todo caminho que cria ou muda uma conta:
  cadastro e convite, primeiro login pelo SSO, SCIM e troca de e-mail. Quem já era membro fora dos domínios não sai
  sozinho: o admin vê a lista e decide.
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
    o.access_policy = {**(o.access_policy or {}), "login": login, "personal_agents": pa}  # o resto (e-mail…) fica
    db.commit()
    audit(db, acc.p.name, "workspace.policy", o.slug or str(o.id), f"login={[m for m, on in login.items() if on]} personal={pa}")
    return policy(db)


# ------------------------------------------------------------------ cadastro da empresa e e-mail corporativo
# provedores de e-mail pessoal: não são domínio de empresa
PUBLIC_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "outlook.com.br", "hotmail.com", "hotmail.com.br",
                  "live.com", "msn.com", "yahoo.com", "yahoo.com.br", "ymail.com", "icloud.com", "me.com", "mac.com",
                  "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com", "gmx.net", "mail.com", "zoho.com",
                  "yandex.com", "yandex.ru", "uol.com.br", "bol.com.br", "terra.com.br", "ig.com.br", "globo.com",
                  "r7.com", "qq.com", "163.com", "tutanota.com", "hey.com", "fastmail.com"}


def email_rule(db, org_id: int | None = None) -> dict:
    o = db.get(Organization, org_id or tenancy.current_org())
    raw = ((o.access_policy if o else None) or {}).get("email_domains") or {}
    doms = list((o.domains if o else None) or [])
    return {"domains": doms, "enforce": bool(raw.get("enforce")) and bool(doms), "subdomains": bool(raw.get("subdomains"))}


def email_allowed(rule: dict, email: str) -> bool:
    if not rule["enforce"]:
        return True
    dom = (email or "").strip().lower().rsplit("@", 1)[-1]
    return any(dom == d or (rule["subdomains"] and dom.endswith("." + d)) for d in rule["domains"])


def check_email(db, email: str, org_id: int | None = None):
    """Recusa um e-mail fora dos domínios da empresa, quando ela exige e-mail corporativo."""
    rule = email_rule(db, org_id)
    if not email_allowed(rule, email):
        doms = ", ".join("@" + d for d in rule["domains"])
        audit(db, "sistema", "workspace.email_refused", (email or "")[:200], f"fora de {doms}")
        raise PlatformError(f"'{email}' não é um e-mail da empresa: só entram membros com e-mail {doms}"
                            f"{' (ou subdomínios)' if rule['subdomains'] else ''}")


def company(db, acc: Access) -> dict:
    """O cadastro da empresa: nome, domínios, a regra de e-mail corporativo e quem está fora dela (para o admin)."""
    if not (acc.p.is_admin or acc.p.is_auditor):
        raise Forbidden("só o admin vê o cadastro da empresa")
    o = db.get(Organization, tenancy.current_org())
    rule = email_rule(db)
    raw = o.access_policy or {}
    out = {"name": o.name, "domains": rule["domains"], "enforce": rule["enforce"], "subdomains": rule["subdomains"],
           "setup_done": bool(raw.get("setup_done_at")), "setup_done_at": raw.get("setup_done_at"),
           "my_email": acc.p.name if acc.p.is_user else ""}
    probe = {**rule, "enforce": bool(rule["domains"])}  # quem ficaria de fora com a regra ligada
    out["outside"] = [{"id": u.id, "email": u.email, "name": u.name, "org_role": u.org_role, "active": u.active}
                      for u in db.scalars(select(User).where(User.active.is_(True), User.org_id == tenancy.current_org())
                                        .order_by(User.email))
                      if not email_allowed(probe, u.email)] if rule["domains"] else []
    return out


def set_company(db, acc: Access, name: str | None = None, domains: list | None = None, enforce: bool | None = None,
                subdomains: bool | None = None, skip: bool = False) -> dict:
    """Primeiro acesso (ou Configurações → Empresa): o nome, os domínios e se só entram e-mails desses domínios."""
    from . import orgs
    if not acc.p.is_admin:
        raise Forbidden("só o admin cadastra a empresa")
    o = db.get(Organization, tenancy.current_org())
    raw = dict(o.access_policy or {})
    if not skip:
        if name is not None:
            if not name.strip():
                raise PlatformError("a empresa precisa de um nome")
            o.name = name.strip()[:200]
        doms = list(o.domains or [])
        if domains is not None:
            doms = list(dict.fromkeys(orgs._domains(domains)))
            public = [d for d in doms if d in PUBLIC_DOMAINS]
            if public:
                raise PlatformError(f"{', '.join(public)} é provedor de e-mail pessoal, não domínio de empresa")
            taken = [d for x in db.scalars(tenancy.unscoped(select(Organization).where(Organization.id != o.id)))
                     for d in (x.domains or []) if d in doms]
            if taken:
                raise PlatformError(f"domínio já é de outra empresa: {', '.join(taken)}")
        rule = {**(raw.get("email_domains") or {})}
        if enforce is not None:
            rule["enforce"] = bool(enforce)
        if subdomains is not None:
            rule["subdomains"] = bool(subdomains)
        if rule.get("enforce"):
            if not doms:
                raise PlatformError("informe ao menos um domínio para exigir e-mail corporativo")
            me = acc.p.name if acc.p.is_user else ""
            if me and not email_allowed({"domains": doms, "enforce": True, "subdomains": bool(rule.get("subdomains"))}, me):
                raise PlatformError(f"o seu e-mail ({me}) não é de nenhum desses domínios: com a regra ligada, você "
                                    "não conseguiria mais convidar a si mesmo nem trocar de conta. Inclua o seu domínio")
        o.domains, raw["email_domains"] = doms, rule
    raw.setdefault("setup_done_at", iso(now()))
    o.access_policy = raw
    db.commit()
    from .. import auth
    auth.clear_cache()
    r = email_rule(db)
    audit(db, acc.p.name, "workspace.company", o.slug or str(o.id),
          "pulado no primeiro acesso" if skip else f"{o.name}; domínios {r['domains']}; e-mail corporativo "
          f"{'exigido' if r['enforce'] else 'opcional'}{' (com subdomínios)' if r['subdomains'] else ''}")
    return company(db, acc)


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
    u.last_login_at = now()
    db.commit()
    return u


# ------------------------------------------------------------------ convites (só o admin, no console)
INVITE_DAYS = 7


def _org_name(db) -> str:
    o = db.get(Organization, tenancy.current_org())
    return o.name if o else "Agent Hangar"


def invite(db, acc: Access, email: str, name: str = "", team: str = "", role: str = "consumer",
           org_role: str = "member", send: bool = True) -> dict:
    """O admin cadastra a pessoa (e o time dela) e manda o convite por e-mail: o link do portal (/app) entra uma
    vez, sem senha, por 7 dias (código de ativação). Sem código liberado na política, o link leva ao login (SSO).
    Para quem ainda não entrou, chamar de novo reenvia (o link anterior deixa de valer)."""
    from .. import config
    from . import notify, org
    if not acc.p.is_admin:
        raise Forbidden("só o admin convida membros")
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise PlatformError("informe o e-mail da pessoa")
    if role not in ("consumer", "developer", "maintainer"):
        raise PlatformError("papel no time: consumer | developer | maintainer")
    check_email(db, email)  # vale também para reenviar o convite de quem foi cadastrado antes da regra
    u = db.scalar(select(User).where(User.email == email, User.org_id == tenancy.current_org()))
    if u is not None and u.last_login_at is not None:
        raise PlatformError("essa pessoa já entrou na plataforma: ajuste times e papel na lista de membros")
    if u is None:
        u = org.create_user(db, acc, email, name, org_role)
    elif not u.active:
        org.update_user(db, acc, u.id, active=True)
    if team:
        org.add_member(db, acc, team, email, role)
    for old in db.scalars(select(ActivationCode).where(ActivationCode.user_id == u.id, ActivationCode.used_at.is_(None))):
        db.delete(old)  # reenvio: só o link mais novo vale
    link, code, expires = f"{config.PUBLIC_BASE_URL}/app/", "", None
    if policy(db)["login"].get("activation", True):
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        expires = now() + timedelta(days=INVITE_DAYS)
        db.add(ActivationCode(user_id=u.id, code_hash=_hash(code), created_by=f"invite:{acc.p.name}", expires_at=expires,
                              created_at=now()))
        link += f"#/login?code={code}"
    db.commit()
    sent, error = False, ""
    if send and notify.email_ready():
        ws = _org_name(db)
        lines = [f"{acc.p.name} convidou você para o workspace {ws} no Agent Hangar.",
                 "No portal você acompanha e cria os seus agentes, decide os pedidos deles e conecta a sua IA "
                 "(Claude, ChatGPT, Codex, Cursor…).",
                 (f"O link entra uma vez, sem senha, e vale por {INVITE_DAYS} dias. Depois, entre com a conta da empresa "
                  "ou gere um código em Meus acessos.") if code else "Entre com a conta da empresa (SSO)."]
        try:
            notify._email(email, f"Convite para o Agent Hangar — {ws}", "\n".join([*lines, link]),
                          notify._mail_html(f"Você foi convidado para {ws}", lines, [("Entrar no Agent Hangar", link)]))
            sent = True
        except Exception as e:  # noqa: BLE001 — o convite fica criado; o admin copia o link
            error = f"o e-mail não saiu ({type(e).__name__}); copie o link e mande para a pessoa"
    audit(db, acc.p.name, "member.invite", email, f"time={team or '-'} papel={role} enviado={sent}")
    return {"user": email, "user_id": u.id, "link": link, "sent": sent, "email_ready": notify.email_ready(),
            "error": error, "expires_at": iso(expires) if expires else None}


def invites(db, acc: Access) -> list[dict]:
    """Convites que ainda esperam a pessoa entrar."""
    if not (acc.p.is_admin or acc.p.is_auditor):
        raise Forbidden("só o admin (ou o auditor) vê os convites")
    from . import employee_tasks as tasksmod
    out = []
    rows = db.execute(select(ActivationCode, User).join(User, User.id == ActivationCode.user_id).where(
        ActivationCode.created_by.like("invite:%"), ActivationCode.used_at.is_(None), User.last_login_at.is_(None),
        User.org_id == tenancy.current_org()).order_by(ActivationCode.id.desc())).all()
    for c, u in rows:
        out.append({"user_id": u.id, "email": u.email, "name": u.name, "invited_by": c.created_by[len("invite:"):],
                    "invited_at": iso(c.created_at), "expires_at": iso(c.expires_at),
                    "expired": tasksmod._aware(c.expires_at) < now()})
    return out


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
def built_agents(db, acc: Access) -> list[dict]:
    """Os agentes comuns (não Digital employees) da pessoa: os pessoais dela, os dos times que ela mantém e os que ela
    criou. Sem isto, quem monta um agente (pelo portal ou pela IA dela, via MCP) não o encontrava em Meus agentes."""
    from ..models import AgentVersion, Schedule, Team
    uid = acc.p.user_id
    maintained = {m.team_id for m in db.scalars(select(TeamMember).where(TeamMember.user_id == uid,
                                                                         TeamMember.role == "maintainer"))}
    employees = set(db.scalars(select(Employee.agent_id)))
    # quem criou: pelo id; versões gravadas antes do id existir (created_user_id vazio) caem no nome
    first_by = {aid: (cuid, by) for aid, cuid, by in db.execute(select(
        AgentVersion.agent_id, AgentVersion.created_user_id, AgentVersion.created_by).where(AgentVersion.version == 1)).all()}
    u = db.get(User, uid)
    # quem a pessoa já foi no "created_by" antigo: o nome (portal), o e-mail ou o usuário, e pelo MCP "<e-mail> (app:Claude)"
    me = {x.strip().lower() for x in (acc.p.name, u.name if u else "", u.email if u else "",
                                      getattr(u, "username", "") if u else "") if x and x.strip()}

    def created(aid: int) -> bool:
        cuid, by = first_by.get(aid, (None, ""))
        if cuid is not None:
            return cuid == uid
        return (by or "").split(" (app:")[0].strip().lower() in me
    teams = {t.id: t.name for t in db.scalars(select(Team))}
    since = now() - timedelta(days=30)
    out = []
    for a in db.scalars(select(Agent).order_by(Agent.name)):
        if a.id in employees:
            continue
        why = ("personal" if a.personal_owner_id == uid else "maintainer" if a.team_id in maintained
               else "creator" if created(a.id) and not a.personal_owner_id else "")
        if not why:
            continue
        sch = list(db.scalars(select(Schedule).where(Schedule.agent_id == a.id, Schedule.enabled.is_(True))))
        nxt = min((s.next_run_at for s in sch if s.next_run_at), default=None)
        cost = float(db.scalar(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
            UsageEvent.agent_id == a.id, UsageEvent.created_at >= since)) or 0)
        out.append({"slug": a.slug, "name": a.name, "objective": (a.objective or "")[:200], "status": a.status,
                    "version": a.current_version, "kind": a.kind, "team": teams.get(a.team_id, ""), "why": why,
                    "personal": bool(a.personal_owner_id), "schedules": len(sch),
                    "next_run_at": iso(nxt) if nxt else None, "cost_30d_usd": round(cost, 4)})
    return out


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
    from ..models import LlmConnection
    return {"llm_ready": bool(db.scalar(select(func.count()).select_from(LlmConnection))), "workspace": (db.get(Organization, tenancy.current_org()).name if db.get(Organization, tenancy.current_org()) else ""),
            "to_decide": decide, "agents": mine, "built_agents": built_agents(db, acc),
            "my_tasks": [{"id": t.id, "title": t.title, "status": t.status, "created_at": iso(t.created_at)} for t in tasks],
            "month_cost_usd": round(cost, 4), "personal_spent_usd": round(personal_spent(db, uid), 4),
            "personal_cap_usd": pa["monthly_budget_usd"], "personal_allowed": pa["enabled"],
            "personal_count": sum(1 for x in mine if x["personal"]), "personal_max": pa["max_per_member"]}
