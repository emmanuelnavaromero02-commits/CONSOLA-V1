FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY cartridges/banxico/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m appuser
COPY --chown=appuser:appuser omega_lakehouse/ omega_lakehouse/
COPY --chown=appuser:appuser cartridges/banxico/app/ app/

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8215"]
