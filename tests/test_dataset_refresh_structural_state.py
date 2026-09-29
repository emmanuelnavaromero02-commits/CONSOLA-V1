from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from tests.airflow_dag_test_loader import load_module


class _Cursor:
    def __init__(self, responses: list[object], calls: list[tuple[str, tuple]]):
        self._responses = responses
        self._calls = calls
        self._last: object = None
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._calls.append((" ".join(sql.split()), tuple(params)))
        self._last = self._responses.pop(0) if self._responses else None

    def fetchone(self):
        return self._last

    def fetchall(self):
        return list(self._last or [])


class _Connection:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


def _fake_psycopg2(monkeypatch, responses: list[object]) -> list[tuple[str, tuple]]:
    calls: list[tuple[str, tuple]] = []
    module = types.ModuleType("psycopg2")
    module.connect = lambda _dsn: _Connection(_Cursor(responses, calls))
    monkeypatch.setitem(sys.modules, "psycopg2", module)
    return calls


_TENANT = "00000000-0000-4000-8000-00000000000a"
_WORKSPACE = "00000000-0000-4000-8000-00000000000b"


def _graph_module(monkeypatch):
    return load_module(monkeypatch, "dataset_refresh_graph")


def _idempotency_module(monkeypatch):
    return load_module(monkeypatch, "dataset_refresh_idempotency")


def test_load_graph_carries_materialization_and_raw_extraction_evidence(
    monkeypatch,
) -> None:
    graph_module = _graph_module(monkeypatch)
    calls = _fake_psycopg2(
        monkeypatch,
        [
            None,
            [
                (
                    "gold_pipeline",
                    "gold",
                    "replicon",
                    ["silver_candidate_latest"],
                    False,
                ),
                (
                    "silver_candidate_latest",
                    "silver",
                    "replicon",
                    ["raw/replicon/Candidate"],
                    False,
                ),
                ("silver_users", "silver", "replicon", '["raw/replicon/Users/"]', True),
            ],
            [("replicon", "users")],
        ],
    )

    graph = graph_module._load_graph(
        "postgresql://unused", tenant_id=_TENANT, workspace_id=_WORKSPACE
    )

    assert calls[0] == (
        "SELECT set_config('app.tenant_id', %s, true), "
        "set_config('app.workspace_id', %s, true)",
        (_TENANT, _WORKSPACE),
    )
    evidence_sql, evidence_params = calls[2]
    assert "FROM pipeline_runs" in evidence_sql
    assert "dag_id <> 'dataset_refresh_chain'" in evidence_sql
    assert "lower(status) IN ('success', 'partial')" in evidence_sql
    assert (
        "(COALESCE(record_count, 0) > 0 OR COALESCE(storage_uri, '') <> '')"
        in evidence_sql
    )
    assert evidence_params == (
        _TENANT,
        _WORKSPACE,
        ["replicon"],
        ["candidate", "users"],
    )
    assert graph["gold_pipeline"]["materialized"] is False
    assert graph["silver_users"]["materialized"] is True
    assert graph["silver_candidate_latest"]["unextracted_raw"] == [
        "raw/replicon/candidate"
    ]
    assert graph["silver_users"]["unextracted_raw"] == []
    assert graph["gold_pipeline"]["unextracted_raw"] == []


def test_load_graph_without_raw_sources_skips_the_evidence_query(monkeypatch) -> None:
    graph_module = _graph_module(monkeypatch)
    calls = _fake_psycopg2(
        monkeypatch,
        [None, [("gold_a", "gold", "replicon", ["config/replicon/rates"], True)]],
    )

    graph = graph_module._load_graph(
        "postgresql://unused", tenant_id=_TENANT, workspace_id=_WORKSPACE
    )

    assert len(calls) == 2
    assert graph["gold_a"]["unextracted_raw"] == []


def test_plan_items_carry_upstreams_and_never_materialized_sources(monkeypatch) -> None:
    graph_module = _graph_module(monkeypatch)
    graph = {
        "silver_candidate_latest": {
            "layer": "silver",
            "cartridge": "replicon",
            "sources": ["raw/replicon/Candidate"],
            "materialized": False,
            "unextracted_raw": ["raw/replicon/candidate"],
        },
        "silver_users": {
            "layer": "silver",
            "cartridge": "replicon",
            "sources": ["raw/replicon/users"],
            "materialized": True,
            "unextracted_raw": [],
        },
        "gold_pipeline": {
            "layer": "gold",
            "cartridge": "replicon",
            "sources": [
                "silver/replicon/silver_candidate_latest",
                "silver_users",
                "silver/replicon/unregistered",
            ],
            "materialized": False,
            "unextracted_raw": [],
        },
        "gold_funnel": {
            "layer": "gold",
            "cartridge": "replicon",
            "sources": ["gold_pipeline", "silver_users"],
            "materialized": True,
            "unextracted_raw": [],
        },
    }

    plan = graph_module._build_plan(
        graph, seed_raw="raw/replicon/*", seed_dataset="", maximum_depth=10
    )
    items = {item["name"]: item for item in plan}

    assert [item["name"] for item in plan] == [
        "silver_candidate_latest",
        "silver_users",
        "gold_pipeline",
        "gold_funnel",
    ]
    assert items["silver_candidate_latest"]["upstreams"] == []
    assert items["silver_candidate_latest"]["materialized"] is False
    assert items["silver_candidate_latest"]["never_materialized_upstreams"] == [
        "raw/replicon/candidate"
    ]
    assert items["gold_pipeline"]["upstreams"] == [
        "silver_candidate_latest",
        "silver_users",
    ]
    assert items["gold_pipeline"]["never_materialized_upstreams"] == [
        "silver_candidate_latest"
    ]
    assert items["gold_funnel"]["upstreams"] == ["gold_pipeline", "silver_users"]
    assert items["gold_funnel"]["materialized"] is True
    assert items["gold_funnel"]["never_materialized_upstreams"] == ["gold_pipeline"]


