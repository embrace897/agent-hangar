# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Desktop governado (experimental): apps liberados por funcionário, a captura vai ao modelo como imagem (e não à
linha do tempo), segredos do cofre ao digitar e a alçada nos cliques e atalhos que confirmam algo."""
import base64

import httpx

from app.db import SessionLocal
from app.models import TaskFile
from app.services import desktop

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401
from .test_phase_g import _close_leftover_tasks, _gate  # noqa: F401

PNG = base64.b64encode(b"\x89PNG tela").decode()


class FakeDesktop:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    def post(self, url, json=None, headers=None):
        name, args = json["params"]["name"], json["params"]["arguments"]
        self.calls.append((name, args, headers.get("x-session-id")))
        if name == "open" and args["app"] not in args["allowed"]:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [
                {"type": "text", "text": f"Error executing tool open: o app '{args['app']}' não está liberado"}]}},
                request=httpx.Request("POST", url))
        out = {"window": "ERP Legado 7.2", "app": "Tk", "size": [760, 460], "elements": ["[0] window \"ERP Legado 7.2\""],
               "dica": "sem árvore de acessibilidade: clique por x,y", "screenshot": PNG}
        if name != "open" and name != "look":
            out = {"ok": True, "detail": f"{name} feito", **out}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"result": out}}},
                              request=httpx.Request("POST", url))


def test_governed_desktop_apps_images_secrets_and_commit_gate(client, env, rt, monkeypatch):
    fake = FakeDesktop()
    monkeypatch.setattr(desktop, "HTTP", lambda timeout=120: fake)
    monkeypatch.setattr(desktop, "_UP", (0.0, False))
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    tid = assign(client, env, slug, "dar baixa na fatura 88")
    assert "desligada" in _gate(slug, tid, "desktop_open", {"app": "ERP Legado"})["message"]  # sem apps liberados: indisponível
    assert client.put(f"/api/employees/{slug}/desktop-apps", headers=env["h"]["req"], json={"apps": ["ERP Legado"]}).status_code == 403
    r = client.put(f"/api/employees/{slug}/desktop-apps", headers=env["h"]["own"], json={"apps": ["ERP Legado", " "]})
    assert r.json() == {"apps": ["ERP Legado"], "ready": True}
    assert "não está liberado" in _gate(slug, tid, "desktop_open", {"app": "Terminal"})["result"]
    g = _gate(slug, tid, "desktop_open", {"app": "ERP Legado"})
    assert g["status"] == "allowed" and g["images"] == [["image/png", PNG]] and PNG not in g["result"]  # a tela vai ao modelo
    assert fake.calls[-1][2].endswith(f"-task-{tid}")  # uma sessão de desktop por tarefa
    events = task(client, env, tid)["events"]
    assert all(PNG not in str(e["payload"]) for e in events)  # nem na linha do tempo
    client.put("/api/secrets", headers=ADMIN, json={"name": "erp-senha", "value": "s3nh4-erp"})
    g = _gate(slug, tid, "desktop_type", {"text": "secret://erp-senha", "label": "Senha", "x": 440, "y": 80})
    assert g["status"] == "allowed" and "s3nh4" not in g["result"]
    assert fake.calls[-1][1] == {"allowed": ["ERP Legado"], "text": "s3nh4-erp", "x": 440, "y": 80}  # o cofre digita
    assert any(e["kind"] == "desktop" and e["payload"].get("value") == "[segredo]" for e in task(client, env, tid)["events"])
    assert _gate(slug, tid, "desktop_click", {"label": "Consultar", "x": 660, "y": 79})["status"] == "allowed"  # consultar não pede alçada
    g = _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 408, "y": 365})
    assert g["status"] == "waiting" and g["action_type"] == "write_internal"
    req = open_request(client, env, tid)
    assert "Tela: tela-" in req["rationale"] and req["payload"] == {"label": "Confirmar baixa", "window": "ERP Legado 7.2",
                                                                    "x": 408, "y": 365}  # o clique exato
    with SessionLocal() as db:
        assert db.query(TaskFile).filter(TaskFile.task_id == tid, TaskFile.name.like("tela-%")).count() == 1  # a evidência fica na tarefa
    client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    g = _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 408, "y": 365})
    assert g["status"] == "allowed" and fake.calls[-1][0] == "click" and fake.calls[-1][1]["x"] == 408
    g = _gate(slug, tid, "desktop_key", {"key": "s", "modifiers": ["ctrl"]})  # salvar por atalho também passa pela alçada
    assert g["status"] == "waiting"
    assert desktop._commit_ref("Pagar boleto", False) == "platform:desktop_pay"
    assert desktop._commit_ref("Abrir menu", False) is None
