from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "console/app/main.py"
EMBED = ROOT / "console/app/domains/apps/embed.py"
V1_APPS = ROOT / "console/app/routers/v1/marketplace_apps.py"


def test_console_app_embed_uses_same_origin_wrapper_and_bridge():
    main_source = MAIN.read_text(encoding="utf-8")
    embed_source = EMBED.read_text(encoding="utf-8")
    assert '@app.get("/apps/{name}/embed"' in main_source
    assert "_app_embed_wrapper_html" in main_source
    assert "_app_embed_csp" in main_source
    assert "_app_declared_datasets(app)" in main_source
    assert "_datasets_from_app_html(html_text)" in main_source
    assert "omega-app-fetch" in embed_source
    assert "omega-app-fetch-result" in embed_source
    assert "allowedDatasets" in embed_source
    # Tightened from a startsWith() prefix check to an exact shape:
    # /api/data/<dataset> plus the two sub-resources the data API exposes,
    # so a deeper path or a traversal segment cannot ride along.
    assert "parts.length === 3" in embed_source
    assert "DATA_SUBPATHS.has(parts[3])" in embed_source
    assert 'dataset ${{dataset || "(empty)"}} not declared by app' in embed_source
    assert "sandbox=\"allow-scripts\"" in embed_source


def test_legacy_marketplace_router_exposes_same_embed_route():
    source = V1_APPS.read_text(encoding="utf-8")
    assert '@router.get("/apps/{name}/embed"' in source
    assert "_app_embed_wrapper_html" in source
    assert "_workspace_app_content_for_embed" in source
