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
    def __init__(self, invocation: object, admitted_conf: dict):
        self.invocation = invocation
        self.admitted_conf = admitted_conf

    def xcom_pull(self, *, task_ids: str, key: str | None = None):
        if task_ids == "resolve_chain" and key == "cartridge_id":
            return "replicon"
        if task_ids == "admit_dataset_refresh":
            return self.admitted_conf
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
    dag_run = _DagRun(task_state, allow_partial=allow_partial)
    dataset_refresh_chain.record_run(
        ti=_TaskInstance(invocation, dict(dag_run.conf)),
        dag_run=dag_run,
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
            {"name": "gold_a", "layer": "gold", "ok": True},
            {"name": "gold_b", "layer": "gold", "ok": False},
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


def test_intelligence_receives_only_successful_gold_datasets(monkeypatch) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    invocation = {
        "status": "completed",
        "materialized": 2,
        "results": [
            {"name": "silver_source", "layer": "silver", "ok": True},
            {"name": "gold_metric", "layer": "gold", "ok": True},
        ],
    }

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "success"]
    assert intelligence == [["gold_metric"]]


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


class _MaterializeTaskInstance:
    def __init__(self, plan: list[dict]):
        self._plan = plan
        self.pushed = None

    def xcom_pull(self, *, task_ids: str, key: str | None = None):
        if key == "plan":
            return self._plan
        if key == "cartridge_id":
            return "replicon"
        return None

    def xcom_push(self, *, key: str, value: object) -> None:
        self.pushed = (key, value)


def _retry_materialization(
    monkeypatch: pytest.MonkeyPatch,
    dataset_refresh_materialize,
    *,
    plan: list[dict],
    completed: dict[str, dict],
) -> tuple[dict, list[str]]:
    posts: list[str] = []

    def reserve(*_args, **kwargs):
        dataset = str(kwargs["dataset"])
        if dataset in completed:
            return {
                "reserved": False,
                "completed": True,
                "result": completed[dataset],
            }
        return {
            "reserved": True,
            "completed": False,
            "slot_id": f"slot-{dataset}",
            "lease_token": 1,
        }

    def run_job(_client, _url: str, *, json, **_kwargs) -> dict:
        body = json() if callable(json) else json
        name = str(body["args"]["name"])
        posts.append(name)
        return {"name": name, "layer": "gold", "row_count": 5}

    monkeypatch.setattr(dataset_refresh_materialize, "reserve_materialization", reserve)
    monkeypatch.setattr(
        dataset_refresh_materialize, "finish_materialization", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "build_materialize_context",
        lambda **_kwargs: {"trusted": True},
    )
    monkeypatch.setattr(dataset_refresh_materialize, "run_service_job", run_job)

    conf = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "cartridge_id": "replicon",
    }
    context = {
        "ti": _MaterializeTaskInstance(plan),
        "run_id": "manual__retry",
        "dag_run": type("DagRun", (), {"conf": conf})(),
    }
    invocation = dataset_refresh_materialize.materialize_in_order(
        context,
        postgres_dsn="postgresql://unused",
        refinement_url="http://refinement",
        headers=lambda *_args: {},
        admitted_conf=conf,
    )
    return invocation, posts


_RETRY_PLAN = [
    {"name": "gold_headcount", "layer": "gold", "cartridge": "replicon"},
    {"name": "gold_employee_360", "layer": "gold", "cartridge": "replicon"},
]
_DURABLE_HEADCOUNT = {"name": "gold_headcount", "row_count": 42}


def test_partial_retry_keeps_every_reused_gold_in_the_refresh_payload(
    monkeypatch,
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, posts = _retry_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_RETRY_PLAN,
        completed={"gold_headcount": _DURABLE_HEADCOUNT},
    )

    assert posts == ["gold_employee_360"]
    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "success"]
    assert intelligence == [sorted(item["name"] for item in _RETRY_PLAN)]


def test_fully_reused_retry_still_triggers_gold_refresh(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, posts = _retry_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_RETRY_PLAN[:1],
        completed={"gold_headcount": _DURABLE_HEADCOUNT},
    )

    assert posts == []
    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "success"]
    assert intelligence == [["gold_headcount"]]


def test_retry_never_repeats_a_completed_materialization(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, posts = _retry_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_RETRY_PLAN,
        completed={
            "gold_headcount": _DURABLE_HEADCOUNT,
            "gold_employee_360": {"name": "gold_employee_360", "row_count": 7},
        },
    )

    assert posts == []
    assert [item["reused"] for item in invocation["results"]] == [True, True]
    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "success"]
    assert len(intelligence) == 1
    assert intelligence == [sorted(item["name"] for item in _RETRY_PLAN)]


