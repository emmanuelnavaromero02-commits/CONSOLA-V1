from fastapi import HTTPException

from app.domains.data_platform.catalog_payloads import (
    catalog_cache_key,
    catalog_query_args,
)
from app.domains.data_platform.data_api_payloads import (
    DataApiQueryValidationError,
    data_api_columns_param,
    data_api_filtered_query,
    data_api_invalid_column,
    data_api_options_response,
    data_api_options_sql,
    data_api_query_limit,
    data_api_select_clause,
)
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
from app.domains.data_platform.refinement_errors import (
    payload_error_detail,
    refinement_error_status,
    upstream_error_detail,
)
from app.domains.data_platform.semantic_enrichment import (
    semantic_enrichment_candidates,
    semantic_enrichment_empty_response,
    semantic_enrichment_limit,
    semantic_enrichment_success_response,
)
from app.domains.data_platform.semantic_payloads import (
    semantic_entities_with_catalog,
    semantic_manifest_response,
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


def test_catalog_payload_helpers_build_stable_filter_args():
    args = catalog_query_args(
        layer="gold",
        cartridge="sap_successfactors",
        tags=" metric, , talent ",
        datasets="employee_profile, talent_9box",
    )
    assert args == {
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "tags": ["metric", "talent"],
        "datasets": ["employee_profile", "talent_9box"],
    }
    assert catalog_cache_key({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'


def test_refinement_error_helpers_classify_payload_errors():
    assert (
        payload_error_detail(
            {
                "code": "SOURCE_FILES_MISSING",
                "error": "No files found for dataset",
                "raw_error": "s3://private/path",
            }
        )
        == "SOURCE_FILES_MISSING: No files found for dataset: s3://private/path"
    )
    assert refinement_error_status("SOURCE_FILES_MISSING: no files found") == 404
    assert refinement_error_status("permission denied") == 403
    assert refinement_error_status("timeout waiting for refinement") == 503
    assert refinement_error_status("sql is required") == 400
    assert refinement_error_status("unexpected") == 502


def test_upstream_error_detail_reads_nested_result_payload():
    class Response:
        text = ""

        def json(self):
            return {"result": {"message": "nested failure"}}

    assert upstream_error_detail(Response()) == "nested failure"


def test_data_api_payload_helpers_preserve_options_contract():
    columns = data_api_columns_param(" revenue_manager, ,cliente ")
    assert columns == ["revenue_manager", "cliente"]
    assert data_api_columns_param("") == []
    assert data_api_invalid_column(["good_column", "bad column"]) == "bad column"
    assert data_api_invalid_column(["good_column"]) is None
    assert data_api_options_sql("gold_sales", columns) == (
        "SELECT DISTINCT revenue_manager AS val, 'revenue_manager' AS col "
        "FROM pggold.gold_gold_sales WHERE revenue_manager IS NOT NULL UNION ALL "
        "SELECT DISTINCT cliente AS val, 'cliente' AS col "
        "FROM pggold.gold_gold_sales WHERE cliente IS NOT NULL ORDER BY col, val"
    )
    assert data_api_options_response(columns, []) == []
    assert data_api_options_response(
        columns,
        [
            {"col": "revenue_manager", "val": "Ana"},
            {"col": "cliente", "val": 123},
            {"col": "ignored", "val": "x"},
            {"col": "cliente", "val": None},
        ],
    ) == {"revenue_manager": ["Ana"], "cliente": ["123"]}


def test_data_api_filtered_query_builds_parameterized_sql():
    query = data_api_filtered_query(
        "ventas",
        filters={
            "region": ["Norte", "Sur"],
            "unsafe-col": "ignored",
            "empty": "",
            "fiscal_year": [2025],
        },
        limit=data_api_query_limit("99999"),
        columns=["region", "bad-col", "*"],
    )

    assert query.limit == 10000
    assert query.params == ["Norte", "Sur", "2025"]
    assert "Norte" not in query.sql
    assert "unsafe-col" not in query.sql
    assert "bad-col" not in query.sql
    assert "region IN (?,?)" in query.sql
    assert "EXTRACT(MONTH FROM mes)" in query.sql
    assert query.sql.endswith("LIMIT 10000")
    assert data_api_select_clause(["valid", "bad-col"]) == "valid"
    assert data_api_select_clause(["bad-col"]) == "*"


def test_data_api_filtered_query_rejects_unsafe_filter_shapes():
    try:
        data_api_filtered_query("ventas", filters=[], limit=100, columns=["*"])
    except DataApiQueryValidationError as exc:
        assert exc.status_code == 400
        assert exc.detail == "filters must be an object"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected DataApiQueryValidationError")

    try:
        data_api_filtered_query(
            "ventas",
            filters={"region": ["v"] * 101},
            limit=100,
            columns=["*"],
        )
    except DataApiQueryValidationError as exc:
        assert "100" in exc.detail
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected DataApiQueryValidationError")


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


def test_semantic_payload_helpers_merge_manifest_and_gold_catalog_entities():
    manifest = {
        "entities": [
            {"entity": "User", "display_name": "Usuarios"},
            {"name": "talent_9box", "layer": "gold"},
        ]
    }
    catalog_entities = [
        {"name": "talent_9box", "layer": "gold"},
        {"name": "talent_operational_features", "layer": "gold"},
    ]

    entities = semantic_entities_with_catalog(manifest["entities"], catalog_entities)
    assert [item.get("entity") or item.get("name") for item in entities] == [
        "User",
        "talent_9box",
        "talent_operational_features",
    ]
    response = semantic_manifest_response(
        cartridge="sap_successfactors",
        manifest=manifest,
        catalog_entities=catalog_entities,
    )
    assert response["server"] is manifest
    assert response["entities"] == entities


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
