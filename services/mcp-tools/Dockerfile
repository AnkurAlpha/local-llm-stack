FROM node:22.18.0-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/mcp-venv/bin:/usr/local/bin:/usr/bin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl python3 python3-pip python3-venv sqlite3 tini \
    && python3 -m venv /opt/mcp-venv \
    && rm -rf /var/lib/apt/lists/*

COPY services/mcp-tools/requirements.txt /tmp/requirements.txt
RUN /opt/mcp-venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

RUN npm install --global \
      supergateway@3.4.3 \
      @modelcontextprotocol/server-sequential-thinking@2026.7.4 \
      @upstash/context7-mcp@4.0.0 \
      @playwright/mcp@0.0.79 \
      playwright@1.63.0-alpha-2026-08-05 \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright /usr/local/lib/node_modules

WORKDIR /app
COPY services/mcp-tools/supervisor.py /app/supervisor.py
COPY services/mcp-tools/healthcheck.py /app/healthcheck.py

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/supervisor.py"]
