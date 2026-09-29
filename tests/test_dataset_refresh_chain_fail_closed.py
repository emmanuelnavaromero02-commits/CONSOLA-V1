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
    saved: list[str] | None = None,
    intelligence: list[list[str]] | None = None,
) -> tuple[list[str], list[list[str]]]:
    saved = [] if saved is None else saved
    intelligence = [] if intelligence is None else intelligence

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
) -> tuple[dict, dict[str, dict], list[str]]:
    finished: dict[str, dict] = {}
    posts: list[str] = []

    def reserve(*_args, **kwargs):
        dataset = str(kwargs["dataset"])
        return {
            "reserved": True,
            "completed": False,
            "slot_id": f"slot-{dataset}",
            "lease_token": 1,
        }

    def finish(_dsn, *, slot_id: str, **kwargs) -> None:
        finished[str(slot_id).removeprefix("slot-")] = kwargs

    def run_job(_client, _url: str, *, json, **_kwargs):
        body = json() if callable(json) else json
        name = str(body["args"]["name"])
        posts.append(name)
        outcome = outcomes[name]
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
    return invocation, finished, posts


def _refinement_error(module, code: str | None, *, status: int = 409):
    return module.ServiceJobError(
        f"job j-1 failed with HTTP {status}: {code}",
        status_code=status,
        result=(
            {"detail": {"code": code, "message": "m", "request_id": "r"}}
            if code
            else None
        ),
    )


def _item(
    name: str,
    layer: str,
    *,
    upstreams: tuple[str, ...] = (),
    materialized: bool = True,
    never_materialized: tuple[str, ...] = (),
) -> dict:
    return {
        "name": name,
        "layer": layer,
        "cartridge": "replicon",
        "upstreams": list(upstreams),
        "materialized": materialized,
        "never_materialized_upstreams": list(never_materialized),
    }


def _payload(name: str, layer: str = "gold", rows: int = 5) -> dict:
    return {"name": name, "layer": layer, "row_count": rows}


def _fallback(name: str, *, rows: int, degraded: bool, **extra) -> dict:
    return {
        "name": name,
        "layer": "gold",
        "row_count": rows,
        "status": "partial",
        "fallback": True,
        "fallback_reason": "missing_materialized_dependency",
        "original_error": "IO Error: No files found that match the pattern",
        "degraded": degraded,
        **extra,
    }


_NEW_SILVER = _item(
    "silver_candidate_latest",
    "silver",
    materialized=False,
    never_materialized=("raw/replicon/candidate",),
)
_READY_GOLD = _item("gold_headcount", "gold")


def test_every_class_is_counted_and_only_ok_golds_reach_intelligence(
    monkeypatch,
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    plan = [
        _NEW_SILVER,
        _READY_GOLD,
        _item("gold_talent_profile", "gold"),
        _item("gold_payroll", "gold"),
    ]

    invocation, finished, posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=plan,
        outcomes={
            "silver_candidate_latest": _refinement_error(
                dataset_refresh_materialize, "source_files_missing"
            ),
            "gold_headcount": _payload("gold_headcount"),
            "gold_talent_profile": _fallback(
                "gold_talent_profile",
                rows=0,
                degraded=True,
                degraded_reason="upstream_dependency_empty_gold",
            ),
            "gold_payroll": _refinement_error(
                dataset_refresh_materialize, None, status=500
            ),
        },
        allow_partial=True,
    )

    assert invocation["breakdown"] == {
        "ok": 1,
        "degraded": 1,
        "skipped": 1,
        "failed": 1,
    }
    assert invocation["materialized"] == 2
    classes = {item["name"]: item["classification"] for item in invocation["results"]}
    assert classes == {
        "silver_candidate_latest": "skipped",
        "gold_headcount": "ok",
        "gold_talent_profile": "degraded",
        "gold_payroll": "failed",
    }
    assert posts == [item["name"] for item in plan]
    assert finished["silver_candidate_latest"] == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "lease_token": 1,
        "success": False,
        "skipped_reason": "upstream_never_materialized:raw/replicon/candidate",
    }
    assert finished["gold_headcount"]["success"] is True
    assert finished["gold_headcount"]["degraded"] is False
    assert finished["gold_talent_profile"]["success"] is True
    assert finished["gold_talent_profile"]["degraded"] is True
    assert finished["gold_payroll"] == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "lease_token": 1,
        "success": False,
    }

    saved, intelligence = _record(
        monkeypatch, dataset_refresh_chain, invocation, allow_partial=True
    )

    assert saved == ["running", "partial"]
    assert intelligence == [["gold_headcount"]]


