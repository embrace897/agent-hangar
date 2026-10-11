"""Calendário útil da empresa: dias de trabalho, feriados (nacionais do Brasil já vêm; estaduais e municipais o admin
acrescenta) e expediente. Prazos de playbooks ("D+3 úteis"), rotinas no "1º dia útil do mês" e horários de reunião
usam este calendário.

Agenda por dia útil (rotinas e playbooks):
- `@bd 1 09:00` → 1º dia útil do mês às 9h; `@bd -1 17:00` → último dia útil do mês.
- `@bdaily 08:30` → todo dia útil.
Qualquer outra expressão é cron comum (`0 9 * * 1-5`).
"""
from datetime import UTC, date, datetime, time, timedelta

from ..cron import Cron, CronError, zone
from ..models import Organization
from ..tenancy import current_org
from .common import PlatformError

DEFAULT = {"workdays": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00", "holidays": [], "removed": []}


def easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def national(y: int) -> dict[str, str]:
    """Feriados nacionais do Brasil (e os pontos facultativos que quase todas as empresas folgam)."""
    e = easter(y)
    days = {date(y, 1, 1): "Confraternização Universal", e - timedelta(days=48): "Carnaval",
            e - timedelta(days=47): "Carnaval", e - timedelta(days=2): "Sexta-feira Santa", date(y, 4, 21): "Tiradentes",
            date(y, 5, 1): "Dia do Trabalho", e + timedelta(days=60): "Corpus Christi", date(y, 9, 7): "Independência",
            date(y, 10, 12): "Nossa Senhora Aparecida", date(y, 11, 2): "Finados", date(y, 11, 15): "Proclamação da República",
            date(y, 11, 20): "Consciência Negra", date(y, 12, 25): "Natal"}
    return {d.isoformat(): n for d, n in days.items()}


class Calendar:
    def __init__(self, cfg: dict | None = None, tz: str = "UTC"):
        c = {**DEFAULT, **(cfg or {})}
        self.workdays = set(c["workdays"])
        self.start, self.end = c["start"], c["end"]
        self.extra = {h["date"]: h.get("name", "") for h in c.get("holidays") or []}
        self.removed = set(c.get("removed") or [])
        self.tz = tz
        self._years: dict[int, dict] = {}

    def holidays(self, y: int) -> dict[str, str]:
        if y not in self._years:
            self._years[y] = {**{k: v for k, v in national(y).items() if k not in self.removed},
                              **{k: v for k, v in self.extra.items() if k.startswith(str(y))}}
        return self._years[y]

    def is_business_day(self, d: date) -> bool:
        return d.weekday() in self.workdays and d.isoformat() not in self.holidays(d.year)

    def add(self, d: date, n: int) -> date:
        """n dias úteis depois (n<0: antes) de d. n=0: o próprio d, ou o próximo dia útil se d não for."""
        step = 1 if n >= 0 else -1
        while not self.is_business_day(d) and n == 0:
            d += timedelta(days=1)
        left = abs(n)
        while left:
            d += timedelta(days=step)
            if self.is_business_day(d):
                left -= 1
        return d

    def nth(self, y: int, m: int, n: int) -> date:
        """O n-ésimo dia útil do mês (n<0: contando do fim)."""
        days = [date(y, m, 1) + timedelta(days=i) for i in range(31)]
        days = [d for d in days if d.month == m and self.is_business_day(d)]
        if not days or abs(n) > len(days) or n == 0:
            raise PlatformError(f"o mês {m:02d}/{y} não tem {n}º dia útil")
        return days[n - 1] if n > 0 else days[n]

    def between(self, a: date, b: date) -> int:
        """Dias úteis de a (exclusive) até b (inclusive); negativo se b < a."""
        sign, (a, b) = (1, (a, b)) if b >= a else (-1, (b, a))
        n, d = 0, a
        while d < b:
            d += timedelta(days=1)
            n += self.is_business_day(d)
        return sign * n

    def hours(self) -> tuple[time, time]:
        return time.fromisoformat(self.start), time.fromisoformat(self.end)


def of(db) -> Calendar:
    o = db.get(Organization, current_org())
    return Calendar((o.calendar if o else None) or {}, (o.timezone if o else None) or "UTC")


def view(db, years: list[int] | None = None) -> dict:
    cal = of(db)
    ys = years or [datetime.now(UTC).year, datetime.now(UTC).year + 1]
    return {"workdays": sorted(cal.workdays), "start": cal.start, "end": cal.end, "timezone": cal.tz,
            "holidays": [{"date": d, "name": n, "national": d in national(int(d[:4]))} for y in ys
                         for d, n in sorted(cal.holidays(y).items())],
            "extra": [{"date": d, "name": n} for d, n in sorted(cal.extra.items())], "removed": sorted(cal.removed)}


def update(db, acc, changes: dict) -> dict:
    from .access import Forbidden
    from .common import audit
    if not acc.p.is_admin:
        raise Forbidden("só o admin muda o calendário da empresa")
    o = db.get(Organization, current_org())
    c = {**DEFAULT, **(o.calendar or {})}
    if changes.get("workdays") is not None:
        wd = sorted({int(x) for x in changes["workdays"]})
        if not wd or any(x < 0 or x > 6 for x in wd):
            raise PlatformError("workdays: 0 (segunda) a 6 (domingo)")
        c["workdays"] = wd
    for k in ("start", "end"):
        if changes.get(k):
            try:
                time.fromisoformat(changes[k])
            except ValueError:
                raise PlatformError(f"{k}: HH:MM") from None
            c[k] = changes[k]
    if changes.get("holidays") is not None:
        hs = []
        for h in changes["holidays"]:
            try:
                date.fromisoformat(str(h.get("date")))
            except ValueError:
                raise PlatformError(f"feriado com data inválida: {h}") from None
            hs.append({"date": str(h["date"]), "name": str(h.get("name") or "Feriado")[:80]})
        c["holidays"] = hs
    if changes.get("removed") is not None:  # feriados nacionais que a empresa trabalha (ex.: Carnaval)
        c["removed"] = sorted({str(x) for x in changes["removed"]})
    o.calendar = c
    db.commit()
    audit(db, acc.p.name, "org.calendar", "org", ", ".join(sorted(k for k, v in changes.items() if v is not None)))
    return view(db)


# ------------------------------------------------------------------ agenda por dia útil
def parse(expr: str) -> tuple[str, int, time] | None:
    parts = (expr or "").split()
    if not parts or parts[0] not in ("@bd", "@bdaily"):
        return None
    try:
        if parts[0] == "@bdaily" and len(parts) == 2:
            return "daily", 0, time.fromisoformat(parts[1])
        if parts[0] == "@bd" and len(parts) == 3 and int(parts[1]) != 0 and abs(int(parts[1])) <= 23:
            return "nth", int(parts[1]), time.fromisoformat(parts[2])
    except ValueError:
        pass
    raise CronError("agenda por dia útil: '@bd N HH:MM' (N-ésimo dia útil do mês; -1 = último) ou '@bdaily HH:MM'")


def validate(expr: str):
    if parse(expr) is None:
        Cron(expr)


def describe(expr: str) -> str:
    p = parse(expr)
    if p is None:
        return Cron(expr).describe()
    kind, n, t = p
    if kind == "daily":
        return f"todo dia útil às {t.strftime('%H:%M')}"
    return (f"{n}º dia útil do mês" if n > 0 else ("último dia útil do mês" if n == -1 else f"{-n}º dia útil antes do fim do mês")) \
        + f" às {t.strftime('%H:%M')}"


def next_after(db, expr: str, after: datetime, tz: str = "UTC") -> datetime:
    """Próximo disparo depois de `after` (UTC): cron comum ou agenda por dia útil."""
    p = parse(expr)
    if p is None:
        return Cron(expr).next_after(after, tz)
    kind, n, t = p
    cal, z = of(db), zone(tz)
    local = after.astimezone(z)
    d = local.date()
    for _ in range(800):
        if kind == "daily":
            cand = d if cal.is_business_day(d) else None
        else:
            try:
                cand = cal.nth(d.year, d.month, n)
            except PlatformError:
                cand = None
            if cand is not None and cand < d:
                cand = None
        if cand is not None:
            at = datetime.combine(cand, t, tzinfo=z)
            if at > local:
                return at.astimezone(UTC)
        d = (d + timedelta(days=1)) if kind == "daily" else (date(d.year + (d.month == 12), d.month % 12 + 1, 1))
    raise CronError("a agenda nunca dispara")
