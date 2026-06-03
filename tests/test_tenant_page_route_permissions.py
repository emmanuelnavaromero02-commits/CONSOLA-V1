from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PAGES_PY = REPO / "console/app/routers/pages.py"
MAIN_PY = REPO / "console/app/main.py"


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
