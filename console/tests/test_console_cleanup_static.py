from pathlib import Path


MAIN_SOURCE = Path("console/app/main.py")
CARTRIDGE_SERVICE_SOURCE = Path("console/app/services/cartridge_service.py")


def test_admin_handlers_use_explicit_admin_and_target_user_names():
    source = MAIN_SOURCE.read_text(encoding="utf-8")

    assert "admin_user: dict = Depends(require_role(ROLE_ADMIN))" in source
    assert "target_user = await _auth.create_user" in source
    assert "target_user = await _auth.update_user" in source
    assert "async def api_admin_users_create(body: dict, user:" not in source
    assert "async def api_admin_users_reinvite(user_id: int, user:" not in source


def test_no_mutable_default_body_dict_remains():
    source = MAIN_SOURCE.read_text(encoding="utf-8")

    assert "body: dict = {}" not in source
    assert "body: dict | None = None" in source


def test_cartridge_service_uses_pool_instead_of_per_operation_connect():
    source = CARTRIDGE_SERVICE_SOURCE.read_text(encoding="utf-8")

    assert "asyncpg.connect" not in source
    assert "asyncpg.create_pool" in source
    assert "async def close_pool()" in source
