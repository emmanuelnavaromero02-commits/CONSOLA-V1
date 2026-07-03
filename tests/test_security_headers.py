from __future__ import annotations

from fastapi.responses import HTMLResponse

from app.services.security_headers import (
    apply_security_headers,
    inject_published_app_theme,
)


def _csp_for(path: str) -> str:
    response = HTMLResponse("")
    apply_security_headers(response, path)
    return response.headers.get("content-security-policy", "")


def test_default_pages_use_strict_script_csp():
    csp = _csp_for("/studio")

    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("style-src", 1)[0]
    assert "frame-ancestors 'none'" in csp


def test_viewer_pages_are_frameable_without_x_frame_options():
    response = HTMLResponse("")
    response.headers["X-Frame-Options"] = "DENY"

    apply_security_headers(response, "/viewer")

    assert "x-frame-options" not in response.headers
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]


def test_app_embeds_keep_sameorigin_frame_headers():
    response = HTMLResponse("")

    apply_security_headers(response, "/apps/demo/embed")

    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]


def test_published_app_theme_injection_is_idempotent():
    html = "<html><head><title>x</title></head><body><main></main></body></html>"

    once = inject_published_app_theme(html)
    twice = inject_published_app_theme(once)

    assert once == twice
    assert once.count("omega-app-theme-shim") == 1
    assert once.count("/static/js/theme-switch.js") == 1
