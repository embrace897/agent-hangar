#!/usr/bin/env sh
# Jobs pela assinatura (services/subscriptions.py) no entrypoint de verdade, com CLIs de mentira (tests/fakebin):
#   - Claude Code recebe só o token OAuth (nada de chave de API) e o resultado volta;
#   - o "limite do plano" volta como status limited, com o horário do reset;
#   - Codex lê o login do auth.json (não do ambiente), e o login renovado volta para a central.
#   sh harness/tests/subscription_check.sh agent-hangar/harness-base:latest
set -eu
IMAGE="$1"
DIR=$(cd "$(dirname "$0")" && pwd)
run() {  # HARNESS_ID, env extra… -> o corpo do callback (JSON)
  h=$1; shift
  docker run --rm -v "$DIR/fakebin:/fakebin:ro" -e HARNESS_ID="$h" -e TASK=check -e LLM_AUTH=subscription \
    -e JOB_CALLBACK_URL=http://127.0.0.1:9999/cb "$@" --entrypoint sh "$IMAGE" -c '
mkdir -p /tmp/bin && cp /fakebin/* /tmp/bin/ && chmod +x /tmp/bin/*
python3 -c "
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(s):
        sys.stdout.write(s.rfile.read(int(s.headers[\"Content-Length\"])).decode()); sys.stdout.flush()
        s.send_response(200); s.end_headers()
    def log_message(s, *a): pass
http.server.HTTPServer((\"127.0.0.1\", 9999), H).handle_request()" &
sleep 1; PATH=/tmp/bin:$PATH node /srv/entrypoint.js >/dev/null 2>&1; wait' 2>/dev/null
}
fail=0
check() { if echo "$2" | grep -q "$3"; then echo "ok   $1"; else echo "FAIL $1: $2" | cut -c1-400; fail=1; fi; }

OUT=$(run claude-code -e CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-test)
check "claude-code pela assinatura: passou, sem chave de API" "$OUT" '"status": *"passed".*feito pela assinatura'
OUT=$(run claude-code -e CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-test -e FAKE_MODE=limit)
check "claude-code no limite do plano: status limited" "$OUT" '"status": *"limited"'
check "claude-code no limite do plano: horário do reset" "$OUT" '"resets_at": *1900000000'
OUT=$(run codex -e 'CODEX_AUTH_JSON={"tokens":{"refresh_token":"ref-old","access_token":"acc-1"}}')
check "codex pela assinatura: passou pelo auth.json" "$OUT" '"status": *"passed".*feito pelo codex'
check "codex: o login renovado volta para a central" "$OUT" '"codex_auth":.*ref-new'
exit $fail
