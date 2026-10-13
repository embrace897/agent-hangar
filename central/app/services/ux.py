"""O que ajuda no dia a dia, fora do trabalho em si: o checklist de primeiros passos, os avisos da empresa e o
"ao vivo" (o que os Digital employees estão fazendo agora)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .. import config
from ..models import (
    Agent,
    Announcement,
    ApiKey,
    BudgetPolicy,
    Employee,
    EmployeeTask,
    LlmConnection,
    Organization,
    TaskEvent,
    Team,
    User,
    now,
)
from ..tenancy import current_org
from .access import Access, Forbidden
from .common import PlatformError, audit, iso


def _n(db, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _channel_on(db) -> bool:
    o = db.get(Organization, current_org())
    cfg = (o.notify if o else None) or {}
    return bool(config.SMTP_HOST or cfg.get("slack_bot_token") or cfg.get("teams_outgoing_secret")
                or cfg.get("telegram_bot_token") or cfg.get("discord_bot_token"))


# ------------------------------------------------------------------ primeiros passos
def onboarding(db, acc: Access, portal: bool = False) -> dict:
    """Os próximos passos de quem está começando: os da empresa (admin, no console) ou os da pessoa (no portal, mesmo
    para o admin). Some quando tudo está feito ou quando a pessoa esconde."""
    p, u = acc.p, (db.get(User, acc.p.user_id) if acc.p.user_id else None)
    if portal:
        from ..models import Issue
        from .workspace import policy
        uid = p.user_id or 0
        steps = [
            ("connect", "Conectar a sua IA (Claude, ChatGPT, Codex…)", _n(db, ApiKey, ApiKey.user_id == uid,
                                                                          ApiKey.revoked_at.is_(None)) > 0, "#/connect-ai"),
            ("issue", "Criar a primeira issue", _n(db, Issue, Issue.creator_user_id == uid) > 0, "#/issues"),
            ("decisions", "Escolher onde recebe as decisões", bool(u and (u.notify or {}).get("channel")), "#/decisions"),
        ]
        if policy(db)["personal_agents"]["enabled"]:
            steps.insert(1, ("personal", "Criar um agente pessoal", _n(db, Agent, Agent.personal_owner_id == uid) > 0,
                             "#/employees/new?personal=1"))
    elif p.is_admin:
        steps = [
            ("llm", "Conectar um modelo de IA", _n(db, LlmConnection) > 0 or not config.DEFAULT_MODEL.startswith("mock/"),
             "#/catalog"),
            ("team", "Criar um time", _n(db, Team) > 0, "#/teams/new"),
            ("people", "Convidar as pessoas", _n(db, User) > 1, "#/users"),
            ("agent", "Criar o primeiro agente", _n(db, Agent) > 0, "#/agents/new"),
            ("employee", "Contratar um Digital employee", _n(db, Employee) > 0, "#/employees/new"),
            ("channel", "Ligar um canal (Slack, Teams, Telegram, Discord ou e-mail)", _channel_on(db), "#/workforce/notify"),
            ("budget", "Definir um orçamento", _n(db, BudgetPolicy) > 0, "#/budgets"),
            ("task", "Ver a primeira tarefa concluída", _n(db, EmployeeTask, EmployeeTask.status == "done") > 0, "#/employees"),
        ]
    else:
        uid = p.user_id or 0
        steps = [
            ("team", "Entrar num time", bool(acc.teams), "#/teams"),
            ("connect", "Conectar uma ferramenta (Claude, Codex, LibreChat…)", _n(db, ApiKey, ApiKey.user_id == uid,
                                                                                 ApiKey.revoked_at.is_(None)) > 0, "#/connect"),
            ("task", "Pedir uma tarefa a um Digital employee", _n(db, EmployeeTask, EmployeeTask.requester_user_id == uid) > 0,
             "#/employees"),
            ("decisions", "Escolher onde recebe as decisões", bool(u and (u.notify or {}).get("channel")), "#/decisions"),
        ]
        if acc.p.is_user and any(r in ("maintainer", "developer") for r in acc.teams.values()):
            steps.insert(1, ("agent", "Criar o primeiro agente do time", _n(db, Agent, Agent.team_id.in_(list(acc.teams))) > 0,
                             "#/agents/new"))
    items = [{"id": i, "title": t, "done": bool(d), "href": h} for i, t, d, h in steps]
    hidden = bool(u and (u.notify or {}).get("onboarding_hidden"))
    return {"steps": items, "done": sum(x["done"] for x in items), "total": len(items), "hidden": hidden,
            "complete": all(x["done"] for x in items)}


def hide_onboarding(db, acc: Access, hidden: bool = True) -> dict:
    if not acc.p.user_id:
        raise Forbidden("entre com o seu usuário")
    u = db.get(User, acc.p.user_id)
    p = dict(u.notify or {})
    p["onboarding_hidden"] = bool(hidden)
    u.notify = p
    db.commit()
    return onboarding(db, acc)


# ------------------------------------------------------------------ avisos da empresa
def _ann(a: Announcement) -> dict:
    return {"id": a.id, "text": a.text, "level": a.level, "created_by": a.created_by, "created_at": iso(a.created_at),
            "expires_at": iso(a.expires_at) if a.expires_at else None}


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def announcements(db, acc: Access, all_: bool = False) -> list[dict]:
    rows = db.scalars(select(Announcement).order_by(Announcement.id.desc()).limit(50))
    t = now()
    return [_ann(a) for a in rows if all_ and acc.p.is_admin or not a.expires_at or _aware(a.expires_at) > t]


def announce(db, acc: Access, text: str, level: str = "info", days: int | None = None) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin publica avisos para a empresa")
    text = (text or "").strip()
    if not text:
        raise PlatformError("escreva o aviso")
    if level not in ("info", "warn"):
        raise PlatformError("level: info | warn")
    exp = now() + timedelta(days=int(days)) if days else None
    a = Announcement(text=text[:1000], level=level, created_by=acc.p.name, created_at=now(), expires_at=exp)
    db.add(a)
    db.commit()
    audit(db, acc.p.name, "announcement.create", str(a.id), text[:120])
    return _ann(a)


def unannounce(db, acc: Access, ann_id: int) -> dict:
    if not acc.p.is_admin:
        raise Forbidden("só o admin remove avisos")
    a = db.get(Announcement, ann_id)
    if a is None:
        raise PlatformError("aviso não encontrado")
    db.delete(a)
    db.commit()
    audit(db, acc.p.name, "announcement.delete", str(ann_id), "")
    return {"deleted": ann_id}


# ------------------------------------------------------------------ ao vivo
LIVE_KINDS = ("created", "run", "tool", "gate", "human_request", "decision", "done", "error", "note", "budget", "job",
              "shadow", "review")


def live(db, acc: Access, since: int = 0, limit: int = 60) -> dict:
    """Os eventos mais recentes das tarefas que a pessoa pode ler, e o que está rodando agora. `since` = o último id
    já visto (o portal pergunta de novo a cada poucos segundos)."""
    from .employees import can_read_task
    limit = max(1, min(int(limit or 60), 200))
    q = select(TaskEvent).where(TaskEvent.kind.in_(LIVE_KINDS)).order_by(TaskEvent.id.desc())
    if since:
        q = q.where(TaskEvent.id > int(since))
    tasks: dict[int, tuple] = {}

    def task_of(tid):
        if tid not in tasks:
            t = db.get(EmployeeTask, tid)
            e = db.get(Employee, t.employee_id) if t else None
            a = db.get(Agent, e.agent_id) if e else None
            tasks[tid] = (t, e, a, bool(t and e and can_read_task(acc, t, e)))
        return tasks[tid]

    events = []
    for ev in db.scalars(q.limit(limit * 4)):
        t, e, a, ok = task_of(ev.task_id)
        if not ok:
            continue
        pl = ev.payload or {}
        events.append({"id": ev.id, "at": iso(ev.at), "kind": ev.kind, "task": t.id, "title": t.title, "employee": a.slug,
                       "name": a.name, "status": t.status,
                       "detail": str(pl.get("tool") or pl.get("error") or pl.get("worker") or pl.get("decision")
                                     or pl.get("action_type") or "")[:160]})
        if len(events) >= limit:
            break
    running = []
    for t in db.scalars(select(EmployeeTask).where(EmployeeTask.status.in_(("in_progress", "waiting_human", "in_review")))
                        .order_by(EmployeeTask.started_at.desc()).limit(100)):
        _, e, a, ok = task_of(t.id)
        if ok:
            running.append({"task": t.id, "title": t.title, "employee": a.slug, "name": a.name, "status": t.status,
                            "since": iso(t.started_at) if isinstance(t.started_at, datetime) else None,
                            "external": (t.worker or "").startswith("external")})
    last = max([since or 0, *[x["id"] for x in events]])
    return {"events": events, "running": running, "last": last}
