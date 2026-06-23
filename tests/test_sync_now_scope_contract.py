from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "console/app/main.py"


def _main() -> str:
    return MAIN.read_text(encoding="utf-8")


def test_sync_now_uses_fresh_pipeline_runs_scope_columns():
    source = _main()
    upsert = source.split("async def _upsert_sync_run", 1)[1].split(
        "async def _fetch_sync_run", 1
    )[0]
    fetch = source.split("async def _fetch_sync_run", 1)[1].split(
        "def _sync_errors_retryable", 1
    )[0]
    assert '"pipeline_runs", "tenant_id", refresh=True' in upsert
    assert '"pipeline_runs", "workspace_id", refresh=True' in upsert
    assert "refresh_columns=True" in fetch


def test_sync_now_records_idempotency_key_before_extract_all():
    source = _main()
    section = source.split("async def api_cartridge_sync_now", 1)[1].split(
        '@app.get(\n    "/api/cartridges/{cartridge_id}/sync-runs/{run_id}"',
        1,
    )[0]
    assert '"idempotency_key": run_id' in section
    assert "active_row = await _fetch_active_sync_run(" in section
    assert "sync run was not recorded" in section


def test_sync_now_serializes_active_run_reservation_with_advisory_lock():
    source = _main()
    section = source.split("async def api_cartridge_sync_now", 1)[1].split(
        "extract_body = {",
        1,
    )[0]
    assert "lock_key = _sync_now_lock_key(" in section
    assert "SELECT pg_advisory_lock(hashtext($1))" in section
    assert "SELECT pg_advisory_unlock(hashtext($1))" in section
    assert section.index("active_row = await _fetch_active_sync_run(") > section.index(
        "SELECT pg_advisory_lock(hashtext($1))"
    )
    assert section.index("await _upsert_sync_run(") > section.index(
        "active_row = await _fetch_active_sync_run("
    )
