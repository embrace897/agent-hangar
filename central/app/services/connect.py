"""Conexões plug and play de um agente com ferramentas agênticas.

Dois modos, opcionais e independentes por ferramenta:
- "mcp": o agente vira uma FERRAMENTA (1 tool MCP) do cliente (Claude Code/Desktop, Codex, OpenCode, Cursor, VS Code,
  LibreChat, Open WebUI…) — o LLM do cliente decide quando chamá-lo;
- "model": o agente vira um MODELO no seletor do cliente de chat (LibreChat, Open WebUI, OpenCode, SDKs OpenAI) — ele
  conduz a conversa, recebe anexos e usa as próprias tools. Em clientes de código (chat do VS Code, Cline, Roo Code,
  Continue) o cliente também manda as ferramentas DELE (ler/editar arquivos, terminal): o agente as chama e o cliente
  executa na máquina do dev, com a aprovação dele (ver probe() para o teste de ponta a ponta).
Cada conexão tem a sua chave (escopo invoke, só aquele agente, marcada com cliente/modo): revogável sozinha, e o
uso aparece por ferramenta nas métricas mesmo quando o cliente não manda X-Channel.
"""
import json
import re
import secrets
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, config, deploy
from ..models import ApiKey
from .common import PlatformError, audit, get_agent, spec_of

MODES = ("mcp", "model")
KEY_PLACEHOLDER = "<SUA_CHAVE>"

# id -> (rótulo, modos suportados, nota)
CLIENTS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "claude-code": ("Claude Code", ("mcp",), "CLI da Anthropic. O agente vira uma ferramenta que o Claude chama."),
    "claude-desktop": ("Claude Desktop", ("mcp",), "App desktop, via mcp-remote (precisa de Node.js)."),
    "codex": ("Codex CLI", ("mcp",), "CLI da OpenAI (~/.codex/config.toml)."),
    "opencode": ("OpenCode", ("mcp", "model"), "Como ferramenta (MCP) ou como modelo (provedor OpenAI-compatible)."),
    "cursor": ("Cursor", ("mcp",), "IDE: .cursor/mcp.json (no projeto) ou ~/.cursor/mcp.json (global)."),
    "vscode": ("VS Code (Copilot)", ("model", "mcp"), "Como modelo no chat (agente de código: edita arquivos e usa o "
                                                    "terminal do VS Code) ou como ferramenta MCP (.vscode/mcp.json)."),
    "cline": ("Cline / Roo Code", ("model",), "Extensões de código do VS Code: o agente vira o modelo e edita o "
                                              "projeto com as ferramentas da extensão."),
    "continue": ("Continue", ("model",), "Extensão do VS Code/JetBrains (config.yaml), com uso de ferramentas."),
    "librechat": ("LibreChat", ("model", "mcp"), "Como modelo (recomendado: anexos, workspace por conversa, "
                                                 "progresso ao vivo) ou como ferramenta MCP."),
    "open-webui": ("Open WebUI", ("model", "mcp"), "Como modelo (conexão OpenAI) ou como ferramenta (MCP)."),
    "openai-sdk": ("SDK OpenAI / outros", ("model",), "Qualquer cliente OpenAI-compatible (Python, JS, n8n…)."),
    "generic-mcp": ("Outro cliente MCP", ("mcp",), "Qualquer cliente MCP com Streamable HTTP e cabeçalhos."),
}


def clients() -> list[dict]:
    return [{"id": k, "label": v[0], "modes": list(v[1]), "note": v[2]} for k, v in CLIENTS.items()]


