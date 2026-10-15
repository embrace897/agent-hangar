# Servidor MCP local (stdio) num container isolado: Node 24 (npx; respeita HTTPS_PROXY com NODE_USE_ENV_PROXY), Python
# e uv (uvx), mais a ponte stdio -> Streamable HTTP que a central chama. Sem privilégios e com o disco só de leitura:
# os pacotes vão para /tmp (tmpfs), a saída para a internet passa pelo proxy da central (só os hosts liberados).
ARG NODE_IMAGE=node:24-slim
FROM ${NODE_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/bridge \
 && /opt/bridge/bin/pip install --no-cache-dir "mcp>=1.10,<2" "uvicorn>=0.30,<1" "uv>=0.8,<1"
COPY bridge.py /opt/bridge/bridge.py
ENV PATH=/opt/bridge/bin:$PATH \
    HOME=/tmp \
    NPM_CONFIG_CACHE=/tmp/.npm \
    npm_config_update_notifier=false \
    UV_CACHE_DIR=/tmp/.uv \
    UV_TOOL_DIR=/tmp/.uv-tools \
    UV_PYTHON_DOWNLOADS=never \
    XDG_CACHE_HOME=/tmp/.cache \
    PYTHONUNBUFFERED=1
USER node
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=240s CMD node -e "fetch('http://127.0.0.1:8000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["/opt/bridge/bin/python", "/opt/bridge/bridge.py"]
