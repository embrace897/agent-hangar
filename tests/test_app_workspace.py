"""M1 do workspace no Hangar desktop (contrato: docs/app-hangar-workspace.md): o token OAuth de escopo `app` nas rotas
do membro da lista (com o acesso da própria pessoa) e recusado em todo o resto, e os eventos SSE de /api/app/events
(cursor, Last-Event-ID, keepalive, filtragem por pessoa, entrega ao vivo, reautenticação e retenção)."""
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import auth
from app.db import SessionLocal
from app.main import app as fastapi_app
from app.models import Agent, AppEvent, Employee, HumanRequest, OAuthGrant, now
from app.routers import deps
from app.services import app_events

from .conftest import ADMIN
from .test_app_hangar import _bearer, _connect, _many_registrations, _register  # noqa: F401
from .test_app_hangar_models import _app_client

M1 = {("GET", "/api/me/workspace"), ("GET", "/api/decisions"), ("GET", "/api/org-chart"), ("GET", "/api/inbox")}
PW = "-".join(["Senha", "Boa", "123"])


def _user(client, uniq, team=None, role="developer"):
    name = uniq("ws").lower()
    u = client.post("/api/users", headers=ADMIN, json={"username": name, "password": PW, "name": f"Pessoa {name}"}).json()
    if team:
        r = client.post(f"/api/teams/{team}/members", headers=ADMIN, json={"email": u["email"], "role": role})
        assert r.status_code == 200, r.text
    return name, PW, u


def _login(client, person):
    client.cookies.clear()
    assert client.post("/api/auth/password", json={"username": person[0], "password": person[1]}).status_code == 200
    return {"X-CSRF-Token": client.cookies.get(auth.CSRF_COOKIE)}


def _token(client, person, scope="app mcp"):
    t, _ = _connect(client, person, _app_client(client) if scope == "app mcp" else _register(client, scope=scope)["client_id"],
                    scope=scope)
    client.cookies.clear()
    return t


@pytest.fixture()
def ws(client, uniq, fake_docker, monkeypatch):
    """Um membro com um agente pessoal (Digital employee pessoal) e uma decisão aberta para ele; um colega do mesmo
    time sem relação com o agente; tokens do app para os dois."""
    from app import services as svc
    monkeypatch.setattr(svc.runtime, "ship", lambda db, slug, actor="admin", **k: [])
    team = client.post("/api/teams", headers=ADMIN, json={"name": f"WS {uniq('t')}"}).json()["slug"]
    me, other = _user(client, uniq, team), _user(client, uniq, team)
    csrf = _login(client, me)
    job = {"name": f"Meu ajudante {uniq('a')}", "title": "Meu ajudante", "mission": "Organizar a minha semana.",
           "personal": True, "responsibilities": ["organizar", "rascunhar", "lembrar"], "team": team,
           "systems": ["e-mail"], "llm": {"model": "mock/echo"}, "channels": ["portal", "mcp"],
           "accept_default_authority": True,
           "probation_tasks": [{"title": f"P {i}", "body": f"r {i}", "expected": f"r {i}"} for i in range(3)]}
    r = client.post("/api/employees", headers=csrf, json=job)
    assert r.status_code == 200, r.text
    slug = r.json()["employee"]["slug"]
    client.cookies.clear()
    with SessionLocal() as s:
        e = s.scalar(select(Employee).join(Agent, Agent.id == Employee.agent_id).where(Agent.slug == slug))
        req = HumanRequest(employee_id=e.id, kind="question", question="Posso mandar o resumo semanal?",
                           assigned_user_id=me[2]["id"])
        s.add(req)
        s.commit()
        rid, eid = req.id, e.id
    out = {"team": team, "me": me, "other": other, "slug": slug, "employee_id": eid, "request_id": rid,
           "t": _token(client, me), "t_other": _token(client, other)}
    yield out
    client.cookies.clear()


# ------------------------------------------------------------------ token do app nas rotas do membro
def test_only_the_m1_routes_are_marked():
    """A lista é explícita: ampliar exige marcar a rota (e mudar este teste junto com o contrato)."""
    marked = {(m, r.path) for r in deps.api_routes(fastapi_app.router.routes) if deps.app_allowed(r) for m in r.methods}
    assert marked == M1


