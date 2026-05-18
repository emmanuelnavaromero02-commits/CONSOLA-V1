"""Static contracts for the legacy Studio action bridge.

The bridge exists because :8000 remains the canonical console while
the legacy Studio JavaScript is being retired gradually. It must call
real /api/studio/* endpoints and must not fabricate invisible E2E-only
DOM sentinels.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
STUDIO_HTML = REPO / "console/app/static/studio.html"
BRIDGE_JS = REPO / "console/app/static/js/studio/action-bridge.js"
BOOTSTRAP_JS = REPO / "console/app/static/js/studio/legacy-bootstrap.js"


EXPECTED_STEP_ACTIONS = [
    (2, "/api/studio/templates"),
    (2, "/api/studio/dag-graph"),
    (3, "/api/studio/entities"),
    (4, "/api/studio/silver/preview"),
    (4, "/api/studio/gold/preview"),
    (4, "/api/studio/master/preview"),
    (6, "/api/studio/semantic"),
    (7, "/api/studio/rag"),
]


EXPECTED_CLICK_ACTIONS = [
    ("grafo", "GET", "/api/studio/dag-graph"),
    ("plantillas", "GET", "/api/studio/templates"),
    ("entidad", "GET", "/api/studio/entities"),
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_action_bridge_module_exists():
    assert BRIDGE_JS.exists()
    assert BOOTSTRAP_JS.exists()


def test_studio_html_loads_bridge_after_bootstrap():
    src = _read(STUDIO_HTML)
    boot_pos = src.find("legacy-bootstrap.js")
    bridge_pos = src.find("action-bridge.js")
    assert boot_pos >= 0
    assert bridge_pos >= 0
    assert bridge_pos > boot_pos


def test_bridge_includes_every_expected_step_action():
    src = _read(BRIDGE_JS)
    for _, path in EXPECTED_STEP_ACTIONS:
        assert f'"{path}"' in src


def test_bridge_includes_real_click_actions():
    src = _read(BRIDGE_JS)
    for marker, method, path in EXPECTED_CLICK_ACTIONS:
        assert marker in src.lower()
        assert f'"{method}"' in src
        assert f'"{path}"' in src


def test_bridge_does_not_autofire_assistant_before_legacy_prompt():
    src = _read(BRIDGE_JS)
    assert "/api/studio/assistant" not in re.search(r"const CLICK_ACTIONS = \[[\s\S]*?\];", src).group(0)


def test_bridge_attaches_csrf_on_mutations():
    src = _read(BRIDGE_JS)
    assert '"X-CSRF-Token"' in src
    assert "readCookie" in src
    assert '"csrf_token"' in src


def test_bridge_uses_credentials_include():
    assert 'credentials: "include"' in _read(BRIDGE_JS)


def test_bridge_does_not_create_fake_e2e_ui():
    src = _read(BRIDGE_JS)
    forbidden = [
        "studio-e2e",
        "SELECT 1",
        "Confirmar eliminación",
        "Plantillas listas",
        "ensureSqlViewer",
        "ensureConfirmDialog",
    ]
    for token in forbidden:
        assert token not in src


def test_bridge_does_not_await_in_step_handler():
    src = _read(BRIDGE_JS)
    block = re.search(r"function patchedGoStep[\s\S]*?return original\.apply", src)
    assert block
    assert "await studioAction" not in block.group(0)