def _name(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", slug)


def snippet(client: str, mode: str, slug: str, agent_name: str, key: str = KEY_PLACEHOLDER) -> dict:
    """Configuração pronta para colar: {language, file, content, steps}."""
    if client not in CLIENTS:
        raise PlatformError(f"cliente desconhecido: {client}. Opções: {sorted(CLIENTS)}")
    if mode not in CLIENTS[client][1]:
        raise PlatformError(f"{CLIENTS[client][0]} não suporta o modo '{mode}' (suporta: {list(CLIENTS[client][1])})")
    base = config.PUBLIC_BASE_URL
    mcp_url, v1 = f"{base}/gw/{slug}/mcp", f"{base}/gw/{slug}/v1"
    name = _name(slug)
    hdr = {"Authorization": f"Bearer {key}", "X-Channel": client}
    in_docker = base.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")

    if mode == "mcp":
        if client == "claude-code":
            return {"language": "bash", "file": None, "steps": ["Rode no terminal (escopo do usuário; troque "
                                                                 "--scope user por project para só este projeto)."],
                    "content": f'claude mcp add --transport http --scope user {name} {mcp_url} '
                               f'--header "Authorization: Bearer {key}" --header "X-Channel: claude-code"'}
        if client == "claude-desktop":
            cfg = {"mcpServers": {name: {"command": "npx", "args": [
                "-y", "mcp-remote", mcp_url, "--header", f"Authorization: Bearer {key}",
                "--header", "X-Channel: claude-desktop", *(["--allow-http"] if mcp_url.startswith("http:") else [])]}}}
            return {"language": "json", "file": "claude_desktop_config.json",
                    "steps": ["Claude Desktop → Settings → Developer → Edit Config.",
                              "Mescle o bloco em mcpServers e reinicie o Claude Desktop."],
                    "content": json.dumps(cfg, indent=2, ensure_ascii=False)}
        if client == "codex":
            return {"language": "toml", "file": "~/.codex/config.toml",
                    "steps": ["Acrescente ao ~/.codex/config.toml e reinicie o Codex."],
                    "content": f'[mcp_servers.{name.replace("-", "_")}]\nurl = "{mcp_url}"\n'
                               f'http_headers = {{ Authorization = "Bearer {key}", X-Channel = "codex" }}\n'}
        if client == "opencode":
            cfg = {"$schema": "https://opencode.ai/config.json",
                   "mcp": {name: {"type": "remote", "url": mcp_url, "enabled": True, "headers": hdr}}}
            return {"language": "json", "file": "opencode.json (projeto) ou ~/.config/opencode/opencode.json",
                    "steps": ["Mescle o bloco no opencode.json."], "content": json.dumps(cfg, indent=2)}
        if client == "cursor":
            return {"language": "json", "file": ".cursor/mcp.json",
                    "steps": ["Mescle em .cursor/mcp.json; o servidor aparece em Settings → MCP."],
                    "content": json.dumps({"mcpServers": {name: {"url": mcp_url, "headers": hdr}}}, indent=2)}
        if client == "vscode":
            return {"language": "json", "file": ".vscode/mcp.json",
                    "steps": ["Mescle em .vscode/mcp.json e habilite o servidor no Copilot Chat (modo Agent)."],
                    "content": json.dumps({"servers": {name: {"type": "http", "url": mcp_url, "headers": hdr}}},
                                          indent=2)}
        if client == "librechat":
            content = (f"mcpServers:\n  {name}:\n    type: streamable-http\n"
                       f'    url: "{in_docker}/gw/{slug}/mcp"\n    headers:\n'
                       f'      Authorization: "Bearer {key}"\n      X-Channel: "librechat"\n'
                       f'      X-Conversation-Id: "{{{{LIBRECHAT_BODY_CONVERSATIONID}}}}"\n')
            return {"language": "yaml", "file": "librechat.yaml",
                    "steps": ["Acrescente ao librechat.yaml e reinicie o LibreChat.",
                              "No chat, ative o servidor pelo seletor de MCP (ícone de ferramentas)."],
                    "content": content}
        if client == "open-webui":
            return {"language": "text", "file": None,
                    "steps": ["Open WebUI → Admin Panel → Settings → External Tools → + (Add Connection).",
                              "Type: MCP (Streamable HTTP); URL e chave abaixo; Auth: Bearer.",
                              "Habilite a ferramenta no modelo (Workspace → Models) ou no chat."],
                    "content": f"URL:   {in_docker}/gw/{slug}/mcp\nAuth:  Bearer\nKey:   {key}\n"}
        return {"language": "text", "file": None,
                "steps": ["Cliente MCP genérico: transporte Streamable HTTP, servidor stateless (sem sessão)."],
                "content": f"URL: {mcp_url}\nHeaders:\n  Authorization: Bearer {key}\n  X-Channel: {client}\n"
                           f"Tool: {name} (argumento: message)\n"}

    # ---------------------------------------------------------------- modo modelo
    if client == "vscode":
        return {"language": "text", "file": None,
                "steps": ["Chat do VS Code → seletor de modelos → Gerenciar modelos → provedor OpenAI Compatible "
                          "(disponível nas versões recentes do Copilot Chat).",
                          "Informe a URL base, a chave e o id do modelo abaixo, com chamada de ferramentas ativada.",
                          "Escolha o agente no seletor e use o modo Agent: ele lê e edita arquivos e roda comandos no "
                          "terminal do VS Code, sempre com a sua aprovação.",
                          "Sem essa opção no seu VS Code? Use Cline, Roo Code ou Continue (mesma URL e chave)."],
                "content": f"URL base:  {v1}\nChave:     {key}\nModelo:    {slug}\nFerramentas: sim (tool calling)\n"}
    if client == "cline":
        return {"language": "text", "file": None,
                "steps": ["Cline (ou Roo Code) → Settings → API Provider: OpenAI Compatible.",
                          "Preencha Base URL, API Key e Model ID com os valores abaixo e salve.",
                          "O agente passa a planejar e editar o projeto com as ferramentas da extensão (arquivos, "
                          "terminal), que pedem a sua aprovação antes de agir."],
                "content": f"Base URL:  {v1}\nAPI Key:   {key}\nModel ID:  {slug}\n"}
    if client == "continue":
        content = (f"models:\n  - name: {agent_name}\n    provider: openai\n    model: {slug}\n"
                   f"    apiBase: {v1}\n    apiKey: {key}\n    roles: [chat, edit, apply]\n"
                   f"    capabilities: [tool_use]\n    requestOptions:\n      headers:\n        X-Channel: continue\n")
        return {"language": "yaml", "file": "~/.continue/config.yaml",
                "steps": ["Mescle o bloco em models do config.yaml do Continue.",
                          "Escolha o agente no seletor do Continue e use o modo Agent para editar o projeto."],
                "content": content}
    if client == "librechat":
        content = (f'endpoints:\n  custom:\n    - name: "{agent_name}"\n      apiKey: "{key}"\n'
                   f'      baseURL: "{in_docker}/gw/{slug}/v1"\n'
                   f'      models:\n        default: ["{slug}"]\n        fetch: false\n'
                   f'      titleConvo: true\n      titleEndpoint: "{agent_name} (títulos)"\n'
                   f'      headers:\n        X-Channel: "librechat"\n'
                   f'        X-Conversation-Id: "{{{{LIBRECHAT_BODY_CONVERSATIONID}}}}"   # workspace por conversa\n'
                   f'        X-User-Id: "{{{{LIBRECHAT_USER_ID}}}}"\n'
                   f'      dropParams: ["stop", "frequency_penalty", "presence_penalty", "top_p", "user"]\n'
                   f'    - name: "{agent_name} (títulos)"          # títulos baratos: 1 chamada, sem ferramentas\n'
                   f'      apiKey: "{key}"\n      baseURL: "{in_docker}/gw/{slug}/v1"\n'
                   f'      models:\n        default: ["{slug}"]\n        fetch: false\n      titleConvo: false\n'
                   f'      headers:\n        X-Hangar-Mode: "lite"\n        X-Channel: "librechat-title"\n'
                   f'fileConfig:\n  endpoints:\n    "{agent_name}":                 # anexos chegam ao agente\n'
                   f'      supportedMimeTypes: ["^image/.*", "^text/.*", "^application/.*"]\n')
        return {"language": "yaml", "file": "librechat.yaml",
                "steps": ["Mescle no librechat.yaml e reinicie o LibreChat.",
                          "Opcional: modelSpecs.enforce: true esconde o endpoint de títulos do seletor.",
                          "Kit completo pronto: integrations/librechat no repositório."],
                "content": content}
    if client == "open-webui":
        return {"language": "text", "file": None,
                "steps": ["Open WebUI → Admin Panel → Settings → Connections → OpenAI API → +.",
                          f"Model IDs: adicione {slug} (ou deixe o Open WebUI listar via /models).",
                          "Para workspace por conversa, rode o Open WebUI com ENABLE_FORWARD_USER_INFO_HEADERS=true "
                          "(o id do chat vira a sessão do agente)."],
                "content": f"URL:  {in_docker}/gw/{slug}/v1\nKey:  {key}\nModel: {slug}\n"}
    if client == "opencode":
        cfg = {"$schema": "https://opencode.ai/config.json", "provider": {"hangar": {
            "npm": "@ai-sdk/openai-compatible", "name": "Agent Hangar",
            "options": {"baseURL": v1, "apiKey": key, "headers": {"X-Channel": "opencode"}},
            "models": {slug: {"name": agent_name}}}}}
        return {"language": "json", "file": "opencode.json",
                "steps": ["Mescle no opencode.json e escolha o modelo com /models."],
                "content": json.dumps(cfg, indent=2, ensure_ascii=False)}
    return {"language": "python", "file": None,
            "steps": ["Qualquer SDK/cliente OpenAI-compatible (stream suportado)."],
            "content": f'from openai import OpenAI\n\nclient = OpenAI(base_url="{v1}", api_key="{key}",\n'
                       f'                default_headers={{"X-Channel": "openai-sdk"}})\n'
                       f'r = client.chat.completions.create(model="{slug}",\n'
                       f'                                   messages=[{{"role": "user", "content": "Olá!"}}])\n'
                       f'print(r.choices[0].message.content)\n'}


def connect(db: Session, slug: str, client: str, mode: str, actor: str = "admin", user_id: int | None = None) -> dict:
    """Cria a chave da conexão (invoke, só este agente) e devolve o trecho pronto com ela preenchida."""
    a = get_agent(db, slug)
    snippet(client, mode, a.slug, a.name)  # valida cliente/modo antes de criar a chave
    row, raw = auth.create_api_key(db, f"{a.slug} · {CLIENTS[client][0]} ({mode})"[:100], ["invoke"], [a.slug],
                                   actor, client=client, mode=mode, user_id=user_id)
    audit(db, actor, "agent.connect", a.slug, f"{client} ({mode}) chave #{row.id}")
    return {"connection": connection_dict(row), "key": raw, **snippet(client, mode, a.slug, a.name, raw),
            "note": "A chave aparece só agora; revogar a conexão desliga só esta ferramenta."}


def connection_dict(k: ApiKey) -> dict:
    label = CLIENTS.get(k.client or "", (k.client or "?",))[0]
    return {**auth.api_key_dict(k), "client_label": label}


def connections(db: Session, slug: str, owner: int | None = None) -> list[dict]:
    """owner: só as conexões desse usuário (quem não administra o agente vê só as próprias)."""
    q = select(ApiKey).where(ApiKey.client.is_not(None), ApiKey.revoked_at.is_(None)).order_by(ApiKey.id.desc())
    if owner is not None:
        q = q.where(ApiKey.user_id == owner)
    rows = db.scalars(q)
    return [connection_dict(k) for k in rows if k.agents == [slug]]


# ---------------------------------------------------------------- teste de conexão (ferramentas do cliente)
PING = {"type": "function", "function": {
    "name": "hangar_ping", "description": "Ferramenta de teste de conexão do Agent Hangar: devolve pong com o nonce.",
    "parameters": {"type": "object", "properties": {"nonce": {"type": "string"}}, "required": ["nonce"]}}}
HTTP = lambda: httpx.Client(timeout=httpx.Timeout(300, connect=10))  # noqa: E731  (os testes trocam)


def _sse(resp: httpx.Response) -> dict:
    """Junta um stream OpenAI (SSE): texto, tool_calls (por índice), finish_reason e usage."""
    text, calls, finish, usage = "", {}, None, None
    for line in resp.iter_lines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        ev = json.loads(line[6:])
        usage = ev.get("usage") or usage
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            text += d.get("content") or ""
            for tc in d.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                          "function": {"name": "", "arguments": ""}})
                c["id"] = tc.get("id") or c["id"]
                f = tc.get("function") or {}
                c["function"]["name"] += f.get("name") or ""
                c["function"]["arguments"] += f.get("arguments") or ""
            finish = ch.get("finish_reason") or finish
    return {"content": text, "tool_calls": [calls[i] for i in sorted(calls)], "finish_reason": finish, "usage": usage}


