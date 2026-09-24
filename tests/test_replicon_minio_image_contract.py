from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import load_cartridge_app


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ERROR = "DuckDB remote source unavailable"


def test_control_room_smoke_reads_real_minio_twice_on_internal_network() -> None:
    workflow = (ROOT / ".github/workflows/control-room-postgres-rls.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts/ci_replicon_minio_smoke.sh").read_text(encoding="utf-8")
    contract = workflow + script
    required = (
        "docker network create --internal",
        "ghcr.io/emmanuelnavaromero02-commits/minio:",
        "ci-bucket",
        "data.parquet",
        "run_kb_sql",
        "expected_rows",
        "reader-1",
        "reader-2",
        "DuckDB remote source unavailable",
        "list_objects",
    )
    assert all(token in contract for token in required)
    smoke = contract.split("Build and smoke Replicon DuckDB/httpfs image", 1)[1]
    assert "INSTALL httpfs" not in smoke


def test_remote_query_execution_error_is_sanitized(monkeypatch) -> None:
    load_cartridge_app("replicon")
    from app.services import duckdb_service

    class Connection:
        def execute(self, sql: str, *_params):
            if sql.startswith("SELECT"):
                raise duckdb_service.duckdb.IOException(
                    "403 secret s3://ci-bucket/private .duckdb/extensions"
                )
            return self

        def close(self):
            return None

    monkeypatch.setattr(
        duckdb_service,
        "_get_duckdb_connection",
        lambda _sql: Connection(),
    )
    with pytest.raises(duckdb_service.DuckDBHTTPFSUnavailable) as exc:
        duckdb_service.run_kb_sql(
            "SELECT * FROM read_parquet('s3://ci-bucket/missing.parquet')"
        )
    assert str(exc.value) == PUBLIC_ERROR
    assert exc.value.__cause__ is None
