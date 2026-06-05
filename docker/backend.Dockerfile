FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN python -m pip install --upgrade pip

COPY backend /app/backend
COPY scripts /app/scripts
COPY docker /app/docker

RUN python -m pip install -e /app/backend

COPY docker/backend-entrypoint.sh /app/docker/backend-entrypoint.sh

EXPOSE 8000

CMD ["/app/docker/backend-entrypoint.sh"]
