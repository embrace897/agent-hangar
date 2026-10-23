# Memória dos agentes: Graphiti (grafo de conhecimento temporal) sobre Neo4j ou FalkorDB, ou Mem0 sobre pgvector
# (MEMORY_BACKEND).
# Embeddings locais (fastembed/ONNX): o modelo é baixado no build, então nada sai da rede para gerar vetores.
ARG PYTHON_IMAGE=python:3.12-slim
# imagem base parametrizada: o CI usa a cópia do espelho no GHCR (.github/base-images.txt); localmente, o Docker Hub
FROM ${PYTHON_IMAGE}
# correções de segurança do Debian publicadas depois da imagem base (o Trivy do CI acusa as que faltarem)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FASTEMBED_CACHE_PATH=/srv/models HF_HUB_OFFLINE=0
WORKDIR /srv
COPY requirements.txt .
RUN PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=8 pip install -r requirements.txt
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('${EMBEDDING_MODEL}')"
COPY app ./app
RUN useradd -r -u 10001 memory && mkdir -p /data && chown -R memory /srv /data
USER memory
# telemetria do Graphiti e do Mem0 desligadas: nada da memória sai da rede além da chamada ao LLM de extração
ENV PYTHONPATH=/srv HF_HUB_OFFLINE=1 EMBEDDING_DIM=384 GRAPHITI_TELEMETRY_ENABLED=false MEM0_TELEMETRY=False \
    MEM0_DIR=/data/.mem0 MEM0_HISTORY_PATH=/data/mem0_history.db
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["python", "-m", "app.server"]
