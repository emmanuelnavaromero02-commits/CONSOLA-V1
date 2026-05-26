from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_control_room_mutations_use_specific_write_permission():
    router = read("console/app/routers/control_room.py")

    assert '"control_room.write"' in router
    assert 'require_permission("workspace.access")' not in router
    for route in (
        '"/items/{item_id}/action-preview"',
        '"/items/{item_id}/action-dry-run"',
        '"/items/{item_id}/execute"',
        '"/thresholds"',
    ):
        assert route in router


def test_control_room_permission_is_registered_and_workspace_admin_can_operate():
    permissions = read("console/app/services/permissions.py")

    assert '"control_room.write"' in permissions
    role_permissions = permissions.split("ROLE_PERMISSIONS = {", 1)[1]
    workspace_admin_section = role_permissions.split('"workspace_admin": {', 1)[1].split('},', 1)[0]
    assert '"control_room.write"' in workspace_admin_section


def test_control_room_routes_are_workspace_context_enriched():
    main = read("console/app/main.py")

    assert '"/api/control-room"' in main


def test_published_apps_stay_on_console_origin_via_workspace_proxy():
    main = read("console/app/main.py")

    assert '@app.get("/apps/{name}/content"' in main
    assert "_proxy_workspace_app" in main
    assert "RedirectResponse(f\"{workspace_url}/apps" not in main
