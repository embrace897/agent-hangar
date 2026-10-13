#!/usr/bin/env sh
# Confere o instalador num sistema sem Docker (runner macOS do GitHub): ferramentas BSD no setup.sh, venv + CLI
# no install.sh e o `hangar setup --answers … --no-start`. Um docker de mentira responde o preflight; nada sobe.
set -eu
cd "$(dirname "$0")/../.."
STUB=$(mktemp -d)
cat > "$STUB/docker" <<'SH'
#!/bin/sh
case "$*" in
  "compose version --short") echo "2.29.7" ;;
  info) echo "stub" ;;
  ps*) ;;
  *) echo "docker stub: $*" >&2 ;;
esac
SH
chmod +x "$STUB/docker"
export PATH="$STUB:$PATH"

echo "== scripts/setup.sh (segredos com as ferramentas do sistema)"
rm -f .env
sh scripts/setup.sh >/dev/null
python3 - <<'PY'
import base64, re
env = dict(l.split("=", 1) for l in open(".env", encoding="utf-8").read().splitlines() if "=" in l and not l.startswith("#"))
for k in ("ADMIN_TOKEN", "INTERNAL_SECRET", "POSTGRES_PASSWORD", "MEMORY_TOKEN"):
    assert re.fullmatch(r"[0-9a-f]{64}", env[k]), k
assert len(base64.urlsafe_b64decode(env["HANGAR_SECRET_KEY"])) == 32, "chave Fernet inválida"
print("setup.sh: segredos ok")
PY

echo "== scripts/install.sh --answers (imagens publicadas, sem subir)"
rm -f .env
cat > /tmp/answers.yaml <<'YAML'
target: docker
images: published
port: 8090
exposure: {mode: local}
YAML
sh scripts/install.sh --answers /tmp/answers.yaml --no-start
python3 - <<'PY'
import re
env = open(".env", encoding="utf-8").read()
version = re.search(r'^VERSION = "([^"]+)"', open("central/app/config.py", encoding="utf-8").read(), re.M).group(1)
assert re.search(r"^HANGAR_REGISTRY=ghcr\.io/valteresj2$", env, re.M), "HANGAR_REGISTRY"
assert re.search(rf"^HANGAR_VERSION={re.escape(version)}$", env, re.M), "HANGAR_VERSION"
assert re.search(r"^ADMIN_TOKEN=.{32,}$", env, re.M), "ADMIN_TOKEN"
print(f"install.sh: .env pronto para as imagens publicadas {version}")
PY
.hangar/venv/bin/hangar --version
