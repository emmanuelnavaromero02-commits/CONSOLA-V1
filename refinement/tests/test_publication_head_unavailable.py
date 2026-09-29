from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import duckdb
import httpx
import psycopg2
import pytest

from omega_lakehouse.errors import ChecksumMismatch, ObjectNotFound, StorageError
from refinement.app import main as refinement_main
from refinement.app import publication_input_binding, publication_inputs
from refinement.app import publication_snapshot
from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.successfactors_fallbacks import (
    is_missing_successfactors_dependency_error,
    is_storage_infra_exception,
)

HEAD_UNAVAILABLE = r"publication head unavailable \(http 503\)"
INTEGRITY_FAILED = r"publication integrity failed"
TENANT = "tenant-a"
WORKSPACE = "workspace-a"
CONTEXT = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
PAIR_KEY = "head-console-refinement-pair-key-more-than-32-characters"
TALENT_GOLD = "sap_successfactors_talent_employee_profile"

OUTAGES = [
    psycopg2.OperationalError("connection to server failed: timeout expired"),
    psycopg2.InterfaceError("connection already closed"),
    ConnectionResetError("connection reset by peer"),
    TimeoutError("read timed out"),
    StorageError("object stat failed", provider="s3", bucket="lakehouse"),
]
INTEGRITY = [
    RuntimeError("published snapshot object checksum mismatch"),
    RuntimeError("published snapshot authority mismatch"),
    ObjectNotFound("object not found", provider="s3", bucket="lakehouse"),
    ChecksumMismatch("checksum mismatch", provider="s3", bucket="lakehouse"),
    KeyError("tenant_id"),
    RuntimeError("GOLD_DATABASE_URL is required for publication snapshots"),
]
EXPECTED = [
    (failure, HEAD_UNAVAILABLE, "PublicationHeadUnavailable") for failure in OUTAGES
] + [(failure, INTEGRITY_FAILED, "PublicationIntegrityError") for failure in INTEGRITY]


def _resolver(outcome):
    class Resolver:
        def __init__(self, *_args, **_kwargs):
            pass

        def published_snapshot(self, *_args, **_kwargs):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    return Resolver


def _runtime_snapshot():
    try:
        return importlib.import_module("app.publication_snapshot")
    except ModuleNotFoundError:
        return publication_snapshot


def _patch_snapshot_resolver(monkeypatch, outcome) -> None:
    for module in {publication_snapshot, _runtime_snapshot()}:
        monkeypatch.setattr(module, "PublicationSnapshotResolver", _resolver(outcome))


@pytest.mark.parametrize("failure, message, kind", EXPECTED)
def test_engine_head_resolution_separates_outages_from_integrity(
    monkeypatch, failure, message, kind
):
    _patch_snapshot_resolver(monkeypatch, failure)

    with pytest.raises(RuntimeError, match=message) as raised:
        DuckDBEngine()._latest_materialized_uri("silver", "hubspot", "x", CONTEXT)

    assert type(raised.value).__name__ == kind
    assert raised.value.__cause__ is failure


def test_engine_genuine_missing_head_is_none(monkeypatch):
    _patch_snapshot_resolver(monkeypatch, None)

    assert (
        DuckDBEngine()._latest_materialized_uri("silver", "hubspot", "x", CONTEXT)
        is None
    )


@pytest.mark.parametrize("failure", [OUTAGES[0], INTEGRITY[0]])
def test_scoped_sql_never_falls_back_to_the_legacy_path(monkeypatch, failure):
    _patch_snapshot_resolver(monkeypatch, failure)
    sql = "SELECT * FROM read_parquet('s3://lakehouse/silver/hubspot/x/data.parquet')"

    with pytest.raises(RuntimeError, match=f"{HEAD_UNAVAILABLE}|{INTEGRITY_FAILED}"):
        DuckDBEngine()._scope_storage_sql(sql, ["silver/hubspot/x"], CONTEXT)


class _BindingEngine(publication_input_binding.PublicationInputBindingMixin):
    storage = None

    @staticmethod
    def _state():
        return None


@pytest.mark.parametrize("failure, message, kind", EXPECTED)
def test_staged_binding_separates_outages_from_integrity(
    monkeypatch, failure, message, kind
):
    monkeypatch.setattr(
        publication_input_binding, "PublicationSnapshotResolver", _resolver(failure)
    )

    with pytest.raises(RuntimeError, match=message) as raised:
        _BindingEngine()._latest_materialized_uri("gold", "hubspot", "x", CONTEXT)
    assert type(raised.value).__name__ == kind


def test_staged_binding_genuine_missing_head_is_none(monkeypatch):
    monkeypatch.setattr(
        publication_input_binding, "PublicationSnapshotResolver", _resolver(None)
    )

    assert (
        _BindingEngine()._latest_materialized_uri("gold", "hubspot", "x", CONTEXT)
        is None
    )


