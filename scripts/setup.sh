#!/usr/bin/env sh
# Gera o .env com segredos fortes (não sobrescreve valores que já existem).
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env

rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
hex16() { head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
# chave Fernet = base64 url-safe de 32 bytes aleatórios
fernet() { head -c 32 /dev/urandom | base64 | tr -d '\n' | tr '+/' '-_'; }

set_if_empty() {
  key="$1"; value="$2"
  if grep -q "^${key}=..*" .env; then
    echo "  ${key}: já definido, mantido"
  elif grep -q "^${key}=" .env; then
    tmp=$(mktemp); sed "s|^${key}=.*|${key}=${value}|" .env > "$tmp" && mv "$tmp" .env
    echo "  ${key}: gerado"
  else
    printf '%s=%s\n' "$key" "$value" >> .env
    echo "  ${key}: gerado"
  fi
}

echo "Configurando .env"
set_if_empty ADMIN_TOKEN "$(rand)"
set_if_empty HANGAR_SECRET_KEY "$(fernet)"
set_if_empty INTERNAL_SECRET "$(rand)"
set_if_empty POSTGRES_PASSWORD "$(rand)"
# Activepieces (docker compose --profile activepieces): só usados se você ligar o profile
set_if_empty ACTIVEPIECES_ENCRYPTION_KEY "$(hex16)"
set_if_empty ACTIVEPIECES_JWT_SECRET "$(rand)"
set_if_empty ACTIVEPIECES_POSTGRES_PASSWORD "$(rand)"
# Memória dos agentes (docker compose --profile memory): só usados se você ligar o profile
set_if_empty MEMORY_TOKEN "$(rand)"
set_if_empty NEO4J_PASSWORD "$(rand)"
set_if_empty FALKORDB_PASSWORD "$(rand)"
set_if_empty MEM0_PG_PASSWORD "$(rand)"
chmod 600 .env 2>/dev/null || true
echo
echo "Pronto. Suba com:  docker compose up -d --build"
echo "Harnesses reais:   docker compose --profile harness build   (e --profile hermes)"
echo "UI:                http://localhost:8090  (token: valor de ADMIN_TOKEN no .env)"
