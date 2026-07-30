from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from airflow.dags.runtime_security_context import (
    MATERIALIZE_PURPOSE,
    build_materialize_context,
    sign_runtime_context,
)
from refinement.app.runtime_security_context import validate_runtime_context


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "airflow/dags"))
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
        now=1000,
    )


def test_materialize_context_is_fresh_signed_and_request_bound(monkeypatch):
    context = _context(monkeypatch)

    assert context["trusted"] is True
    assert context["audience"] == "refinement"
    assert context["purpose"] == MATERIALIZE_PURPOSE
    assert context["tool"] == "materialize"
    assert context["dataset"] == "employee_360"
    assert context["_signature_version"] == "hmac-sha256-v1"
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


def test_fresh_signature_cannot_authorize_broader_prefixes(monkeypatch):
    context = _context(monkeypatch)
    payload = {
        key: value
        for key, value in context.items()
        if key not in {"_signature", "_signed_at", "_signature_version"}
    }
    payload["allowed_prefixes"] = ["raw/sap_successfactors/"]
    broadened = sign_runtime_context(payload, now=1000)

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
        )


class _TaskInstance:
    def __init__(self):
        self.pushed = None

    def xcom_pull(self, *, task_ids, key):
        if key == "plan":
            return [
                {
                    "name": "employee_360",
                    "layer": "gold",
                    "cartridge": "sap_successfactors",
                }
            ]
        if key == "cartridge_id":
            return "sap_successfactors"
        return None

    def xcom_push(self, *, key, value):
        self.pushed = (key, value)


def _materialize_context(allow_partial=True):
    return {
        "ti": _TaskInstance(),
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
    with pytest.raises(RuntimeError, match="failed closed"):
        dataset_refresh_materialize.materialize_in_order(
            _materialize_context(allow_partial=True),
            postgres_dsn="postgresql://unused",
            refinement_url="http://refinement",
            headers=lambda *_args: {},
        )
    assert finished == [
        {
            "slot_id": "slot-a",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "success": False,
        }
    ]


def test_successful_replay_reuses_durable_result_without_second_post(monkeypatch):
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "reserve_materialization",
        lambda *_args, **_kwargs: {
            "reserved": False,
            "completed": True,
            "result": {"name": "employee_360", "row_count": 7},
        },
    )
    monkeypatch.setattr(
        dataset_refresh_materialize.requests,
        "post",
        lambda *_args, **_kwargs: pytest.fail("replay performed a second POST"),
    )
    result = dataset_refresh_materialize.materialize_in_order(
        _materialize_context(),
        postgres_dsn="postgresql://unused",
        refinement_url="http://refinement",
        headers=lambda *_args: {},
    )
    assert result["materialized"] == 1
    assert result["results"] == [
        {
            "name": "employee_360",
            "ok": True,
            "reused": True,
            "row_count": 7,
        }
    ]


def test_refinement_timeout_fails_slot_and_never_completes_pipeline(monkeypatch):
    finished = []
    monkeypatch.setattr(
        dataset_refresh_materialize,
        "reserve_materialization",
        lambda *_args, **_kwargs: {
            "reserved": True,
            "completed": False,
            "slot_id": "slot-timeout",
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

    def timeout(*_args, **_kwargs):
        raise dataset_refresh_materialize.requests.Timeout("private endpoint")

    monkeypatch.setattr(dataset_refresh_materialize.requests, "post", timeout)
    with pytest.raises(RuntimeError, match="failed materializations"):
        dataset_refresh_materialize.materialize_in_order(
            _materialize_context(allow_partial=False),
            postgres_dsn="postgresql://unused",
            refinement_url="http://refinement",
            headers=lambda *_args: {},
        )
    assert finished[-1]["success"] is False
    assert finished[-1]["slot_id"] == "slot-timeout"
