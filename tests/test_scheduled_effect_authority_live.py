from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
from fastapi import HTTPException

from tests.test_control_room_live_postgres_operational_truth_pipeline import _seed_scope
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


ROOT = Path(__file__).resolve().parents[1]
MCP_PASSWORD = "test_omega_mcp_infra_password"
SIGNING_KEY = "scheduled-effect-signing-key-that-is-isolated-123456"


def _purge_app() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _authority(
    *, tool: str, args: dict, scope: dict, agent_id: str, schedule_id: int, fence: int
) -> dict:
    value = {
        "source": "console",
        "audience": "mcp-infra",
        "purpose": "mcp.scheduled_effect",
        "tool": tool,
        "schedule_run_id": schedule_id,
        "fencing_token": fence,
        "tenant_id": scope["tenant_id"],
        "workspace_id": scope["workspace_id"],
        "agent_id": agent_id,
        "agent_run_id": 71,
        "jti": uuid.uuid4().hex + uuid.uuid4().hex,
        "body_digest": hashlib.sha256(
            json.dumps(
                {"tool": tool, "args": args},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "_signed_at": int(time.time()),
        "_signature_version": "hmac-sha256-v2",
    }
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    value["_signature"] = hmac.new(
        SIGNING_KEY.encode(), canonical, hashlib.sha256
    ).hexdigest()
    return value


@pytest.mark.asyncio
async def test_reclaimed_schedule_fence_rejects_old_effect_at_real_sink(
    postgres_with_real_init_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = urlsplit(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        scope = await _seed_scope(admin, "scheduled-effect")
        agent_id = str(uuid.uuid4())
        await admin.execute(
            """INSERT INTO agents(
                   id,cartridge_id,slug,name,is_active,tenant_id,workspace_id,extra
               ) VALUES($1,'replicon',$2,$2,TRUE,$3,$4,'{}')""",
            agent_id,
            f"scheduled-effect-{uuid.uuid4().hex}",
            scope["tenant_id"],
            scope["workspace_id"],
        )
        schedule_id = await admin.fetchval(
            """INSERT INTO agent_schedule_runs(
                   tenant_id,workspace_id,agent_id,scheduled_fire_at,status,
                   lease_expires_at,fencing_token
               ) VALUES($1,$2,$3,NOW(),'running',NOW()+INTERVAL '10 minutes',1)
               RETURNING id""",
            scope["tenant_id"],
            scope["workspace_id"],
            agent_id,
        )
        assert await admin.fetchval(
            "SELECT has_table_privilege('omega_mcp_infra',"
            "'agent_schedule_runs','SELECT')"
        )

        _purge_app()
        monkeypatch.syspath_prepend(str(ROOT / "mcp-infra"))
        for name, value in {
            "APP_ENV": "test",
            "SECURITY_CONTEXT_SIGNING_KEY": SIGNING_KEY,
            "DATABASE_URL": postgres_with_real_init_schema.replace(
                "postgres:test_postgres_password", f"omega_mcp_infra:{MCP_PASSWORD}"
            ),
            "PG_HOST": parsed.hostname or "127.0.0.1",
            "PG_PORT": str(parsed.port),
            "PG_DB": parsed.path.lstrip("/"),
            "PG_USER": "omega_mcp_infra",
            "PG_PASSWORD": MCP_PASSWORD,
            "AIRFLOW_USER": "airflow",
            "AIRFLOW_PASSWORD": "airflow-password",
            "SUPERSET_USER": "superset",
            "SUPERSET_PASSWORD": "superset-password",
        }.items():
            monkeypatch.setenv(name, value)
        verifier = importlib.import_module("app.scheduled_effect_authority")
        control_room = importlib.import_module("app.tools.control_room")
        tool = "control_room__raise_alert"
        args = {
            "alert_type": "fence_probe",
            "cartridge_id": "replicon",
            "domain": "Operaciones",
            "source_dataset": "employee_360",
            "entity_key": "employee=42",
            "title": "Fencing probe",
            "message": "Scheduled effect authority acceptance probe.",
            "severity": "high",
            "confidence": 0.91,
        }
        context = {
            "trusted": True,
            "tenant_id": scope["tenant_id"],
            "workspace_id": scope["workspace_id"],
            "agent_id": agent_id,
            "agent_slug": "scheduled-effect",
            "agent_name": "Scheduled Effect",
            "agent_run_id": 71,
            "email": "agent-runner@omega.local",
            "permissions": ["control_room.write"],
        }
        old = _authority(
            tool=tool,
            args=args,
            scope=scope,
            agent_id=agent_id,
            schedule_id=schedule_id,
            fence=1,
        )
        verifier.validate_scheduled_effect_authority(
            old, tool=tool, args={**args, "effect_authority": old}, context=context
        )
        await admin.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            f"agent_schedule_effect:{schedule_id}",
        )
        await admin.execute(
            "UPDATE agent_schedule_runs SET fencing_token=2,"
            "lease_expires_at=NOW()+INTERVAL '10 minutes' WHERE id=$1",
            schedule_id,
        )
        with pytest.raises(HTTPException) as stale:
            control_room.control_room__raise_alert(
                **args, effect_authority=old, security_context=context
            )
        assert stale.value.status_code == 409
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM control_room_items WHERE workspace_id=$1",
                scope["workspace_id"],
            )
            == 0
        )

        fresh = _authority(
            tool=tool,
            args=args,
            scope=scope,
            agent_id=agent_id,
            schedule_id=schedule_id,
            fence=2,
        )
        verifier.validate_scheduled_effect_authority(
            fresh, tool=tool, args={**args, "effect_authority": fresh}, context=context
        )
        result = control_room.control_room__raise_alert(
            **args, effect_authority=fresh, security_context=context
        )
        assert result["created"] is True
        with pytest.raises(ValueError, match="replay"):
            verifier.validate_scheduled_effect_authority(
                fresh,
                tool=tool,
                args={**args, "effect_authority": fresh},
                context=context,
            )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM control_room_items WHERE workspace_id=$1",
                scope["workspace_id"],
            )
            == 1
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM control_room_item_events WHERE workspace_id=$1",
                scope["workspace_id"],
            )
            == 1
        )
    finally:
        await admin.close()
        _purge_app()
