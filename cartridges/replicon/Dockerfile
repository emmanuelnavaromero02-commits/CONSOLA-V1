# Multi-stage: keep build-essential and curl out of the runtime image, ship
# only the compiled wheels and the pre-fetched DuckDB httpfs extension.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# Pre-warm the DuckDB httpfs extension into the appuser's home so the
# runtime container never needs network access on first connect.
ENV PYTHONPATH=/install/lib/python3.12/site-packages
RUN mkdir -p /opt/duckdb_home && \
    HOME=/opt/duckdb_home python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL httpfs;')"


FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime cert bundle — duckdb's httpfs needs it for HTTPS to S3/MinIO.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN adduser --disabled-password --gecos "" --home /app appuser \
    && chown -R appuser:appuser /app

# Move the warmed extension cache into appuser's home (HOME=/app per adduser).
COPY --from=builder --chown=appuser:appuser /opt/duckdb_home/.duckdb /app/.duckdb

COPY --chown=appuser:appuser app /app/app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8201"]
