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
USER e2e
CMD ["pytest", "-ra", "-p", "no:cacheprovider", "--junitxml=/artifacts/operational-truth-e2e.xml", "tests/operational_truth_e2e"]
