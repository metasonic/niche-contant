# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1 — build a frozen virtualenv with UV
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.5.11-python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
COPY uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

# ---------------------------------------------------------------------------
# Stage 2 — slim runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DASHBOARD_PORT=5050

# libjpeg/libwebp shared libs Pillow needs at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg62-turbo libwebp7 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bring in the prebuilt venv.
COPY --from=builder /app/.venv /app/.venv

# Application code.
COPY dashboard/ ./dashboard/

# Bake in evaluation data + report metadata (small).
COPY evaluation/ ./evaluation/
COPY report.json ./report.json

# Bake in optimized images (downloads_optimized/ → /app/downloads/).
# Generate locally first: python3 scripts/optimize_images.py
COPY downloads_optimized/ ./downloads/

# A default empty labels file ships with the image (overlay-mount a path
# to /app/dashboard/human_labels.json for cross-run persistence).
# Thumbnail cache lives at /app/dashboard/static/thumbs (mount for warm cache).

EXPOSE 5050

# Production server: gunicorn with a sensible worker count for a read-mostly app.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${DASHBOARD_PORT} --workers 2 --threads 4 --timeout 60 --access-logfile - dashboard.app:app"]
