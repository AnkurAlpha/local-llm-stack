FROM node:22.18.0-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/memory-venv/bin:/usr/local/bin:/usr/bin:/bin \
    HOME=/home/node

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl python3 python3-pip python3-venv tini \
    && python3 -m venv /opt/memory-venv \
    && rm -rf /var/lib/apt/lists/*

COPY services/memory-mcp/requirements.txt /tmp/requirements.txt
RUN /opt/memory-venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && npm install --global supergateway@3.4.3

# Cache Chroma's local default embedding model in the image so memory remains local at runtime.
RUN HOME=/home/node /opt/memory-venv/bin/python -c \
      "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; DefaultEmbeddingFunction()(['container build warmup'])" \
    && chown -R node:node /home/node

WORKDIR /app
COPY --chown=node:node services/memory-mcp/server.py /app/server.py
COPY --chown=node:node services/memory-mcp/memory_store.py /app/memory_store.py
COPY --chown=node:node services/memory-mcp/healthcheck.py /app/healthcheck.py
COPY --chown=node:node services/memory-mcp/entrypoint.sh /app/entrypoint.sh
RUN chmod 0755 /app/entrypoint.sh /app/healthcheck.py

USER node
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]

