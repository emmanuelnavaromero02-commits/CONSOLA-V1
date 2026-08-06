"""Isolation contract for published analytic apps.

Published app HTML is authored outside this repository. The viewer used to
frame it directly at ``/apps/{name}`` with ``allow-same-origin``, which handed
externally-authored markup the console's own origin: parent DOM, storage,
cookies, CSRF token and every authenticated endpoint. This module pins the
three properties that close that, so none of them can be relaxed silently.

The boundary is layered on purpose:

* the viewer frames the wrapper, never the app;
* the wrapper frames the app with ``sandbox="allow-scripts"`` — no
  ``allow-same-origin``, so the app holds an opaque origin;
* the content response repeats that as a CSP ``sandbox`` directive, so the
  opaque origin survives a direct navigation that skips the wrapper entirely.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

from app.domains.apps.embed import (  # noqa: E402
    APP_BRIDGE_MAX_BODY,
    APP_DATA_SUBPATHS,
    app_bridge_script,
    app_content_headers,
    app_embed_wrapper_html,
    inject_app_bridge,
)

NONCE = "Ab3dEf6hIj9kLm2nOp5q"
MAIN = (REPO / "console/app/main.py").read_text(encoding="utf-8")
V1_APPS = (REPO / "console/app/routers/v1/marketplace_apps.py").read_text(
    encoding="utf-8"
)


def _csp() -> str:
    return app_content_headers(NONCE)["Content-Security-Policy"]


def _directive(name: str, csp: str | None = None) -> str:
    for part in (csp or _csp()).split(";"):
        part = part.strip()
        if part == name or part.startswith(f"{name} "):
            return part
    raise AssertionError(f"{name} missing from the policy")


# --- the inner boundary -----------------------------------------------------


def test_inner_frame_is_the_untrusted_content_boundary():
    wrapper = app_embed_wrapper_html("demo_app", ["demo_dataset"], NONCE)
    sandbox = re.search(r'<iframe[^>]*\bsandbox="([^"]*)"', wrapper)
    assert sandbox, "the wrapper must sandbox the app frame"
    assert sandbox.group(1).split() == ["allow-scripts"]


@pytest.mark.parametrize(
    "token",
    [
        "allow-same-origin",
        "allow-downloads",
        "allow-top-navigation",
        "allow-top-navigation-by-user-activation",
        "allow-popups",
        "allow-popups-to-escape-sandbox",
        "allow-forms",
        "allow-modals",
        "allow-storage-access-by-user-activation",
    ],
)
def test_inner_frame_never_grants_an_escape_token(token):
    """Each of these hands back part of what the sandbox exists to remove.

    ``allow-same-origin`` alone re-opens parent DOM, storage and cookies —
    the entire reported P0.
    """
    wrapper = app_embed_wrapper_html("demo_app", ["demo_dataset"], NONCE)
    sandbox = re.search(r'<iframe[^>]*\bsandbox="([^"]*)"', wrapper).group(1)
    assert token not in sandbox.split()


# --- the content response ---------------------------------------------------


def test_content_csp_sandboxes_even_without_the_wrapper():
    """Defence in depth: the opaque origin comes from the header too.

    An iframe attribute only protects content reached through the wrapper.
    Someone navigating straight to /apps/{name}/content bypasses that
    attribute entirely, so the policy has to carry the sandbox itself.
    """
    assert _directive("sandbox") == "sandbox allow-scripts"


def test_content_csp_closes_the_network():
    """The app gets no network primitive: fetch, XHR, WebSocket, EventSource,
    sendBeacon all fall under connect-src. postMessage is not a fetch
    directive, which is precisely why the broker still works."""
    assert _directive("connect-src") == "connect-src 'none'"


@pytest.mark.parametrize(
    "expected",
    [
        "object-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "default-src 'self'",
    ],
)
def test_content_csp_keeps_the_rest_closed(expected):
    assert expected in _csp()


def test_content_script_src_is_nonced_and_narrow():
    directive = _directive("script-src")
    assert f"'nonce-{NONCE}'" in directive
    assert "'unsafe-inline'" not in directive
    assert "'unsafe-eval'" not in directive
    assert "*" not in directive
    # jsDelivr is the charting runtime the packaged apps actually load.
    assert directive.count("https://") == 1
    assert "https://cdn.jsdelivr.net" in directive


# --- the broker client ------------------------------------------------------


def test_bridge_is_installed_ahead_of_the_app_bootstrap():
    """Order is the contract. An app script that runs first captures the real
    ``fetch`` before the shadow lands, and the broker becomes optional for the
    very code it is meant to contain."""
    html = "<!doctype html><html><head><title>x</title></head><body>"
    html += '<script>window.__early = typeof fetch;</script></body></html>'
    out = inject_app_bridge(html, NONCE)
    assert out.index("omegaBrokeredFetch") < out.index("window.__early")


def test_bridge_survives_html_without_a_head():
    out = inject_app_bridge("<html><body><script>go()</script></body></html>", NONCE)
    assert out.index("omegaBrokeredFetch") < out.index("go()")
    out = inject_app_bridge("<script>go()</script>", NONCE)
    assert out.index("omegaBrokeredFetch") < out.index("go()")


def test_bridge_carries_the_server_nonce_and_no_configuration():
    script = app_bridge_script(NONCE)
    assert f'nonce="{NONCE}"' in script
    # The dataset allowlist must live in the wrapper. Anything here sits in the
    # same document as the app's own scripts, so the app could edit it.
    assert "allowedDatasets" not in script
    assert "allowedSrc" not in script


@pytest.mark.parametrize("value", ["", "short", "with space", "quote'value", "a" * 15])
def test_bridge_refuses_a_malformed_nonce(value):
    with pytest.raises(ValueError):
        app_bridge_script(value)


@pytest.mark.parametrize(
    "primitive", ["XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon"]
)
def test_bridge_shadows_every_direct_network_primitive(primitive):
    assert primitive in app_bridge_script(NONCE)


def test_bridge_locks_its_own_replacements():
    """A writable override would be trivially undone by the app."""
    script = app_bridge_script(NONCE)
    assert script.count("writable: false") >= 3
    assert script.count("configurable: false") >= 3


def test_bridge_only_speaks_to_the_parent():
    script = app_bridge_script(NONCE)
    assert "event.source !== window.parent" in script
    assert "window.parent.postMessage" in script


# --- the wrapper's validation ----------------------------------------------


def _wrapper() -> str:
    return app_embed_wrapper_html("demo_app", ["demo_dataset"], NONCE)


def test_wrapper_answers_only_its_own_frame():
    assert "event.source !== frame.contentWindow" in _wrapper()


def test_wrapper_bounds_the_correlation_id():
    assert "/^appfetch:[0-9]{1,9}$/" in _wrapper()


def test_wrapper_refuses_absolute_urls_rather_than_normalising_them():
    """`new URL("https://evil/api/data/x", origin).pathname` is /api/data/x, so
    normalising would silently turn a cross-origin attempt into an allowed
    same-origin read. Refuse the shape instead."""
    wrapper = _wrapper()
    assert 'raw.startsWith("/")' in wrapper
    assert 'raw.startsWith("//")' in wrapper


def test_wrapper_requires_the_exact_dataset_path():
    """/api/data/<dataset> plus the two sub-resources the data API exposes.

    A prefix match would forward any deeper path; the tail is an allowlist so
    an unknown segment or a traversal is refused instead.
    """
    wrapper = _wrapper()
    assert 'parts[0] === "api"' in wrapper
    assert 'parts[1] === "data"' in wrapper
    assert "parts.length === 3" in wrapper
    assert "DATA_SUBPATHS.has(parts[3])" in wrapper
    assert '["options", "query"]' in wrapper


def test_data_subpaths_match_the_routes_the_console_exposes():
    """The allowlist must not drift from the API. If a new sub-resource lands
    without being listed here, apps break; if one is removed from the API but
    left here, the broker forwards a request that no longer exists."""
    main = MAIN
    exposed = set(re.findall(r'"/api/data/\{dataset\}/([a-z]+)"', main))
    assert exposed == set(APP_DATA_SUBPATHS), (exposed, APP_DATA_SUBPATHS)


def test_bridge_mirrors_the_same_subpath_allowlist():
    script = app_bridge_script(NONCE)
    assert '["options", "query"]' in script
    assert "SUBPATHS.has(parts[3])" in script


# --- storage: removed, then handed back frame-local ------------------------


def test_bridge_replaces_unavailable_storage_with_a_frame_local_stub():
    """An opaque origin has no storage, and *reading* window.localStorage
    throws there — which killed two packaged apps' bootstrap outright. The
    stub keeps them alive without returning any of the shell's storage."""
    script = app_bridge_script(NONCE)
    assert "localStorage" in script and "sessionStorage" in script
    assert "new Map()" in script
    # It must only install where the native one is unavailable, never shadow a
    # working same-origin Storage.
    assert "if (!unavailable) return;" in script
    # Nothing is read from the parent to seed it.
    assert "parent.localStorage" not in script
    assert "parent.sessionStorage" not in script


