import pytest
from fastapi import HTTPException

from app.domains.data_platform.bronze_query import bronze_query_payload
from app.domains.data_platform.catalog_payloads import (
    catalog_cache_key,
    catalog_query_args,
)
from app.domains.data_platform.catalog_requests import catalog_get_payload
from app.domains.data_platform.catalog_requests import (
    catalog_relationship_payload,
    catalog_upsert_payload,
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
    data_api_valid_dataset_name,
)
from app.domains.data_platform.schema_payloads import (
    bronze_schema_payload,
    dataset_detail_columns,
    empty_partitions,
    empty_preview,
    gold_schema_error_payload,
    normalize_preview_payload,
    normalize_dataset_detail,
    preview_has_columns,
    schema_error,
    schema_message,
    schema_payload_warnings,
    schema_status,
)
from app.domains.data_platform.schema_requests import schema_response_payload
from app.domains.data_platform.rag_payloads import (
    rag_context_from_results,
    rag_empty_answer,
    rag_search_arguments,
    rag_synthesis_messages,
)
from app.domains.data_platform.rag_requests import (
    rag_delete_source_payload,
    rag_ingest_payload,
    rag_reindex_payload,
    rag_search_payload,
    rag_sources_payload,
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


def test_normalize_preview_payload_accepts_columns_rows_and_fields_shapes():
    source = "raw/acme/User"

    columns_payload = normalize_preview_payload(
        source,
        {
            "columns": [
                {"column_name": "userId", "type": "VARCHAR"},
                "lastModifiedDateTime",
            ],
            "rows": [{"userId": "u1", "lastModifiedDateTime": "2026-07-01"}],
        },
    )
    assert columns_payload["schema"] == [
        {"column_name": "userId", "type": "VARCHAR", "name": "userId"},
        {"name": "lastModifiedDateTime"},
    ]
    assert columns_payload["columns"] == columns_payload["schema"]
    assert columns_payload["data"] == columns_payload["rows"]
    assert preview_has_columns({"fields": [{"name": "externalCode"}]})

    rows_only_payload = normalize_preview_payload(
        source,
        {"data": [{"externalCode": "100", "status": "A"}]},
    )
    assert rows_only_payload["schema"] == [{"name": "externalCode"}, {"name": "status"}]
    assert rows_only_payload["rows"] == [{"externalCode": "100", "status": "A"}]


def test_schema_response_payloads_keep_safe_statuses():
    error = schema_error("preview", HTTPException(404, "No files found"))

    gold_payload = gold_schema_error_payload("gold/acme/employees", error)
    assert gold_payload["source_kind"] == "gold"
    assert gold_payload["status"] == "error"
    assert gold_payload["message"] == "sin parquet materializado"
    assert gold_payload["preview"]["rows"] == []

    bronze_payload = bronze_schema_payload(
        "raw/acme/User",
        empty_partitions("raw/acme/User"),
        empty_preview("raw/acme/User"),
        [error],
    )
    assert bronze_payload["source_kind"] == "bronze"
    assert bronze_payload["status"] == "error"
    assert bronze_payload["errors"] == [error]

    normalized_bronze = bronze_schema_payload(
        "raw/acme/User",
        empty_partitions("raw/acme/User"),
        {"fields": [{"column_name": "userId"}], "rows": [{"userId": "u1"}]},
        [],
    )
    assert normalized_bronze["status"] == "ready"
    assert normalized_bronze["preview"]["schema"] == [{"column_name": "userId", "name": "userId"}]
    assert normalized_bronze["preview"]["data"] == [{"userId": "u1"}]


@pytest.mark.asyncio
async def test_schema_response_payload_handles_missing_bronze_source():
    async def refinement_invoke(_tool, _args, **_kwargs):
        raise HTTPException(404, "No files found")

    async def gold_schema_payload(_source, _user):
        raise AssertionError("gold schema should not be called")

    payload = await schema_response_payload(
        source="raw/acme/User",
        user=SCOPED_USER,
        gold_dataset_from_source=lambda _source: None,
        gold_schema_payload=gold_schema_payload,
        refinement_invoke=refinement_invoke,
        schema_error=schema_error,
        gold_schema_error_payload=gold_schema_error_payload,
        empty_partitions=empty_partitions,
        empty_preview=empty_preview,
        schema_payload_warnings=schema_payload_warnings,
        preview_has_columns=preview_has_columns,
        bronze_schema_payload=bronze_schema_payload,
    )

    assert payload["status"] == "error"
    assert payload["message"] == "sin parquet materializado"
    assert payload["preview"]["columns"] == []


@pytest.mark.asyncio
async def test_schema_response_payload_handles_missing_gold_source():
    async def refinement_invoke(_tool, _args, **_kwargs):
        raise AssertionError("bronze refinement should not be called")

    async def gold_schema_payload(_source, _user):
        raise HTTPException(404, "Dataset no materializado")

    payload = await schema_response_payload(
        source="gold/acme/employees",
        user=SCOPED_USER,
        gold_dataset_from_source=lambda _source: "employees",
        gold_schema_payload=gold_schema_payload,
        refinement_invoke=refinement_invoke,
        schema_error=schema_error,
        gold_schema_error_payload=gold_schema_error_payload,
        empty_partitions=empty_partitions,
        empty_preview=empty_preview,
        schema_payload_warnings=schema_payload_warnings,
        preview_has_columns=preview_has_columns,
        bronze_schema_payload=bronze_schema_payload,
    )

    assert payload["source_kind"] == "gold"
    assert payload["status"] == "error"
    assert payload["message"] == "sin parquet materializado"


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


@pytest.mark.asyncio
async def test_catalog_get_payload_returns_empty_for_scoped_user_without_cartridge():
    async def scope_catalog_cartridge_arg(_user, _cartridge):
        return None

    result = await catalog_get_payload(
        layer="gold",
        cartridge="",
        tags="",
        datasets="",
        user=SCOPED_USER,
        scope_catalog_cartridge_arg=scope_catalog_cartridge_arg,
        user_allowed_cartridges=lambda _user: ["sap_successfactors"],
        empty_catalog_payload=lambda: {"datasets": {}},
        catalog_query_args=catalog_query_args,
        catalog_cache_key=catalog_cache_key,
        refinement_invoke=lambda *_args, **_kwargs: None,
        raise_for_refinement_payload_error=lambda _payload, _fallback: None,
        scoped_read_cache_get_or_set=lambda *_args, **_kwargs: None,
    )

    assert result == {"datasets": {}}


@pytest.mark.asyncio
async def test_catalog_get_payload_uses_scoped_cache_and_refinement():
    captured = {}

    async def scope_catalog_cartridge_arg(_user, cartridge):
        assert cartridge == ""
        return "sap_successfactors"

    async def refinement_invoke(tool, args, **kwargs):
        captured["tool"] = tool
        captured["args"] = args
        captured["user"] = kwargs["user"]
        return {"datasets": ["gold_ready"]}

    def raise_for_refinement_payload_error(payload, fallback):
        captured["checked"] = (payload, fallback)

    async def scoped_read_cache_get_or_set(scope, user, key, loader):
        captured["cache"] = (scope, user, key)
        return await loader()

    result = await catalog_get_payload(
        layer="gold",
        cartridge="",
        tags="talent, kpi",
        datasets="employee_profile",
        user=SCOPED_USER,
        scope_catalog_cartridge_arg=scope_catalog_cartridge_arg,
        user_allowed_cartridges=lambda _user: ["sap_successfactors"],
        empty_catalog_payload=lambda: {"datasets": {}},
        catalog_query_args=catalog_query_args,
        catalog_cache_key=catalog_cache_key,
        refinement_invoke=refinement_invoke,
        raise_for_refinement_payload_error=raise_for_refinement_payload_error,
        scoped_read_cache_get_or_set=scoped_read_cache_get_or_set,
    )

    assert result == {"datasets": ["gold_ready"]}
    assert captured["args"] == {
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "tags": ["talent", "kpi"],
        "datasets": ["employee_profile"],
    }
    assert captured["tool"] == "get_data_catalog"
    assert captured["user"] == SCOPED_USER
    assert captured["checked"] == ({"datasets": ["gold_ready"]}, "Refinement catalog failed")
    assert captured["cache"] == (
        "catalog",
        SCOPED_USER,
        (
            '{"cartridge": "sap_successfactors", "datasets": ["employee_profile"], '
            '"layer": "gold", "tags": ["talent", "kpi"]}',
        ),
    )


@pytest.mark.asyncio
async def test_catalog_upsert_payload_invalidates_scoped_cache():
    captured = {}

    async def refinement_invoke(tool, args, **kwargs):
        captured["tool"] = tool
        captured["args"] = args
        captured["user"] = kwargs["user"]
        return {"updated": 1}

    def raise_for_refinement_payload_error(payload, fallback):
        captured["checked"] = (payload, fallback)

    def scoped_read_cache_invalidate(scope, user):
        captured["cache"] = (scope, user)

    result = await catalog_upsert_payload(
        body={"entries": [{"dataset": "gold_ready", "column_name": "employee_count"}]},
        user=SCOPED_USER,
        refinement_invoke=refinement_invoke,
        raise_for_refinement_payload_error=raise_for_refinement_payload_error,
        scoped_read_cache_invalidate=scoped_read_cache_invalidate,
    )

    assert result == {"updated": 1}
    assert captured["tool"] == "upsert_catalog_entries"
    assert captured["args"]["entries"][0]["dataset"] == "gold_ready"
    assert captured["user"] == SCOPED_USER
    assert captured["checked"] == ({"updated": 1}, "Refinement catalog update failed")
    assert captured["cache"] == ("catalog", SCOPED_USER)


@pytest.mark.asyncio
async def test_catalog_relationship_payload_invalidates_scoped_cache():
    captured = {}

    async def refinement_invoke(tool, args, **kwargs):
        captured["tool"] = tool
        captured["args"] = args
        captured["user"] = kwargs["user"]
        return {"created": 1}

    def raise_for_refinement_payload_error(payload, fallback):
        captured["checked"] = (payload, fallback)

    def scoped_read_cache_invalidate(scope, user):
        captured["cache"] = (scope, user)

    result = await catalog_relationship_payload(
        body={"from_dataset": "gold_a", "to_dataset": "gold_b"},
        user=SCOPED_USER,
        refinement_invoke=refinement_invoke,
        raise_for_refinement_payload_error=raise_for_refinement_payload_error,
        scoped_read_cache_invalidate=scoped_read_cache_invalidate,
    )

    assert result == {"created": 1}
    assert captured["tool"] == "register_relationship"
    assert captured["args"]["to_dataset"] == "gold_b"
    assert captured["user"] == SCOPED_USER
    assert captured["checked"] == ({"created": 1}, "Refinement relationship update failed")
    assert captured["cache"] == ("catalog", SCOPED_USER)


@pytest.mark.asyncio
async def test_bronze_query_payload_uses_rewritten_sql_and_rls_context():
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"rows": [{"id": 1}]}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    result = await bronze_query_payload(
        body={"sql": "select * from raw_table", "sources": []},
        user=SCOPED_USER,
        merge_sources=lambda sources, _sql: list(sources or []) + ["raw/acme/User"],
        rewrite_paths=lambda sql, _user: sql.replace("raw_table", "scoped_s3_path"),
        rls_user_context=lambda user: {"tenant_id": user["tenant_id"]},
        headers_factory=lambda service: {"x-service": service},
        mcp_payload=lambda tool, args, user: {"tool": tool, "args": args, "user": user},
        refinement_url="http://refinement",
        upstream_error_detail=lambda _response, fallback: fallback,
        raise_for_refinement_payload_error=lambda _payload, _fallback: None,
        http_client_factory=FakeClient,
    )

    assert result == {"rows": [{"id": 1}]}
    assert captured["client_kwargs"]["headers"] == {"x-service": "REFINEMENT"}
    assert captured["json"]["tool"] == "preview_transform"
    assert captured["json"]["args"]["sql"] == "select * from scoped_s3_path"
    assert captured["json"]["args"]["sources"] == ["raw/acme/User"]
    assert captured["json"]["args"]["user_context"] == {"tenant_id": "tenant-a"}


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
    assert data_api_valid_dataset_name("gold_sales")
    assert data_api_valid_dataset_name("_gold_sales_2026")
    assert not data_api_valid_dataset_name("bad-name")
    assert not data_api_valid_dataset_name("1bad")
    assert not data_api_valid_dataset_name("")
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