@pytest.mark.parametrize("failure, message, kind", EXPECTED)
def test_input_state_separates_outages_from_integrity(
    monkeypatch, failure, message, kind
):
    monkeypatch.setattr(
        publication_inputs, "PublicationSnapshotResolver", _resolver(failure)
    )

    with pytest.raises(RuntimeError, match=message) as raised:
        publication_inputs._published_state(
            SimpleNamespace(storage=None), "gold/hubspot/x", CONTEXT
        )
    assert type(raised.value).__name__ == kind


def test_input_state_genuine_missing_head_is_unpublished(monkeypatch):
    monkeypatch.setattr(
        publication_inputs, "PublicationSnapshotResolver", _resolver(None)
    )

    assert publication_inputs._published_state(
        SimpleNamespace(storage=None), "gold/hubspot/x", CONTEXT
    ) == {"source": "gold/hubspot/x", "published": None}


@pytest.mark.parametrize(
    "failure, message, kind",
    [
        (OUTAGES[4], HEAD_UNAVAILABLE, "PublicationHeadUnavailable"),
        (OUTAGES[2], HEAD_UNAVAILABLE, "PublicationHeadUnavailable"),
        (INTEGRITY[2], INTEGRITY_FAILED, "PublicationIntegrityError"),
    ],
)
def test_snapshot_object_stat_separates_outages_from_integrity(failure, message, kind):
    class Storage:
        @staticmethod
        def uri_for(key):
            return f"s3://lakehouse/{key}"

        @staticmethod
        def stat(*_args, **_kwargs):
            raise failure

    resolver = publication_snapshot.PublicationSnapshotResolver.__new__(
        publication_snapshot.PublicationSnapshotResolver
    )
    resolver.storage = Storage()
    snapshot = SimpleNamespace(
        head={
            "status": "published",
            "object_uri": "s3://lakehouse/gold/hubspot/x/v.parquet",
            "object_version": "v1",
            "object_checksum": "c",
        }
    )

    with pytest.raises(RuntimeError, match=message) as raised:
        resolver._validate_object(snapshot)
    assert type(raised.value).__name__ == kind


@pytest.mark.parametrize("dataset", [TALENT_GOLD, "sap_successfactors_movement_events"])
@pytest.mark.parametrize(
    "error",
    [
        lambda: refinement_main.PublicationHeadUnavailable(
            "publication head unavailable (http 503): OperationalError"
        ),
        lambda: refinement_main.PublicationIntegrityError(
            "publication integrity failed: ObjectNotFound 404 (Not Found)"
        ),
    ],
)
def test_head_failures_never_publish_a_fallback(monkeypatch, dataset, error):
    calls: list[str] = []

    def materialize(ds, _context):
        calls.append(str(ds.get("sql_def") or ""))
        raise error()

    monkeypatch.setattr(refinement_main.engine, "materialize", materialize)

    with pytest.raises(RuntimeError):
        refinement_main._materialize_with_operational_fallback(
            {"name": dataset, "sql_def": "SELECT real", "description": "d"}, CONTEXT
        )
    assert calls == ["SELECT real"]


def _security_context() -> dict:
    return {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "workspace_admin",
        "user_id": 41,
        "permissions": ["datasets.read", "datasets.write"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": ["sap_successfactors"],
        "allowed_prefixes": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, status, code",
    [
        (
            lambda: refinement_main.PublicationHeadUnavailable(
                "publication head unavailable (http 503): OperationalError"
            ),
            503,
            "storage_unavailable",
        ),
        (
            lambda: refinement_main.PublicationIntegrityError(
                "publication integrity failed: ObjectNotFound"
            ),
            409,
            "publication_integrity_failed",
        ),
    ],
)
async def test_materialize_tool_reports_head_failures_by_class(
    monkeypatch, error, status, code
):
    dataset = {
        "name": TALENT_GOLD,
        "sql_def": "SELECT 1 AS real_value",
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "sources": ["gold/sap_successfactors/sap_successfactors_employee_360"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "created_by_id": 41,
    }
    calls: list[str] = []

    def materialize(ds, _context):
        calls.append(str(ds.get("sql_def") or ""))
        raise error()

    update_refresh = MagicMock()
    monkeypatch.setattr(
        refinement_main.store, "get_dataset", MagicMock(return_value=dataset)
    )
    monkeypatch.setattr(refinement_main.store, "update_refresh", update_refresh)
    monkeypatch.setattr(refinement_main.engine, "materialize", materialize)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)
    transport = httpx.ASGITransport(app=refinement_main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://refinement.test"
    ) as client:
        response = await client.post(
            "/mcp/invoke",
            headers={"x-api-key": PAIR_KEY, "x-internal-service": "console"},
            json={
                "tool": "materialize",
                "args": {"name": TALENT_GOLD},
                "security_context": refinement_main._sign_security_context(
                    _security_context()
                ),
            },
        )

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert calls == ["SELECT 1 AS real_value"]
    update_refresh.assert_not_called()


def _duckdb_error(sql: str) -> duckdb.Error:
    try:
        duckdb.connect().execute(sql)
    except duckdb.Error as exc:
        return exc
    raise AssertionError("query unexpectedly succeeded")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST('ServiceUnavailable' AS INTEGER)",
        "SELECT internalerror_count FROM (SELECT 1 AS a)",
        "SELECT * FROM accessdenied_events",
        "SELECT 1 a accessdenied",
    ],
)
def test_sql_and_data_errors_echoing_markers_are_not_outages(monkeypatch, sql):
    monkeypatch.setattr(
        refinement_main, "_log_internal_error", lambda *_args, **_kwargs: "req-4"
    )
    exc = _duckdb_error(sql)

    status, detail = refinement_main._friendly_duckdb_error(exc, "silver_a")

    assert detail["code"] != "storage_unavailable"
    assert status != 503
    assert not is_storage_infra_exception(exc)
    assert not is_missing_successfactors_dependency_error(exc)


