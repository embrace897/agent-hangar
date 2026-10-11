"""Navegador governado: o Digital employee opera sistemas sem API (portal do banco, site do governo, ERP antigo) por um
navegador isolado (perfil `browser`), com a alçada na central.

- **Domínios liberados:** o funcionário só abre páginas dos domínios do cargo (`browser_domains`) ou do procedimento
  em execução; qualquer outra navegação é bloqueada no próprio navegador.
- **Ações que confirmam algo** (enviar, salvar, pagar, transferir, aprovar, assinar…) passam pela alçada com a
  captura da tela no pedido: pagar/transferir é `financial`, enviar é `send_external`, o resto é `write_internal`.
- **Segredos:** `value: "secret://nome"` preenche com o valor do cofre — o modelo nunca vê o valor, e a linha do tempo
  registra só "[segredo]".
- **CAPTCHA e verificação em duas etapas** nunca são contornados: viram uma pergunta para uma pessoa resolver.
- **Procedimentos:** um passo a passo versionado (abrir, preencher, clicar…) que o funcionário executa com
  `run_procedure` — escrito à mão ou salvo a partir de uma tarefa que deu certo.
"""
import base64
import json
import re
import time

import httpx
from sqlalchemy import select

from .. import config, tenancy
from ..models import Procedure, now
from . import employee_tasks as tasksmod
from . import task_files
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso, slugify
from .platform_tools import Ctx, side_effect, tool

HTTP = lambda timeout=60: httpx.Client(timeout=timeout)  # noqa: E731  (os testes trocam)
_UP: tuple[float, bool] = (0.0, False)
PAY = re.compile(r"\b(pagar|pague|pagamento|transferir|transfer[eê]ncia|pix|pay|transfer)\b", re.I)
SEND = re.compile(r"\b(enviar|envie|send|submit|protocolar|transmitir)\b", re.I)
COMMIT = re.compile(r"\b(salvar|confirmar|concluir|finalizar|aprovar|assinar|gravar|emitir|cadastrar|excluir|apagar|"
                    r"cancelar|save|confirm|approve|sign|delete)\b", re.I)


def ready() -> bool:
    global _UP
    if not config.BROWSER_URL:
        return False
    ts, ok = _UP
    if time.monotonic() - ts < 60:
        return ok
    base = config.BROWSER_URL.rsplit("/mcp", 1)[0]
    try:
        with HTTP(5) as h:
            ok = h.get(base + "/health").status_code == 200
    except httpx.HTTPError:
        ok = False
    _UP = (time.monotonic(), ok)
    return ok


def _avail(db, e) -> bool:
    return ready()


def _sid(task) -> str:
    return f"hangar-{tenancy.current_org()}-task-{task.id}"


def call(task, name: str, args: dict, timeout: int = 60) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    try:
        with HTTP(timeout) as h:
            r = h.post(config.BROWSER_URL, json=body, headers={"Accept": "application/json, text/event-stream",
                                                               "Content-Type": "application/json", "x-session-id": _sid(task)})
    except httpx.HTTPError as e:
        raise PlatformError(f"navegador indisponível: {e}") from None
    if r.status_code >= 400:
        raise PlatformError(f"navegador respondeu {r.status_code}")
    data = r.json()
    if data.get("error"):
        raise PlatformError(f"navegador: {data['error'].get('message')}")
    res = data.get("result") or {}
    text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    if res.get("isError"):
        raise PlatformError(text[:400] or "erro no navegador")
    if res.get("structuredContent") is not None:
        sc = res["structuredContent"]
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def _domains(ctx: Ctx, extra: list[str] | None = None) -> list[str]:
    return sorted({d.lower().strip() for d in [*(ctx.e.browser_domains or []), *(extra or [])] if d and d.strip()})


def _active_procedure(ctx: Ctx) -> list[str]:
    from ..models import TaskEvent
    ev = ctx.db.scalar(select(TaskEvent).where(TaskEvent.task_id == ctx.task.id, TaskEvent.kind == "browser")
                       .order_by(TaskEvent.id.desc()))
    return (ev.payload or {}).get("domains", []) if ev else []


def _log(ctx: Ctx, action: str, target: str = "", value: str = "", state: dict | None = None, domains=None):
    tasksmod.event(ctx.db, ctx.task, "browser", {"action": action, "target": target[:200], "value": value[:200],
                                                  "url": (state or {}).get("url", ""), "domains": domains or []})


