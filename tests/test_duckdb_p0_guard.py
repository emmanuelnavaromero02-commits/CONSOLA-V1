from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_duckdb_runtime_does_not_install_httpfs_dynamically():
    paths = [
        ROOT / "mcp-infra/app/main.py",
        ROOT / "mcp-infra/app/tools/cartridges.py",
        *(
            ROOT / "cartridges" / name / "app/services/duckdb_service.py"
            for name in (
                "hubspot",
                "replicon",
                "salesforce",
                "sap_hcm",
                "sap_s4hana",
                "sap_successfactors",
            )
        ),
    ]

    for path in paths:
        src = path.read_text(encoding="utf-8")
        assert "INSTALL httpfs" not in src


def test_mcp_infra_cartridge_sql_guard_blocks_ssrf_reader_shapes():
    src = (ROOT / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    guard = src.split("def _validate_cartridge_query_sql", 1)[1].split("def _postgres_mentioned_tables", 1)[0]

    assert "_SQL_FORBIDDEN_RE" in guard
    assert "_SQL_READER_CALL_RE" in guard
    assert "_SCOPED_READER_RE" in guard
    assert "cartridge SQL must read only direct s3:// file literals" in guard
    assert "cartridge SQL readers must use s3:// paths" in src
