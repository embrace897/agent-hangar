# Navegador governado: MCP com Playwright/Chromium para os Digital employees operarem sistemas sem API.
# A alçada, os segredos e a auditoria ficam na central; este container só executa o passo pedido, nos domínios
# liberados em cada chamada.
ARG PYTHON_IMAGE=python:3.12-slim
# imagem base parametrizada: o CI usa a cópia do espelho no GHCR (.github/base-images.txt); localmente, o Docker Hub
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /srv
COPY requirements.txt .
RUN PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=8 pip install -r requirements.txt
# Chromium e as bibliotecas do sistema; o apt tenta de novo se o espelho do Debian estiver no meio de uma sincronização
RUN for i in 1 2 3 4 5; do \
        apt-get -o Acquire::Retries=5 update && python -m playwright install --with-deps chromium && break; \
        [ "$i" = 5 ] && exit 1; echo "instalação falhou (tentativa $i)"; rm -rf /var/lib/apt/lists/*; sleep $((i * 30)); \
    done && rm -rf /var/lib/apt/lists/*
COPY app ./app
RUN useradd -r -m -u 10002 browser && chown -R browser /srv /ms-playwright
USER browser
ENV PYTHONPATH=/srv
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["python", "-m", "app.server"]
