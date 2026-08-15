FROM python:3.14.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       aria2 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/model-manager/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY services/model-manager/model_manager /app/model_manager
COPY services/model-manager/model-manager /usr/local/bin/model-manager
RUN chmod 0755 /usr/local/bin/model-manager

ENTRYPOINT []
CMD ["model-manager", "daemon"]