def _shot(ctx: Ctx, allowed: list[str], why: str) -> str:
    st = call(ctx.task, "screenshot", {"allowed": allowed})
    if not st.get("screenshot"):
        return ""
    f = task_files.store(ctx.db, ctx.task, f"tela-{int(time.time())}.png", base64.b64decode(st["screenshot"]), "output",
                         f"agent:{ctx.agent.slug}", "image/png", why[:200])
    ctx.db.commit()
    return f.name


def _captcha(ctx: Ctx, st: dict, allowed: list[str]) -> dict | None:
    if not st.get("captcha"):
        return None
    shot = _shot(ctx, allowed, "a página pediu verificação")
    q = (f"O site {st.get('url')} pediu CAPTCHA ou verificação em duas etapas, que eu não contorno. Resolva no "
         f"navegador da tarefa (ou mande o código) e responda aqui para eu continuar.{f' Tela: {shot}.' if shot else ''}")
    return tasksmod._gate(ctx.db, ctx.agent, ctx.task.id, "ask_human", "hangar:ask_human", {"question": q}, ctx.rationale, "ask_human")


def _state_out(st: dict) -> dict:
    out = {k: st[k] for k in ("url", "title", "text", "elements", "blocked") if k in st}
    if st.get("blocked"):
        out["aviso"] = "navegação para fora dos domínios liberados foi bloqueada"
    return out


@tool("browser_open", "Abre uma página no navegador da tarefa (só nos domínios liberados para você). Depois use "
      "browser_read para ver o texto e os campos.",
      {"properties": {"url": {"type": "string"}}, "required": ["url"]}, "browser", _avail)
def browser_open(ctx: Ctx, args: dict):
    allowed = _domains(ctx, _active_procedure(ctx))
    st = call(ctx.task, "open", {"url": str(args.get("url") or ""), "allowed": allowed})
    _log(ctx, "open", str(args.get("url")), state=st, domains=allowed)
    ctx.db.commit()
    return _captcha(ctx, st, allowed) or _state_out(st)


@tool("browser_read", "O texto visível da página atual e os campos, botões e links que dá para usar.",
      {"properties": {"max_chars": {"type": "integer"}}}, "browser", _avail)
def browser_read(ctx: Ctx, args: dict):
    allowed = _domains(ctx, _active_procedure(ctx))
    st = call(ctx.task, "read", {"allowed": allowed, "max_chars": int(args.get("max_chars") or 6000)})
    return _captcha(ctx, st, allowed) or _state_out(st)


@tool("browser_fill", "Preenche um campo pelo rótulo, placeholder ou seletor CSS. Para senhas e chaves use "
      "value=\"secret://nome\" (o cofre preenche; você nunca vê o valor).",
      {"properties": {"target": {"type": "string"}, "value": {"type": "string"}}, "required": ["target", "value"]},
      "browser", _avail)
def browser_fill(ctx: Ctx, args: dict):
    allowed = _domains(ctx, _active_procedure(ctx))
    value, shown = str(args.get("value") or ""), str(args.get("value") or "")
    if value.startswith("secret://"):
        from . import secrets_vault
        value = secrets_vault.resolve(value, ctx.agent.team_id, f"browser:{ctx.agent.slug}", ctx.db)
        shown = "[segredo]"
    st = call(ctx.task, "fill", {"target": str(args.get("target") or ""), "value": value, "allowed": allowed})
    _log(ctx, "fill", str(args.get("target")), shown if shown == "[segredo]" else shown[:60], st, allowed)
    ctx.db.commit()
    return _captcha(ctx, st, allowed) or {"ok": True, "url": st.get("url")}


@tool("browser_select", "Escolhe uma opção numa lista (pelo rótulo do campo e o texto da opção).",
      {"properties": {"target": {"type": "string"}, "value": {"type": "string"}}, "required": ["target", "value"]},
      "browser", _avail)
def browser_select(ctx: Ctx, args: dict):
    allowed = _domains(ctx, _active_procedure(ctx))
    st = call(ctx.task, "select", {"target": str(args.get("target") or ""), "value": str(args.get("value") or ""),
                                   "allowed": allowed})
    _log(ctx, "select", str(args.get("target")), str(args.get("value"))[:60], st, allowed)
    ctx.db.commit()
    return {"ok": True, "url": st.get("url")}


