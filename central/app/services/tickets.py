"""Sistemas de chamados como porta de entrada: Jira, Linear, Asana e ServiceNow alimentam uma fila do time.

- **Entrada:** de N em N minutos (e na hora, com o webhook do sistema) os chamados do filtro viram itens da fila —
  com categoria e prioridade pelo mapeamento, e o Digital employee ou as pessoas pegam como qualquer item.
- **Volta:** o andamento do item vira comentário no chamado (em atendimento, repassado a uma pessoa, concluído com o
  resultado), os anexos do resultado sobem para o chamado e o estado do chamado muda (transição configurada).
- **Conversa:** comentários novos no chamado chegam à conversa da tarefa do Digital employee (ou ao item, se estiver
  com uma pessoa); chamado fechado lá fora cancela o item aberto aqui.
- **Governança:** o conector usa a credencial da empresa (no cofre) e escreve num sistema de fora — só entra no ar
  depois de outra pessoa aprovar (quatro olhos), e volta a rascunho a cada mudança. Tudo fica na auditoria.
"""
import base64
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy import select

from .. import config
from ..models import (
    EmployeeTask,
    TaskFile,
    TaskMessage,
    Team,
    TicketConnector,
    TicketLink,
    WorkItem,
    WorkItemFile,
    WorkQueue,
    now,
)
from . import employee_tasks as tasksmod
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso, slugify

HTTP = lambda timeout=30: httpx.Client(timeout=timeout)  # noqa: E731  (os testes trocam)
KINDS = ("jira", "linear", "asana", "servicenow")
MARK = "[Agent Hangar]"  # os comentários que a plataforma escreve: não voltam como comentário novo
DEFAULT_BASE = {"linear": "https://api.linear.app/graphql", "asana": "https://app.asana.com/api/1.0"}
TERMINAL = ("done", "cancelled")
MAX_FILE = 10 * 1024 * 1024


# ------------------------------------------------------------------ adaptadores
class Adapter:
    """O que cada sistema sabe fazer. Um chamado é um dict: id, key, url, title, body, reporter, type, labels,
    priority, closed, updated, comments [{id, author, body, created}]."""

    def __init__(self, c: TicketConnector, token: str):
        self.c, self.cfg, self.token = c, c.config or {}, token
        self.base = (c.base_url or DEFAULT_BASE.get(c.kind, "")).rstrip("/")

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        with HTTP() as h:
            r = h.request(method, url, headers={**self.headers(), **kw.pop("headers", {})}, **kw)
        if r.status_code >= 400:
            raise PlatformError(f"{self.c.kind} respondeu {r.status_code}: {r.text[:200]}")
        return r

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}


