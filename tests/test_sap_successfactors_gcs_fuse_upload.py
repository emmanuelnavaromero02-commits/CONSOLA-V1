from __future__ import annotations

import sys
from types import SimpleNamespace


def test_successfactors_upload_uses_gcs_fuse_without_minio(monkeypatch, tmp_path):
    sys.modules.pop("app.core.minio_client", None)
    sys.modules.pop("app.core.config", None)

    monkeypatch.syspath_prepend("cartridges/sap_successfactors")
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs_fuse")
    monkeypatch.setenv("LAKEHOUSE_LOCAL_ROOT", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("MINIO_BUCKET", "omega-gcs")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "")
    monkeypatch.setenv("MINIO_SECRET_KEY", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

    class ExplodingMinio:
        def __init__(self, *args, **kwargs):
            raise AssertionError("MinIO client must not be created for gcs_fuse")

    monkeypatch.setitem(
        sys.modules,
        "minio",
        SimpleNamespace(Minio=ExplodingMinio),
    )
    monkeypatch.setitem(
        sys.modules,
        "minio.credentials",
        SimpleNamespace(Credentials=object, Provider=object),
    )

    from app.core.minio_client import upload_file_to_minio

    local = tmp_path / "User.parquet"
    local.write_bytes(b"parquet")
    upload_file_to_minio(str(local), "raw/sap_successfactors/User/data.parquet")

    assert (tmp_path / "lakehouse/raw/sap_successfactors/User/data.parquet").read_bytes() == b"parquet"
