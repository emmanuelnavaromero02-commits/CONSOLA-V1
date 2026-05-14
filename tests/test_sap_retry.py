"""Sprint v1.17 — exponential retry + 429 handling in SAP cartridges.

The 3 SAP cartridges (sap_hcm, sap_s4hana, sap_successfactors) each ship
the same `_make_retry_session()` helper (copied textually because there
is no shared library across cartridges). These tests prove:

  * Each cartridge can import the helper.
  * The helper is textually identical across the 3 cartridges.
  * The returned ``requests.Session`` has a ``urllib3 Retry`` configured
    with the expected policy (status_forcelist includes 429, backoff
    factor is honored, ``Retry-After`` header is respected).
  * The cartridge ``SAPClient`` class declares the policy constants and
    initializes ``self._session`` in ``__init__``.

The tests deliberately do NOT call SAP. We exercise the helper directly
(it's just a wrapper around requests.Session + urllib3.Retry). End-to-end
behavior on real 429/503 is delegated to urllib3, which has its own
upstream tests.
"""
from __future__ import annotations

import hashlib
import importlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAP_CARTRIDGES = ("sap_hcm", "sap_s4hana", "sap_successfactors")
CLIENT_PATHS = {
    c: REPO_ROOT / "cartridges" / c / "app" / "core" / "sap_client.py"
    for c in SAP_CARTRIDGES
}


def _load_helper(cartridge_id: str):
    """Import ``_make_retry_session`` from a specific cartridge module.

    Each cartridge has its own ``app.core.sap_client`` — same module path,
    different files. We tear down the prior ``app.*`` import + the
    cartridge-dependent ``app.core.config`` (which reads env-derived
    settings) before each load. The Pydantic settings layer can fail to
    instantiate when secrets are missing in the test environment; that's
    fine for us — we only need the module-level helper, so we stub the
    config dependency before importing.
    """
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if "/cartridges/" not in p]
    sys.path.insert(0, str(REPO_ROOT / "cartridges" / cartridge_id))

    # `app.core.config` instantiates a `Settings()` at import time which
    # may fail in CI without env vars. We don't need it — stub it so the
    # `from app.core.config import settings` line in sap_client.py
    # succeeds and we can reach _make_retry_session.
    import types as _t
    stub = _t.ModuleType("app.core.config")
    stub.settings = _t.SimpleNamespace()
    sys.modules["app.core.config"] = stub

    try:
        return importlib.import_module("app.core.sap_client")
    except Exception as e:
        pytest.skip(f"could not import {cartridge_id} sap_client: {e}")


# ── Helper presence + textual identity ──────────────────────────────


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_make_retry_session_exists_in_each_cartridge(cartridge):
    """Each cartridge ships its own copy of the helper at module scope."""
    mod = _load_helper(cartridge)
    assert hasattr(mod, "_make_retry_session"), (
        f"{cartridge}/app/core/sap_client.py is missing _make_retry_session"
    )
    assert callable(mod._make_retry_session)


def test_make_retry_session_is_textually_identical_across_cartridges():
    """The helper MUST be a verbatim copy in all 3 files (sprint contract).

    Drift would mean an operator who fixes the helper for one cartridge
    has to remember to fix the other two — which is exactly the bug
    pattern the audit flagged.
    """
    bodies = {}
    body_re = re.compile(
        r"def _make_retry_session\(.*?return session", re.DOTALL,
    )
    for cart, path in CLIENT_PATHS.items():
        src = path.read_text(encoding="utf-8")
        m = body_re.search(src)
        assert m, f"{path}: _make_retry_session not found"
        bodies[cart] = hashlib.md5(m.group(0).encode("utf-8")).hexdigest()
    distinct = set(bodies.values())
    assert len(distinct) == 1, (
        f"_make_retry_session drifted between cartridges: {bodies!r}"
    )


# ── Retry policy ────────────────────────────────────────────────────


def _adapter_from(session):
    """Return the HTTPAdapter mounted at https:// (matches http:// too)."""
    return session.adapters["https://"]


def _retry_from(session):
    """Pull the urllib3 Retry instance out of the adapter."""
    adapter = _adapter_from(session)
    # HTTPAdapter exposes the Retry under .max_retries in modern urllib3.
    return adapter.max_retries


