"""Sprint v1.43.4 — Claude H7: GDPR-aligned PII sanitization in
audit_deletes trigger (migration 47).

Static verification of:
  * Migration file exists, self-registers in schema_migrations.
  * Trigger function strips ``email`` and ``name`` from the JSONB
    snapshot when fired on the ``users`` table.
  * Retains a one-way ``email_hash = md5(lower(email))`` for
    forensic correlation.
  * Preserves the v1.43.2 secret-key strip (password_hash et al).
  * Migration is idempotent (CREATE OR REPLACE FUNCTION).
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INIT_DIR = REPO / "infra/init"
MIGRATION = INIT_DIR / "47_audit_deletes_pii_sanitization.sql"
PARENT = INIT_DIR / "45_cascade_to_restrict.sql"


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_47_exists():
    assert MIGRATION.exists(), f"missing {MIGRATION}"


def test_migration_47_rewrites_trigger_with_create_or_replace():
    """The migration must use CREATE OR REPLACE FUNCTION so re-runs
    are no-ops and the existing trigger bindings (attached in
    migration 45) keep firing."""
    src = _src()
    assert "CREATE OR REPLACE FUNCTION soft_delete_audit_trigger()" in src, (
        "migration 47 must rewrite soft_delete_audit_trigger() via "
        "CREATE OR REPLACE so existing trigger bindings work"
    )


def test_user_delete_strips_email_from_audit():
    """The whole point: ``email`` must no longer survive into
    audit_deletes.deleted_row for a users row."""
    src = _src()
    # Must reference users-table branch AND strip 'email'.
    assert "TG_TABLE_NAME = 'users'" in src
    # The stripping syntax: `- 'email'`
    assert re.search(r"-\s*'email'", src), (
        "migration 47 must strip 'email' from the users snapshot"
    )


def test_user_delete_strips_name_from_audit():
    """name is the display name on users — also PII per GDPR."""
    src = _src()
    assert re.search(r"-\s*'name'", src), (
        "migration 47 must strip 'name' from the users snapshot"
    )


def test_user_delete_preserves_secret_key_strip_from_v1432():
    """The v1.43.2 (Security R1) secret-key strip must remain in the
    new trigger body. Regression here would re-leak password_hash."""
    src = _src()
    for key in (
        "password_hash",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "private_key",
        "session_token",
        "csrf_secret",
        "mfa_secret",
        "totp_secret",
    ):
        assert f"- '{key}'" in src, (
            f"migration 47 dropped the v1.43.2 strip for {key!r}"
        )


def test_user_delete_keeps_email_hash_for_correlation():
    """Forensic correlation (fraud investigation, "has this email
    been seen before?") must still work without retaining the
    cleartext. Migration 47 stores md5(lower(email)) as
    ``email_hash`` instead."""
    src = _src()
    assert "email_hash" in src
    assert "md5(lower(email_value))" in src or "md5(lower(" in src, (
        "migration 47 must compute md5 over a normalized (lowercased) "
        "email so 'Foo@Bar' and 'foo@bar' correlate"
    )
    # And the hash must be inserted into the JSONB snapshot via
    # jsonb_set (not just printed to the logs).
    assert "jsonb_set(" in src, (
        "migration 47 must use jsonb_set to write email_hash into the "
        "audit_deletes JSONB column"
    )


def test_migration_47_idempotent():
    """Re-running must be a no-op: the schema_migrations INSERT uses
    ON CONFLICT DO NOTHING, the function is CREATE OR REPLACE."""
    src = _src()
    assert "ON CONFLICT (filename) DO NOTHING" in src
    assert "CREATE OR REPLACE FUNCTION" in src


def test_migration_47_self_registers():
    src = _src()
    assert "INSERT INTO schema_migrations" in src
    assert "'47_audit_deletes_pii_sanitization.sql'" in src
    assert "filename" in src and "migration_name" not in src, (
        "schema_migrations has no migration_name column — use 'filename'"
    )


def test_migration_47_ordering():
    """46 → 47: lexicographic ordering ensures docker-entrypoint-initdb.d
    processes 47 strictly after 46 (which also self-registers)."""
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    assert names.index("45_cascade_to_restrict.sql") < names.index(
        "47_audit_deletes_pii_sanitization.sql"
    )
    assert names.index("46_sap_jobs_permissions.sql") < names.index(
        "47_audit_deletes_pii_sanitization.sql"
    )


def test_migration_47_extracts_email_before_stripping():
    """Subtle correctness check: the function must pull email VALUE
    out of the snapshot BEFORE stripping it, or the md5 would always
    be md5(NULL). The migration achieves this by reading
    ``snapshot ->> 'email'`` into a TEXT variable first."""
    src = _src()
    # The email_value extraction must happen before the strip.
    extract_pos = src.find("email_value :=")
    strip_pos = src.find("- 'email'")
    assert extract_pos != -1, (
        "migration 47 must extract email into email_value before strip"
    )
    assert extract_pos < strip_pos, (
        "migration 47 strips email BEFORE extracting its value — "
        "md5(NULL) would be the result (correctness bug)"
    )


def test_migration_47_does_not_break_v1_43_2_audit_deletes_grants():
    """Sanity check: migration 47 must NOT touch audit_deletes
    permissions. v1.43.2 carefully REVOKE'd UPDATE/DELETE from
    omega_console; we should not silently re-grant them here."""
    # Strip SQL comments so the reference to v1.43.2's REVOKE in the
    # migration's prose doesn't trip the test.
    src_without_comments = re.sub(r"--.*?$", "", _src(), flags=re.MULTILINE)
    assert not re.search(r"\bGRANT\b", src_without_comments), (
        "migration 47 should not GRANT anything — only rewrite the "
        "trigger function. Grants on audit_deletes belong to v1.43.2."
    )
    assert not re.search(r"\bREVOKE\b", src_without_comments), (
        "migration 47 should not REVOKE anything either"
    )