def test_m1_routes_accept_the_app_token_with_the_persons_access(client, ws):
    h = _bearer(ws["t"])
    got = {p: client.get(p, headers=h) for p in ("/api/me/workspace", "/api/decisions", "/api/org-chart?mine=1",
                                                 "/api/inbox", "/api/inbox?view=unread")}
    assert all(r.status_code == 200 for r in got.values()), {p: (r.status_code, r.text[:200]) for p, r in got.items()}
    _login(client, ws["me"])  # o mesmo pela sessão do portal: mesmas respostas
    same = {p: client.get(p) for p in got}
    client.cookies.clear()
    for p in ("/api/decisions", "/api/org-chart?mine=1", "/api/inbox"):
        assert got[p].json() == same[p].json(), p
    home = got["/api/me/workspace"].json()
    assert home["to_decide"] == 1 and [a["slug"] for a in home["agents"]] == [ws["slug"]]
    assert [d["id"] for d in got["/api/decisions"].json()] == [ws["request_id"]]
    assert [n["slug"] for n in got["/api/org-chart?mine=1"].json()["nodes"] if n.get("slug") == ws["slug"]]
    assert got["/api/inbox"].json()["counts"]["decisions"] == 1
    # o colega do mesmo time não vê a decisão nem o agente pessoal
    ho = _bearer(ws["t_other"])
    assert client.get("/api/decisions", headers=ho).json() == []
    assert client.get("/api/me/workspace", headers=ho).json()["agents"] == []


def test_org_chart_only_with_mine(client, ws):
    h = _bearer(ws["t"])
    for q in ("", "?mine=0", "?mine=false", "?team=" + ws["team"]):
        r = client.get("/api/org-chart" + q, headers=h)
        assert r.status_code == 403 and "mine=1" in r.json()["detail"], (q, r.text)
    for q in ("?mine=1", "?mine=true"):
        assert client.get("/api/org-chart" + q, headers=h).status_code == 200


FORBIDDEN = [
    # admin, auditor e configuração
    ("GET", "/api/agents"), ("GET", "/api/users"), ("POST", "/api/users"), ("GET", "/api/teams"), ("POST", "/api/teams"),
    ("GET", "/api/audit"), ("GET", "/api/org"), ("PATCH", "/api/org"), ("GET", "/api/catalog/model-profiles/settings"),
    ("POST", "/api/catalog/llm"), ("PATCH", "/api/catalog/llm/x/desktop"), ("DELETE", "/api/catalog/llm/x"),
    ("GET", "/api/admin/workforce"), ("POST", "/api/admin/workforce/stop-all"), ("PUT", "/api/admin/notifications"),
    ("GET", "/api/admin/action-catalog"),
    ("GET", "/api/app/admin/overview"), ("GET", "/api/app/admin/devices"), ("GET", "/api/employees"),
    ("GET", "/api/metrics"), ("GET", "/api/budgets"),
    # chaves, segredos, sessões, OAuth/SSO
    ("GET", "/api/keys"), ("POST", "/api/keys"), ("DELETE", "/api/keys/1"), ("GET", "/api/me/oauth"),
    ("DELETE", "/api/me/oauth/1"), ("GET", "/api/me/sessions"), ("POST", "/api/me/activation-code"), ("GET", "/api/secrets"),
    ("PUT", "/api/secrets"), ("POST", "/api/secrets/proposals"), ("GET", "/api/oauth/consent"),
    ("POST", "/api/oauth/consent"), ("GET", "/api/sso"), ("POST", "/api/sso/mappings"),
    # do membro, mas fora da lista da M1 (as escritas da caixa e das decisões entram na M2)
    ("POST", "/api/inbox/mark"), ("POST", "/api/decisions/batch"), ("POST", "/api/decisions/1"), ("GET", "/api/search?q=ab"),
    ("GET", "/api/me/push"), ("GET", "/api/onboarding"), ("GET", "/api/tasks/1"), ("GET", "/api/me"),
    # fora de /api
    ("GET", "/scim/v2/Users"), ("GET", "/gw/nao-existe/v1/models"),
]


def test_app_token_is_refused_everywhere_else_even_for_an_admin(client, ws):
    client.patch(f"/api/users/{ws['me'][2]['id']}", headers=ADMIN, json={"org_role": "admin"})
    t = _token(client, ws["me"])
    try:
        for method, path in FORBIDDEN:
            r = client.request(method, path, headers=_bearer(t), json={})
            assert r.status_code == 403, (method, path, r.status_code, r.text[:200])
        # as rotas existem: a recusa é do token, não de rota inexistente
        from starlette.routing import Match
        routes = list(deps.api_routes(fastapi_app.router.routes))
        for method, path in FORBIDDEN:
            scope = {"type": "http", "method": method, "path": path.split("?")[0], "root_path": "", "headers": []}
            assert any(r.matches(scope)[0] == Match.FULL for r in routes), (method, path)
    finally:
        client.patch(f"/api/users/{ws['me'][2]['id']}", headers=ADMIN, json={"org_role": "member"})


