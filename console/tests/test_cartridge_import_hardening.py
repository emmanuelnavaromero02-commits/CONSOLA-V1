from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services import cartridge_service
from app.services.cartridge_service import (
    _generate_seed_sql,
    _resolve_dag_file,
    _split_sql_statements,
    _validate_dag_filename,
    _validate_import_zip_members,
    _validate_seed_sql,
)


def test_import_zip_rejects_path_traversal():
    with pytest.raises(ValueError, match="unsafe ZIP path"):
        _validate_import_zip_members(["../evil.py"])


def test_import_zip_rejects_backslash_path_alias():
    with pytest.raises(ValueError, match="unsafe ZIP path"):
        _validate_import_zip_members([r"dags\escape.py"])


def test_import_zip_rejects_unknown_root():
    with pytest.raises(ValueError, match="unexpected ZIP member"):
        _validate_import_zip_members(["tmp/payload.py"])


def test_import_zip_allows_expected_cartridge_roots():
    _validate_import_zip_members([
        "config/seed.sql",
        "dags/extract.py",
        "agents/billing.yaml",
        "apps/dashboard/index.html",
        "datasets/gold.sql",
        "hints/assistant.md",
        "specs/openapi.yaml",
    ])


def test_import_zip_rejects_duplicate_members():
    with pytest.raises(ValueError, match="duplicate ZIP member"):
        _validate_import_zip_members(["config/seed.sql", "config/seed.sql"])


def test_import_zip_rejects_oversized_member(monkeypatch):
    monkeypatch.setattr(cartridge_service, "_MAX_IMPORT_MEMBER_BYTES", 10)

    with pytest.raises(ValueError, match="ZIP member too large"):
        _validate_import_zip_members([
            SimpleNamespace(filename="config/seed.sql", file_size=11),
        ])


def test_import_zip_rejects_oversized_uncompressed_payload(monkeypatch):
    monkeypatch.setattr(cartridge_service, "_MAX_IMPORT_MEMBER_BYTES", 100)
    monkeypatch.setattr(cartridge_service, "_MAX_IMPORT_UNCOMPRESSED_BYTES", 15)

    with pytest.raises(ValueError, match="uncompressed payload too large"):
        _validate_import_zip_members([
            SimpleNamespace(filename="config/seed.sql", file_size=10),
            SimpleNamespace(filename="hints/assistant.md", file_size=10),
        ])


def test_seed_sql_allows_expected_seed_tables():
    _validate_seed_sql(
        """
        INSERT INTO cartridges (id, name, assistant_hints)
        VALUES ('replicon', 'Replicon', 'safe')
        ON CONFLICT (id) DO UPDATE
        SET name=EXCLUDED.name, assistant_hints=EXCLUDED.assistant_hints;
        INSERT INTO entity_config (cartridge_id, entity) VALUES ('replicon', 'Project')
        ON CONFLICT (cartridge_id, entity) DO NOTHING;
        """
    )


def test_generated_full_seed_round_trips_through_ast_validator():
    manifest = {
        "id": "canary",
        "name": "Synthetic Canary",
        "version": "1.0",
        "description": "non-sensitive fixture",
        "pattern": "custom",
        "category": "test",
        "bronze_path": "raw/canary",
        "assistant_hints": "synthetic only",
        "connections": [
            {
                "conn_id": "default",
                "description": "fixture",
                "auth_type": "none",
                "poll_strategy": None,
            }
        ],
        "dags": [
            {
                "dag_id": "canary_extract",
                "file": "canary_extract.py",
                "description": "fixture",
                "trigger": "manual",
                "params": "[]",
                "dag_params_example": {},
            }
        ],
        "entities": [
            {
                "entity": "Canary",
                "display_name": "Canary",
                "mode": "full",
                "primary_key": "id",
                "dag_id": "canary_extract",
                "trigger_type": "manual",
                "cron_expression": "",
                "description": "fixture",
                "dag_params": {},
            }
        ],
        "datasets": [
            {
                "name": "canary_silver",
                "layer": "silver",
                "sources": ["raw/canary"],
                "sql_def": "SELECT 1 AS synthetic",
                "description": "fixture",
                "column_mapping": {},
                "schedule": None,
            }
        ],
        "semantic_model": {
            "vocabulary": [
                {"term": "canary", "definition": "fixture", "maps_to": "id"}
            ]
        },
        "knowledge_bits": [
            {
                "kb_id": "canary_kb",
                "name": "Canary",
                "description": "fixture",
                "sql": "SELECT 1",
                "pg_table": None,
                "output_path": "silver/canary",
            }
        ],
        "custom_tools": [
            {
                "name": "canary_tool",
                "description": "fixture",
                "tool_type": "query",
                "config": {},
            }
        ],
        "analytic_apps": [
            {
                "name": "canary_app",
                "title": "Canary",
                "html": "<p>synthetic</p>",
                "description": "fixture",
            }
        ],
        "agents": [
            {
                "slug": "canary_agent",
                "name": "Canary",
                "description": "fixture",
                "instructions": "synthetic",
                "personality": "neutral",
                "allowed_tools": [],
                "rag_filter": {},
                "extra": {},
                "model": "fixture-model",
                "max_tokens": 128,
                "temperature": 0.0,
                "is_active": True,
            }
        ],
    }

    sql = _generate_seed_sql(manifest)

    assert _validate_seed_sql(sql) == "canary"
    assert "ALTER TABLE" not in sql
    assert "UPDATE cartridges" not in sql


def test_seed_sql_rejects_insert_select_exfiltration():
    with pytest.raises(ValueError, match="literal VALUES"):
        _validate_seed_sql(
            "INSERT INTO cartridges (id, name) VALUES ('canary', 'Canary');"
            "INSERT INTO analytic_apps (cartridge_id, name, html) "
            "SELECT 'canary', email, password_hash FROM users;"
        )