def test_retry_without_a_resolvable_gold_layer_never_reaches_success(
    monkeypatch,
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")

    with pytest.raises(RuntimeError, match="layer is unavailable"):
        _retry_materialization(
            monkeypatch,
            dataset_refresh_materialize,
            plan=[{"name": "gold_headcount", "layer": "", "cartridge": "replicon"}],
            completed={"gold_headcount": _DURABLE_HEADCOUNT},
        )


def _classified_materialization(
    monkeypatch: pytest.MonkeyPatch,
    dataset_refresh_materialize,
    *,
    plan: list[dict],
    outcomes: dict[str, object],
    allow_partial: bool = False,
) -> tuple[dict, list[tuple[str, bool]]]:
    finished: list[tuple[str, bool]] = []

    def reserve(*_args, **kwargs):
        dataset = str(kwargs["dataset"])
        return {
            "reserved": True,
            "completed": False,
            "slot_id": f"slot-{dataset}",
            "lease_token": 1,
        }

    def finish(_dsn, *, slot_id: str, success: bool, **_kwargs) -> None:
        finished.append((str(slot_id), bool(success)))

    def run_job(_client, _url: str, *, json, **_kwargs):
        body = json() if callable(json) else json
        outcome = outcomes[str(body["args"]["name"])]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(dataset_refresh_materialize, "reserve_materialization", reserve)
    monkeypatch.setattr(dataset_refresh_materialize, "finish_materialization", finish)
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "build_materialize_context",
        lambda **_kwargs: {"trusted": True},
    )
    monkeypatch.setattr(dataset_refresh_materialize, "run_service_job", run_job)

    conf = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "cartridge_id": "replicon",
        "allow_partial": allow_partial,
    }
    context = {
        "ti": _MaterializeTaskInstance(plan),
        "run_id": "manual__classified",
        "dag_run": type("DagRun", (), {"conf": conf})(),
    }
    invocation = dataset_refresh_materialize.materialize_in_order(
        context,
        postgres_dsn="postgresql://unused",
        refinement_url="http://refinement",
        headers=lambda *_args: {},
        admitted_conf=conf,
    )
    return invocation, finished


def _missing_upstream_error(dataset_refresh_materialize, code: str):
    return dataset_refresh_materialize.ServiceJobError(
        f"job j-1 failed with HTTP 409: {code}",
        status_code=409,
        result={"detail": {"code": code, "message": "m", "request_id": "r"}},
    )


_OK_GOLD_PAYLOAD = {"name": "gold_headcount", "layer": "gold", "row_count": 5}
_CLASSIFY_PLAN = [
    {"name": "silver_candidate_latest", "layer": "silver", "cartridge": "replicon"},
    {"name": "gold_headcount", "layer": "gold", "cartridge": "replicon"},
]


@pytest.mark.parametrize(
    "code", ["source_files_missing", "dependency_not_materialized"]
)
def test_missing_upstream_silver_is_skipped_not_failed(monkeypatch, code: str) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, finished = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_CLASSIFY_PLAN,
        outcomes={
            "silver_candidate_latest": _missing_upstream_error(
                dataset_refresh_materialize, code
            ),
            "gold_headcount": _OK_GOLD_PAYLOAD,
        },
    )

    assert invocation["materialized"] == 1
    assert invocation["breakdown"] == {
        "ok": 1,
        "skipped_upstream_missing": 1,
        "failed": 0,
    }
    skipped = invocation["results"][0]
    assert skipped["ok"] is False
    assert skipped["classification"] == "skipped_upstream_missing"
    assert skipped["reason"] == code
    assert invocation["results"][1]["classification"] == "ok"
    assert ("slot-silver_candidate_latest", False) in finished
    assert ("slot-gold_headcount", True) in finished

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "partial"]
    assert intelligence == [["gold_headcount"]]


def test_fallback_materialization_counts_as_upstream_skip(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    fallback_payload = {
        "name": "silver_candidate_latest",
        "layer": "silver",
        "row_count": 0,
        "status": "partial",
        "fallback": True,
        "fallback_reason": "missing_materialized_dependency",
        "original_error": "IO Error: No files found that match the pattern",
        "degraded": True,
        "degraded_reason": "upstream_dependency_empty_gold",
    }
    invocation, finished = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_CLASSIFY_PLAN,
        outcomes={
            "silver_candidate_latest": fallback_payload,
            "gold_headcount": _OK_GOLD_PAYLOAD,
        },
    )

    assert invocation["breakdown"] == {
        "ok": 1,
        "skipped_upstream_missing": 1,
        "failed": 0,
    }
    assert invocation["results"][0]["classification"] == "skipped_upstream_missing"
    assert invocation["results"][0]["reason"] == "missing_materialized_dependency"
    assert ("slot-silver_candidate_latest", False) in finished

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "partial"]
    assert intelligence == [["gold_headcount"]]