def test_skip_propagates_transitively_without_calling_refinement(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    plan = [
        _NEW_SILVER,
        _READY_GOLD,
        _item(
            "gold_pipeline",
            "gold",
            upstreams=("silver_candidate_latest",),
            materialized=False,
            never_materialized=("silver_candidate_latest",),
        ),
        _item("gold_funnel", "gold", upstreams=("gold_headcount", "gold_pipeline")),
    ]

    invocation, finished, posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=plan,
        outcomes={
            "silver_candidate_latest": _refinement_error(
                dataset_refresh_materialize, "source_files_missing"
            ),
            "gold_headcount": _payload("gold_headcount"),
        },
    )

    assert posts == ["silver_candidate_latest", "gold_headcount"]
    reasons = {
        item["name"]: item.get("reason")
        for item in invocation["results"]
        if item["classification"] == "skipped"
    }
    assert reasons == {
        "silver_candidate_latest": "upstream_never_materialized:raw/replicon/candidate",
        "gold_pipeline": "upstream_not_refreshed:silver_candidate_latest",
        "gold_funnel": "upstream_not_refreshed:gold_pipeline",
    }
    assert finished["gold_pipeline"]["skipped_reason"] == (
        "upstream_not_refreshed:silver_candidate_latest"
    )
    assert finished["gold_funnel"]["skipped_reason"] == (
        "upstream_not_refreshed:gold_pipeline"
    )
    assert invocation["breakdown"] == {
        "ok": 1,
        "degraded": 0,
        "skipped": 3,
        "failed": 0,
    }

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "partial"]
    assert intelligence == [["gold_headcount"]]


def test_failed_upstream_skips_its_dependents_and_still_fails_the_run(
    monkeypatch,
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    plan = [
        _item("silver_timesheets", "silver"),
        _item("gold_utilization", "gold", upstreams=("silver_timesheets",)),
    ]

    with pytest.raises(RuntimeError, match="failed materializations"):
        _classified_materialization(
            monkeypatch,
            dataset_refresh_materialize,
            plan=plan,
            outcomes={
                "silver_timesheets": _refinement_error(
                    dataset_refresh_materialize, "storage_unavailable", status=503
                )
            },
        )

    invocation, finished, posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=plan,
        outcomes={
            "silver_timesheets": _refinement_error(
                dataset_refresh_materialize, "storage_unavailable", status=503
            )
        },
        allow_partial=True,
    )

    assert posts == ["silver_timesheets"]
    assert [item["classification"] for item in invocation["results"]] == [
        "failed",
        "skipped",
    ]
    assert invocation["results"][1]["reason"] == (
        "upstream_not_refreshed:silver_timesheets"
    )
    assert "skipped_reason" not in finished["silver_timesheets"]


@pytest.mark.parametrize(
    "code", ["source_files_missing", "dependency_not_materialized"]
)
def test_same_missing_source_409_splits_on_materialization_history(
    monkeypatch, code: str
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    previously = _item(
        "silver_candidate_latest",
        "silver",
        materialized=True,
        never_materialized=("raw/replicon/candidate",),
    )

    invocation, finished, _posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=[_NEW_SILVER],
        outcomes={
            "silver_candidate_latest": _refinement_error(
                dataset_refresh_materialize, code
            )
        },
    )
    assert invocation["results"][0]["classification"] == "skipped"
    assert finished["silver_candidate_latest"]["skipped_reason"] == (
        "upstream_never_materialized:raw/replicon/candidate"
    )

    with pytest.raises(RuntimeError, match="failed materializations"):
        _classified_materialization(
            monkeypatch,
            dataset_refresh_materialize,
            plan=[previously],
            outcomes={
                "silver_candidate_latest": _refinement_error(
                    dataset_refresh_materialize, code
                )
            },
        )


