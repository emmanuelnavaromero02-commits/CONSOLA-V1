from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from app.services.control_room import grounded_analysis as ga
from app.services.intelligence.evidence import dataset_evidence_pack
from app.services.intelligence.persistence import persist_evidence
from tests.test_operational_rls_console_refinement import (
    _role_dsn,
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _seed(admin: asyncpg.Connection) -> dict[str, str | int]:
    suffix = uuid.uuid4().hex
    tenant_a = await admin.fetchval(
        "INSERT INTO tenants(name,slug) VALUES($1,$2) RETURNING id",
        f"grounded-a-{suffix}",
        f"grounded-a-{suffix}",
    )
    tenant_b = await admin.fetchval(
        "INSERT INTO tenants(name,slug) VALUES($1,$2) RETURNING id",
        f"grounded-b-{suffix}",
        f"grounded-b-{suffix}",
    )
    workspace_a = await admin.fetchval(
        "INSERT INTO workspaces(tenant_id,name) VALUES($1,$2) RETURNING id",
        tenant_a,
        f"Grounded A {suffix}",
    )
    workspace_b = await admin.fetchval(
        "INSERT INTO workspaces(tenant_id,name) VALUES($1,$2) RETURNING id",
        tenant_b,
        f"Grounded B {suffix}",
    )
    pack_a = await admin.fetchval(
        """
        INSERT INTO evidence_packs(
            tenant_id,workspace_id,signal_id,summary,confidence,metadata
        ) VALUES($1,$2,'signal-a','sealed A',0.9,'{}')
        RETURNING id
        """,
        tenant_a,
        workspace_a,
    )
    pack_b = await admin.fetchval(
        """
        INSERT INTO evidence_packs(
            tenant_id,workspace_id,signal_id,summary,confidence,metadata
        ) VALUES($1,$2,'signal-b','sealed B',0.9,'{}')
        RETURNING id
        """,
        tenant_b,
        workspace_b,
    )
    evidence_a = await admin.fetchval(
        """
        INSERT INTO evidence_items(
            tenant_id,workspace_id,evidence_pack_id,source_type,source_ref,data
        ) VALUES($1,$2,$3,'gold','dataset:signal-a','{"source_row_count":10}')
        RETURNING id
        """,
        tenant_a,
        workspace_a,
        pack_a,
    )
    evidence_b = await admin.fetchval(
        """
        INSERT INTO evidence_items(
            tenant_id,workspace_id,evidence_pack_id,source_type,source_ref,data
        ) VALUES($1,$2,$3,'gold','dataset:signal-b','{"source_row_count":10}')
        RETURNING id
        """,
        tenant_b,
        workspace_b,
        pack_b,
    )
    await admin.execute(
        """
        UPDATE evidence_packs
           SET attestation_key_id='test-key',attestation_signature=$2,
               attestation_digest=$2,sealed_at=now()
         WHERE id=$1
        """,
        pack_a,
        "a" * 64,
    )
    await admin.execute(
        """
        UPDATE evidence_packs
           SET attestation_key_id='test-key',attestation_signature=$2,
               attestation_digest=$2,sealed_at=now()
         WHERE id=$1
        """,
        pack_b,
        "b" * 64,
    )
    agent_id = await admin.fetchval(
        "SELECT id FROM agents WHERE is_active = TRUE ORDER BY id LIMIT 1"
    )
    analysis_run_id = await admin.fetchval(
        """
        INSERT INTO agent_runs(
            agent_id,tenant_id,workspace_id,status,finished_at,input_messages
        ) VALUES($1,$2,$3,'ok',now(),'[]') RETURNING id
        """,
        agent_id,
        tenant_a,
        workspace_a,
    )
    verifier_run_id = await admin.fetchval(
        """
        INSERT INTO agent_runs(
            agent_id,tenant_id,workspace_id,status,finished_at,input_messages
        ) VALUES($1,$2,$3,'ok',now(),'[]') RETURNING id
        """,
        agent_id,
        tenant_a,
        workspace_a,
    )
    return {
        "tenant_a": str(tenant_a),
        "workspace_a": str(workspace_a),
        "tenant_b": str(tenant_b),
        "workspace_b": str(workspace_b),
        "pack_a": int(pack_a),
        "pack_b": int(pack_b),
        "evidence_a": int(evidence_a),
        "evidence_b": int(evidence_b),
        "agent_id": str(agent_id),
        "analysis_run_id": int(analysis_run_id),
        "verifier_run_id": int(verifier_run_id),
    }


async def _advance_handoff_to_verifying(
    conn: asyncpg.Connection,
    seeded: dict[str, str | int],
    handoff_id: uuid.UUID,
) -> tuple[int, int]:
    analysis_run_id = int(
        await conn.fetchval(
            """
            INSERT INTO agent_runs(
                agent_id,tenant_id,workspace_id,status,input_messages
            ) VALUES($1,$2,$3,'running','[]') RETURNING id
            """,
            seeded["agent_id"],
            seeded["tenant_a"],
            seeded["workspace_a"],
        )
    )
    lease_owner = uuid.uuid4()
    await conn.execute(
        """
        UPDATE agent_handoffs
           SET status='analyzing',producer_agent_id=$2,producer_run_id=$3,
               analysis_run_id=$3,lease_owner=$4,
               lease_expires_at=now()+interval '1 hour',attempt_count=1
         WHERE id=$1
        """,
        handoff_id,
        seeded["agent_id"],
        analysis_run_id,
        lease_owner,
    )
    await conn.execute(
        """
        UPDATE agent_runs SET status='ok',finished_at=now() WHERE id=$1
        """,
        analysis_run_id,
    )
    verifier_run_id = int(
        await conn.fetchval(
            """
            INSERT INTO agent_runs(
                agent_id,tenant_id,workspace_id,status,input_messages
            ) VALUES($1,$2,$3,'running','[]') RETURNING id
            """,
            seeded["agent_id"],
            seeded["tenant_a"],
            seeded["workspace_a"],
        )
    )
    await conn.execute(
        """
        UPDATE agent_handoffs SET status='verifying',verifier_run_id=$2
         WHERE id=$1
        """,
        handoff_id,
        verifier_run_id,
    )
    await conn.execute(
        """
        UPDATE agent_runs SET status='ok',finished_at=now() WHERE id=$1
        """,
        verifier_run_id,
    )
    return analysis_run_id, verifier_run_id


def _test_verifier_attestation(digest: str) -> str:
    return json.dumps(
        {"key_id": "live-test", "digest": digest, "signature": "e" * 64}
    )


@pytest.mark.asyncio
async def test_grounded_handoffs_are_scoped_evidence_bound_and_immutable(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        seeded = await _seed(admin)
    finally:
        await admin.close()

    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        async with console.transaction():
            await console.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                seeded["tenant_a"],
                seeded["workspace_a"],
            )
            visible = await console.fetch(
                "SELECT id FROM evidence_packs WHERE id=ANY($1::bigint[])",
                [seeded["pack_a"], seeded["pack_b"]],
            )
            assert [int(row["id"]) for row in visible] == [seeded["pack_a"]]

            handoff_id = uuid.uuid4()
            await console.execute(
                """
                INSERT INTO agent_handoffs(
                    id,tenant_id,workspace_id,item_id,signal_id,evidence_pack_id,
                    status,version,input_digest,grounding_status,as_of,expires_at
                ) VALUES($1,$2,$3,'item-a','signal-a',$4,'ready',1,$5,
                         'pending',now(),now()+interval '1 hour')
                """,
                handoff_id,
                seeded["tenant_a"],
                seeded["workspace_a"],
                seeded["pack_a"],
                "c" * 64,
            )
            with pytest.raises(asyncpg.PostgresError, match="evidence identity is immutable"):
                async with console.transaction():
                    await console.execute(
                        "UPDATE agent_handoffs SET item_id='item-tampered' WHERE id=$1",
                        handoff_id,
                    )
            with pytest.raises(asyncpg.PostgresError, match="outside the bound"):
                async with console.transaction():
                    await console.execute(
                        """
                        INSERT INTO agent_claims(
                            tenant_id,workspace_id,handoff_id,claim_key,claim_type,
                            statement,value,unit,population,evidence_item_ids,
                            evidence_paths,
                            verification_status,verification_reason,verified_at
                        ) VALUES($1,$2,$3,'bad','observed','bad','10','records',10,
                                 $4,ARRAY['data.source_row_count'],'verified','test',now())
                        """,
                        seeded["tenant_a"],
                        seeded["workspace_a"],
                        handoff_id,
                        [seeded["evidence_b"]],
                    )
            with pytest.raises(asyncpg.PostgresError, match="exact path"):
                async with console.transaction():
                    await console.execute(
                        """
                        INSERT INTO agent_claims(
                            tenant_id,workspace_id,handoff_id,claim_key,claim_type,
                            statement,value,unit,population,evidence_item_ids,
                            evidence_paths,verification_status,
                            verification_reason,verified_at
                        ) VALUES(
                            $1,$2,$3,'missing-path','observed','bad path','10',
                            'records',10,$4,ARRAY['metadata.readiness_status'],
                            'verified','test',now()
                        )
                        """,
                        seeded["tenant_a"],
                        seeded["workspace_a"],
                        handoff_id,
                        [seeded["evidence_a"]],
                    )
            await console.execute(
                """
                INSERT INTO agent_claims(
                    tenant_id,workspace_id,handoff_id,claim_key,claim_type,
                    statement,value,unit,population,evidence_item_ids,
                    evidence_paths,
                    verification_status,verification_reason,verified_at
                ) VALUES($1,$2,$3,'ok','observed','count observed','10','records',10,
                         $4,ARRAY['data.source_row_count'],'verified','exact',now())
                """,
                seeded["tenant_a"],
                seeded["workspace_a"],
                handoff_id,
                [seeded["evidence_a"]],
            )
            await console.execute(
                """
                INSERT INTO agent_claims(
                    tenant_id,workspace_id,handoff_id,claim_key,claim_type,
                    statement,value,evidence_item_ids,evidence_paths,verification_status,
                    verification_reason,verified_at
                ) VALUES($1,$2,$3,'option:0','option','Evidence-bound',
                         $4::jsonb,$5,ARRAY['data.source_row_count'],
                         'verified','exact',now())
                """,
                seeded["tenant_a"],
                seeded["workspace_a"],
                handoff_id,
                json.dumps("Review evidence"),
                [seeded["evidence_a"]],
            )
            await _advance_handoff_to_verifying(console, seeded, handoff_id)
            output_digest = "d" * 64
            await console.execute(
                """
                UPDATE agent_handoffs
                   SET status='verified',grounding_status='verified',
                       output_digest=$2,verified_at=now(),
                       metadata=metadata || jsonb_build_object(
                           'verifier_attestation',$3::jsonb
                       ),lease_owner=NULL,lease_expires_at=NULL
                 WHERE id=$1
                """,
                handoff_id,
                output_digest,
                _test_verifier_attestation(output_digest),
            )
            with pytest.raises(asyncpg.PostgresError, match="immutable"):
                async with console.transaction():
                    await console.execute(
                        "UPDATE agent_handoffs SET blockers='[\"tamper\"]' WHERE id=$1",
                        handoff_id,
                    )
    finally:
        await console.close()


@pytest.mark.asyncio
async def test_forged_verified_handoff_insert_is_rejected(
    postgres_with_real_init_schema: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        seeded = await _seed(admin)
        digest = "f" * 64
        with pytest.raises(
            asyncpg.PostgresError,
            match="agent handoff must start ready and unverified",
        ):
            await admin.execute(
                """
                INSERT INTO agent_handoffs(
                    tenant_id,workspace_id,item_id,signal_id,evidence_pack_id,
                    producer_agent_id,producer_run_id,analysis_run_id,
                    verifier_run_id,status,version,input_digest,output_digest,
                    grounding_status,metadata,as_of,expires_at,verified_at
                ) VALUES(
                    $1,$2,'forged-terminal','signal-a',$3,$4,$5,$5,$6,
                    'verified',1,$7,$8,'verified',jsonb_build_object(
                        'verifier_attestation',$9::jsonb
                    ),now(),now()+interval '1 hour',now()
                )
                """,
                seeded["tenant_a"],
                seeded["workspace_a"],
                seeded["pack_a"],
                seeded["agent_id"],
                seeded["analysis_run_id"],
                seeded["verifier_run_id"],
                "a" * 64,
                digest,
                _test_verifier_attestation(digest),
            )
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_sealed_evidence_item_cannot_escape_by_reparenting(
    postgres_with_real_init_schema: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        seeded = await _seed(admin)
        unsealed_pack = await admin.fetchval(
            """
            INSERT INTO evidence_packs(
                tenant_id,workspace_id,signal_id,summary,confidence,metadata
            ) VALUES($1,$2,'unsealed-target','unsealed target',0.9,'{}')
            RETURNING id
            """,
            seeded["tenant_a"],
            seeded["workspace_a"],
        )

        with pytest.raises(
            asyncpg.PostgresError,
            match="items in a sealed evidence pack are immutable",
        ):
            await admin.execute(
                "UPDATE evidence_items SET evidence_pack_id=$2 WHERE id=$1",
                seeded["evidence_a"],
                unsealed_pack,
            )

        assert (
            await admin.fetchval(
                "SELECT evidence_pack_id FROM evidence_items WHERE id=$1",
                seeded["evidence_a"],
            )
            == seeded["pack_a"]
        )
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_verified_claim_cannot_escape_by_reparenting(
    postgres_with_real_init_schema: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        seeded = await _seed(admin)
        verified_handoff = uuid.uuid4()
        ready_handoff = uuid.uuid4()
        for handoff_id, item_id, version, digest in (
            (verified_handoff, "verified-parent", 1, "8" * 64),
            (ready_handoff, "ready-parent", 1, "9" * 64),
        ):
            await admin.execute(
                """
                INSERT INTO agent_handoffs(
                    id,tenant_id,workspace_id,item_id,signal_id,evidence_pack_id,
                    status,version,input_digest,grounding_status,as_of,expires_at
                ) VALUES($1,$2,$3,$4,'signal-a',$5,'ready',$6,$7,
                         'pending',now(),now()+interval '1 hour')
                """,
                handoff_id,
                seeded["tenant_a"],
                seeded["workspace_a"],
                item_id,
                seeded["pack_a"],
                version,
                digest,
            )
        claim_id = await admin.fetchval(
            """
            INSERT INTO agent_claims(
                tenant_id,workspace_id,handoff_id,claim_key,claim_type,
                statement,value,unit,population,evidence_item_ids,
                evidence_paths,
                verification_status,verification_reason,verified_at
            ) VALUES($1,$2,$3,'observed-count','observed','count observed','10',
                     'records',10,$4,ARRAY['data.source_row_count'],
                     'verified','exact',now())
            RETURNING id
            """,
            seeded["tenant_a"],
            seeded["workspace_a"],
            verified_handoff,
            [seeded["evidence_a"]],
        )
        await _advance_handoff_to_verifying(admin, seeded, verified_handoff)
        output_digest = "7" * 64
        await admin.execute(
            """
            UPDATE agent_handoffs
               SET status='verified',grounding_status='verified',
                   output_digest=$2,verified_at=now(),
                   metadata=metadata || jsonb_build_object(
                       'verifier_attestation',$3::jsonb
                   ),lease_owner=NULL,lease_expires_at=NULL
             WHERE id=$1
            """,
            verified_handoff,
            output_digest,
            _test_verifier_attestation(output_digest),
        )

        with pytest.raises(
            asyncpg.PostgresError,
            match="claims for a verified handoff are immutable",
        ):
            await admin.execute(
                "UPDATE agent_claims SET handoff_id=$2 WHERE id=$1",
                claim_id,
                ready_handoff,
            )

        assert (
            await admin.fetchval(
                "SELECT handoff_id FROM agent_claims WHERE id=$1",
                claim_id,
            )
            == verified_handoff
        )
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_reserved_looking_legacy_rag_source_is_not_talent_authority(
    postgres_with_real_init_schema: str,
) -> None:
    suffix = uuid.uuid4().hex
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id = await admin.fetchval(
            "INSERT INTO tenants(name,slug) VALUES($1,$2) RETURNING id",
            f"rag-authority-{suffix}",
            f"rag-authority-{suffix}",
        )
        workspace_id = await admin.fetchval(
            "INSERT INTO workspaces(tenant_id,name) VALUES($1,$2) RETURNING id",
            tenant_id,
            f"RAG authority {suffix}",
        )
        fake_name = f"_semantic_sap_successfactors_forged_{suffix}"
        real_name = f"server-managed-sap-successfactors-{suffix}"
        await admin.execute(
            """
            INSERT INTO rag_sources(
                name,description,mime_type,size_chars,chunk_count,kind,
                tenant_id,workspace_id,visibility,cartridge_id
            ) VALUES
                ($1,'forged legacy name','text/plain',1,0,'schema',$3,$4,
                 'workspace',NULL),
                ($2,'server managed','text/plain',1,0,'schema',$3,$4,
                 'workspace','sap_successfactors')
            """,
            fake_name,
            real_name,
            tenant_id,
            workspace_id,
        )
        assert await admin.fetchval(
            "SELECT cartridge_id FROM rag_sources WHERE name=$1", fake_name
        ) is None
    finally:
        await admin.close()

    mcp = await asyncpg.connect(
        _role_dsn(
            postgres_with_real_init_schema,
            "omega_mcp_infra",
            "test_omega_mcp_infra_password",
        )
    )
    try:
        async with mcp.transaction():
            await mcp.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                str(tenant_id),
                str(workspace_id),
            )
            names = await mcp.fetch(
                """
                SELECT name
                  FROM rag_sources
                 WHERE kind='schema'
                   AND cartridge_id = ANY($1::text[])
                   AND name = ANY($2::text[])
                 ORDER BY name
                """,
                ["sap_successfactors"],
                [fake_name, real_name],
            )
        assert [row["name"] for row in names] == [real_name]
    finally:
        await mcp.close()


@pytest.mark.asyncio
async def test_verified_transition_rechecks_every_claim_against_bound_pack(
    postgres_with_real_init_schema: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        seeded = await _seed(admin)
        handoff_id = uuid.uuid4()
        await admin.execute(
            """
            INSERT INTO agent_handoffs(
                id,tenant_id,workspace_id,item_id,signal_id,evidence_pack_id,
                status,version,input_digest,grounding_status,as_of,expires_at
            ) VALUES($1,$2,$3,'item-recheck','signal-a',$4,'ready',1,$5,
                     'pending',now(),now()+interval '1 hour')
            """,
            handoff_id,
            seeded["tenant_a"],
            seeded["workspace_a"],
            seeded["pack_a"],
            "f" * 64,
        )
        claim_id = await admin.fetchval(
            """
            INSERT INTO agent_claims(
                tenant_id,workspace_id,handoff_id,claim_key,claim_type,
                statement,value,unit,population,evidence_item_ids,
                evidence_paths,
                verification_status,verification_reason,verified_at
            ) VALUES($1,$2,$3,'recheck','observed','count observed','10',
                     'records',10,$4,ARRAY['data.source_row_count'],
                     'verified','exact',now())
            RETURNING id
            """,
            seeded["tenant_a"],
            seeded["workspace_a"],
            handoff_id,
            [seeded["evidence_a"]],
        )
        # Simulate legacy/tampered data that predates the row trigger. The
        # verified transition itself must independently fail closed.
        await admin.execute("SET session_replication_role = 'replica'")
        try:
            await admin.execute(
                "UPDATE agent_claims SET evidence_item_ids=$2 WHERE id=$1",
                claim_id,
                [seeded["evidence_b"]],
            )
        finally:
            await admin.execute("SET session_replication_role = 'origin'")

        await _advance_handoff_to_verifying(admin, seeded, handoff_id)
        output_digest = "1" * 64
        with pytest.raises(asyncpg.PostgresError, match="outside the bound pack"):
            await admin.execute(
                """
                UPDATE agent_handoffs
                   SET status='verified', grounding_status='verified',
                       output_digest=$2, verified_at=now(),
                       metadata=metadata || jsonb_build_object(
                           'verifier_attestation',$3::jsonb
                       ),lease_owner=NULL,lease_expires_at=NULL
                 WHERE id=$1
                """,
                handoff_id,
                output_digest,
                _test_verifier_attestation(output_digest),
            )
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_real_baseline_pack_is_enriched_and_sealed_by_gold_collector(
    postgres_with_real_init_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "collector-live-key")
    monkeypatch.setenv(
        "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
        "collector-live-secret-that-is-longer-than-thirty-two-bytes",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    digest = "a" * 64
    dataset = "sap_successfactors_talent_9box"
    suffix = uuid.uuid4().hex
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id = await admin.fetchval(
            "INSERT INTO tenants(name,slug) VALUES($1,$2) RETURNING id",
            f"collector-{suffix}",
            f"collector-{suffix}",
        )
        workspace_id = await admin.fetchval(
            "INSERT INTO workspaces(tenant_id,name) VALUES($1,$2) RETURNING id",
            tenant_id,
            f"Collector {suffix}",
        )
        intelligence_run_id = 918273
        signal_id = f"collector-signal-{suffix}"
        pack = dataset_evidence_pack(
            dataset=dataset,
            id_field="box_id",
            entity_id="high_high",
            time_field="materialized_at",
            value_field="population_total",
            latest={"population_total": 100, "materialized_at": now.isoformat()},
            history_values=[float(value) for value in range(10, 20)],
            method="deterministic_baseline",
            confidence=0.9,
            source_system="sap_successfactors",
            freshness_at=now.isoformat(),
        )
        pack_id = await persist_evidence(
            admin,
            str(tenant_id),
            str(workspace_id),
            {
                "signal_id": signal_id,
                "metric": "population_total",
                "dataset": dataset,
                "source_system": "sap_successfactors",
                "confidence": 0.9,
                "intelligence_run_id": intelligence_run_id,
                "freshness_at": now.isoformat(),
            },
            pack,
            None,
        )
        result = await ga.seal_talent_evidence_packs(
            admin,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            intelligence_run_id=intelligence_run_id,
            publication_bindings=[
                {
                    "dataset": dataset,
                    "pipeline_run_id": "pipeline-collector-live",
                    "materialization_run_id": str(uuid.uuid4()),
                    "receipt_id": str(uuid.uuid4()),
                    "head_generation": 7,
                    "object_uri": "gs://omega-test/gold/talent.parquet",
                    "object_version": "generation-7",
                    "object_checksum": digest,
                    "schema_digest": digest,
                    "evidence_digest": digest,
                    "row_count": 100,
                    "published_at": now.isoformat(),
                    "outcome_digest": digest,
                }
            ],
        )
        assert result == {"candidates": 1, "sealed": 1, "blocked": 0}
        sealed = await admin.fetchrow(
            """
            SELECT sealed_at,metadata FROM evidence_packs WHERE id=$1
            """,
            pack_id,
        )
        item = await admin.fetchrow(
            """
            SELECT source_type,source_ref,data,metadata
              FROM evidence_items WHERE evidence_pack_id=$1
            """,
            pack_id,
        )
        sealed_metadata = json.loads(sealed["metadata"])
        item_data = json.loads(item["data"])
        item_metadata = json.loads(item["metadata"])
        assert sealed["sealed_at"] is not None
        assert sealed_metadata["completeness"] == "complete"
        assert sealed_metadata["source_row_count"] == 100
        assert item["source_type"] == "gold"
        assert item["source_ref"].startswith(dataset + ":baseline:")
        assert item_data["source_row_count"] == 100
        assert item_metadata["publication"]["head_generation"] == 7
    finally:
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_rule_proposals_are_paused_at_role_and_trigger_boundaries(
    postgres_with_real_init_schema: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        privileges = await admin.fetchrow(
            """
            SELECT
                has_table_privilege(
                    'omega_console', 'agent_rule_proposals', 'SELECT'
                ) AS can_select,
                has_table_privilege(
                    'omega_console', 'agent_rule_proposals', 'INSERT'
                ) AS can_insert,
                has_table_privilege(
                    'omega_console', 'agent_rule_proposals', 'UPDATE'
                ) AS can_update,
                has_table_privilege(
                    'omega_console', 'agent_rule_proposals', 'DELETE'
                ) AS can_delete
            """
        )
        assert dict(privileges) == {
            "can_select": True,
            "can_insert": False,
            "can_update": False,
            "can_delete": False,
        }

        suffix = uuid.uuid4().hex
        tenant_id = await admin.fetchval(
            "INSERT INTO tenants(name,slug) VALUES($1,$2) RETURNING id",
            f"paused-rule-{suffix}",
            f"paused-rule-{suffix}",
        )
        workspace_id = await admin.fetchval(
            "INSERT INTO workspaces(tenant_id,name) VALUES($1,$2) RETURNING id",
            tenant_id,
            f"Paused rules {suffix}",
        )
        with pytest.raises(
            asyncpg.PostgresError,
            match="agent rule proposals are paused by server policy",
        ):
            await admin.execute(
                """
                INSERT INTO agent_rule_proposals(
                    tenant_id,workspace_id,ruleset_name,proposed_version,
                    parent_version,proposal,evidence_pack_ids,outcome_ids,
                    proposal_digest,status,proposed_by_run_id
                ) VALUES(
                    $1,$2,'sap_successfactors.talent.9box','v2','v1',
                    '{"threshold":1}',ARRAY[1]::bigint[],ARRAY[1]::bigint[],
                    $3,'proposed',1
                )
                """,
                tenant_id,
                workspace_id,
                "a" * 64,
            )
    finally:
        await admin.close()
@pytest.mark.asyncio
async def test_analysis_queue_is_async_idempotent_and_reclaims_expired_lease(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "grounding-live-key")
    monkeypatch.setenv(
        "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
        "grounding-live-secret-that-is-longer-than-thirty-two-bytes",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    digest = "e" * 64
    publication = {
        "dataset": "sap_successfactors_talent_9box",
        "pipeline_run_id": "pipeline-live",
        "materialization_run_id": str(uuid.uuid4()),
        "receipt_id": str(uuid.uuid4()),
        "head_generation": 1,
        "object_uri": "gs://omega-test/gold/talent.parquet",
        "object_version": "generation-1",
        "object_checksum": digest,
        "schema_digest": digest,
        "evidence_digest": digest,
        "row_count": 100,
        "published_at": now.isoformat(),
        "outcome_digest": digest,
    }
    suffix = uuid.uuid4().hex
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id = await admin.fetchval(
            "INSERT INTO tenants(name,slug) VALUES($1,$2) RETURNING id",
            f"queue-{suffix}",
            f"queue-{suffix}",
        )
        workspace_id = await admin.fetchval(
            "INSERT INTO workspaces(tenant_id,name) VALUES($1,$2) RETURNING id",
            tenant_id,
            f"Queue {suffix}",
        )
        agent_id = await admin.fetchval(
            """
            INSERT INTO agents(
                cartridge_id,slug,name,description,model,temperature,
                tenant_id,workspace_id
            ) VALUES(
                'sap_successfactors','sap_successfactors_talent_monitor',
                $3,'grounded live test','claude-sonnet-4-6',0,$1,$2
            ) RETURNING id
            """,
            tenant_id,
            workspace_id,
            f"Grounded worker {suffix}",
        )
        signal_id = f"queue-signal-{suffix}"
        pack_id = await admin.fetchval(
            """
            INSERT INTO evidence_packs(
                tenant_id,workspace_id,signal_id,summary,confidence,metadata
            ) VALUES($1,$2,$3,'queue pack',0.9,$4::jsonb) RETURNING id
            """,
            tenant_id,
            workspace_id,
            signal_id,
            json.dumps(
                {
                    "source_system": "sap_successfactors",
                    "dataset": "sap_successfactors_talent_9box",
                    "readiness_status": "ready",
                    "completeness": "complete",
                    "materialized_at": now.isoformat(),
                    "publication": publication,
                }
            ),
        )
        evidence_item_id = await admin.fetchval(
            """
            INSERT INTO evidence_items(
                tenant_id,workspace_id,evidence_pack_id,source_type,source_ref,
                data,metadata
            ) VALUES($1,$2,$3,'gold',$4,$5::jsonb,'{}'::jsonb)
            RETURNING id
            """,
            tenant_id,
            workspace_id,
            pack_id,
            f"sap_successfactors_talent_9box:{signal_id}",
            json.dumps(
                {
                    "source_row_count": 100,
                    "readiness_status": "ready",
                    "completeness": "complete",
                    "materialized_at": now.isoformat(),
                }
            ),
        )
        async with admin.transaction():
            pack, evidence_items = await ga._load_pack(
                admin,
                str(tenant_id),
                str(workspace_id),
                signal_id,
                int(pack_id),
            )
            assert pack is not None
            facts, _ = ga._safe_facts(evidence_items)
            as_of = ga._evidence_as_of(pack, evidence_items, {})
            assert as_of is not None
            document = ga._evidence_document(
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                item_id=signal_id,
                pack=pack,
                items=evidence_items,
                facts=facts,
                as_of=as_of,
            )
            attestation = ga._attest(document)
            await admin.execute(
                """
                UPDATE evidence_packs
                   SET attestation_key_id=$2,attestation_signature=$3,
                       attestation_digest=$4,sealed_at=clock_timestamp()
                 WHERE id=$1
                """,
                pack_id,
                attestation["key_id"],
                attestation["signature"],
                ga._digest(document),
            )
    finally:
        await admin.close()

    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=5)

    async def _pool() -> asyncpg.Pool:
        return pool

    async def _item(_item_id: str, _user: dict) -> dict:
        return {
            "cartridge": "sap_successfactors",
            "metadata": {"evidence_pack_id": int(pack_id)},
        }

    monkeypatch.setattr(ga.auth, "pool", _pool)
    monkeypatch.setattr(ga.control_room_service, "get_item", _item)
    user = {
        "active_tenant_id": str(tenant_id),
        "active_workspace_id": str(workspace_id),
    }
    request_model_calls = 0

    async def _must_not_run(*_args, **_kwargs):
        nonlocal request_model_calls
        request_model_calls += 1
        raise AssertionError("request path invoked the model")

    try:
        queued = await ga.create_item_analysis(
            signal_id, user, model_caller=_must_not_run
        )
        duplicates = await asyncio.gather(
            ga.create_item_analysis(signal_id, user, model_caller=_must_not_run),
            ga.create_item_analysis(signal_id, user, model_caller=_must_not_run),
        )
        assert queued["status"] == "ready"
        assert {row["analysis_run_id"] for row in duplicates} == {
            queued["analysis_run_id"]
        }
        assert request_model_calls == 0

        admin = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            stale_run_id = await admin.fetchval(
                """
                INSERT INTO agent_runs(
                    agent_id,tenant_id,workspace_id,status,input_messages
                ) VALUES($1,$2,$3,'running','[]') RETURNING id
                """,
                agent_id,
                tenant_id,
                workspace_id,
            )
            await admin.execute(
                """
                UPDATE agent_handoffs
                   SET status='analyzing',analysis_run_id=$2,
                       lease_owner=$3,lease_expires_at=now()-interval '1 second',
                       attempt_count=1
                 WHERE id=$1
                """,
                uuid.UUID(queued["analysis_run_id"]),
                stale_run_id,
                uuid.uuid4(),
            )
        finally:
            await admin.close()

        assert await ga.reconcile_abandoned_analyses(user) == 1
        adversarial_calls = 0

        async def _adversarial_model(*_args, **_kwargs) -> str:
            nonlocal adversarial_calls
            adversarial_calls += 1
            return json.dumps(
                {
                    "hypotheses": [
                        {
                            "statement": "readiness_status complete",
                            "evidence_refs": [
                                {
                                    "evidence_item_id": int(evidence_item_id),
                                    "path": "data.readiness_status",
                                }
                            ],
                        }
                    ],
                    "options": [],
                    "assumptions": [],
                    "blockers": [],
                }
            )

        await ga.process_item_analysis(
            queued["analysis_run_id"],
            user,
            model_caller=_adversarial_model,
            worker_id=str(uuid.uuid4()),
        )
        rejected = await ga.get_item_analysis(signal_id, user)
        assert adversarial_calls == 1
        assert rejected["status"] == "insufficient_data"
        assert rejected["hypotheses"] == []
        assert "citation_value_mismatch" in rejected["blockers"]

        queued = await ga.create_item_analysis(signal_id, user)
        assert queued["status"] == "ready"
        model_calls = 0

        async def _model(*_args, **_kwargs) -> str:
            nonlocal model_calls
            model_calls += 1
            return json.dumps(
                {
                    "hypotheses": [
                        {
                            "statement": "Revisar readiness_status ready antes de decidir",
                            "evidence_refs": [
                                {
                                    "evidence_item_id": int(evidence_item_id),
                                    "path": "data.readiness_status",
                                }
                            ],
                        }
                    ],
                    "options": [
                        {
                            "label": "Revisar datos",
                            "rationale": "Validar readiness_status ready",
                            "evidence_refs": [
                                {
                                    "evidence_item_id": int(evidence_item_id),
                                    "path": "data.readiness_status",
                                }
                            ],
                        }
                    ],
                    "assumptions": [
                        {
                            "statement": "Confirmar readiness_status ready",
                            "evidence_refs": [
                                {
                                    "evidence_item_id": int(evidence_item_id),
                                    "path": "data.readiness_status",
                                }
                            ],
                        }
                    ],
                    "blockers": [],
                }
            )

        await asyncio.gather(
            ga.process_item_analysis(
                queued["analysis_run_id"],
                user,
                model_caller=_model,
                worker_id=str(uuid.uuid4()),
            ),
            ga.process_item_analysis(
                queued["analysis_run_id"],
                user,
                model_caller=_model,
                worker_id=str(uuid.uuid4()),
            ),
        )
        completed = await ga.get_item_analysis(signal_id, user)
        assert model_calls == 1, completed["blockers"]
        assert completed["status"] == "verified"
        assert completed["grounding_status"] == "verified"
        assert completed["claims"]
        assert completed["hypotheses"] == [
            "Revisar readiness_status ready antes de decidir"
        ]
        assert completed["options"] == [
            {
                "label": "Revisar datos",
                "rationale": "Validar readiness_status ready",
                "evidence_refs": [
                    {
                        "evidence_item_id": int(evidence_item_id),
                        "path": "data.readiness_status",
                    }
                ],
                "evidence_item_ids": [int(evidence_item_id)],
                "evidence_paths": ["data.readiness_status"],
            }
        ]
        assert completed["assumptions"] == [
            {
                "statement": "Confirmar readiness_status ready",
                "evidence_refs": [
                    {
                        "evidence_item_id": int(evidence_item_id),
                        "path": "data.readiness_status",
                    }
                ],
                "evidence_item_ids": [int(evidence_item_id)],
                "evidence_paths": ["data.readiness_status"],
            }
        ]

        admin = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            handoff = await admin.fetchrow(
                """
                SELECT status,producer_agent_id,producer_run_id,
                       analysis_run_id,verifier_run_id,lease_owner,
                       lease_expires_at,attempt_count,hypotheses,options,
                       assumptions,output_digest,metadata
                  FROM agent_handoffs WHERE id=$1
                """,
                uuid.UUID(queued["analysis_run_id"]),
            )
            assert handoff is not None
            assert handoff["status"] == "verified"
            assert handoff["producer_agent_id"] == agent_id
            assert handoff["producer_run_id"] == handoff["analysis_run_id"]
            assert handoff["analysis_run_id"] != handoff["verifier_run_id"]
            assert handoff["lease_owner"] is None
            assert handoff["lease_expires_at"] is None
            assert handoff["attempt_count"] == 3
            assert json.loads(handoff["hypotheses"]) == []
            assert json.loads(handoff["options"]) == []
            assert json.loads(handoff["assumptions"]) == []
            metadata = json.loads(handoff["metadata"])
            verifier_attestation = metadata["verifier_attestation"]
            assert verifier_attestation["digest"] == handoff["output_digest"]
            claim_types = await admin.fetch(
                """
                SELECT claim_type FROM agent_claims
                 WHERE handoff_id=$1 ORDER BY claim_type
                """,
                uuid.UUID(queued["analysis_run_id"]),
            )
            assert {row["claim_type"] for row in claim_types} >= {
                "hypothesis",
                "option",
                "assumption",
            }
            runs = await admin.fetch(
                "SELECT id,status,finished_at FROM agent_runs WHERE id=ANY($1::bigint[])",
                [
                    int(stale_run_id),
                    int(handoff["analysis_run_id"]),
                    int(handoff["verifier_run_id"]),
                ],
            )
            assert {row["status"] for row in runs} == {"error", "ok"}
            assert all(row["finished_at"] is not None for row in runs)
            await admin.execute("SET session_replication_role = 'replica'")
            try:
                await admin.execute(
                    "UPDATE agent_handoffs SET output_digest=$2 WHERE id=$1",
                    uuid.UUID(queued["analysis_run_id"]),
                    "0" * 64,
                )
            finally:
                await admin.execute("SET session_replication_role = 'origin'")
        finally:
            await admin.close()
        tampered = await ga.get_item_analysis(signal_id, user)
        assert tampered["status"] == "insufficient_data"
        assert tampered["claims"] == []
        assert tampered["options"] == []
        assert "analysis_output_digest_invalid" in tampered["blockers"]
        assert "verifier_output_attestation_invalid" in tampered["blockers"]
    finally:
        await pool.close()
