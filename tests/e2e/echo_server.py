"""Sistema de mentira para os testes ponta a ponta: devolve em JSON o que recebeu (método, caminho, cabeçalhos, corpo);
/status/<código> responde com aquele código; /portal é um "portal de banco" em HTML (login, saldo e pagamento) para o
navegador governado; /llm/v1 é um provedor de LLM compatível com a OpenAI (lista de modelos e chat que repete a
última mensagem), que guarda o que recebeu em /llm/_state. Só a biblioteca padrão."""
import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>"""
JIRA = {"issues": {}, "comments": [], "transitions": [], "attachments": []}
AGUI_AGENT = {"calls": 0, "authorization": "", "roles": [], "thread": None, "last": ""}
LLM = {"requests": []}
LLM_MODELS = [
    {"id": "e2e/thinker", "name": "E2E Thinker", "context_length": 128000, "supported_parameters": ["tools", "reasoning"],
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.000001", "completion": "0.000004"}},
    {"id": "e2e/plain", "name": "E2E Plain", "context_length": 32000, "supported_parameters": ["tools"],
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "pricing": {"prompt": "0.0000002", "completion": "0.0000008"}},
]


class Echo(BaseHTTPRequestHandler):
    def _html(self, title: str, body: str):
        out = PAGE.format(title=title, body=body).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _portal(self, body: str) -> bool:
        path = self.path.split("?")[0]
        form = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
        if path == "/portal":
            self._html("Banco de Teste", '<form method="post" action="/portal/home"><label for="u">Usuário</label>'
                                         '<input id="u" name="usuario"><label for="s">Senha</label>'
                                         '<input id="s" name="senha" type="password"><button type="submit">Entrar</button></form>')
        elif path == "/portal/home":
            ok = form.get("senha") == "s3nh4-e2e"
            self._html("Conta", (f"<p>Bem-vindo, {html.escape(form.get('usuario', ''))}. Saldo: R$ 1.000,00</p>"
                                 '<form method="post" action="/portal/pago"><button type="submit">Pagar boleto</button></form>')
                       if ok else "<p>Senha incorreta</p>")
        elif path == "/portal/pago":
            self._html("Comprovante", "<p>Pagamento confirmado: boleto 123</p>")
        else:
            return False
        return True

    def _json(self, data, code: int = 200):
        out = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _jira(self, body: str):
        """Um Jira Cloud de mentira (API v3): /jira/_seed cria chamados, /jira/_state mostra o que a central mandou."""
        path = self.path.split("?")[0][len("/jira"):]
        if path == "/_reset":
            JIRA.update(issues={}, comments=[], transitions=[], attachments=[])
            return self._json({"ok": True})
        if path == "/_seed":
            for i in json.loads(body or "{}").get("issues", []):
                JIRA["issues"][i["key"]] = i
            return self._json({"ok": True})
        if path == "/_state":
            return self._json({k: v for k, v in JIRA.items() if k != "issues"})
        if path == "/rest/api/3/myself":
            return self._json({"emailAddress": "bot@acme.test"})
        if path == "/rest/api/3/search/jql":
            return self._json({"issues": list(JIRA["issues"].values()), "isLast": True})
        parts = path.strip("/").split("/")  # rest api 3 issue KEY [comment|transitions|attachments]
        i = JIRA["issues"].get(parts[4]) if len(parts) > 4 else None
        if i is None:
            return self._json({"errorMessages": ["não existe"]}, 404)
        if len(parts) == 5:
            return self._json(i)
        if parts[5] == "comment":
            JIRA["comments"].append({"key": i["key"], "body": json.loads(body)["body"]})
            return self._json({"id": str(len(JIRA["comments"]))}, 201)
        if parts[5] == "transitions" and self.command == "GET":
            return self._json({"transitions": [{"id": "21", "name": "Start", "to": {"name": "In Progress"}},
                                               {"id": "31", "name": "Resolve", "to": {"name": "Done"}}]})
        if parts[5] == "transitions":
            JIRA["transitions"].append({"key": i["key"], "id": json.loads(body)["transition"]["id"]})
            return self._json({}, 204)
        if parts[5] == "attachments":
            name = body.split('filename="', 1)[1].split('"', 1)[0] if 'filename="' in body else "?"
            JIRA["attachments"].append({"key": i["key"], "name": name})
            return self._json([{"id": "1"}])
        return self._json({}, 404)

    def _llm(self, body: str):
        """Provedor de LLM de mentira, no formato da OpenRouter: a central lista os modelos (com recursos e preço) e o
        agente conversa; a resposta repete a última mensagem do usuário. /llm/_state mostra os pedidos recebidos."""
        path = self.path.split("?")[0][len("/llm"):]
        if path == "/_reset":
            LLM["requests"].clear()
            return self._json({"ok": True})
        if path == "/_state":
            return self._json(LLM)
        if path == "/v1/models":
            return self._json({"data": LLM_MODELS})
        if path == "/v1/chat/completions":
            req = json.loads(body or "{}")
            LLM["requests"].append({k: v for k, v in req.items() if k != "messages"} | {"n_messages": len(req.get("messages", []))})
            msgs = req.get("messages", [])
            last = next((m for m in reversed(msgs) if m.get("role") == "user"), {})
            text = last.get("content") if isinstance(last.get("content"), str) else json.dumps(last.get("content"))
            usage = {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}
            # "use-tool <nome>" com essa ferramenta na lista: o modelo pede a ferramenta; com o resultado, ecoa o resultado
            message = {"role": "assistant", "content": f"eco: {text}"}
            if msgs and msgs[-1].get("role") == "tool":
                message = {"role": "assistant", "content": f"eco-tool: {msgs[-1].get('content')}"}
            else:
                names = {(t.get("function") or {}).get("name") for t in req.get("tools") or []}
                want = text.split("use-tool ", 1)[1].split()[0] if "use-tool " in (text or "") else ""
                if want in names:
                    # "use-tool app_state_update /caminho=valor" troca esse campo (sem nada: /pedido/status=pago)
                    target = text.split("use-tool app_state_update", 1)[1].split()[:1] if want == "app_state_update" else []
                    path, _, value = (target[0] if target and "=" in target[0] else "/pedido/status=pago").partition("=")
                    args = ({"patch": [{"op": "replace", "path": path, "value": value}]}
                            if want == "app_state_update" else {"id": "4471"})
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": f"call_{len(LLM['requests'])}", "type": "function",
                        "function": {"name": want, "arguments": json.dumps(args)}}]}
            if req.get("stream"):
                return self._llm_stream(req, message, usage)
            finish = "tool_calls" if message.get("tool_calls") else "stop"
            return self._json({"id": "e2e", "object": "chat.completion", "model": req.get("model"), "usage": usage,
                               "choices": [{"index": 0, "finish_reason": finish, "message": message}]})
        return self._json({"error": "não existe"}, 404)

    def _llm_stream(self, req: dict, message: dict, usage: dict):
        """A mesma resposta em SSE, como um provedor de verdade: o texto palavra por palavra, a tool_call com os
        argumentos fatiados e, com stream_options.include_usage, o uso num último pedaço."""
        def chunk(delta, finish=None):
            return {"id": "e2e", "object": "chat.completion.chunk", "model": req.get("model"),
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        out = [chunk({"role": "assistant"})]
        if message.get("tool_calls"):
            c = message["tool_calls"][0]
            args = c["function"]["arguments"]
            out.append(chunk({"tool_calls": [{"index": 0, "id": c["id"], "type": "function",
                                              "function": {"name": c["function"]["name"], "arguments": args[:5]}}]}))
            out.append(chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[5:]}}]}))
            out.append(chunk({}, "tool_calls"))
        else:
            out.append(chunk({"reasoning": "pensando na resposta"}))
            words = message["content"].split(" ")
            out += [chunk({"content": w if i == 0 else " " + w}) for i, w in enumerate(words)]
            out.append(chunk({}, "stop"))
        if (req.get("stream_options") or {}).get("include_usage"):
            out.append({"id": "e2e", "object": "chat.completion.chunk", "choices": [], "usage": usage})
        raw = "".join(f"data: {json.dumps(x, ensure_ascii=False)}\n\n" for x in out).encode() + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _agui_agent(self, body: str):
        """Um agente AG-UI de fora do hangar (como um LangGraph ou um Google ADK): responde em eventos SSE, com uma
        ferramenta e o texto em pedaços. /agui-agent/_state diz o que chegou (a credencial que o hangar tirou do cofre)."""
        if self.path.endswith("/_state"):
            return self._json(AGUI_AGENT)
        inp = json.loads(body or "{}")
        AGUI_AGENT.update(calls=AGUI_AGENT["calls"] + 1, authorization=self.headers.get("authorization", ""),
                          roles=[m.get("role") for m in inp.get("messages", [])], thread=inp.get("threadId"))
        last = next((m.get("content") for m in reversed(inp.get("messages", [])) if m.get("role") == "user"), "")
        AGUI_AGENT["last"] = last  # o que o agente viu (com o DLP da empresa, [CPF_…] no lugar do CPF)
        t, r = inp.get("threadId", "t"), inp.get("runId", "r")
        evs = [{"type": "RUN_STARTED", "threadId": t, "runId": r},
               {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "consultar_estoque"},
               {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": '{"sku": "A1"}'},
               {"type": "TOOL_CALL_END", "toolCallId": "c1"},
               {"type": "TOOL_CALL_RESULT", "messageId": "r1", "toolCallId": "c1", "content": "12 unidades"},
               {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"}]
        evs += [{"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": w} for w in ["remoto: ", str(last)]]
        evs += [{"type": "TEXT_MESSAGE_END", "messageId": "m1"},
                # o uso do modelo do agente remoto, para a medição e o orçamento do hangar
                {"type": "CUSTOM", "name": "hangar.usage", "value": {"prompt_tokens": 120, "completion_tokens": 30}},
                # o que chegou aqui, com os colchetes trocados (a ponte devolve o valor real só no formato [CPF_…])
                {"type": "CUSTOM", "name": "e2e.seen", "value": str(last).replace("[", "(").replace("]", ")")},
                {"type": "RUN_FINISHED", "threadId": t, "runId": r}]
        raw = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in evs).encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _reply(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode(errors="replace") if n else ""
        if self.path.startswith("/agui-agent"):
            return self._agui_agent(body)
        if self.path.startswith("/llm/"):
            return self._llm(body)
        if self.path.startswith("/portal") and self._portal(body):
            return
        if self.path.startswith("/jira/"):
            return self._jira(body)
        out = json.dumps({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}).encode()
        code = int(self.path.split("/status/", 1)[1].split("?")[0]) if "/status/" in self.path else 200
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _reply

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    from http.server import ThreadingHTTPServer
    ThreadingHTTPServer(("0.0.0.0", 8080), Echo).serve_forever()
