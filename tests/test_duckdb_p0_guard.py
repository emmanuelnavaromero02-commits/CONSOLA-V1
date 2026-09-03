from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_duckdb_runtime_does_not_install_httpfs_dynamically():
    paths = [
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


def test_mcp_infra_httpfs_is_prebuilt_and_runtime_is_load_only():
    runtime = (ROOT / "mcp-infra/app/duckdb_runtime.py").read_text(encoding="utf-8")
    main = (ROOT / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    installer = (ROOT / "mcp-infra/scripts/install_duckdb_extensions.py").read_text(
        encoding="utf-8"
    )
    smoke = (ROOT / "mcp-infra/scripts/duckdb_offline_smoke.py").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "mcp-infra/Dockerfile").read_text(encoding="utf-8")

    assert "INSTALL httpfs" not in runtime
    assert "INSTALL httpfs" not in main
    assert 'connection.execute("SET autoinstall_known_extensions=false")' in runtime
    assert 'connection.execute("SET autoload_known_extensions=false")' in runtime
    assert 'connection.execute("LOAD httpfs")' in runtime
    assert "require_loaded_extensions(connection)" in runtime
    assert 'RuntimeError("DuckDB required extensions are unavailable")' in runtime

    assert 'for extension in ("httpfs", "aws")' in installer
    assert 'connection.execute(f"INSTALL {extension}")' in installer
    assert 'connection.execute("LOAD aws;")' in (
        ROOT / "mcp-infra/app/lakehouse_runtime.py"
    ).read_text(encoding="utf-8")
    assert "connect_duckdb_runtime()" in smoke
    assert '"autoinstall_known_extensions": "false"' in smoke
    assert '"autoload_known_extensions": "false"' in smoke
    assert (
        dockerfile.index("USER appuser")
        < dockerfile.index("RUN python scripts/install_duckdb_extensions.py")
        < dockerfile.index("RUN python scripts/duckdb_offline_smoke.py")
    )


def test_mcp_infra_cartridge_sql_guard_uses_fail_closed_ast_policy():
    main = (ROOT / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    policy = (ROOT / "mcp-infra/app/sql_reader_policy.py").read_text(encoding="utf-8")
    ast.parse(main)
    ast.parse(policy)
    guard = main.split("def _validate_cartridge_query_sql", 1)[1].split(
        "def _postgres_mentioned_tables", 1
    )[0]

    assert "validate_cartridge_reader_query(" in guard
    assert "except ReaderPolicyError:" in guard
    assert "cartridge SQL rejected by safety policy" in guard
    assert 'Tokenizer(dialect="duckdb")' in policy
    assert "sqlglot.parse(" in policy
    assert "len(statements) != 1" in policy
    assert "not isinstance(tree, exp.Query)" in policy
    assert "list(tree.find_all(exp.Placeholder))" in policy
    assert "list(tree.find_all(exp.Parameter))" in policy
    assert "has_adjacent_relation_string_scan(tree, tokens)" in policy
    assert "if table.db or table.catalog" in policy
    assert "isinstance(target, exp.Literal) and target.is_string" in policy
    assert "if name not in _STORAGE_FUNCTIONS" in policy
    assert "allow_server_resolution: bool = False" in policy
