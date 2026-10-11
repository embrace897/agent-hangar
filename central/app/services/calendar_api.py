"""Agenda da empresa: horários livres e convites, pelo Microsoft 365 (Graph, credencial de aplicativo do tenant) ou
pelo Google Workspace (conta de serviço com delegação no domínio). Configurada uma vez pelo admin; ninguém precisa
conectar a própria agenda, e as credenciais ficam criptografadas.
"""
import base64
import json
import time
from datetime import UTC, datetime, timedelta

import httpx

from .. import crypto
from . import notify
from .common import PlatformError

HTTP = lambda: httpx.Client(timeout=20)  # noqa: E731  (os testes trocam)
_TOKENS: dict[str, tuple[float, str]] = {}


def provider(cfg: dict) -> str:
    if cfg.get("calendar_provider") == "microsoft" and cfg.get("ms_tenant") and cfg.get("ms_client_id") and cfg.get("ms_client_secret"):
        return "microsoft"
    if cfg.get("calendar_provider") == "google" and cfg.get("google_sa"):
        return "google"
    return ""


def configure(cfg: dict, changes: dict):
    if changes.get("calendar_provider") is not None:
        if changes["calendar_provider"] not in ("", "microsoft", "google"):
            raise PlatformError("calendar_provider: microsoft | google | (vazio)")
        cfg["calendar_provider"] = changes["calendar_provider"]
    for k in ("ms_tenant", "ms_client_id"):
        if changes.get(k) is not None:
            cfg[k] = str(changes[k]).strip()[:120]
    if changes.get("ms_client_secret") is not None:
        v = str(changes["ms_client_secret"]).strip()
        cfg["ms_client_secret"] = crypto.encrypt(v) if v else ""
    if changes.get("google_sa") is not None:
        v = str(changes["google_sa"]).strip()
        if v:
            try:
                sa = json.loads(v)
                assert sa.get("client_email") and sa.get("private_key")
            except (ValueError, AssertionError):
                raise PlatformError("cole o JSON da conta de serviço do Google (com client_email e private_key)") from None
        cfg["google_sa"] = crypto.encrypt(v) if v else ""


def admin_view(cfg: dict) -> dict:
    sa = notify._secret(cfg, "google_sa")
    return {"calendar": {"provider": provider(cfg), "chosen": cfg.get("calendar_provider", ""),
                         "ms_tenant": cfg.get("ms_tenant", ""), "ms_client_id": cfg.get("ms_client_id", ""),
                         "ms_client_secret": bool(cfg.get("ms_client_secret")),
                         "google_sa": (json.loads(sa).get("client_email") if sa else "")}}


# ------------------------------------------------------------------ tokens
def _ms_token(cfg: dict) -> str:
    key = f"ms:{cfg['ms_tenant']}:{cfg['ms_client_id']}"
    ts, tok = _TOKENS.get(key, (0, ""))
    if time.time() < ts:
        return tok
    with HTTP() as h:
        r = h.post(f"https://login.microsoftonline.com/{cfg['ms_tenant']}/oauth2/v2.0/token", data={
            "grant_type": "client_credentials", "client_id": cfg["ms_client_id"],
            "client_secret": notify._secret(cfg, "ms_client_secret"), "scope": "https://graph.microsoft.com/.default"})
    if r.status_code >= 400:
        raise PlatformError(f"Microsoft recusou a credencial: {r.text[:200]}")
    d = r.json()
    _TOKENS[key] = (time.time() + int(d.get("expires_in", 3600)) - 120, d["access_token"])
    return d["access_token"]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _google_token(cfg: dict, subject: str) -> str:
    key = f"g:{subject}"
    ts, tok = _TOKENS.get(key, (0, ""))
    if time.time() < ts:
        return tok
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    sa = json.loads(notify._secret(cfg, "google_sa"))
    t = int(time.time())
    claims = {"iss": sa["client_email"], "sub": subject, "scope": "https://www.googleapis.com/auth/calendar",
              "aud": "https://oauth2.googleapis.com/token", "iat": t, "exp": t + 3600}
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + "." + _b64(json.dumps(claims).encode())
    key_obj = serialization.load_pem_private_key(sa["private_key"].encode(), None)
    sig = key_obj.sign(head.encode(), padding.PKCS1v15(), hashes.SHA256())
    with HTTP() as h:
        r = h.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{head}.{_b64(sig)}"})
    if r.status_code >= 400:
        raise PlatformError(f"Google recusou a conta de serviço: {r.text[:200]}")
    d = r.json()
    _TOKENS[key] = (time.time() + int(d.get("expires_in", 3600)) - 120, d["access_token"])
    return d["access_token"]