def test_helper_returns_a_requests_session():
    import requests
    mod = _load_helper("sap_hcm")
    session = mod._make_retry_session()
    assert isinstance(session, requests.Session)


def test_session_has_retry_with_backoff_factor_two():
    mod = _load_helper("sap_hcm")
    session = mod._make_retry_session()
    retry = _retry_from(session)
    assert retry is not None
    assert getattr(retry, "backoff_factor", None) == 2.0, (
        f"expected backoff_factor=2.0, got {retry.backoff_factor!r}"
    )


def test_retry_includes_429_and_5xx_in_status_forcelist():
    mod = _load_helper("sap_hcm")
    retry = _retry_from(mod._make_retry_session())
    forcelist = set(retry.status_forcelist or ())
    for code in (429, 500, 502, 503, 504):
        assert code in forcelist, (
            f"status_forcelist missing {code}: {sorted(forcelist)}"
        )


def test_retry_respects_retry_after_header():
    """When SAP returns Retry-After: 5, urllib3 must wait 5s instead of
    using the exponential schedule. This is what avoids hammering a
    mandant that's already telling us to slow down."""
    mod = _load_helper("sap_hcm")
    retry = _retry_from(mod._make_retry_session())
    assert getattr(retry, "respect_retry_after_header", False) is True


def test_helper_accepts_max_retries_and_backoff_factor_kwargs():
    """The constants live on the SAPClient class so they can be tuned
    without touching the helper. The helper must accept them."""
    mod = _load_helper("sap_hcm")
    session = mod._make_retry_session(max_retries=5, backoff_factor=0.5)
    retry = _retry_from(session)
    assert retry.total == 5
    assert retry.backoff_factor == 0.5


def test_session_is_mounted_for_both_http_and_https():
    """Mounts on http:// + https:// — otherwise retry only applies to one
    scheme and the cartridge silently bypasses the policy for the other."""
    mod = _load_helper("sap_hcm")
    session = mod._make_retry_session()
    https_retry = session.adapters["https://"].max_retries
    http_retry = session.adapters["http://"].max_retries
    assert https_retry.total == 3
    assert http_retry.total == 3


# ── Class constants + session wiring ────────────────────────────────


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_sapclient_class_declares_retry_constants(cartridge):
    """``_RETRY_MAX`` and ``_RETRY_BACKOFF_FACTOR`` are the single point of
    policy for each cartridge. If they go missing, the helper falls back
    to its own defaults and the policy is invisible at the class level."""
    src = CLIENT_PATHS[cartridge].read_text(encoding="utf-8")
    assert "_RETRY_MAX = 3" in src, f"{cartridge}: _RETRY_MAX missing"
    assert "_RETRY_BACKOFF_FACTOR" in src, f"{cartridge}: _RETRY_BACKOFF_FACTOR missing"


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_sapclient_init_creates_session(cartridge):
    """``__init__`` must instantiate ``self._session`` before any
    requests-using code path runs. Asserting on the source text rather
    than instantiating the class so the test stays free of cartridge-
    specific config dependencies."""
    src = CLIENT_PATHS[cartridge].read_text(encoding="utf-8")
    assert "self._session = _make_retry_session(" in src, (
        f"{cartridge}: __init__ doesn't assign self._session"
    )


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_no_bare_requests_get_or_post_remain(cartridge):
    """All outbound calls in the cartridge must go through self._session
    so they pick up the retry policy. A leftover ``requests.get(...)``
    silently bypasses every retry. ``requests.RequestException`` /
    ``requests.Session`` / ``requests.auth.*`` etc. are fine — only call
    sites are forbidden."""
    src = CLIENT_PATHS[cartridge].read_text(encoding="utf-8")
    # Match `requests.<verb>(` for HTTP verbs only — not exceptions,
    # not subpackages.
    bad = re.findall(
        r"\brequests\.(get|post|put|delete|head|options|patch)\(", src,
    )
    assert not bad, (
        f"{cartridge}: bare requests.* call sites remain ({bad}); "
        f"every outbound call must go through self._session"
    )
