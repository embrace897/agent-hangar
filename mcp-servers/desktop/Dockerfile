# Desktop governado (experimental): um desktop Linux isolado da Cua (https://github.com/trycua/cua, MIT) com o
# daemon cua-spacesd (as ferramentas de desktop por MCP, com token, só dentro do container) e o nosso portão
# (app/gateway.py, :8000/mcp), que a central usa. O Digital employee nunca recebe o token.
#
# Para o piloto vêm duas aplicações de desktop: o Gnumeric (planilha, com árvore de acessibilidade) e um "ERP
# legado" em Tk, sem acessibilidade — só dá para operar olhando a tela.
FROM ghcr.io/trycua/linux:24.04

RUN for i in 1 2 3; do apt-get update && apt-get install -y --no-install-recommends gnumeric python3-tk fonts-dejavu-core \
      && break || sleep 15; done \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/gateway/requirements.txt
RUN uv venv /opt/gateway/venv --python /usr/bin/python3 \
    && uv pip install --python /opt/gateway/venv/bin/python -r /opt/gateway/requirements.txt
COPY app /opt/gateway/app
COPY erp_legado.py /opt/erp/erp_legado.py
RUN printf '[Desktop Entry]\nType=Application\nName=ERP Legado\nExec=python3 /opt/erp/erp_legado.py\nTerminal=false\nCategories=Office;\n' \
      > /usr/share/applications/erp-legado.desktop \
    && printf '\n[program:hangar-gateway]\ncommand=/opt/gateway/venv/bin/python -m app.gateway\ndirectory=/opt/gateway\nuser=root\nautorestart=true\nstartsecs=2\npriority=60\nstdout_logfile=/var/log/supervisor/hangar-gateway.log\nredirect_stderr=true\n' \
      >> /etc/supervisor/supervisord.conf

EXPOSE 8000
