from __future__ import annotations

import hashlib
import importlib
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_GUARD = ROOT / "mcp-infra/app/operational_truth.py"
LIVE_REPLICON = ROOT / "tests/test_control_room_live_postgres_replicon_wip_v3.py"
LEGACY_FIXTURE = ROOT / "tests/fixtures/replicon_wip_mensual_legacy_v1.sql"
LEGACY_DIGEST = "2bf0d0456874fd068c7885e6c0397d3e54241922e67b8cd3cb8a34fa1ab7f558"


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _sql, _params=()):
        return None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return _Cursor(self.rows)


def _guard(sql: str) -> str | None:
    current = "s3://lake/silver/replicon/customer/path/run-current.parquet"
    rows = [("customer_table", "customer/path", current)]
    block = runpy.run_path(str(MCP_GUARD))["replicon_generic_query_block_reason"]
    return block(
        sql,
        cartridge_id="replicon",
        connection_factory=lambda: _Connection(rows),
        security_context={"tenant_id": "tenant-a", "workspace_id": "ws-a"},
    )


def test_replicon_generic_wip_reader_allows_only_exact_current_literal() -> None:
    current = "s3://lake/silver/replicon/customer/path/run-current.parquet"
    assert _guard(f"SELECT * FROM read_parquet('{current}')") is None
    for sql in (
        "SELECT * FROM read_parquet('s3://lake/raw/customer/path/legacy.parquet')",
        "SELECT * FROM read_parquet('s3://lake/raw/customer/pat?')",
        "SELECT * FROM read_parquet('s3://lake/raw/customer/*')",
        f"SELECT * FROM read_parquet(['{current}'])",
        f"SELECT * FROM read_parquet(['{current}', 's3://lake/raw/customer/path/old.parquet'])",
        "SELECT * FROM read_parquet('s3://lake/raw/customer/' || 'path/old.parquet')",
        "SELECT * FROM read_parquet(path_column)",
        "SELECT * FROM read_parquet('s3://lake/silver/replicon/customer/path/run-old.parquet')",
    ):
        assert _guard(sql) == "noncurrent_replicon_wip_artifact"


def test_base_currency_runtime_frame_has_duckdb_compatible_dtypes() -> None:
    frame_module = importlib.import_module(
        "cartridges.replicon.app.services.base_currency_frame"
    )
    for rows in (
        [],
        [
            {
                "effective_from": "2026-01-01",
                "effective_to": None,
                "currency": "USD",
                "authority_source": "workspace_config",
                "verified_at": "2026-01-01T00:00:00Z",
            }
        ],
    ):
        frame = frame_module.base_currency_frame(rows)
        assert all(str(dtype) != "str" for dtype in frame.dtypes)
        assert list(frame.columns) == list(frame_module.BASE_CURRENCY_COLUMNS)


def test_legacy_fixture_is_versioned_digest_checked_and_git_independent() -> None:
    source = LIVE_REPLICON.read_text(encoding="utf-8")
    assert '"git", "show"' not in source
    assert "49f792eedcf1177d8e7681478c5efecdc43e908b:" not in source
    payload = LEGACY_FIXTURE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == LEGACY_DIGEST
    assert b"COALESCE(fx.mxn_to_usd, 0.05)" in payload
