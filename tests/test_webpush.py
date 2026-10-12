# ruff: noqa: F811  (env é fixture importada de test_employees)
"""Portal instalável (PWA) e notificações do navegador (Web Push): a cifra aes128gcm (RFC 8291) abre do lado do
navegador, o VAPID (RFC 8292) assina, a inscrição é de quem está logado, o pedido novo chega no aparelho e a
inscrição que o serviço de push diz que morreu é apagada."""
import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.db import SessionLocal
from app.models import PushSubscription
from app.services import notify, webpush

from .conftest import ADMIN
from .test_employees import _fields, env  # noqa: F401


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Browser:
    """O lado do navegador: as chaves da inscrição e a decifração (como o Chrome/Firefox fazem)."""

    def __init__(self, endpoint="https://push.example.com/send/abc"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pub = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = b"0123456789abcdef"
        self.endpoint = endpoint

    def sub(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": _b64(self.pub), "auth": _b64(self.auth)}}

    def open(self, body: bytes) -> dict:
        salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        as_pub = body[21:21 + idlen]
        assert rs == 4096 and idlen == 65
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        ikm = webpush._hkdf(self.auth, shared, b"WebPush: info\x00" + self.pub + as_pub, 32)
        cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])


class FakePush:
    def __init__(self, status=201):
        self.sent, self.status = [], status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None):
        self.sent.append({"url": url, "body": content, "headers": headers})
        return type("R", (), {"status_code": self.status})()


def _check_vapid(headers: dict, public_key: str):
    t = headers["Authorization"].split("t=")[1].split(",")[0]
    k = headers["Authorization"].split("k=")[1]
    assert k == public_key
    head, body, sig = t.split(".")
    claims = json.loads(_unb64(body))
    assert claims["aud"] == "https://push.example.com" and claims["sub"].startswith("mailto:")
    raw = _unb64(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64(k))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))  # levanta se a assinatura não bate


def _sync(fn, *args):
    with SessionLocal() as db:
        fn(db, *args)


def test_pwa_files_and_encryption_roundtrip(client):
    m = client.get("/app/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["scope"] == "/app/" and m.json()["display"] == "standalone"
    sw = client.get("/app/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "showNotification" in sw.text
    assert client.get("/app/icon-512.png").headers["content-type"] == "image/png"
    assert client.get("/app/style.css").status_code == 404 and client.get("/app/secret.txt").status_code == 404  # só a lista
    b = Browser()
    assert b.open(webpush.encrypt(b'{"title": "oi"}', _b64(b.pub), _b64(b.auth))) == {"title": "oi"}


def test_subscribe_send_and_dead_subscription(client, env, monkeypatch):
    h = env["h"]["mgr"]
    key = client.get("/api/me/push", headers=h).json()["public_key"]
    assert len(_unb64(key)) == 65 and client.get("/api/me/push", headers=h).json()["public_key"] == key  # estável
    assert client.post("/api/me/push", headers=h, json={"endpoint": "http://insecure", "keys": {}}).status_code == 400
    b = Browser()
    assert client.post("/api/me/push", headers=h, json=b.sub()).status_code == 200
    assert client.post("/api/me/push", headers=h, json=b.sub()).status_code == 200  # de novo: atualiza, não duplica
    assert len(client.get("/api/me/push", headers=h).json()["subscriptions"]) == 1
    with SessionLocal() as db:
        row = db.query(PushSubscription).filter_by(endpoint=b.endpoint).one()
        assert row.auth != _b64(b.auth)  # a chave do navegador fica cifrada
    fake = FakePush()
    monkeypatch.setattr(webpush, "HTTP", lambda: fake)
    assert client.post("/api/me/push/test", headers=h).json()["sent"] == 1
    msg = fake.sent[-1]
    assert msg["url"] == b.endpoint and msg["headers"]["Content-Encoding"] == "aes128gcm"
    _check_vapid(msg["headers"], key)
    assert b.open(msg["body"])["title"] == "Agent Hangar"
    # um pedido novo para o gestor chega no aparelho (com o link para decidir)
    monkeypatch.setattr(notify, "_bg", _sync)
    owner = env["h"]["req"]
    body = {**_fields(env), "personal": True}
    body.pop("manager", None)
    slug = client.post("/api/employees", headers=owner, json=body).json()["employee"]["slug"]
    req = client.post(f"/api/employees/{slug}/promote-to-team", headers=owner, json={"note": "time"}).json()["request"]
    got = b.open(fake.sent[-1]["body"])
    assert got["url"] == f"/app/#/decisions?open={req}" and got["tag"] == f"req-{req}"
    # o navegador saiu: o serviço responde 410 e a inscrição é apagada
    fake.status = 410
    assert client.post("/api/me/push/test", headers=h).status_code == 400
    assert client.get("/api/me/push", headers=h).json()["subscriptions"] == []
    # desinscrever é só da própria pessoa
    assert client.post("/api/me/push", headers=h, json=b.sub()).status_code == 200
    assert client.post("/api/me/push/remove", headers=env["h"]["own"], json={"endpoint": b.endpoint}).json()["removed"] == 0
    assert client.post("/api/me/push/remove", headers=h, json={"endpoint": b.endpoint}).json()["removed"] == 1
    assert client.post("/api/me/push", headers=ADMIN, json=b.sub()).status_code == 400  # token admin não é pessoa
