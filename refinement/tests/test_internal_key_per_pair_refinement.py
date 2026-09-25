from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException


LEGACY = "legacy_internal_key_with_more_than_thirty_two_characters_aaaa"
CONSOLE_KEY = "console_to_refinement_dedicated_key_64_chars_xxxxxxxxxxxxxxxxxx"
WS_KEY = "workspace_to_refinement_dedicated_key_64_chars_yyyyyyyyyyyyyyy"
AF_KEY = "airflow_to_refinement_dedicated_key_64_chars_zzzzzzzzzzzzzzzzzz"
CART_KEY = "cartridge_to_refinement_dedicated_key_64_chars_kkkkkkkkkkkkkkk"
MCP_KEY = "mcp_infra_to_refinement_dedicated_key_64_chars_mmmmmmmmmmmmm"
REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _force_refinement_import_path() -> None:
    sys.path[:] = [
        p for p in sys.path if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(REPO_ROOT / "refinement"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


class _GeneratedSQLValidationError(ValueError):
    pass


class _DuckDBEngineStub:
    pass


@pytest.fixture()
def refinement_main(monkeypatch):
    saved_path = list(sys.path)
    _force_refinement_import_path()
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", CONSOLE_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT", WS_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT", AF_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT", CART_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT", MCP_KEY)
    monkeypatch.setitem(
        sys.modules, "app.duckdb_engine", _module(DuckDBEngine=_DuckDBEngineStub)
    )
    monkeypatch.setitem(
        sys.modules, "app.dataset_store", _module(DatasetStore=lambda path: object())
    )

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
    main = importlib.import_module("app.main")
    yield main
    sys.path[:] = saved_path
    _purge_app_modules()


def test_pair_key_accepted_for_console_caller(refinement_main):
    refinement_main.verify_api_key(x_api_key=CONSOLE_KEY, x_internal_service="console")


def test_pair_key_for_one_pair_does_not_authorize_a_different_caller(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(
            x_api_key=CONSOLE_KEY, x_internal_service="workspace"
        )
    assert exc.value.status_code == 403


def test_wrong_key_rejected(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(
            x_api_key="garbage", x_internal_service="console"
        )
    assert exc.value.status_code == 403


def test_legacy_key_still_accepted(refinement_main):
    refinement_main.verify_api_key(x_api_key=LEGACY, x_internal_service="console")


def test_missing_api_key_rejected(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(x_api_key=None, x_internal_service="console")
    assert exc.value.status_code == 403


def test_unknown_service_rejected(refinement_main):
    with pytest.raises(HTTPException) as exc:
        refinement_main.verify_api_key(
            x_api_key=LEGACY, x_internal_service="rogue-service"
        )
    assert exc.value.status_code == 403


def test_workspace_caller_pair_key(refinement_main):
    refinement_main.verify_api_key(x_api_key=WS_KEY, x_internal_service="workspace")


def test_airflow_caller_pair_key(refinement_main):
    refinement_main.verify_api_key(x_api_key=AF_KEY, x_internal_service="airflow")


def test_mcp_infra_caller_pair_key(refinement_main):
    refinement_main.verify_api_key(x_api_key=MCP_KEY, x_internal_service="mcp-infra")


def test_cartridge_caller_pair_key_via_canonical_name(refinement_main):
    refinement_main.verify_api_key(
        x_api_key=CART_KEY, x_internal_service="cartridge-replicon"
    )


def test_cartridge_caller_legacy_replicon_identifier_still_works(refinement_main):
    refinement_main.verify_api_key(x_api_key=CART_KEY, x_internal_service="replicon")


@pytest.fixture()
def refinement_main_prod_legacy_only(monkeypatch):
    saved_path = list(sys.path)
    _force_refinement_import_path()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    for name in (
        "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",
        "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT",
        "INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT",
        "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
        "INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(
        sys.modules, "app.duckdb_engine", _module(DuckDBEngine=_DuckDBEngineStub)
    )
    monkeypatch.setitem(
        sys.modules, "app.dataset_store", _module(DatasetStore=lambda path: object())
    )

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
        sys.modules, "app.security", _module(get_internal_api_key=lambda: LEGACY)
    )
    main = importlib.import_module("app.main")
    yield main
    sys.path[:] = saved_path
    _purge_app_modules()


def test_legacy_key_rejected_in_production(refinement_main_prod_legacy_only):
    with pytest.raises(HTTPException) as exc:
        refinement_main_prod_legacy_only.verify_api_key(
            x_api_key=LEGACY, x_internal_service="console"
        )
    assert exc.value.status_code == 403


def test_pair_key_still_accepted_in_production(
    monkeypatch, refinement_main_prod_legacy_only
):
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", CONSOLE_KEY)
    refinement_main_prod_legacy_only.verify_api_key(
        x_api_key=CONSOLE_KEY, x_internal_service="console"
    )
