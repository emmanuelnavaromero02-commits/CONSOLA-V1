from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import cartridge_service
from app.services.cartridge_service import _split_sql_statements, _validate_import_zip_members, _validate_seed_sql


def test_import_zip_rejects_path_traversal():
    with pytest.raises(ValueError, match="unsafe ZIP path"):
        _validate_import_zip_members(["../evil.py"])


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
        INSERT INTO cartridges (id, name) VALUES ('replicon', 'Replicon')
        ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name;
        INSERT INTO entity_config (cartridge_id, entity) VALUES ('replicon', 'Project')
        ON CONFLICT (cartridge_id, entity) DO NOTHING;
        UPDATE cartridges SET assistant_hints = 'safe' WHERE id = 'replicon';
        """
    )


def test_seed_sql_rejects_insert_select_exfiltration():
    with pytest.raises(ValueError, match="literal VALUES"):
        _validate_seed_sql(
            "INSERT INTO analytic_apps (name, html) SELECT email, password_hash FROM users;"
        )


def test_seed_sql_rejects_global_assistant_hint_update():
    with pytest.raises(ValueError, match="single cartridge"):
        _validate_seed_sql("UPDATE cartridges SET assistant_hints = 'owned';")


def test_upload_spec_rejects_unsafe_names(monkeypatch):
    with pytest.raises(ValueError, match="invalid cartridge_id"):
        cartridge_service.upload_spec("../replicon", "openapi.yaml", "ok")
    with pytest.raises(ValueError, match="invalid filename"):
        cartridge_service.upload_spec("replicon", "../openapi.yaml", "ok")


def test_upload_code_rejects_unsafe_names_before_minio(monkeypatch):
    with pytest.raises(ValueError, match="invalid cartridge_id"):
        cartridge_service.upload_code("../replicon", "extract.py", "ok")
    with pytest.raises(ValueError, match="invalid filename"):
        cartridge_service.upload_code("replicon", "../extract.py", "ok")


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