@pytest.mark.parametrize(
    "item, failure",
    [
        pytest.param(
            _NEW_SILVER,
            lambda module: _refinement_error(module, "storage_unavailable", status=503),
            id="never-materialized-storage-503",
        ),
        pytest.param(
            _NEW_SILVER,
            lambda module: module.ServiceJobError(
                "job j-9 failed with HTTP 500: no detail", status_code=500
            ),
            id="never-materialized-unstructured-500",
        ),
        pytest.param(
            _NEW_SILVER,
            lambda module: _refinement_error(module, "s3_storage_list_failed"),
            id="never-materialized-listing-409",
        ),
        pytest.param(
            _NEW_SILVER,
            lambda module: _refinement_error(
                module, "source_files_missing", status=502
            ),
            id="missing-code-without-409",
        ),
        pytest.param(
            _NEW_SILVER,
            lambda module: _refinement_error(
                module, "missing_materialized_dependencies"
            ),
            id="dead-refresh-by-source-reason",
        ),
        pytest.param(
            _item("silver_candidate_latest", "silver", materialized=True),
            lambda module: _refinement_error(module, "source_files_missing"),
            id="previously-materialized-no-files-found",
        ),
        pytest.param(
            _item("silver_candidate_latest", "silver", materialized=False),
            lambda module: _refinement_error(module, "source_files_missing"),
            id="never-materialized-with-materialized-upstreams",
        ),
    ],
)
def test_storage_and_misroute_failures_are_never_skips(
    monkeypatch, item: dict, failure
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")

    invocation, finished, _posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=[item, _READY_GOLD],
        outcomes={
            "silver_candidate_latest": failure(dataset_refresh_materialize),
            "gold_headcount": _payload("gold_headcount"),
        },
        allow_partial=True,
    )

    assert invocation["results"][0]["classification"] == "failed"
    assert invocation["breakdown"] == {
        "ok": 1,
        "degraded": 0,
        "skipped": 0,
        "failed": 1,
    }
    assert "skipped_reason" not in finished["silver_candidate_latest"]


def test_upstream_first_materialized_in_this_run_turns_a_missing_source_into_failure(
    monkeypatch,
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    plan = [
        _item("silver_orders", "silver", materialized=False),
        _item(
            "gold_revenue",
            "gold",
            upstreams=("silver_orders",),
            materialized=False,
            never_materialized=("silver_orders",),
        ),
    ]

    invocation, _finished, posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=plan,
        outcomes={
            "silver_orders": _payload("silver_orders", "silver"),
            "gold_revenue": _refinement_error(
                dataset_refresh_materialize, "dependency_not_materialized"
            ),
        },
        allow_partial=True,
    )

    assert posts == ["silver_orders", "gold_revenue"]
    assert [item["classification"] for item in invocation["results"]] == [
        "ok",
        "failed",
    ]


def test_all_skipped_run_is_blocked_and_raises(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")
    plan = [
        _NEW_SILVER,
        _item("gold_pipeline", "gold", upstreams=("silver_candidate_latest",)),
    ]

    invocation, _finished, posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=plan,
        outcomes={
            "silver_candidate_latest": _refinement_error(
                dataset_refresh_materialize, "source_files_missing"
            )
        },
    )

    assert posts == ["silver_candidate_latest"]
    assert invocation["breakdown"] == {
        "ok": 0,
        "degraded": 0,
        "skipped": 2,
        "failed": 0,
    }

    for allow_partial in (False, True):
        saved: list[str] = []
        intelligence: list[list[str]] = []
        with pytest.raises(RuntimeError, match="blocked run") as raised:
            _record(
                monkeypatch,
                dataset_refresh_chain,
                invocation,
                allow_partial=allow_partial,
                saved=saved,
                intelligence=intelligence,
            )
        assert "'skipped': 2" in str(raised.value)
        assert saved == ["blocked"]
        assert intelligence == []


def test_degraded_only_run_is_partial_and_never_notifies_intelligence(
    monkeypatch,
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, finished, _posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=[_item("gold_talent_profile", "gold")],
        outcomes={
            "gold_talent_profile": _fallback(
                "gold_talent_profile", rows=0, degraded=True
            )
        },
    )

    assert invocation["results"][0]["classification"] == "degraded"
    assert invocation["results"][0]["ok"] is True
    assert finished["gold_talent_profile"]["degraded"] is True

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "partial"]
    assert intelligence == [[]]


def test_fallback_with_real_rows_is_ok(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, finished, _posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=[_item("gold_talent_profile", "gold")],
        outcomes={
            "gold_talent_profile": _fallback(
                "gold_talent_profile", rows=12, degraded=False
            )
        },
    )

    assert invocation["results"][0]["classification"] == "ok"
    assert invocation["results"][0]["row_count"] == 12
    assert finished["gold_talent_profile"]["degraded"] is False

    saved, intelligence = _record(monkeypatch, dataset_refresh_chain, invocation)

    assert saved == ["running", "success"]
    assert intelligence == [["gold_talent_profile"]]


