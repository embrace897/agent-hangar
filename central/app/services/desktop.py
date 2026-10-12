"""Desktop governado (experimental): o Digital employee opera aplicativos de desktop sem API nem página web — ERP
legado, planilha, sistema instalado — num desktop Linux isolado (perfil `desktop`, a partir da Cua), olhando a tela.

- **Apps liberados:** o funcionário só abre e opera os apps do cargo (`desktop_apps`).
- **O modelo vê a tela:** `desktop_look` e cada ação devolvem a captura da janela (vai ao modelo como imagem) e os
  elementos de acessibilidade com número; sem acessibilidade (Tk, Citrix, tela pintada), clica por x,y na captura.
- **Cliques que confirmam algo** (salvar, confirmar, enviar, pagar…) passam pela alçada com a captura no pedido, como
  no navegador: pagar/transferir é `financial`, enviar é `send_external`, o resto é `write_internal`.
- **Segredos:** `text: "secret://nome"` digita o valor do cofre — o modelo nunca vê, a linha do tempo registra
  "[segredo]".
"""
import base64
import json
import re
import time

import httpx

from .. import config, tenancy
from . import employee_tasks as tasksmod
from . import task_files
from .access import Access
from .browser import PAY, SEND
from .common import PlatformError, audit
from .platform_tools import Ctx, side_effect, tool

HTTP = lambda timeout=120: httpx.Client(timeout=timeout)  # noqa: E731  (os testes trocam)
_UP: tuple[float, bool] = (0.0, False)
COMMIT = re.compile(r"\b(salvar|confirmar|concluir|finalizar|aprovar|assinar|gravar|emitir|cadastrar|excluir|apagar|"
                    r"lan[çc]ar|baixar|baixa|registrar|save|confirm|approve|sign|delete|submit|ok)\b", re.I)


def ready() -> bool:
    global _UP
    if not config.DESKTOP_URL:
        return False
    ts, ok = _UP
    if time.monotonic() - ts < 60:
        return ok
    try:
        with HTTP(5) as h:
            ok = h.get(config.DESKTOP_URL.rsplit("/mcp", 1)[0] + "/health").status_code == 200
    except httpx.HTTPError:
        ok = False
    _UP = (time.monotonic(), ok)
    return ok


def _avail(db, e) -> bool:
    return ready() and bool(e.desktop_apps)


