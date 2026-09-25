from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")

from app.core.extraction_status import (
    hard_failure_code,
    summarize_extraction_results,
)

ROOT = Path(__file__).resolve().parents[1]


def _summary(**statuses: int) -> dict[str, int]:
    results = []
    for status, count in statuses.items():
        results.extend({"status": status.replace("_", "-")} for _ in range(count))
    return summarize_extraction_results(results)


def test_failed_open_with_nothing_produced_is_a_hard_failure():
    summary = _summary(failed_open=2, permission_blocked=1)
    assert hard_failure_code(summary, [], [], attempted=3) == "extraction_failed"


def test_a_mixed_run_stays_partial_so_gold_and_the_cascade_still_run():
    summary = _summary(extracted=24, failed_open=1)
    assert hard_failure_code(summary, [], [], attempted=25) is None


def test_credentials_rejected_for_every_entity_is_a_hard_failure():
    summary = _summary(auth_blocked=1, permission_blocked=2)
    assert hard_failure_code(summary, [], [], attempted=3) == "successfactors_access_denied"


def test_one_entity_extracted_keeps_the_run_partial():
    summary = _summary(extracted=1, permission_blocked=5)
    assert hard_failure_code(summary, [], [], attempted=6) is None
    summary = _summary(empty_valid=1, auth_blocked=2)
    assert hard_failure_code(summary, [], [], attempted=3) is None


def test_metadata_preflight_unreachable_in_the_plan_fails_the_run():
    skipped = [
        {
            "entity": "PerPerson",
            "status": "skipped_explicit",
            "code": "SUCCESSFACTORS_METADATA_BLOCKED",
            "failure_code": "metadata_upstream_unavailable",
        }
    ]
    summary = _summary(skipped_explicit=1)
    assert (
        hard_failure_code(summary, [], skipped, attempted=0)
        == "successfactors_metadata_unavailable"
    )
    denied = [{**skipped[0], "failure_code": "metadata_access_denied"}]
    assert hard_failure_code(summary, [], denied, attempted=0) == "successfactors_access_denied"


def test_metadata_guard_rejecting_every_attempted_entity_fails_the_run():
    results = [
        {
            "entity": entity,
            "status": "skipped_explicit",
            "code": "SUCCESSFACTORS_METADATA_BLOCKED",
            "failure_code": "metadata_upstream_unavailable",
        }
        for entity in ("PerPerson", "EmpJob", "FOCompany")
    ]
    summary = _summary(skipped_explicit=3)
    assert (
        hard_failure_code(summary, results, [], attempted=3)
        == "successfactors_metadata_unavailable"
    )


def test_metadata_not_found_or_invalid_stays_partial():
    summary = _summary(skipped_explicit=2)
    for code in ("metadata_not_found", "metadata_query_invalid"):
        skipped = [
            {"entity": "X", "code": "SUCCESSFACTORS_METADATA_BLOCKED", "failure_code": code}
        ]
        assert hard_failure_code(summary, [], skipped, attempted=0) is None
    plain = [{"entity": "X", "status": "blocked", "reason": "not_scoped_for_connection"}]
    assert hard_failure_code(summary, [], plain, attempted=0) is None


def test_missing_configuration_is_named_not_mislabelled_as_access_denied():
    results = [
        {"entity": "PerPerson", "status": "auth-blocked", "failure_code": "configuration_incomplete"}
    ]
    summary = _summary(auth_blocked=1)
    assert hard_failure_code(summary, results, [], attempted=1) == "configuration_incomplete"


