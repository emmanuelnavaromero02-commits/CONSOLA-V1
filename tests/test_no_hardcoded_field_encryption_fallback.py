"""
Sprint v1.33 — audit B1 (P0).

The three SAP cartridges (sap_hcm, sap_s4hana, sap_successfactors) used to
fall back to a hardcoded ``"change-this-key-in-prod"`` Fernet seed if the
operator forgot to provision FIELD_ENCRYPTION_KEY. That meant every PII
column (Pernr, salaries, banking, biographical) could be encrypted with a
publicly-known key — reversible by anyone with read access to a parquet
file in MinIO.

This test suite enforces, at the source level, that:

1. Each cartridge's ``protection_service`` module raises ``RuntimeError``
   when ``FIELD_ENCRYPTION_KEY`` is unset.
2. Each cartridge's ``protection_service`` module raises ``RuntimeError``
   when ``FIELD_ENCRYPTION_KEY`` is set to something that isn't a valid
   Fernet key.
3. Each cartridge's ``protection_service`` module imports cleanly and
   exposes a ``Fernet`` instance when given a valid key.
4. The literal ``"change-this-key-in-prod"`` never reappears in
   production source.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES_ROOT = REPO_ROOT / "cartridges"

SAP_CARTRIDGES = ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1")


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _prep_sys_path(cartridge_id: str) -> None:
    cart_dir = CARTRIDGES_ROOT / cartridge_id
    assert cart_dir.is_dir(), f"cartridge dir missing: {cart_dir}"
    _purge_app_modules()
    sys.path[:] = [p for p in sys.path if "/cartridges/" not in p]
    sys.path.insert(0, str(cart_dir))


def _import_protection(cartridge_id: str):
    _prep_sys_path(cartridge_id)
    return importlib.import_module("app.services.protection_service")


@pytest.mark.parametrize("cartridge_id", SAP_CARTRIDGES)
def test_protection_service_raises_when_key_missing(cartridge_id, monkeypatch):
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        _import_protection(cartridge_id)
    assert "FIELD_ENCRYPTION_KEY is required" in str(exc_info.value)


@pytest.mark.parametrize("cartridge_id", SAP_CARTRIDGES)
def test_protection_service_raises_when_key_invalid(cartridge_id, monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "not-a-valid-fernet")
    with pytest.raises(RuntimeError) as exc_info:
        _import_protection(cartridge_id)
    assert "invalid" in str(exc_info.value).lower()


@pytest.mark.parametrize("cartridge_id", SAP_CARTRIDGES)
def test_protection_service_loads_with_valid_key(cartridge_id, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key)
    mod = _import_protection(cartridge_id)
    assert isinstance(mod._FERNET, Fernet)
    # And the module's _encrypt helper round-trips through that Fernet.
    token = mod._encrypt("hello")
    assert isinstance(token, str)
    assert Fernet(key.encode("utf-8")).decrypt(token.encode("utf-8")) == b"hello"


def test_no_change_this_key_in_repo():
    """Production source must not contain the legacy hardcoded fallback."""
    bad_literal = "change-this-key-in-prod"
    production_dirs = (
        "cartridges",
        "console",
        "refinement",
        "vault",
        "workspace",
        "mcp-infra",
    )
    offenders: list[str] = []
    for top in production_dirs:
        for path in (REPO_ROOT / top).rglob("*.py"):
            # Skip test files; they may legitimately reference the literal
            # to assert it is gone.
            if path.name.startswith("test_") or "/tests/" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if bad_literal in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"Hardcoded fallback {bad_literal!r} still present in production "
        f"source: {offenders}"
    )
