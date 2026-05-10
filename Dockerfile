# ──────────────────────────────────────────────────────────────
# AI Fresher Job Hunter — Dockerfile
# Multi-stage-ready, production-hardened, minimal attack surface.
# ──────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# Metadata
LABEL maintainer="AI Fresher Job Hunter"
LABEL description="Automated daily fresher job hunter powered by GPT-4o-mini"

# ── Build-time env ────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /app

# ── Install OS deps (minimal) ─────────────────────────────────
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Create non-root user (security best practice) ─────────────
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

# ── Install Python dependencies ───────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application code ─────────────────────────────────────
COPY --chown=appuser:appgroup . .

# ── Create runtime directories (owned by non-root user) ───────
RUN mkdir -p /app/data /app/logs \
    && chown -R appuser:appgroup /app/data /app/logs

# ── Switch to non-root user ───────────────────────────────────
USER appuser

# ── Volumes for persistent data and logs ─────────────────────
VOLUME ["/app/data", "/app/logs"]

# ── Default command ───────────────────────────────────────────
CMD ["python", "main.py"]