def test_mcp_only_token_cannot_use_the_member_routes(client, ws):
    t = _token(client, ws["me"], scope="mcp")
    for path in ("/api/me/workspace", "/api/decisions", "/api/org-chart?mine=1", "/api/inbox"):
        assert client.get(path, headers=_bearer(t)).status_code == 403, path


# ------------------------------------------------------------------ eventos (SSE)
@pytest.fixture()
def fast(monkeypatch):
    monkeypatch.setattr(app_events, "POLL", 0.05)
    monkeypatch.setattr(app_events, "LIFETIME", 0.6)
    monkeypatch.setattr(app_events, "KEEPALIVE", 0.2)


def _sse(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        ev = {}
        for line in block.splitlines():
            k, _, v = line.partition(": ") if not line.startswith(":") else ("comment", "", line[1:].strip())
            ev[k] = v
        if ev:
            out.append(ev)
    return out


def _events(client, t, last=None) -> list[dict]:
    h = {**_bearer(t), **({"Last-Event-ID": str(last)} if last is not None else {})}
    r = client.get("/api/app/events", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), r.text
    import json
    return [{**e, "data": json.loads(e["data"])} if "data" in e else e for e in _sse(r.text)]


def _unread(client, t) -> int:
    return client.get("/api/inbox", headers=_bearer(t)).json()["counts"]["unread"]


def _typed(evs, kind):
    return [e for e in evs if e.get("event") == kind]


def test_events_snapshot_cursor_and_keepalive(client, ws, fast):
    evs = _events(client, ws["t"])
    assert evs[0] == {"retry": "5000"}
    first = evs[1]
    assert first["event"] == "inbox.changed" and first["data"] == {"unread": _unread(client, ws["t"])}
    assert first["id"].isdigit()
    assert any(e.get("comment") == "keepalive" for e in evs)
    # com Last-Event-ID: sem retrato, só o que veio depois
    assert _typed(_events(client, ws["t"], first["id"]), "inbox.changed") == []


def test_decision_events_are_filtered_by_who_can_decide(client, ws, fast):
    cur, base = _events(client, ws["t"])[1]["id"], _unread(client, ws["t"])
    with SessionLocal() as s:
        req = HumanRequest(employee_id=ws["employee_id"], kind="approval", tool_name="enviar_email",
                           action_type="send_external", assigned_user_id=ws["me"][2]["id"])
        s.add(req)
        s.commit()
        rid = req.id
    evs = _events(client, ws["t"], cur)
    created = _typed(evs, "decision.created")
    assert [e["data"] for e in created] == [{"id": rid, "title": "enviar_email", "agent": ws["slug"]}]
    assert _typed(evs, "inbox.changed")[-1]["data"] == {"unread": base + 1}
    ids = [int(e["id"]) for e in evs if "id" in e]
    assert ids == sorted(ids) and len(ids) == len(set(ids)) and ids[0] > int(cur)
    assert _typed(_events(client, ws["t_other"], cur), "decision.created") == []  # o colega não pode decidir
    with SessionLocal() as s:
        r = s.get(HumanRequest, rid)
        r.status, r.decision = "decided", "approve"
        s.commit()
    evs = _events(client, ws["t"], ids[-1])
    assert [e["data"] for e in _typed(evs, "decision.decided")] == [{"id": rid}]
    assert _typed(evs, "inbox.changed")[-1]["data"] == {"unread": base}
    assert _typed(_events(client, ws["t_other"], ids[-1]), "decision.decided") == []


def test_agent_status_and_inbox_mark_go_only_to_the_person(client, ws, fast):
    cur = _events(client, ws["t"])[1]["id"]
    with SessionLocal() as s:
        s.get(Employee, ws["employee_id"]).status = "paused"
        s.commit()
    assert [e["data"] for e in _typed(_events(client, ws["t"], cur), "agent.status")] == [
        {"slug": ws["slug"], "status": "paused"}]
    assert _typed(_events(client, ws["t_other"], cur), "agent.status") == []
    csrf = _login(client, ws["me"])
    assert client.post("/api/inbox/mark", headers=csrf, json={"keys": [f"decision:{ws['request_id']}"], "read": True}).status_code == 200
    client.cookies.clear()
    assert [e["data"] for e in _typed(_events(client, ws["t"], cur), "inbox.changed")] == [{"unread": _unread(client, ws["t"])}]
    assert _typed(_events(client, ws["t_other"], cur), "inbox.changed") == []


def test_models_changed_carries_the_models_version(client, ws, fast, uniq):
    from app.services import llm_models
    from app.services.catalog import upsert_llm_connection
    cur = _events(client, ws["t"])[1]["id"]
    with SessionLocal() as s:
        c = upsert_llm_connection(s, uniq("ev-conn"), "http://upstream.test", "claude-x", "sk-x", "", "t", "anthropic",
                                  None, None, "custom-anthropic")
        llm_models.add_manual(s, c, "claude-x", "text", 3.0, 15.0)
        name = c.name
    assert client.patch(f"/api/catalog/llm/{name}/desktop", headers=ADMIN, json={"sync": True}).status_code == 200
    version = client.get("/api/app/models", headers=_bearer(ws["t"])).json()["version"]
    got = _typed(_events(client, ws["t"], cur), "models.changed")
    assert got and got[-1]["data"] == {"version": version}
    assert len({e["data"]["version"] for e in got}) == len(got)  # só quando a versão muda
    client.delete(f"/api/catalog/llm/{name}", headers=ADMIN)


def test_live_delivery_while_connected(client, ws, monkeypatch):
    monkeypatch.setattr(app_events, "POLL", 0.05)
    monkeypatch.setattr(app_events, "LIFETIME", 1.5)

    def later():
        time.sleep(0.4)
        with SessionLocal() as s:
            s.add(HumanRequest(employee_id=ws["employee_id"], kind="question", question="Ao vivo?",
                               assigned_user_id=ws["me"][2]["id"]))
            s.commit()
    base = _unread(client, ws["t"])
    th = threading.Thread(target=later)
    th.start()
    evs = _events(client, ws["t"])
    th.join()
    assert [e["data"]["title"] for e in _typed(evs, "decision.created")] == ["Ao vivo?"]
    assert _typed(evs, "inbox.changed")[-1]["data"] == {"unread": base + 1}


def test_revoked_authorization_closes_the_stream(client, ws, monkeypatch):
    monkeypatch.setattr(app_events, "POLL", 0.05)
    monkeypatch.setattr(app_events, "REAUTH", 0.1)
    monkeypatch.setattr(app_events, "LIFETIME", 10.0)

    def later():
        time.sleep(0.3)
        with SessionLocal() as s:
            for g in s.scalars(select(OAuthGrant).where(OAuthGrant.user_id == ws["me"][2]["id"])):
                g.revoked_at = now()
            s.commit()
        auth.clear_cache()
    th = threading.Thread(target=later)
    th.start()
    began = time.monotonic()
    _events(client, ws["t"])
    th.join()
    assert time.monotonic() - began < 5


def test_events_need_a_person_and_an_app_token(client, ws):
    assert client.get("/api/app/events", headers=ADMIN).status_code == 403
    assert client.get("/api/app/events").status_code == 401
    t = _token(client, ws["me"], scope="mcp")
    assert client.get("/api/app/events", headers=_bearer(t)).status_code == 403


def test_rows_follow_the_transaction_and_late_rows_have_no_id(ws):
    with SessionLocal() as s:
        before = s.scalar(select(func.count()).select_from(AppEvent))
        s.add(HumanRequest(employee_id=ws["employee_id"], kind="question", question="x", assigned_user_id=ws["me"][2]["id"]))
        s.flush()
        assert s.scalar(select(func.count()).select_from(AppEvent)) == before + 2
        s.rollback()
        assert s.scalar(select(func.count()).select_from(AppEvent)) == before
    with SessionLocal() as s:  # uma linha que chegou depois do cursor (transação mais longa): sem id
        s.add(HumanRequest(employee_id=ws["employee_id"], kind="question", question="atrasada",
                           assigned_user_id=ws["me"][2]["id"]))
        s.commit()
        last = s.scalar(select(func.max(AppEvent.id)))
        p = auth.authenticate(s, ws["t"]["access_token"])
        reader = app_events.Reader(p.user_id, 1, cursor=last + 5, floor=last - 10)
        msgs = app_events.poll(s, p, reader)
    assert msgs and all(not m.startswith("id:") for m in msgs)
    assert any("atrasada" in m for m in msgs) and reader.cursor == last + 5


def test_purge_keeps_only_the_retention_window(ws):
    with SessionLocal() as s:
        s.execute(AppEvent.__table__.insert(), [{"org_id": 1, "kind": "inbox.changed", "ref": "", "user_id": -1,
                                                  "created_at": now() - app_events.RETENTION - timedelta(minutes=1)}])
        s.commit()
        assert app_events.purge(s, force=True) >= 1
        assert s.scalar(select(func.count()).select_from(AppEvent).where(AppEvent.user_id == -1)) == 0
