# Timeweb Cloud App Platform — Dockerfile deployment
# One image contains both the Vite frontend and Flask/Gunicorn backend.

# ---------- 1. Build frontend ----------
FROM node:22-alpine AS frontend-build
WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm config set registry https://registry.npmjs.org/ \
 && npm config set fetch-retries 3 \
 && npm config set fetch-retry-mintimeout 1000 \
 && npm config set fetch-retry-maxtimeout 10000 \
 && npm config set fetch-timeout 60000 \
 && (timeout 180 npm install --no-audit --no-fund --prefer-online \
     || (echo "npm install failed; retry 1" && sleep 3 && timeout 180 npm install --no-audit --no-fund --prefer-online) \
     || (echo "npm install failed; retry 2" && sleep 5 && timeout 180 npm install --no-audit --no-fund --prefer-online))

COPY frontend/ ./
RUN npm run build

# ---------- 2. Runtime ----------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONMALLOC=malloc \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    PORT=5000 \
    DATA_DIR=/data \
    SCHEDULE_OPTIONS_CACHE_MAX=24 \
    SCHEDULE_OPTIONS_CACHE_MAX_BYTES=8388608 \
    PUBLIC_SHARD_CACHE_MAX=16 \
    NODE_WORKER_MAX_OLD_SPACE_MB=192

WORKDIR /app

COPY requirements.txt ./
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY server_scheduler.mjs ./
COPY server_graph_scheduler.mjs ./
COPY --from=frontend-build /frontend/dist ./static

RUN mkdir -p /data

# Timeweb App Platform detects the application port from EXPOSE.
EXPOSE 5000

# app.py already provides GET /healthz. Docker healthcheck has priority over
# the health-check path configured in the Timeweb panel.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=3).read()" || exit 1

# Shell expansion keeps the image compatible if PORT is overridden later.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 2 --timeout 120 --graceful-timeout 20 --keep-alive 3 --max-requests 700 --max-requests-jitter 100 app:app"]