def test_io_errors_with_the_same_marker_are_still_outages(monkeypatch):
    monkeypatch.setattr(
        refinement_main, "_log_internal_error", lambda *_args, **_kwargs: "req-5"
    )
    exc = duckdb.IOException(
        "HTTP GET error on '/lakehouse/x.parquet' ServiceUnavailable"
    )

    status, detail = refinement_main._friendly_duckdb_error(exc, "silver_a")

    assert (status, detail["code"]) == (503, "storage_unavailable")
    assert is_storage_infra_exception(exc)


_DIGEST = "a" * 64
_DEPENDENCY = "gold/sap_successfactors/sap_successfactors_employee_360"
_KEY = f"{_DEPENDENCY}/v.parquet"
_LEGACY_CONDITIONS = {
    "outside-managed-storage": ({"object_uri": "file:///tmp/x.parquet"}, _DIGEST),
    "missing-pinned-version": ({"object_version": ""}, _DIGEST),
    "no-recorded-checksum": ({}, ""),
    "checksum-mismatch": ({}, "b" * 64),
}


def _legacy_inputs(monkeypatch, overrides: dict, stored_checksum: str):
    resolve = type(refinement_main.engine).materialize.__globals__[
        "resolve_input_state"
    ]
    inputs = resolve.__globals__
    head = {
        "materialization_run_id": "run-1",
        "generation": 1,
        "status": "legacy_unverified",
        "gold_table": None,
        "object_uri": f"s3://lakehouse/{_KEY}",
        "object_version": "v1",
        "object_checksum": _DIGEST,
        **overrides,
    }
    monkeypatch.setitem(
        inputs,
        "PublicationSnapshotResolver",
        _resolver(SimpleNamespace(head=head)),
    )

    class Storage:
        @staticmethod
        def stat(*_args, **_kwargs):
            return SimpleNamespace(checksum_sha256=stored_checksum)

    engine = SimpleNamespace(
        storage=Storage(),
        _s3_object_key=lambda uri: (
            uri.removeprefix("s3://lakehouse/") if uri.startswith("s3://") else ""
        ),
    )
    return resolve, engine


@pytest.mark.parametrize("condition", sorted(_LEGACY_CONDITIONS))
def test_legacy_dependency_integrity_conditions_raise_integrity_errors(
    monkeypatch, condition
):
    overrides, stored_checksum = _LEGACY_CONDITIONS[condition]
    resolve, engine = _legacy_inputs(monkeypatch, overrides, stored_checksum)

    with pytest.raises(RuntimeError, match="published dependency") as raised:
        resolve(engine, {"sources": [_DEPENDENCY]}, CONTEXT)

    assert type(raised.value).__name__ == "PublicationIntegrityError"


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", sorted(_LEGACY_CONDITIONS))
async def test_legacy_dependency_integrity_conditions_return_409_without_fallback(
    monkeypatch, condition
):
    overrides, stored_checksum = _LEGACY_CONDITIONS[condition]
    resolve, fake_engine = _legacy_inputs(monkeypatch, overrides, stored_checksum)
    dataset = {
        "name": TALENT_GOLD,
        "sql_def": "SELECT 1 AS real_value",
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "sources": [_DEPENDENCY],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "created_by_id": 41,
    }
    calls: list[str] = []

    def materialize(ds, context):
        calls.append(str(ds.get("sql_def") or ""))
        resolve(fake_engine, {"sources": [_DEPENDENCY]}, context)
        raise AssertionError("integrity failure was not raised")

    update_refresh = MagicMock()
    monkeypatch.setattr(
        refinement_main.store, "get_dataset", MagicMock(return_value=dataset)
    )
    monkeypatch.setattr(refinement_main.store, "update_refresh", update_refresh)
    monkeypatch.setattr(refinement_main.engine, "materialize", materialize)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)
    transport = httpx.ASGITransport(app=refinement_main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://refinement.test"
    ) as client:
        response = await client.post(
            "/mcp/invoke",
            headers={"x-api-key": PAIR_KEY, "x-internal-service": "console"},
            json={
                "tool": "materialize",
                "args": {"name": TALENT_GOLD},
                "security_context": refinement_main._sign_security_context(
                    _security_context()
                ),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "publication_integrity_failed"
    assert calls == ["SELECT 1 AS real_value"]
    update_refresh.assert_not_called()
