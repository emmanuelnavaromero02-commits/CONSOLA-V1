import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from app.services import auth as auth_service

@pytest.fixture
def mock_pool():
    with patch.object(auth_service, "pool", new_callable=AsyncMock) as mock:
        pool_instance = AsyncMock()
        mock.return_value = pool_instance
        yield pool_instance

@pytest.fixture
def mock_get_user():
    with patch.object(auth_service, "_get_user_auth_record_by_email", new_callable=AsyncMock) as mock:
        yield mock

@pytest.fixture
def mock_verify_password():
    with patch.object(auth_service, "verify_password", new_callable=MagicMock) as mock:
        yield mock

@pytest.mark.asyncio
async def test_successful_login_registers_success(mock_pool, mock_get_user, mock_verify_password):
    mock_pool.fetchval.return_value = 0
    mock_get_user.return_value = {"id": 1, "email": "test@example.com", "is_active": True, "password_hash": "hash"}
    mock_verify_password.return_value = True

    result = await auth_service.authenticate("test@example.com", "password", "127.0.0.1")

    assert result is not None
    assert result["id"] == 1
    mock_pool.fetchval.assert_called_once()
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "TRUE" in call.args[0]]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == "test@example.com"
    assert insert_calls[0].args[2] == "127.0.0.1"

@pytest.mark.asyncio
async def test_failed_login_registers_failure_wrong_password(mock_pool, mock_get_user, mock_verify_password):
    mock_pool.fetchval.return_value = 0
    mock_get_user.return_value = {"id": 1, "email": "test@example.com", "is_active": True, "password_hash": "hash"}
    mock_verify_password.return_value = False

    result = await auth_service.authenticate("test@example.com", "wrong_password", "127.0.0.1")

    assert result is None
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "FALSE" in call.args[0]]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == "test@example.com"

@pytest.mark.asyncio
async def test_failed_login_registers_failure_user_not_found(mock_pool, mock_get_user):
    mock_pool.fetchval.return_value = 0
    mock_get_user.return_value = None

    result = await auth_service.authenticate("test@example.com", "password", "127.0.0.1")

    assert result is None
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "FALSE" in call.args[0]]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == "test@example.com"

@pytest.mark.asyncio
async def test_brute_force_protection_blocks_after_5_failures(mock_pool, mock_get_user):
    mock_pool.fetchval.return_value = 5

    with pytest.raises(HTTPException) as excinfo:
        await auth_service.authenticate("test@example.com", "password", "127.0.0.1")

    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == "Cuenta bloqueada temporalmente"
    mock_get_user.assert_not_called()

@pytest.mark.asyncio
async def test_brute_force_protection_allows_after_15_minutes_expire(mock_pool, mock_get_user, mock_verify_password):
    mock_pool.fetchval.return_value = 4
    mock_get_user.return_value = {"id": 1, "email": "test@example.com", "is_active": True, "password_hash": "hash"}
    mock_verify_password.return_value = True

    result = await auth_service.authenticate("test@example.com", "password", "127.0.0.1")

    assert result is not None
    insert_calls = [call for call in mock_pool.execute.call_args_list if len(call.args) > 0 and isinstance(call.args[0], str) and "INSERT INTO login_attempts" in call.args[0] and "TRUE" in call.args[0]]
    assert len(insert_calls) == 1
