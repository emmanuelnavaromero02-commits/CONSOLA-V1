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
