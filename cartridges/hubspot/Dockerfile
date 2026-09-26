FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY cartridges/hubspot/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL httpfs;'); c.execute('INSTALL aws;')"

COPY omega_lakehouse /app/omega_lakehouse
COPY omega_cartridge_kit /app/omega_cartridge_kit
COPY cartridges/hubspot/app /app/app

RUN adduser --disabled-password --gecos "" --home /app appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8210"]
