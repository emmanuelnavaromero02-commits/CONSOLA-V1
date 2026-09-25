from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
STUDIO_HTML = REPO / "console/app/static/studio.html"
BRIDGE_JS = REPO / "console/app/static/js/studio/action-bridge.js"
BOOTSTRAP_JS = REPO / "console/app/static/js/studio/legacy-bootstrap.js"
LEGACY_JS = REPO / "console/app/static/js/studio/legacy.js"
STUDIO_MODERN_CSS = REPO / "console/app/static/css/studio-modern.css"


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


def test_bridge_stops_legacy_inline_click_handlers_for_owned_actions():
    src = _read(BRIDGE_JS)
    block = re.search(r"function hookClicks\(\)[\s\S]*?function stopInlineHandler", src)
    assert block
    assert "stopInlineHandler(event)" in block.group(0)
    assert block.group(0).find("stopInlineHandler(event)") < block.group(0).find("studioAction(")


def test_spec_upload_zone_is_wired_without_inline_handlers():
    src = _read(LEGACY_JS)
    block = re.search(r"export function makeUploadZone[\s\S]*?`;\n    \}", src)
    assert block
    assert "onclick=" not in block.group(0)
    assert "ondrop=" not in block.group(0)
    assert "onchange=" not in block.group(0)
    bridge = _read(BRIDGE_JS)
    assert "hookUploadZones" in bridge
    assert "handleSpecFile" in bridge
    assert "handleSpecDrop" in bridge
    css = _read(REPO / "console/app/static/css/studio.css")
    assert ".upload-zone input[type=file]" in css
    assert "opacity: 0" in css


def test_dag_templates_are_wired_without_inline_handlers():
    src = _read(LEGACY_JS)
    block = re.search(r"export async function loadDagTemplates[\s\S]*?catch\(e\)", src)
    assert block
    assert "onclick=" not in block.group(0)
    assert "data-template-id" in block.group(0)
    bridge = _read(BRIDGE_JS)
    assert "hookRuntimeActions" in bridge
    assert "applyDagTemplate" in bridge


def test_deploy_button_has_csp_safe_bridge_handler():
    src = _read(BRIDGE_JS)
    assert "#btn-deploy" in src
    assert "deployDag" in src
    assert "stopInlineHandler(event)" in src


def test_studio_assistant_is_collapsible_in_modern_ui():
    src = _read(STUDIO_MODERN_CSS)
    assert "body.studio-modern-ready #ai-panel" in src
    assert "display: none !important" in src
    assert "body.studio-modern-ready.studio-ai-open #ai-panel" in src
    assert "display: flex !important" in src
    assert ".studio-ai-toggle" in src
    bridge = _read(BRIDGE_JS)
    assert "hookAssistantPanel" in bridge
    assert "studio-ai-toggle" in bridge
    assert "studio-ai-close" in bridge
    assert "localStorage" in bridge
    assert "body.studio-modern-ready #ai-panel" in src
    assert "display: flex !important" in src


def test_studio_assistant_stream_uses_csrf_headers():
    src = _read(LEGACY_JS)
    block = re.search(r"export async function aiSend[\s\S]*?fetch\('/studio/chat/stream'[\s\S]*?body: JSON\.stringify", src)
    assert block
    assert "headers: jsonHeaders()" in block.group(0)
