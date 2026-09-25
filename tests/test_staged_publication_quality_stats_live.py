from __future__ import annotations

import psycopg2
import pytest

from tests.staged_publication_live import (
    TENANT_A,
    WORKSPACE_A,
    LiveStack,
)
from tests.test_staged_publication_live import (  # noqa: F401 — fixture reuse
    staged_publication_live_stack,
)

PROBE_SQL = (
    "SELECT * FROM (VALUES"
    " (1::INTEGER, 'alpha'),"
    " (2::INTEGER, NULL),"
    " (2::INTEGER, 'beta'),"
    " (3::INTEGER, 'beta')"
    ") probe(entity_key, category)"
)

_DATA_CATALOG_DDL = """
CREATE TABLE IF NOT EXISTS public.data_catalog (
    dataset text NOT NULL,
    layer text NOT NULL,
    cartridge text NOT NULL,
    column_name text NOT NULL,
    data_type text NOT NULL,
    description text NOT NULL DEFAULT '',
    example_values text[],
    tags text[],
    is_key boolean NOT NULL DEFAULT FALSE,
    is_metric boolean NOT NULL DEFAULT FALSE,
    null_rate double precision,
    distinct_count bigint,
    min_value text,
    max_value text,
    profiled_at timestamptz,
    tenant_id uuid,
    workspace_id uuid,
    scope_status text NOT NULL DEFAULT 'scoped',
    updated_at timestamptz NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS data_catalog_scope_key
    ON public.data_catalog (workspace_id, dataset, column_name)
    WHERE workspace_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS public.data_relationships (
    from_dataset text NOT NULL,
    from_column text NOT NULL,
    to_dataset text NOT NULL,
    to_column text NOT NULL,
    join_hint text,
    description text,
    tenant_id uuid,
    workspace_id uuid,
    scope_status text NOT NULL DEFAULT 'scoped'
);
"""


def _stats_by_name(catalog: list[dict]) -> dict[str, dict]:
    return {str(field.get("name")): field for field in catalog}


def test_quality_stats_survive_the_staged_pipeline_end_to_end(
    staged_publication_live_stack: LiveStack,  # noqa: F811 — pytest fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = staged_publication_live_stack
    with psycopg2.connect(stack.admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(_DATA_CATALOG_DDL)
    for name, value in {
        "DATABASE_URL": stack.admin_dsn,
        "GOLD_DATABASE_URL": stack.reader_dsn,
        "GOLD_PUBLISHER_DATABASE_URL": stack.publisher_dsn,
        "MINIO_ENDPOINT": stack.minio_endpoint.removeprefix("http://"),
        "MINIO_ACCESS_KEY": "minio",
        "MINIO_SECRET_KEY": "minio-secret",
        "MINIO_BUCKET": "lakehouse",
        "MINIO_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    from refinement.app.staged_publication_engine import StagedPublicationEngine

    engine = StagedPublicationEngine()
    dataset = {
        "name": "quality_stats_probe",
        "layer": "silver",
        "cartridge": "acceptance",
        "sql_def": PROBE_SQL,
        "sources": [],
    }
    scope = {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A}
    result = engine.materialize(dataset, scope)
    assert result["row_count"] == 4

    evidence = stack.evidence("quality_stats_probe")
    assert len(evidence) == 1
    stats = _stats_by_name(evidence[0][3])
    entity_key = stats["entity_key"]
    category = stats["category"]
    assert entity_key["null_rate"] == 0.0
    assert entity_key["distinct_count"] == 3
    assert str(entity_key["min_value"]) == "1"
    assert str(entity_key["max_value"]) == "3"
    assert category["null_rate"] == 0.25
    assert category["distinct_count"] == 2

    with psycopg2.connect(stack.admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT column_name, null_rate, distinct_count, min_value,
                      max_value, profiled_at
                 FROM data_catalog
                WHERE dataset = 'quality_stats_probe'
                ORDER BY column_name""",
        )
        rows = {r[0]: r for r in cur.fetchall()}
    assert rows["entity_key"][2] == 3, "distinct_count must persist"
    assert rows["entity_key"][5] is not None, "profiled_at must be stamped"
    assert rows["category"][1] == 0.25, "null_rate must persist"

    schema = engine.get_dataset_schema(dataset, scope)
    assert "error" not in schema, schema.get("error")
    schema_stats = _stats_by_name(schema["fields"])
    assert schema_stats["entity_key"]["distinct_count"] == 3
    assert schema_stats["category"]["null_rate"] == 0.25

    parent_result = engine.materialize(
        {
            "name": "quality_parent_probe",
            "layer": "silver",
            "cartridge": "acceptance",
            "sql_def": (
                "SELECT * FROM (VALUES (1::INTEGER), (2::INTEGER),"
                " (3::INTEGER)) parent(entity_key)"
            ),
            "sources": [],
        },
        scope,
    )
    assert parent_result["row_count"] == 3
    with psycopg2.connect(stack.admin_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT dataset, column_name, data_type, distinct_count, null_rate
                 FROM data_catalog
                WHERE distinct_count IS NOT NULL AND workspace_id = %s::uuid""",
            (WORKSPACE_A,),
        )
        catalog_rows = [
            {
                "dataset": r[0],
                "column_name": r[1],
                "data_type": r[2],
                "distinct_count": r[3],
                "null_rate": r[4],
            }
            for r in cur.fetchall()
        ]
    from refinement.app.relationship_discovery import (
        discover_relationship_candidates,
    )

    candidates = discover_relationship_candidates(
        catalog_rows,
        {"quality_stats_probe": 4, "quality_parent_probe": 3},
    )
    edges = {
        (c.get("from_dataset"), c.get("from_column"),
         c.get("to_dataset"), c.get("to_column"))
        for c in candidates
    }
    assert (
        "quality_stats_probe",
        "entity_key",
        "quality_parent_probe",
        "entity_key",
    ) in edges, f"expected FK candidate from real profiler stats, got: {candidates}"

    if engine._con is not None:
        engine._con.close()
