"""Real PostgreSQL proof for scoped Talent benchmark approval authority."""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


REPO = Path(__file__).resolve().parents[1]
DATASET = "sap_successfactors_talent_benchmark_internal"
MIGRATION = REPO / "infra/init/99zzs_talent_benchmark_approval_ledger.sql"


async def _seed(admin: asyncpg.Connection) -> dict[str, object]:
    suffix = uuid.uuid4().hex
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    workspace_a, workspace_b = uuid.uuid4(), uuid.uuid4()
    await admin.executemany(
        "INSERT INTO tenants(id,name,slug) VALUES($1,$2,$3)",
        [
            (tenant_a, f"Benchmark A {suffix}", f"benchmark-a-{suffix}"),
            (tenant_b, f"Benchmark B {suffix}", f"benchmark-b-{suffix}"),
        ],
    )
    await admin.executemany(
        "INSERT INTO workspaces(id,tenant_id,name) VALUES($1,$2,$3)",
        [
            (workspace_a, tenant_a, f"Benchmark A {suffix}"),
            (workspace_b, tenant_b, f"Benchmark B {suffix}"),
        ],
    )

    users: dict[str, int] = {}
    for label, tenant_id in (
        ("maker_a", tenant_a),
        ("checker_a", tenant_a),
        ("outsider_a", tenant_a),
        ("checker_b", tenant_b),
    ):
        users[label] = await admin.fetchval(
            """
            INSERT INTO users(email,name,password_hash,role,tenant_id,is_active)
            VALUES($1,$2,'not-a-login-secret','user',$3,TRUE)
            RETURNING id
            """,
            f"{label}-{suffix}@example.test",
            label,
            tenant_id,
        )

    await admin.executemany(
        """
        INSERT INTO user_workspace_roles(user_id,workspace_id,role_id)
        SELECT $1,$2,id FROM roles WHERE name='control_room_approver'
        """,
        [
            (users["maker_a"], workspace_a),
            (users["checker_a"], workspace_a),
            (users["checker_b"], workspace_b),
        ],
    )

    head_a, wrong_head, head_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def evidence(
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        owner_user_id: int,
        head: uuid.UUID,
    ) -> int:
        pack_id = await admin.fetchval(
            """
            INSERT INTO evidence_packs(
                tenant_id,workspace_id,signal_id,summary,metadata,owner_user_id
            ) VALUES($1,$2,$3,'Benchmark approval evidence',
              jsonb_build_object(
                'purpose','talent_benchmark_approval',
                'dataset',$4::text,
                'materialization_head',$5::text
              ),$6)
            RETURNING id
            """,
            tenant_id,
            workspace_id,
            f"benchmark:{head}",
            DATASET,
            str(head),
            owner_user_id,
        )
        await admin.execute(
            """
            INSERT INTO evidence_items(
                tenant_id,workspace_id,evidence_pack_id,source_type,source_ref,
                data,supports_hypothesis,strength,owner_user_id,metadata
            ) VALUES($1,$2,$3,'gold_dataset',$4,'{"observed":true}'::jsonb,
                     'Benchmark inputs support this approval',0.95,$5,
                     '{"server_owned":true}'::jsonb)
            """,
            tenant_id,
            workspace_id,
            pack_id,
            f"benchmark:{head}",
            owner_user_id,
        )
        return pack_id

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "workspace_a": workspace_a,
        "workspace_b": workspace_b,
        "head_a": head_a,
        "head_b": head_b,
        "users": users,
        "evidence_a": await evidence(tenant_a, workspace_a, users["maker_a"], head_a),
        "evidence_wrong_head": await evidence(
            tenant_a, workspace_a, users["maker_a"], wrong_head
        ),
        "evidence_b": await evidence(tenant_b, workspace_b, users["checker_b"], head_b),
    }


async def _record(
    conn: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: int,
    head: uuid.UUID,
    evidence_id: int,
) -> int:
    async with conn.transaction():
        await conn.execute(
            """
            SELECT set_config('app.tenant_id',$1,true),
                   set_config('app.workspace_id',$2,true),
                   set_config('app.user_id',$3,true)
            """,
            str(tenant_id),
            str(workspace_id),
            str(user_id),
        )
        return await conn.fetchval(
            "SELECT record_talent_benchmark_approval($1,$2)", head, evidence_id
        )


