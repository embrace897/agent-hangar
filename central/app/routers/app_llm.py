"""Proxy de LLM do app Hangar (desktop): /api/app/llm/<conexão>/v1/… no formato da API de Mensagens da Anthropic.

Autenticação pela chave do dispositivo (Authorization: Bearer ahl_… ou x-api-key: ahl_…), conferida aqui (o
middleware deixa passar). A credencial da conexão sai do cofre e vai só para o provedor; o streaming SSE é repassado
pedaço a pedaço, sem buffer, e os tokens dos eventos message_start/message_delta viram o UsageEvent ao final. Nada do
corpo nem da resposta vai para log ou auditoria. Regras em services/app_llm.py; contrato em docs/app-hangar-models.md.
"""
import json
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .. import auth, tenancy
from ..db import SessionLocal
from ..services import app_llm

router = APIRouter(prefix="/api/app/llm")

MAX_BODY = 32 * 1024 * 1024  # o limite de requisição da API da Anthropic (contexto grande com imagens)
DROP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "content-length",
        "content-encoding", "proxy-authenticate", "proxy-authorization", "set-cookie"}
HTTP = lambda: httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10))  # noqa: E731  (os testes trocam)


def _err(e: app_llm.ProxyError) -> JSONResponse:
    return JSONResponse(e.body(), e.status)


async def _authorize(request: Request, connection_id: int, model: str | None = None, budget: bool = False) -> dict:
    """Chave do dispositivo -> empresa da requisição -> conexão e regras. Levanta ProxyError. Vem ANTES de ler o corpo:
    a rota fica fora do middleware de autenticação, e ler o corpo de quem não tem chave era deixar qualquer um ocupar
    a memória da central."""
    key = auth.token_from_headers({k.lower(): v for k, v in request.headers.items()})

    def device():
        with SessionLocal() as db:
            d = app_llm.device_of_key(db, key)
            return d.org_id, d.user_id, d.id

    org, user, device_pk = await run_in_threadpool(device)
    tenancy.set_org(org, user)  # tudo daqui em diante (e nas threads) age dentro da empresa do dispositivo

    def rules():
        from ..models import AppDevice
        with SessionLocal() as db:
            t = app_llm.target(db, db.get(AppDevice, device_pk), connection_id)
            if model is not None:
                app_llm.check_model(t, model)
            if budget:
                app_llm.check_budget(db, t)
            return t

    return await run_in_threadpool(rules)


async def _body(request: Request) -> tuple[bytes, dict]:
    """O corpo, lido em pedaços e cortado no limite (sem confiar só no Content-Length, que pode faltar ou mentir)."""
    too_big = app_llm.ProxyError(413, "request_too_large", f"corpo maior que {MAX_BODY // (1024 * 1024)} MB")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY:
        raise too_big
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise too_big
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise app_llm.ProxyError(400, "invalid_request_error", "o corpo não é JSON") from None
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
        raise app_llm.ProxyError(400, "invalid_request_error", "informe o modelo (campo model)")
    return raw, body


def _budget(t: dict):
    with SessionLocal() as db:
        app_llm.check_budget(db, t)


def _record(t: dict, model: str, usage: dict, ok: bool, t0: float):
    with SessionLocal() as db:
        app_llm.record(db, t, model, usage, ok, int((time.monotonic() - t0) * 1000))


@router.get("/{connection_id}/v1/models")
async def models(connection_id: int, request: Request):
    try:
        t = await _authorize(request, connection_id)
    except app_llm.ProxyError as e:
        return _err(e)

    def go():
        with SessionLocal() as db:
            return app_llm.models_list(t, db)
    return await run_in_threadpool(go)


