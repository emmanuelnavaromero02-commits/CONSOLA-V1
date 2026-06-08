from pathlib import Path


MAIN_SOURCE = Path("console/app/main.py")
ROUTERS_V1 = Path("console/app/routers/v1")
CARTRIDGE_SERVICE_SOURCE = Path("console/app/services/cartridge_service.py")


def _console_route_source() -> str:
    parts = [MAIN_SOURCE.read_text(encoding="utf-8")]
    parts.extend(
        path.read_text(encoding="utf-8")
        for path in sorted(ROUTERS_V1.glob("*.py"))
        if path.name != "__init__.py"
    )
    return "\n".join(parts)


def test_admin_handlers_use_explicit_admin_and_target_user_names():
    source = _console_route_source()

    assert 'admin_user: dict = Depends(require_permission("iam.users.write"))' in source
    assert "target_user = await _auth.create_user" in source
    assert "target_user = await _auth.update_user" in source
    assert "async def api_admin_users_create(body: dict, user:" not in source
    assert "async def api_admin_users_reinvite(user_id: int, user:" not in source


def test_no_mutable_default_body_dict_remains():
    source = _console_route_source()

    assert "body: dict = {}" not in source
    assert "body: dict | None = None" in source


def test_cartridge_service_uses_pool_instead_of_per_operation_connect():
    source = CARTRIDGE_SERVICE_SOURCE.read_text(encoding="utf-8")

    assert "asyncpg.connect" not in source
    assert "asyncpg.create_pool" in source
    assert "async def close_pool()" in source


def test_cartridge_service_exposes_entity_select_fields_to_semantic_viewer():
    source = CARTRIDGE_SERVICE_SOURCE.read_text(encoding="utf-8")

    assert "select_fields, protection, effective_dated" in source
    assert '"select_fields":   r["select_fields"] or []' in source
    assert '"fields":          r["select_fields"] or []' in source
    assert '"columns":         r["select_fields"] or []' in source
