"""Agente remoto (spec `remote`): o agente roda fora do hangar e fala AG-UI. Aqui fica a ponte.

- **Uma rodada** (`ask`): manda um RunAgentInput com a conversa e junta os eventos que voltam (texto, ferramentas,
  interrupt). É o que o playground, os agendamentos, os testes em stage e a ponte de protocolos usam (OpenAI, A2A, ACP
  e MCP chegam aqui por invoke.chat).
- **AG-UI de ponta a ponta** (`relay`): o app fala AG-UI com o gateway e o gateway repassa ao agente, evento por
  evento, sem traduzir.

O endereço passa pela mesma regra dos plugins: rede interna só com PLUGIN_ALLOW_PRIVATE=1. Os cabeçalhos aceitam
`secret://nome` (o valor sai do cofre do time do agente, e o uso fica registrado).
"""
import json
import time
import uuid
from urllib.parse import urlparse

import httpx

from . import remote_dlp, secrets_vault
from .common import PlatformError

HTTP = lambda timeout: httpx.Client(timeout=httpx.Timeout(timeout, connect=10))  # noqa: E731  (os testes trocam)
AHTTP = lambda timeout: httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10))  # noqa: E731
TEXT_LIMIT = 60000


def target(spec: dict, env: str) -> tuple[str, int]:
    rem = spec["remote"]
    over = rem.get(env) if isinstance(rem.get(env), dict) else {}
    return over.get("url") or rem["url"], int(rem.get("timeout_s") or 120)


def check_url(url: str):
    from .plugins import _check_host
    host = urlparse(url).hostname
    if not host:
        raise PlatformError(f"URL inválida: {url}")
    _check_host(host)


def headers_for(spec: dict, team_id: int | None, slug: str, db=None) -> dict:
    out = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    for k, v in (spec["remote"].get("headers") or {}).items():
        out[k] = secrets_vault.resolve(v, team_id, f"agente remoto {slug}", db) if str(v).startswith(
            secrets_vault.PREFIX) else v
    return out


def run_input(messages: list[dict], thread: str | None = None) -> dict:
    """OpenAI -> RunAgentInput (texto; o resto da conversa vai junto, na ordem)."""
    out = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            content = "\n".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")
        out.append({"id": f"m{len(out) + 1}", "role": role, "content": content or ""})
    return {"threadId": thread or f"hangar-{uuid.uuid4().hex[:12]}", "runId": uuid.uuid4().hex[:12], "messages": out,
            "tools": [], "context": [], "state": {}, "forwardedProps": {}}


def events(resp: httpx.Response):
    for line in resp.iter_lines():
        if line.startswith("data:"):
            d = line[5:].strip()
            if d and d != "[DONE]":
                try:
                    ev = json.loads(d)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    yield ev


def collect(evs) -> dict:
    """Os eventos de uma rodada -> resposta: o texto, as ferramentas que o agente usou, o raciocínio (contado), o
    interrupt (quando ele pede uma decisão) e o erro."""
    text, tools, trace, interrupts, error, kinds = "", {}, [], [], "", []
    for ev in evs:
        k = ev.get("type")
        kinds.append(k)
        if k == "TEXT_MESSAGE_CONTENT" or (k == "TEXT_MESSAGE_CHUNK" and ev.get("delta")):
            text += ev.get("delta") or ""
        elif k in ("TOOL_CALL_START", "TOOL_CALL_CHUNK") and ev.get("toolCallName"):
            tools[ev.get("toolCallId")] = {"tool": ev["toolCallName"], "args": "", "result": ""}
        elif k in ("TOOL_CALL_ARGS", "TOOL_CALL_CHUNK") and ev.get("toolCallId") in tools:
            tools[ev["toolCallId"]]["args"] += ev.get("delta") or ""
        elif k == "TOOL_CALL_RESULT" and ev.get("toolCallId") in tools:
            tools[ev["toolCallId"]]["result"] = str(ev.get("content", ""))[:300]
        elif k == "RUN_ERROR":
            error = ev.get("message") or "erro no agente remoto"
        elif k == "RUN_FINISHED" and (ev.get("outcome") or {}).get("type") == "interrupt":
            interrupts = ev["outcome"].get("interrupts") or []
    for t in tools.values():
        trace.append({"tool": f"remoto:{t['tool']}", "args": {"args": t["args"][:200]}, "result": t["result"]})
    status = "failed" if error else "interrupted" if interrupts else "passed" if "RUN_FINISHED" in kinds else "failed"
    if not error and "RUN_FINISHED" not in kinds:
        error = "o agente remoto encerrou sem RUN_FINISHED"
    reply = text[:TEXT_LIMIT]
    if interrupts:
        reply = (reply + "\n\n" if reply else "") + "O agente pediu uma decisão: " + "; ".join(
            i.get("message") or i.get("reason") or i.get("id", "") for i in interrupts)
    return {"reply": reply or (f"Erro no agente remoto: {error}" if error else ""), "status": status, "error": error,
            "trace": trace, "interrupts": interrupts, "events": len(kinds)}


