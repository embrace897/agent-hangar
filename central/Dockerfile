# Contexto de build: raiz do repositório (para levar os templates e a extensão do VS Code junto).
# Estágio 1: a extensão Agent Hangar do VS Code (.vsix), servida pela central em /downloads/.
ARG NODE_ALPINE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.12-slim
ARG COSIGN_IMAGE=ghcr.io/sigstore/cosign/cosign:v2.4.1
# imagem base parametrizada: o CI usa a cópia do espelho no GHCR (.github/base-images.txt); localmente, o Docker Hub
FROM ${NODE_ALPINE_IMAGE} AS vscode-ext
WORKDIR /ext
COPY extensions/vscode/package.json ./
RUN npm install --no-audit --no-fund
COPY extensions/vscode/ ./
COPY LICENSE /LICENSE
RUN LICENSE_PATH=/LICENSE npm test && LICENSE_PATH=/LICENSE npm run package

# cosign: verifica a assinatura das imagens dos plugins com código (PLUGIN_COSIGN_KEY / PLUGIN_COSIGN_IDENTITY)
FROM ${COSIGN_IMAGE} AS cosign

FROM ${PYTHON_IMAGE}
# correções de segurança do Debian publicadas depois da imagem base (o Trivy do CI acusa as que faltarem)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY central/requirements.txt .
RUN PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=8 pip install -r requirements.txt && useradd -u 10001 -m hangar \
    && mkdir -p /mcp-gateway && chown hangar /mcp-gateway   # volume compartilhado com o gateway Docker MCP
COPY central/alembic.ini ./alembic.ini
COPY central/migrations ./migrations
COPY central/app ./app
COPY templates ./templates
COPY --from=vscode-ext /ext/agent-hangar-vscode.vsix ./downloads/agent-hangar-vscode.vsix
COPY --from=cosign /ko-app/cosign /usr/local/bin/cosign
USER hangar
# 3128: proxy de saída dos plugins com código (só na rede dos plugins)
EXPOSE 8080 3128
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
