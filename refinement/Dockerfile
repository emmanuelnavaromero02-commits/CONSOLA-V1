FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser
WORKDIR /app
COPY refinement/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m appuser
COPY --chown=appuser:appuser refinement/scripts/install_duckdb_extensions.py /tmp/install_duckdb_extensions.py
USER appuser
RUN python /tmp/install_duckdb_extensions.py && rm /tmp/install_duckdb_extensions.py
COPY --chown=appuser:appuser omega_lakehouse/ omega_lakehouse/
COPY --chown=appuser:appuser refinement/app/ app/
COPY --chown=appuser:appuser refinement/datasets/ datasets/
COPY --chown=appuser:appuser refinement/scripts/ scripts/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8500"]
