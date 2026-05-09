import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from app.services.auth import authenticate

@pytest.fixture
def mock_pool():
    with patch("app.services.auth.pool", new_callable=AsyncMock) as mock:
        pool_instance = AsyncMock()
        mock.return_value = pool_instance
        yield pool_instance

@pytest.fixture
def mock_get_user():
    with patch("app.services.auth._get_user_auth_record_by_email", new_callable=AsyncMock) as mock:
        yield mock

@pytest.fixture
def mock_verify_password():
    with patch("app.services.auth.verify_password", new_callable=MagicMock) as mock:
        yield mock

@pytest.mark.asyncio
async def test_successful_login_registers_success(mock_pool, mock_get_user, mock_verify_password):
    # Setup
    mock_pool.fetchval.return_value = 0
    mock_get_user.return_value = {"id": 1, "email": "test@example.com", "is_active": True, "password_hash": "hash"}
    mock_verify_password.return_value = True

    # Action
    result = await authenticate("test@example.com", "password", "127.0.0.1")

    # Verify
    assert result is not None
    assert result["id"] == 1
    # Check that fetchval was called to check brute force
    mock_pool.fetchval.assert_called_once()
    # Check that successful login attempt was inserted
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "TRUE" in call.args[0]]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == "test@example.com"
    assert insert_calls[0].args[2] == "127.0.0.1"

@pytest.mark.asyncio
async def test_failed_login_registers_failure_wrong_password(mock_pool, mock_get_user, mock_verify_password):
    # Setup
    mock_pool.fetchval.return_value = 0
    mock_get_user.return_value = {"id": 1, "email": "test@example.com", "is_active": True, "password_hash": "hash"}
    mock_verify_password.return_value = False

    # Action
    result = await authenticate("test@example.com", "wrong_password", "127.0.0.1")

    # Verify
    assert result is None
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "FALSE" in call.args[0]]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == "test@example.com"

@pytest.mark.asyncio
async def test_failed_login_registers_failure_user_not_found(mock_pool, mock_get_user):
    # Setup
    mock_pool.fetchval.return_value = 0
    mock_get_user.return_value = None

    # Action
    result = await authenticate("test@example.com", "password", "127.0.0.1")

    # Verify
    assert result is None
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "FALSE" in call.args[0]]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == "test@example.com"

@pytest.mark.asyncio
async def test_brute_force_protection_blocks_after_5_failures(mock_pool, mock_get_user):
    # Setup
    mock_pool.fetchval.return_value = 5  # 5 failures in last 15 mins

    # Action
    with pytest.raises(HTTPException) as excinfo:
        await authenticate("test@example.com", "password", "127.0.0.1")

    # Verify
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == "Cuenta bloqueada temporalmente"
    # Ensure no further action was taken (no login queries)
    mock_get_user.assert_not_called()

@pytest.mark.asyncio
async def test_brute_force_protection_allows_after_15_minutes_expire(mock_pool, mock_get_user, mock_verify_password):
    # Setup
    mock_pool.fetchval.return_value = 4  # Block expired or only 4 failures
    mock_get_user.return_value = {"id": 1, "email": "test@example.com", "is_active": True, "password_hash": "hash"}
    mock_verify_password.return_value = True

    # Action
    result = await authenticate("test@example.com", "password", "127.0.0.1")

    # Verify
    assert result is not None
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "TRUE" in call.args[0]]
    assert len(insert_calls) == 1
