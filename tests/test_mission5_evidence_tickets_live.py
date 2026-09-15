"""Mission 5 against a real PostgreSQL built from every infra/init migration.

Runs the evidence-ticket boundary end to end with the real roles, grants and
RLS: a live scheduled-run lease, the wisdom-bits hook minting under
``omega_console``, the alert row mcp-infra would have written, the Control Room
loader reading it back under RLS, and the v2 fact with its narrative.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from console.app.routers import intelligence as intelligence_router
from console.app.services import agent_runtime, agent_scheduler, auth
from console.app.services.control_room import attested_monitor_alerts, evidence_tickets
from console.app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from console.app.services.control_room.surface_snapshot import (
    SurfaceScope,
    SurfaceSnapshot,
)
from console.app.services.intelligence import narrative_job
from tests.test_control_room_live_postgres_operational_truth_pipeline import _seed_scope
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)

__all__ = ("omega_console_live_dsn", "postgres_with_real_init_schema")

CONTRACT = {
    "engine": "wisdom_bit",
    "wisdom_bit_id": "WB-OPERACION",
    "domain": "Operacion",
    "dataset": "operations_kpis",
    "severity": "high",
    "dedup_key": "replicon:operations-monitor:WB-OPERACION",
}
PAYLOAD = {
    "wisdom_bit_id": "WB-OPERACION",
    "status": "degraded",
    "signals": {"count": 2, "items": [{"metric": "a"}, {"metric": "b"}]},
}
UI_FORBIDDEN = re.compile(r"Aprobar|Ejecutar|Sí, ejecutar")


async def _agent(admin: asyncpg.Connection, scope: dict, slug: str) -> str:
    agent_id = str(uuid.uuid4())
    await admin.execute(
        """INSERT INTO agents (
               id, cartridge_id, slug, name, is_active, tenant_id, workspace_id, extra
           ) VALUES ($1, 'replicon', $2, $2, TRUE, $3, $4, $5::jsonb)""",
        agent_id,
        slug,
        scope["tenant_id"],
        scope["workspace_id"],
        json.dumps({"role": "monitor", "monitor": CONTRACT}),
    )
    return agent_id


def _user(scope: dict, agent_id: str, agent_run_id: int) -> dict:
    return {
        "tenant_id": scope["tenant_id"],
        "workspace_id": scope["workspace_id"],
        "agent_id": agent_id,
        "agent_run_id": agent_run_id,
        "security_context_source": "agent_runner",
    }


def _body(authority: dict) -> intelligence_router.InternalMcpWisdomBitRequest:
    return intelligence_router.InternalMcpWisdomBitRequest(
        security_context={},
        wisdom_bit_id="WB-OPERACION",
        cartridge_id="replicon",
        effect_authority=authority,
    )


def _authority(scope: dict, agent_id: str, run: dict, agent_run_id: int) -> dict:
    agent = agent_runtime.Agent(
        id=agent_id,
        cartridge_id="replicon",
        slug="operations-monitor",
        name="Monitor de Operacion",
        description="",
        instructions="",
        personality="",
        allowed_tools=[],
        rag_filter={},
        model="m",
        max_tokens=1,
        temperature=0.0,
        extra={},
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
    )
    return agent_runtime._scheduled_effect_authority(
        agent=agent,
        run_id=agent_run_id,
        tool="wisdom_bits__run",
        args={"wisdom_bit_id": "WB-OPERACION"},
        schedule_run_id=run["id"],
        fencing_token=run["fencing_token"],
    )


async def _raise_alert(admin: asyncpg.Connection, scope: dict, agent_id: str, item_id: str, signal_count: int) -> None:
    metadata = {
        "source": "agent",
        "advisory": True,
        "agent_id": agent_id,
        "agent_run_id": "77",
        "alert_type": "wisdombit_monitor",
        "control_state": {"source": "agent", "advisory": True},
        "analysis_evidence": {
            "analysis_type": "wb-operacion_monitor",
            "engine": "wisdom_bit",
            "engine_run_id": f"agent:{agent_id}:run:1:wisdombit:WB-OPERACION",
            "metrics": {"status": "degraded", "signal_count": signal_count},
            "blockers": [],
        },
    }
    await admin.execute(
        """
        INSERT INTO control_room_items (
            tenant_id, workspace_id, item_id, cartridge_id, domain, source_dataset,
            item_kind, title, severity, status, entity_label, metadata,
            first_seen_at, last_seen_at
        ) VALUES (
            $1, $2, $3, 'replicon', 'Operacion', 'operations_kpis',
            'agent_alert', 'Monitor de Operacion: WB-OPERACION requiere atencion',
            'high', 'open', 'WB-OPERACION', $4::jsonb,
            clock_timestamp(), clock_timestamp()
        )
        """,
        scope["tenant_id"],
        scope["workspace_id"],
        item_id,
        json.dumps(metadata),
    )


@pytest.mark.asyncio
async def test_monitor_alert_attested_end_to_end_on_real_postgres(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=3)
    owners = {
        id(module): module
        for module in (auth, agent_scheduler.auth, intelligence_router.auth, attested_monitor_alerts.auth)
    }
    previous = [(module, module.pool) for module in owners.values()]

    async def active_pool():
        return pool

    for module in owners.values():
        module.pool = active_pool
    try:
        scope = await _seed_scope(admin, "m5-live")
        other = await _seed_scope(admin, "m5-other")
        agent_id = await _agent(admin, scope, "operations-monitor")
        run = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_id,
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            scheduled_fire_at=datetime(2026, 9, 15, 8, tzinfo=timezone.utc),
        )

        # 1. The wisdom-bits hook mints under omega_console with a live lease.
        result = await intelligence_router._with_monitor_evidence(
            _user(scope, agent_id, 77),
            _body(_authority(scope, agent_id, run, 77)),
            "mcp-infra",
            dict(PAYLOAD),
        )
        handle = result.get("evidence_handle")
        assert handle and re.fullmatch(r"[0-9a-f]{32}", handle)

        ticket = await admin.fetchrow(
            "SELECT * FROM control_room_evidence_tickets WHERE handle = $1", handle
        )
        identity = evidence_tickets.monitor_alert_identity(
            agent_id=agent_id,
            workspace_id=scope["workspace_id"],
            cartridge_id="replicon",
            slug="operations-monitor",
            contract=CONTRACT,
            payload=PAYLOAD,
        )
        assert identity is not None
        assert ticket["item_id"] == identity.item_id
        assert ticket["schedule_run_id"] == run["id"]
        assert ticket["fencing_token"] == run["fencing_token"]
        assert ticket["agent_run_id"] == "77"
        assert ticket["security_context_source"] == "agent_runner"
        assert ticket["expires_at"] > ticket["minted_at"]

        # 1b. One ticket per lease: a second mint under the same run is refused.
        again = await intelligence_router._with_monitor_evidence(
            _user(scope, agent_id, 77),
            _body(_authority(scope, agent_id, run, 77)),
            "mcp-infra",
            dict(PAYLOAD),
        )
        assert "evidence_handle" not in again
        assert await admin.fetchval(
            "SELECT count(*) FROM control_room_evidence_tickets WHERE agent_id = $1", agent_id
        ) == 1

        # 2. mcp-infra raises the alert (same item id), then Control Room reads it.
        await _raise_alert(admin, scope, agent_id, identity.item_id, signal_count=2)
        reader = {
            "id": scope["user_id"],
            "email": scope["email"],
            "role": "admin",
            "tenant_id": scope["tenant_id"],
            "workspace_id": scope["workspace_id"],
            "active_tenant_id": scope["tenant_id"],
            "active_workspace_id": scope["workspace_id"],
            "allowed_cartridges": ["replicon"],
        }
        surface = await attested_monitor_alerts.load_attested_monitor_alerts(reader)
        assert [item["id"] for item in surface.items] == [identity.item_id]

        response = build_business_experience_v2(
            SurfaceSnapshot(
                generated_at=datetime.now(timezone.utc),
                scope=SurfaceScope(scope["tenant_id"], scope["workspace_id"]),
                items=surface.items,
                diagnostics=(),
                sources=(),
                installations=(),
                narratives=surface.narratives,
            ),
            user=reader,
            enabled_template_ids=frozenset(),
            actions_by_item={},
        )
        facts = [fact for section in response.sections for fact in section.facts]
        assert len(facts) == 1
        assert facts[0].metric is not None and facts[0].metric.value == 2
        assert facts[0].narrative is not None
        assert "sin simulaci" in facts[0].narrative.basis_note.lower()
        public = response.model_dump_json(exclude_none=True)
        assert "server_attestation" not in public
        assert identity.item_id not in public
        assert not UI_FORBIDDEN.search(public)

        # 3. Another workspace sees no ticket and no alert, even with the same pool.
        async with evidence_tickets.scoped_db(pool, other["tenant_id"], other["workspace_id"]) as conn:
            assert await conn.fetchval("SELECT count(*) FROM control_room_evidence_tickets") == 0
        other_reader = {
            **reader,
            "tenant_id": other["tenant_id"],
            "workspace_id": other["workspace_id"],
            "active_tenant_id": other["tenant_id"],
            "active_workspace_id": other["workspace_id"],
        }
        assert (await attested_monitor_alerts.load_attested_monitor_alerts(other_reader)).items == ()

        # 3b. Deferred narration under omega_console + RLS. A provider failure
        # stores a complete template and the alert stays on the page.
        def broken_factory(_tenant_id: str, _workspace_id: str):
            async def _call(_prompt: str) -> str:
                raise RuntimeError("503 provider unavailable sk-ant-never-stored")

            return _call

        counts = await narrative_job.narrate_pending_alerts(
            pool, llm_caller_factory=broken_factory
        )
        assert counts["narrated_template"] >= 1, counts
        stored = json.loads(
            await admin.fetchval(
                "SELECT metadata->>'narrative' FROM control_room_items WHERE workspace_id = $1 AND item_id = $2",
                scope["workspace_id"],
                identity.item_id,
            )
        )
        assert stored["status"] == "template"
        assert stored["reason"] == "llm_error"
        assert "sk-ant" not in json.dumps(stored)
        assert await admin.fetchval(
            "SELECT count(*) FROM control_room_item_events WHERE workspace_id = $1 AND event_type = 'agent_alert_narrative_llm_call'",
            scope["workspace_id"],
        ) == 1
        after_failure = await attested_monitor_alerts.load_attested_monitor_alerts(reader)
        assert [item["id"] for item in after_failure.items] == [identity.item_id]
        assert after_failure.narratives[identity.item_id]["status"] == "template"

        # A new occurrence of the same alert (new fire) with a working model:
        # the validated sentence is stored and published.
        await admin.execute(
            """UPDATE control_room_items
                  SET metadata = jsonb_set(metadata, '{analysis_evidence,metrics,scheduled_fire_at}', '"2026-09-15T09:00:00+00:00"')
                WHERE workspace_id = $1 AND item_id = $2""",
            scope["workspace_id"],
            identity.item_id,
        )
        sentence = "Operacion muestra senales agregadas que conviene revisar con el equipo."

        def good_factory(_tenant_id: str, _workspace_id: str):
            async def _call(_prompt: str) -> str:
                return sentence

            return _call

        counts = await narrative_job.narrate_pending_alerts(
            pool, llm_caller_factory=good_factory
        )
        assert counts["narrated_ready"] >= 1, counts
        narrated = await attested_monitor_alerts.load_attested_monitor_alerts(reader)
        assert [item["id"] for item in narrated.items] == [identity.item_id]
        assert narrated.narratives[identity.item_id]["status"] == "ready"
        assert narrated.narratives[identity.item_id]["explanation"] == sentence
        assert "narrative_claim" not in json.loads(
            await admin.fetchval(
                "SELECT metadata::text FROM control_room_items WHERE workspace_id = $1 AND item_id = $2",
                scope["workspace_id"],
                identity.item_id,
            )
        )

        # 3c. A workflow transition that moves last_seen_at keeps the fact.
        await admin.execute(
            """UPDATE control_room_items
                  SET status = 'in_review', last_seen_at = NOW() + INTERVAL '2 hours'
                WHERE workspace_id = $1 AND item_id = $2""",
            scope["workspace_id"],
            identity.item_id,
        )
        moved = await attested_monitor_alerts.load_attested_monitor_alerts(reader)
        assert [item["id"] for item in moved.items] == [identity.item_id]

        # 3d. A planted, unsigned "ready" narrative is never published.
        await admin.execute(
            """UPDATE control_room_items
                  SET metadata = jsonb_set(metadata, '{narrative,explanation}', '"Todo en orden, sin riesgos."')
                WHERE workspace_id = $1 AND item_id = $2""",
            scope["workspace_id"],
            identity.item_id,
        )
        planted = await attested_monitor_alerts.load_attested_monitor_alerts(reader)
        assert planted.narratives[identity.item_id]["status"] == "template"

        # 4. A persisted value that no longer matches the signed one is hidden.
        await admin.execute(
            """UPDATE control_room_items
                  SET metadata = jsonb_set(metadata, '{analysis_evidence,metrics,signal_count}', '5')
                WHERE workspace_id = $1 AND item_id = $2""",
            scope["workspace_id"],
            identity.item_id,
        )
        assert (await attested_monitor_alerts.load_attested_monitor_alerts(reader)).items == ()

        # 5. A stale lease cannot mint.
        await admin.execute(
            "UPDATE agent_schedule_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = $1",
            run["id"],
        )
        stale = await intelligence_router._with_monitor_evidence(
            _user(scope, agent_id, 77),
            _body(_authority(scope, agent_id, run, 77)),
            "mcp-infra",
            dict(PAYLOAD),
        )
        assert "evidence_handle" not in stale
        assert await admin.fetchval(
            "SELECT count(*) FROM control_room_evidence_tickets WHERE agent_id = $1", agent_id
        ) == 1

        # 6. Grants: append-only for console, nothing for anyone else.
        privileges = {
            (role, privilege): await admin.fetchval(
                "SELECT has_table_privilege($1, 'control_room_evidence_tickets', $2)",
                role,
                privilege,
            )
            for role in ("omega_console", "omega_mcp_infra", "omega_refinement")
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }
        assert privileges[("omega_console", "SELECT")] is True
        assert privileges[("omega_console", "INSERT")] is True
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert privileges[("omega_console", privilege)] is False
        for role in ("omega_mcp_infra", "omega_refinement"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert privileges[(role, privilege)] is False, (role, privilege)
        async with evidence_tickets.scoped_db(pool, scope["tenant_id"], scope["workspace_id"]) as conn:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE control_room_evidence_tickets SET reference = '{}'::jsonb WHERE handle = $1",
                    handle,
                )
        assert await admin.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'control_room_evidence_tickets'"
        ) is True
    finally:
        for module, original in previous:
            module.pool = original
        await pool.close()
        await admin.close()