@pytest.mark.asyncio
async def test_benchmark_ledger_derives_authority_and_fails_closed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        seed = await _seed(admin)
        users = seed["users"]
        assert isinstance(users, dict)

        approval_id = await _record(
            console,
            tenant_id=seed["tenant_a"],
            workspace_id=seed["workspace_a"],
            user_id=users["checker_a"],
            head=seed["head_a"],
            evidence_id=seed["evidence_a"],
        )
        row = await admin.fetchrow(
            "SELECT * FROM talent_benchmark_approvals WHERE id=$1", approval_id
        )
        assert row["tenant_id"] == seed["tenant_a"]
        assert row["workspace_id"] == seed["workspace_a"]
        assert row["dataset"] == DATASET
        assert row["materialization_head"] == seed["head_a"]
        assert row["actor_user_id"] == users["checker_a"]
        assert row["maker_user_id"] == users["maker_a"]
        assert row["evidence_pack_id"] == seed["evidence_a"]
        assert row["evidence_digest_version"] == 1
        assert row["evidence_item_count"] == 1
        assert row["recorded_by_server"] is True

        for statement in (
            "UPDATE evidence_items SET strength=0.10 WHERE evidence_pack_id=$1",
            "DELETE FROM evidence_items WHERE evidence_pack_id=$1",
            "UPDATE evidence_packs SET summary='changed' WHERE id=$1",
        ):
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                async with console.transaction():
                    await console.execute(
                        "SELECT set_config('app.tenant_id',$1,true),"
                        "set_config('app.workspace_id',$2,true)",
                        str(seed["tenant_a"]),
                        str(seed["workspace_a"]),
                    )
                    await console.execute(statement, seed["evidence_a"])

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with console.transaction():
                await console.execute(
                    "SELECT set_config('app.tenant_id',$1,true),"
                    "set_config('app.workspace_id',$2,true)",
                    str(seed["tenant_a"]),
                    str(seed["workspace_a"]),
                )
                await console.execute(
                    """INSERT INTO evidence_items(
                    tenant_id,workspace_id,evidence_pack_id,source_type,source_ref
                    ) VALUES($1,$2,$3,'gold_dataset','late-item')""",
                    seed["tenant_a"],
                    seed["workspace_a"],
                    seed["evidence_a"],
                )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM evidence_items WHERE evidence_pack_id=$1",
                seed["evidence_a"],
            )
            == 1
        )

        rejected = [
            dict(
                tenant_id=seed["tenant_a"],
                workspace_id=seed["workspace_a"],
                user_id=users["maker_a"],
                head=seed["head_a"],
                evidence_id=seed["evidence_a"],
            ),
            dict(
                tenant_id=seed["tenant_a"],
                workspace_id=seed["workspace_a"],
                user_id=users["checker_a"],
                head=seed["head_a"],
                evidence_id=seed["evidence_b"],
            ),
            dict(
                tenant_id=seed["tenant_a"],
                workspace_id=seed["workspace_a"],
                user_id=users["checker_a"],
                head=seed["head_a"],
                evidence_id=seed["evidence_wrong_head"],
            ),
            dict(
                tenant_id=seed["tenant_a"],
                workspace_id=seed["workspace_a"],
                user_id=users["outsider_a"],
                head=uuid.uuid4(),
                evidence_id=seed["evidence_a"],
            ),
        ]
        for probe in rejected:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await _record(console, **probe)

        with pytest.raises(asyncpg.UniqueViolationError):
            await _record(
                console,
                tenant_id=seed["tenant_a"],
                workspace_id=seed["workspace_a"],
                user_id=users["checker_a"],
                head=seed["head_a"],
                evidence_id=seed["evidence_a"],
            )

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await console.execute(
                "UPDATE talent_benchmark_approvals SET approval_status='approved'"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await console.fetchval(
                "SELECT record_talent_benchmark_approval($1,$2)",
                seed["head_b"],
                seed["evidence_b"],
            )

        async with console.transaction():
            await console.execute(
                """
                SELECT set_config('app.tenant_id',$1,true),
                       set_config('app.workspace_id',$2,true)
                """,
                str(seed["tenant_b"]),
                str(seed["workspace_b"]),
            )
            assert (
                await console.fetchval(
                    "SELECT count(*) FROM talent_benchmark_approvals WHERE id=$1",
                    approval_id,
                )
                == 0
            )

        before = await admin.fetchval("SELECT count(*) FROM talent_benchmark_approvals")
        migration = MIGRATION.read_text(encoding="utf-8")
        await admin.execute(migration)
        await admin.execute(migration)
        assert (
            await admin.fetchval("SELECT count(*) FROM talent_benchmark_approvals")
            == before
        )
    finally:
        await console.close()
        await admin.close()
