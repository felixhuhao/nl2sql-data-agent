# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/model-cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/model-cache/sentence-transformers \
    TORCH_HOME=/app/model-cache/torch

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip

COPY backend/pyproject.toml /tmp/backend-pyproject.toml
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c 'import subprocess, sys, tomllib; data = tomllib.load(open("/tmp/backend-pyproject.toml", "rb")); deps = data["project"]["dependencies"] + data["project"]["optional-dependencies"]["vector"] + data["project"]["optional-dependencies"]["mcp"]; subprocess.check_call([sys.executable, "-m", "pip", "install", *deps])'

COPY backend /app/backend
COPY mcp_servers /app/mcp_servers
COPY scripts /app/scripts
COPY docker /app/docker

RUN python -m pip install --no-build-isolation --no-deps -e /app/backend

COPY docker/backend-entrypoint.sh /app/docker/backend-entrypoint.sh

EXPOSE 8000

CMD ["/app/docker/backend-entrypoint.sh"]
