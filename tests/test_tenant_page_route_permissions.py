from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PAGES_PY = REPO / "console/app/routers/pages.py"
MAIN_PY = REPO / "console/app/main.py"
SIDEBAR_TSX = REPO / "console-next/src/components/AppSidebar.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _route_block(source: str, route: str) -> str:
    pattern = re.compile(
        rf"@(?:app|router)\.get\(\s*{re.escape(route)}[\s\S]*?(?=\n@(?:app|router)\.|$)",
    )
    match = pattern.search(source)
    assert match, f"route {route} not found"
    return match.group(0)


def test_tenant_workspace_pages_do_not_require_platform_admin():
    src = _read(PAGES_PY)
    expectations = {
        '"/operations"': "operations.read",
        '"/operations/audit"': "security.audit.read",
        '"/operations/vault"': "vault.connections.read",
        '"/operations/metrics"': "operations.read",
        '"/cartridges"': "cartridges.read",
        '"/cartridges/viewer"': "cartridges.read",
        '"/copilot/tokens"': "copilot.use",
    }
    for route, permission in expectations.items():
        block = _route_block(src, route)
        assert f'require_permission("{permission}")' in block
        assert "require_admin" not in block


def test_internal_pages_keep_platform_admin_gate():
    pages_src = _read(PAGES_PY)
    main_src = _read(MAIN_PY)
    expectations = [
        (pages_src, '"/security"', "security.audit.read"),
        (pages_src, '"/settings"', "settings.read"),
        (pages_src, '"/data/bronze"', "datasets.write"),
        (main_src, '"/studio"', "studio.read"),
    ]
    for source, route, permission in expectations:
        block = _route_block(source, route)
        assert f'require_permission("{permission}")' in block
        assert "require_admin" in block


def test_visible_routes_use_ui_capabilities_that_match_backend_guards():
    sidebar = _read(SIDEBAR_TSX)
    main_src = _read(MAIN_PY)
    page_src = _read(PAGES_PY)
    expectations = (
        ("/studio", "can_view_studio"),
        ("/data/bronze", "can_view_bronze"),
        ("/operations/workflows", "can_view_workflows"),
        ("/operations/companies", "can_manage_companies"),
        ("/operations/users", "can_manage_workspace_users"),
        ("/operations/audit", "can_view_audit"),
        ("/operations/vault", "can_view_vault"),
        ("/operations/metrics", "can_view_metrics"),
    )
    for route, capability in expectations:
        assert f'"{route}"' in sidebar
        assert f'"{capability}"' in sidebar
        assert f'"{capability}"' in main_src

    viewer_block = _route_block(page_src, '"/viewer"')
    assert "Depends(_require_viewer_permission)" in viewer_block
    assert "can_view_lineage" in sidebar
    assert "/skills" not in sidebar
