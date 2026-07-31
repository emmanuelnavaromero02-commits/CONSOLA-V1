"""Verdad Operacional: a pipeline run may only report success on evidence.

Both scheduler DAGs used to derive their `pipeline_runs.status` from counters
that are indistinguishable between "there was nothing to do" and "the work
never happened". These tests pin the fail-closed behaviour of the two status
deciders.

The helpers under test are pure, so they are lifted out of the DAG files by
AST rather than imported: importing a DAG module would require an Airflow
runtime and would execute the module-level DAG construction.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Callable

import pytest


REPO = Path(__file__).resolve().parents[1]


def _load_function(dag_relpath: str, name: str, *deps: str) -> Callable[..., Any]:
    """Compile a single top-level function out of a DAG file.

    `deps` names module-level constants the function closes over; they are
    compiled alongside it so the extracted function is self-contained.
    """
    source = (REPO / dag_relpath).read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = set(deps)
    body: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in wanted for t in node.targets)
    ]
    target = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name),
        None,
    )
    if target is None:
        raise AssertionError(f"{name} not found in {dag_relpath}")
    assert len(body) == len(wanted), f"missing constants {wanted} in {dag_relpath}"
    module = ast.Module(body=[*body, target], type_ignores=[])
    namespace: dict[str, Any] = {}
    exec(compile(module, str(REPO / dag_relpath), "exec"), namespace)  # noqa: S102
    return namespace[name]


# ── dataset_refresh_chain._chain_status ──────────────────────────────────────

@pytest.fixture(scope="module")
def chain_status() -> Callable[..., str]:
    return _load_function("airflow/dags/dataset_refresh_chain.py", "_chain_status")


def test_hard_failure_before_xcom_is_not_success(chain_status):
    """The P0: materialize_in_order dies before pushing XCom.

    record_run runs under ALL_DONE so it is still reached. The synthesised
    invocation carries zero results and zero materialized, which the old
    `"success" if total == materialized` ternary resolved to success on its
    very first line -- the guard underneath could only ever *set* success, so
    it never fired. Every such run was written to pipeline_runs as green.
    """
    invocation = {
        "materialized": 0,
        "results": [],
        "error": "materialize_in_order ended with state=failed",
    }
    assert chain_status(invocation, materialize_failed=True) == "failed"


def test_missing_xcom_without_readable_state_is_not_success(chain_status):
    """No XCom at all and no observable task state: no evidence of work."""
    assert chain_status({}, materialize_failed=False) == "failed"


def test_empty_plan_from_a_clean_task_is_success(chain_status):
    """materialize_in_order returns {"materialized": 0, "results": []} when the
    resolved plan is empty. That is a genuine no-op, not a failure."""
    assert chain_status({"materialized": 0, "results": []}, materialize_failed=False) == "success"


def test_every_dataset_materialized_is_success(chain_status):
    invocation = {"materialized": 2, "results": [{"ok": True}, {"ok": True}]}
    assert chain_status(invocation, materialize_failed=False) == "success"


def test_some_datasets_materialized_is_partial(chain_status):
    invocation = {"materialized": 1, "results": [{"ok": True}, {"ok": False}]}
    assert chain_status(invocation, materialize_failed=False) == "partial"


def test_no_dataset_materialized_is_failed(chain_status):
    invocation = {"materialized": 0, "results": [{"ok": False}]}
    assert chain_status(invocation, materialize_failed=False) == "failed"


@pytest.mark.parametrize(
    "invocation",
    [
        {"materialized": 1, "results": [{"ok": True}, {"ok": True}]},
        {"materialized": 1, "results": [{"ok": True}], "error": "boom"},
    ],
)
def test_partial_work_under_a_failure_signal_is_partial(chain_status, invocation):
    """Work that demonstrably happened is not erased by the failure signal,
    but it can never be reported as success either."""
    assert chain_status(invocation, materialize_failed=True) == "partial"


def test_failure_signal_always_beats_the_counters(chain_status):
    """Counters that look clean must not override an explicit failure."""
    invocation = {"materialized": 0, "results": []}
    assert chain_status(invocation, materialize_failed=True) == "failed"
    assert chain_status({**invocation, "error": "boom"}, materialize_failed=False) == "failed"


# ── agent_runner._pipeline_status ────────────────────────────────────────────

AGENT_RUNNER = "airflow/dags/agent_runner.py"


@pytest.fixture(scope="module")
def pipeline_status() -> Callable[..., str]:
    return _load_function(AGENT_RUNNER, "_pipeline_status")


def test_idle_window_is_success(pipeline_status):
    """No agent was due in this five minute window: a real, healthy no-op."""
    invocation = {"invoked": 0, "results": [], "scan": {"scanned": 4, "blind": False}}
    assert pipeline_status(invocation) == "success"


def test_blind_scan_is_not_success(pipeline_status):
    """The severed loop: the agents table read back zero rows because RLS
    filtered them, not because no agent was due. Reporting that green wrote a
    healthy pipeline_runs row every five minutes over zero work done."""
    invocation = {
        "invoked": 0,
        "results": [],
        "scan": {"scanned": 0, "blind": True, "reason": "no policy for omega_airflow_dag"},
    }
    assert pipeline_status(invocation) == "failed"


def test_blindness_outranks_a_clean_result_set(pipeline_status):
    """A blind scan can never be talked back into success by the counters."""
    assert pipeline_status({"results": [{"status": 200}], "scan": {"blind": True}}) == "failed"


def test_all_invocations_ok_is_success(pipeline_status):
    invocation = {"results": [{"status": 200}, {"status": 201}], "scan": {"blind": False}}
    assert pipeline_status(invocation) == "success"


def test_some_invocations_failing_is_partial(pipeline_status):
    invocation = {"results": [{"status": 200}, {"status": 500}], "scan": {"blind": False}}
    assert pipeline_status(invocation) == "partial"


def test_every_invocation_failing_is_failed(pipeline_status):
    invocation = {"results": [{"status": 500}, {"status": "exception"}], "scan": {"blind": False}}
    assert pipeline_status(invocation) == "failed"


def test_explicit_error_is_failed(pipeline_status):
    assert pipeline_status({"error": "no_token", "results": []}) == "failed"


# ── agent_runner._agents_visibility ──────────────────────────────────────────

@pytest.fixture(scope="module")
def agents_visibility() -> Callable[..., dict]:
    return _load_function(AGENT_RUNNER, "_agents_visibility", "_AGENTS_VISIBILITY_SQL")


class _Cursor:
    """Minimal stand-in for a psycopg2 cursor."""

    def __init__(self, row: Any = None, raises: Exception | None = None) -> None:
        self._row = row
        self._raises = raises
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        if self._raises is not None:
            raise self._raises
        self.executed.append(sql)

    def fetchone(self) -> Any:
        return self._row


def test_no_applicable_policy_reports_blind(agents_visibility):
    """This is production today: omega_airflow_dag holds SELECT on agents, the
    table runs FORCE ROW LEVEL SECURITY, the role is NOBYPASSRLS, and all three
    policies are granted to omega_console/omega_refinement only."""
    row = ("omega_airflow_dag", True, True, False, False)
    result = agents_visibility(_Cursor(row))
    assert result["blind"] is True
    assert "omega_airflow_dag" in result["reason"]


def test_applicable_policy_reports_visible(agents_visibility):
    row = ("omega_console", True, True, False, True)
    assert agents_visibility(_Cursor(row))["blind"] is False


def test_superuser_bypassing_rls_is_not_blind(agents_visibility):
    """A BYPASSRLS role sees every row, so zero rows really means empty."""
    row = ("postgres", True, True, True, False)
    assert agents_visibility(_Cursor(row))["blind"] is False


def test_rls_disabled_is_not_blind(agents_visibility):
    row = ("omega_airflow_dag", False, False, False, False)
    assert agents_visibility(_Cursor(row))["blind"] is False


def test_missing_relation_reports_blind(agents_visibility):
    assert agents_visibility(_Cursor(None))["blind"] is True


def test_a_probe_that_raises_reports_blind(agents_visibility):
    """The probe must not crash the DAG, but an unanswered question is never
    an all-clear."""
    result = agents_visibility(_Cursor(raises=RuntimeError("connection reset")))
    assert result["blind"] is True
    assert "connection reset" in result["reason"]
