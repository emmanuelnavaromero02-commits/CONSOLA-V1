from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INIT_DIR = REPO / "infra/init"
MIGRATION = INIT_DIR / "46_sap_jobs_permissions.sql"


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_46_exists():
    assert MIGRATION.exists(), f"missing {MIGRATION}"


def test_migration_46_grants_create_on_public():
    src = _src()
    for role in (
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_cartridge_replicon",
    ):
        assert f"'{role}'" in src, (
            f"migration 46 missing GRANT CREATE entry for {role!r}"
        )
    assert "GRANT CREATE ON SCHEMA public TO" in src


def test_migration_46_creates_jobs_owner_role():
    src = _src()
    assert "CREATE ROLE omega_cartridge_jobs_owner NOLOGIN" in src, (
        "migration 46 must create omega_cartridge_jobs_owner with NOLOGIN"
    )
    assert re.search(
        r"IF NOT EXISTS \(\s*SELECT 1 FROM pg_roles WHERE rolname = "
        r"'omega_cartridge_jobs_owner'",
        src,
    ), "CREATE ROLE block must be guarded by pg_roles existence check"


def test_migration_46_transfers_jobs_ownership():
    src = _src()
    assert "ALTER TABLE public.jobs OWNER TO omega_cartridge_jobs_owner" in src
    assert (
        "ALTER INDEX public.idx_jobs_status OWNER TO omega_cartridge_jobs_owner"
        in src
    )


def test_migration_46_grants_owner_membership():
    src = _src()
    assert "GRANT omega_cartridge_jobs_owner TO" in src, (
        "membership GRANT missing"
    )
    for role in (
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_cartridge_replicon",
    ):
        assert src.count(f"'{role}'") >= 2, (
            f"role {role!r} must appear in both the CREATE-grants loop "
            f"AND the membership-grant loop"
        )


def test_migration_46_idempotent():
    src = _src()
    assert "IF NOT EXISTS" in src and "pg_roles" in src
    assert "relowner" in src or "pg_get_userbyid" in src, (
        "ALTER TABLE OWNER must be guarded by a pg_class ownership check "
        "so re-runs against an already-patched DB are no-ops"
    )
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_migration_46_self_registers_in_schema_migrations():
    src = _src()
    assert "INSERT INTO schema_migrations" in src
    assert "'46_sap_jobs_permissions.sql'" in src


def test_migration_46_uses_correct_schema_migrations_columns():
    src = _src()
    insert_block = re.search(
        r"INSERT INTO schema_migrations.*?;", src, re.DOTALL
    )
    assert insert_block, "INSERT INTO schema_migrations block not found"
    assert "filename" in insert_block.group(0)
    assert "migration_name" not in insert_block.group(0), (
        "schema_migrations has no migration_name column — use 'filename'"
    )


def test_migration_46_ordering():
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    assert names.index("37_replicon_role_and_tables.sql") < names.index(
        "46_sap_jobs_permissions.sql"
    )
    assert names.index("45_cascade_to_restrict.sql") < names.index(
        "46_sap_jobs_permissions.sql"
    )


def test_migration_46_does_not_grant_excessive_privileges():
    src = _src()
    for forbidden in (
        "SUPERUSER",
        "CREATEROLE",
        "CREATEDB",
        "BYPASSRLS",
        "WITH ADMIN OPTION",
    ):
        assert forbidden not in src, (
            f"migration 46 must not grant {forbidden!r} to cartridge roles"
        )


def test_migration_46_jobs_owner_cannot_log_in():
    src = _src()
    m = re.search(
        r"CREATE ROLE omega_cartridge_jobs_owner\b([^;]*);", src
    )
    assert m, "CREATE ROLE omega_cartridge_jobs_owner statement not found"
    stmt_tail = m.group(1)
    assert "NOLOGIN" in stmt_tail, (
        "omega_cartridge_jobs_owner must be NOLOGIN"
    )
    assert not re.search(r"\bLOGIN\b(?!.*NOLOGIN)", stmt_tail), (
        "omega_cartridge_jobs_owner must not have LOGIN"
    )


def test_sap_cartridges_can_init_after_migration_46():
    src = _src()
    sap_roles = (
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_cartridge_replicon",
    )
    for role in sap_roles:
        assert src.count(role) >= 2, (
            f"role {role!r} appears < 2 times — would miss one of the "
            f"two fix steps (CREATE on public / jobs_owner membership)"
        )


def test_migration_46_registered_in_schema_migrations():
    src = _src()
    assert "schema_migrations" in src
    assert "'46_sap_jobs_permissions.sql'" in src
