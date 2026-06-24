from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "console/app/main.py"
V1_APPS = ROOT / "console/app/routers/v1/marketplace_apps.py"


def test_console_app_embed_uses_same_origin_wrapper_and_bridge():
    source = MAIN.read_text(encoding="utf-8")
    assert '@app.get("/apps/{name}/embed"' in source
    assert "_app_embed_wrapper_html" in source
    assert "omega-app-fetch" in source
    assert "omega-app-fetch-result" in source
    assert "allowedDatasets" in source
    assert 'url.pathname.startsWith("/api/data/")' in source
    assert 'dataset ${{dataset || "(empty)"}} not declared by app' in source
    assert "_app_declared_datasets(app)" in source
    assert "_datasets_from_app_html(html_text)" in source
    assert "sandbox=\"allow-scripts\"" in source


def test_legacy_marketplace_router_exposes_same_embed_route():
    source = V1_APPS.read_text(encoding="utf-8")
    assert '@router.get("/apps/{name}/embed"' in source
    assert "_app_embed_wrapper_html" in source
    assert "_workspace_app_content_for_embed" in source
