from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from airflow.dags.runtime_security_context import (
    MATERIALIZE_SIGNATURE_VERSION,
    MATERIALIZE_PURPOSE,
    _sign_context,
    build_materialize_context,
)
from refinement.app.runtime_security_context import validate_runtime_context


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "airflow/dags"))
import dataset_refresh_idempotency
import dataset_refresh_materialize


KEY = "runtime-signing-key-that-is-long-and-isolated-123456"


def _context(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT", "transport-key")
    return build_materialize_context(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        cartridge_id="sap_successfactors",
        dataset_name="employee_360",
        run_id="scheduled__2026-07-30T12:00:00Z",
        now=1000,
    )


def test_materialize_context_is_fresh_signed_and_request_bound(monkeypatch):
    context = _context(monkeypatch)

    assert context["trusted"] is True
    assert context["audience"] == "refinement"
    assert context["purpose"] == MATERIALIZE_PURPOSE
    assert context["tool"] == "materialize"
    assert context["dataset"] == "employee_360"
    assert context["_signature_version"] == "hmac-sha256-v2"
    assert context["_signature"]
    validate_runtime_context(
        context,
        body={"tool": "materialize", "args": {"name": "employee_360"}},
        internal_service="airflow",
        now=1000,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant-b"),
        ("workspace_id", "workspace-b"),
        ("dataset", "other"),
        ("purpose", "refinement.mcp.query"),
        ("audience", "console"),
        ("source", "workspace"),
    ],
)
def test_signed_materialize_context_rejects_tampering(monkeypatch, field, value):
    context = _context(monkeypatch)
    context[field] = value

    with pytest.raises(ValueError):
        validate_runtime_context(
            context,
            body={"tool": "materialize", "args": {"name": "employee_360"}},
            internal_service="airflow",
            now=1000,
        )


def test_context_cannot_be_replayed_for_other_tool_or_dataset(monkeypatch):
    context = _context(monkeypatch)
    for body in (
        {"tool": "query_dataset", "args": {"name": "employee_360"}},
        {"tool": "materialize", "args": {"name": "other"}},
    ):
        with pytest.raises(ValueError):
            validate_runtime_context(
                copy.deepcopy(context),
                body=body,
                internal_service="airflow",
                now=1000,
            )


def test_context_rejects_unsigned_top_level_request_fields(monkeypatch):
    context = _context(monkeypatch)
    body = {
        "tool": "materialize",
        "args": {"name": "employee_360"},
        "security_context": context,
        "allow_partial": True,
    }

    with pytest.raises(ValueError, match="request fields"):
        validate_runtime_context(
            context,
            body=body,
            internal_service="airflow",
            now=1000,
        )


def test_fresh_signature_cannot_authorize_broader_prefixes(monkeypatch):
    context = _context(monkeypatch)
    payload = {
        key: value
        for key, value in context.items()
        if key not in {"_signature", "_signed_at", "_signature_version"}
    }
    payload["allowed_prefixes"] = ["raw/sap_successfactors/"]
    broadened = _sign_context(
        payload,
        version=MATERIALIZE_SIGNATURE_VERSION,
        now=1000,
    )

    with pytest.raises(ValueError, match="prefixes"):
        validate_runtime_context(
            broadened,
            body={"tool": "materialize", "args": {"name": "employee_360"}},
            internal_service="airflow",
            now=1000,
        )


def test_unsigned_expired_and_wrong_transport_contexts_fail_closed(monkeypatch):
    context = _context(monkeypatch)
    unsigned = {key: value for key, value in context.items() if key != "_signature"}
    cases = [
        (unsigned, "airflow", 1000),
        (context, "workspace", 1000),
        (context, "airflow", 1301),
    ]
    for candidate, service, now in cases:
        with pytest.raises(ValueError):
            validate_runtime_context(
                candidate,
                body={"tool": "materialize", "args": {"name": "employee_360"}},
                internal_service=service,
                now=now,
            )


