"""Sprint v1.26 (audit F11) — refinement → vault dedicated pair key.

Mirror of test_per_pair_keys_workspace.py for the refinement service.
Refinement doesn't call vault as of v1.26; the key exists so the
future call site lands on the dedicated credential and vault's
WARNING log surfaces any caller still on the legacy path.
"""
from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException


LEGACY         = "legacy_internal_key_with_more_than_thirty_two_characters_aaaa"
REFINEMENT_KEY = "refinement_to_vault_dedicated_key_64_chars_qqqqqqqqqqqqqqqqqq"
CONSOLE_KEY    = "console_to_vault_dedicated_key_64_chars_xxxxxxxxxxxxxxxxxxxxxxx"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture()
def vault_main(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_VAULT",     CONSOLE_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_REFINEMENT_TO_VAULT",  REFINEMENT_KEY)
    monkeypatch.setitem(sys.modules, "psycopg2", _module())
    monkeypatch.setitem(sys.modules, "yaml", _module(safe_load=lambda *a, **kw: {}))
    monkeypatch.setitem(
        sys.modules, "app.security",
        _module(get_internal_api_key=lambda: LEGACY),
    )
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    if hasattr(main, "_LEGACY_WARN_SEEN"):
        main._LEGACY_WARN_SEEN.clear()
    yield main
    sys.modules.pop("app.main", None)


def test_refinement_to_vault_uses_dedicated_key(vault_main):
    """Vault accepts the dedicated refinement→vault key for x-internal-service=refinement."""
    vault_main.verify_api_key(
        x_api_key=REFINEMENT_KEY, x_internal_service="refinement",
    )


def test_refinement_key_does_not_authorize_other_services(vault_main):
    """Per-pair isolation guard: refinement's key MUST NOT authorize
    console / mcp-infra / workspace requests."""
    for foreign_svc in ("console", "mcp-infra", "workspace"):
        with pytest.raises(HTTPException) as exc:
            vault_main.verify_api_key(
                x_api_key=REFINEMENT_KEY, x_internal_service=foreign_svc,
            )
        assert exc.value.status_code == 403, (
            f"refinement key wrongly authorized x-internal-service={foreign_svc}"
        )


def test_legacy_key_still_accepted_for_refinement_during_migration(vault_main):
    """Compat window: legacy INTERNAL_API_KEY is still accepted for
    x-internal-service=refinement."""
    vault_main.verify_api_key(x_api_key=LEGACY, x_internal_service="refinement")


def test_legacy_use_by_refinement_emits_warning(vault_main, caplog):
    """Migration trail: legacy fallback emits a WARNING that mentions
    refinement so operators can find the caller to roll forward."""
    with caplog.at_level(logging.WARNING, logger="vault"):
        vault_main.verify_api_key(x_api_key=LEGACY, x_internal_service="refinement")
    msgs = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert any("legacy INTERNAL_API_KEY" in m and "refinement" in m for m in msgs), (
        f"expected a WARNING about legacy key use by refinement, got: {msgs}"
    )


def test_wrong_key_for_refinement_rejected(vault_main):
    with pytest.raises(HTTPException) as exc:
        vault_main.verify_api_key(x_api_key="garbage", x_internal_service="refinement")
    assert exc.value.status_code == 403
