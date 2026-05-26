from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_published_app_csp_avoids_unsupported_navigate_to_directive():
    source = (ROOT / "workspace/app/main.py").read_text(encoding="utf-8")
    apps_csp = source.split("_APPS_CONTENT_CSP = (", 1)[1].split(")", 1)[0]

    assert "navigate-to" not in apps_csp
    assert "frame-ancestors 'self'" in apps_csp
    assert "sandbox allow-scripts" in apps_csp
