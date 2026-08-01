from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.airflow_dag_test_loader import load_dag


class _Response:
    status_code = 200

    def __init__(self, status: str):
        self._status = status

    def json(self) -> dict:
        return {"result": {"saved": True, "status": self._status}}


class _Task:
    def __init__(self, state: str):
        self.state = state


class _DagRun:
    def __init__(self, state: str, *, allow_partial: bool):
        self.conf = {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "cartridge_id": "replicon",
            "allow_partial": allow_partial,
        }
        self._task = _Task(state)

    def get_task_instance(self, _task_id: str) -> _Task:
        return self._task


class _TaskInstance:
    def __init__(self, invocation: object):
        self.invocation = invocation

    def xcom_pull(self, *, task_ids: str, key: str | None = None):
        if task_ids == "resolve_chain" and key == "cartridge_id":
            return "replicon"
        if task_ids == "materialize_in_order":
            return self.invocation
        return None


def _record(
    monkeypatch: pytest.MonkeyPatch,
    dataset_refresh_chain,
    invocation: object,
    *,
    task_state: str = "success",
    allow_partial: bool = False,
) -> tuple[list[str], list[list[str]]]:
    saved: list[str] = []
    intelligence: list[list[str]] = []

    def post(_url: str, *, json: dict, **_kwargs) -> _Response:
        status = str(json["args"]["status"])
        saved.append(status)
        return _Response(status)

    def trigger(**kwargs) -> None:
        intelligence.append(list(kwargs["datasets"]))

    monkeypatch.setattr(dataset_refresh_chain.requests, "post", post)
    monkeypatch.setattr(dataset_refresh_chain, "_internal_headers", lambda *_a: {})
    monkeypatch.setattr(
        dataset_refresh_chain,
        "_trigger_gold_refresh_intelligence",
        trigger,
    )
    dataset_refresh_chain.record_run(
        ti=_TaskInstance(invocation),
        dag_run=_DagRun(task_state, allow_partial=allow_partial),
        run_id="manual__fail_closed",
        logical_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    return saved, intelligence


@pytest.mark.parametrize("task_state", ["failed", "upstream_failed"])
def test_task_failure_dominates_empty_counters(monkeypatch, task_state: str) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    invocation = {"materialized": 0, "results": [], "error": "task_failed"}

    with pytest.raises(RuntimeError, match="failed run"):
        _record(
            monkeypatch,
            dataset_refresh_chain,
            invocation,
            task_state=task_state,
        )


def test_missing_xcom_without_explicit_noop_fails_closed(monkeypatch) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    with pytest.raises(RuntimeError, match="failed run"):
        _record(monkeypatch, dataset_refresh_chain, None)


def test_explicit_error_dominates_successful_result(monkeypatch) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    invocation = {
        "status": "completed",
        "materialized": 1,
        "results": [{"name": "gold_a", "ok": True}],
        "error": "authority_failed",
    }

    with pytest.raises(RuntimeError, match="failed run"):
        _record(
            monkeypatch,
            dataset_refresh_chain,
            invocation,
            allow_partial=True,
        )


def test_total_failure_is_never_accepted_by_allow_partial(monkeypatch) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    invocation = {
        "status": "completed",
        "materialized": 0,
        "results": [{"name": "gold_a", "ok": False}],
    }

    with pytest.raises(RuntimeError, match="failed run"):
        _record(
            monkeypatch,
            dataset_refresh_chain,
            invocation,
            allow_partial=True,
        )


def test_valid_partial_is_the_only_failure_allow_partial_accepts(monkeypatch) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    invocation = {
        "status": "completed",
        "materialized": 1,
        "results": [
            {"name": "gold_a", "ok": True},
            {"name": "gold_b", "ok": False},
        ],
    }

    saved, intelligence = _record(
        monkeypatch,
        dataset_refresh_chain,
        invocation,
        allow_partial=True,
    )

    assert saved == ["running", "partial"]
    assert intelligence == [["gold_a"]]


def test_noop_requires_explicit_status_and_successful_task(monkeypatch) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    invocation = {
        "status": "no_downstream_datasets",
        "materialized": 0,
        "results": [],
    }

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["noop"]
    assert intelligence == []


@pytest.mark.parametrize(
    "invocation",
    [
        {"status": "completed", "materialized": True, "results": []},
        {"status": "completed", "materialized": -1, "results": []},
        {"status": "completed", "materialized": 1, "results": {}},
        {"status": "completed", "materialized": 1, "results": [{"ok": 1}]},
    ],
)
def test_malformed_xcom_contract_fails_closed(monkeypatch, invocation: dict) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    with pytest.raises(RuntimeError, match="failed run"):
        _record(
            monkeypatch,
            dataset_refresh_chain,
            invocation,
            allow_partial=True,
        )