def test_signing_key_must_not_reuse_transport_key(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT", KEY)

    with pytest.raises(RuntimeError, match="distinct"):
        build_materialize_context(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            cartridge_id="sap_successfactors",
            dataset_name="employee_360",
            run_id="scheduled__2026-07-30T12:00:00Z",
        )


_DEFAULT_PLAN = [
    {
        "name": "employee_360",
        "layer": "gold",
        "cartridge": "sap_successfactors",
    }
]


class _TaskInstance:
    def __init__(self, plan=None):
        self.pushed = None
        self._plan = _DEFAULT_PLAN if plan is None else plan

    def xcom_pull(self, *, task_ids, key):
        if key == "plan":
            return self._plan
        if key == "cartridge_id":
            return "sap_successfactors"
        return None

    def xcom_push(self, *, key, value):
        self.pushed = (key, value)


def _materialize_context(allow_partial=True, plan=None):
    return {
        "ti": _TaskInstance(plan),
        "run_id": "scheduled__2026-07-30T12:00:00Z",
        "dag_run": type(
            "DagRun",
            (),
            {
                "conf": {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "cartridge_id": "sap_successfactors",
                    "allow_partial": allow_partial,
                }
            },
        )(),
    }


def test_allow_partial_never_absorbs_runtime_authority_failure(monkeypatch):
    finished = []
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "reserve_materialization",
        lambda *_args, **_kwargs: {
            "reserved": True,
            "completed": False,
            "slot_id": "slot-a",
            "lease_token": 1,
        },
    )
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "finish_materialization",
        lambda *_args, **kwargs: finished.append(kwargs),
    )
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "build_materialize_context",
        lambda **_kwargs: {"trusted": True},
    )

    class _Rejected:
        status_code = 403

    monkeypatch.setattr(
        dataset_refresh_materialize.requests,
        "post",
        lambda *_args, **_kwargs: _Rejected(),
    )
    context = _materialize_context(allow_partial=True)
    with pytest.raises(RuntimeError, match="failed closed"):
        dataset_refresh_materialize.materialize_in_order(
            context,
            postgres_dsn="postgresql://unused",
            refinement_url="http://refinement",
            headers=lambda *_args: {},
            admitted_conf=context["dag_run"].conf,
        )
    assert finished == [
        {
            "slot_id": "slot-a",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "lease_token": 1,
            "success": False,
        }
    ]


@pytest.mark.parametrize("layer", [None, "", "raw", "GOLD"])
def test_durable_slot_refuses_to_record_a_success_without_a_valid_layer(layer):
    result = {"name": "employee_360", "row_count": 7}
    if layer is not None:
        result["layer"] = layer

    with pytest.raises(RuntimeError, match="layer is unavailable"):
        dataset_refresh_idempotency.finish_materialization(
            "postgresql://unused",
            slot_id="slot-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            lease_token=1,
            success=True,
            result=result,
        )


def _replay_reservation(monkeypatch, result):
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "reserve_materialization",
        lambda *_args, **_kwargs: {
            "reserved": False,
            "completed": True,
            "result": result,
        },
    )
    monkeypatch.setattr(
        dataset_refresh_materialize.requests,
        "post",
        lambda *_args, **_kwargs: pytest.fail("replay performed a second POST"),
    )


def _replay(context):
    return dataset_refresh_materialize.materialize_in_order(
        context,
        postgres_dsn="postgresql://unused",
        refinement_url="http://refinement",
        headers=lambda *_args: {},
        admitted_conf=context["dag_run"].conf,
    )


def test_successful_replay_reuses_durable_result_without_second_post(monkeypatch):
    _replay_reservation(monkeypatch, {"name": "employee_360", "row_count": 7})
    context = _materialize_context()

    result = _replay(context)

    assert result["materialized"] == 1
    assert result["results"] == [
        {
            "name": "employee_360",
            "layer": "gold",
            "ok": True,
            "reused": True,
            "row_count": 7,
        }
    ]


def test_replay_keeps_the_durable_layer_when_the_slot_recorded_one(monkeypatch):
    _replay_reservation(
        monkeypatch, {"name": "employee_360", "layer": "gold", "row_count": 7}
    )
    context = _materialize_context()

    assert _replay(context)["results"][0]["layer"] == "gold"


def test_replay_layer_mismatch_between_plan_and_slot_fails_closed(monkeypatch):
    _replay_reservation(
        monkeypatch, {"name": "employee_360", "layer": "silver", "row_count": 7}
    )
    context = _materialize_context()

    with pytest.raises(RuntimeError, match="contradicts the plan"):
        _replay(context)


@pytest.mark.parametrize("planned", ["", "raw", "GOLD"])
def test_replay_without_a_resolvable_layer_fails_closed(monkeypatch, planned):
    _replay_reservation(monkeypatch, {"name": "employee_360", "row_count": 7})
    context = _materialize_context(
        plan=[
            {
                "name": "employee_360",
                "layer": planned,
                "cartridge": "sap_successfactors",
            }
        ]
    )

    with pytest.raises(RuntimeError, match="layer is unavailable"):
        _replay(context)


def test_replay_with_an_invalid_durable_layer_fails_closed(monkeypatch):
    _replay_reservation(
        monkeypatch, {"name": "employee_360", "layer": "raw", "row_count": 7}
    )
    context = _materialize_context(
        plan=[{"name": "employee_360", "layer": "", "cartridge": "sap_successfactors"}]
    )

    with pytest.raises(RuntimeError, match="layer is unavailable"):
        _replay(context)