@router.get("/{connection_id}/v1/models/{model_id:path}")
async def model(connection_id: int, model_id: str, request: Request):
    try:
        t = await _authorize(request, connection_id)
    except app_llm.ProxyError as e:
        return _err(e)

    def go():
        with SessionLocal() as db:
            return next((m for m in app_llm.models_list(t, db)["data"] if m["id"] == model_id), None)
    m = await run_in_threadpool(go)
    if m is None:
        return _err(app_llm.ProxyError(404, "not_found_error", f"o modelo '{model_id}' não está liberado nesta conexão"))
    return m


@router.post("/{connection_id}/v1/messages/count_tokens")
async def count_tokens(connection_id: int, request: Request):
    """Contar tokens não custa: sem orçamento nem UsageEvent, mas com as mesmas regras de conexão e modelo."""
    try:
        t = await _authorize(request, connection_id)  # a chave primeiro: o corpo só é lido para quem tem acesso
        raw, body = await _body(request)
        app_llm.check_model(t, body["model"])
    except app_llm.ProxyError as e:
        return _err(e)
    hdr = app_llm.upstream_headers(t, {k.lower(): v for k, v in request.headers.items()})
    async with HTTP() as client:
        try:
            r = await client.post(f"{t['base_url']}/v1/messages/count_tokens{_query(request)}", content=raw, headers=hdr)
        except httpx.HTTPError as e:
            return _err(app_llm.ProxyError(502, "api_error", f"provedor indisponível: {type(e).__name__}"))
    return JSONResponse(_json_or_text(r), r.status_code)


def _query(request: Request) -> str:
    """A query string vai junto (o Claude Code chama /v1/messages?beta=true)."""
    return f"?{request.url.query}" if request.url.query else ""


def _json_or_text(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        return {"type": "error", "error": {"type": "api_error", "message": r.text[:500]}}


@router.post("/{connection_id}/v1/messages")
async def messages(connection_id: int, request: Request):
    try:
        t = await _authorize(request, connection_id)  # a chave primeiro: o corpo só é lido para quem tem acesso
        raw, body = await _body(request)
        app_llm.check_model(t, body["model"])
        await run_in_threadpool(_budget, t)
    except app_llm.ProxyError as e:
        return _err(e)
    model = body["model"]
    hdr = app_llm.upstream_headers(t, {k.lower(): v for k, v in request.headers.items()})
    if body.get("stream"):
        hdr["accept"] = "text/event-stream"
    t0 = time.monotonic()
    client = HTTP()
    try:
        upstream = await client.send(client.build_request("POST", f"{t['base_url']}/v1/messages{_query(request)}",
                                                          content=raw, headers=hdr), stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        await run_in_threadpool(_record, t, model, {}, False, t0)
        return _err(app_llm.ProxyError(502, "api_error", f"provedor indisponível: {type(e).__name__}"))
    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in DROP}
    ok = upstream.status_code < 400

    if upstream.headers.get("content-type", "").startswith("text/event-stream"):
        usage: dict = {}

        async def relay():
            """Repassa cada pedaço assim que chega e, de passagem, lê as linhas com `usage`."""
            pending = b""
            async for chunk in upstream.aiter_bytes():
                yield chunk
                pending += chunk
                if b"\n" in pending:
                    *lines, pending = pending.split(b"\n")
                    for line in lines:
                        app_llm.sse_usage(line.rstrip(b"\r"), usage)
                if len(pending) > 1024 * 1024:  # uma linha enorme sem fim (não é SSE de verdade): não acumula
                    pending = b""

        async def done():
            await upstream.aclose()
            await client.aclose()
            await run_in_threadpool(_record, t, model, usage, ok, t0)
        return StreamingResponse(relay(), status_code=upstream.status_code, headers=headers,
                                 background=BackgroundTask(done))

    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
        await client.aclose()
    usage = {}
    try:
        payload = json.loads(content)
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
        app_llm.merge_usage(usage, payload["usage"])
    await run_in_threadpool(_record, t, model, usage, ok, t0)  # o preço é o do modelo pedido (o da lista)
    return Response(content, upstream.status_code, headers=headers)
