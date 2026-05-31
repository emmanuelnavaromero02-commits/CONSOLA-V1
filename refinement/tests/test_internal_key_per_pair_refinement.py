"""Sprint v1.12 — refinement: verify_api_key accepts per-pair keys + legacy.

Refinement is called by console, workspace, airflow and the 4 cartridges.
Each pair has its own INTERNAL_API_KEY_*_TO_REFINEMENT secret. The legacy
shared INTERNAL_API_KEY also still works during the migration window.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import HTTPException


LEGACY      = "legacy_internal_key_with_more_than_thirty_two_characters_aaaa"
CONSOLE_KEY = "console_to_refinement_dedicated_key_64_chars_xxxxxxxxxxxxxxxxxx"
WS_KEY      = "workspace_to_refinement_dedicated_key_64_chars_yyyyyyyyyyyyyyy"
AF_KEY      = "airflow_to_refinement_dedicated_key_64_chars_zzzzzzzzzzzzzzzzzz"
CART_KEY    = "cartridge_to_refinement_dedicated_key_64_chars_kkkkkkkkkkkkkkk"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _GeneratedSQLValidationError(ValueError):
    pass


@pytest.fixture()
def refinement_main(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",   CONSOLE_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT", WS_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT",   AF_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT", CART_KEY)
    monkeypatch.setitem(sys.modules, "app.duckdb_engine", _module(DuckDBEngine=lambda: object()))
    monkeypatch.setitem(sys.modules, "app.dataset_store", _module(DatasetStore=lambda path: object()))

    async def generate_sql(*args, **kwargs):
        return "", ""

    monkeypatch.setitem(
        sys.modules,
        "app.llm_sql",
        _module(
            GeneratedSQLValidationError=_GeneratedSQLValidationError,
            generate_sql=generate_sql,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.security",
        _module(get_internal_api_key=lambda: LEGACY),
    )
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    yield main
    sys.modules.pop("app.main", None)


def test_pair_key_accepted_for_console_caller(refinement_main):
    refinement_main.verify_api_key(x_api_key=CONSOLE_KEY, x_internal_service="console")


def test_pair_key_for_one_pair_does_not_authorize_a_different_caller(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(x_api_key=CONSOLE_KEY, x_internal_service="workspace")
    assert exc.value.status_code == 403


def test_wrong_key_rejected(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(x_api_key="garbage", x_internal_service="console")
    assert exc.value.status_code == 403


def test_legacy_key_still_accepted(refinement_main):
    refinement_main.verify_api_key(x_api_key=LEGACY, x_internal_service="console")


def test_missing_api_key_rejected(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(x_api_key=None, x_internal_service="console")
    assert exc.value.status_code == 403


def test_unknown_service_rejected(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(x_api_key=LEGACY, x_internal_service="rogue-service")
    assert exc.value.status_code == 403


def test_workspace_caller_pair_key(refinement_main):
    refinement_main.verify_api_key(x_api_key=WS_KEY, x_internal_service="workspace")


def test_airflow_caller_pair_key(refinement_main):
    refinement_main.verify_api_key(x_api_key=AF_KEY, x_internal_service="airflow")


def test_cartridge_caller_pair_key_via_canonical_name(refinement_main):
    refinement_main.verify_api_key(x_api_key=CART_KEY, x_internal_service="cartridge-replicon")


def test_cartridge_caller_legacy_replicon_identifier_still_works(refinement_main):
    refinement_main.verify_api_key(x_api_key=CART_KEY, x_internal_service="replicon")


@pytest.fixture()
def refinement_main_prod_legacy_only(monkeypatch):
    """Production stack where only the legacy shared key is set (no pair keys)."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    for name in (
        "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",
        "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT",
        "INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT",
        "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(sys.modules, "app.duckdb_engine", _module(DuckDBEngine=lambda: object()))
    monkeypatch.setitem(sys.modules, "app.dataset_store", _module(DatasetStore=lambda path: object()))

    async def generate_sql(*args, **kwargs):
        return "", ""

    monkeypatch.setitem(
        sys.modules,
        "app.llm_sql",
        _module(
            GeneratedSQLValidationError=_GeneratedSQLValidationError,
            generate_sql=generate_sql,
        ),
    )
    monkeypatch.setitem(sys.modules, "app.security", _module(get_internal_api_key=lambda: LEGACY))
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    yield main
    sys.modules.pop("app.main", None)


def test_legacy_key_rejected_in_production(refinement_main_prod_legacy_only):
    # The legacy fallback is disabled in production: even a correct legacy key
    # is refused once APP_ENV=production and no pair key is configured.
    with pytest.raises(HTTPException) as exc:
        refinement_main_prod_legacy_only.verify_api_key(
            x_api_key=LEGACY, x_internal_service="console"
        )
    assert exc.value.status_code == 403


def test_pair_key_still_accepted_in_production(monkeypatch, refinement_main_prod_legacy_only):
    # verify_api_key reads the pair-key env live, so configuring it makes the
    # caller work even in production (only the legacy fallback is disabled).
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", CONSOLE_KEY)
    refinement_main_prod_legacy_only.verify_api_key(
        x_api_key=CONSOLE_KEY, x_internal_service="console"
    )