@pytest.mark.asyncio
async def test_rag_sources_payload_uses_scoped_headers_and_filters():
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"sources": [{"name": "manual"}]}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def get(self, url, params=None):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    result = await rag_sources_payload(
        kinds="dataset",
        user=SCOPED_USER,
        rag_url="http://rag",
        http_client_factory=FakeClient,
        headers_for_user=lambda user: {"x-workspace": user["workspace_id"]},
    )

    assert result == {"sources": [{"name": "manual"}]}
    assert captured["client_kwargs"]["headers"] == {"x-workspace": "workspace-a"}
    assert captured["url"] == "http://rag/rag/sources"
    assert captured["params"] == {"kinds": "dataset"}


@pytest.mark.asyncio
async def test_rag_delete_source_payload_handles_not_found():
    class FakeResponse:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("404 should be converted before raise_for_status")

        def json(self):
            return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def delete(self, _url):
            return FakeResponse()

    with pytest.raises(HTTPException) as exc:
        await rag_delete_source_payload(
            source_id=123,
            user=SCOPED_USER,
            rag_url="http://rag",
            http_client_factory=lambda **_kwargs: FakeClient(),
            headers_for_user=lambda _user: {},
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Source not found"


@pytest.mark.asyncio
async def test_rag_delete_source_payload_returns_deleted_source():
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"deleted": True}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def delete(self, url):
            captured["url"] = url
            return FakeResponse()

    result = await rag_delete_source_payload(
        source_id=123,
        user=SCOPED_USER,
        rag_url="http://rag",
        http_client_factory=FakeClient,
        headers_for_user=lambda user: {"x-tenant": user["tenant_id"]},
    )

    assert result == {"deleted": True}
    assert captured["client_kwargs"]["headers"] == {"x-tenant": "tenant-a"}
    assert captured["url"] == "http://rag/rag/sources/123"


