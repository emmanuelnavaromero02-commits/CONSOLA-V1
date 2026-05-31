"""Sprint v1.44.1 backend hooks — static contracts for the endpoints
that the next session's frontend wiring will consume.

Live-DB exercise of these endpoints is covered by tests/e2e/* once
the stack is up; this file is the CI-sandbox guard that the route
declarations, RBAC dependencies, audit calls, and SQL strings all
exist and have the shape the brief documents.

Covered:
  * console/app/routers/cartridges.py — POST/DELETE /credentials
    (Tarea B backend), enhanced POST /test_connection (Codex C2
    follow-through: normalised response + audit).
  * console/app/routers/dashboard.py  — GET /api/dashboard/kpis
    (Tarea E backend).
  * console/app/routers/onboarding.py — GET /state + POST /complete
    (Tarea F backend).
  * console/app/main.py wires all three routers.
  * infra/init/48_users_onboarding_completed.sql is idempotent
    and self-registers.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO        = Path(__file__).resolve().parents[1]
CART_ROUTER = REPO / "console/app/routers/cartridges.py"
DASH_ROUTER = REPO / "console/app/routers/dashboard.py"
ONB_ROUTER  = REPO / "console/app/routers/onboarding.py"
MAIN        = REPO / "console/app/main.py"
MIGRATION48 = REPO / "infra/init/48_users_onboarding_completed.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Tarea B backend: credentials endpoints ────────────────────────────────


def _decorator_to_def(src: str, method: str, path: str) -> str:
    """Return the substring from the ``@router.<method>(... path ...)``
    decorator up to (but not including) the next ``async def``. Lets
    tests assert that the dependencies block — which spans multiple
    lines and contains its own parentheses — sits inside the route
    declaration."""
    needle = f'@router.{method}(\n    "{path}"'
    start = src.find(needle)
    if start < 0:
        return ""
    nxt = src.find("async def", start)
    return src[start:nxt] if nxt > start else src[start:]


def test_cartridges_router_declares_post_credentials():
    src = _read(CART_ROUTER)
    block = _decorator_to_def(src, "post", "/{cartridge}/credentials")
    assert block, "cartridges router missing POST /{cartridge}/credentials"
    assert "require_csrf" in block
    assert "vault.connections.write" in block


def test_cartridges_router_declares_delete_credentials():
    src = _read(CART_ROUTER)
    block = _decorator_to_def(src, "delete", "/{cartridge}/credentials")
    assert block, "cartridges router missing DELETE /{cartridge}/credentials"
    assert "require_csrf" in block
    assert "vault.connections.write" in block


def test_save_credentials_scrubs_values_before_audit():
    """The brief is explicit: ``cartridge.credentials.write`` audit
    events must record the field names but NEVER the field values.
    The scrub helper turns every non-empty value into ``"***"``."""
    src = _read(CART_ROUTER)
    assert "_scrub_credential_payload" in src
    # The scrub helper must be CALLED from the save_credentials handler.
    save_block = re.search(
        r"async def save_credentials.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert save_block, "save_credentials handler not found"
    assert "_scrub_credential_payload" in save_block.group(0), (
        "save_credentials must call _scrub_credential_payload before "
        "passing the payload to audit_service.record_event"
    )


def test_save_credentials_rejects_empty_body():
    """Sending ``{}`` would happily call the vault PUT with nothing
    to store and audit a no-op. Reject at the boundary."""
    src = _read(CART_ROUTER)
    save_block = re.search(
        r"async def save_credentials.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = save_block.group(0) if save_block else ""
    assert "HTTPException(400" in body, (
        "save_credentials must 400 on empty payload"
    )


def test_save_credentials_records_audit_event():
    src = _read(CART_ROUTER)
    save_block = re.search(
        r"async def save_credentials.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = save_block.group(0) if save_block else ""
    assert "audit_service.record_event" in body
    assert "cartridge.credentials.write" in body


def test_delete_credentials_records_audit_event():
    src = _read(CART_ROUTER)
    del_block = re.search(
        r"async def delete_credentials.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = del_block.group(0) if del_block else ""
    assert "audit_service.record_event" in body
    assert "cartridge.credentials.delete" in body


def test_test_connection_returns_normalised_shape_with_audit():
    """v1.44.1 normalised /test_connection to ``{ok, message, latency_ms}``
    and added an audit_event so the upcoming UI gets a consistent
    contract and the platform owners get a record of every probe.
    Verify by reading the handler body."""
    src = _read(CART_ROUTER)
    handler = re.search(
        r"async def test_connection.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = handler.group(0) if handler else ""
    assert '"ok": ok' in body or '"ok"' in body, (
        "test_connection response must include 'ok'"
    )
    assert "latency_ms" in body
    assert "audit_service.record_event" in body, (
        "test_connection must audit each probe (success + failure)"
    )
    assert "cartridge.test_connection" in body


# ── Tarea E backend: dashboard KPIs ──────────────────────────────────────


def test_dashboard_router_declares_kpis_endpoint():
    src = _read(DASH_ROUTER)
    assert '@router.get("/kpis")' in src
    # Authenticated only.
    assert "require_authenticated" in src


def test_dashboard_kpis_payload_includes_all_sections():
    """The brief documents six KPI groups. Each helper must exist
    and the top-level /kpis handler must reference each one."""
    src = _read(DASH_ROUTER)
    for helper in (
        "_cartridge_counts",
        "_extraction_counts",
        "_freshness_per_cartridge",
        "_user_counts",
        "_copilot_counts",
        "_audit_counts",
    ):
        assert f"async def {helper}" in src, (
            f"dashboard router missing helper {helper}"
        )
    # The top-level shape mirrors the brief:
    for key in ("cartridges", "extractions", "data_freshness",
                "users", "copilot", "audit"):
        assert f'"{key}"' in src, (
            f"dashboard /kpis payload missing key {key!r}"
        )


def test_dashboard_freshness_labels_handle_never_and_old():
    """The freshness helper maps hour-deltas to UI status codes:
    fresh / stale / very_stale / never. Without these the dashboard
    can't render the colored badges the brief calls out."""
    src = _read(DASH_ROUTER)
    for label in ('"fresh"', '"stale"', '"very_stale"', '"never"'):
        assert label in src, (
            f"dashboard freshness helper missing label {label}"
        )


