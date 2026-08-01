FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /e2e
COPY infra/e2e/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip check \
    && useradd --create-home e2e
COPY --chown=e2e:e2e tests/operational_truth_e2e/ tests/operational_truth_e2e/
COPY --chown=e2e:e2e airflow/dags/dataset_refresh_outcome.py runtime/dataset_refresh_outcome.py
COPY --chown=e2e:e2e airflow/dags/runtime_security_context.py runtime/runtime_security_context.py
RUN touch runtime/__init__.py && chown e2e:e2e runtime/__init__.py
USER e2e
CMD ["python", "-m", "pytest", "-ra", "-p", "no:cacheprovider", "--junitxml=/tmp/operational-truth-e2e.xml", "tests/operational_truth_e2e"]
