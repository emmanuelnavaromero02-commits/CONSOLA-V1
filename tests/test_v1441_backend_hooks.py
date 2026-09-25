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


def _decorator_to_def(src: str, method: str, path: str) -> str:
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
    src = _read(CART_ROUTER)
    assert "_scrub_credential_payload" in src
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
    src = _read(CART_ROUTER)
    save_block = re.search(
        r"async def save_credentials.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = save_block.group(0) if save_block else ""
    assert "HTTPException(400" in body, (
        "save_credentials must 400 on empty payload"
    )


def test_save_credentials_validates_payload_fields_before_vault_write():
    src = _read(CART_ROUTER)
    save_block = re.search(
        r"async def save_credentials.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = save_block.group(0) if save_block else ""
    assert "_validate_credential_payload" in src
    assert "_validate_credential_payload(cartridge, body)" in body
    assert "json=credential_payload" in body


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


def test_dashboard_router_declares_kpis_endpoint():
    src = _read(DASH_ROUTER)
    assert '@router.get("/kpis"' in src
    assert "require_permission" in src


def test_dashboard_kpis_payload_includes_all_sections():
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
    for key in ("cartridges", "extractions", "data_freshness",
                "users", "copilot", "audit"):
        assert f'"{key}"' in src, (
            f"dashboard /kpis payload missing key {key!r}"
        )
    assert "_active_scoped_cartridges" in src, (
        "dashboard KPIs must scope cartridge/extraction/freshness counts to "
        "cartridges with an active scoped Vault connection"
    )


def test_dashboard_freshness_labels_handle_never_and_old():
    src = _read(DASH_ROUTER)
    for label in ('"fresh"', '"stale"', '"very_stale"', '"never"'):
        assert label in src, (
            f"dashboard freshness helper missing label {label}"
        )


def test_dashboard_freshness_uses_active_scoped_cartridge_set():
    src = _read(DASH_ROUTER)
    assert "_active_scoped_cartridges" in src
    assert "active_cartridges" in src
    assert "_freshness_per_cartridge(pool, active_cartridges)" in src
    assert "_extraction_counts(pool, active_cartridges)" in src


def test_dashboard_copilot_helper_uses_to_regclass_guard():
    src = _read(DASH_ROUTER)
    helper_block = re.search(
        r"async def _copilot_counts.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = helper_block.group(0) if helper_block else ""
    assert "to_regclass" in body
    assert "copilot_conversations" in body


def test_onboarding_router_declares_state_endpoint():
    src = _read(ONB_ROUTER)
    assert '@router.get("/state")' in src
    assert "require_authenticated" in src


def test_onboarding_state_payload_shape():
    src = _read(ONB_ROUTER)
    for key in ('"completed"', '"current_step"', '"total_steps"'):
        assert key in src, f"/state payload missing key {key}"
    assert re.search(r"TOTAL_STEPS\s*=\s*5", src), (
        "TOTAL_STEPS must be 5 — the brief documents 5 wizard steps"
    )


def test_onboarding_router_declares_complete_endpoint():
    src = _read(ONB_ROUTER)
    assert '@router.post("/complete", dependencies=[Depends(require_csrf)])' in src


def test_onboarding_complete_writes_and_audits():
    src = _read(ONB_ROUTER)
    handler = re.search(
        r"async def onboarding_complete.*?(?=^async def|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    body = handler.group(0) if handler else ""
    assert "UPDATE users SET onboarding_completed = TRUE" in body
    assert "audit_service.record_event" in body
    assert "onboarding.complete" in body


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
    src = _read(MIGRATION48)
    insert = re.search(
        r"INSERT INTO schema_migrations.*?;", src, re.DOTALL
    )
    assert insert, "schema_migrations INSERT not found"
    assert "filename" in insert.group(0)
    assert "migration_name" not in insert.group(0)


def test_migration_48_ordering():
    init = REPO / "infra/init"
    names = sorted(p.name for p in init.glob("*.sql"))
    assert names.index("47_audit_deletes_pii_sanitization.sql") < names.index(
        "48_users_onboarding_completed.sql"
    )


def test_main_includes_new_routers():
    src = _read(MAIN)
    assert "from app.routers import dashboard as dashboard_router" in src
    assert "from app.routers import onboarding as onboarding_router" in src
    assert "app.include_router(dashboard_router.router)" in src
    assert "app.include_router(onboarding_router.router)" in src
