# ===========================================================================
# Sport5 Fantasy API - Production Dockerfile
# Multi-stage lightweight build using python:3.11-slim
# ===========================================================================

FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging configuration and install package into wheels
COPY pyproject.toml README.md LICENSE ./
COPY sport5_fantasy_api ./sport5_fantasy_api
RUN pip install --no-cache-dir --upgrade pip build \
    && pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels .

# ---------------------------------------------------------------------------
# Final Runtime Stage
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runner

WORKDIR /app

# Create a non-root system user for secure execution
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy built wheels from builder stage
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

USER appuser

# Expose FastAPI application port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Launch Uvicorn ASGI server
ENTRYPOINT ["uvicorn", "sport5_fantasy_api.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
