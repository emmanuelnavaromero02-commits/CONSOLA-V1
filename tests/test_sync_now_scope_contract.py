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


def test_sync_now_window_is_env_configurable_single_source():
    source = _main()
    assert '_env_int("SYNC_NOW_ACTIVE_WINDOW_SECONDS", 4 * 60 * 60)' in source
    assert "active_window_seconds=_SYNC_NOW_ACTIVE_WINDOW_SECONDS" in source
    sync_state = (ROOT / "console/app/domains/pipeline/sync_state.py").read_text(
        encoding="utf-8"
    )
    assert "INTERVAL '4 hours'" not in sync_state
    assert "make_interval(secs =>" in sync_state


def test_sync_now_runs_connection_check_before_dispatch():
    source = _main()
    section = source.split("async def _continue_sync_now_after_reservation", 1)[1]
    check = section.index("_sync_now_connection_check_or_response_impl(")
    seed = section.index("_seed_sync_packaged_datasets_or_response(")
    dispatch = section.index("_trigger_sync_extract_all_components(")
    assert check < seed < dispatch
    assert "connection_check=connection_check" in section


def test_sync_run_get_endpoints_attach_orchestrator_block():
    source = _main()
    for marker in (
        'sync-runs/active"',
        'sync-runs/{run_id}"',
    ):
        section = source.split(marker, 1)[1].split("@app.", 1)[0]
        assert (
            'payload["orchestrator"] = await _sync_orchestrator_block' in section
        ), marker