@pytest.mark.asyncio
async def test_rag_search_payload_preserves_mcp_shape():
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"result": {"results": [{"source": "x"}]}}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    result = await rag_search_payload(
        body={"query": "skills", "top_k": 7, "source_ids": [1], "kinds": ["schema"]},
        user=SCOPED_USER,
        rag_url="http://rag",
        http_client_factory=FakeClient,
        headers_factory=lambda service: {"x-service": service},
        mcp_payload_factory=lambda tool, args, user: {
            "tool": tool,
            "args": args,
            "user": user,
        },
        upstream_error_detail=lambda _response, fallback: fallback,
    )

    assert result == {"results": [{"source": "x"}]}
    assert captured["client_kwargs"]["headers"] == {"x-service": "MCP_INFRA"}
    assert captured["url"] == "http://rag/mcp/invoke"
    assert captured["json"]["tool"] == "search_rag"
    assert captured["json"]["args"] == {
        "query": "skills",
        "top_k": 7,
        "source_ids": [1],
        "kinds": ["schema"],
    }
    assert captured["json"]["user"] == SCOPED_USER


@pytest.mark.asyncio
async def test_rag_search_payload_raises_safe_upstream_error():
    class FakeResponse:
        status_code = 502

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json):
            return FakeResponse()

    with pytest.raises(HTTPException) as exc:
        await rag_search_payload(
            body={},
            user=SCOPED_USER,
            rag_url="http://rag",
            http_client_factory=lambda **_kwargs: FakeClient(),
            headers_factory=lambda _service: {},
            mcp_payload_factory=lambda tool, args, user: {
                "tool": tool,
                "args": args,
                "user": user,
            },
            upstream_error_detail=lambda _response, fallback: f"{fallback}: down",
        )

    assert exc.value.status_code == 502
    assert exc.value.detail == "RAG search failed: down"


