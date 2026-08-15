ARG LLAMA_CPP_BASE_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda-b10362
FROM ${LLAMA_CPP_BASE_IMAGE}

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       bash ca-certificates curl jq \
    && rm -rf /var/lib/apt/lists/*

COPY services/llama-cpp/launcher.sh /usr/local/bin/llama-launcher
COPY services/llama-cpp/healthcheck.sh /usr/local/bin/llama-healthcheck
RUN chmod 0755 /usr/local/bin/llama-launcher /usr/local/bin/llama-healthcheck

ENTRYPOINT ["/usr/local/bin/llama-launcher"]

