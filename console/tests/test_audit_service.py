import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.audit_service import record_event


def test_audit_user_id_accepts_only_database_integer_ids():
    from app.services.audit_service import _audit_user_id

    assert _audit_user_id(7) == 7
    assert _audit_user_id("7") == 7
    assert _audit_user_id(" 7 ") == 7
    assert _audit_user_id("11111111-1111-1111-1111-111111111111") is None
    assert _audit_user_id("system") is None
    assert _audit_user_id(True) is None


@pytest.mark.asyncio
async def test_record_event_success():
    mock_pool = AsyncMock()
    mock_pool.fetchval.side_effect = ["audit_events", True]
    with patch("app.services.audit_service.auth.pool", return_value=mock_pool):
        await record_event(
            user_id=1,
            email="test@example.com",
            action="login",
            resource_type="auth",
            request_id="rid-test",
            metadata={"key": "value"}
        )
        # Give the background task a moment to execute
        await asyncio.sleep(0.01)

        mock_pool.execute.assert_called_once()
        args = mock_pool.execute.call_args[0]
        assert "INSERT INTO audit_events" in args[0]
        assert args[1] == 1  # user_id
        assert args[2] == "test@example.com"  # email
        assert args[3] == "login"  # action
        assert args[4] == "auth"  # resource_type
        assert args[9] == "rid-test"  # request_id
        assert args[10] == '{"key": "value"}'  # metadata


@pytest.mark.asyncio
async def test_record_event_normalizes_string_user_id():
    mock_pool = AsyncMock()
    mock_pool.fetchval.side_effect = ["audit_events", True]
    with patch("app.services.audit_service.auth.pool", return_value=mock_pool):
        await record_event(user_id="42", email="test@example.com", action="login")

        args = mock_pool.execute.call_args[0]
        assert args[1] == 42

@pytest.mark.asyncio
async def test_record_event_db_failure_no_exception():
    mock_pool = AsyncMock()
    mock_pool.execute.side_effect = Exception("DB connection failed")

    with patch("app.services.audit_service.auth.pool", return_value=mock_pool):
        # This should NOT raise an exception
        try:
            await record_event(action="test")
            # Give the background task a moment to execute
            await asyncio.sleep(0.01)
        except Exception as e:
            pytest.fail(f"record_event raised an exception unexpectedly: {e}")


@pytest.mark.asyncio
async def test_record_event_critical_db_failure_raises():
    mock_pool = AsyncMock()
    mock_pool.execute.side_effect = Exception("DB connection failed")

    with patch("app.services.audit_service.auth.pool", return_value=mock_pool):
        with pytest.raises(Exception, match="DB connection failed"):
            await record_event(action="copilot.tool.test", critical=True)


@pytest.mark.asyncio
async def test_record_event_critical_missing_table_raises():
    mock_pool = AsyncMock()
    mock_pool.fetchval.return_value = None

    with patch("app.services.audit_service.auth.pool", return_value=mock_pool):
        with pytest.raises(RuntimeError, match="critical audit table"):
            await record_event(action="copilot.tool.test", critical=True)
