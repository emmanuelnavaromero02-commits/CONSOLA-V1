from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import require_authenticated
from app.services import audit_service, auth
from app.services.csrf import require_csrf
from app.services.db_scope import scoped_db_for_user
from app.services.permissions import require_permission


router = APIRouter(
    prefix="/api/copilot/memory",
    tags=["copilot-memory"],
    dependencies=[Depends(require_permission("copilot.use"))],
)


_MAX_FACT_LEN = 500


@router.post("/fact", dependencies=[Depends(require_csrf)])
async def add_fact(body: dict, user: dict = Depends(require_authenticated)):
    """Record an explicit user-stated fact.

    Body: ``{"fact": "...", "source": "explicit"}``. Source is
    optional — defaults to 'explicit'. The LLM extraction path uses
    source='extracted'.
    """
    fact = (body or {}).get("fact", "").strip()
    if not fact:
        raise HTTPException(400, "Missing 'fact'")
    if len(fact) > _MAX_FACT_LEN:
        raise HTTPException(400, f"Fact too long (max {_MAX_FACT_LEN} chars)")
    source = (body or {}).get("source", "explicit")
    if source not in {"explicit", "extracted"}:
        raise HTTPException(400, "Invalid source")

    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        row = await conn.fetchrow(
            """
            INSERT INTO user_facts
                (user_id, tenant_id, workspace_id, scope_status, fact, source)
            VALUES ($1, $2::uuid, $3::uuid, 'scoped', $4, $5)
            ON CONFLICT (user_id, workspace_id, fact) WHERE workspace_id IS NOT NULL
            DO UPDATE
              SET source = EXCLUDED.source,
                  tenant_id = EXCLUDED.tenant_id,
                  scope_status = 'scoped'
            RETURNING id, user_id, fact, source, confidence, created_at
            """,
            user["id"], tenant_id, workspace_id, fact, source,
        )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.memory.fact.add",
        resource_type="user_fact",
        resource_id=str(row["id"]),
        status="success",
        metadata={"source": source, "fact_len": len(fact)},
    )
    return {"ok": True, "fact": {**dict(row), "id": int(row["id"])}}


@router.get("")
async def list_memory(user: dict = Depends(require_authenticated)):
    """Return facts + preferences for the current user. Pure read,
    no audit (read-of-self is non-eventful)."""
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        fact_rows = await conn.fetch(
            """
            SELECT id, fact, source, confidence, created_at
              FROM user_facts
             WHERE user_id = $1
               AND scope_status = 'scoped'
               AND workspace_id = $2::uuid
               AND ($3::uuid IS NULL OR tenant_id = $3::uuid)
             ORDER BY created_at DESC
             LIMIT 200
            """,
            user["id"], workspace_id, tenant_id,
        )
        pref_rows = await conn.fetch(
            """
            SELECT pref_key, pref_value, updated_at
              FROM user_preferences
             WHERE user_id = $1
               AND scope_status = 'scoped'
               AND workspace_id = $2::uuid
               AND ($3::uuid IS NULL OR tenant_id = $3::uuid)
             ORDER BY pref_key
            """,
            user["id"], workspace_id, tenant_id,
        )
    return {
        "facts":       [dict(r) | {"id": int(r["id"])} for r in fact_rows],
        "preferences": [dict(r) for r in pref_rows],
    }


@router.delete("/fact/{fact_id}", dependencies=[Depends(require_csrf)])
async def delete_fact(
    fact_id: int,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Permanently forget a fact. Audits the deletion so we can
    distinguish "user forgot they had this fact" from "fact never
    existed" later. Only the fact owner can delete; the SQL WHERE
    clause carries user_id so this is RBAC-by-rows."""
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        deleted = await conn.fetchval(
            """
            DELETE FROM user_facts
             WHERE id = $1
               AND user_id = $2
               AND scope_status = 'scoped'
               AND workspace_id = $3::uuid
               AND ($4::uuid IS NULL OR tenant_id = $4::uuid)
            RETURNING id
            """,
            fact_id, user["id"], workspace_id, tenant_id,
        )
    if deleted is None:
        raise HTTPException(404, "Fact not found")
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.memory.fact.delete",
        resource_type="user_fact",
        resource_id=str(fact_id),
        status="success",
    )
    return {"ok": True, "deleted": int(deleted)}


_ALLOWED_PREF_KEYS = {
    "tone",
    "language",
    "default_cartridge",
    "briefing_enabled",
    "memory_extraction_enabled",
}


@router.put("/preference/{key}", dependencies=[Depends(require_csrf)])
async def set_preference(
    key: str,
    body: dict,
    user: dict = Depends(require_authenticated),
):
    """Set or update a user preference. The keys are an allowlist
    so a typo can't pollute the table with junk."""
    if key not in _ALLOWED_PREF_KEYS:
        raise HTTPException(400, f"Unknown preference key: {key}")
    value = (body or {}).get("value", "")
    if not isinstance(value, str) or len(value) > 200:
        raise HTTPException(400, "Preference value must be a string ≤ 200 chars")
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        await conn.execute(
            """
            INSERT INTO user_preferences
                (user_id, tenant_id, workspace_id, scope_status, pref_key, pref_value)
            VALUES ($1, $2::uuid, $3::uuid, 'scoped', $4, $5)
            ON CONFLICT (user_id, workspace_id, pref_key) WHERE workspace_id IS NOT NULL
            DO UPDATE
              SET pref_value = EXCLUDED.pref_value,
                  tenant_id = EXCLUDED.tenant_id,
                  scope_status = 'scoped',
                  updated_at = NOW()
            """,
            user["id"], tenant_id, workspace_id, key, value,
        )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.memory.preference.set",
        resource_type="user_preference",
        resource_id=key,
        status="success",
        metadata={"value": value},
    )
    return {"ok": True, "key": key, "value": value}
