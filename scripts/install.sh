#!/usr/bin/env sh
# Instalação guiada do Agent Hangar (Linux/macOS/WSL): confere Python e Docker, instala o CLI num ambiente isolado
# (.hangar/venv) e roda `hangar setup`. Argumentos são repassados: ./scripts/install.sh --answers setup.yaml
set -eu
cd "$(dirname "$0")/.."
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10+ é necessário (https://www.python.org/downloads/ ou o gerenciador do seu sistema)."; exit 1
fi
command -v docker >/dev/null 2>&1 || { echo "Docker não encontrado: instale o Docker Desktop ou o Docker Engine."; exit 1; }
if [ ! -x .hangar/venv/bin/python ]; then
  mkdir -p .hangar
  "$PY" -m venv .hangar/venv || { echo "Falta o módulo venv (Debian/Ubuntu: sudo apt install python3-venv)."; exit 1; }
fi
.hangar/venv/bin/python -m pip install -q --upgrade pip
.hangar/venv/bin/python -m pip install -q ./cli
echo "CLI instalado em .hangar/venv (atalho: .hangar/venv/bin/hangar)"
exec .hangar/venv/bin/hangar setup --dir . "$@"
