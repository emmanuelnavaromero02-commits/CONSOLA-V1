from __future__ import annotations

import re
from pathlib import Path

CARTRIDGES_ROUTER = (
    Path(__file__).resolve().parents[1]
    / "console"
    / "app"
    / "routers"
    / "cartridges.py"
)


def _router_source() -> str:
    return CARTRIDGES_ROUTER.read_text(encoding="utf-8")


def test_endpoint_registered():
    src = _router_source()
    assert 'prefix="/api/cartridges"' in src
    assert '"/{cartridge}/test_connection"' in src


def test_endpoint_requires_csrf_and_permission():
    src = _router_source()
    match = re.search(
        r'@router\.post\(\s*"/\{cartridge\}/test_connection"[\s\S]+?\)\s*\n',
        src,
    )
    assert match, "test_connection route not found"
    decorator = match.group(0)
    assert "require_csrf" in decorator
    assert 'require_permission("cartridges.write")' in decorator


def test_cartridge_port_map_complete():
    src = _router_source()
    expected = {
        "hubspot": 8210,
        "replicon": 8201,
        "sap_hcm": 8202,
        "sap_successfactors": 8203,
        "sap_s4hana": 8204,
        "salesforce": 8205,
        "sap_b1": 8206,
    }
    for cart, port in expected.items():
        assert f'"{cart}": {port}' in src, f"port map missing {cart} -> {port}"


def test_test_connection_requires_explicit_ok_status():
    src = _router_source()
    assert "def _test_connection_succeeded" in src
    assert '== "ok"' in src
    assert 'not in ("error", "fail")' not in src


def test_router_registered_in_main():
    main_src = (
        Path(__file__).resolve().parents[1] / "console" / "app" / "main.py"
    ).read_text(encoding="utf-8")
    assert "cartridges_router" in main_src
    assert "app.include_router(cartridges_router.router)" in main_src


def test_cartridge_permissions_registered():
    from pathlib import Path as _Path

    services = _Path(__file__).resolve().parents[1] / "console" / "app" / "services"
    catalog_src = (services / "permission_catalog.py").read_text(encoding="utf-8")
    permissions_src = (services / "permissions.py").read_text(encoding="utf-8")
    for permission in (
        "cartridges.read",
        "cartridges.write",
        "cartridges.execute",
    ):
        assert f'"{permission}"' in catalog_src
    assert "from app.services.permission_catalog import" in permissions_src
    assert "PERMISSION_KEYS" in permissions_src


def test_cartridge_skills_test_connection_routes_exist():
    root = Path(__file__).resolve().parents[1] / "cartridges"
    for cart in (
        "hubspot",
        "replicon",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "salesforce",
        "sap_b1",
    ):
        routes = root / cart / "app" / "api" / "routes_skills.py"
        src = routes.read_text(encoding="utf-8")
        assert (
            '@router.post("/test_connection")' in src
        ), f"{cart} missing /test_connection"
        assert "dependencies=[Depends(verify_api_key)]" in src