def test_dashboard_freshness_covers_all_built_in_cartridges():
    """Per the brief: every cartridge appears in data_freshness even
    if it has zero extraction_runs (status='never'). The helper does
    this by iterating a constant list — verify the list contains all built-ins."""
    src = _read(DASH_ROUTER)
    cart_list_match = re.search(
        r"_CARTRIDGES\s*=\s*\(([^)]+)\)", src
    )
    assert cart_list_match, "_CARTRIDGES constant not found"
    list_body = cart_list_match.group(1)
    for cart in ("replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors"):
        assert f'"{cart}"' in list_body, (
            f"dashboard _CARTRIDGES list missing {cart}"
        )


def test_dashboard_copilot_helper_uses_to_regclass_guard():
    """The copilot_conversations table only exists post-v1.42 (mig 38).
    The helper must to_regclass-guard the SELECT so older DBs don't
    explode."""
    src = _read(DASH_ROUTER)
    helper_block = re.search(
        r"async def _copilot_counts.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = helper_block.group(0) if helper_block else ""
    assert "to_regclass" in body
    assert "copilot_conversations" in body


# ── Tarea F backend: onboarding endpoints ────────────────────────────────


def test_onboarding_router_declares_state_endpoint():
    src = _read(ONB_ROUTER)
    assert '@router.get("/state")' in src
    assert "require_authenticated" in src


def test_onboarding_state_payload_shape():
    """The brief specifies ``{completed, current_step, total_steps}``.
    The UI's wizard wrapper destructures all three; missing any
    key breaks the autostart check."""
    src = _read(ONB_ROUTER)
    for key in ('"completed"', '"current_step"', '"total_steps"'):
        assert key in src, f"/state payload missing key {key}"
    # total_steps must default to the brief's documented 5.
    assert re.search(r"TOTAL_STEPS\s*=\s*5", src), (
        "TOTAL_STEPS must be 5 — the brief documents 5 wizard steps"
    )


def test_onboarding_router_declares_complete_endpoint():
    src = _read(ONB_ROUTER)
    assert '@router.post("/complete", dependencies=[Depends(require_csrf)])' in src


def test_onboarding_complete_writes_and_audits():
    """POST /complete must (a) flip the column to TRUE, (b) record
    an audit event so we can spot suspicious re-runs."""
    src = _read(ONB_ROUTER)
    handler = re.search(
        r"async def onboarding_complete.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = handler.group(0) if handler else ""
    assert "UPDATE users SET onboarding_completed = TRUE" in body
    assert "audit_service.record_event" in body
    assert "onboarding.complete" in body


# ── Migration 48 ─────────────────────────────────────────────────────────


def test_migration_48_exists():
    assert MIGRATION48.exists()


def test_migration_48_adds_onboarding_completed_column():
    src = _read(MIGRATION48)
    assert (
        "ALTER TABLE users\n    ADD COLUMN IF NOT EXISTS onboarding_completed"
        in src
    ), "migration 48 must add onboarding_completed via IF NOT EXISTS"
    assert "NOT NULL DEFAULT FALSE" in src


def test_migration_48_backfills_existing_users():
    """Pre-v1.44.1 users shouldn't be force-walked through the wizard.
    The migration marks every existing row as completed."""
    src = _read(MIGRATION48)
    assert "UPDATE users" in src
    assert "SET onboarding_completed = TRUE" in src
    assert "WHERE onboarding_completed = FALSE" in src


def test_migration_48_idempotent_via_if_not_exists_and_onconflict():
    src = _read(MIGRATION48)
    assert "ADD COLUMN IF NOT EXISTS" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_migration_48_self_registers():
    src = _read(MIGRATION48)
    assert "INSERT INTO schema_migrations" in src
    assert "'48_users_onboarding_completed.sql'" in src


def test_migration_48_uses_filename_column_not_migration_name():
    """The schema_migrations table uses ``filename`` (per migration 22).
    A misnamed INSERT would silently 0-row in some Postgres builds
    and explode in others."""
    src = _read(MIGRATION48)
    insert = re.search(
        r"INSERT INTO schema_migrations.*?;", src, re.DOTALL
    )
    assert insert, "schema_migrations INSERT not found"
    assert "filename" in insert.group(0)
    assert "migration_name" not in insert.group(0)


def test_migration_48_ordering():
    """46 → 47 → 48: docker-entrypoint-initdb.d processes init files
    in lexicographic order, so 48 must follow 47 (PII sanitization)."""
    init = REPO / "infra/init"
    names = sorted(p.name for p in init.glob("*.sql"))
    assert names.index("47_audit_deletes_pii_sanitization.sql") < names.index(
        "48_users_onboarding_completed.sql"
    )


# ── main.py wiring ───────────────────────────────────────────────────────


def test_main_includes_new_routers():
    src = _read(MAIN)
    assert "from app.routers import dashboard as dashboard_router" in src
    assert "from app.routers import onboarding as onboarding_router" in src
    assert "app.include_router(dashboard_router.router)" in src
    assert "app.include_router(onboarding_router.router)" in src
