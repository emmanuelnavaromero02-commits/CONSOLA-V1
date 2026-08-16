"""F7-4 — the credentialless app-content frame must reach its own guard.

``/apps/{name}/content`` authorises itself with the capability minted by
``/apps/{name}/embed`` (signature + Fetch-Metadata + membership + active
manifest digest, all re-checked server side, fail-closed 403). The inner
frame is credentialless, so the request carries no session cookie by design.
Before this fix the auth middleware bounced the cookie-less request to
``/login`` (``X-Frame-Options: DENY`` / ``frame-ancestors 'none'``), which
every analytic-app viewer surfaced as "localhost rechazó la conexión".

Two layers of pinning:
  * behaviour of the pure classifier (importable without the runtime stack);
  * the middleware wire-up in ``console/app/main.py`` as source text, in the
    style of ``test_public_routes_audit.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.domains.security.request_classification import (
    is_app_content_capability_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN = REPO_ROOT / "console" / "app" / "main.py"


def test_matches_only_the_content_route():
    assert is_app_content_capability_path(
        "/apps/sap_successfactors_workforce_overview/content"
    )
    assert is_app_content_capability_path("/apps/x/content")


def test_rejects_every_sibling_and_lookalike():
    for path in (
        "/apps/x",                # viewer redirect — session-gated
        "/apps/x/embed",          # wrapper — session-gated, mints the cap
        "/apps//content",         # empty app name
        "/apps/x/content/",       # trailing slash — not the route
        "/apps/x/y/content",      # extra segment
        "/apps/x/contents",       # suffix lookalike
        "/api/apps/x/content",    # api namespace
        "/apps",                  # bare prefix
        "/login",
        "",
    ):
        assert not is_app_content_capability_path(path), path


def test_middleware_passes_the_route_to_its_own_guard():
    src = MAIN.read_text(encoding="utf-8")
    gate = re.search(
        r"if \(\s*not user\s*and not is_public\s*and \(_uses_rbac_dependency\(path\)"
        r" or _is_app_content_capability_path\(path\)\)\s*\):\s*"
        r"return await call_next\(request\)",
        src,
    )
    assert gate, (
        "auth_middleware must forward /apps/{name}/content to its "
        "capability guard instead of redirecting the cookie-less frame "
        "to /login"
    )


def test_capability_subject_derives_workspace_entitlements():
    """Second half of the same break: the resolved subject must carry the
    workspace's entitled cartridges (derived server side, fail-closed), or
    ``_require_cartridge_visible`` rejects an entitled app with
    "cartridge not allowed" right after the capability admits the frame."""
    src = MAIN.read_text(encoding="utf-8")
    fn = re.search(
        r"async def _resolve_capability_subject\(.*?\n(?=\n\nasync def |\n\ndef )",
        src,
        re.DOTALL,
    )
    assert fn, "_resolve_capability_subject not found in main.py"
    body = fn.group(0)
    assert "_workspace_cartridges(" in body, (
        "capability subject must derive allowed_cartridges from the "
        "entitlement ledger"
    )
    assert re.search(r'except Exception:.*?\[\]', body, re.DOTALL), (
        "entitlement lookup failure must fail closed to an empty list"
    )


def test_grant_lookup_runs_inside_a_transaction():
    """The scope GUCs are transaction-local; without an explicit transaction
    asyncpg's per-statement autocommit discards them and the ledger read
    returns no grants (every app data read then 403s)."""
    src = MAIN.read_text(encoding="utf-8")
    fn = re.search(
        r"async def _app_grant_context\(.*?\n(?=\n\nasync def |\n\ndef )",
        src,
        re.DOTALL,
    )
    assert fn, "_app_grant_context not found in main.py"
    body = fn.group(0)
    scope_set = body.find("_set_rls_scope(")
    txn = body.find("conn.transaction()")
    assert txn != -1 and txn < scope_set, (
        "_app_grant_context must set the RLS scope inside conn.transaction() "
        "or the transaction-local GUCs never reach the grants read"
    )


def test_grants_owner_rls_repair_migration_pinned():
    """99zzy must give the SECURITY DEFINER grants role scoped read policies
    on the three FORCE-RLS tables its functions read; without them every
    activation aborts with 'cartridge is not installed for this workspace'."""
    sql = (
        REPO_ROOT / "infra" / "init"
        / "99zzy_analytic_app_grants_owner_rls_repair.sql"
    ).read_text(encoding="utf-8")
    for policy, table in (
        ("cartridge_installations_app_grants_owner_read", "cartridge_installations"),
        ("analytic_apps_app_grants_owner_read", "analytic_apps"),
        ("datasets_app_grants_owner_read", "datasets"),
    ):
        assert policy in sql, policy
        assert f"ON public.{table}" in sql, table
    assert "FOR SELECT TO omega_app_grants_owner" in sql
    assert "WITH CHECK" not in sql, "repair must stay read-only"
    assert "omega_rls_workspace_matches" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert "99zzy_analytic_app_grants_owner_rls_repair.sql" in sql


def test_content_path_is_not_blanket_public():
    """The pass-through must not creep into the public allow-lists."""
    src = MAIN.read_text(encoding="utf-8")
    block = re.search(r"_AUTH_PUBLIC_EXACT = \{(.*?)\n\}", src, re.DOTALL)
    assert block
    assert "/apps" not in block.group(1)
    prefixes = re.search(r"_AUTH_PUBLIC_PREFIX = \(([^)]*)\)", src)
    assert prefixes
    assert "/apps" not in prefixes.group(1)
