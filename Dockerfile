# Stage 1: React/Vite build. Amvera builds this stage, so static/ can never lag behind src/.
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm config set registry https://registry.npmjs.org/ \
 && npm config set fetch-retries 3 \
 && npm config set fetch-retry-mintimeout 1000 \
 && npm config set fetch-retry-maxtimeout 10000 \
 && npm config set fetch-timeout 60000 \
 && (timeout 150 npm install --no-audit --no-fund --prefer-online \
     || (echo "npm install attempt 1 failed; retrying..." && sleep 3 && timeout 150 npm install --no-audit --no-fund --prefer-online) \
     || (echo "npm install attempt 2 failed; retrying..." && sleep 5 && timeout 150 npm install --no-audit --no-fund --prefer-online))
COPY frontend/ ./
RUN npm run build

# Stage 2: Flask API + freshly built frontend.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONMALLOC=malloc \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    DATA_DIR=/data \
    SCHEDULE_OPTIONS_CACHE_MAX=24 \
    SCHEDULE_OPTIONS_CACHE_MAX_BYTES=8388608 \
    PUBLIC_SHARD_CACHE_MAX=16 \
    NODE_WORKER_MAX_OLD_SPACE_MB=192
WORKDIR /app
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY server_scheduler.mjs ./
COPY server_graph_scheduler.mjs ./
# Production image contains only runtime files. Source-level smoke tests are
# executed before packaging; the uploaded v1586 archive did not contain tools/,
# so copying them here made a clean Amvera build impossible.
COPY --from=frontend-build /frontend/dist ./static
RUN mkdir -p /data
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--timeout", "120", "--graceful-timeout", "20", "--keep-alive", "3", "--max-requests", "700", "--max-requests-jitter", "100", "app:app"]
