from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gcp_startup_requires_gcs_hmac_and_declares_provider():
    text = (REPO_ROOT / "infra" / "terraform-gcp" / "templates" / "startup.sh.tftpl").read_text(
        encoding="utf-8"
    )

    assert "set_env LAKEHOUSE_PROVIDER gcs" in text
    assert "missing GCS HMAC secrets" in text
    assert "refusing to fall back to local MinIO" in text
    assert "LAKEHOUSE_PROVIDER: $${LAKEHOUSE_PROVIDER:-gcs}" in text
    assert "AIRFLOW_VAR_LAKEHOUSE_PROVIDER: $${LAKEHOUSE_PROVIDER:-gcs}" in text
    assert "AIRFLOW_URL: $${AIRFLOW_URL:-http://airflow:8080/airflow}" in text
