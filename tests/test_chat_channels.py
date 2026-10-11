# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Telegram e Discord como canais de entrada: cada pessoa liga a conta com um código de uso único, a mensagem vira
tarefa do Digital employee e o resultado volta para a mesma conversa. Assinaturas conferidas (segredo do webhook no
Telegram, Ed25519 no Discord)."""
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.db import SessionLocal
from app.models import EmployeeTask, Organization, User
from app.services import notify

from .conftest import ADMIN
from .test_employees import env, hire, make_active, rt, task, work  # noqa: F401
from .test_employees_notify import _sync

TG_TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"


class FakeChatApis:
    """api.telegram.org e discord.com: guarda as chamadas e responde ok."""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _resp(self, method, url, body):
        self.calls.append((method, url, body))
        if "api.telegram.org" in url:
            out = {"ok": True, "result": {"username": "hangar_bot"} if url.endswith("/getMe") else {}}
        else:
            out = [] if method == "PUT" else {"id": "1"}
        return httpx.Response(200, json=out, request=httpx.Request(method, url))

    def post(self, url, json=None, **kw):
        return self._resp("POST", url, json)

    def request(self, method, url, json=None, **kw):
        return self._resp(method, url, json)

    def of(self, suffix):
        return [b for m, u, b in self.calls if u.endswith(suffix)]


@pytest.fixture()
def apis(monkeypatch):
    fake = FakeChatApis()
    monkeypatch.setattr(notify, "_bg", _sync)
    monkeypatch.setattr(notify, "HTTP", lambda: fake)
    yield fake
    with SessionLocal() as db:
        db.get(Organization, 1).notify = None
        for u in db.query(User).filter(User.notify.is_not(None)):
            u.notify = None
        db.commit()


def _employee(client, env, rt, name):
    slug = hire(client, env, name=name)
    make_active(client, env, slug, rt)
    return slug


def _tg(client, secret, update_id, user, text, chat=-100, chat_type="private"):
    body = {"update_id": update_id, "message": {"message_id": update_id * 10, "text": text, "from": {"id": user, "is_bot": False},
                                                "chat": {"id": chat, "type": chat_type}}}
    return client.post("/hooks/telegram/1", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def test_telegram_link_then_message_becomes_a_task_and_the_result_comes_back(client, env, rt, apis):
    r = client.put("/api/admin/notifications", headers=ADMIN, json={"telegram_bot_token": TG_TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["telegram"] == {"configured": True, "bot": "hangar_bot", "webhook_error": ""}
    assert TG_TOKEN not in json.dumps(r.json())
    hook = apis.of("/setWebhook")[-1]
    assert hook["url"].endswith("/hooks/telegram/1") and hook["allowed_updates"] == ["message"]
    secret = hook["secret_token"]
    assert client.put("/api/admin/notifications", headers=env["h"]["req"], json={"telegram_bot_token": TG_TOKEN}).status_code == 403
    name = f"Tereza {env['n']}"
    slug = _employee(client, env, rt, name)
    link = client.post("/api/me/notifications/chat-link", headers=env["h"]["req"], json={"channel": "telegram"}).json()
    assert link["url"] == f"https://t.me/hangar_bot?start={link['code']}" and link["how"] == f"/start {link['code']}"
    # sem o segredo do webhook: nada
    assert _tg(client, "errado", 1, 555, f"/start {link['code']}").status_code == 401
    assert _tg(client, secret, 1, 555, f"/start {link['code']}").status_code == 200
    assert "Pronto" in apis.of("/sendMessage")[-1]["text"]
    assert client.get("/api/me/notifications", headers=env["h"]["req"]).json()["chat"]["linked"]["telegram"] is True
    assert _tg(client, secret, 2, 556, f"/start {link['code']}").status_code == 200  # o código é de uso único
    assert "inválido" in apis.of("/sendMessage")[-1]["text"]
    # quem não ligou a conta recebe o caminho
    _tg(client, secret, 3, 777, f"{name}, renova a ACME")
    assert "Conectar Telegram" in apis.of("/sendMessage")[-1]["text"]
    # a mensagem vira tarefa, com a resposta na conversa
    _tg(client, secret, 4, 555, f"{name}, renova o contrato da ACME")
    reply = apis.of("/sendMessage")[-1]
    assert reply["chat_id"] == -100 and reply["reply_to_message_id"] == 40 and "Tarefa #" in reply["text"]
    with SessionLocal() as db:
        t = db.query(EmployeeTask).filter_by(source="telegram").order_by(EmployeeTask.id.desc()).first()
        tid = t.id
        assert t.title == "renova o contrato da ACME" and t.requester == env["em"]["req"]
        assert t.origin == {"telegram": {"chat": -100, "message": 40}}
    n = len(apis.of("/sendMessage"))
    _tg(client, secret, 4, 555, f"{name}, renova o contrato da ACME")  # o Telegram reenviou
    assert len(apis.of("/sendMessage")) == n
    # num grupo, só o que é para o bot
    _tg(client, secret, 5, 555, "conversa de grupo qualquer", chat=-200, chat_type="group")
    assert len(apis.of("/sendMessage")) == n
    work()
    assert task(client, env, tid)["status"] == "done"
    done = apis.of("/sendMessage")[-1]
    assert done["chat_id"] == -100 and f"#{tid}" in done["text"] and slug  # o resultado volta para a conversa
    client.delete("/api/me/notifications/chat-link/telegram", headers=env["h"]["req"])
    assert client.get("/api/me/notifications", headers=env["h"]["req"]).json()["chat"]["linked"]["telegram"] is False


def _discord_keys():
    k = Ed25519PrivateKey.generate()
    return k, k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _dc(client, key, body, bad=False):
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = key.sign(ts.encode() + raw).hex()
    if bad:
        sig = "00" * 64
    return client.post("/hooks/discord/1", content=raw, headers={"X-Signature-Ed25519": sig, "X-Signature-Timestamp": ts,
                                                                "Content-Type": "application/json"})


def _cmd(user, text, channel="C1"):
    return {"type": 2, "channel_id": channel, "member": {"user": {"id": user}},
            "data": {"name": "hangar", "options": [{"name": "text", "type": 3, "value": text}]}}


def test_discord_signed_command_becomes_a_task_and_the_result_goes_to_the_channel(client, env, rt, apis):
    key, pub = _discord_keys()
    r = client.put("/api/admin/notifications", headers=ADMIN, json={"discord_app_id": "1234567890", "discord_public_key": pub,
                                                                     "discord_bot_token": "bot-token-x"})
    assert r.status_code == 200, r.text
    d = r.json()["discord"]
    assert d["configured"] and d["interactions_url"].endswith("/hooks/discord/1") and not d["command_error"]
    cmd = [b for m, u, b in apis.calls if m == "PUT" and u.endswith("/applications/1234567890/commands")][-1]
    assert cmd[0]["name"] == "hangar" and cmd[0]["options"][0]["name"] == "text"
    assert client.put("/api/admin/notifications", headers=ADMIN, json={"discord_public_key": "xyz"}).status_code == 400
    assert _dc(client, key, {"type": 1}).json() == {"type": 1}  # o PING do Discord
    assert _dc(client, key, {"type": 1}, bad=True).status_code == 401
    other, _ = _discord_keys()
    assert _dc(client, other, {"type": 1}).status_code == 401
    name = f"Dora {env['n']}"
    _employee(client, env, rt, name)
    out = _dc(client, key, _cmd("u9", f"{name}, renova a ACME")).json()
    assert out["data"]["flags"] == 64 and "Conectar Discord" in out["data"]["content"]
    code = client.post("/api/me/notifications/chat-link", headers=env["h"]["req"], json={"channel": "discord"}).json()["code"]
    out = _dc(client, key, _cmd("u9", f"link {code}")).json()
    assert out["data"]["flags"] == 64 and "Pronto" in out["data"]["content"]
    out = _dc(client, key, _cmd("u9", f"{name}, renova o contrato da ACME")).json()
    assert out["type"] == 4 and "Tarefa #" in out["data"]["content"]
    with SessionLocal() as db:
        t = db.query(EmployeeTask).filter_by(source="discord").order_by(EmployeeTask.id.desc()).first()
        tid = t.id
        assert t.origin == {"discord": {"channel": "C1"}} and t.requester == env["em"]["req"]
    work()
    assert task(client, env, tid)["status"] == "done"
    posted = [b for m, u, b in apis.calls if m == "POST" and u.endswith("/channels/C1/messages")]
    assert posted and f"#{tid}" in posted[-1]["content"] and posted[-1]["allowed_mentions"] == {"parse": []}
