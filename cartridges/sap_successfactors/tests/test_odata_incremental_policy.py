from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=")

from app.core.sap_client import SAPClientError
from app.services import extraction_service


def _stub_run(monkeypatch, *, run_id: str = "run-odata-policy") -> None:
    monkeypatch.setattr(extraction_service, "create_run", lambda **_kwargs: run_id)
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(
        extraction_service,
        "write_parquet_and_upload",
        lambda **kwargs: "s3://lakehouse/" + str(kwargs["entity"]),
    )


def test_incremental_filter_normalizes_sap_date_watermark() -> None:
    filter_expr, plan = extraction_service._build_incremental_filter(
        entity="PerPerson",
        watermark_field="lastModifiedDateTime",
        watermark_value="/Date(1767225600000+0000)/",
    )

    assert filter_expr == "lastModifiedDateTime gt datetime'2026-01-01T00:00:00'"
    assert plan["watermark_value_type"] == "sap_date_ms"
    assert plan["filter_strategy"] == "server_filter"
    assert "'/Date(" not in filter_expr


def test_incremental_filter_falls_back_when_watermark_is_future() -> None:
    future = "/Date(4102444800000+0000)/"

    filter_expr, plan = extraction_service._build_incremental_filter(
        entity="PerPerson",
        watermark_field="lastModifiedDateTime",
        watermark_value=future,
    )

    assert filter_expr is None
    assert plan["filter_strategy"] == "full_snapshot"
    assert plan["fallback_reason"] == "future_or_unparseable_watermark"


def test_client_side_watermark_filter_compares_sap_dates() -> None:
    rows = [
        {"id": "old", "lastModifiedDateTime": "/Date(1767225599000+0000)/"},
        {"id": "new", "lastModifiedDateTime": "/Date(1767225601000+0000)/"},
    ]

    out = extraction_service._apply_watermark_filter(
        rows,
        "lastModifiedDateTime",
        "2026-01-01T00:00:00Z",
    )

    assert out == [{"id": "new", "lastModifiedDateTime": "/Date(1767225601000+0000)/"}]


def test_run_entity_retries_full_snapshot_when_incremental_filter_is_rejected(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            assert conn_id == "femsa_sf"

        def fetch_entity(self, **kwargs):
            calls.append(kwargs)
            if kwargs["skip"]:
                return []
            if kwargs["filter_expr"]:
                raise SAPClientError("GET https://example/PerPerson failed: 400 Client Error: Bad Request")
            return [
                {
                    "personIdExternal": "1",
                    "lastModifiedDateTime": "/Date(1767225601000+0000)/",
                }
            ]

    _stub_run(monkeypatch)
    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: "/Date(1767225600000+0000)/")
    updated: dict = {}
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **kwargs: updated.update(kwargs))

    result = extraction_service.run_entity(
        {
            "entity": "PerPerson",
            "mode": "incremental",
            "watermark_field": "lastModifiedDateTime",
            "conn_id": "femsa_sf",
            "select_fields": ["personIdExternal", "lastModifiedDateTime"],
        }
    )

    assert calls[0]["filter_expr"] == "lastModifiedDateTime gt datetime'2026-01-01T00:00:00'"
    assert calls[1]["filter_expr"] is None
    assert result["status"] == "success"
    assert result["record_count"] == 1
    assert result["retried_as_full_snapshot"] is True
    assert result["incremental_fallback_reason"] == "incremental_filter_rejected_full_snapshot"
    assert updated["last_watermark_value"] == "2025-12-31T23:55:01Z"


def test_run_entity_preserves_expected_columns_after_metadata_select_pruning(monkeypatch) -> None:
    captured: dict = {}

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            assert conn_id == "femsa_sf"

        def fetch_entity(self, **kwargs):
            captured["select"] = kwargs["select"]
            if kwargs["skip"]:
                return []
            return [{"jobReqId": "10", "status": "open", "lastModifiedDateTime": "2026-06-25T00:00:00Z"}]

    _stub_run(monkeypatch)
    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)

    def fake_write(**kwargs):
        captured["expected_columns"] = kwargs["expected_columns"]
        return "s3://lakehouse/JobRequisition"

    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", fake_write)

    result = extraction_service.run_entity(
        {
            "entity": "JobRequisition",
            "mode": "incremental",
            "watermark_field": "lastModifiedDateTime",
            "conn_id": "femsa_sf",
            "select_fields": ["jobReqId", "status", "lastModifiedDateTime"],
            "expected_select_fields": ["jobReqId", "jobTitle", "status", "lastModifiedDateTime"],
            "metadata_status": "select_pruned",
            "metadata_pruned_fields": ["jobTitle"],
        }
    )

    assert captured["select"] == ["jobReqId", "status", "lastModifiedDateTime"]
    assert "jobTitle" in captured["expected_columns"]
    assert result["metadata_status"] == "select_pruned"
    assert result["metadata_pruned_fields"] == ["jobTitle"]