# ------------------------------------------------------------------ agenda
def busy(cfg: dict, organizer: str, people: list[str], start: datetime, end: datetime) -> dict[str, list[tuple[datetime, datetime]]]:
    """Intervalos ocupados de cada pessoa (UTC)."""
    p = provider(cfg)
    out: dict[str, list] = {x: [] for x in people}
    if p == "microsoft":
        with HTTP() as h:
            r = h.post(f"https://graph.microsoft.com/v1.0/users/{organizer}/calendar/getSchedule",
                       headers={"Authorization": f"Bearer {_ms_token(cfg)}", "Prefer": 'outlook.timezone="UTC"'},
                       json={"schedules": people, "startTime": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
                             "endTime": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
                             "availabilityViewInterval": 30})
        if r.status_code >= 400:
            raise PlatformError(f"Microsoft Graph: {r.status_code} {r.text[:200]}")
        for s in r.json().get("value") or []:
            for it in s.get("scheduleItems") or []:
                if it.get("status") in ("busy", "tentative", "oof", "workingElsewhere"):
                    out.setdefault(s["scheduleId"].lower(), []).append((_dt(it["start"]["dateTime"]), _dt(it["end"]["dateTime"])))
    elif p == "google":
        with HTTP() as h:
            r = h.post("https://www.googleapis.com/calendar/v3/freeBusy",
                       headers={"Authorization": f"Bearer {_google_token(cfg, organizer)}"},
                       json={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": x} for x in people]})
        if r.status_code >= 400:
            raise PlatformError(f"Google Calendar: {r.status_code} {r.text[:200]}")
        for who, c in (r.json().get("calendars") or {}).items():
            out[who.lower()] = [(_dt(b["start"]), _dt(b["end"])) for b in c.get("busy") or []]
    else:
        raise PlatformError("agenda da empresa não configurada")
    return out


def _dt(s: str) -> datetime:
    """ISO do Graph (fração de 7 dígitos, sem fuso) ou do Google (com Z ou deslocamento) → aware em UTC."""
    import re
    d = datetime.fromisoformat(re.sub(r"\.\d+", "", s.replace("Z", "+00:00")))
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)


def create_event(cfg: dict, organizer: str, subject: str, start: datetime, end: datetime, attendees: list[str],
                 body: str = "") -> str:
    p = provider(cfg)
    if p == "microsoft":
        with HTTP() as h:
            r = h.post(f"https://graph.microsoft.com/v1.0/users/{organizer}/events",
                       headers={"Authorization": f"Bearer {_ms_token(cfg)}"},
                       json={"subject": subject, "body": {"contentType": "text", "content": body},
                             "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
                             "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
                             "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in attendees],
                             "isOnlineMeeting": True})
    elif p == "google":
        with HTTP() as h:
            r = h.post(f"https://www.googleapis.com/calendar/v3/calendars/{organizer}/events?sendUpdates=all",
                       headers={"Authorization": f"Bearer {_google_token(cfg, organizer)}"},
                       json={"summary": subject, "description": body, "start": {"dateTime": start.isoformat()},
                             "end": {"dateTime": end.isoformat()}, "attendees": [{"email": a} for a in attendees]})
    else:
        raise PlatformError("agenda da empresa não configurada")
    if r.status_code >= 400:
        raise PlatformError(f"o convite não foi criado: {r.status_code} {r.text[:200]}")
    return str(r.json().get("id", ""))


def free_slots(cal, busy_map: dict, start: datetime, end: datetime, minutes: int, limit: int = 5) -> list[datetime]:
    """Horários em que todos estão livres, dentro do expediente e dos dias úteis da empresa."""
    from . import bizcal
    z = bizcal.zone(cal.tz)
    h0, h1 = cal.hours()
    out, t = [], start.astimezone(z)
    t = t.replace(minute=0 if t.minute == 0 else 30 if t.minute <= 30 else 0, second=0, microsecond=0)
    if t < start.astimezone(z):
        t += timedelta(minutes=30)
    dur = timedelta(minutes=minutes)
    while t + dur <= end.astimezone(z) and len(out) < limit:
        ok_day = cal.is_business_day(t.date()) and t.time() >= h0 and (t + dur).time() <= h1 and (t + dur).date() == t.date()
        if ok_day:
            s, e = t.astimezone(UTC), (t + dur).astimezone(UTC)
            if all(not (bs < e and be > s) for spans in busy_map.values() for bs, be in spans):
                out.append(s)
        t += timedelta(minutes=30)
    return out
