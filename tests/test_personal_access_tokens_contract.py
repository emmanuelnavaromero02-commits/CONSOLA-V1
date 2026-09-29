from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INIT_DIR = REPO / "infra" / "init"
MIGRATION = INIT_DIR / "99zzzzzzb_personal_access_tokens.sql"
FUNCTIONS = (
    "omega_auth_create_access_token(bigint, uuid, text, text, text, text[], timestamptz)",
    "omega_auth_resolve_access_token(text, text)",
    "omega_auth_list_access_tokens(bigint)",
    "omega_auth_revoke_access_token(bigint, uuid)",
    "omega_auth_revoke_user_tokens(bigint)",
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function_body(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{name}\(.*?\n\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, name
    return match.group(0)


def test_migration_sorts_after_the_previous_latest_in_c_and_glibc_order() -> None:
    names = sorted(p.name for p in INIT_DIR.glob("*.sql"))
    previous = "99zzzzzza_talent_attrition_exposure_seed.sql"
    assert names.index(previous) < names.index(MIGRATION.name)
    collated = subprocess.run(
        ["sort"],
        input="\n".join([MIGRATION.name, previous, "99zzzzzz_analytic_app_manifest_registry_packaged_scope.sql"]),
        capture_output=True,
        text=True,
        env={"LC_ALL": "en_US.UTF-8", "PATH": "/usr/bin:/bin"},
        check=True,
    ).stdout.split()
    assert collated[-1] == MIGRATION.name


def test_token_table_is_hash_only_and_bounded() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS public.user_access_tokens" in sql
    assert "token_hash      varchar(64) NOT NULL" in sql
    assert "UNIQUE (token_hash)" in sql
    assert "CHECK (token_hash ~ '^[0-9a-f]{64}$')" in sql
    assert "CHECK (token_prefix ~ '^omega_pat_[A-Za-z0-9]{4}$')" in sql
    assert "scopes <@ ARRAY['lectura', 'acciones']::text[]" in sql
    assert "expires_at <= created_at + interval '90 days'" in sql
    assert "revoked_reason IN ('usuario', 'administrador', 'credenciales_restablecidas')" in sql
    assert "char_length(last_used_ip) <= 64" in sql
    assert "REFERENCES public.users(id) ON DELETE CASCADE" in sql
    assert "REFERENCES public.workspaces(id) ON DELETE CASCADE" in sql
    assert "REFERENCES public.tenants(id) ON DELETE CASCADE" in sql
    assert "WHERE revoked_at IS NULL" in sql
    assert re.search(r"\btoken\s+text\b", sql) is None
    assert "plaintext" not in sql.lower()


def test_token_table_is_forced_rls_and_revoked_from_service_roles() -> None:
    sql = _sql()
    assert "ALTER TABLE public.user_access_tokens ENABLE ROW LEVEL SECURITY;" in sql
    assert "ALTER TABLE public.user_access_tokens FORCE ROW LEVEL SECURITY;" in sql
    assert re.search(
        r"CREATE POLICY user_access_tokens_auth_boundary_rls ON public\.user_access_tokens\s+FOR ALL TO omega_auth",
        sql,
    )
    assert "REVOKE ALL ON public.user_access_tokens FROM PUBLIC;" in sql
    for role in ("omega_console", "omega_workspace", "omega_refinement", "omega_mcp_infra"):
        assert f"'{role}'" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_access_tokens TO omega_auth;" in sql
    assert not re.search(r"GRANT [^;]*ON public\.user_access_tokens TO (?!omega_auth)", sql)


def test_functions_are_owned_by_omega_auth_and_executable_only_by_console() -> None:
    sql = _sql()
    for signature in FUNCTIONS:
        name = signature.split("(", 1)[0]
        body = _function_body(sql, name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
        assert f"ALTER FUNCTION public.{signature} OWNER TO omega_auth;" in sql
        assert f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC;" in sql
        grants = re.findall(rf"GRANT EXECUTE ON FUNCTION public\.{re.escape(signature)} TO ([^;]+);", sql)
        assert grants == ["omega_console"], signature


def test_create_function_enforces_membership_limit_and_lock() -> None:
    body = _function_body(_sql(), "omega_auth_create_access_token")
    assert "pg_advisory_xact_lock(hashtext('omega_pat:' || p_user_id::text))" in body
    assert "ERRCODE = '22023'" in body
    assert "ERRCODE = '42501'" in body
    assert "ERRCODE = '53400'" in body
    assert "active_count >= 10" in body
    assert "subject_must_change IS TRUE" in body
    assert "NOT IN ('owner', 'super_admin', 'admin')" in body
    assert "user_workspace_roles" in body
    assert "SELECT w.tenant_id INTO bound_tenant" in body
    assert "p_tenant" not in body
    assert "LEAST(p_expires_at, policy_max_expiry)" in body


def test_resolve_reports_every_status_and_throttles_last_use() -> None:
    body = _function_body(_sql(), "omega_auth_resolve_access_token")
    for status in (
        "activo",
        "vencido",
        "revocado",
        "usuario_inactivo",
        "cambio_de_contrasena",
        "espacio_inexistente",
    ):
        assert f"'{status}'" in body
    assert "t.last_used_at < policy_now - interval '5 minutes'" in body
    returns = body.split("LANGUAGE plpgsql", 1)[0]
    assert "token_hash" not in returns.split("RETURNS TABLE", 1)[1]


def test_list_never_returns_the_hash_and_revoke_is_owner_only() -> None:
    sql = _sql()
    listing = _function_body(sql, "omega_auth_list_access_tokens")
    assert "token_hash" not in listing
    revoke = _function_body(sql, "omega_auth_revoke_access_token")
    assert "t.user_id = p_user_id" in revoke
    assert "t.revoked_at IS NULL" in revoke
    assert "revoked_reason = 'usuario'" in revoke
    assert "token_hash" not in revoke
    assert "RETURNING t.id, t.workspace_id, t.token_prefix, t.scopes, t.expires_at" in revoke


def test_list_puts_live_tokens_first_and_flags_lost_access() -> None:
    listing = _function_body(_sql(), "omega_auth_list_access_tokens")
    assert "ORDER BY (t.revoked_at IS NULL AND t.expires_at > clock_timestamp()) DESC" in listing
    assert "LIMIT 100" in listing
    assert "THEN 'sin_acceso'" in listing
    assert "u.is_active IS NOT TRUE" in listing
    assert "public.user_workspace_roles uwr" in listing
    assert "('owner', 'super_admin', 'admin')" in listing


def test_revoke_function_return_type_change_is_replay_safe() -> None:
    sql = _sql()
    guard = sql.index("prorettype = 'boolean'::regtype")
    drop = sql.index("DROP FUNCTION public.omega_auth_revoke_access_token(bigint, uuid);")
    create = sql.index("CREATE OR REPLACE FUNCTION public.omega_auth_revoke_access_token(")
    assert guard < drop < create


def test_revoke_user_tokens_keeps_signature_and_revokes_access_tokens() -> None:
    body = _function_body(_sql(), "omega_auth_revoke_user_tokens")
    assert body.startswith(
        "CREATE OR REPLACE FUNCTION public.omega_auth_revoke_user_tokens(p_user_id bigint)\nRETURNS integer"
    )
    assert "DELETE FROM public.user_sessions WHERE user_id = p_user_id" in body
    assert "DELETE FROM public.refresh_tokens WHERE user_id = p_user_id" in body
    assert "revoked_reason = 'credenciales_restablecidas'" in body


def test_migration_is_idempotent_and_registers_itself() -> None:
    sql = _sql()
    assert "DROP POLICY IF EXISTS user_access_tokens_auth_boundary_rls" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_user_access_tokens_active_user" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_user_access_tokens_workspace" in sql
    assert sql.rstrip().endswith(
        "INSERT INTO schema_migrations (filename, applied_at)\n"
        "VALUES ('99zzzzzzb_personal_access_tokens.sql', NOW())\n"
        "ON CONFLICT (filename) DO NOTHING;"
    )
