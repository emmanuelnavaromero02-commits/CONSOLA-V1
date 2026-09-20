"""Live PostgreSQL proof for the aggregate-only BigQuery shadow baseline."""

from __future__ import annotations

import json
import uuid

import asyncpg
import psycopg2
import pytest
from fastapi import HTTPException

from console.app.services.intelligence import bigquery_shadow
from tests.test_gold_native_rls_contract import (
    GOLD_ROLE_PASSWORD,
    POSTGRES_PASSWORD as GOLD_POSTGRES_PASSWORD,
    postgres_gold_with_native_rls,  # noqa: F401
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,  # noqa: F401
)


DATASET = "sap_successfactors_talent_9box"
CHECKSUM = "a" * 64
SCHEMA_DIGEST = "b" * 64
EVIDENCE_DIGEST = "c" * 64


class _PoolLease:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._connection: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._connection = await self._pool.acquire()
        return self._connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self._connection is not None
        await self._pool.release(self._connection)


class _PoolAdapter:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def acquire(self) -> _PoolLease:
        return _PoolLease(self._pool)


def _seed_gold(
    dsn: str,
    *,
    tenant_id: str,
    workspace_id: str,
    source_run_id: str,
    generation: int = 1,
) -> None:
    table = f"run_{uuid.UUID(source_run_id).hex}"
    object_key = (
        f"gold/sap_successfactors/{DATASET}/tenant_id={tenant_id}/"
        f"workspace_id={workspace_id}/_snapshots/_pending/"
        f"{uuid.UUID(source_run_id).hex}/{CHECKSUM}.parquet"
    )
    uri = f"gs://omega-gold/{object_key}"
    input_digest = "d" * 64
    contract_digest = "e" * 64
    candidate_digest = "f" * 64
    attestation_digest = "1" * 64
    candidate_id = str(uuid.uuid4())
    attestation_id = str(uuid.uuid4())
    receipt_id = str(uuid.uuid4())

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE omega_publication_gold.{table} (
                    tenant_id uuid NOT NULL,
                    workspace_id uuid NOT NULL,
                    invalid_score_input boolean NOT NULL,
                    performance_score numeric,
                    potential_score numeric,
                    box_status text,
                    box_key text,
                    performance_band_available text,
                    source_mode text
                )
                """
            )
            cur.execute(
                f"""
                INSERT INTO omega_publication_gold.{table} VALUES
                  (%s,%s,FALSE,90,90,'ready','estrella','high','cpa_real'),
                  (%s,%s,FALSE,70,70,'ready','core','medium','cpa_real'),
                  (%s,%s,TRUE,NULL,NULL,'blocked',NULL,NULL,'cpa_real')
                """,
                (
                    tenant_id,
                    workspace_id,
                    tenant_id,
                    workspace_id,
                    tenant_id,
                    workspace_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO omega_publication.materialization_runs(
                    materialization_run_id,tenant_id,workspace_id,dataset,layer,
                    input_digest,contract_digest,object_uri,object_version,
                    object_checksum,row_count,schema_digest,evidence_digest,
                    gold_table,status,published_at
                ) VALUES(%s,%s,%s,%s,'gold',%s,%s,%s,'9007199254740993',
                         %s,3,%s,%s,%s,'published',clock_timestamp())
                """,
                (
                    source_run_id,
                    tenant_id,
                    workspace_id,
                    DATASET,
                    input_digest,
                    contract_digest,
                    uri,
                    CHECKSUM,
                    SCHEMA_DIGEST,
                    EVIDENCE_DIGEST,
                    table,
                ),
            )
            cur.execute(
                """
                INSERT INTO omega_publication.materialization_verification_candidates(
                    candidate_id,materialization_run_id,tenant_id,workspace_id,
                    dataset,layer,attempt,object_uri,object_version,
                    object_checksum,row_count,schema_digest,input_digest,
                    contract_digest,lineage,catalog,candidate_digest,status,
                    expires_at
                ) VALUES(%s,%s,%s,%s,%s,'gold',1,%s,'9007199254740993',
                         %s,3,%s,%s,%s,'{}','[]',%s,'attested',
                         clock_timestamp()+interval '1 day')
                """,
                (
                    candidate_id,
                    source_run_id,
                    tenant_id,
                    workspace_id,
                    DATASET,
                    uri,
                    CHECKSUM,
                    SCHEMA_DIGEST,
                    input_digest,
                    contract_digest,
                    candidate_digest,
                ),
            )
            cur.execute(
                """
                INSERT INTO omega_publication.materialization_attestations(
                    attestation_id,materialization_run_id,tenant_id,workspace_id,
                    dataset,layer,attempt,object_uri,object_version,
                    object_checksum,row_count,schema_digest,input_digest,
                    contract_digest,lineage,catalog,verifier_identity,
                    attestation_digest,candidate_id,signature_version,
                    key_version,attestation_signature,expires_at
                ) VALUES(%s,%s,%s,%s,%s,'gold',1,%s,'9007199254740993',
                         %s,3,%s,%s,%s,'{}','[]',
                         'refinement.parquet-verifier/v1',%s,%s,
                         'hmac-sha256-v1','v1',%s,
                         clock_timestamp()+interval '1 day')
                """,
                (
                    attestation_id,
                    source_run_id,
                    tenant_id,
                    workspace_id,
                    DATASET,
                    uri,
                    CHECKSUM,
                    SCHEMA_DIGEST,
                    input_digest,
                    contract_digest,
                    attestation_digest,
                    candidate_id,
                    "2" * 64,
                ),
            )
            cur.execute(
                """
                INSERT INTO omega_publication.materialization_evidence(
                    materialization_run_id,tenant_id,workspace_id,dataset,layer,
                    object_uri,object_version,object_checksum,row_count,
                    schema_digest,evidence_digest,attestation_id,lineage,catalog
                ) VALUES(%s,%s,%s,%s,'gold',%s,'9007199254740993',%s,3,
                         %s,%s,%s,'{}','[]')
                """,
                (
                    source_run_id,
                    tenant_id,
                    workspace_id,
                    DATASET,
                    uri,
                    CHECKSUM,
                    SCHEMA_DIGEST,
                    EVIDENCE_DIGEST,
                    attestation_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO omega_publication.materialization_receipts(
                    receipt_id,materialization_run_id,tenant_id,workspace_id,
                    dataset,layer,input_digest,contract_digest,object_checksum,
                    object_version,schema_digest,evidence_digest,row_count,generation
                ) VALUES(%s,%s,%s,%s,%s,'gold',%s,%s,%s,'9007199254740993',
                         %s,%s,3,%s)
                """,
                (
                    receipt_id,
                    source_run_id,
                    tenant_id,
                    workspace_id,
                    DATASET,
                    input_digest,
                    contract_digest,
                    CHECKSUM,
                    SCHEMA_DIGEST,
                    EVIDENCE_DIGEST,
                    generation,
                ),
            )
            cur.execute(
                """
                INSERT INTO omega_publication.dataset_publication_heads(
                    tenant_id,workspace_id,dataset,layer,
                    materialization_run_id,generation,published_at
                ) VALUES(%s,%s,%s,'gold',%s,%s,clock_timestamp())
                ON CONFLICT (tenant_id,workspace_id,dataset,layer)
                DO UPDATE SET
                    materialization_run_id=EXCLUDED.materialization_run_id,
                    generation=EXCLUDED.generation,
                    published_at=EXCLUDED.published_at
                """,
                (tenant_id, workspace_id, DATASET, source_run_id, generation),
            )
            cur.execute(
                f"ALTER TABLE omega_publication_gold.{table} OWNER TO omega_gold_owner"
            )
            cur.execute(
                f"ALTER TABLE omega_publication_gold.{table} ENABLE ROW LEVEL SECURITY"
            )
            cur.execute(
                f"ALTER TABLE omega_publication_gold.{table} FORCE ROW LEVEL SECURITY"
            )
            cur.execute(
                f"""
                CREATE POLICY published_head_scope ON omega_publication_gold.{table}
                FOR SELECT TO omega_refinement_gold USING (
                    tenant_id::text=NULLIF(current_setting('app.tenant_id',true),'')
                    AND workspace_id::text=NULLIF(current_setting('app.workspace_id',true),'')
                    AND EXISTS (
                        SELECT 1 FROM omega_publication.dataset_publication_heads h
                         WHERE h.tenant_id::text=NULLIF(current_setting('app.tenant_id',true),'')
                           AND h.workspace_id::text=NULLIF(current_setting('app.workspace_id',true),'')
                           AND h.materialization_run_id='{source_run_id}'::uuid
                    )
                )
                """
            )
            cur.execute(
                f"""
                CREATE POLICY publication_owner_scope
                  ON omega_publication_gold.{table}
                  TO omega_gold_owner USING (true)
                """
            )
            cur.execute(
                f"GRANT SELECT ON omega_publication_gold.{table} TO omega_refinement_gold"
            )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_baseline_uses_exact_verified_head_and_rejects_cross_tenant_scope(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    postgres_gold_with_native_rls: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    workspace_a = str(uuid.uuid4())
    workspace_b = str(uuid.uuid4())
    source_run_id = str(uuid.uuid4())
    pipeline_run_id = f"dataset_refresh_chain:manual__{uuid.uuid4().hex}"

    operational = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await operational.execute(
            """
            INSERT INTO tenants(id,name,slug) VALUES
              ($1,'BQ shadow tenant A',$2),($3,'BQ shadow tenant B',$4)
            """,
            tenant_a,
            f"bq-shadow-a-{uuid.uuid4().hex}",
            tenant_b,
            f"bq-shadow-b-{uuid.uuid4().hex}",
        )
        await operational.execute(
            """
            INSERT INTO workspaces(id,tenant_id,name) VALUES
              ($1,$2,'Main Workspace'),($3,$4,'Foreign Workspace')
            """,
            workspace_a,
            tenant_a,
            workspace_b,
            tenant_b,
        )
        await operational.execute(
            """
            INSERT INTO pipeline_runs(
                run_id,dag_id,cartridge_id,entity,status,tenant_id,workspace_id,extra
            ) VALUES($1,'dataset_refresh_chain','sap_successfactors',
                     'DatasetRefreshChain','success',$2,$3,$4::jsonb)
            """,
            pipeline_run_id,
            tenant_a,
            workspace_a,
            json.dumps(
                {
                    "results": [
                        {
                            "name": DATASET,
                            "layer": "gold",
                            "ok": True,
                            "publication_run_id": source_run_id,
                        }
                    ]
                }
            ),
        )
    finally:
        await operational.close()

    _seed_gold(
        postgres_gold_with_native_rls,
        tenant_id=tenant_a,
        workspace_id=workspace_a,
        source_run_id=source_run_id,
    )
    gold_role_dsn = postgres_gold_with_native_rls.replace(
        f"postgres:{GOLD_POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    monkeypatch.setenv("BIGQUERY_TALENT_9BOX_SHADOW_ENABLED", "true")
    monkeypatch.setenv(
        "BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST",
        f"{tenant_a},{tenant_b}",
    )
    monkeypatch.setenv(
        "BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST", workspace_a
    )
    monkeypatch.setenv("GOLD_DATABASE_URL", gold_role_dsn)

    async def scoped_pool() -> _PoolAdapter:
        return _PoolAdapter(pool)

    monkeypatch.setattr(bigquery_shadow.auth, "pool", scoped_pool)
    try:
        baseline = await bigquery_shadow.build_talent_9box_baseline(
            tenant_id=tenant_a,
            workspace_id=workspace_a,
            cartridge_id="sap_successfactors",
            dataset=DATASET,
            pipeline_run_id=pipeline_run_id,
            source_run_id=source_run_id,
        )
        assert baseline["manifest"]["source_run_id"] == source_run_id
        assert baseline["manifest"]["row_count"] == 3
        assert baseline["totals"] == {
            "population": 3,
            "assigned_to_cell": 2,
            "classified": 2,
            "unclassified": 1,
        }
        assert baseline["cohorts"] == {"high": 1, "medium": 1, "low": 0}

        # The Gold role itself can see the exact scoped head and no rows when
        # tenant/workspace context is crossed, independently of the Console
        # allowlist guard exercised below.
        gold_scope = await asyncpg.connect(gold_role_dsn)
        try:
            table = f"run_{uuid.UUID(source_run_id).hex}"
            async with gold_scope.transaction(readonly=True):
                await gold_scope.execute(
                    "SELECT set_config('app.tenant_id',$1,true),"
                    " set_config('app.workspace_id',$2,true)",
                    tenant_a,
                    workspace_a,
                )
                assert await gold_scope.fetchval(
                    f"SELECT COUNT(*) FROM omega_publication_gold.{table}"
                ) == 3
            async with gold_scope.transaction(readonly=True):
                await gold_scope.execute(
                    "SELECT set_config('app.tenant_id',$1,true),"
                    " set_config('app.workspace_id',$2,true)",
                    tenant_b,
                    workspace_b,
                )
                assert await gold_scope.fetchval(
                    f"SELECT COUNT(*) FROM omega_publication_gold.{table}"
                ) == 0
        finally:
            await gold_scope.close()

        with pytest.raises(HTTPException, match="source pipeline"):
            await bigquery_shadow.build_talent_9box_baseline(
                tenant_id=tenant_a,
                workspace_id=workspace_a,
                cartridge_id="sap_successfactors",
                dataset=DATASET,
                pipeline_run_id=pipeline_run_id,
                source_run_id=str(uuid.uuid4()),
            )
        with pytest.raises(HTTPException, match="not enabled"):
            await bigquery_shadow.build_talent_9box_baseline(
                tenant_id=tenant_b,
                workspace_id=workspace_a,
                cartridge_id="sap_successfactors",
                dataset=DATASET,
                pipeline_run_id=pipeline_run_id,
                source_run_id=source_run_id,
            )

        # A newer fully verified head invalidates the older trigger even when
        # the old immutable relation still exists and the pipeline binding is
        # otherwise valid.
        next_source_run_id = str(uuid.uuid4())
        _seed_gold(
            postgres_gold_with_native_rls,
            tenant_id=tenant_a,
            workspace_id=workspace_a,
            source_run_id=next_source_run_id,
            generation=2,
        )
        with pytest.raises(HTTPException, match="Gold publication head changed"):
            await bigquery_shadow.build_talent_9box_baseline(
                tenant_id=tenant_a,
                workspace_id=workspace_a,
                cartridge_id="sap_successfactors",
                dataset=DATASET,
                pipeline_run_id=pipeline_run_id,
                source_run_id=source_run_id,
            )
    finally:
        await pool.close()