def test_seed_sql_rejects_global_assistant_hint_update():
    with pytest.raises(ValueError, match="only INSERT"):
        _validate_seed_sql("UPDATE cartridges SET assistant_hints = 'owned';")


@pytest.mark.parametrize(
    "payload",
    [
        "(SELECT query_to_xml('SELECT current_user', true, true, ''))",
        "dblink('host=canary.invalid', 'SELECT 1')",
        "(SELECT secret FROM users LIMIT 1)",
        "TRUE OR TRUE",
    ],
)
def test_seed_sql_rejects_function_subquery_and_or_true_canaries(payload):
    sql = (
        "INSERT INTO cartridges (id, name) VALUES ('canary', 'Canary');"
        "INSERT INTO analytic_apps (cartridge_id, name, html) "
        f"VALUES ('canary', 'probe', {payload});"
    )
    with pytest.raises(ValueError):
        _validate_seed_sql(sql)


def test_seed_sql_rejects_multiple_cartridge_ids():
    with pytest.raises(ValueError, match="one cartridge_id"):
        _validate_seed_sql(
            "INSERT INTO cartridges (id, name) VALUES ('alpha', 'Alpha');"
            "INSERT INTO entity_config (cartridge_id, entity) "
            "VALUES ('beta', 'CrossTenantCanary');"
        )


def test_seed_sql_rejects_unapproved_columns_on_allowed_table():
    with pytest.raises(ValueError, match="cannot insert column"):
        _validate_seed_sql(
            "INSERT INTO cartridges (id, name) VALUES ('canary', 'Canary');"
            "INSERT INTO cartridge_connections (cartridge_id, conn_id, token) "
            "VALUES ('canary', 'default', 'synthetic-canary');"
        )


def test_seed_sql_allows_clock_only_for_updated_at():
    with pytest.raises(ValueError, match="non-declarative value"):
        _validate_seed_sql(
            "INSERT INTO cartridges (id, name) VALUES ('canary', NOW());"
        )

    assert (
        _validate_seed_sql(
            "INSERT INTO cartridges (id, name) VALUES ('canary', 'Canary') "
            "ON CONFLICT (id) DO UPDATE "
            "SET name=EXCLUDED.name, updated_at=NOW();"
        )
        == "canary"
    )


def test_seed_sql_rejects_dag_path_canary():
    with pytest.raises(ValueError, match="DAG filename"):
        _validate_seed_sql(
            "INSERT INTO cartridges (id, name) VALUES ('canary', 'Canary');"
            "INSERT INTO cartridge_dags (cartridge_id, dag_id, file) "
            "VALUES ('canary', 'probe', '/etc/hostname');"
        )


@pytest.mark.parametrize(
    "filename",
    [
        "/etc/hostname",
        "../escape.py",
        "nested/escape.py",
        r"nested\escape.py",
        "dag\uff0fescape.py",
        "dag\uff3cescape.py",
        "d\u0430g.py",  # Cyrillic small a
        "dag.txt",
    ],
)
def test_dag_filename_rejects_absolute_traversal_separators_and_confusables(filename):
    with pytest.raises(ValueError, match="DAG filename"):
        _validate_dag_filename(filename)


def test_dag_file_resolve_stays_under_approved_root(tmp_path):
    root = tmp_path / "approved"
    root.mkdir()
    assert _resolve_dag_file(root, "extract.py") == root / "extract.py"

    outside = tmp_path / "outside.py"
    outside.write_text("# synthetic canary", encoding="utf-8")
    (root / "linked.py").symlink_to(outside)
    with pytest.raises(ValueError, match="approved root"):
        _resolve_dag_file(root, "linked.py")


def test_upload_spec_rejects_unsafe_names(monkeypatch):
    with pytest.raises(ValueError, match="invalid cartridge_id"):
        cartridge_service.upload_spec("../replicon", "openapi.yaml", "ok")
    with pytest.raises(ValueError, match="invalid filename"):
        cartridge_service.upload_spec("replicon", "../openapi.yaml", "ok")


def test_seed_sql_splitter_preserves_semicolons_inside_literals():
    statements = _split_sql_statements(
        "INSERT INTO analytic_apps (name, html) VALUES ('demo', '<script>a();</script>');"
        "UPDATE cartridges SET assistant_hints = 'usa A; luego B' WHERE id = 'replicon';"
    )

    assert len(statements) == 2
    assert "<script>a();</script>" in statements[0]
    assert "usa A; luego B" in statements[1]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE users;",
        "DELETE FROM cartridges;",
        "COPY users TO PROGRAM 'cat /etc/passwd';",
        "INSERT INTO users (email) VALUES ('x@example.com');",
        "CREATE EXTENSION dblink;",
        "DO $$ BEGIN RAISE NOTICE 'x'; END $$;",
    ],
)
def test_seed_sql_rejects_dangerous_or_unapproved_statements(sql):
    with pytest.raises(ValueError):
        _validate_seed_sql(sql)


def test_dag_import_is_rejected_before_seed_sql_runs():
    source = Path(cartridge_service.__file__).read_text(encoding="utf-8")

    disabled_check = source.index('raise ValueError("DAG import is disabled in production")')
    seed_execute = source.index("await conn.execute(sql)")
    assert disabled_check < seed_execute
    assert "async with conn.transaction()" in source


def test_dag_import_to_mcp_infra_carries_trusted_actor_context():
    source = Path(cartridge_service.__file__).read_text(encoding="utf-8")

    assert "build_security_context(actor_user)" in source
    assert "_mcp_infra_payload(" in source
    assert "\"airflow_create_dag\"" in source
