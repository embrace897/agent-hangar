#!/usr/bin/env sh
# Gera integrations/librechat/.env com segredos do LibreChat. A chave do Agent Hangar vem de HANGAR_INVOKE_KEY
# (ou de examples/data-studio/.invoke_key, criado por build_agent.py). Não sobrescreve valores existentes.
set -eu
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
hex() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
set_if_empty() {
  if grep -q "^$1=..*" .env; then return; fi
  tmp=$(mktemp); sed "s|^$1=.*|$1=$2|" .env > "$tmp" && mv "$tmp" .env
  echo "  $1: definido"
}
set_if_empty CREDS_KEY "$(hex 32)"
set_if_empty CREDS_IV "$(hex 16)"
set_if_empty JWT_SECRET "$(hex 32)"
set_if_empty JWT_REFRESH_SECRET "$(hex 32)"
KEY="${HANGAR_INVOKE_KEY:-}"
[ -z "$KEY" ] && [ -f ../../examples/data-studio/.invoke_key ] && KEY=$(cat ../../examples/data-studio/.invoke_key)
[ -n "$KEY" ] && set_if_empty HANGAR_INVOKE_KEY "$KEY"
chmod 600 .env 2>/dev/null || true
grep -q "^HANGAR_INVOKE_KEY=..*" .env || echo "ATENÇÃO: defina HANGAR_INVOKE_KEY no .env"
echo "Pronto: docker compose up -d  →  http://localhost:3090"