@pytest.mark.asyncio
async def test_rag_reindex_payload_validates_dataset_scope_and_adds_security_context():
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    async def refinement_invoke(tool, args, **kwargs):
        assert tool == "list_datasets"
        assert args == {}
        assert kwargs["user"] == SCOPED_USER
        return {
            "datasets": [
                {"name": "employees", "cartridge": "sap_successfactors"}
            ]
        }

    visible = []
    result = await rag_reindex_payload(
        body={
            "kind": "dataset",
            "cartridge": "sap_successfactors",
            "name": "employees",
        },
        user=SCOPED_USER,
        rag_url="http://rag",
        http_client_factory=FakeClient,
        headers_factory=lambda service: {"x-service": service},
        upstream_error_detail=lambda _response, fallback: fallback,
        refinement_invoke=refinement_invoke,
        require_cartridge_visible=lambda _user, cartridge: visible.append(cartridge),
        build_security_context=lambda user: {"tenant_id": user["tenant_id"]},
    )

    assert result == {"ok": True}
    assert visible == ["sap_successfactors"]
    assert captured["client_kwargs"]["headers"] == {"x-service": "MCP_INFRA"}
    assert captured["url"] == "http://rag/rag/reindex"
    assert captured["json"]["security_context"] == {"tenant_id": "tenant-a"}


