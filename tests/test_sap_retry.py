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
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if "/cartridges/" not in p]
    sys.path.insert(0, str(REPO_ROOT / "cartridges" / cartridge_id))

    import types as _t
    stub = _t.ModuleType("app.core.config")
    stub.settings = _t.SimpleNamespace()
    sys.modules["app.core.config"] = stub

    try:
        return importlib.import_module("app.core.sap_client")
    except Exception as e:
        pytest.skip(f"could not import {cartridge_id} sap_client: {e}")


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_make_retry_session_exists_in_each_cartridge(cartridge):
    mod = _load_helper(cartridge)
    assert hasattr(mod, "_make_retry_session"), (
        f"{cartridge}/app/core/sap_client.py is missing _make_retry_session"
    )
    assert callable(mod._make_retry_session)


def test_make_retry_session_is_textually_identical_across_cartridges():
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


def _adapter_from(session):
    return session.adapters["https://"]


def _retry_from(session):
    adapter = _adapter_from(session)
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
    mod = _load_helper("sap_hcm")
    retry = _retry_from(mod._make_retry_session())
    assert getattr(retry, "respect_retry_after_header", False) is True


def test_helper_accepts_max_retries_and_backoff_factor_kwargs():
    mod = _load_helper("sap_hcm")
    session = mod._make_retry_session(max_retries=5, backoff_factor=0.5)
    retry = _retry_from(session)
    assert retry.total == 5
    assert retry.backoff_factor == 0.5


def test_session_is_mounted_for_both_http_and_https():
    mod = _load_helper("sap_hcm")
    session = mod._make_retry_session()
    https_retry = session.adapters["https://"].max_retries
    http_retry = session.adapters["http://"].max_retries
    assert https_retry.total == 3
    assert http_retry.total == 3


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_sapclient_class_declares_retry_constants(cartridge):
    src = CLIENT_PATHS[cartridge].read_text(encoding="utf-8")
    assert "_RETRY_MAX = 3" in src, f"{cartridge}: _RETRY_MAX missing"
    assert "_RETRY_BACKOFF_FACTOR" in src, f"{cartridge}: _RETRY_BACKOFF_FACTOR missing"


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_sapclient_init_creates_session(cartridge):
    src = CLIENT_PATHS[cartridge].read_text(encoding="utf-8")
    assert "self._session = _make_retry_session(" in src, (
        f"{cartridge}: __init__ doesn't assign self._session"
    )


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_no_bare_requests_get_or_post_remain(cartridge):
    src = CLIENT_PATHS[cartridge].read_text(encoding="utf-8")
    bad = re.findall(
        r"\brequests\.(get|post|put|delete|head|options|patch)\(", src,
    )
    assert not bad, (
        f"{cartridge}: bare requests.* call sites remain ({bad}); "
        f"every outbound call must go through self._session"
    )
