from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from refinement.app.duckdb_engine import DuckDBEngine


class FakeStorage:
    config = SimpleNamespace(provider="minio", bucket="lakehouse")

    def __init__(self) -> None:
        self.uploads = []
        self.deleted_prefixes = []
        self.deleted_objects = []
        self.objects = [
            SimpleNamespace(key="silver/x/y/_snapshots/3.parquet"),
            SimpleNamespace(key="silver/x/y/_snapshots/2.parquet"),
            SimpleNamespace(key="silver/x/y/_snapshots/1.parquet"),
        ]

    def put_file(self, key, path: Path, *, overwrite=False):
        self.uploads.append((key, path.read_bytes(), overwrite))
        return SimpleNamespace(uri=f"s3://lakehouse/{key}")

    def delete_prefix(self, prefix, *, require_trailing_slash=True):
        self.deleted_prefixes.append((prefix, require_trailing_slash))
        return 1

    def iter_list(self, prefix):
        return iter([obj for obj in self.objects if obj.key.startswith(prefix)])

    def delete_object(self, key):
        self.deleted_objects.append(key)
        return True


def test_refinement_storage_helpers_preserve_s3_layout(tmp_path):
    engine = DuckDBEngine()
    engine.storage = FakeStorage()
    engine.minio_bucket = "lakehouse"
    local = tmp_path / "data.parquet"
    local.write_bytes(b"parquet")

    uri = engine._upload_local_parquet(str(local), "s3://lakehouse/silver/x/y/data.parquet")
    engine._delete_s3_prefix("s3://lakehouse/silver/x/y/data.parquet")
    engine._prune_snapshots("silver", "x", "y", keep=1)

    assert uri == "s3://lakehouse/silver/x/y/data.parquet"
    assert engine.storage.uploads == [("silver/x/y/data.parquet", b"parquet", False)]
    assert engine.storage.deleted_prefixes == [("silver/x/y/data.parquet", False)]
    assert engine.storage.deleted_objects == [
        "silver/x/y/_snapshots/2.parquet",
        "silver/x/y/_snapshots/1.parquet",
    ]
