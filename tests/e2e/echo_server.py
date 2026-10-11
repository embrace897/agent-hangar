"""Sistema de mentira para os testes ponta a ponta: devolve em JSON o que recebeu (método, caminho, cabeçalhos, corpo);
/status/<código> responde com aquele código; /portal é um "portal de banco" em HTML (login, saldo e pagamento) para o
navegador governado. Só a biblioteca padrão."""
import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>"""


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

    def _reply(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode(errors="replace") if n else ""
        if self.path.startswith("/portal") and self._portal(body):
            return
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
    HTTPServer(("0.0.0.0", 8080), Echo).serve_forever()
