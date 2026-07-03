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
from app.domains.data_platform.rag_payloads import (
    rag_context_from_results,
    rag_empty_answer,
    rag_search_arguments,
    rag_synthesis_messages,
)
from app.domains.data_platform.semantic_enrichment import (
    semantic_enrichment_candidates,
    semantic_enrichment_empty_response,
    semantic_enrichment_limit,
    semantic_enrichment_success_response,
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


def test_semantic_enrichment_response_helpers_keep_direct_mode_contract():
    candidates = {
        "entries": [{"dataset": "gold_ready", "column_name": "employee_count"}],
        "scanned_datasets": 2,
        "scanned_columns": 4,
    }

    assert semantic_enrichment_limit({"limit": "999"}) == 200
    assert semantic_enrichment_limit({"limit": "bad"}) == 80
    empty = semantic_enrichment_empty_response(
        cartridge="sap_successfactors",
        candidate_payload={**candidates, "entries": []},
    )
    assert empty["approval_required"] is False
    assert empty["enriched"] == 0
    assert empty["message"] == "No hay columnas pendientes de descripción en el catálogo visible."

    success = semantic_enrichment_success_response(
        cartridge="sap_successfactors",
        candidate_payload=candidates,
        result={"updated": "1"},
    )
    assert success["mode"] == "direct_semantic_enrichment"
    assert success["candidate_count"] == 1
    assert success["enriched"] == 1
    assert success["entries_preview"] == candidates["entries"]


def test_rag_payload_helpers_preserve_existing_request_shape():
    assert rag_search_arguments(
        {
            "query": "  quien falta  ",
            "top_k": "3",
            "source_ids": [],
            "kinds": ["dataset"],
        }
    ) == {
        "query": "quien falta",
        "top_k": 3,
        "source_ids": None,
        "kinds": ["dataset"],
    }
    assert rag_empty_answer() == {
        "answer": "No encontré información relacionada en las fuentes ingeridas.",
        "results": [],
    }


def test_rag_synthesis_messages_build_cited_context():
    results = [
        {"source_name": "dataset_a", "context": "Dato A"},
        {"source_name": "", "child_content": "Dato B"},
    ]

    assert rag_context_from_results(results) == (
        "[1] Fuente: dataset_a\nDato A\n\n---\n\n[2] Fuente: ?\nDato B"
    )
    messages = rag_synthesis_messages("Que pasa?", results)
    assert "ÚNICAMENTE el contexto provisto" in messages["system"]
    assert "[1] Fuente: dataset_a" in messages["user"]
    assert "Pregunta: Que pasa?" in messages["user"]


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