@pytest.mark.asyncio
async def test_rag_reindex_payload_rejects_dataset_outside_cartridge():
    async def refinement_invoke(_tool, _args, **_kwargs):
        return {
            "datasets": [
                {"name": "other", "cartridge": "sap_successfactors"}
            ]
        }

    with pytest.raises(HTTPException) as exc:
        await rag_reindex_payload(
            body={
                "kind": "dataset",
                "cartridge": "sap_successfactors",
                "name": "missing",
            },
            user=SCOPED_USER,
            rag_url="http://rag",
            http_client_factory=lambda **_kwargs: None,
            headers_factory=lambda _service: {},
            upstream_error_detail=lambda _response, fallback: fallback,
            refinement_invoke=refinement_invoke,
            require_cartridge_visible=lambda _user, _cartridge: None,
            build_security_context=lambda _user: {},
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_rag_ingest_payload_adds_security_context():
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"source": "manual"}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    result = await rag_ingest_payload(
        body={"name": "manual", "content": "texto"},
        user=SCOPED_USER,
        rag_url="http://rag",
        http_client_factory=FakeClient,
        headers_factory=lambda service: {"x-service": service},
        upstream_error_detail=lambda _response, fallback: fallback,
        build_security_context=lambda user: {"workspace_id": user["workspace_id"]},
    )

    assert result == {"source": "manual"}
    assert captured["client_kwargs"]["headers"] == {"x-service": "MCP_INFRA"}
    assert captured["url"] == "http://rag/rag/ingest"
    assert captured["json"]["security_context"] == {"workspace_id": "workspace-a"}


@pytest.mark.asyncio
async def test_rag_ingest_payload_raises_safe_upstream_error():
    class FakeResponse:
        status_code = 503

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json):
            return FakeResponse()

    with pytest.raises(HTTPException) as exc:
        await rag_ingest_payload(
            body={"name": "manual"},
            user=SCOPED_USER,
            rag_url="http://rag",
            http_client_factory=lambda **_kwargs: FakeClient(),
            headers_factory=lambda _service: {},
            upstream_error_detail=lambda _response, fallback: f"{fallback}: timeout",
            build_security_context=lambda _user: {},
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "RAG ingest failed: timeout"


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