def _adf_text(node) -> str:
    """Atlassian Document Format (Jira Cloud) → texto."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if node.get("type") == "text":
        return node.get("text", "")
    parts = [_adf_text(n) for n in node.get("content") or []]
    sep = "\n" if node.get("type") in ("doc", "bulletList", "orderedList") else ""
    out = sep.join(parts)
    return out + ("\n" if node.get("type") in ("paragraph", "heading", "listItem") else "")


def _adf(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}] if line else []} for line in text.split("\n")]}


class Jira(Adapter):
    """Jira Cloud (API v3, e-mail + token de API) ou Jira Server/Data Center (API v2, token pessoal)."""

    def __init__(self, c, token):
        super().__init__(c, token)
        self.cloud = self.cfg.get("deployment", "cloud") == "cloud"
        self.v = "3" if self.cloud else "2"

    def headers(self) -> dict:
        if self.cloud:
            raw = f"{(self.c.auth or {}).get('user', '')}:{self.token}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode(), "Accept": "application/json"}
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    FIELDS = ["summary", "description", "issuetype", "labels", "priority", "reporter", "status", "updated", "comment"]

    def _ticket(self, i: dict) -> dict:
        f = i.get("fields") or {}
        desc = f.get("description")
        comments = (f.get("comment") or {}).get("comments") or []
        return {"id": str(i["id"]), "key": i.get("key", ""), "url": f"{self.base}/browse/{i.get('key', '')}",
                "title": f.get("summary") or "", "body": (_adf_text(desc) if isinstance(desc, dict) else desc or "").strip(),
                "reporter": ((f.get("reporter") or {}).get("emailAddress") or (f.get("reporter") or {}).get("displayName") or ""),
                "type": (f.get("issuetype") or {}).get("name", ""), "labels": f.get("labels") or [],
                "priority": (f.get("priority") or {}).get("name", ""), "updated": f.get("updated", ""),
                "closed": ((f.get("status") or {}).get("statusCategory") or {}).get("key") == "done",
                "comments": [{"id": str(c["id"]), "author": (c.get("author") or {}).get("displayName", ""),
                              "body": (_adf_text(c.get("body")) if isinstance(c.get("body"), dict) else c.get("body") or "").strip(),
                              "created": c.get("created", "")} for c in comments]}

    def test(self) -> str:
        me = self._req("GET", f"{self.base}/rest/api/{self.v}/myself").json()
        return me.get("emailAddress") or me.get("displayName") or me.get("name") or "ok"

    def fetch(self, since_minutes: int) -> list[dict]:
        jql = f"({self.cfg.get('jql') or 'resolution = Unresolved'}) AND updated >= -{max(1, since_minutes)}m ORDER BY updated ASC"
        out, token = [], None
        for _ in range(4):  # até 200 por rodada; o resto vem na próxima
            if self.cloud:
                body = {"jql": jql, "fields": self.FIELDS, "maxResults": 50, **({"nextPageToken": token} if token else {})}
                d = self._req("POST", f"{self.base}/rest/api/3/search/jql", json=body).json()
                out += [self._ticket(i) for i in d.get("issues") or []]
                token = d.get("nextPageToken")
                if d.get("isLast", True) or not token:
                    break
            else:
                d = self._req("GET", f"{self.base}/rest/api/2/search", params={
                    "jql": jql, "fields": ",".join(self.FIELDS), "maxResults": 50, "startAt": len(out)}).json()
                out += [self._ticket(i) for i in d.get("issues") or []]
                if len(out) >= d.get("total", 0):
                    break
        return out

    def get(self, ref: str) -> dict:
        return self._ticket(self._req("GET", f"{self.base}/rest/api/{self.v}/issue/{quote(ref)}",
                                      params={"fields": ",".join(self.FIELDS)}).json())

    def comment(self, t: dict, text: str):
        body = {"body": _adf(text)} if self.cloud else {"body": text}
        self._req("POST", f"{self.base}/rest/api/{self.v}/issue/{t['key'] or t['id']}/comment", json=body)

    def transition(self, t: dict, target: str) -> bool:
        ref = t["key"] or t["id"]
        ts = self._req("GET", f"{self.base}/rest/api/{self.v}/issue/{ref}/transitions").json().get("transitions") or []
        hit = next((x for x in ts if target.lower() in ((x.get("name") or "").lower(), ((x.get("to") or {}).get("name") or "").lower())), None)
        if hit is None:
            return False
        self._req("POST", f"{self.base}/rest/api/{self.v}/issue/{ref}/transitions", json={"transition": {"id": hit["id"]}})
        return True

    def attach(self, t: dict, name: str, data: bytes, ctype: str):
        self._req("POST", f"{self.base}/rest/api/{self.v}/issue/{t['key'] or t['id']}/attachments",
                  headers={"X-Atlassian-Token": "no-check"}, files={"file": (name, data, ctype or "application/octet-stream")})

    def webhook_refs(self, payload: dict, headers: dict) -> list[str]:
        i = payload.get("issue") or {}
        return [str(i.get("key") or i.get("id"))] if (i.get("key") or i.get("id")) else []


class Linear(Adapter):
    """Linear (GraphQL, chave de API). Filtro: o time (key) e, se quiser, os nomes dos estados."""

    def headers(self) -> dict:
        return {"Authorization": self.token, "Content-Type": "application/json"}

    Q = ("id identifier url title description priority updatedAt state { name type } creator { name email } "
         "labels { nodes { name } } comments { nodes { id body createdAt user { name } } }")

    def _gql(self, query: str, variables: dict) -> dict:
        d = self._req("POST", self.base, json={"query": query, "variables": variables}).json()
        if d.get("errors"):
            raise PlatformError(f"linear: {d['errors'][0].get('message')}")
        return d.get("data") or {}

    def _ticket(self, i: dict) -> dict:
        prio = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}.get(i.get("priority"), "")
        return {"id": i["id"], "key": i.get("identifier", ""), "url": i.get("url", ""), "title": i.get("title", ""),
                "body": i.get("description") or "", "reporter": (i.get("creator") or {}).get("email") or (i.get("creator") or {}).get("name", ""),
                "type": "", "labels": [x["name"] for x in (i.get("labels") or {}).get("nodes") or []], "priority": prio,
                "updated": i.get("updatedAt", ""), "closed": (i.get("state") or {}).get("type") in ("completed", "canceled"),
                "comments": [{"id": c["id"], "author": (c.get("user") or {}).get("name", ""), "body": c.get("body") or "",
                              "created": c.get("createdAt", "")} for c in (i.get("comments") or {}).get("nodes") or []]}

    def test(self) -> str:
        return (self._gql("query { viewer { email name } }", {}).get("viewer") or {}).get("email") or "ok"

    def fetch(self, since_minutes: int) -> list[dict]:
        since = (datetime.now(UTC) - timedelta(minutes=max(1, since_minutes))).isoformat()
        flt = {"updatedAt": {"gte": since}, "team": {"key": {"eq": self.cfg.get("team") or ""}}}
        if self.cfg.get("states"):
            flt["state"] = {"name": {"in": self.cfg["states"]}}
        d = self._gql(f"query($f: IssueFilter) {{ issues(filter: $f, first: 100, orderBy: updatedAt) {{ nodes {{ {self.Q} }} }} }}",
                      {"f": flt})
        return [self._ticket(i) for i in (d.get("issues") or {}).get("nodes") or []]

    def get(self, ref: str) -> dict:
        return self._ticket(self._gql(f"query($id: String!) {{ issue(id: $id) {{ {self.Q} }} }}", {"id": ref}).get("issue") or {})

    def comment(self, t: dict, text: str):
        self._gql("mutation($i: CommentCreateInput!) { commentCreate(input: $i) { success } }",
                  {"i": {"issueId": t["id"], "body": text}})

    def transition(self, t: dict, target: str) -> bool:
        st = self._gql("query($f: WorkflowStateFilter) { workflowStates(filter: $f) { nodes { id name } } }",
                       {"f": {"team": {"key": {"eq": self.cfg.get("team") or ""}}, "name": {"eqIgnoreCase": target}}})
        nodes = (st.get("workflowStates") or {}).get("nodes") or []
        if not nodes:
            return False
        self._gql("mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }",
                  {"id": t["id"], "i": {"stateId": nodes[0]["id"]}})
        return True

    def attach(self, t: dict, name: str, data: bytes, ctype: str):
        raise PlatformError("o Linear não recebe anexos por esta integração: o nome do arquivo vai no comentário")

    def webhook_refs(self, payload: dict, headers: dict) -> list[str]:
        d = payload.get("data") or {}
        if payload.get("type") == "Comment":
            return [d.get("issueId")] if d.get("issueId") else []
        return [d["id"]] if payload.get("type") == "Issue" and d.get("id") else []


class Asana(Adapter):
    """Asana (token pessoal). Filtro: o projeto (gid); seções nomeadas para "em atendimento" e "concluído"."""

    def _ticket(self, i: dict) -> dict:
        return {"id": i["gid"], "key": i["gid"], "url": i.get("permalink_url", ""), "title": i.get("name", ""),
                "body": i.get("notes") or "", "reporter": (i.get("created_by") or {}).get("name", ""), "type": "",
                "labels": [t["name"] for t in i.get("tags") or []], "priority": "", "updated": i.get("modified_at", ""),
                "closed": bool(i.get("completed")), "comments": []}

    F = "name,notes,completed,permalink_url,modified_at,tags.name,created_by.name"

    def test(self) -> str:
        return (self._req("GET", f"{self.base}/users/me").json().get("data") or {}).get("email") or "ok"

    def _comments(self, gid: str) -> list[dict]:
        st = self._req("GET", f"{self.base}/tasks/{gid}/stories", params={"opt_fields": "type,text,created_by.name,created_at"}).json()
        return [{"id": s["gid"], "author": (s.get("created_by") or {}).get("name", ""), "body": s.get("text") or "",
                 "created": s.get("created_at", "")} for s in st.get("data") or [] if s.get("type") == "comment"]

    def fetch(self, since_minutes: int) -> list[dict]:
        since = (datetime.now(UTC) - timedelta(minutes=max(1, since_minutes))).isoformat()
        d = self._req("GET", f"{self.base}/tasks", params={"project": self.cfg.get("project") or "", "modified_since": since,
                                                           "opt_fields": self.F, "limit": 100}).json()
        out = []
        for i in d.get("data") or []:
            t = self._ticket(i)
            t["comments"] = self._comments(t["id"])
            out.append(t)
        return out

    def get(self, ref: str) -> dict:
        t = self._ticket(self._req("GET", f"{self.base}/tasks/{ref}", params={"opt_fields": self.F}).json().get("data") or {})
        t["comments"] = self._comments(t["id"])
        return t

    def comment(self, t: dict, text: str):
        self._req("POST", f"{self.base}/tasks/{t['id']}/stories", json={"data": {"text": text}})

    def transition(self, t: dict, target: str) -> bool:
        if target.lower() in ("completed", "complete", "done", "concluído", "concluido"):
            self._req("PUT", f"{self.base}/tasks/{t['id']}", json={"data": {"completed": True}})
            return True
        secs = self._req("GET", f"{self.base}/projects/{self.cfg.get('project') or ''}/sections").json().get("data") or []
        hit = next((s for s in secs if (s.get("name") or "").lower() == target.lower()), None)
        if hit is None:
            return False
        self._req("POST", f"{self.base}/sections/{hit['gid']}/addTask", json={"data": {"task": t["id"]}})
        return True

    def attach(self, t: dict, name: str, data: bytes, ctype: str):
        self._req("POST", f"{self.base}/attachments", data={"parent": t["id"]},
                  files={"file": (name, data, ctype or "application/octet-stream")})

    def webhook_refs(self, payload: dict, headers: dict) -> list[str]:
        return list(dict.fromkeys(str(e["resource"]["gid"]) for e in payload.get("events") or []
                                  if (e.get("resource") or {}).get("resource_type") == "task"))


class ServiceNow(Adapter):
    """ServiceNow (Table API; usuário + senha, ou token OAuth). Filtro: tabela (incident) e uma consulta encoded."""

    def headers(self) -> dict:
        user = (self.c.auth or {}).get("user")
        if user:
            raw = f"{user}:{self.token}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode(), "Accept": "application/json"}
        return super().headers()

    @property
    def table(self) -> str:
        return re.sub(r"[^\w]", "", self.cfg.get("table") or "incident")

    def _ticket(self, i: dict) -> dict:
        v = lambda k: (i.get(k) or {}).get("display_value") if isinstance(i.get(k), dict) else i.get(k)  # noqa: E731
        raw = lambda k: (i.get(k) or {}).get("value") if isinstance(i.get(k), dict) else i.get(k)  # noqa: E731
        sid = raw("sys_id")
        return {"id": sid, "key": v("number") or sid, "url": f"{self.base}/nav_to.do?uri={self.table}.do?sys_id={sid}",
                "title": v("short_description") or "", "body": v("description") or "", "reporter": v("caller_id") or "",
                "type": v("category") or "", "labels": [], "priority": v("priority") or "", "updated": raw("sys_updated_on") or "",
                "closed": str(raw("state") or "") in ("6", "7", "8"), "comments": []}

    F = "sys_id,number,short_description,description,priority,category,caller_id,state,sys_updated_on"

    def test(self) -> str:
        self._req("GET", f"{self.base}/api/now/table/{self.table}", params={"sysparm_limit": 1, "sysparm_fields": "sys_id"})
        return (self.c.auth or {}).get("user") or "ok"

    def _comments(self, sid: str) -> list[dict]:
        d = self._req("GET", f"{self.base}/api/now/table/sys_journal_field", params={
            "sysparm_query": f"element_id={sid}^element=comments^ORDERBYsys_created_on",
            "sysparm_fields": "sys_id,value,sys_created_by,sys_created_on", "sysparm_limit": 50}).json()
        return [{"id": c["sys_id"], "author": c.get("sys_created_by", ""), "body": c.get("value", ""),
                 "created": c.get("sys_created_on", "")} for c in d.get("result") or []]

    def fetch(self, since_minutes: int) -> list[dict]:
        q = self.cfg.get("query") or "active=true"
        d = self._req("GET", f"{self.base}/api/now/table/{self.table}", params={
            "sysparm_query": f"{q}^sys_updated_on>=javascript:gs.minutesAgoStart({max(1, since_minutes)})^ORDERBYsys_updated_on",
            "sysparm_fields": self.F, "sysparm_display_value": "all", "sysparm_limit": 100}).json()
        out = []
        for i in d.get("result") or []:
            t = self._ticket(i)
            t["comments"] = self._comments(t["id"])
            out.append(t)
        return out

    def get(self, ref: str) -> dict:
        d = self._req("GET", f"{self.base}/api/now/table/{self.table}/{ref}",
                      params={"sysparm_fields": self.F, "sysparm_display_value": "all"}).json()
        t = self._ticket(d.get("result") or {})
        t["comments"] = self._comments(t["id"])
        return t

    def comment(self, t: dict, text: str):
        field = "work_notes" if self.cfg.get("internal_notes") else "comments"
        self._req("PATCH", f"{self.base}/api/now/table/{self.table}/{t['id']}", json={field: text})

    def transition(self, t: dict, target: str) -> bool:
        states = {"in_progress": "2", "resolved": "6", "closed": "7", **(self.cfg.get("states") or {})}
        value = states.get(target.lower(), target if target.isdigit() else None)
        if value is None:
            return False
        body = {"state": value}
        if value == "6":
            body.update(close_code=self.cfg.get("close_code") or "Solved (Permanently)", close_notes=t.get("_result", "")[:4000])
        self._req("PATCH", f"{self.base}/api/now/table/{self.table}/{t['id']}", json=body)
        return True

    def attach(self, t: dict, name: str, data: bytes, ctype: str):
        self._req("POST", f"{self.base}/api/now/attachment/file", params={"table_name": self.table, "table_sys_id": t["id"],
                                                                          "file_name": name},
                  headers={"Content-Type": ctype or "application/octet-stream"}, content=data)

    def webhook_refs(self, payload: dict, headers: dict) -> list[str]:
        sid = payload.get("sys_id") or (payload.get("record") or {}).get("sys_id")
        return [sid] if sid else []


ADAPTERS = {"jira": Jira, "linear": Linear, "asana": Asana, "servicenow": ServiceNow}


def adapter(db, c: TicketConnector) -> Adapter:
    from . import secrets_vault
    ref = (c.auth or {}).get("secret") or ""
    token = secrets_vault.resolve(ref, c.team_id, f"tickets:{c.name}", db) if ref else ""
    return ADAPTERS[c.kind](c, token)


# ------------------------------------------------------------------ conectores
def _can_manage(acc: Access, c_team: int | None) -> bool:
    return acc.p.is_admin or (c_team is not None and RANK.get(acc.teams.get(c_team, ""), 0) >= RANK["maintainer"])


def connector_dict(db, c: TicketConnector, acc: Access | None = None) -> dict:
    q = db.get(WorkQueue, c.queue_id)
    return {"name": c.name, "title": c.title or c.name, "kind": c.kind, "base_url": c.base_url,
            "auth": {"user": (c.auth or {}).get("user", ""), "secret": (c.auth or {}).get("secret", "")},
            "config": c.config or {}, "queue": q.slug if q else None, "status": c.status, "poll_minutes": c.poll_minutes,
            "last_sync_at": iso(c.last_sync_at) if c.last_sync_at else None, "last_error": c.last_error,
            "stats": c.stats or {}, "created_by": c.created_by, "approved_by": c.approved_by,
            "links": db.query(TicketLink).filter(TicketLink.connector_id == c.id).count(),
            "webhook": bool(c.webhook_token_hash),
            "can_manage": bool(acc and _can_manage(acc, c.team_id)),
            "can_approve": bool(acc and c.status == "draft" and _can_manage(acc, c.team_id) and acc.p.name != c.created_by)}


def _get(db, name: str) -> TicketConnector:
    c = db.scalar(select(TicketConnector).where(TicketConnector.name == slugify(name or "").replace("-", "_")))
    if c is None:
        raise PlatformError(f"conector '{name}' não encontrado")
    return c


def list_connectors(db, acc: Access) -> list[dict]:
    return [connector_dict(db, c, acc) for c in db.scalars(select(TicketConnector).order_by(TicketConnector.name))
            if acc.p.is_admin or acc.p.is_auditor or c.team_id in acc.teams]


def _check_base(kind: str, url: str) -> str:
    url = (url or DEFAULT_BASE.get(kind, "")).strip().rstrip("/")
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise PlatformError("base_url: o endereço do sistema (https://…)")
    if u.scheme == "http" and not config.PLUGIN_ALLOW_PRIVATE:
        raise PlatformError("use https (http só numa instalação com PLUGIN_ALLOW_PRIVATE=1)")
    from .plugins import _check_host
    _check_host(u.hostname)
    return url


def save_connector(db, acc: Access, name: str, kind: str, queue: str, base_url: str = "", auth: dict | None = None,
                   cfg: dict | None = None, title: str = "", poll_minutes: int = 5) -> dict:
    if kind not in KINDS:
        raise PlatformError(f"kind: {', '.join(KINDS)}")
    q = db.scalar(select(WorkQueue).where(WorkQueue.slug == queue))
    if q is None:
        raise PlatformError(f"fila '{queue}' não encontrada")
    if not _can_manage(acc, q.team_id):
        raise Forbidden("cria um conector quem mantém o time da fila (ou o admin)")
    auth = {k: str(v).strip() for k, v in (auth or {}).items() if k in ("user", "secret") and v}
    if not str(auth.get("secret", "")).startswith("secret://"):
        raise PlatformError("a credencial vai no cofre: auth.secret = \"secret://nome\"")
    from . import secrets_vault
    secrets_vault.check_ref(db, auth["secret"], q.team_id)
    cfg = dict(cfg or {})
    need = {"jira": "jql", "linear": "team", "asana": "project"}.get(kind)
    if need and not str(cfg.get(need) or "").strip():
        raise PlatformError(f"o filtro do {kind}: config.{need}")
    if kind == "jira" and cfg.get("deployment", "cloud") == "cloud" and not auth.get("user"):
        raise PlatformError("Jira Cloud: auth.user = o e-mail da conta do token")
    url = _check_base(kind, base_url) if kind in ("jira", "servicenow") or base_url else DEFAULT_BASE[kind]
    slug = slugify(name or title or kind).replace("-", "_")[:80]
    c = db.scalar(select(TicketConnector).where(TicketConnector.name == slug))
    if c is None:
        c = TicketConnector(name=slug, kind=kind, created_by=acc.p.name, stats={}, created_at=now())
        db.add(c)
    elif not _can_manage(acc, c.team_id):
        raise Forbidden("só quem mantém o conector o altera")
    c.title, c.kind, c.base_url, c.auth, c.config = (title or slug)[:200], kind, url, auth, cfg
    c.queue_id, c.team_id, c.poll_minutes = q.id, q.team_id, max(1, min(int(poll_minutes or 5), 1440))
    c.status, c.approved_by = "draft", ""  # mudou: outra pessoa aprova de novo
    c.created_by = c.created_by or acc.p.name
    db.commit()
    audit(db, acc.p.name, "tickets.save", c.name, f"{kind} → {q.slug}")
    return connector_dict(db, c, acc)


def approve(db, acc: Access, name: str) -> dict:
    """Quatro olhos: o conector escreve num sistema de fora com a credencial da empresa."""
    c = _get(db, name)
    if not _can_manage(acc, c.team_id):
        raise Forbidden("aprova quem mantém o time da fila (ou o admin)")
    if acc.p.name == c.created_by:
        raise Forbidden("quatro olhos: quem configurou não aprova")
    c.status, c.approved_by, c.last_error = "active", acc.p.name, ""
    c.next_poll_at = now()
    db.commit()
    audit(db, acc.p.name, "tickets.approve", c.name, c.kind)
    return connector_dict(db, c, acc)


def set_status(db, acc: Access, name: str, status: str) -> dict:
    c = _get(db, name)
    if not _can_manage(acc, c.team_id):
        raise Forbidden("só quem mantém o conector")
    if status not in ("active", "paused"):
        raise PlatformError("status: active | paused")
    if status == "active" and not c.approved_by:
        raise PlatformError("o conector ainda não foi aprovado")
    c.status = status
    db.commit()
    audit(db, acc.p.name, f"tickets.{status}", c.name, "")
    return connector_dict(db, c, acc)


def delete(db, acc: Access, name: str) -> dict:
    c = _get(db, name)
    if not _can_manage(acc, c.team_id):
        raise Forbidden("só quem mantém o conector")
    db.delete(c)
    db.commit()
    audit(db, acc.p.name, "tickets.delete", name, "")
    return {"deleted": name}


def webhook_token(db, acc: Access, name: str) -> dict:
    c = _get(db, name)
    if not _can_manage(acc, c.team_id):
        raise Forbidden("só quem mantém o conector")
    tok = secrets.token_urlsafe(24)
    c.webhook_token_hash = hashlib.sha256(tok.encode()).hexdigest()
    db.commit()
    from .. import tenancy
    return {"url": f"{config.PUBLIC_BASE_URL}/hooks/tickets/{tenancy.current_org()}/{c.name}?token={tok}",
            "note": "mostrado uma vez só"}


def test(db, acc: Access, name: str) -> dict:
    c = _get(db, name)
    if not _can_manage(acc, c.team_id):
        raise Forbidden("só quem mantém o conector")
    try:
        who = adapter(db, c).test()
        sample = adapter(db, c).fetch(60 * 24 * 7)[:5]
    except (PlatformError, httpx.HTTPError) as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True, "as": who, "sample": [{"key": t["key"], "title": t["title"][:120], "closed": t["closed"]} for t in sample]}


def links(db, acc: Access, name: str) -> list[dict]:
    c = _get(db, name)
    if not (acc.p.is_admin or acc.p.is_auditor or c.team_id in acc.teams):
        raise Forbidden("sem acesso")
    out = []
    for lk in db.scalars(select(TicketLink).where(TicketLink.connector_id == c.id).order_by(TicketLink.id.desc()).limit(200)):
        i = db.get(WorkItem, lk.work_item_id)
        out.append({"key": lk.external_key, "url": lk.url, "item": lk.work_item_id, "item_status": i.status if i else None,
                    "pushed": (lk.pushed or {}).get("state", ""), "created_at": iso(lk.created_at)})
    return out


# ------------------------------------------------------------------ entrada
PRIO = {"highest": 1, "critical": 1, "urgent": 1, "1": 1, "1 - critical": 1, "high": 1, "2": 2, "2 - high": 2,
        "medium": 2, "3": 3, "3 - moderate": 3, "low": 3, "4": 4, "4 - low": 4, "lowest": 4, "5 - planning": 4}


def _category(c: TicketConnector, t: dict) -> str:
    """A primeira regra que casa (tipo, etiqueta ou prioridade); sem regra, a fila classifica pelas palavras."""
    for r in (c.config or {}).get("category_rules") or []:
        field, value = r.get("field"), str(r.get("value") or "").lower()
        vals = [x.lower() for x in t.get("labels") or []] if field == "label" else [str(t.get(field) or "").lower()]
        if value and value in vals:
            return r.get("category") or ""
    return ""


def _body(c: TicketConnector, t: dict) -> str:
    meta = " · ".join(x for x in (t.get("type"), t.get("priority") and f"prioridade {t['priority']}",
                                  t.get("labels") and "etiquetas: " + ", ".join(t["labels"])) if x)
    return (f"{t.get('body') or ''}\n\n— Chamado {t['key']} no {c.kind.capitalize()}: {t['url']}"
            + (f"\n{meta}" if meta else "") + f"\nAberto por: {t.get('reporter') or '—'}").strip()


def ingest(db, c: TicketConnector, t: dict) -> str:
    """Um chamado novo vira item da fila; um que já existe traz os comentários novos e o fechamento."""
    from . import work_queues
    if not t.get("id"):
        return "ignored"
    lk = db.scalar(select(TicketLink).where(TicketLink.connector_id == c.id, TicketLink.external_id == t["id"]))
    if lk is None:
        if t.get("closed"):
            return "ignored"
        q = db.get(WorkQueue, c.queue_id)
        prio = PRIO.get(str(t.get("priority") or "").lower())
        item = work_queues.intake(db, q, t["title"] or t["key"], _body(c, t), (t.get("reporter") or c.kind)[:254], None,
                                  "ticket", _category(c, t), prio)
        item.source_ref = f"{c.kind}:{t['key']}"[:120]
        seen = [x["id"] for x in t.get("comments") or []]  # o que já estava no chamado entrou como contexto
        db.add(TicketLink(connector_id=c.id, external_id=t["id"], external_key=t["key"][:120], url=t["url"][:500],
                          work_item_id=item.id, pushed={"comments_seen": seen}, remote_updated=t.get("updated", "")[:40],
                          created_at=now()))
        c.stats = {**(c.stats or {}), "received": (c.stats or {}).get("received", 0) + 1}
        db.commit()
        return "created"
    if lk.remote_updated == t.get("updated") and t.get("updated"):
        return "unchanged"
    lk.remote_updated = (t.get("updated") or "")[:40]
    item = db.get(WorkItem, lk.work_item_id)
    pushed = dict(lk.pushed or {})
    seen = list(pushed.get("comments_seen") or [])
    for cm in t.get("comments") or []:
        if cm["id"] in seen:
            continue
        seen.append(cm["id"])
        if MARK in (cm.get("body") or "") or not (cm.get("body") or "").strip():
            continue  # o que a plataforma escreveu
        if item is not None:
            _comment_in(db, c, t, item, cm)
    pushed["comments_seen"] = seen[-500:]
    lk.pushed = pushed
    if item is not None and t.get("closed") and item.status not in TERMINAL and (c.config or {}).get("close_on_remote", True):
        item.status = "cancelled"
        item.result = (f"{item.result}\n\n[{c.kind}] O chamado {t['key']} foi fechado no {c.kind}.").strip()[:20000]
        pushed["state"] = "cancelled"
        lk.pushed = pushed
        if item.employee_task_id:
            task = db.get(EmployeeTask, item.employee_task_id)
            if task is not None and task.status not in tasksmod.TERMINAL:
                task.status, task.error = "cancelled", f"o chamado {t['key']} foi fechado no {c.kind}"
    db.commit()
    return "updated"


def _comment_in(db, c: TicketConnector, t: dict, item: WorkItem, cm: dict):
    """Um comentário novo no chamado: na conversa da tarefa do Digital employee, se ela estiver andando; senão no item."""
    text = f"Comentário de {cm.get('author') or 'alguém'} no chamado {t['key']} ({c.kind}):\n{cm['body'][:8000]}"
    task = db.get(EmployeeTask, item.employee_task_id) if item.employee_task_id else None
    if task is not None and task.status not in tasksmod.TERMINAL:
        db.add(TaskMessage(task_id=task.id, author=f"{c.kind}:{(cm.get('author') or '')[:80]}", user_id=None, body=text,
                           status="queued", created_at=now()))
        if task.status in ("waiting_external",):
            task.status = "new"
        tasksmod.event(db, task, "message", {"by": f"{c.kind}:{cm.get('author') or ''}", "queued": True})
    else:
        item.body = f"{item.body}\n\n[{c.kind} · {cm.get('author') or ''}] {cm['body'][:4000]}"[:20000]


def poll(db, c: TicketConnector) -> dict:
    last = datetime.fromisoformat(c.cursor) if c.cursor else None
    minutes = int((now() - last).total_seconds() // 60) + 2 if last else int((c.config or {}).get("initial_days", 1)) * 1440
    started = now()
    out = {"created": 0, "updated": 0, "unchanged": 0, "ignored": 0}
    for t in adapter(db, c).fetch(minutes):
        out[ingest(db, c, t)] += 1
    c.cursor, c.last_sync_at, c.last_error = started.isoformat(), started, ""
    db.commit()
    return out


def sync_now(db, acc: Access, name: str) -> dict:
    c = _get(db, name)
    if not _can_manage(acc, c.team_id):
        raise Forbidden("só quem mantém o conector")
    if c.status != "active":
        raise PlatformError("o conector não está no ar (aprovação pendente ou pausado)")
    try:
        got = poll(db, c)
        got["pushed"] = push_all(db, c)
    except (PlatformError, httpx.HTTPError) as e:
        c.last_error = str(e)[:500]
        db.commit()
        raise PlatformError(f"falha ao sincronizar: {e}") from None
    audit(db, acc.p.name, "tickets.sync", c.name, json.dumps(got))
    return got


def webhook(db, org_id: int, name: str, token: str, payload: dict, headers: dict) -> dict:
    from .. import tenancy
    c = db.scalar(tenancy.unscoped(select(TicketConnector).where(TicketConnector.org_id == org_id, TicketConnector.name == name)))
    if c is None or not c.webhook_token_hash or not secrets.compare_digest(
            hashlib.sha256((token or "").encode()).hexdigest(), c.webhook_token_hash):
        raise Forbidden("token inválido")
    tenancy.set_org(c.org_id or 1)
    if c.status != "active":
        return {"ok": False, "reason": "conector fora do ar"}
    a = adapter(db, c)
    n = 0
    for ref in a.webhook_refs(payload or {}, headers)[:20]:
        ingest(db, c, a.get(ref))
        n += 1
    push_all(db, c)
    return {"ok": True, "tickets": n}


# ------------------------------------------------------------------ volta ao chamado
def _state(db, item: WorkItem) -> str:
    """O andamento do item, do jeito que interessa a quem abriu o chamado."""
    if item.status == "done":
        return "done"
    if item.status == "cancelled":
        return "cancelled"
    if item.status == "employee":
        return "employee"
    if item.status in ("in_progress", "review"):
        return "person"
    if item.handled_by == "assisted" and item.prepared:
        return "handed"
    return "received"


def _who(db, item: WorkItem) -> str:
    from ..models import Agent, Employee, User
    if item.employee_task_id:
        t = db.get(EmployeeTask, item.employee_task_id)
        e = db.get(Employee, t.employee_id) if t else None
        a = db.get(Agent, e.agent_id) if e else None
        if a and item.status in ("employee", "done") and item.handled_by == "employee":
            return f"{a.name} (Digital employee)"
    u = db.get(User, item.assignee_user_id) if item.assignee_user_id else None
    return (u.name or u.email) if u else "o time"


def _message(c: TicketConnector, state: str, item: WorkItem, who: str) -> str:
    wb = (c.config or {}).get("messages") or {}
    if wb.get(state):
        return f"{MARK} " + str(wb[state]).replace("{who}", who).replace("{item}", f"#{item.id}")
    default = {"received": f"Recebido na fila (item #{item.id}).",
               "employee": f"Em atendimento por {who}.",
               "person": f"Em atendimento por {who}.",
               "handed": f"Encaminhado a uma pessoa do time para terminar.\n\n{(item.prepared or '')[:3000]}",
               "done": f"Concluído por {who}.\n\n{(item.result or '')[:30000]}",
               "cancelled": "Cancelado."}[state]
    return f"{MARK} {default}"


def _files(db, item: WorkItem) -> list[tuple[str, bytes, str]]:
    out = [(f.name, f.data, f.content_type) for f in db.scalars(select(WorkItemFile).where(WorkItemFile.item_id == item.id))]
    if item.employee_task_id:
        latest: dict[str, TaskFile] = {}
        for f in db.scalars(select(TaskFile).where(TaskFile.task_id == item.employee_task_id, TaskFile.kind == "output")
                            .order_by(TaskFile.version)):
            if not f.name.startswith("tela-"):  # capturas da alçada não são entregáveis
                latest[f.name] = f
        out += [(f.name, f.data, f.content_type) for f in latest.values()]
    return [x for x in out if x[1] and len(x[1]) <= MAX_FILE]


def push(db, c: TicketConnector, lk: TicketLink, a: Adapter | None = None) -> bool:
    """Manda ao chamado o que mudou no item desde a última vez. Devolve se mandou algo."""
    item = db.get(WorkItem, lk.work_item_id)
    if item is None:
        return False
    cfg = c.config or {}
    pushed = dict(lk.pushed or {})
    state = _state(db, item)
    if state == pushed.get("state") or (state == "received" and not cfg.get("ack_received", True)):
        if state == "received" and "state" not in pushed:
            pushed["state"] = state
            lk.pushed = pushed
            db.commit()
        return False
    a = a or adapter(db, c)
    t = {"id": lk.external_id, "key": lk.external_key, "_result": item.result or ""}
    if state not in cfg.get("silent_states", []):
        a.comment(t, _message(c, state, item, _who(db, item)))
    if state == "done" and cfg.get("attach_files", True):
        sent = set(pushed.get("files") or [])
        for name, data, ctype in _files(db, item):
            if name in sent:
                continue
            try:
                a.attach(t, name, data, ctype)
            except PlatformError:  # o sistema não recebe anexos: o nome já foi no comentário
                pass
            sent.add(name)
        pushed["files"] = sorted(sent)
    target = (cfg.get("transitions") or {}).get(state)
    if target:
        pushed.setdefault("transitions", {})[state] = a.transition(t, target)
    pushed["state"] = state
    lk.pushed = pushed
    c.stats = {**(c.stats or {}), "pushed": (c.stats or {}).get("pushed", 0) + 1}
    db.commit()
    return True


def push_all(db, c: TicketConnector) -> int:
    n, a = 0, None
    for lk in db.scalars(select(TicketLink).where(TicketLink.connector_id == c.id)).all():
        if (lk.pushed or {}).get("state") in TERMINAL:
            continue
        a = a or adapter(db, c)
        n += push(db, c, lk, a)
    return n


def sweep(db, at=None) -> int:
    """No tick: lê os conectores no horário e manda de volta o que mudou nos itens ligados a chamados."""
    at, n = at or now(), 0
    for c in db.scalars(select(TicketConnector).where(TicketConnector.status == "active")).all():
        try:
            if c.next_poll_at is None or tasksmod._aware(c.next_poll_at) <= at:
                poll(db, c)
                c.next_poll_at = at + timedelta(minutes=c.poll_minutes or 5)
                db.commit()
            n += push_all(db, c)
        except (PlatformError, httpx.HTTPError) as e:
            c.last_error, c.next_poll_at = str(e)[:500], at + timedelta(minutes=c.poll_minutes or 5)
            c.stats = {**(c.stats or {}), "errors": (c.stats or {}).get("errors", 0) + 1}
            db.commit()
    return n


def item_ticket(db, item_id: int) -> dict | None:
    lk = db.scalar(select(TicketLink).where(TicketLink.work_item_id == item_id))
    if lk is None:
        return None
    c = db.get(TicketConnector, lk.connector_id)
    return {"kind": c.kind if c else "", "key": lk.external_key, "url": lk.url, "pushed": (lk.pushed or {}).get("state", "")}


def team_of(db, c: TicketConnector) -> str:
    t = db.get(Team, c.team_id)
    return t.slug if t else ""
