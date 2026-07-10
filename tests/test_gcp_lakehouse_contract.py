from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gcp_startup_uses_gcsfuse_and_declares_provider():
    text = (REPO_ROOT / "infra" / "terraform-gcp" / "templates" / "startup.sh.tftpl").read_text(
        encoding="utf-8"
    )

    assert "apt-get install -y fuse3 gcsfuse" in text
    assert "gcsfuse --implicit-dirs --file-mode=0666 --dir-mode=0777 -o allow_other" in text
    assert "set_env LAKEHOUSE_PROVIDER gcs_fuse" in text
    assert "set_env LAKEHOUSE_LOCAL_ROOT /lakehouse" in text
    assert "LAKEHOUSE_PROVIDER: $${LAKEHOUSE_PROVIDER:-gcs_fuse}" in text
    assert "LAKEHOUSE_LOCAL_ROOT: $${LAKEHOUSE_LOCAL_ROOT:-/lakehouse}" in text
    assert "AIRFLOW_VAR_LAKEHOUSE_PROVIDER: $${LAKEHOUSE_PROVIDER:-gcs_fuse}" in text
    assert "/mnt/omega-lakehouse:/lakehouse:rw" in text
    assert 'set_env MINIO_ACCESS_KEY ""' not in text
    assert 'set_env MINIO_SECRET_KEY ""' not in text
    assert "AIRFLOW_URL: $${AIRFLOW_URL:-http://airflow:8080/airflow}" in text
