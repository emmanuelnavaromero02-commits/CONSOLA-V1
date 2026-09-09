from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_SQL = (
    REPO_ROOT
    / "cartridges"
    / "sap_successfactors"
    / "datasets"
    / "sap_successfactors_candidate_latest.sql"
)
PACKAGED_SOURCE = "s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet"


def _real_duckdb():
    """Peer tests install an import-time duckdb mock in ``sys.modules``."""
    existing = sys.modules.get("duckdb")
    if isinstance(existing, MagicMock) or isinstance(
        getattr(existing, "connect", None), MagicMock
    ):
        sys.modules.pop("duckdb", None)
    return importlib.import_module("duckdb")


def test_candidate_silver_preserves_null_status_when_metadata_omits_field(
    tmp_path: Path,
) -> None:
    """A missing OData property stays missing; Silver never invents status."""
    parquet_dir = (
        tmp_path
        / "load_date=2026-09-03"
        / "batch_id=candidate-contract-fixture"
    )
    parquet_dir.mkdir(parents=True)
    parquet_path = parquet_dir / "Candidate.parquet"

    con = _real_duckdb().connect()
    try:
        con.execute(
            """
            CREATE TABLE candidate_bronze AS
            SELECT * FROM (VALUES
                ('candidate-hash', 'masked-old', 'masked-last',
                 '2026-09-01T00:00:00Z', '2026-09-01T00:01:00Z'),
                ('candidate-hash', 'masked-new', 'masked-last',
                 '2026-09-02T00:00:00Z', '2026-09-02T00:01:00Z')
            ) AS rows(
                candidateId,
                firstName,
                lastName,
                lastModifiedDateTime,
                _extracted_at
            )
            """
        )
        escaped_path = str(parquet_path).replace("'", "''")
        con.execute(f"COPY candidate_bronze TO '{escaped_path}' (FORMAT PARQUET)")

        sql = CANDIDATE_SQL.read_text(encoding="utf-8").replace(
            PACKAGED_SOURCE,
            f"{tmp_path.as_posix()}/**/*.parquet",
        )
        rows = con.execute(sql).fetchall()
    finally:
        con.close()

    assert len(rows) == 1
    assert rows[0][0:4] == (
        "candidate-hash",
        "masked-new",
        "masked-last",
        None,
    )


def test_candidate_silver_never_derives_status_from_an_unrelated_field() -> None:
    sql = CANDIDATE_SQL.read_text(encoding="utf-8")

    assert "CAST(NULL AS VARCHAR) AS status" in sql
    assert "status               AS status" not in sql
