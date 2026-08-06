"""CSP contract for published analytic apps served at /apps/{name}.

Before this, ``script-src`` carried neither a nonce nor 'unsafe-inline', so the
app's own inline bootstrap was blocked: the page rendered its chrome and nothing
else, without even an error. The fix is a per-response nonce, not a relaxation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

from app.domains.apps.embed import (  # noqa: E402
    app_content_headers,
    inject_script_nonce,
)

NONCE = "Ab3dEf6hIj9kLm2nOp5q"
OTHER = "Zz9yXx8wVv7uTt6sRr5q"
APPS_DIR = REPO / "cartridges" / "sap_successfactors" / "apps"
APP_NAMES = (
    "sap_successfactors_workforce_overview",
    "sap_successfactors_talent_health",
)


def _csp(nonce: str = NONCE) -> str:
    return app_content_headers(nonce)["Content-Security-Policy"]


def _script_src(nonce: str = NONCE) -> str:
    for directive in _csp(nonce).split(";"):
        if directive.strip().startswith("script-src"):
            return directive.strip()
    raise AssertionError("script-src missing from the published app CSP")


def test_script_src_carries_the_nonce():
    assert f"'nonce-{NONCE}'" in _script_src()


def test_script_src_has_no_unsafe_directives():
    directive = _script_src()
    assert "'unsafe-inline'" not in directive
    assert "'unsafe-eval'" not in directive
    assert "*" not in directive


def test_policy_keeps_the_rest_of_the_surface_closed():
    csp = _csp()
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


def test_distinct_nonces_produce_distinct_policies():
    assert _csp(NONCE) != _csp(OTHER)


@pytest.mark.parametrize(
    "value", ["", "short", "with space", "quote'value", "../traversal", "a" * 15]
)
def test_malformed_nonce_is_refused(value):
    """A value that did not come from the server never reaches policy or markup."""
    with pytest.raises(ValueError):
        app_content_headers(value)
    with pytest.raises(ValueError):
        inject_script_nonce("<script>1</script>", value)


def test_inline_scripts_get_the_nonce_and_external_ones_do_not():
    html = '<script src="https://cdn/x.js" integrity="sha384-x"></script><script>go()</script>'
    out = inject_script_nonce(html, NONCE)
    external, inline = re.findall(r"<script[^>]*>", out)
    assert "nonce=" not in external, "a nonce on an external script widens nothing"
    assert "integrity=" in external
    assert f'nonce="{NONCE}"' in inline


def test_injection_is_idempotent():
    once = inject_script_nonce("<script>go()</script>", NONCE)
    assert inject_script_nonce(once, NONCE) == once


@pytest.mark.parametrize("name", APP_NAMES)
def test_published_app_bootstrap_runs_under_the_policy(name):
    """End to end on the real app: every inline script ends up authorised."""
    html = (APPS_DIR / f"{name}.html").read_text(encoding="utf-8")
    stamped = inject_script_nonce(html, NONCE)
    inline = re.findall(r"<script(?![^>]*\bsrc\s*=)[^>]*>", stamped)
    assert inline, f"{name}: expected an inline bootstrap"
    assert all(f'nonce="{NONCE}"' in tag for tag in inline)
    external = re.findall(r"<script[^>]*\bsrc\s*=[^>]*>", stamped)
    assert all("nonce=" not in tag for tag in external)
