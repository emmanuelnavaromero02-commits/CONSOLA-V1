from __future__ import annotations

from app.core.extraction_status import classify_extraction_exception


def test_classifies_missing_connection_as_config_incomplete():
    result = classify_extraction_exception(
        "JobApplication",
        RuntimeError(
            "SAP SuccessFactors entity JobApplication requires entity_config.connection_id "
            "or dag_run.conf conn_id/connection_id; no default connection fallback is allowed."
        ),
    )

    assert result["entity"] == "JobApplication"
    assert result["status"] == "auth-blocked"
    assert result["code"] == "CONFIG_INCOMPLETE"


def test_classifies_successfactors_missing_odata_entity_as_metadata_blocked():
    result = classify_extraction_exception(
        "GoalPlan",
        RuntimeError(
            "SuccessFactors rechazo solicitud OData (HTTP 404) para Goal: "
            "NotFoundException Entity Goal is not found"
        ),
    )

    assert result["entity"] == "GoalPlan"
    assert result["status"] == "permission-blocked"
    assert result["code"] == "SUCCESSFACTORS_METADATA_BLOCKED"
    assert result["failure_code"] == "successfactors_metadata_invalid"
    assert result["http_status"] == 404
    assert "error" not in result


def test_real_extraction_error_payload_never_contains_url_query_body_or_token():
    sentinel = (
        "SuccessFactors rechazo solicitud OData (HTTP 400) "
        "url=https://tenant.example/odata/v2/User?$filter=email%20eq%20'a@b.test' "
        "body={access_token:SENTINEL-TOKEN,employee:SENTINEL-PII}"
    )

    result = classify_extraction_exception("User", RuntimeError(sentinel))

    assert result == {
        "entity": "User",
        "status": "permission-blocked",
        "code": "SUCCESSFACTORS_METADATA_BLOCKED",
        "failure_code": "successfactors_metadata_invalid",
        "http_status": 400,
    }
    assert "SENTINEL" not in repr(result)
    assert "tenant.example" not in repr(result)
