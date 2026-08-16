from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99zzy_identity_session_boundary.sql"


def test_identity_boundary_is_forward_only_and_hash_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert MIGRATION.name == "99zzy_identity_session_boundary.sql"
    assert "CREATE ROLE omega_auth" in sql
    for attribute in (
        "NOLOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOINHERIT",
        "NOBYPASSRLS",
    ):
        assert attribute in sql
    assert "omega_auth_resolve_session(text)" in sql
    assert "omega_auth_resolve_workspace_session(text, uuid)" in sql
    assert "omega_auth_logout(text, text)" in sql
    assert "TO omega_console, omega_workspace" in sql
    assert "policy_now + interval '7 days'" in sql
    assert "policy_expires_at - interval '1 day'" in sql
    assert "policy_now - interval '12 hours'" in sql
    assert "DROP COLUMN IF EXISTS token" in sql
    assert "PRIMARY KEY (token_hash)" in sql
    assert "DROP CONSTRAINT IF EXISTS user_sessions_token_hash_shape" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON public.user_sessions, public.refresh_tokens" in sql
    assert "GRANT SELECT (id, email, name, role, is_active, must_change_password, tenant_id)" in sql
    assert "password_hash" not in sql.split("TO omega_auth", 1)[0].rsplit("GRANT SELECT", 1)[-1]


def test_runtime_and_probes_have_no_user_sessions_token_column_reference() -> None:
    offenders: list[str] = []
    pattern = re.compile(r"user_sessions[^\n;]*(?:\btoken\b|\.token\b)", re.IGNORECASE)
    for root in (
        REPO / "console" / "app",
        REPO / "workspace" / "app",
        REPO / "scripts",
    ):
        for path in root.rglob("*.py"):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and "token_hash" not in line:
                    offenders.append(f"{path.relative_to(REPO)}:{line_number}")
    assert offenders == []

    aws_probe = (
        REPO / "scripts" / "tenant_isolation_aws_probe.py"
    ).read_text(encoding="utf-8")
    assert "omega_auth_create_session" in aws_probe
    assert "INSERT INTO user_sessions" not in aws_probe
