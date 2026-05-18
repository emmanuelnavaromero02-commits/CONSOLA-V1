"""Unit tests for app.services.settings_service.

Patterns mirror console/tests/test_audit_service.py:
- patch `auth.pool` with a return_value=AsyncMock() so `await auth.pool()` yields
  the mock. asyncpg's fetch/fetchrow/execute become AsyncMock children.
- spy on audit_service.record_event to avoid touching the audit_events table.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import settings_service


def _row(key: str, value, is_secret: bool, category: str = "test", description: str = ""):
    return {
        "key": key,
        "value": value,
        "is_secret": is_secret,
        "category": category,
        "description": description,
        "updated_at": datetime(2026, 1, 1),
    }


@pytest.mark.asyncio
async def test_list_settings_masks_secret_when_include_secrets_false():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [
        _row("plain", "visible", False),
        _row("secret_key", "hex-value", True),
    ]
    with patch.object(settings_service.auth, "pool", return_value=mock_pool):
        result = await settings_service.list_settings()
    assert result[0]["value"] == "visible"
    assert result[1]["value"] == "***"


@pytest.mark.asyncio
async def test_list_settings_returns_real_value_when_include_secrets_true():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [_row("secret_key", "hex-value", True)]
    with patch.object(settings_service.auth, "pool", return_value=mock_pool):
        result = await settings_service.list_settings(include_secrets=True)
    assert result[0]["value"] == "hex-value"


@pytest.mark.asyncio
async def test_list_settings_filters_by_category():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    with patch.object(settings_service.auth, "pool", return_value=mock_pool):
        await settings_service.list_settings(category="integrations")
    args = mock_pool.fetch.call_args[0]
    assert "WHERE category = $1" in args[0]
    assert args[1] == "integrations"


@pytest.mark.asyncio
async def test_set_setting_updates_and_audits():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _row("airflow_connection_mode", "real", False, category="integrations")
    with patch.object(settings_service.auth, "pool", return_value=mock_pool), \
         patch.object(settings_service.audit_service, "record_event", new=AsyncMock()) as mock_audit:
        result = await settings_service.set_setting(
            "airflow_connection_mode", "real", user_id=1, user_email="a@b.com",
        )
    assert result["value"] == "real"
    # fetchrow(SQL, $1=key, $2=json_value, $3=user_id)
    args = mock_pool.fetchrow.call_args[0]
    assert args[1] == "airflow_connection_mode"
    assert args[2] == json.dumps("real")
    assert args[3] == 1  # updated_by
    mock_audit.assert_awaited_once()
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["action"] == "settings.update"
    assert audit_kwargs["resource_id"] == "airflow_connection_mode"
    assert audit_kwargs["metadata"] == {"is_secret": False}


@pytest.mark.asyncio
async def test_set_setting_raises_keyerror_when_missing():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    with patch.object(settings_service.auth, "pool", return_value=mock_pool), \
         patch.object(settings_service.audit_service, "record_event", new=AsyncMock()):
        with pytest.raises(KeyError):
            await settings_service.set_setting("nonexistent", "x", user_id=1)


@pytest.mark.asyncio
async def test_set_setting_masks_secret_value_on_return():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _row("replicon_token", "real-token", True, category="integrations")
    with patch.object(settings_service.auth, "pool", return_value=mock_pool), \
         patch.object(settings_service.audit_service, "record_event", new=AsyncMock()):
        result = await settings_service.set_setting("replicon_token", "real-token", user_id=1)
    assert result["value"] == "***"


@pytest.mark.asyncio
async def test_reveal_setting_returns_real_value_and_audits():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _row("replicon_token", "real-token", True, category="integrations")
    with patch.object(settings_service.auth, "pool", return_value=mock_pool), \
         patch.object(settings_service.audit_service, "record_event", new=AsyncMock()) as mock_audit:
        result = await settings_service.reveal_setting("replicon_token", user_id=1, user_email="a@b.com")
    assert result["value"] == "real-token"
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["action"] == "settings.reveal"


@pytest.mark.asyncio
async def test_reveal_setting_returns_none_when_missing():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    with patch.object(settings_service.auth, "pool", return_value=mock_pool), \
         patch.object(settings_service.audit_service, "record_event", new=AsyncMock()) as mock_audit:
        result = await settings_service.reveal_setting("nope", user_id=1)
    assert result is None
    mock_audit.assert_not_called()


@pytest.mark.asyncio
async def test_rotate_secret_generates_64_hex_chars_and_audits():
    """openssl rand -hex 32 → 64 hex chars."""
    mock_pool = AsyncMock()
    # set_setting fetches updated row
    mock_pool.fetchrow.return_value = _row("internal_api_key", "newhex", True, category="security")
    with patch.object(settings_service.auth, "pool", return_value=mock_pool), \
         patch.object(settings_service.audit_service, "record_event", new=AsyncMock()) as mock_audit:
        await settings_service.rotate_secret("internal_api_key", user_id=1, user_email="a@b.com")
    # fetchrow(SQL, $1=key, $2=json_value, $3=user_id). $2 must decode to 64 hex chars.
    sent_value = json.loads(mock_pool.fetchrow.call_args[0][2])
    assert len(sent_value) == 64
    assert all(c in "0123456789abcdef" for c in sent_value)
    # Both audit events fired: update + rotate
    actions = [c.kwargs["action"] for c in mock_audit.call_args_list]
    assert "settings.update" in actions
    assert "settings.rotate" in actions
