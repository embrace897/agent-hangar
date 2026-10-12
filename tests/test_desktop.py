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
        self.calls, self.window = [], "ERP Legado 7.2"

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
        out = {"window": self.window, "app": "Tk", "size": [760, 460], "elements": ["[0] window \"ERP Legado 7.2\""],
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
    assert "Tela: tela-" in req["rationale"] and req["payload"] == {"label": "Confirmar baixa", "window": "ERP Legado 7.2"}
    assert '"x": 408' in req["rationale"]  # onde vai clicar, na justificativa
    with SessionLocal() as db:
        assert db.query(TaskFile).filter(TaskFile.task_id == tid, TaskFile.name.like("tela-%")).count() == 1  # a evidência fica na tarefa
    client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    g = _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 413, "y": 361})  # o modelo repete uns pixels ao lado
    assert g["status"] == "allowed" and fake.calls[-1][0] == "click" and (fake.calls[-1][1]["x"], fake.calls[-1][1]["y"]) == (408, 365)
    fake.window = "Confirmação"  # o ERP abre o diálogo "Confirma a baixa?": o Sim é parte da ação já aprovada
    g = _gate(slug, tid, "desktop_click", {"label": "Sim", "x": 130, "y": 125})
    assert g["status"] == "allowed" and fake.calls[-1][0] == "click"
    ev = [e for e in task(client, env, tid)["events"] if e["kind"] == "desktop"][-1]["payload"]
    assert ev["target"] == "Sim" and ev["covered_by"] == req["id"]
    assert _gate(slug, tid, "desktop_click", {"label": "Sim", "x": 130, "y": 125})["status"] == "waiting"  # um segundo Sim: pede de novo
    fake.window = "ERP Legado 7.2"
    g = _gate(slug, tid, "desktop_key", {"key": "s", "modifiers": ["ctrl"]})  # salvar por atalho também passa pela alçada
    assert g["status"] == "waiting"
    client.post(f"/api/decisions/{open_request(client, env, tid)['id']}", headers=env["h"]["mgr"], json={"decision": "reject"})
    assert _gate(slug, tid, "desktop_key", {"key": "Return"})["status"] == "allowed"  # Return num formulário: passa de linha
    fake.window = "Save the current workbook as"  # Return num diálogo de salvar: é a confirmação
    assert _gate(slug, tid, "desktop_key", {"key": "Return"})["status"] == "waiting"
    fake.window = "ERP Legado 7.2"
    assert desktop._commit_ref("Pagar boleto", False) == "platform:desktop_pay"
    assert desktop._commit_ref("Abrir menu", False) is None
    assert desktop._commit_ref("Sim", False) == "platform:desktop_commit"  # o "Sim" do diálogo "Confirma a baixa?"
    assert desktop._commit_ref("Save As", False) is None and desktop._commit_ref("Salvar como…", False) is None  # só abrem o diálogo
    assert desktop._commit_ref("Save", False) == "platform:desktop_commit"


def test_resume_after_approval_knows_what_the_round_already_did(client, env, rt, monkeypatch):
    """Achado da bateria de desktop: o checkpoint só guardava a fala final, e a rodada que retomava depois de uma
    aprovação refazia tudo (uma baixa lançada duas vezes). Agora guarda o registro do que foi feito."""
    from app.models import EmployeeTask
    from app.services import employee_tasks as tasksmod
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    orig = rt.post

    def post(url, headers=None, json=None, timeout=None):
        r = orig(url, headers, json, timeout)
        body = r.json()
        body["x_trace"] = [{"tool": "desktop_type", "args": {"label": "Número da fatura", "text": "88"}, "result": "ok"},
                           {"tool": "desktop_click", "args": {"label": "Sim", "x": 130, "y": 125}, "result": "Baixa registrada"},
                           {"tool": "desktop_look", "args": {}, "result": "tela"}]
        return httpx.Response(200, json=body, request=r.request)
    monkeypatch.setattr(rt, "post", post)
    tid = assign(client, env, slug, 'use send_email {"to": "a@b.com", "body": "baixas feitas"}')
    work()
    assert task(client, env, tid)["status"] == "waiting_human"
    with SessionLocal() as db:
        said = tasksmod.last_checkpoint(db, db.get(EmployeeTask, tid)).messages[-1]["content"]
    assert said.startswith("[Registro da rodada 1") and "desktop_click(label=Sim, x=130, y=125) → Baixa registrada" in said
    assert "desktop_look" not in said and "text=88" in said  # consultas de leitura ficam de fora
    assert tasksmod.journal([], 1) == ""


def test_desktop_approval_shows_the_fields_and_blocks_a_repeated_confirmation(client, env, rt, monkeypatch):
    """Achado da bateria: o modelo, sem certeza se a baixa da 88 tinha entrado, preencheu tudo de novo. O pedido mostra
    o que vai ser confirmado e o que já foi; a mesma confirmação com os mesmos campos não vira pedido sem repeat."""
    fake = FakeDesktop()
    monkeypatch.setattr(desktop, "HTTP", lambda timeout=120: fake)
    monkeypatch.setattr(desktop, "_UP", (0.0, False))
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    client.put(f"/api/employees/{slug}/desktop-apps", headers=env["h"]["own"], json={"apps": ["ERP Legado"]})
    tid = assign(client, env, slug, "baixas")

    def fill(fatura, valor):
        for label, text in (("Número da fatura:", fatura), ("Data do pagamento (dd/mm/aaaa)", "07/10/2026"), ("Valor pago", valor)):
            assert _gate(slug, tid, "desktop_type", {"label": label, "text": text, "x": 440, "y": 80})["status"] == "allowed"
    fill("88", "2500,00")
    assert _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 408, "y": 365})["status"] == "waiting"
    req = open_request(client, env, tid)
    assert "Vai confirmar: número da fatura=88, data do pagamento=07/10/2026, valor pago=2500,00" in req["rationale"]
    client.post(f"/api/decisions/{req['id']}", headers=env["h"]["mgr"], json={"decision": "approve"})
    assert _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 408, "y": 365})["status"] == "allowed"
    fill("88", "2500,00")  # o modelo, em dúvida, preenche a mesma baixa de novo
    g = _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 408, "y": 365})
    assert g["status"] == "allowed" and g["result"].startswith("NÃO CONFIRMEI") and f"#{req['id']}" in g["result"]
    assert fake.calls[-1][0] != "click"  # nada foi clicado
    g = _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 408, "y": 365, "repeat": True})
    assert g["status"] == "waiting" and "REPETIÇÃO" in open_request(client, env, tid)["rationale"]
    client.post(f"/api/decisions/{open_request(client, env, tid)['id']}", headers=env["h"]["mgr"], json={"decision": "reject"})
    fill("91", "980,40")  # outra fatura: segue normal, e o pedido lista o que já foi confirmado
    assert _gate(slug, tid, "desktop_click", {"label": "Confirmar baixa", "x": 408, "y": 365})["status"] == "waiting"
    assert f"Já confirmado nesta tarefa: #{req['id']} Confirmar baixa (número da fatura=88" in open_request(client, env, tid)["rationale"]
