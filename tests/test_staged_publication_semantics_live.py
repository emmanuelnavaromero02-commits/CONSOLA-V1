from __future__ import annotations

import psycopg2

from refinement.app.publication_public import published_catalog, published_lineage
from tests.staged_publication_canaries import TENANT_A, WORKSPACE_A
from tests.test_operational_rls_console_refinement import (
    OMEGA_REFINEMENT_PASSWORD,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    postgres_with_real_init_schema,
)
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_live import staged_publication_live_stack


def _refinement_dsn(admin_dsn: str) -> str:
    return admin_dsn.replace(
        f"{POSTGRES_USER}:{POSTGRES_PASSWORD}",
        f"omega_refinement:{OMEGA_REFINEMENT_PASSWORD}",
    )


def test_public_semantics_are_snapshotted_into_immutable_head_evidence(
    postgres_with_real_init_schema: str,
    staged_publication_live_stack,
    monkeypatch,
) -> None:
    with psycopg2.connect(postgres_with_real_init_schema) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants(id,name,slug,status) "
            "VALUES(%s,'Semantic Tenant','semantic-tenant','active')",
            (TENANT_A,),
        )
        cur.execute(
            "INSERT INTO workspaces(id,tenant_id,name) "
            "VALUES(%s,%s,'Semantic Workspace')",
            (WORKSPACE_A, TENANT_A),
        )
        cur.execute(
            """INSERT INTO data_catalog(
                   dataset,layer,cartridge,column_name,data_type,description,
                   example_values,tags,is_key,is_metric,
                   tenant_id,workspace_id,scope_status
               ) VALUES(
                   'semantic_snapshot','gold','acceptance','value','INTEGER',
                   'Valor publicado','[1,2]'::jsonb,ARRAY['finance'],FALSE,TRUE,
                   %s,%s,'scoped')""",
            (TENANT_A, WORKSPACE_A),
        )
        cur.execute(
            """INSERT INTO data_relationships(
                   from_dataset,from_column,to_dataset,to_column,join_hint,
                   description,tenant_id,workspace_id,scope_status
               ) VALUES(
                   'semantic_snapshot','value','employees','employee_id','LEFT',
                   'Empleado responsable',%s,%s,'scoped')""",
            (TENANT_A, WORKSPACE_A),
        )

    monkeypatch.setenv("DATABASE_URL", _refinement_dsn(postgres_with_real_init_schema))
    engine = _engine(staged_publication_live_stack, monkeypatch)
    engine.materialize(
        {
            "name": "semantic_snapshot",
            "layer": "gold",
            "cartridge": "acceptance",
            "description": "Indicadores financieros",
            "sources": [],
            "sql_def": "SELECT 1::INTEGER AS value",
        },
        _scope(),
    )
    with psycopg2.connect(postgres_with_real_init_schema) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE data_catalog SET description='Mutado',tags=ARRAY['mutable'] "
            "WHERE workspace_id=%s AND dataset='semantic_snapshot'",
            (WORKSPACE_A,),
        )
        cur.execute(
            "UPDATE data_relationships SET description='Mutada' "
            "WHERE workspace_id=%s AND from_dataset='semantic_snapshot'",
            (WORKSPACE_A,),
        )

    evidence = staged_publication_live_stack.evidence("semantic_snapshot")[0]
    lineage, catalog = evidence[2], evidence[3]
    value = next(item for item in catalog if item["name"] == "value")
    assert value["description"] == "Valor publicado"
    assert value["tags"] == ["finance"]
    assert value["is_metric"] is True
    assert value["example_values"] == [1, 2]
    assert lineage["public_metadata"]["description"] == "Indicadores financieros"
    relation = lineage["public_metadata"]["relationships"][0]
    assert relation["from_dataset"] == "semantic_snapshot"
    assert relation["description"] == "Empleado responsable"
    dataset = {
        "name": "semantic_snapshot",
        "layer": "gold",
        "cartridge": "acceptance",
    }
    public = published_catalog([dataset], _scope(), tags=["finance"])
    assert public["datasets"]["semantic_snapshot"]["description"] == (
        "Indicadores financieros"
    )
    assert public["datasets"]["semantic_snapshot"]["columns"][0]["tags"] == ["finance"]
    assert public["relationships"][0]["description"] == "Empleado responsable"
    edge = published_lineage(dataset, _scope())["lineage"][0]
    assert edge["silver_name"] == "semantic_snapshot"
    assert edge["row_count"] == 1
