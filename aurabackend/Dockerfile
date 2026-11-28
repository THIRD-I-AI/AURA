# ──────────────────────────────────────────────────────────────────────────
# AURA Backend — Multi-stage tiered builds
#   - base-builder: Compiles wheels for base dependencies
#   - causal-builder: Compiles wheels for base + causal dependencies
#   - streaming-builder: Compiles wheels for base + streaming dependencies
#   - base-runtime: Slim image with base dependencies
#   - causal-runtime: Slim image with causal dependencies
#   - streaming-runtime: Slim image with streaming dependencies
# ──────────────────────────────────────────────────────────────────────────

# ── Base Builder ─────────────────────────────────────────────────────────
FROM python:3.13-slim AS base-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ unixodbc-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir=/build/wheels -r requirements.txt -c constraints.txt

# ── Causal Builder ───────────────────────────────────────────────────────
FROM base-builder AS causal-builder
COPY requirements-causal.txt constraints.txt ./
RUN pip wheel --no-cache-dir --wheel-dir=/build/wheels -r requirements-causal.txt -c constraints.txt

# ── Streaming Builder ────────────────────────────────────────────────────
FROM base-builder AS streaming-builder
COPY requirements-streaming.txt constraints.txt ./
RUN pip wheel --no-cache-dir --wheel-dir=/build/wheels -r requirements-streaming.txt -c constraints.txt

# ── Base Runtime (Shared Setup) ──────────────────────────────────────────
FROM python:3.13-slim AS base-os
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc libpq5 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# BUG-593: compose mounts the aura-data volume at /data. Docker gives a new named volume the
# ownership of the image directory it is mounted over; with no /data in the image a fresh box got a
# root-owned volume the `aura` user cannot write, so neither a new deploy nor a restore could start.
RUN useradd --create-home --shell /bin/bash aura && mkdir -p /data && chown -R aura:aura /app /data

# ── Base Runtime ─────────────────────────────────────────────────────────
FROM base-os AS base-runtime
COPY --from=base-builder /build/wheels /tmp/wheels
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt -c constraints.txt \
    && rm -rf /tmp/wheels
COPY . .
RUN mkdir -p /app/data/uploads /app/data/db && chown -R aura:aura /app/data
ENV PYTHONPATH=/app
USER aura
# Pre-install DuckDB httpfs so air-gapped s3:// reads work with no network.
# Runs as the `aura` user (after USER directive) so the extension is written
# to /home/aura/.duckdb/extensions — the same path DuckDB looks up at runtime.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"
# The commit this image was built from, surfaced by GET /health (BUG-204). Placed last so a changing
# value cannot invalidate any earlier layer.
ARG GIT_SHA=unknown
ENV AURA_GIT_SHA=${GIT_SHA}
EXPOSE 8000 8001 8002 8003 8004 8005 8006 8007
CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Causal Runtime ───────────────────────────────────────────────────────
FROM base-os AS causal-runtime
COPY --from=causal-builder /build/wheels /tmp/wheels
COPY requirements.txt requirements-causal.txt constraints.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt -r requirements-causal.txt -c constraints.txt \
    && rm -rf /tmp/wheels
COPY . .
RUN mkdir -p /app/data/uploads /app/data/db && chown -R aura:aura /app/data
ENV PYTHONPATH=/app
USER aura
# Pre-install DuckDB httpfs so air-gapped s3:// reads work with no network.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"
# The commit this image was built from, surfaced by GET /health (BUG-204). Placed last so a changing
# value cannot invalidate any earlier layer.
ARG GIT_SHA=unknown
ENV AURA_GIT_SHA=${GIT_SHA}
EXPOSE 8000 8001 8002 8003 8004 8005 8006 8007
CMD ["uvicorn", "counterfactual_service.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Streaming Runtime ────────────────────────────────────────────────────
FROM base-os AS streaming-runtime
COPY --from=streaming-builder /build/wheels /tmp/wheels
COPY requirements.txt requirements-streaming.txt constraints.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt -r requirements-streaming.txt -c constraints.txt \
    && rm -rf /tmp/wheels
COPY . .
RUN mkdir -p /app/data/uploads /app/data/db && chown -R aura:aura /app/data
ENV PYTHONPATH=/app
USER aura
# Pre-install DuckDB httpfs so air-gapped s3:// reads work with no network.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"
# The commit this image was built from, surfaced by GET /health (BUG-204). Placed last so a changing
# value cannot invalidate any earlier layer.
ARG GIT_SHA=unknown
ENV AURA_GIT_SHA=${GIT_SHA}
EXPOSE 8000 8001 8002 8003 8004 8005 8006 8007
CMD ["uvicorn", "uasr.service:app", "--host", "0.0.0.0", "--port", "8009"]
