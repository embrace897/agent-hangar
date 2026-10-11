"""Chamadas de LLM feitas pela própria central (extração de documentos, revisor crítico, triagem, memorando…).

Usa a conexão do Digital employee (a de produção, ou a que o cargo indicar). Sem conexão OpenAI-compatível — o modelo
de teste mock/echo, por exemplo — devolve None, e quem chamou usa a regra determinística dela: assim tudo funciona (e
é testável) sem LLM e sem custo.
"""
import json
import re

import httpx

from .. import crypto
from ..models import Agent, Employee
from .common import PlatformError


def target(db, agent: Agent | None) -> tuple[str, str, str] | None:
    """(base_url, api_key, modelo) da conexão do agente, ou None (sem conexão OpenAI-compatível)."""
    if agent is None:
        return None
    from .catalog import get_connection
    from .common import spec_of
    try:
        spec = spec_of(agent)
    except PlatformError:
        return None
    llm = spec.get("llm") or {}
    name = ((llm.get("prod") or {}).get("connection") or llm.get("connection") or (llm.get("stage") or {}).get("connection"))
    if not name:
        return None
    try:
        conn = get_connection(db, name)
    except PlatformError:
        return None
    if conn.protocol != "openai":
        return None
    model = (llm.get("prod") or {}).get("model") or llm.get("model") or conn.model_name
    if str(model).startswith("mock"):
        return None
    return conn.base_url, crypto.decrypt(conn.api_key), model


def target_for_employee(db, e: Employee | None):
    return target(db, db.get(Agent, e.agent_id)) if e is not None else None


def CALL(base: str, key: str, model: str, system: str, user: str, json_mode: bool = False) -> str:  # noqa: N802  (os testes trocam)
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(f"{base.rstrip('/')}/chat/completions", timeout=180,
                   headers={"Authorization": f"Bearer {key}"} if key else {}, json=body)
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content") or ""


def complete(tgt, system: str, user: str) -> str | None:
    if tgt is None:
        return None
    return CALL(*tgt, system, user)


def complete_json(tgt, system: str, user: str) -> dict | list | None:
    """O JSON que o modelo devolveu (o primeiro objeto ou lista no texto), ou None sem conexão."""
    if tgt is None:
        return None
    text = CALL(*tgt, system, user, json_mode=True)
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    if not m:
        raise PlatformError(f"o modelo não devolveu JSON: {text[:200]}")
    return json.loads(m.group(0))
