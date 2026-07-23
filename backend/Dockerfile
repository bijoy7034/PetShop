# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8000

# WORKERS defaults to 2 — override at runtime if you have more CPU headroom
# (rule of thumb: 2 * cores + 1 for I/O-bound workloads like this). The
# app is stateless across workers (session state lives in Mongo + JWT
# cookies), so scaling out is safe.
ENV WORKERS=4

CMD ["sh", "-c", "uv run uvicorn app:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --proxy-headers --forwarded-allow-ips '*'"]
