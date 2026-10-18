"""Gateway dos agentes: /gw/<slug> (produção) e /gw-stage/<slug> para clientes externos, e /internal/gw
para agente -> sub-agente. Agentes de chat são proxiados para o container deles; agentes-com-harness
(sem container fixo) são atendidos aqui mesmo, disparando um job por chamada. No AG-UI (POST <slug>/agui), um
Digital employee recebe a conversa como tarefa, para passar pela alçada (ver services/agui.py)."""
import json
import re
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .. import config, deploy
from .. import services as svc
from ..db import SessionLocal
from ..models import Employee
from ..services import agui
from .internal import caller_agent, may_call

router = APIRouter()

FORWARD = ("x-session-id", "x-conversation-id", "x-user-id", "x-channel", "x-hangar-mode", "x-progress",
           # Open WebUI com ENABLE_FORWARD_USER_INFO_HEADERS=true: id do chat = sessão do agente
           "x-openwebui-chat-id", "x-openwebui-user-id")
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "content-length",
              "content-encoding", "proxy-authenticate", "proxy-authorization"}


def channel_of(request: Request) -> str:
    """Canal das métricas: X-Channel explícito; senão a ferramenta dona da chave (conexões criadas na aba
    "Conectar" do agente); senão "api"."""
    p = request.scope.get("state", {}).get("principal")
    return request.headers.get("x-channel") or (p.client if p is not None and p.client else "api")


def _is_tool_call(raw: bytes) -> bool:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return False
    items = body if isinstance(body, list) else [body]
    return any(isinstance(i, dict) and i.get("method") == "tools/call" for i in items)


def _sends_client_tools(raw: bytes) -> bool:
    if b'"tools"' not in raw:
        return False
    try:
        return bool((json.loads(raw) or {}).get("tools"))
    except ValueError:
        return False


def protocol_of(path: str) -> str:
    if path.startswith("v1/"):
        return "openai"
    if path.startswith(("a2a", ".well-known")):
        return "a2a"
    if path.startswith("acp"):
        return "acp"
    if path.startswith("mcp"):
        return "mcp"
    if path == "agui":
        return "agui"
    return "other"


def mcp_tool_name(slug: str) -> str:
    # mesma regra do runtime de chat (_safe em runtime/app.py): o nome da tool MCP é o slug nos dois casos
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug).strip("_-") or "agent"


def _record(agent_id, env, channel, proto, t0, ok, payload=None, user_id=None):
    with SessionLocal() as db:
        svc.record_usage(db, agent_id, env, channel, proto, int((time.time() - t0) * 1000), ok, payload,
                         user_id=user_id)


def user_of(request: Request) -> int | None:
    p = request.scope.get("state", {}).get("principal")
    return p.user_id if p is not None else None


# ------------------------------------------------------------------ agente-com-harness: ponte de protocolos
CARD_PATHS = (".well-known/agent.json", ".well-known/agent-card.json")


def _with_summary(content: bytes, summary: str) -> bytes:
    """Agent card do runtime com a descrição trocada pelo resumo do guia revisado (melhor descoberta via A2A)."""
    try:
        card = json.loads(content)
    except ValueError:
        return content
    if isinstance(card, dict):
        card["description"] = summary
        return json.dumps(card, ensure_ascii=False).encode()
    return content


def _card(info: dict, base: str) -> dict:
    skills = [s if isinstance(s, str) else s.get("name") for s in info["skills"]]
    return {"protocolVersion": "0.3.0", "name": info["name"], "description": info.get("summary") or info["objective"],
            "url": f"{base}/a2a", "preferredTransport": "JSONRPC", "version": str(info["version"]),
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
            "skills": [{"id": s, "name": s, "description": "", "tags": []} for s in skills if s]
            or [{"id": info["slug"], "name": info["name"], "description": info["final_output"], "tags": []}]}


async def _invoke(info, task, env, channel, proto="openai"):
    # sessão própria dentro da thread: não prende conexão do pool durante o job inteiro
    return await run_in_threadpool(svc.chat_new_session, info["slug"], task, env, channel, proto)


