"""Issues (quadro kanban): issues, comentários, relações, inscrição, etiquetas, status e prefixo do quadro."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .. import services as svc
from .deps import acc, db_dep, guard

router = APIRouter(prefix="/api")


class IssueBody(BaseModel):
    title: str = ""
    description: str = ""
    team: str = ""
    project: str = ""
    status: str = ""
    priority: str = "none"
    assignee: str = ""  # me | user:<id ou e-mail> | employee:<slug>
    labels: list = []
    parent: str = ""
    stage: int | None = None
    start_date: str | None = None
    due_date: str | None = None
    estimate: int | None = None
    start: bool = True  # atribuída a um Digital employee: começa a rodada agora (false = "não iniciar ainda")


class CommentBody(BaseModel):
    body: str = ""
    parent_id: int | None = None


class RelationBody(BaseModel):
    kind: str
    other: str


class LabelBody(BaseModel):
    name: str
    color: str = ""


class StatusBody(BaseModel):
    name: str = ""
    category: str = ""
    color: str = ""


@router.get("/issues")
def issues(request: Request, team: str = "", project: str = "", assignee: str = "", status: str = "", priority: str = "",
           label: str = "", q: str = "", parent: str = "", view: str = "", db=Depends(db_dep)):
    """Uma pessoa vê as issues dela; view=org (admin/auditor) é a visão da empresa, só leitura, com as removidas."""
    return guard(lambda: svc.issues.list_issues(db, _acc(request, db, view), team, project, assignee, status, priority,
                                                label, q, parent))


def _acc(request: Request, db, view: str = ""):
    a = acc(request, db)
    if view == "org":
        if not (a.p.is_admin or a.p.is_auditor):
            raise svc.access.Forbidden("a visão da empresa é de admins e auditores")
        a.issue_view = "org"
    return a


@router.post("/issues")
def issue_create(body: IssueBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.create(db, acc(request, db), **body.model_dump()))


@router.get("/issues/settings")
def issue_settings(request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.settings(db, acc(request, db)))


@router.get("/issues/assignees")
def issue_assignees(request: Request, team: str = "", db=Depends(db_dep)):
    return guard(lambda: svc.issues.assignees(db, acc(request, db), team))


@router.put("/issues/settings/prefix")
def issue_prefix(body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.set_prefix(db, acc(request, db), str(body.get("prefix") or "")))


@router.post("/issues/statuses")
def issue_status_create(body: StatusBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.create_status(db, acc(request, db), body.name, body.category, body.color))


@router.patch("/issues/statuses/{key}")
def issue_status_update(key: str, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.update_status(db, acc(request, db), key, body))


@router.post("/issues/labels")
def issue_label_create(body: LabelBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.create_label(db, acc(request, db), body.name, body.color))


@router.delete("/issues/labels/{label_id}")
def issue_label_delete(label_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.delete_label(db, acc(request, db), label_id))


@router.get("/issues/{ref}")
def issue_get(ref: str, request: Request, view: str = "", db=Depends(db_dep)):
    def go():
        a = _acc(request, db, view)
        out = svc.issues.get_issue(db, a, ref)
        if a.p.user_id:  # abrir a issue marca como lida na caixa de entrada
            svc.inbox.mark(db, a, [f"issue:{out['id']}"], read=True)
        return out
    return guard(go)


@router.patch("/issues/{ref}")
def issue_update(ref: str, body: dict, request: Request, db=Depends(db_dep)):
    start = body.pop("start", None)
    return guard(lambda: svc.issues.update(db, acc(request, db), ref, body, start=start))


@router.delete("/issues/{ref}")
def issue_delete(ref: str, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.delete(db, acc(request, db), ref))


@router.post("/issues/{ref}/comments")
def issue_comment(ref: str, body: CommentBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.add_comment(db, acc(request, db), ref, body.body, body.parent_id))


@router.patch("/issues/{ref}/comments/{comment_id}")
def issue_comment_edit(ref: str, comment_id: int, body: CommentBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.edit_comment(db, acc(request, db), ref, comment_id, body.body))


@router.delete("/issues/{ref}/comments/{comment_id}")
def issue_comment_delete(ref: str, comment_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.delete_comment(db, acc(request, db), ref, comment_id))


@router.post("/issues/{ref}/comments/{comment_id}/resolve")
def issue_comment_resolve(ref: str, comment_id: int, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.resolve_comment(db, acc(request, db), ref, comment_id, bool(body.get("resolved", True))))


@router.post("/issues/{ref}/comments/{comment_id}/react")
def issue_comment_react(ref: str, comment_id: int, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.react(db, acc(request, db), ref, comment_id, str(body.get("emoji") or "")))


@router.post("/issues/{ref}/relations")
def issue_relate(ref: str, body: RelationBody, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.relate(db, acc(request, db), ref, body.kind, body.other))


@router.delete("/issues/{ref}/relations/{relation_id}")
def issue_unrelate(ref: str, relation_id: int, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.unrelate(db, acc(request, db), ref, relation_id))


@router.post("/issues/{ref}/subscription")
def issue_subscribe(ref: str, body: dict, request: Request, db=Depends(db_dep)):
    return guard(lambda: svc.issues.set_subscription(db, acc(request, db), ref, bool(body.get("subscribed", True))))


@router.post("/issues/{ref}/run")
def issue_run(ref: str, body: dict, request: Request, db=Depends(db_dep)):
    """Começa (ou retoma) a rodada do Digital employee responsável — o "Iniciar" depois de "não iniciar ainda"."""
    def go():
        a = acc(request, db)
        i = svc.issues.find_issue(db, a, ref, write=True)
        t = svc.issues.start_run(db, a, i, request=str(body.get("request") or ""))
        return {"task_id": t.id if t else None, "started": t is not None}
    return guard(go)