def probe(db: Session, slug: str, env: str = "prod") -> dict:
    """Teste de ponta a ponta do modo "agente de código": manda uma ferramenta do cliente (hangar_ping), confere que o
    agente a chama pelo stream (tool_calls + finish_reason=tool_calls), devolve o resultado como o cliente faria e
    confere a resposta final. É o caminho do chat do VS Code, Cline, Roo e Continue."""
    from .runtime import active_deployment, refresh_deployments

    a = get_agent(db, slug)
    if spec_of(a).get("harness"):
        return {"ok": False, "env": env, "steps": [{"name": "tipo", "ok": False, "detail":
                "agente com harness roda num container efêmero próprio e não recebe ferramentas do cliente; "
                "use um agente de chat para o modo agente de código"}]}
    refresh_deployments(db, [a])
    if not active_deployment(a, env):
        raise PlatformError(f"'{slug}' não está rodando em {env}")
    from .catalog import code_allowed
    ok_code, msg = code_allowed(db, spec_of(a), env)
    if not ok_code:
        return {"ok": False, "env": env, "steps": [{"name": "política de código", "ok": False, "detail": msg}]}
    if spec_of(a).get("llm", {}).get("client_tools") is False:
        return {"ok": False, "env": env, "steps": [{"name": "configuração", "ok": False,
                "detail": "a spec desliga as ferramentas do cliente (llm.client_tools=false)"}]}
    nonce = secrets.token_hex(4)
    deploy.ensure_up(slug, env)
    url = f"{deploy.internal_url(slug, env)}/v1/chat/completions"
    hdr = {"X-Channel": "connection-test", "X-Session-Id": f"connection-test-{nonce}", "X-Progress": "off"}
    user = {"role": "user", "content": f"Teste de conexão: chame hangar_ping nonce={nonce} e depois responda com o "
                                       "texto exato que a ferramenta devolver."}
    steps, t0 = [], time.monotonic()
    with HTTP() as http:
        with http.stream("POST", url, headers=hdr, json={
                "model": slug, "stream": True, "messages": [user], "tools": [PING],
                "tool_choice": {"type": "function", "function": {"name": "hangar_ping"}}}) as r:
            if r.status_code >= 400:
                r.read()
                raise PlatformError(f"o agente respondeu HTTP {r.status_code}: {r.text[:200]}")
            first = _sse(r)
        call = next((c for c in first["tool_calls"] if c["function"]["name"] == "hangar_ping"), None)
        ok1 = bool(call) and first["finish_reason"] == "tool_calls" and nonce in call["function"]["arguments"]
        steps.append({"name": "o agente chama a ferramenta do cliente", "ok": ok1,
                      "ms": int((time.monotonic() - t0) * 1000),
                      "detail": (f"tool_call hangar_ping({call['function']['arguments']}), finish_reason=tool_calls"
                                 if ok1 else f"esperava tool_call hangar_ping com o nonce; veio "
                                             f"{first['tool_calls'] or 'nenhuma'} / {first['finish_reason']} / "
                                             f"{first['content'][:200]!r}")})
        if not ok1:
            return {"ok": False, "env": env, "steps": steps}
        t1 = time.monotonic()
        r2 = http.post(url, headers=hdr, json={"model": slug, "tools": [PING], "messages": [
            user, {"role": "assistant", "content": first["content"] or None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": f"pong {nonce}"}]})
        if r2.status_code >= 400:
            raise PlatformError(f"o agente respondeu HTTP {r2.status_code} ao resultado: {r2.text[:200]}")
        final = r2.json()["choices"][0]
        reply = final["message"].get("content") or ""
        ok2 = final.get("finish_reason") == "stop" and bool(reply.strip())
        steps.append({"name": "o agente usa o resultado e responde", "ok": ok2,
                      "ms": int((time.monotonic() - t1) * 1000),
                      "detail": ("resposta final recebida" + (" (com o pong)" if nonce in reply else ""))
                      if ok2 else f"esperava uma resposta final; veio {final.get('finish_reason')}"})
    audit(db, "connection-test", "agent.connection_test", slug, f"{env}: {'ok' if ok1 and ok2 else 'falhou'}")
    return {"ok": ok1 and ok2, "env": env, "steps": steps, "reply": reply[:500]}