def bridge(db, spec: dict) -> tuple[remote_dlp.Masker, remote_dlp.Stream]:
    """O DLP (o que mascarar, pela política da empresa) e o leitor dos eventos (valores reais de volta, uso somado)."""
    masker = remote_dlp.Masker(remote_dlp.kinds_for(db, spec["remote"]))
    return masker, remote_dlp.Stream(masker, spec["remote"].get("prices"))


def ask(db, agent, spec: dict, env: str, messages: list[dict], session: str | None = None) -> dict:
    """Uma rodada no agente remoto, de forma síncrona (playground, agendamentos, testes, ponte de protocolos)."""
    url, timeout = target(spec, env)
    check_url(url)
    hdr = headers_for(spec, agent.team_id, agent.slug, db)
    masker, st = bridge(db, spec)
    body = masker.run_input(run_input(messages, session))
    t0 = time.time()
    try:
        with HTTP(timeout) as http, http.stream("POST", url, headers=hdr, json=body) as r:
            if r.status_code >= 400:
                r.read()
                raise PlatformError(f"o agente remoto respondeu HTTP {r.status_code}: {r.text[:200]}")
            if not r.headers.get("content-type", "").startswith("text/event-stream"):
                r.read()
                raise PlatformError(f"o agente remoto não respondeu em AG-UI (SSE): {r.headers.get('content-type')}")
            out = collect(e for ev in events(r) for e in st.events(ev))
    except httpx.HTTPError as e:
        raise PlatformError(f"agente remoto indisponível: {e}") from None
    out["latency_ms"] = int((time.time() - t0) * 1000)
    out["usage"] = st.result() or {}
    return out


async def relay(url: str, hdr: dict, body: dict, timeout: int, st: "remote_dlp.Stream | None" = None):
    """AG-UI de ponta a ponta: repassa o RunAgentInput (já mascarado) e devolve os eventos do agente como chegam, com
    os valores reais no lugar das marcas do DLP e o uso somado em `st`. Erro de rede ou HTTP vira RUN_ERROR, para o app
    não ficar sem resposta."""
    def sse(ev):
        return "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
    try:
        async with AHTTP(timeout) as http, http.stream("POST", url, headers=hdr, json=body) as r:
            if r.status_code >= 400:
                await r.aread()
                if st:
                    st.failed = True
                yield sse({"type": "RUN_ERROR", "message": f"o agente remoto respondeu HTTP {r.status_code}",
                           "code": "remote_http"})
                return
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    for out in (st.line(line) if st else [line]):
                        yield out + "\n\n"
                elif line.startswith(":"):
                    yield ": keepalive\n\n"
    except httpx.HTTPError as e:
        if st:
            st.failed = True
        yield sse({"type": "RUN_ERROR", "message": f"agente remoto indisponível: {e}", "code": "remote_unavailable"})


def probe(db, agent, spec: dict, env: str) -> dict:
    """Teste de conexão (aba do agente): o endereço passa na regra de rede, os segredos existem no cofre e uma rodada
    AG-UI vai do começo ao fim. Não precisa de deploy: serve para configurar antes de testar em stage."""
    steps, reply = [], ""

    def step(name, fn):
        t0 = time.time()
        try:
            ok, detail = fn()
        except PlatformError as e:
            ok, detail = False, str(e)
        steps.append({"name": name, "ok": ok, "detail": str(detail)[:300], "ms": int((time.time() - t0) * 1000)})
        return ok

    url, _ = target(spec, env)
    if not step("endereço", lambda: (check_url(url), (True, url))[1]):
        return {"ok": False, "env": env, "steps": steps}
    if not step("cabeçalhos e cofre", lambda: (headers_for(spec, agent.team_id, agent.slug, db),
                                               (True, f"{len(spec['remote'].get('headers') or {})} cabeçalho(s)"))[1]):
        return {"ok": False, "env": env, "steps": steps}

    def run():
        nonlocal reply
        out = ask(db, agent, spec, env, [{"role": "user", "content": "Teste de conexão do Agent Hangar: responda ok."}])
        reply = out["reply"]
        ok = out["status"] in ("passed", "interrupted") and bool(reply.strip())
        return ok, (f"{out['events']} eventos, {out['latency_ms']} ms" if ok else out["error"] or "sem texto na resposta")
    ok = step("rodada AG-UI (RUN_STARTED … RUN_FINISHED)", run)
    return {"ok": ok, "env": env, "steps": steps, "reply": reply[:500]}
