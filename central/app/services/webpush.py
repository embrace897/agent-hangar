"""Notificações do navegador (Web Push) para o portal instalado como app (PWA): o pedido chega no Windows, Mac,
Linux, Android ou iPhone mesmo com o portal fechado.

Sem dependência nova: VAPID (RFC 8292, JWT ES256) e a cifra do conteúdo (RFC 8291, aes128gcm) com `cryptography`.
O par de chaves VAPID é da instalação — vem de WEBPUSH_VAPID_PRIVATE_KEY ou é gerado na primeira vez e guardado
cifrado no banco (todas as réplicas usam o mesmo). Cada navegador do membro guarda uma inscrição; a que o serviço
de push diz que não existe mais (404/410) é apagada.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import crypto
from ..models import Ephemeral, PushSubscription, now
from .access import Access
from .common import PlatformError, iso

log = logging.getLogger("hangar.webpush")
HTTP = lambda: httpx.Client(timeout=10)  # noqa: E731  (os testes trocam)
VAPID_KEY = "webpush:vapid"
FOREVER = datetime(9999, 1, 1, tzinfo=UTC)
SUBJECT = os.environ.get("WEBPUSH_SUBJECT", "mailto:admin@agent-hangar.local")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _raw_public(k: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    pub = k.public_key() if isinstance(k, ec.EllipticCurvePrivateKey) else k
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ------------------------------------------------------------------ chaves VAPID da instalação
def _load(pem: str) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(pem.encode(), password=None)


def vapid(db) -> ec.EllipticCurvePrivateKey:
    env = os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY", "").strip()
    if env:
        return _load(env.replace("\\n", "\n"))
    row = db.get(Ephemeral, VAPID_KEY)
    if row is None:
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        db.add(Ephemeral(key=VAPID_KEY, value={"pem": crypto.encrypt(pem)}, expires_at=FOREVER))
        try:
            db.commit()
            return key
        except IntegrityError:  # outra réplica gerou ao mesmo tempo: vale a dela
            db.rollback()
            row = db.get(Ephemeral, VAPID_KEY)
    return _load(crypto.decrypt(row.value["pem"]))


def public_key(db) -> str:
    """A chave pública (applicationServerKey) que o navegador usa para se inscrever."""
    return _b64(_raw_public(vapid(db)))


def _jwt(key: ec.EllipticCurvePrivateKey, endpoint: str) -> str:
    u = urlparse(endpoint)
    head = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SUBJECT},
                           separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{body}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ------------------------------------------------------------------ cifra do conteúdo (RFC 8291)
def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def encrypt(payload: bytes, p256dh: str, auth: str, _eph: ec.EllipticCurvePrivateKey | None = None,
            _salt: bytes | None = None) -> bytes:
    ua_pub = _unb64(p256dh)
    secret = _unb64(auth)
    eph = _eph or ec.generate_private_key(ec.SECP256R1())
    as_pub = _raw_public(eph)
    shared = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = _salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + body


# ------------------------------------------------------------------ inscrições do membro
def subscribe(db, acc: Access, sub: dict, user_agent: str = "") -> dict:
    if not acc.p.user_id:
        raise PlatformError("só uma pessoa logada recebe notificações")
    endpoint = str(sub.get("endpoint") or "")
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise PlatformError("inscrição inválida: endpoint https e keys.p256dh/keys.auth")
    try:
        if len(_unb64(keys["p256dh"])) != 65 or len(_unb64(keys["auth"])) < 16:
            raise ValueError
    except ValueError:
        raise PlatformError("inscrição inválida: chaves do navegador") from None
    h = hashlib.sha256(endpoint.encode()).hexdigest()
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == h))
    if row is None:
        row = PushSubscription(endpoint_hash=h)
        db.add(row)
    row.user_id, row.endpoint = acc.p.user_id, endpoint
    row.p256dh, row.auth = keys["p256dh"], crypto.encrypt(keys["auth"])
    row.user_agent = (user_agent or "")[:300]
    db.commit()
    return {"id": row.id}


def unsubscribe(db, acc: Access, endpoint: str) -> dict:
    h = hashlib.sha256((endpoint or "").encode()).hexdigest()
    n = 0
    for row in db.scalars(select(PushSubscription).where(PushSubscription.endpoint_hash == h,
                                                          PushSubscription.user_id == acc.p.user_id)).all():
        db.delete(row)
        n += 1
    db.commit()
    return {"removed": n}


def mine(db, acc: Access) -> list[dict]:
    from .workspace import device
    rows = db.scalars(select(PushSubscription).where(PushSubscription.user_id == acc.p.user_id).order_by(PushSubscription.id))
    return [{"id": r.id, "device": device(r.user_agent), "created_at": iso(r.created_at),
             "last_sent_at": iso(r.last_sent_at) if r.last_sent_at else None} for r in rows]


# ------------------------------------------------------------------ envio
def send(db, user_id: int, title: str, body: str = "", url: str = "/app/#/decisions", tag: str = "") -> int:
    """Manda para todos os navegadores inscritos da pessoa. Devolve quantos aceitaram."""
    rows = db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all()
    if not rows:
        return 0
    key = vapid(db)
    k = public_key(db)
    payload = json.dumps({"title": title[:120], "body": body[:300], "url": url, "tag": tag}).encode()
    ok = 0
    with HTTP() as http:
        for r in rows:
            try:
                res = http.post(r.endpoint, content=encrypt(payload, r.p256dh, crypto.decrypt(r.auth)), headers={
                    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": "86400",
                    "Urgency": "high", "Authorization": f"vapid t={_jwt(key, r.endpoint)}, k={k}"})
            except httpx.HTTPError as e:
                log.warning("push: falha de rede (%s)", e)
                continue
            if res.status_code in (404, 410):  # o navegador saiu: a inscrição morreu
                db.delete(r)
            elif res.status_code < 300:
                r.last_sent_at = now()
                ok += 1
            else:
                log.warning("push: o serviço respondeu %s", res.status_code)
    db.commit()
    return ok


def test_mine(db, acc: Access) -> dict:
    n = send(db, acc.p.user_id, "Agent Hangar", "As notificações estão funcionando neste navegador.", "/app/#/", "test")
    if not n:
        raise PlatformError("nenhum navegador inscrito recebeu: ative as notificações neste navegador primeiro")
    return {"sent": n}
