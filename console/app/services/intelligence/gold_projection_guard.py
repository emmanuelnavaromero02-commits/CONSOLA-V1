from __future__ import annotations

import re
from datetime import datetime
from typing import Any


_BENCHMARK_DATASET = "sap_successfactors_talent_benchmark_internal"
_DERIVED_DATASETS = set(
    "sap_successfactors_talent_readiness sap_successfactors_talent_9box "
    "sap_successfactors_talent_9box_operational sap_successfactors_talent_operational_features "
    "sap_successfactors_talent_simulation_inputs".split()
)
_BENCHMARK_FIELDS = set(
    "benchmark_raw_score benchmark_score benchmark_performance_proxy "
    "benchmark_potential_proxy benchmark_performance_percentile "
    "benchmark_potential_percentile".split()
)
_CLASSIFICATION_FIELDS = set(
    "readiness_score performance_band potential_band box_key box_label confidence".split()
)
_ATTESTATION_FIELDS = set(
    "benchmark_version approval_source approved_by approved_at approval_actor_source "
    "approval_evidence_ref approval_authorization_ref".split()
)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parseable_timestamp(value: Any) -> bool:
    if isinstance(value, datetime):
        return True
    if not _nonempty(value):
        return False
    text = value.strip()
    try:
        datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return False
    return True


def _server_actor_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"[1-9][0-9]*", text) is None:
        return None
    return int(text)


def _benchmark_attested(row: dict[str, Any]) -> bool:
    return (
        row.get("approved") is True
        and _server_actor_id(row.get("approved_by")) is not None
        and _parseable_timestamp(row.get("approved_at"))
        and row.get("approval_actor_source") == "server"
        and row.get("approval_recorded_by_server") is True
        and row.get("approval_authorization_verified") is True
        and _nonempty(row.get("approval_evidence_ref"))
        and _nonempty(row.get("approval_authorization_ref"))
    )


def _positive_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _claims_benchmark_result(row: dict[str, Any]) -> bool:
    provenance = row.get("benchmark_provenance_status")
    return (
        row.get("source_mode") == "benchmark_internal"
        or row.get("readiness_status") == "benchmark_internal"
        or row.get("box_status") == "benchmark_internal"
        or row.get("benchmark_raw_score") is not None
        or row.get("benchmark_score") is not None
        or _positive_count(row.get("readiness_benchmark_count"))
        or _positive_count(row.get("benchmark_count"))
        or provenance not in {None, "", "approved_durable", "not_applicable"}
    )


def _durable_benchmark_result(row: dict[str, Any]) -> bool:
    return (
        row.get("benchmark_approval_valid") is True
        and row.get("benchmark_provenance_status") == "approved_durable"
    )


def _update_present(row: dict[str, Any], **updates: Any) -> None:
    for field, value in updates.items():
        if field in row:
            row[field] = value


def _clear_present(row: dict[str, Any], fields: set[str]) -> None:
    for field in fields.intersection(row):
        row[field] = None


def _sanitize_benchmark_row(row: dict[str, Any]) -> None:
    _clear_present(row, _ATTESTATION_FIELDS)
    _update_present(
        row,
        approved=False,
        approval_valid=False,
        approval_recorded_by_server=False,
        approval_authorization_verified=False,
        approval_status="unreviewed",
    )


def _degrade_benchmark_result(dataset: str, row: dict[str, Any]) -> None:
    _clear_present(row, _BENCHMARK_FIELDS | _CLASSIFICATION_FIELDS)
    _update_present(row, source_mode="insufficient_data")
    if dataset == "sap_successfactors_talent_readiness":
        _update_present(
            row,
            readiness_status="insufficient_data",
            readiness_label="Datos insuficientes",
            blocker_count=None,
        )
    elif dataset.startswith("sap_successfactors_talent_9box"):
        _update_present(row, box_status="blocked", ready_count=0, benchmark_count=0)
    elif dataset == "sap_successfactors_talent_operational_features":
        cpa_count = row.get("readiness_cpa_real_count")
        if not isinstance(cpa_count, int) or isinstance(cpa_count, bool):
            cpa_count = None
        _update_present(
            row,
            feature_status="partial",
            readiness_status="insufficient_data",
            calculable_count=cpa_count,
            calculable_employee_count=cpa_count,
            readiness_benchmark_count=0,
        )
    elif dataset == "sap_successfactors_talent_simulation_inputs":
        _update_present(
            row,
            input_status="blocked",
            readiness_status="insufficient_data",
            scenario_count=0,
            input_variables_json=None,
            escenarios=None,
            blocked_reason="stale_unapproved_benchmark",
        )


def project_operational_truth_rows(
    dataset: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        if dataset == _BENCHMARK_DATASET and not _benchmark_attested(row):
            _sanitize_benchmark_row(row)
        elif (
            dataset in _DERIVED_DATASETS
            and _claims_benchmark_result(row)
            and not _durable_benchmark_result(row)
        ):
            if (
                dataset == "sap_successfactors_talent_readiness"
                and row.get("source_mode") == "cpa_real"
            ):
                _clear_present(row, _BENCHMARK_FIELDS)
            else:
                _degrade_benchmark_result(dataset, row)
        projected.append(row)
    return projected