def test_wrapper_enforces_the_declared_dataset_allowlist():
    wrapper = _wrapper()
    assert "allowedDatasets.has(dataset)" in wrapper
    assert '"demo_dataset"' in wrapper


def test_wrapper_caps_the_request_body():
    wrapper = _wrapper()
    assert str(APP_BRIDGE_MAX_BODY) in wrapper
    assert "requestBody.length > MAX_BODY" in wrapper


def test_wrapper_builds_its_own_headers():
    """The app must not be able to set Authorization, Cookie or a CSRF value."""
    wrapper = _wrapper()
    assert "msg.headers" not in wrapper
    assert "const headers = {}" in wrapper


def test_wrapper_redacts_the_rejection_reason():
    """A reason that names a dataset turns a refusal into an enumeration
    primitive: the app learns which datasets exist by reading the error."""
    wrapper = _wrapper()
    assert '"request blocked by the app data broker"' in wrapper
    assert "detail: message" not in wrapper


def test_wrapper_restricts_methods():
    assert '["GET", "POST"].includes(method)' in _wrapper()


# --- the direct route -------------------------------------------------------


def test_direct_app_route_redirects_to_the_canonical_viewer():
    """`/apps/{name}` must not serve published HTML as a top-level document."""
    for source in (MAIN, V1_APPS):
        assert "/analytics/viewer?app=" in source
    # Body runs to the next top-level definition or decorator.
    served = re.search(
        r"^async def serve_app\(.*?(?=^@|^async def |^def )", MAIN, re.S | re.M
    )
    assert served, "serve_app handler not found"
    assert "RedirectResponse" in served.group(0)
    assert "_proxy_workspace_app" not in served.group(0)


def test_content_route_still_serves_through_the_proxy():
    assert "_proxy_workspace_app(request, name, content=True" in MAIN
    assert "_inject_app_bridge" in MAIN
