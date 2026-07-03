from fastapi import HTTPException

from app.domains.data_platform.schema_payloads import (
    dataset_detail_columns,
    empty_partitions,
    empty_preview,
    normalize_dataset_detail,
    schema_error,
    schema_message,
    schema_status,
)
from app.domains.data_platform.semantic_enrichment import (
    semantic_enrichment_candidates,
)
from app.domains.data_platform.source_visibility import (
    dataset_source_visible_for_user,
    is_security_admin_context,
    sanitize_dataset_metadata_for_user,
    technical_source_allowed,
)


SCOPED_USER = {
    "id": "u1",
    "role": "viewer",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "allowed_cartridges": ["sap_successfactors"],
}


def test_schema_payload_helpers_return_safe_empty_states():
    assert empty_partitions("raw/acme/User", "error", "sin parquet materializado") == {
        "source": "raw/acme/User",
        "partitions": [],
        "latest": None,
        "sql_latest": None,
        "status": "error",
        "message": "sin parquet materializado",
    }
    assert empty_preview("raw/acme/User", "empty", "sin columnas inferidas") == {
        "source": "raw/acme/User",
        "schema": [],
        "columns": [],
        "rows": [],
        "data": [],
        "status": "empty",
        "message": "sin columnas inferidas",
    }


def test_schema_error_and_message_classify_missing_parquet():
    error = schema_error("preview", HTTPException(404, "No files found"))
    assert error["reason"] == "source_files_missing"
    assert error["message"] == "sin parquet materializado"
    assert schema_status([error], {"columns": []}) == "error"
    assert schema_message("error", [error]) == "sin parquet materializado"


def test_dataset_detail_columns_normalizes_multiple_shapes():
    columns = dataset_detail_columns(
        {
            "schema": [
                {"column_name": "userId", "type": "VARCHAR"},
                "lastModifiedDateTime",
            ]
        }
    )
    assert columns == [
        {"column_name": "userId", "type": "VARCHAR", "name": "userId"},
        {"name": "lastModifiedDateTime"},
    ]


def test_normalize_dataset_detail_uses_injected_sanitizer():
    def sanitizer(user, definition):
        assert user == {"id": "u1"}
        return {**definition, "sql": "select masked", "sources": ["raw/acme/User"]}

    detail = normalize_dataset_detail(
        {"name": "employees", "layer": "silver", "row_count": 0, "sql": "select pii"},
        {"columns": [{"name": "user_id"}]},
        user={"id": "u1"},
        sanitize_dataset_metadata=sanitizer,
    )
    assert detail["status"] == "empty"
    assert detail["sql"] == "select masked"
    assert detail["sources"] == ["raw/acme/User"]


def test_semantic_enrichment_candidates_describes_missing_columns():
    payload = semantic_enrichment_candidates(
        {
            "datasets": {
                "employee_profile": {
                    "cartridge": "sap_successfactors",
                    "layer": "gold",
                    "columns": [
                        {"name": "employee_id", "type": "VARCHAR", "description": ""},
                        {"name": "status", "description": "Estado ya definido"},
                    ],
                },
                "other_dataset": {
                    "cartridge": "other",
                    "columns": [{"name": "ignored"}],
                },
            }
        },
        cartridge="sap_successfactors",
        limit=10,
    )
    assert payload["scanned_datasets"] == 1
    assert payload["entries"][0]["dataset"] == "employee_profile"
    assert payload["entries"][0]["column_name"] == "employee_id"
    assert "key" in payload["entries"][0]["tags"]


def test_technical_source_visibility_requires_active_scope():
    assert technical_source_allowed(
        SCOPED_USER,
        "raw/sap_successfactors/User/tenant_id=tenant-a/workspace_id=workspace-a/part.parquet",
    )
    assert not technical_source_allowed(
        SCOPED_USER,
        "raw/sap_successfactors/User/tenant_id=tenant-b/workspace_id=workspace-a/part.parquet",
    )
    assert not technical_source_allowed(
        SCOPED_USER,
        "raw/sap_successfactors/User/tenant_id=tenant-a/workspace_id=workspace-b/part.parquet",
    )
    assert not technical_source_allowed(
        SCOPED_USER,
        "raw/salesforce/Account/tenant_id=tenant-a/workspace_id=workspace-a/part.parquet",
    )


def test_dataset_source_visibility_rejects_unscoped_physical_references():
    assert dataset_source_visible_for_user(SCOPED_USER, "raw/sap_successfactors/User")
    assert not dataset_source_visible_for_user(
        SCOPED_USER,
        "s3://lakehouse/raw/sap_successfactors/User/tenant_id=tenant-b/workspace_id=workspace-a/part.parquet",
    )
    assert not dataset_source_visible_for_user(
        SCOPED_USER,
        "s3://lakehouse/raw/sap_successfactors/User/part.parquet",
    )


def test_dataset_metadata_sanitizer_strips_disallowed_sources():
    sanitized = sanitize_dataset_metadata_for_user(
        SCOPED_USER,
        {
            "name": "employee_profile",
            "sources": [
                "raw/sap_successfactors/User",
                "raw/salesforce/Account",
                "s3://lakehouse/raw/sap_successfactors/User/tenant_id=tenant-b/workspace_id=workspace-a/part.parquet",
            ],
            "metadata": {
                "sources": [
                    "silver/sap_successfactors/employee_profile",
                    "gold/salesforce/opportunities",
                ]
            },
        },
    )

    assert sanitized["sources"] == ["raw/sap_successfactors/User"]
    assert sanitized["metadata"]["sources"] == [
        "silver/sap_successfactors/employee_profile"
    ]


def test_security_admin_context_can_see_all_sources():
    assert is_security_admin_context(
        {
            "trusted": True,
            "role": "super_admin",
            "tenant_id": "",
            "workspace_id": "",
            "allowed_cartridges": ["*"],
        }
    )
