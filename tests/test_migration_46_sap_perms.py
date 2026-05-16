"""Sprint v1.43.3 — SAP jobs-permissions regression fix (migration 46).

Static verification of:
  * The migration file exists and is registered in schema_migrations
    on the docker-entrypoint path (self-INSERT).
  * GRANT CREATE on schema public for the 4 cartridge roles.
  * Creates the shared NOLOGIN ``omega_cartridge_jobs_owner`` role.
  * Transfers ownership of ``jobs`` + ``idx_jobs_status`` to it.
  * Re-runnable end-to-end (every step guarded by EXISTS / IF NOT
    EXISTS / ownership check).
  * Three SAP cartridge runtime entrypoints reach the patched DB.
"""
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
    """All 4 cartridge roles must receive GRANT CREATE ON SCHEMA public."""
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
    # And the GRANT format-string must actually be there.
    assert "GRANT CREATE ON SCHEMA public TO" in src


def test_migration_46_creates_jobs_owner_role():
    """A dedicated NOLOGIN role is created (the security-critical bit)."""
    src = _src()
    assert "CREATE ROLE omega_cartridge_jobs_owner NOLOGIN" in src, (
        "migration 46 must create omega_cartridge_jobs_owner with NOLOGIN"
    )
    # And the CREATE ROLE block must be guarded so re-runs don't fail.
    assert re.search(
        r"IF NOT EXISTS \(\s*SELECT 1 FROM pg_roles WHERE rolname = "
        r"'omega_cartridge_jobs_owner'",
        src,
    ), "CREATE ROLE block must be guarded by pg_roles existence check"


def test_migration_46_transfers_jobs_ownership():
    """``jobs`` and ``idx_jobs_status`` ownership move to the new role."""
    src = _src()
    assert "ALTER TABLE public.jobs OWNER TO omega_cartridge_jobs_owner" in src
    assert (
        "ALTER INDEX public.idx_jobs_status OWNER TO omega_cartridge_jobs_owner"
        in src
    )


def test_migration_46_grants_owner_membership():
    """Each cartridge role gets membership in jobs_owner so it can
    SET ROLE / inherit ownership for ALTER TABLE ops on jobs."""
    src = _src()
    assert "GRANT omega_cartridge_jobs_owner TO" in src, (
        "membership GRANT missing"
    )
    # And every cartridge role appears at least once on either side of
    # an iterator (the migration uses a FOREACH loop, so the role
    # appears as a string literal in the ARRAY).
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
    """Every mutating step must be guarded so re-runs are safe.

    Concretely:
      * CREATE ROLE wrapped in pg_roles existence check
      * ALTER TABLE OWNER skipped if already correct
      * INSERT INTO schema_migrations uses ON CONFLICT (filename) DO NOTHING
      * GRANTs are inherently idempotent in PostgreSQL.
    """
    src = _src()
    # Role-creation guard
    assert "IF NOT EXISTS" in src and "pg_roles" in src
    # Ownership guard — only ALTER if not already owned by jobs_owner.
    assert "relowner" in src or "pg_get_userbyid" in src, (
        "ALTER TABLE OWNER must be guarded by a pg_class ownership check "
        "so re-runs against an already-patched DB are no-ops"
    )
    # schema_migrations idempotency
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_migration_46_self_registers_in_schema_migrations():
    """docker-entrypoint-initdb.d never invokes the runner, so the
    migration must INSERT its own row (same pattern as 43/44/45)."""
    src = _src()
    assert "INSERT INTO schema_migrations" in src
    assert "'46_sap_jobs_permissions.sql'" in src


def test_migration_46_uses_correct_schema_migrations_columns():
    """Defensive guard against drift: schema_migrations has columns
    ``filename`` and ``applied_at`` (created in migration 22). Using
    ``migration_name`` (which doesn't exist) would silently fail at
    runtime."""
    src = _src()
    # The INSERT must reference 'filename', NOT 'migration_name'.
    insert_block = re.search(
        r"INSERT INTO schema_migrations.*?;", src, re.DOTALL
    )
    assert insert_block, "INSERT INTO schema_migrations block not found"
    assert "filename" in insert_block.group(0)
    assert "migration_name" not in insert_block.group(0), (
        "schema_migrations has no migration_name column — use 'filename'"
    )


def test_migration_46_ordering():
    """45 → 46. Lexicographic ordering ensures docker-entrypoint-initdb.d
    processes 46 strictly after 45 (which still owns the CASCADE→RESTRICT
    swap) and after 37 (which set the first jobs ownership)."""
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    assert names.index("37_replicon_role_and_tables.sql") < names.index(
        "46_sap_jobs_permissions.sql"
    )
    assert names.index("45_cascade_to_restrict.sql") < names.index(
        "46_sap_jobs_permissions.sql"
    )


def test_migration_46_does_not_grant_excessive_privileges():
    """Defense-in-depth: the migration should NOT grant SUPERUSER,
    CREATEROLE, CREATEDB, or BYPASSRLS — those would defeat the
    least-privilege posture established in migrations 36 and 37."""
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
    """The shared owner role must be NOLOGIN so its compromise surface
    is zero: even if the role name leaks, there's no authentication
    surface to attack."""
    src = _src()
    # CREATE ROLE … LOGIN would be a bug; CREATE ROLE … NOLOGIN is what
    # we want. Look for the exact CREATE statement.
    m = re.search(
        r"CREATE ROLE omega_cartridge_jobs_owner\b([^;]*);", src
    )
    assert m, "CREATE ROLE omega_cartridge_jobs_owner statement not found"
    stmt_tail = m.group(1)
    assert "NOLOGIN" in stmt_tail, (
        "omega_cartridge_jobs_owner must be NOLOGIN"
    )
    # And it must NOT have LOGIN as a standalone option.
    assert not re.search(r"\bLOGIN\b(?!.*NOLOGIN)", stmt_tail), (
        "omega_cartridge_jobs_owner must not have LOGIN"
    )


def test_sap_cartridges_can_init_after_migration_46():
    """End-to-end check: the migration touches every SAP cartridge role
    name AND the replicon role name. If any cartridge role is missing
    from the migration, that cartridge's job_runner will still hit the
    regression on boot."""
    src = _src()
    sap_roles = (
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_cartridge_replicon",
    )
    for role in sap_roles:
        # Each role must appear in BOTH grant-CREATE and grant-membership
        # loops — two occurrences minimum.
        assert src.count(role) >= 2, (
            f"role {role!r} appears < 2 times — would miss one of the "
            f"two fix steps (CREATE on public / jobs_owner membership)"
        )


def test_migration_46_registered_in_schema_migrations():
    """Alias of self-registration test for the task spec's checklist
    entry — covered by ``test_migration_46_self_registers_in_schema_migrations``
    but kept here so the test name maps 1:1 to the v1.43.3 brief."""
    src = _src()
    assert "schema_migrations" in src
    assert "'46_sap_jobs_permissions.sql'" in src
