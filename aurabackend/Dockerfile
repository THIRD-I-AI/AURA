# ──────────────────────────────────────────────────────────────────────────
# AURA Backend — Multi-stage build
#   Stage 1 (builder):  install compilers + build wheels
#   Stage 2 (runtime):  slim image with only runtime deps
# ──────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir=/build/wheels -r requirements.txt

# ── Stage 2: Runtime ─────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Only runtime system libs — no compilers
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pre-built wheels (no gcc needed)
COPY --from=builder /build/wheels /tmp/wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt \
    && rm -rf /tmp/wheels

# Copy application code
COPY . .

ENV PYTHONPATH=/app

# Non-root user
RUN useradd --create-home --shell /bin/bash aura \
    && chown -R aura:aura /app
USER aura

# Create data directories
RUN mkdir -p /app/data/uploads /app/data/db

EXPOSE 8000 8001 8002 8003 8004 8005 8006 8007

# Default — API Gateway
CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
