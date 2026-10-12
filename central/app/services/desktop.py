"""Desktop governado: o Digital employee opera aplicativos de desktop sem API nem página web — ERP
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
from .platform_tools import Ctx, tool

HTTP = lambda timeout=120: httpx.Client(timeout=timeout)  # noqa: E731  (os testes trocam)
_UP: tuple[float, bool] = (0.0, False)
COMMIT = re.compile(r"\b(salvar|confirmar|concluir|finalizar|aprovar|assinar|gravar|emitir|cadastrar|excluir|apagar|"
                    r"lan[çc]ar|baixar|baixa|registrar|save|confirm|approve|sign|delete|submit|ok|"
                    r"sim|yes)\b", re.I)  # "Sim" num diálogo de confirmação é a confirmação


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


def _log(ctx: Ctx, action: str, target: str = "", value: str = "", window: str = "", **extra):
    tasksmod.event(ctx.db, ctx.task, "desktop", {"action": action, "target": target[:200], "value": value[:200],
                                                  "window": window[:200], **extra})


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


DIALOG = re.compile(r"confirma|salvar|gravar|save|confirm", re.I)  # título de diálogo onde Return confirma
OPENS = re.compile(r"(\bcomo\b|\bas\b|\.\.\.|…)\s*$", re.I)  # "Salvar como…", "Save As": só abrem um diálogo


def _commit_ref(label: str, commit: bool) -> str | None:
    if OPENS.search(label.strip()) and not commit:
        return None  # a confirmação é o botão do diálogo que abre, e esse passa pela alçada
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
                      "double": {"type": "boolean"}, "commit": {"type": "boolean"},
                      "repeat": {"type": "boolean", "description": "true só para confirmar de novo algo idêntico a uma "
                                                                    "confirmação já feita nesta tarefa, de propósito"}},
       "required": ["label"]}, "desktop", _avail)
def desktop_click(ctx: Ctx, args: dict):
    allowed = ctx.e.desktop_apps or []
    label = str(args.get("label") or "")
    where = {k: args[k] for k in ("element", "x", "y") if args.get(k) not in (None, "")}
    ref = _commit_ref(label, bool(args.get("commit")))
    approved, covered, where_win = None, None, ""
    if ref:
        before = call(ctx.task, "look", {"allowed": allowed})
        where_win = before.get("window", "")
        covered = _covered_dialog(ctx, label, where_win) or _retry_of(ctx, where_win)
        if covered is None:
            done, fields = _confirmations(ctx)
            same = [d for d in done if d["label"].lower() == label.lower() and fields and d["fields"] == fields]
            if same and not args.get("repeat"):  # parece repetir uma confirmação desta tarefa: confira antes
                return ("NÃO CONFIRMEI: os campos preenchidos agora (" + _fmt(fields) + f") são os mesmos de '{label}' "
                        f"que já foi confirmado nesta tarefa (aprovação #{same[-1]['approved']}). Olhe a tela e o "
                        "registro do que você já fez: se já está feito, siga para o próximo item. Se for mesmo para "
                        "confirmar de novo, chame desktop_click com repeat=true e diga por quê.")
            shot = _evidence(ctx, before, f"antes de clicar em '{label}'")
            why = (f"{ctx.rationale}\nTela: {shot}\nJanela: {where_win}\nOnde: {json.dumps(where)}"
                   + (f"\nVai confirmar: {_fmt(fields)}" if fields else "")
                   + ("\nJá confirmado nesta tarefa: " + "; ".join(f"#{d['approved']} {d['label']} ({_fmt(d['fields'])})"
                                                               for d in done) if done else "")
                   + ("\nATENÇÃO: o funcionário diz que é uma REPETIÇÃO de propósito." if same else "")).strip()
            # a alçada libera a AÇÃO (rótulo e janela); o clique acontece onde a pessoa aprovou, na tela que ela viu —
            # o modelo errar uns pixels ao repetir a chamada não vira outro pedido
            g = tasksmod._gate(ctx.db, ctx.agent, ctx.task.id, "desktop_click", ref, {"label": label, "window": where_win},
                               why, "tool")
            if g.get("status") != "allowed":
                if g.get("request_id"):
                    _log(ctx, "pending", label, json.dumps(where), where_win, request=g["request_id"], where=where)
                    ctx.db.commit()
                return g
            approved = g.get("request_id")
            where = _approved_where(ctx, approved) or where
    st = call(ctx.task, "click", {"allowed": allowed, "double": bool(args.get("double")), **where})
    _log(ctx, "click", label, json.dumps(where), where_win or st.get("window", ""), after=st.get("window", ""),
         **({"approved": approved} if approved else {}), **({"covered_by": covered} if covered else {}))
    ctx.db.commit()
    return _out(st)


def _fmt(fields: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in fields.items()) or "—"


def _confirmations(ctx: Ctx) -> tuple[list[dict], dict]:
    """As confirmações já feitas nesta tarefa, cada uma com os campos digitados antes dela, e os campos digitados
    desde a última — para mostrar na aprovação e pegar uma confirmação repetida."""
    from sqlalchemy import select

    from ..models import TaskEvent
    done, fields = [], {}
    for ev in ctx.db.scalars(select(TaskEvent).where(TaskEvent.task_id == ctx.task.id, TaskEvent.kind == "desktop")
                             .order_by(TaskEvent.id)):
        p = ev.payload or {}
        if p.get("action") == "type" and p.get("target"):
            key = re.sub(r"\s*\(.*?\)|:", "", p["target"]).strip().lower()
            fields[key] = re.sub(r"\s+", "", str(p.get("value") or ""))
        elif p.get("action") == "click" and p.get("approved"):
            done.append({"label": p.get("target") or "", "approved": p["approved"], "fields": dict(fields)})
            fields = {}
        elif p.get("action") == "click" and p.get("covered_by"):
            fields = {}
    return done, fields


def _approved_where(ctx: Ctx, request_id: int | None) -> dict | None:
    """Onde estava o clique que a pessoa aprovou (gravado quando o pedido foi aberto)."""
    if not request_id:
        return None
    from sqlalchemy import select

    from ..models import TaskEvent
    for ev in ctx.db.scalars(select(TaskEvent).where(TaskEvent.task_id == ctx.task.id, TaskEvent.kind == "desktop")
                             .order_by(TaskEvent.id.desc())):
        p = ev.payload or {}
        if p.get("action") == "pending" and p.get("request") == request_id:
            return p.get("where") or None
    return None


CONFIRM = re.compile(r"^\s*(sim|yes|ok|confirmar|confirma)\s*[.!]?\s*$", re.I)


OPEN_KEYS = {"ctrl+shift+s", "ctrl+o", "ctrl+p", "ctrl+n", "ctrl+f", "ctrl+h", "f10", "f12"}


def _commit_gate(ctx: Ctx, before: dict, tool_name: str, payload: dict, extra: str) -> tuple[dict | None, int | None]:
    """Pede a alçada para uma tecla ou digitação que confirma: (resposta que segura, ou None; o pedido aprovado)."""
    win = before.get("window", "")
    shot = _evidence(ctx, before, f"antes de {payload.get('key') or 'digitar com Enter'}")
    why = "\n".join(x for x in (ctx.rationale, f"Tela: {shot}", f"Janela: {win}", extra) if x).strip()
    g = tasksmod._gate(ctx.db, ctx.agent, ctx.task.id, tool_name, "platform:desktop_commit", payload, why, "tool")
    return (None, g.get("request_id")) if g.get("status") == "allowed" else (g, None)


def _retry_of(ctx: Ctx, window: str) -> int | None:
    """Repetir uma confirmação aprovada que não teve efeito (o mesmo diálogo continua aberto e nada foi digitado
    desde então) é a mesma ação: a aprovação vale para a nova tentativa. Depois de um lançamento de verdade a tela
    muda — e um lançamento novo exige digitar de novo, o que volta a pedir a alçada."""
    from datetime import timedelta

    from sqlalchemy import select

    from ..models import TaskEvent, now
    if not DIALOG.search(window or ""):
        return None  # só num diálogo de salvar/confirmar: na tela de um sistema, clicar de novo pode lançar de novo
    for ev in ctx.db.scalars(select(TaskEvent).where(TaskEvent.task_id == ctx.task.id, TaskEvent.kind == "desktop")
                             .order_by(TaskEvent.id.desc()).limit(30)):
        p = ev.payload or {}
        if p.get("action") == "type" and not (p.get("approved") or p.get("covered_by")):
            return None  # digitou depois: é outra coisa
        if p.get("approved") or p.get("covered_by"):
            ok = (p.get("window") == window and p.get("after") == p.get("window")
                  and tasksmod._aware(ev.at) >= now() - timedelta(minutes=3))
            return (p.get("approved") or p.get("covered_by")) if ok else None
    return None


def _covered_dialog(ctx: Ctx, label: str, window: str) -> int | None:
    """O "Sim" do diálogo "Confirma?" que aparece logo depois de um clique aprovado faz parte da mesma ação: a
    aprovação já vale para ele (sem pedir de novo). Só um "Sim/OK" seco, numa janela diferente da do clique aprovado
    (o diálogo), até 3 minutos depois, e uma vez só."""
    from datetime import timedelta

    from sqlalchemy import select

    from ..models import TaskEvent, now
    if not CONFIRM.match(label):
        return None
    ev = ctx.db.scalar(select(TaskEvent).where(TaskEvent.task_id == ctx.task.id, TaskEvent.kind == "desktop")
                       .order_by(TaskEvent.id.desc()))
    p = (ev.payload or {}) if ev else {}
    if (ev is None or p.get("action") != "click" or not p.get("approved") or p.get("window") == window
            or tasksmod._aware(ev.at) < now() - timedelta(minutes=3)):
        return None
    return p["approved"]


@tool("desktop_type", "Digita texto na janela do app — no campo indicado (element ou x,y: clica nele antes) ou onde "
      "o cursor está. Para preencher várias células ou campos de uma vez, use \\t (Tab, próxima coluna/campo) e \\n "
      "(Enter, próxima linha) no texto — ex.: \"Item\\tValor\\nHotel\\t400\\n\" — em vez de uma chamada por célula. Um \\n "
      "num diálogo de salvar/confirmar confirma e passa pela sua alçada. Para senhas use text=\"secret://nome\" (o cofre "
      "digita; você nunca vê o valor).",
      {"properties": {"text": {"type": "string"}, "label": {"type": "string", "description": "o campo"},
                      "element": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}},
       "required": ["text"]}, "desktop", _avail)
def desktop_type(ctx: Ctx, args: dict):
    allowed = ctx.e.desktop_apps or []
    text, shown = str(args.get("text") or ""), str(args.get("text") or "")
    if text.startswith("secret://"):
        from . import secrets_vault
        text = secrets_vault.resolve(text, ctx.agent.team_id, f"desktop:{ctx.agent.slug}", ctx.db)
        shown = "[segredo]"
    where = {k: args[k] for k in ("element", "x", "y") if args.get(k) not in (None, "")}
    covered, approved, win = None, None, ""
    if "\n" in text or "\r" in text:  # um Enter digitado num diálogo de salvar/confirmar é a confirmação
        before = call(ctx.task, "look", {"allowed": allowed})
        win = before.get("window", "")
        if COMMIT.search(win) or PAY.search(win) or SEND.search(win) or DIALOG.search(win):
            covered = _covered_dialog(ctx, "ok", win)
            if covered is None:
                held, approved = _commit_gate(ctx, before, "desktop_type", {"text": shown[:200], "window": win},
                                              f"Texto: {shown[:200]}")
                if held:
                    return held
    st = call(ctx.task, "type_text", {"allowed": allowed, "text": text, **where})
    _log(ctx, "type", str(args.get("label") or ""), shown if shown == "[segredo]" else shown[:60], win or st.get("window", ""),
         after=st.get("window", ""), **({"approved": approved} if approved else {}),
         **({"covered_by": covered} if covered else {}))
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
    enter = not mods and key.lower() in ("return", "enter", "kp_enter")
    opens = combo.lower() in OPEN_KEYS or bool(re.fullmatch(r"alt\+\w", combo.lower()))  # só abrem diálogo ou menu
    covered, approved, win = None, None, ""
    if not opens and (args.get("commit") or combo.lower() in ("ctrl+s", "ctrl+enter") or enter):
        before = call(ctx.task, "look", {"allowed": allowed})
        win = before.get("window", "")
        # Return só confirma num diálogo de salvar/confirmar ("Save the current workbook as", "Confirmação"); numa
        # planilha ou num formulário ele só passa de linha
        commit = bool(args.get("commit")) or not enter or bool(COMMIT.search(win) or PAY.search(win) or SEND.search(win)
                                                                   or DIALOG.search(win))
        if commit:
            covered = (_covered_dialog(ctx, "ok", win) if enter else None) or _retry_of(ctx, win)
        if commit and covered is None:
            held, approved = _commit_gate(ctx, before, "desktop_key", {"key": combo, "window": win}, "")
            if held:
                return held
    st = call(ctx.task, "key", {"allowed": allowed, "key": key, **({"modifiers": mods} if mods else {})})
    _log(ctx, "key", combo, window=win or st.get("window", ""), after=st.get("window", ""),
         **({"approved": approved} if approved else {}), **({"covered_by": covered} if covered else {}))
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