def _commit_ref(target: str, commit: bool) -> str | None:
    if PAY.search(target):
        return "platform:browser_pay"
    if SEND.search(target):
        return "platform:browser_send"
    if commit or COMMIT.search(target):
        return "platform:browser_commit"
    return None


@tool("browser_click", "Clica num botão ou link (pelo texto visível ou seletor). Um clique que confirma algo — enviar, "
      "salvar, pagar, transferir, aprovar, assinar — passa pela sua alçada com a captura da tela; marque commit=true "
      "quando o botão confirmar algo e o texto não deixar claro.",
      {"properties": {"target": {"type": "string"}, "commit": {"type": "boolean"}}, "required": ["target"]},
      "browser", _avail)
def browser_click(ctx: Ctx, args: dict):
    allowed = _domains(ctx, _active_procedure(ctx))
    target = str(args.get("target") or "")
    ref = _commit_ref(target, bool(args.get("commit")))
    if ref:
        page = call(ctx.task, "read", {"allowed": allowed, "max_chars": 1500})
        shot = _shot(ctx, allowed, f"antes de clicar em '{target}'")
        # a alçada decide sobre o clique exato (alvo e página); a captura e o texto vão na justificativa do pedido
        why = (f"{ctx.rationale}\nTela: {shot}\nPágina: {(page.get('text') or '')[:800]}").strip()
        held = side_effect(Ctx(ctx.db, ctx.e, ctx.agent, ctx.task, why), "browser_click", ref,
                           {"target": target, "url": page.get("url", "")})
        if held:
            return held
    st = call(ctx.task, "click", {"target": target, "allowed": allowed})
    _log(ctx, "click", target, state=st, domains=allowed)
    ctx.db.commit()
    return _captcha(ctx, st, allowed) or _state_out(st)


@tool("browser_screenshot", "Salva a captura da tela atual na tarefa (como evidência).", {"properties": {}}, "browser", _avail)
def browser_screenshot(ctx: Ctx, args: dict):
    name = _shot(ctx, _domains(ctx, _active_procedure(ctx)), "captura pedida pelo funcionário")
    return f"Captura salva na tarefa: {name}" if name else "sem captura"


# ------------------------------------------------------------------ procedimentos
STEP_ACTIONS = ("open", "fill", "select", "click", "read", "screenshot")


def _clean_steps(steps) -> list[dict]:
    out = []
    for s in steps or []:
        a = s.get("action")
        if a not in STEP_ACTIONS:
            raise PlatformError(f"ação do passo: {', '.join(STEP_ACTIONS)}")
        if a in ("open", "fill", "select", "click") and not str(s.get("target") or s.get("url") or "").strip():
            raise PlatformError(f"o passo '{a}' precisa do alvo")
        out.append({"action": a, "target": str(s.get("target") or s.get("url") or "")[:500], "value": str(s.get("value") or "")[:500],
                    "commit": bool(s.get("commit")), "note": str(s.get("note") or "")[:300]})
    if not out:
        raise PlatformError("o procedimento precisa de passos")
    return out


