ARG CHROMA_BASE_IMAGE=chromadb/chroma:1.5.3
FROM ${CHROMA_BASE_IMAGE}

USER root
RUN apt-get update     && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends        ca-certificates curl     && rm -rf /var/lib/apt/lists/*
