from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import psycopg2
import pytest

from refinement.app import main as refinement_main
from refinement.app import publication_input_binding, publication_inputs
from refinement.app import publication_snapshot
from refinement.app.duckdb_engine import DuckDBEngine

HEAD_UNAVAILABLE = r"publication head unavailable \(http 503\)"

TENANT = "tenant-a"
WORKSPACE = "workspace-a"
CONTEXT = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
PAIR_KEY = "head-console-refinement-pair-key-more-than-32-characters"
TALENT_GOLD = "sap_successfactors_talent_employee_profile"


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


def _assert_head_unavailable(raised) -> None:
    assert type(raised.value).__name__ == "PublicationHeadUnavailable"


_TRANSIENT = [
    psycopg2.OperationalError("connection to server failed: timeout expired"),
    RuntimeError("published snapshot object checksum mismatch"),
    OSError("stat failed"),
]


@pytest.mark.parametrize("failure", _TRANSIENT)
def test_engine_head_resolution_failure_is_never_a_missing_head(monkeypatch, failure):
    _patch_snapshot_resolver(monkeypatch, failure)

    with pytest.raises(RuntimeError, match=HEAD_UNAVAILABLE) as raised:
        DuckDBEngine()._latest_materialized_uri("silver", "hubspot", "x", CONTEXT)

    _assert_head_unavailable(raised)
    assert raised.value.__cause__ is failure


def test_engine_genuine_missing_head_is_none(monkeypatch):
    _patch_snapshot_resolver(monkeypatch, None)

    assert (
        DuckDBEngine()._latest_materialized_uri("silver", "hubspot", "x", CONTEXT)
        is None
    )


def test_scoped_sql_never_falls_back_to_the_legacy_path_on_head_failure(monkeypatch):
    _patch_snapshot_resolver(monkeypatch, _TRANSIENT[0])
    engine = DuckDBEngine()
    sql = "SELECT * FROM read_parquet('s3://lakehouse/silver/hubspot/x/data.parquet')"

    with pytest.raises(RuntimeError, match=HEAD_UNAVAILABLE) as raised:
        engine._scope_storage_sql(sql, ["silver/hubspot/x"], CONTEXT)
    _assert_head_unavailable(raised)


class _BindingEngine(publication_input_binding.PublicationInputBindingMixin):
    storage = None

    @staticmethod
    def _state():
        return None


@pytest.mark.parametrize("failure", _TRANSIENT)
def test_staged_binding_head_failure_is_storage_unavailable(monkeypatch, failure):
    monkeypatch.setattr(
        publication_input_binding, "PublicationSnapshotResolver", _resolver(failure)
    )

    with pytest.raises(RuntimeError, match=HEAD_UNAVAILABLE) as raised:
        _BindingEngine()._latest_materialized_uri("gold", "hubspot", "x", CONTEXT)
    _assert_head_unavailable(raised)


def test_staged_binding_genuine_missing_head_is_none(monkeypatch):
    monkeypatch.setattr(
        publication_input_binding, "PublicationSnapshotResolver", _resolver(None)
    )

    assert (
        _BindingEngine()._latest_materialized_uri("gold", "hubspot", "x", CONTEXT)
        is None
    )


def test_input_state_head_failure_is_storage_unavailable(monkeypatch):
    monkeypatch.setattr(
        publication_inputs, "PublicationSnapshotResolver", _resolver(_TRANSIENT[0])
    )
    engine = SimpleNamespace(storage=None)

    with pytest.raises(RuntimeError, match=HEAD_UNAVAILABLE) as raised:
        publication_inputs._published_state(engine, "gold/hubspot/x", CONTEXT)
    _assert_head_unavailable(raised)

    monkeypatch.setattr(
        publication_inputs, "PublicationSnapshotResolver", _resolver(None)
    )
    assert publication_inputs._published_state(engine, "gold/hubspot/x", CONTEXT) == {
        "source": "gold/hubspot/x",
        "published": None,
    }


def test_snapshot_object_stat_failure_is_storage_unavailable():
    class Storage:
        @staticmethod
        def uri_for(key):
            return f"s3://lakehouse/{key}"

        @staticmethod
        def stat(*_args, **_kwargs):
            raise OSError("connection reset by peer")

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

    with pytest.raises(RuntimeError, match=HEAD_UNAVAILABLE) as raised:
        resolver._validate_object(snapshot)
    _assert_head_unavailable(raised)


@pytest.mark.parametrize("dataset", [TALENT_GOLD, "sap_successfactors_movement_events"])
def test_head_outage_never_publishes_a_fallback(monkeypatch, dataset):
    calls: list[str] = []

    def materialize(ds, _context):
        calls.append(str(ds.get("sql_def") or ""))
        raise refinement_main.PublicationHeadUnavailable(
            "publication head unavailable (http 503): X"
        )

    monkeypatch.setattr(refinement_main.engine, "materialize", materialize)

    with pytest.raises(refinement_main.PublicationHeadUnavailable):
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
async def test_materialize_tool_reports_head_outage_as_storage_unavailable(
    monkeypatch,
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
        raise refinement_main.PublicationHeadUnavailable(
            "publication head unavailable (http 503): OperationalError"
        )

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

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert calls == ["SELECT 1 AS real_value"]
    update_refresh.assert_not_called()