def test_all_skipped_run_records_partial_without_failing(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, _finished = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_CLASSIFY_PLAN,
        outcomes={
            "silver_candidate_latest": _missing_upstream_error(
                dataset_refresh_materialize, "source_files_missing"
            ),
            "gold_headcount": _missing_upstream_error(
                dataset_refresh_materialize, "dependency_not_materialized"
            ),
        },
    )

    assert invocation["breakdown"] == {
        "ok": 0,
        "skipped_upstream_missing": 2,
        "failed": 0,
    }

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "partial"]
    assert intelligence == [[]]


@pytest.mark.parametrize(
    "failure_factory",
    [
        lambda module: module.ServiceJobError(
            "job j-2 failed with HTTP 500: no detail", status_code=500, result=None
        ),
        lambda module: module.ServiceJobError(
            "job j-3 failed with HTTP 409: s3_storage_list_failed",
            status_code=409,
            result={"detail": {"code": "s3_storage_list_failed"}},
        ),
        lambda module: module.ServiceJobError(
            "job j-4 failed with HTTP 502: source_files_missing",
            status_code=502,
            result={"detail": {"code": "source_files_missing"}},
        ),
    ],
)
def test_real_failure_still_fails_the_run(monkeypatch, failure_factory) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")

    with pytest.raises(RuntimeError, match="failed materializations"):
        _classified_materialization(
            monkeypatch,
            dataset_refresh_materialize,
            plan=_CLASSIFY_PLAN,
            outcomes={
                "silver_candidate_latest": failure_factory(dataset_refresh_materialize),
                "gold_headcount": _OK_GOLD_PAYLOAD,
            },
        )


def test_real_failure_partial_still_requires_allow_partial(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, _finished = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_CLASSIFY_PLAN,
        outcomes={
            "silver_candidate_latest": dataset_refresh_materialize.ServiceJobError(
                "job j-5 failed with HTTP 500: no detail",
                status_code=500,
                result=None,
            ),
            "gold_headcount": _OK_GOLD_PAYLOAD,
        },
        allow_partial=True,
    )

    assert invocation["breakdown"] == {
        "ok": 1,
        "skipped_upstream_missing": 0,
        "failed": 1,
    }
    assert invocation["results"][0]["classification"] == "failed"
    assert invocation["results"][0]["error_code"] == "ServiceJobError"

    with pytest.raises(RuntimeError, match="failed run"):
        _record(monkeypatch, dataset_refresh_chain, invocation)

    saved, intelligence = _record(
        monkeypatch, dataset_refresh_chain, invocation, allow_partial=True
    )

    assert saved == ["running", "partial"]
    assert intelligence == [["gold_headcount"]]


def test_strict_fallback_error_payload_stays_a_real_failure(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")

    strict_payload = {
        "name": "silver_candidate_latest",
        "layer": "silver",
        "row_count": 0,
        "status": "partial",
        "fallback": True,
        "fallback_reason": "missing_materialized_dependency",
        "error": "successfactors foundation fallback produjo gold vacio",
    }

    with pytest.raises(RuntimeError, match="failed materializations"):
        _classified_materialization(
            monkeypatch,
            dataset_refresh_materialize,
            plan=_CLASSIFY_PLAN,
            outcomes={
                "silver_candidate_latest": strict_payload,
                "gold_headcount": _OK_GOLD_PAYLOAD,
            },
        )


@pytest.mark.parametrize(
    "invocation",
    [
        {
            "status": "completed",
            "materialized": 1,
            "results": [
                {
                    "name": "gold_a",
                    "ok": True,
                    "classification": "skipped_upstream_missing",
                }
            ],
        },
        {
            "status": "completed",
            "materialized": 0,
            "results": [
                {"name": "gold_a", "ok": False, "classification": "unexpected"}
            ],
        },
        {
            "status": "completed",
            "materialized": 1,
            "results": [{"name": "gold_a", "layer": "gold", "ok": True}],
            "breakdown": {"ok": 0, "skipped_upstream_missing": 1, "failed": 0},
        },
        {
            "status": "completed",
            "materialized": 0,
            "results": [
                {
                    "name": "gold_a",
                    "ok": False,
                    "classification": "skipped_upstream_missing",
                }
            ],
            "breakdown": {"ok": True, "skipped_upstream_missing": 1, "failed": 0},
        },
    ],
)
def test_forged_classification_contracts_fail_closed(
    monkeypatch, invocation: dict
) -> None:
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    with pytest.raises(RuntimeError, match="failed run"):
        _record(
            monkeypatch,
            dataset_refresh_chain,
            invocation,
            allow_partial=True,
        )