def call(task, name: str, args: dict) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    try:
        with HTTP() as h:
            r = h.post(config.DESKTOP_URL, json=body, headers={
                "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                "x-session-id": f"hangar-{tenancy.current_org()}-task-{task.id}"})
    except httpx.HTTPError as e:
        raise PlatformError(f"desktop indisponível: {e}") from None
    if r.status_code >= 400:
        raise PlatformError(f"desktop respondeu {r.status_code}")
    data = r.json()
    if data.get("error"):
        raise PlatformError(f"desktop: {data['error'].get('message')}")
    res = data.get("result") or {}
    text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    if res.get("isError"):
        raise PlatformError(re.sub(r"^Error executing tool \w+: ", "", text)[:500] or "erro no desktop")
    sc = res.get("structuredContent")
    if isinstance(sc, dict):
        return sc["result"] if set(sc) == {"result"} else sc
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def _log(ctx: Ctx, action: str, target: str = "", value: str = "", window: str = ""):
    tasksmod.event(ctx.db, ctx.task, "desktop", {"action": action, "target": target[:200], "value": value[:200], "window": window[:200]})


def _out(st: dict) -> dict:
    """O que volta ao modelo: o texto e a captura como imagem (o runtime manda a imagem ao modelo)."""
    shot = st.pop("screenshot", "") or ""
    out = {k: st[k] for k in ("ok", "detail", "window", "app", "size", "elements", "dica", "apps") if k in st}
    if shot:
        out["_images"] = [["image/png", shot]]
    return out


def _evidence(ctx: Ctx, st: dict, why: str) -> str:
    shot = st.get("screenshot") or ""
    if not shot:
        return ""
    f = task_files.store(ctx.db, ctx.task, f"tela-{int(time.time() * 1000)}.png", base64.b64decode(shot), "output",
                         f"agent:{ctx.agent.slug}", "image/png", why[:200])
    ctx.db.commit()
    return f.name


@tool("desktop_open", "Abre um aplicativo de desktop liberado para você (ERP legado, planilha…) e devolve a tela: a "
      "captura da janela e os elementos clicáveis com número. Se o app já estiver aberto, volta à janela dele (com o que "
      "já foi preenchido).",
      {"properties": {"app": {"type": "string", "description": "nome do app, ex.: ERP Legado"}}, "required": ["app"]},
      "desktop", _avail)
def desktop_open(ctx: Ctx, args: dict):
    app = str(args.get("app") or "")
    st = call(ctx.task, "open", {"app": app, "allowed": ctx.e.desktop_apps or []})
    _log(ctx, "open", app, window=st.get("window", ""))
    ctx.db.commit()
    return _out(st)


@tool("desktop_look", "Olha de novo a janela do app da tarefa: captura e elementos (com número para clicar).",
      {"properties": {"window": {"type": "string", "description": "parte do título, se houver mais de uma janela"}}},
      "desktop", _avail)
def desktop_look(ctx: Ctx, args: dict):
    return _out(call(ctx.task, "look", {"allowed": ctx.e.desktop_apps or [], "window": str(args.get("window") or "")}))


def _commit_ref(label: str, commit: bool) -> str | None:
    if PAY.search(label):
        return "platform:desktop_pay"
    if SEND.search(label):
        return "platform:desktop_send"
    if commit or COMMIT.search(label):
        return "platform:desktop_commit"
    return None


@tool("desktop_click", "Clica na janela do app: pelo número do elemento (element) ou, sem acessibilidade, por x,y em "
      "pixels da última captura. Diga em label o que está clicando (o texto do botão). Clique que confirma algo — salvar, "
      "confirmar, lançar, enviar, pagar — passa pela sua alçada com a captura; marque commit=true quando o botão "
      "confirmar algo e o texto não deixar claro.",
      {"properties": {"label": {"type": "string", "description": "o texto do botão/campo clicado"},
                      "element": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
                      "double": {"type": "boolean"}, "commit": {"type": "boolean"}},
       "required": ["label"]}, "desktop", _avail)
def desktop_click(ctx: Ctx, args: dict):
    allowed = ctx.e.desktop_apps or []
    label = str(args.get("label") or "")
    where = {k: args[k] for k in ("element", "x", "y") if args.get(k) not in (None, "")}
    ref = _commit_ref(label, bool(args.get("commit")))
    if ref:
        before = call(ctx.task, "look", {"allowed": allowed})
        shot = _evidence(ctx, before, f"antes de clicar em '{label}'")
        why = (f"{ctx.rationale}\nTela: {shot}\nJanela: {before.get('window', '')}").strip()
        # a alçada libera o clique exato — rótulo, janela e onde —, e a chamada aprovada se repete igual
        held = side_effect(Ctx(ctx.db, ctx.e, ctx.agent, ctx.task, why), "desktop_click", ref,
                           {"label": label, "window": before.get("window", ""), **where})
        if held:
            return held
    st = call(ctx.task, "click", {"allowed": allowed, "double": bool(args.get("double")), **where})
    _log(ctx, "click", label, json.dumps(where), st.get("window", ""))
    ctx.db.commit()
    return _out(st)


@tool("desktop_type", "Digita texto na janela do app — no campo indicado (element ou x,y: clica nele antes) ou onde "
      "o cursor está. Para senhas use text=\"secret://nome\" (o cofre digita; você nunca vê o valor).",
      {"properties": {"text": {"type": "string"}, "label": {"type": "string", "description": "o campo"},
                      "element": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}},
       "required": ["text"]}, "desktop", _avail)
def desktop_type(ctx: Ctx, args: dict):
    text, shown = str(args.get("text") or ""), str(args.get("text") or "")
    if text.startswith("secret://"):
        from . import secrets_vault
        text = secrets_vault.resolve(text, ctx.agent.team_id, f"desktop:{ctx.agent.slug}", ctx.db)
        shown = "[segredo]"
    where = {k: args[k] for k in ("element", "x", "y") if args.get(k) not in (None, "")}
    st = call(ctx.task, "type_text", {"allowed": ctx.e.desktop_apps or [], "text": text, **where})
    _log(ctx, "type", str(args.get("label") or ""), shown if shown == "[segredo]" else shown[:60], st.get("window", ""))
    ctx.db.commit()
    return _out(st)


@tool("desktop_key", "Aperta uma tecla (Return, Tab, Escape, F2…) ou um atalho (modifiers: [\"ctrl\"] + key \"s\"). "
      "Atalho que salva ou confirma (ctrl+s, Return num diálogo de confirmação) passa pela alçada: marque commit=true.",
      {"properties": {"key": {"type": "string"}, "modifiers": {"type": "array", "items": {"type": "string"}},
                      "commit": {"type": "boolean"}}, "required": ["key"]}, "desktop", _avail)
def desktop_key(ctx: Ctx, args: dict):
    allowed = ctx.e.desktop_apps or []
    key, mods = str(args.get("key") or ""), [str(m) for m in args.get("modifiers") or []]
    combo = "+".join([*mods, key])
    if args.get("commit") or combo.lower() in ("ctrl+s", "ctrl+enter"):
        before = call(ctx.task, "look", {"allowed": allowed})
        shot = _evidence(ctx, before, f"antes de {combo}")
        why = (f"{ctx.rationale}\nTela: {shot}\nJanela: {before.get('window', '')}").strip()
        held = side_effect(Ctx(ctx.db, ctx.e, ctx.agent, ctx.task, why), "desktop_key", "platform:desktop_commit",
                           {"key": combo, "window": before.get("window", "")})
        if held:
            return held
    st = call(ctx.task, "key", {"allowed": allowed, "key": key, **({"modifiers": mods} if mods else {})})
    _log(ctx, "key", combo, window=st.get("window", ""))
    ctx.db.commit()
    return _out(st)


@tool("desktop_scroll", "Rola a janela do app (direction: up, down, left, right).",
      {"properties": {"direction": {"type": "string"}, "amount": {"type": "integer"}}}, "desktop", _avail)
def desktop_scroll(ctx: Ctx, args: dict):
    return _out(call(ctx.task, "scroll", {"allowed": ctx.e.desktop_apps or [], "direction": str(args.get("direction") or "down"),
                                          "amount": int(args.get("amount") or 3)}))


@tool("desktop_screenshot", "Salva a captura da janela atual na tarefa (como evidência).", {"properties": {}}, "desktop", _avail)
def desktop_screenshot(ctx: Ctx, args: dict):
    name = _evidence(ctx, call(ctx.task, "look", {"allowed": ctx.e.desktop_apps or []}), "captura pedida pelo funcionário")
    return f"Captura salva na tarefa: {name}" if name else "sem captura"


def set_apps(db, acc: Access, slug: str, apps: list[str]) -> dict:
    from .employees import find
    e, a = find(db, acc, slug, "manage")
    e.desktop_apps = sorted({x.strip() for x in apps if x and x.strip()}, key=str.lower)
    db.commit()
    audit(db, acc.p.name, "employee.desktop_apps", a.slug, ", ".join(e.desktop_apps))
    return {"apps": e.desktop_apps, "ready": ready()}
