"""Beta-8 hardening: release version identity + Control Room ops summary."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.version import app_version

REPO = Path(__file__).resolve().parents[2]

USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_workspace_id": "workspace-A",
    "tenant_id": "tenant-A",
}


# ── Track 1: release identity is not a lie ────────────────────────────

def test_version_file_is_not_the_stale_placeholder():
    raw = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    assert raw != "1.0.0", "VERSION must be aligned with the real release, not 1.0.0"
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", raw), (
        f"VERSION must be semver-ish, got {raw!r}"
    )


def test_app_version_reads_version_file():
    assert app_version() == (REPO / "VERSION").read_text(encoding="utf-8").strip()


@pytest.mark.asyncio
async def test_healthz_reports_version_and_app_env():
    from app.main import healthz
    body = await healthz()
    assert body["ok"] is True
    assert body["service"] == "console"
    assert body["version"] == app_version()
    assert "app_env" in body


def test_system_info_and_healthz_share_one_version_source():
    # Both surfaces must resolve version through app.version.app_version so
    # they can never drift. main._console_version delegates to it.
    from app import main
    assert main._console_version() == app_version()


# ── Track 3: ops summary ──────────────────────────────────────────────

def _ops_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[
        # items by status
        [{"status": "open", "n": 3}, {"status": "approved", "n": 1}],
        # open items by severity
        [{"severity": "high", "n": 2}, {"severity": "low", "n": 1}],
        # action executions by status
        [{"status": "dry_run_validated", "n": 4}, {"status": "preview", "n": 1}],
    ])
    pool.fetchval = AsyncMock(side_effect=[
        5,  # lessons
        2,  # thresholds active
        datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),  # last_item_seen_at
    ])
    return pool


@pytest.mark.asyncio
async def test_ops_summary_shape_and_counts():
    with patch.object(control_room_service.auth, "pool", return_value=_ops_pool()):
        out = await control_room_service.ops_summary(USER)
    assert out["active_workspace"] == "workspace-A"
    assert out["version"] == app_version()
    assert "app_env" in out
    assert out["items"]["total"] == 4
    assert out["items"]["by_status"]["open"] == 3
    assert out["items"]["by_status"]["resolved"] == 0  # zero-filled
    assert out["open_items_by_severity"]["high"] == 2
    assert out["action_executions"]["dry_run_validated"] == 4
    assert out["lessons"] == 5
    assert out["thresholds_active"] == 2
    assert out["last_item_seen_at"] == "2026-05-27T12:00:00+00:00"


@pytest.mark.asyncio
async def test_ops_summary_requires_active_workspace():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await control_room_service.ops_summary({"id": 1, "email": "x@y.z"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_ops_summary_write_back_reflects_real_flag(monkeypatch):
    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    with patch.object(control_room_service.auth, "pool", return_value=_ops_pool()):
        out = await control_room_service.ops_summary(USER)
    assert out["write_back_enabled"] is False
    assert out["writeback_blocked_by_default"] is True

    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    with patch.object(control_room_service.auth, "pool", return_value=_ops_pool()):
        out = await control_room_service.ops_summary(USER)
    assert out["write_back_enabled"] is True
    assert out["writeback_blocked_by_default"] is False


@pytest.mark.asyncio
async def test_ops_summary_exposes_no_secrets():
    with patch.object(control_room_service.auth, "pool", return_value=_ops_pool()):
        out = await control_room_service.ops_summary(USER)
    blob = repr(out).lower()
    for needle in ("password", "secret", "api_key", "token", "fernet", "dsn"):
        assert needle not in blob, f"ops summary leaked {needle!r}"
