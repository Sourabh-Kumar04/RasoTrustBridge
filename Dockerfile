# syntax=docker/dockerfile:1
# Multi-stage slim Dockerfile for TrustBridge FastAPI application

# ==============================================================================
# Stage 1: Build Dependencies
# ==============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

# Install compilation tools needed for native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies to an isolated folder
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install .

# ==============================================================================
# Stage 2: Final Production Runtime Image
# ==============================================================================
FROM python:3.12-slim AS runner

# Security: Non-root user
RUN groupadd -g 1001 trustbridge && \
    useradd -u 1001 -g trustbridge -m -s /bin/bash trustbridge && \
    mkdir -p /app && chown -R trustbridge:trustbridge /app

WORKDIR /app

# Install runtime shared libraries (libpq for postgresql)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source code
COPY --chown=trustbridge:trustbridge . /app

# Ensure non-root ownership of app directory
USER trustbridge

# Expose standard application port
EXPOSE 8000

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    DEBUG=False \
    HOST=0.0.0.0 \
    PORT=8000

# Container healthcheck using standard Python runtime probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Launch uvicorn production server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