@pytest.mark.parametrize(
    "payload",
    [
        _fallback(
            "gold_talent_profile",
            rows=0,
            degraded=True,
            error="successfactors foundation fallback produjo gold vacio",
        ),
        _fallback("gold_talent_profile", rows=0, degraded="yes"),
        _fallback("gold_talent_profile", rows=-1, degraded=True),
        _fallback("gold_other", rows=3, degraded=False),
        {
            **_fallback("gold_talent_profile", rows=3, degraded=False),
            "fallback_reason": "x",
        },
        {**_fallback("gold_talent_profile", rows=3, degraded=False), "layer": "raw"},
        {**_fallback("gold_talent_profile", rows=3, degraded=False), "ok": False},
    ],
)
def test_invalid_or_strict_fallback_payloads_are_failures(
    monkeypatch, payload: dict
) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")

    invocation, finished, _posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=[_item("gold_talent_profile", "gold"), _READY_GOLD],
        outcomes={
            "gold_talent_profile": payload,
            "gold_headcount": _payload("gold_headcount"),
        },
        allow_partial=True,
    )

    assert invocation["results"][0]["classification"] == "failed"
    assert finished["gold_talent_profile"] == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "lease_token": 1,
        "success": False,
    }


def test_real_failure_partial_still_requires_allow_partial(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")
    dataset_refresh_chain = load_dag(monkeypatch, "dataset_refresh_chain")

    invocation, _finished, _posts = _classified_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=[_item("silver_timesheets", "silver"), _READY_GOLD],
        outcomes={
            "silver_timesheets": _refinement_error(
                dataset_refresh_materialize, None, status=500
            ),
            "gold_headcount": _payload("gold_headcount"),
        },
        allow_partial=True,
    )

    assert invocation["breakdown"] == {
        "ok": 1,
        "degraded": 0,
        "skipped": 0,
        "failed": 1,
    }
    assert invocation["results"][0]["error_code"] == "ServiceJobError"

    with pytest.raises(RuntimeError, match="failed run"):
        _record(monkeypatch, dataset_refresh_chain, invocation)

    saved, intelligence = _record(
        monkeypatch, dataset_refresh_chain, invocation, allow_partial=True
    )

    assert saved == ["running", "partial"]
    assert intelligence == [["gold_headcount"]]


def test_degraded_reuse_keeps_its_class_on_retry(monkeypatch) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")

    invocation, posts = _retry_materialization(
        monkeypatch,
        dataset_refresh_materialize,
        plan=_RETRY_PLAN[:1],
        completed={"gold_headcount": {**_DURABLE_HEADCOUNT, "degraded": True}},
    )

    assert posts == []
    assert invocation["results"][0]["classification"] == "degraded"
    assert invocation["breakdown"] == {
        "ok": 0,
        "degraded": 1,
        "skipped": 0,
        "failed": 0,
    }


@pytest.mark.parametrize(
    "plan_item",
    [
        {**_READY_GOLD, "upstreams": "silver_a"},
        {**_READY_GOLD, "upstreams": [None]},
        {**_READY_GOLD, "never_materialized_upstreams": [""]},
    ],
)
def test_malformed_plan_fails_before_reserving(monkeypatch, plan_item: dict) -> None:
    dataset_refresh_materialize = load_dag(monkeypatch, "dataset_refresh_materialize")

    with pytest.raises(RuntimeError, match="plan is malformed"):
        _classified_materialization(
            monkeypatch,
            dataset_refresh_materialize,
            plan=[plan_item],
            outcomes={},
        )


@pytest.mark.parametrize(
    "invocation",
    [
        {
            "status": "completed",
            "materialized": 1,
            "results": [{"name": "gold_a", "ok": True, "classification": "skipped"}],
        },
        {
            "status": "completed",
            "materialized": 0,
            "results": [{"name": "gold_a", "ok": False, "classification": "degraded"}],
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
            "breakdown": {"ok": 0, "degraded": 0, "skipped": 1, "failed": 0},
        },
        {
            "status": "completed",
            "materialized": 0,
            "results": [{"name": "gold_a", "ok": False, "classification": "skipped"}],
            "breakdown": {"ok": True, "degraded": 0, "skipped": 1, "failed": 0},
        },
        {
            "status": "completed",
            "materialized": 1,
            "results": [{"name": "gold_a", "layer": "gold", "ok": True}],
            "breakdown": {"ok": 1, "skipped_upstream_missing": 0, "failed": 0},
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
