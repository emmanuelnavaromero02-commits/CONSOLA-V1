FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/refinement-app
WORKDIR /app
COPY refinement/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --system omega-refinement \
    && useradd -m --gid omega-refinement refinement-app \
    && useradd -m --gid omega-refinement refinement-verifier \
    && install -d -o refinement-app -g omega-refinement -m 2770 /var/lib/omega/duckdb-spill
COPY --chown=refinement-app:omega-refinement refinement/scripts/install_duckdb_extensions.py /tmp/install_duckdb_extensions.py
USER refinement-app
RUN python /tmp/install_duckdb_extensions.py && rm /tmp/install_duckdb_extensions.py
COPY --chown=root:root omega_lakehouse/ omega_lakehouse/
COPY --chown=root:root refinement/app/ app/
COPY --chown=root:root refinement/datasets/ datasets/
COPY --chown=root:root refinement/scripts/ scripts/
USER root
ENTRYPOINT ["python", "/app/scripts/refinement_supervisor.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8500"]