def _ctx() -> dict:
    from app.core import request_context

    return request_context._sign_security_context(
        {
            "trusted": True,
            "source": "console",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
    )


def _route(monkeypatch, plan, run_entity):
    from app.api import routes_console

    monkeypatch.setattr(routes_console, "preflight_for_extract", lambda **_kwargs: None)
    monkeypatch.setattr(routes_console, "_mark_external_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes_console, "get_extract_all_plan", lambda **_kwargs: plan)
    monkeypatch.setattr(routes_console, "run_entity", run_entity)
    return routes_console


def test_console_extract_all_is_failed_when_every_entity_dies_on_the_network(monkeypatch):
    def fake_run_entity(config, **_kwargs):
        raise RuntimeError("HTTPSConnectionPool: read timed out")

    routes_console = _route(
        monkeypatch, ([{"entity": "FOCompany"}, {"entity": "EmpJob"}], []), fake_run_entity
    )
    response = routes_console.extract_all(
        mode="incremental", conn_id="tenant_sf", body={"security_context": _ctx()}
    )
    assert response["status"] == "failed"
    assert response["hard_failure"] == "extraction_failed"
    assert response["summary"]["failed_open"] == 2


def test_console_extract_all_is_failed_when_credentials_are_rejected_everywhere(monkeypatch):
    from app.core.sap_client import SAPClientError

    def fake_run_entity(config, **_kwargs):
        raise SAPClientError(
            "SuccessFactors rechazo acceso OData (HTTP 401) para entity="
            f"{config['entity']} (successfactors_access_denied)."
        )

    routes_console = _route(
        monkeypatch, ([{"entity": "FOCompany"}, {"entity": "EmpJob"}], []), fake_run_entity
    )
    response = routes_console.extract_all(
        mode="incremental", conn_id="tenant_sf", body={"security_context": _ctx()}
    )
    assert response["status"] == "failed"
    assert response["hard_failure"] == "successfactors_access_denied"
    assert response["summary"]["permission_blocked"] == 2


def test_console_extract_all_is_failed_when_metadata_preflight_is_unreachable(monkeypatch):
    plan = (
        [],
        [
            {
                "entity": "PerPerson",
                "status": "skipped",
                "reason": "metadata_unavailable",
                "code": "SUCCESSFACTORS_METADATA_BLOCKED",
                "metadata_status": "metadata_unavailable",
                "failure_code": "metadata_upstream_unavailable",
            }
        ],
    )
    routes_console = _route(monkeypatch, plan, lambda *_a, **_k: pytest.fail("no entity runs"))
    response = routes_console.extract_all(
        mode="incremental", conn_id="tenant_sf", body={"security_context": _ctx()}
    )
    assert response["status"] == "failed"
    assert response["hard_failure"] == "successfactors_metadata_unavailable"


def test_console_extract_all_stays_completed_with_blocks_for_a_partial_tenant(monkeypatch):
    from app.core.sap_client import SAPClientError

    def fake_run_entity(config, **_kwargs):
        if config["entity"] == "EmpJob":
            raise SAPClientError("SuccessFactors rechazo acceso OData (HTTP 403) para entity=EmpJob")
        return {"entity": config["entity"], "status": "success", "record_count": 7}

    routes_console = _route(
        monkeypatch, ([{"entity": "FOCompany"}, {"entity": "EmpJob"}], []), fake_run_entity
    )
    response = routes_console.extract_all(
        mode="incremental", conn_id="tenant_sf", body={"security_context": _ctx()}
    )
    assert response["status"] == "completed_with_blocks"
    assert response["hard_failure"] is None


def test_extract_all_dag_records_and_raises_hard_failures():
    source = (ROOT / "dags" / "sap_successfactors_extract_all.py").read_text(encoding="utf-8")

    assert "hard_failed = False" not in source, "the aggregate must not be a constant partial"
    assert re.search(
        r"hard_failure = runtime\.hard_failure_code\(\s*summary, results, skipped, attempted=len\(entities\)",
        source,
    )
    assert "hard_failure_code=extraction_status.hard_failure_code" in source
    saved_at = source.index("aggregate_saved = True")
    raised_at = source.index("raise AirflowFailException(hard_failure)")
    assert saved_at < raised_at
    assert "from airflow.exceptions import AirflowFailException" in source
    assert "_HardExtractionFailure" not in source
    assert "isinstance(exc, AirflowFailException)" in source
    assert "error_message=hard_failure" in source
    assert '{"failure_code": outcome.get("failure_code")}' in source


def test_async_job_uses_the_same_rule():
    source = (ROOT / "app" / "core" / "job_runner.py").read_text(encoding="utf-8")

    assert 'final_status = "failed" if result_summary["failed_open"] else "done"' not in source
    assert re.search(
        r"hard_failure = hard_failure_code\(\s*result_summary, results, skipped_results, attempted=len\(entities\)",
        source,
    )
    assert 'final_status = "failed" if hard_failure else "done"' in source