async def _harness_mcp(info: dict, request: Request, env: str, channel: str):
    """MCP para agente-com-harness (sem container fixo onde montar um FastMCP). Mesmo wire format do
    servidor MCP da plataforma: JSON puro, stateless."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "corpo inválido"}, 400)
    method, rid, tool = body.get("method"), body.get("id"), mcp_tool_name(info["slug"])
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": (body.get("params") or {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"experimental": {}, "prompts": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False}, "tools": {"listChanged": False}},
            "serverInfo": {"name": info["name"], "version": str(info["version"])},
            "instructions": f"Ferramenta do agente '{info['name']}' (harness) do Agent Hangar.\n"
                            f"Objetivo: {info['objective']}\nSaída esperada: {info['final_output']}"}}
    if method and method.startswith("notifications/"):
        return Response(status_code=202)
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [{
            "name": tool, "description": f"{info['objective']} Devolve: {info['final_output']}".strip(),
            "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}]}}
    if method == "tools/call":
        params = body.get("params") or {}
        if params.get("name") != tool:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"tool desconhecida: {params.get('name')}"}}
        try:
            r = await _invoke(info, (params.get("arguments") or {}).get("message", ""), env, channel, "mcp")
        except svc.PlatformError as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True}}
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": r["reply"]}], "isError": r.get("status") != "passed"}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"método não suportado: {method}"}}


async def _proxy_harness(info: dict, path: str, request: Request, env: str):
    base = f"{config.PUBLIC_BASE_URL}/{'gw' if env == 'prod' else 'gw-stage'}/{info['slug']}"
    channel = channel_of(request)
    if path in CARD_PATHS:
        return _card(info, base)
    if path == "mcp":
        return await _harness_mcp(info, request, env, channel)
    meta = {"framework": "agent-hangar", "version": info["version"]}
    if path == "acp/agents":
        return {"agents": [{"name": info["slug"], "description": info["objective"], "metadata": meta}]}
    if path == f"acp/agents/{info['slug']}":
        return {"name": info["slug"], "description": info["objective"], "metadata": meta}
    if path == "agui":
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "corpo inválido: envie um RunAgentInput (JSON)"}, 400)
        if info.get("remote"):
            return await _remote_agui(info, body, request, env, channel)
        return StreamingResponse(agui.harness_stream(lambda text: _invoke(info, text, env, channel, "agui"), body),
                                 media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    if path not in ("v1/chat/completions", "a2a", "acp/runs"):
        return JSONResponse({"error": f"caminho '{path}' não suportado para este agente (sem container)"}, 501)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "corpo inválido"}, 400)

    if path == "v1/chat/completions" and info.get("remote"):
        return await _remote_openai(info, body, request, env, channel)
    if path == "v1/chat/completions":
        task = next((m.get("content", "") for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
        try:
            r = await _invoke(info, task, env, channel)
        except svc.PlatformError as e:
            return JSONResponse({"error": {"message": str(e)}}, 502)
        return {"id": f"chatcmpl-job{r.get('job_id', '')}", "object": "chat.completion", "created": int(time.time()),
                "model": info["slug"], "choices": [{"index": 0, "finish_reason": "stop",
                                                   "message": {"role": "assistant", "content": r["reply"]}}],
                "usage": r["usage"], "x_trace": r["trace"], "x_job_id": r.get("job_id"), "x_diff": r.get("diff")}
    if path == "a2a":
        rid = body.get("id")
        if body.get("method") not in ("message/send", "SendMessage"):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
        task = "".join(p.get("text", "") for p in body.get("params", {}).get("message", {}).get("parts", []))
        try:
            r = await _invoke(info, task, env, channel, "a2a")
        except svc.PlatformError as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "kind": "message", "role": "agent", "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": r["reply"]}],
            "metadata": {"job_id": r.get("job_id"), "diff": r.get("diff"), "usage": r["usage"]}}}
    # acp/runs
    task = "\n".join(p.get("content", "") for m in body.get("input", []) for p in m.get("parts", []))
    try:
        r = await _invoke(info, task, env, channel, "acp")
    except svc.PlatformError as e:
        return JSONResponse({"status": "failed", "error": {"message": str(e)}}, 502)
    return {"run_id": str(uuid.uuid4()), "agent_name": info["slug"], "status": "completed",
            "output": [{"role": f"agent/{info['slug']}", "parts": [{"content_type": "text/plain", "content": r["reply"]}]}],
            "metadata": {"job_id": r.get("job_id"), "diff": r.get("diff"), "usage": r["usage"]}}


# ------------------------------------------------------------------ agente remoto (spec `remote`, AG-UI)
def _remote_target(info: dict, env: str, body: dict):
    """URL (conferida contra rede interna), cabeçalhos (com os segredos do cofre), tempo máximo, o RunAgentInput com o
    DLP da empresa aplicado e o leitor dos eventos (valores reais de volta, uso somado)."""
    from ..services import remote_agui
    spec = {"remote": info["remote"]}
    url, timeout = remote_agui.target(spec, env)
    remote_agui.check_url(url)
    with SessionLocal() as db:
        hdr = remote_agui.headers_for(spec, info["team_id"], info["slug"], db)
        masker, st = remote_agui.bridge(db, spec)
    return url, hdr, timeout, masker.run_input(body), st


def _record_remote(info: dict, env: str, channel: str, t0: float, uid, st):
    """Depois do stream: os tokens e o custo que o agente informou (zero se não informou) entram na medição."""
    u = st.result()
    _record(info["id"], env, channel, "agui", t0, not st.failed, {"usage": u} if u else None, uid)


async def _remote_agui(info: dict, body: dict, request: Request, env: str, channel: str):
    """AG-UI de ponta a ponta: o RunAgentInput vai ao agente (com o DLP da empresa) e os eventos voltam como chegam,
    com os valores reais. A chamada entra na medição com os tokens e o custo que o agente informar."""
    from ..services import remote_agui
    try:
        url, hdr, timeout, sent, st = await run_in_threadpool(_remote_target, info, env, body)
    except svc.PlatformError as e:
        return JSONResponse({"error": str(e)}, 400)
    uid, t0 = user_of(request), time.time()
    return StreamingResponse(remote_agui.relay(url, hdr, sent, timeout, st), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                             background=BackgroundTask(_record_remote, info, env, channel, t0, uid, st))


async def _remote_openai(info: dict, body: dict, request: Request, env: str, channel: str):
    """OpenAI-compatible para um agente remoto: a conversa inteira vai ao agente (não só a última mensagem); com
    `stream: true`, a resposta volta em SSE no formato OpenAI."""
    from ..services import remote_agui
    uid, t0 = user_of(request), time.time()

    def go():
        with SessionLocal() as db:
            a = svc.get_agent(db, info["slug"])
            return remote_agui.ask(db, a, svc.spec_of(a), env, body.get("messages") or [],
                                   request.headers.get("x-session-id") or request.headers.get("x-conversation-id"))
    try:
        out = await run_in_threadpool(go)
    except svc.PlatformError as e:
        await run_in_threadpool(_record, info["id"], env, channel, "openai", t0, False, None, uid)
        return JSONResponse({"error": {"message": str(e), "type": "remote_error"}}, 502)
    ok = out["status"] != "failed"
    await run_in_threadpool(_record, info["id"], env, channel, "openai", t0, ok,
                            {"usage": out["usage"]} if out.get("usage") else None, uid)
    cid, created = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
    if body.get("stream"):
        def chunk(delta, finish=None):
            return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created,
                                          "model": info["slug"], "choices": [{"index": 0, "delta": delta,
                                                                              "finish_reason": finish}]},
                                         ensure_ascii=False) + "\n\n"

        async def sse():
            yield chunk({"role": "assistant", "content": out["reply"]})
            yield chunk({}, "stop")
            yield "data: [DONE]\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream")
    return {"id": cid, "object": "chat.completion", "created": created, "model": info["slug"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": out["reply"]}}],
            "usage": out.get("usage") or {}, "x_trace": out["trace"], "x_status": out["status"]}


# ------------------------------------------------------------------ proxy
# `usage"` (sem a aspa de abertura) casa com {"usage": …} do OpenAI e com "name": "hangar.usage" do AG-UI
USAGE_MARK = b'usage"'


def usage_of(line: bytes) -> dict | None:
    """Tokens e custo de uma linha SSE. OpenAI: {"usage": …}; AG-UI: {"type": "CUSTOM", "name": "hangar.usage",
    "value": …}."""
    if not line.startswith(b"data: {") or USAGE_MARK not in line:
        return None
    try:
        ev = json.loads(line[6:])
    except ValueError:
        return None
    if not isinstance(ev, dict):
        return None
    return (ev.get("value") if ev.get("name") == "hangar.usage" else ev.get("usage")) or None


async def _proxy(request: Request, slug: str, path: str, env: str):
    with SessionLocal() as db:
        a = svc.find_agent(db, slug)
        if not a:
            return JSONResponse({"error": f"agente '{slug}' não existe"}, 404)
        svc.refresh_deployments(db, [a])
        if not svc.active_deployment(a, env):
            return JSONResponse({"error": f"'{slug}' não está rodando em {env}"}, 503)
        if svc.org.budget_blocked(db, a.team_id):
            return JSONResponse({"error": "orçamento mensal do time esgotado — fale com o mantenedor do time"}, 429)
        stop = svc.budgets.stopped(db, a)
        if stop:
            return JSONResponse({"error": f"orçamento parado ({stop['scope']}, US$ {stop['amount_usd']:g} por "
                                          f"{'dia' if stop['window'] == 'day' else 'mês'}) — peça para liberar"}, 429)
        spec = svc.spec_of(a)
        info = {"slug": a.slug, "name": a.name, "objective": a.objective, "final_output": a.final_output,
                "version": a.current_version, "skills": spec.get("skills", []), "id": a.id,
                "summary": svc.guides.card_summary(db, a) if path in CARD_PATHS else ""}
        is_harness = bool(spec.get("harness") or spec.get("remote"))  # sem container: a central atende
        info.update(remote=spec.get("remote"), team_id=a.team_id)
        code_ok, code_msg = svc.code_allowed(db, spec, env)
        employee = agui.employee_of(db, a.id) if path == "agui" else None
    if not code_ok and protocol_of(path) in ("openai", "agui") and _sends_client_tools(await request.body()):
        # modo agente de código (VS Code, Cline…): o cliente mandaria arquivos e saídas de terminal para esse LLM
        return JSONResponse({"error": {"message": code_msg, "type": "code_policy"}}, 403)
    # sessão já fechada aqui: nada abaixo segura conexão do pool enquanto espera o agente
    if is_harness:
        return await _proxy_harness(info, path, request, env)
    if employee is not None and request.scope.get("state", {}).get("principal") is not None:
        return await _agui_employee(request, info, employee.id, env)

    channel = channel_of(request)
    uid = user_of(request)
    proto = protocol_of(path)
    fwd = {"content-type": request.headers.get("content-type", "application/json"),
           "accept": request.headers.get("accept", "application/json, text/event-stream")}
    # identificação da conversa/usuário do cliente (ex.: LibreChat) — o agente repassa aos MCPs como sessão
    fwd.update({h: request.headers[h] for h in FORWARD if h in request.headers})
    fwd["x-channel"] = channel  # o agente usa o canal (ex.: escapar "$" para renderizadores LaTeX)
    t0 = time.time()
    # read=900: um agente com várias tools pode demorar; em streaming o runtime manda keepalive a cada 10s
    client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10))
    try:
        await run_in_threadpool(deploy.ensure_up, slug, env)  # stage hibernado: acorda antes
        url = f"{deploy.internal_url(slug, env)}/{path}" + (f"?{request.url.query}" if request.url.query else "")
        raw = await request.body()
        # MCP: handshake, tools/list, ping e notificações não são "uso" — só tools/call entra nas métricas
        if proto == "mcp" and not _is_tool_call(raw):
            proto = "other"
        req = client.build_request(request.method, url, content=raw, headers=fwd)
        upstream = await client.send(req, stream=True)
    except Exception as e:
        await client.aclose()
        if proto != "other":
            _record(info["id"], env, channel, proto, t0, False, user_id=uid)
        return JSONResponse({"error": f"agente indisponível: {e}"}, 502)
    # CORS é só do gateway (spec.cors): um container não libera origem por conta própria
    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP and not k.lower().startswith("access-control-")}
    ok = upstream.status_code < 400

    if upstream.headers.get("content-type", "").startswith("text/event-stream"):
        seen = {"usage": None}

        async def relay():
            """Repassa o stream intacto e, de passagem, guarda o último bloco `usage` (tokens/custo) dos eventos."""
            tail = b""
            async for chunk in upstream.aiter_bytes():
                yield chunk
                tail = (tail + chunk)[-65536:]
                if USAGE_MARK in chunk or USAGE_MARK in tail[-len(chunk) - 16:]:
                    for line in tail.split(b"\n"):
                        seen["usage"] = usage_of(line) or seen["usage"]

        async def done():
            await upstream.aclose()
            await client.aclose()
            if proto != "other":
                await run_in_threadpool(_record, info["id"], env, channel, proto, t0, ok,
                                        {"usage": seen["usage"]} if seen["usage"] else None, uid)
        # aiter_bytes (já descomprimido) combina com o content-encoding removido dos headers
        return StreamingResponse(relay(), status_code=upstream.status_code, headers=headers,
                                 background=BackgroundTask(done))

    content = await upstream.aread()
    await upstream.aclose()
    await client.aclose()
    if path in CARD_PATHS and ok and info["summary"]:
        content = _with_summary(content, info["summary"])
        headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}
    if proto != "other" and not path.startswith(".well-known"):
        try:
            payload = httpx.Response(200, content=content).json()
        except Exception:
            payload = None
        await run_in_threadpool(_record, info["id"], env, channel, proto, t0, ok, payload, uid)
    return Response(content, upstream.status_code, headers=headers)


async def _agui_employee(request: Request, info: dict, employee_id: int, env: str):
    """AG-UI de um Digital employee: a conversa vira tarefa (ou a rodada retoma a tarefa de um interrupt)."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return JSONResponse({"error": "corpo inválido: envie um RunAgentInput (JSON)"}, 400)
    channel, uid, t0 = channel_of(request), user_of(request), time.time()

    def start():
        with SessionLocal() as db:
            acc = svc.access.of(db, request.scope["state"]["principal"])
            return agui.start_or_resume(db, acc, db.get(Employee, employee_id), info["slug"], body)
    try:
        task_id, after, _resumed = await run_in_threadpool(start)
    except (svc.PlatformError, svc.access.Forbidden) as e:
        return JSONResponse({"error": str(e)}, 403 if isinstance(e, svc.access.Forbidden) else 400)
    await run_in_threadpool(_record, info["id"], env, channel, "agui", t0, True, None, uid)
    thread, run = agui.ids(body)
    return StreamingResponse(agui.employee_stream(task_id, after, thread, run), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.api_route("/gw/{slug}/{path:path}", methods=["GET", "POST"])
async def gw_prod(request: Request, slug: str, path: str):
    return await _proxy(request, slug, path, "prod")


@router.api_route("/gw-stage/{slug}/{path:path}", methods=["GET", "POST"])
async def gw_stage(request: Request, slug: str, path: str):
    return await _proxy(request, slug, path, "stage")


def _check_internal(request: Request, target: str):
    """Agente -> sub-agente: o token é do próprio agente (HMAC do slug) e o destino tem de estar nos
    sub_agents dele. Um agente comprometido não chama agentes que não são seus sub-agentes."""
    with SessionLocal() as db:
        caller = caller_agent(request, db)
        if not may_call(caller, target):
            raise HTTPException(403, f"'{caller.slug}' não tem '{target}' como sub_agent")


@router.api_route("/internal/gw/{slug}/{path:path}", methods=["GET", "POST"])
async def internal_gw_prod(request: Request, slug: str, path: str):
    await run_in_threadpool(_check_internal, request, slug)
    return await _proxy(request, slug, path, "prod")


@router.api_route("/internal/gw-stage/{slug}/{path:path}", methods=["GET", "POST"])
async def internal_gw_stage(request: Request, slug: str, path: str):
    await run_in_threadpool(_check_internal, request, slug)
    return await _proxy(request, slug, path, "stage")
