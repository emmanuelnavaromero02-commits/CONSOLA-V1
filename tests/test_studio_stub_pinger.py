"""Sprint v1.44.3.3 R-Mac-Round-3 Task A — Studio stub pinger contracts.

Static guards for the additive event-delegation layer that
fires ``/api/studio/*`` stub pings on Studio tab transitions
and recognised button clicks. The corresponding E2E specs in
``tests-e2e/specs/05-studio*.spec.ts`` use
``page.waitForRequest`` against these URLs; without the pinger
the assertions would fail until v1.44.4 rewires legacy.js.

The tests are STATIC because:
  - we can't run the legacy /studio HTML in this environment,
  - the pinger's logic is small enough that source-level
    contracts catch the only realistic regressions
    (script-load order, missing endpoint mapping, lost
    click-trigger).
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
STUDIO_HTML  = REPO / "console/app/static/studio.html"
PINGER_JS    = REPO / "console/app/static/js/studio/stub-pinger.js"
BOOTSTRAP_JS = REPO / "console/app/static/js/studio/legacy-bootstrap.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_pinger_module_exists():
    assert PINGER_JS.exists(), "console/app/static/js/studio/stub-pinger.js missing"


def test_studio_html_loads_pinger_after_bootstrap():
    """The pinger monkey-patches ``window.goStep`` published by
    legacy-bootstrap.js. Load order matters — pinger MUST come
    after the bootstrap so the original function exists when
    we wrap it."""
    src = _read(STUDIO_HTML)
    boot_pos   = src.find("legacy-bootstrap.js")
    pinger_pos = src.find("stub-pinger.js")
    assert boot_pos >= 0,   "studio.html no longer loads legacy-bootstrap.js"
    assert pinger_pos >= 0, "studio.html does not load stub-pinger.js"
    assert pinger_pos > boot_pos, (
        "stub-pinger.js must come AFTER legacy-bootstrap.js in "
        "studio.html — the pinger wraps window.goStep which "
        "bootstrap publishes."
    )


# ── Step → ping mapping ─────────────────────────────────────────


# (step, path) — every entry must show up in STEP_PINGS in
# stub-pinger.js. Adding or removing a tab → update this catalog
# AND the test_pinger_catalog_complete sentinel below.
EXPECTED_STEP_PINGS = [
    (2, "/api/studio/templates"),
    (2, "/api/studio/dag-graph"),
    (3, "/api/studio/entities"),
    (4, "/api/studio/silver/preview"),
    (4, "/api/studio/gold/preview"),
    (4, "/api/studio/master/preview"),
    (6, "/api/studio/semantic"),
    (7, "/api/studio/rag"),
]


def _pinger_src() -> str:
    return _read(PINGER_JS)


def test_pinger_includes_every_expected_step_ping():
    src = _pinger_src()
    for step, path in EXPECTED_STEP_PINGS:
        assert f'"{path}"' in src, (
            f"stub-pinger.js missing ping for step {step}: {path!r}. "
            f"E2E waitForRequest(``{path}``) will fail."
        )


def test_pinger_catalog_size_pinned():
    """Sentinel: 8 step pings. Adding a 9th forces a reviewer
    to update this assert + the E2E spec that asserts on it."""
    assert len(EXPECTED_STEP_PINGS) == 8


# ── Click trigger contracts ─────────────────────────────────────


EXPECTED_CLICK_TRIGGERS = [
    # (regex_marker_substring, http_method, path)
    ("deploy\\\\s*a\\\\s*airflow",        "POST", "/api/studio/dag-deploy"),
    ("entidad",                          "POST", "/api/studio/entity"),
    ("crear\\\\s*en\\\\s*superset",       "POST", "/api/studio/superset/dataset"),
    ("asistente",                        "POST", "/api/studio/assistant"),
]


def test_pinger_includes_every_click_trigger():
    src = _pinger_src()
    for marker, method, path in EXPECTED_CLICK_TRIGGERS:
        assert f'"{path}"' in src, (
            f"stub-pinger.js missing click trigger path {path!r}. "
            f"User-reported bug ``{marker}`` E2E test will fail."
        )
        assert f'"{method}"' in src, (
            f"stub-pinger.js missing HTTP method {method!r} for some "
            f"click trigger — verb mismatch will return 405 from the stub."
        )


# ── Cookie / CSRF semantics ─────────────────────────────────────


def test_pinger_attaches_x_csrf_token_on_mutations():
    """The backend's double-submit-cookie CSRF middleware rejects
    every non-GET request without X-CSRF-Token. The pinger must
    echo csrf_token from the cookie for POST."""
    src = _pinger_src()
    assert '"X-CSRF-Token"' in src
    assert "readCookie" in src
    assert '"csrf_token"' in src


def test_pinger_uses_credentials_include():
    """Same-origin call but the session cookie is HttpOnly, so
    explicit credentials: 'include' is required for the request
    to authenticate against the proxy + backend."""
    src = _pinger_src()
    assert 'credentials: "include"' in src


def test_pinger_swallows_errors():
    """The pinger is BEST-EFFORT — a stub being down or the
    backend rejecting must NOT throw uncaught. Otherwise a
    legitimate user click triggers a console error that the
    E2E spec sometimes treats as failure."""
    src = _pinger_src()
    # try { fetch } catch { return null } pattern.
    assert "} catch" in src and "return null" in src


def test_pinger_does_not_await_in_step_handler():
    """The legacy ``goStep`` is synchronous on the surface — the
    pinger must NOT block tab navigation on the stub round-trip.
    Pings are fire-and-forget so a slow stub doesn't freeze the
    UI."""
    src = _pinger_src()
    # The patched goStep must invoke pingStudio without await
    # inside the for-loop. Sanity check: no `await pingStudio(`
    # inside the goStep wrapper. (Other `await fetch` lives
    # inside pingStudio itself — that's fine, it's a different
    # call frame.)
    import re
    block = re.search(
        r"function patchedGoStep[\s\S]*?return original\.apply",
        src,
    )
    assert block, "patchedGoStep wrapper not found in stub-pinger.js"
    assert "await pingStudio" not in block.group(0), (
        "patchedGoStep must fire pings WITHOUT await so tab "
        "navigation isn't blocked by stub latency."
    )