def test_plan_without_materialization_state_never_allows_a_structural_skip(
    monkeypatch,
) -> None:
    graph_module = _graph_module(monkeypatch)
    graph = {
        "silver_a": {
            "layer": "silver",
            "cartridge": "replicon",
            "sources": ["raw/replicon/users"],
        },
        "gold_b": {"layer": "gold", "cartridge": "replicon", "sources": ["silver_a"]},
    }

    plan = graph_module._build_plan(
        graph, seed_raw="raw/replicon/users", seed_dataset="", maximum_depth=10
    )

    assert [item["materialized"] for item in plan] == [True, True]
    assert [item["never_materialized_upstreams"] for item in plan] == [[], []]
    assert plan[1]["upstreams"] == ["silver_a"]


def _finish(module, **kwargs) -> None:
    module.finish_materialization(
        "postgresql://unused",
        slot_id="slot-a",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        lease_token=3,
        **kwargs,
    )


@pytest.mark.parametrize(
    "kwargs, status, error_message, extra",
    [
        (
            {"success": False, "skipped_reason": "upstream_not_refreshed:silver_a"},
            "skipped",
            "upstream_not_refreshed:silver_a",
            {},
        ),
        ({"success": False}, "failed", "materialization_failed", {}),
        (
            {
                "success": True,
                "result": {"name": "gold_a", "layer": "gold", "row_count": 4},
            },
            "success",
            None,
            {"result": {"name": "gold_a", "row_count": 4, "layer": "gold"}},
        ),
        (
            {
                "success": True,
                "degraded": True,
                "result": {"name": "gold_a", "layer": "gold", "row_count": 0},
            },
            "partial",
            "degraded_fallback_published",
            {
                "result": {
                    "name": "gold_a",
                    "row_count": 0,
                    "layer": "gold",
                    "degraded": True,
                }
            },
        ),
    ],
)
def test_reservation_rows_record_the_honest_outcome(
    monkeypatch, kwargs: dict, status: str, error_message: str | None, extra: dict
) -> None:
    module = _idempotency_module(monkeypatch)
    calls = _fake_psycopg2(monkeypatch, [None, None])

    _finish(module, **kwargs)

    sql, params = calls[1]
    assert sql.startswith("UPDATE pipeline_runs SET status = %s")
    assert "AND status = 'running' AND fencing_token = %s" in sql
    assert params[0] == status
    assert params[1] == error_message
    assert json.loads(params[2]) == extra
    assert params[3:] == ("slot-a", _TENANT, _WORKSPACE, 3)


def test_skip_reason_is_bounded(monkeypatch) -> None:
    module = _idempotency_module(monkeypatch)
    calls = _fake_psycopg2(monkeypatch, [None, None])

    _finish(module, success=False, skipped_reason="x" * 1000)

    assert calls[1][1][1] == "x" * module.MAX_SKIP_REASON_LENGTH


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {
                "success": True,
                "skipped_reason": "upstream_not_refreshed:a",
                "result": {"name": "gold_a", "layer": "gold", "row_count": 1},
            },
            "cannot be skipped",
        ),
        ({"success": False, "degraded": True}, "cannot be degraded"),
        ({"success": False, "skipped_reason": "   "}, "skip reason"),
        ({"success": True, "degraded": True, "result": {"row_count": 1}}, "layer"),
    ],
)
def test_contradictory_outcomes_never_reach_the_database(
    monkeypatch, kwargs: dict, message: str
) -> None:
    module = _idempotency_module(monkeypatch)
    calls = _fake_psycopg2(monkeypatch, [])

    with pytest.raises(RuntimeError, match=message):
        _finish(module, **kwargs)
    assert calls == []


def _reserve(module):
    return module.reserve_materialization(
        "postgresql://unused",
        airflow_run_id="manual__retry",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        cartridge_id="replicon",
        dataset="gold_a",
    )


def test_degraded_reservation_is_reused_on_retry(monkeypatch) -> None:
    module = _idempotency_module(monkeypatch)
    durable = {"name": "gold_a", "row_count": 0, "layer": "gold", "degraded": True}
    calls = _fake_psycopg2(
        monkeypatch,
        [None, ("pipeline_runs",), ("partial", {"result": durable}, None, 2)],
    )

    reservation = _reserve(module)

    assert reservation == {"reserved": False, "completed": True, "result": durable}
    assert not any(sql.startswith("UPDATE") for sql, _params in calls)


@pytest.mark.parametrize(
    "existing",
    [
        ("skipped", {}, None, 2),
        ("failed", {}, None, 2),
        ("partial", {"result": {"name": "gold_a", "layer": "gold"}}, None, 2),
        (
            "running",
            {},
            datetime.now(timezone.utc) - timedelta(seconds=5),
            2,
        ),
    ],
)
def test_skipped_or_failed_reservations_are_retried(monkeypatch, existing) -> None:
    module = _idempotency_module(monkeypatch)
    calls = _fake_psycopg2(
        monkeypatch,
        [None, ("pipeline_runs",), existing, None, (3,)],
    )

    reservation = _reserve(module)

    assert reservation["reserved"] is True
    assert reservation["lease_token"] == 3
    assert any(
        sql.startswith("UPDATE pipeline_runs SET status = 'running'")
        for sql, _params in calls
    )
