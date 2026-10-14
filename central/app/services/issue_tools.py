"""As ferramentas do Digital employee numa rodada de issue: ler a issue, comentar o andamento, mudar o status (menos
concluir ou cancelar) e dividir o trabalho em sub-issues. Repassar uma sub-issue para outro Digital employee é
delegar: passa pela alçada (e roda como pedido de quem atribuiu a issue a ele)."""
from sqlalchemy import select

from ..models import Issue, User
from . import issues as im
from .access import Access, principal_for
from .common import PlatformError
from .platform_tools import Ctx, side_effect, tool


def _issue(ctx: Ctx, ref: str = "") -> Issue:
    i = im._by_ref(ctx.db, ref) if ref else (ctx.db.get(Issue, ctx.task.issue_id) if ctx.task.issue_id else None)
    if i is None:
        raise PlatformError("esta tarefa não veio de uma issue: as ferramentas de issue não se aplicam aqui (siga a tarefa "
                            "sem elas; para ler uma issue, passe a chave, ex.: HGR-12)")
    return i


def _requester_acc(ctx: Ctx) -> Access | None:
    """As permissões de quem pediu esta rodada: delegar não dá ao agente mais acesso do que a pessoa tem."""
    u = ctx.db.get(User, ctx.task.requester_user_id) if ctx.task.requester_user_id else None
    return Access(ctx.db, principal_for(u)) if u is not None and u.active else None


@tool("get_issue", "Lê uma issue do quadro pela chave (ex.: HGR-12): descrição, status, sub-issues, relações e os "
      "últimos comentários. Sem a chave, só numa tarefa que veio de uma issue (o título começa com a chave).",
      {"properties": {"key": {"type": "string", "description": "chave da issue (opcional; padrão: a desta tarefa)"}}}, "issues")
def get_issue(ctx: Ctx, args: dict):
    i = _issue(ctx, str(args.get("key") or ""))
    if ctx.task.issue_id and i.id != ctx.task.issue_id and i.team_id and i.team_id != ctx.e.team_id:
        raise PlatformError("essa issue é de outro time")
    return im._context(ctx.db, i, "", False)


@tool("issue_comment", "Só em tarefas que vieram de uma issue (o título começa com a chave, ex.: HGR-12). Comenta na issue (markdown): o andamento, o que encontrou, uma pergunta ou o "
      "bloqueio. Quem está inscrito vê na caixa de entrada.",
      {"properties": {"body": {"type": "string"}}, "required": ["body"]}, "issues")
def issue_comment(ctx: Ctx, args: dict):
    body = str(args.get("body") or "").strip()
    if not body:
        raise PlatformError("escreva o comentário (body)")
    i = _issue(ctx)
    c = im.agent_comment(ctx.db, i, ctx.e, body, ctx.task.id)
    ctx.db.commit()
    return {"comment": c.id, "issue": im.key_of(ctx.db, i)}


@tool("issue_set_status", "Só em tarefas que vieram de uma issue. Muda o status da issue: in_progress, blocked (diga o motivo num comentário), "
      "in_review (entregue) ou um status do time das mesmas categorias. Concluir e cancelar ficam com quem revisa.",
      {"properties": {"status": {"type": "string"}, "comment": {"type": "string", "description": "opcional"}},
       "required": ["status"]}, "issues")
def issue_set_status(ctx: Ctx, args: dict):
    i = _issue(ctx)
    out = im.update(ctx.db, None, im.key_of(ctx.db, i), {"status": str(args.get("status") or "")}, by_employee=ctx.e)
    if str(args.get("comment") or "").strip():
        im.agent_comment(ctx.db, i, ctx.e, str(args["comment"]), ctx.task.id)
        ctx.db.commit()
    return {"issue": out["key"], "status": out["status"]}


@tool("create_sub_issue", "Só em tarefas que vieram de uma issue. Divide a issue: cria uma sub-issue (título, descrição, prioridade, etapa 1, 2, "
      "3…). Com assignee (o slug de um Digital employee do time ou o e-mail de uma pessoa), ela é atribuída — para outro "
      "Digital employee isso é delegar, e passa pela alçada.",
      {"properties": {"title": {"type": "string"}, "description": {"type": "string"},
                      "priority": {"type": "string", "enum": list(im.PRIORITIES)},
                      "stage": {"type": "integer", "description": "etapa (opcional): a próxima só depois desta"},
                      "assignee": {"type": "string", "description": "slug do Digital employee ou e-mail (opcional)"}},
       "required": ["title"]}, "issues")
def create_sub_issue(ctx: Ctx, args: dict):
    db, parent = ctx.db, _issue(ctx)
    title = str(args.get("title") or "").strip()
    if not title:
        raise PlatformError("a sub-issue precisa de um título")
    assignee = str(args.get("assignee") or "").strip()
    a_slug = assignee[9:] if assignee.startswith("employee:") else assignee
    other = None
    if assignee and "@" not in assignee:
        other = im._employee_by_slug(db, a_slug)
        if other is None:
            raise PlatformError(f"'{a_slug}' não é um Digital employee")
        if other.team_id and parent.team_id and other.team_id != parent.team_id:
            raise PlatformError("esse Digital employee é de outro time")
    if other is not None and other.id != ctx.e.id:  # delegar a outro funcionário: a alçada decide
        g = side_effect(ctx, "create_sub_issue", "hangar:delegate_issue", {**args, "parent": im.key_of(db, parent)})
        if g is not None:
            return g
    acc = _requester_acc(ctx)
    out = im.create(db, None, title, str(args.get("description") or ""), parent=im.key_of(db, parent),
                    priority=str(args.get("priority") or "none"), stage=args.get("stage"),
                    status="todo" if assignee else "backlog", by_employee=ctx.e)
    i = db.get(Issue, out["id"])
    if assignee:
        if other is not None:
            i.assignee_employee_id = other.id
        else:
            u = db.scalar(select(User).where(User.email == assignee.lower()))
            if u is None or not u.active:
                raise PlatformError(f"pessoa '{assignee}' não encontrada")
            i.assignee_user_id = u.id
            im.subscribe(db, i, u.id)
        im.activity(db, i, im._actor(None, ctx.e, db)[0], None, "assignee", {"to": assignee})
        db.commit()
        if other is not None:
            im.start_run(db, acc, i, request=f"Sub-issue criada por {ctx.agent.name} a partir de {im.key_of(db, parent)}.",
                         by_employee=ctx.e)
    return {"issue": out["key"], "assignee": assignee or None}