def test_run_entity_copies_metadata_alias_fields_before_parquet_write(monkeypatch) -> None:
    captured: dict = {}

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            assert conn_id == "femsa_sf"

        def fetch_entity(self, **kwargs):
            captured["select"] = kwargs["select"]
            if kwargs["skip"]:
                return []
            return [
                {
                    "externalCode": "review-1",
                    "worker": "u-1",
                    "rating": "4",
                    "lastModifiedDateTime": "2026-06-25T00:00:00Z",
                }
            ]

    _stub_run(monkeypatch)
    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)

    def fake_write(**kwargs):
        captured["rows"] = kwargs["rows"]
        captured["expected_columns"] = kwargs["expected_columns"]
        return "s3://lakehouse/PerformanceReview"

    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", fake_write)

    result = extraction_service.run_entity(
        {
            "entity": "PerformanceReview",
            "odata_entity": "cust_TalentPerformanceReview",
            "mode": "incremental",
            "watermark_field": "lastModifiedDateTime",
            "conn_id": "femsa_sf",
            "select_fields": ["externalCode", "worker", "rating", "lastModifiedDateTime"],
            "metadata_field_aliases": {
                "formSubjectId": "worker",
                "overallRating": "rating",
            },
        }
    )

    assert captured["select"] == ["externalCode", "worker", "rating", "lastModifiedDateTime"]
    assert captured["rows"][0]["formSubjectId"] == "u-1"
    assert captured["rows"][0]["overallRating"] == "4"
    assert "formSubjectId" in captured["expected_columns"]
    assert "overallRating" in captured["expected_columns"]
    assert result["record_count"] == 1


def test_token_400_is_not_treated_as_incremental_filter_rejection(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            return None

        def fetch_entity(self, **kwargs):
            calls.append(kwargs)
            raise SAPClientError("SAML bearer token request failed: 400 Client Error for url: https://api68sales.successfactors.com/oauth/token")

    _stub_run(monkeypatch)
    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: "/Date(1767225600000+0000)/")
    failures: dict = {}
    monkeypatch.setattr(extraction_service, "fail_run", lambda **kwargs: failures.update(kwargs))

    with pytest.raises(SAPClientError):
        extraction_service.run_entity(
            {
                "entity": "EmpJob",
                "mode": "incremental",
                "watermark_field": "lastModifiedDateTime",
                "conn_id": "femsa_sf",
                "select_fields": ["userId", "lastModifiedDateTime"],
            }
        )

    assert "oauth/token" in failures["error_message"]
    assert len(calls) == 1


def test_saved_watermark_normalizes_sap_payload_date(monkeypatch) -> None:
    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            return None

        def fetch_entity(self, **kwargs):
            if kwargs["skip"]:
                return []
            return [{"id": "1", "lastModifiedDateTime": "/Date(1767225900000+0000)/"}]

    _stub_run(monkeypatch)
    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    updated: dict = {}
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **kwargs: updated.update(kwargs))

    result = extraction_service.run_entity(
        {
            "entity": "PerEmail",
            "mode": "incremental",
            "watermark_field": "lastModifiedDateTime",
            "conn_id": "femsa_sf",
        }
    )

    assert result["record_count"] == 1
    assert updated["last_watermark_value"] == "2026-01-01T00:00:00Z"


def test_watermark_datetime_parser_accepts_iso_with_timezone() -> None:
    parsed = extraction_service._parse_watermark_datetime("2026-01-01T00:00:00Z")

    assert parsed == datetime(2026, 1, 1, tzinfo=timezone.utc)
