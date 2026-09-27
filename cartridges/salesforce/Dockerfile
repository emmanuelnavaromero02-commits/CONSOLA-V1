FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY cartridges/salesforce/requirements.txt requirements.txt
RUN pip install --prefix=/install --no-cache-dir --only-binary=:all: -r requirements.txt


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN useradd -m appuser
COPY --chown=root:root omega_lakehouse/ omega_lakehouse/
COPY --chown=root:root omega_cartridge_kit/ omega_cartridge_kit/
COPY --chown=root:root cartridges/salesforce/app/ app/
USER appuser
RUN python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL httpfs;'); c.execute('INSTALL aws;')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8205"]
