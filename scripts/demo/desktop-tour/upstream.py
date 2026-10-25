"""Upstream Anthropic falso só para a gravação do vídeo do Hangar desktop (porta 8792, loopback).
Responde a qualquer /v1/messages com a mesma resposta roteirizada, em streaming SSE, e lista dois modelos."""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8792
MODELS = [{"type": "model", "id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5", "created_at": "2025-09-29T00:00:00Z"},
          {"type": "model", "id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "created_at": "2025-10-01T00:00:00Z"}]
REPLY = """Li o contrato renovado da **ACME** e comparei com a versão anterior. Três pontos para você revisar:

1. **Reajuste** — passou de IPCA para IPCA + 2%, a partir do 13º mês.
2. **SLA** — o tempo de resposta crítico caiu de 8 h para 4 h, com multa de 5% da mensalidade por violação.
3. **Rescisão** — o aviso prévio subiu de 30 para 90 dias.

Sugiro levar o item 2 para o time de suporte antes de assinar: com a escala atual, as 4 h só são viáveis em horário comercial."""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            return self._json(200, {"data": MODELS, "has_more": False, "first_id": MODELS[0]["id"], "last_id": MODELS[-1]["id"]})
        self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.loads(raw or b"{}")
        if self.path.startswith("/v1/messages/count_tokens"):
            return self._json(200, {"input_tokens": 1200})
        if not self.path.startswith("/v1/messages"):
            return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}})
        model = body.get("model", "claude-sonnet-4-5")
        usage = {"input_tokens": 1830, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1}
        if not body.get("stream"):
            return self._json(200, {"id": "msg_video", "type": "message", "role": "assistant", "model": model,
                                    "content": [{"type": "text", "text": REPLY}], "stop_reason": "end_turn",
                                    "stop_sequence": None, "usage": {**usage, "output_tokens": 160}})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()

        def ev(kind, data):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
        ev("message_start", {"type": "message_start", "message": {"id": "msg_video", "type": "message", "role": "assistant",
                                                                  "model": model, "content": [], "stop_reason": None,
                                                                  "stop_sequence": None, "usage": usage}})
        ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        words = REPLY.split(" ")
        for i in range(0, len(words), 3):
            ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "text_delta", "text": " ".join(words[i:i + 3]) + (" " if i + 3 < len(words) else "")}})
            time.sleep(0.06)
        ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                             "usage": {"output_tokens": 160}})
        ev("message_stop", {"type": "message_stop"})


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
