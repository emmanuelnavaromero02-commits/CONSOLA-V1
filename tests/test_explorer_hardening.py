from __future__ import annotations

import ast
from pathlib import Path

from tests.console_route_source import console_route_source


ROOT = Path(__file__).resolve().parents[1]


def test_explorer_delete_requires_permission_csrf_scope_audit_and_confirmation():
    source = console_route_source()
    ast.parse(source)

    assert '"/api/explorer/object"' in source
    assert "Depends(require_csrf)" in source
    assert 'Depends(require_permission("pipelines.write"))' in source
    assert "_resolve_explorer_bucket(bucket, user)" in source
    assert "_explorer_path_allowed(key, user)" in source
    assert "strong confirmation required" in source
    assert 'action="explorer.object.delete"' in source


def test_explorer_frontend_sends_strong_delete_confirmation():
    source = (ROOT / "console/app/static/js/explorer.js").read_text(encoding="utf-8")

    assert "prompt(`Escribe la ruta completa" in source
    assert "typed !== key" in source
    assert "confirm: typed" in source