def procedure_dict(p: Procedure) -> dict:
    return {"name": p.name, "title": p.title or p.name, "start_url": p.start_url, "domains": p.domains, "steps": p.steps,
            "version": p.version, "team": p.team_id, "created_by": p.created_by, "updated_at": iso(p.updated_at),
            "vars": sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", json.dumps(p.steps))))}


def _can(acc: Access, team_id: int | None) -> bool:
    return acc.p.is_admin or (team_id is not None and RANK.get(acc.teams.get(team_id, ""), 0) >= RANK["developer"])


def list_procedures(db, acc: Access) -> list[dict]:
    return [procedure_dict(p) for p in db.scalars(select(Procedure).order_by(Procedure.name))]


def save_procedure(db, acc: Access, name: str, title: str, domains: list[str], steps, team: str = "", start_url: str = "") -> dict:
    from ..models import Team
    team_id = None
    if team:
        t = db.scalar(select(Team).where(Team.slug == team))
        if t is None:
            raise PlatformError(f"time '{team}' não encontrado")
        team_id = t.id
    key = slugify(name or title).replace("-", "_")[:80]
    p = db.scalar(select(Procedure).where(Procedure.name == key))
    if not _can(acc, team_id if p is None else p.team_id):
        raise Forbidden("só quem desenvolve no time escreve procedimentos")
    doms = sorted({d.lower().strip() for d in domains or [] if d.strip()})
    if not doms:
        raise PlatformError("diga em que domínios o procedimento navega")
    clean = _clean_steps(steps)
    if p is None:
        p = Procedure(name=key, team_id=team_id, created_by=acc.p.name, version=0)
        db.add(p)
    p.version += 1 if (p.steps != clean or p.domains != doms) else 0
    p.title, p.domains, p.steps, p.start_url, p.updated_at = (title or key)[:200], doms, clean, (start_url or "")[:1000], now()
    db.commit()
    audit(db, acc.p.name, "procedure.save", key, f"v{p.version}")
    return procedure_dict(p)


def from_task(db, acc: Access, task_id: int, name: str, title: str = "", team: str = "") -> dict:
    """O caminho que deu certo numa tarefa vira procedimento (os segredos continuam como secret://)."""
    from ..models import TaskEvent
    from .task_thread import _task
    t, _, _ = _task(db, acc, task_id)
    steps, doms = [], set()
    for ev in db.scalars(select(TaskEvent).where(TaskEvent.task_id == t.id, TaskEvent.kind == "browser").order_by(TaskEvent.id)):
        p = ev.payload or {}
        doms |= set(p.get("domains") or [])
        if p.get("action") in STEP_ACTIONS:
            steps.append({"action": p["action"], "target": p.get("target", ""),
                          "value": "" if p.get("value") == "[segredo]" else p.get("value", "")})
    if not steps:
        raise PlatformError("a tarefa não usou o navegador")
    return save_procedure(db, acc, name, title, sorted(doms), steps, team)


@tool("run_procedure", "Executa um procedimento de navegador da empresa (passo a passo de um sistema sem API), com os "
      "valores das variáveis {{assim}}. Cliques que confirmam algo passam pela sua alçada.",
      {"properties": {"name": {"type": "string"}, "values": {"type": "object"}}, "required": ["name"]}, "browser", _avail)
def run_procedure(ctx: Ctx, args: dict):
    p = ctx.db.scalar(select(Procedure).where(Procedure.name == slugify(str(args.get("name") or "")).replace("-", "_")))
    if p is None:
        raise PlatformError(f"procedimento '{args.get('name')}' não existe")
    values = args.get("values") or {}
    allowed = _domains(ctx, p.domains)
    tasksmod.event(ctx.db, ctx.task, "browser", {"action": "procedure", "target": p.name, "version": p.version, "domains": p.domains})
    ctx.db.commit()

    def fill_vars(s: str) -> str:
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(values.get(m.group(1), m.group(0))), s)
    done = []
    for i, s in enumerate(p.steps, start=1):
        target, value = fill_vars(s["target"]), fill_vars(s["value"])
        missing = re.findall(r"\{\{\s*(\w+)", target + value)
        if missing:
            raise PlatformError(f"passo {i}: faltam valores para {', '.join(missing)}")
        try:
            if s["action"] == "open":
                out = browser_open(ctx, {"url": target})
            elif s["action"] == "fill":
                out = browser_fill(ctx, {"target": target, "value": value})
            elif s["action"] == "select":
                out = browser_select(ctx, {"target": target, "value": value})
            elif s["action"] == "click":
                out = browser_click(ctx, {"target": target, "commit": s.get("commit")})
            elif s["action"] == "screenshot":
                out = browser_screenshot(ctx, {})
            else:
                out = browser_read(ctx, {})
        except PlatformError as e:
            shot = _shot(ctx, allowed, f"falha no passo {i}")
            raise PlatformError(f"{p.name} v{p.version}, passo {i} ({s['action']} {target}): {e}"
                                f"{f' — tela salva: {shot}' if shot else ''}") from None
        if isinstance(out, dict) and out.get("status") in ("waiting", "denied", "simulated"):
            return out  # a alçada (ou uma pessoa, no CAPTCHA) segura o resto
        done.append(f"{i}. {s['action']} {target}")
    final = browser_read(ctx, {"max_chars": 3000})
    return {"procedure": p.name, "version": p.version, "steps": done, "page": final}


def set_domains(db, acc: Access, slug: str, domains: list[str]) -> dict:
    from .employees import find
    e, a = find(db, acc, slug, "manage")
    e.browser_domains = sorted({d.lower().strip() for d in domains if d.strip()})
    db.commit()
    audit(db, acc.p.name, "employee.browser_domains", a.slug, ", ".join(e.browser_domains))
    return {"domains": e.browser_domains, "ready": ready()}

