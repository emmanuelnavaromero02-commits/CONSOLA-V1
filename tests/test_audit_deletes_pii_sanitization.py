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
    src = _src()
    assert "CREATE OR REPLACE FUNCTION soft_delete_audit_trigger()" in src, (
        "migration 47 must rewrite soft_delete_audit_trigger() via "
        "CREATE OR REPLACE so existing trigger bindings work"
    )


def test_user_delete_strips_email_from_audit():
    src = _src()
    assert "TG_TABLE_NAME = 'users'" in src
    assert re.search(r"-\s*'email'", src), (
        "migration 47 must strip 'email' from the users snapshot"
    )


def test_user_delete_strips_name_from_audit():
    src = _src()
    assert re.search(r"-\s*'name'", src), (
        "migration 47 must strip 'name' from the users snapshot"
    )


def test_user_delete_preserves_secret_key_strip_from_v1432():
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
    src = _src()
    assert "email_hash" in src
    assert "md5(lower(email_value))" in src or "md5(lower(" in src, (
        "migration 47 must compute md5 over a normalized (lowercased) "
        "email so 'Foo@Bar' and 'foo@bar' correlate"
    )
    assert "jsonb_set(" in src, (
        "migration 47 must use jsonb_set to write email_hash into the "
        "audit_deletes JSONB column"
    )


def test_migration_47_idempotent():
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
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    assert names.index("45_cascade_to_restrict.sql") < names.index(
        "47_audit_deletes_pii_sanitization.sql"
    )
    assert names.index("46_sap_jobs_permissions.sql") < names.index(
        "47_audit_deletes_pii_sanitization.sql"
    )


def test_migration_47_extracts_email_before_stripping():
    src = _src()
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
    src_without_comments = re.sub(r"--.*?$", "", _src(), flags=re.MULTILINE)
    assert not re.search(r"\bGRANT\b", src_without_comments), (
        "migration 47 should not GRANT anything — only rewrite the "
        "trigger function. Grants on audit_deletes belong to v1.43.2."
    )
    assert not re.search(r"\bREVOKE\b", src_without_comments), (
        "migration 47 should not REVOKE anything either"
    )
