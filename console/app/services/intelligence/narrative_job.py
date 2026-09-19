"""Mission 5: deferred narration of persisted monitor alerts.

The monitor chain raises the alert and finishes. This job runs afterwards, from
the ``agent_runner`` DAG, and attaches a Spanish narrative to each open monitor
alert that does not yet have one for its current occurrence.

Guarantees, in the order they matter:

* The alert is never blocked. Nothing here runs inside the monitor chain, and no
  row lock is held while the model is called: a short transaction claims the
  row with a random token, the model is called with no transaction open, and a
  second short transaction writes the narrative only if the token still matches
  (compare-and-set). A lost race is reported, never retried in place.
* Only console's own narratives count. ``control_room_items.metadata`` is
  writable by mcp-infra, so a stored narrative is "current" only when its
  console attestation verifies for this item and scope AND its fingerprint
  matches the alert as it is now. A planted narrative, signed or not, never
  blocks a claim and never makes the job skip an alert. What the job stores is
  signed with :func:`alert_narrative.attest_narrative`.
* The budget is bounded before the model is called. At most
  :data:`MAX_LLM_CALLS_PER_DAY` attempts per workspace per UTC day, reserved as a
  ``control_room_item_events`` row in the claim transaction, so a crash after the
  reservation still counts. At most :data:`MAX_ALERTS_PER_TICK` alerts per
  workspace per tick.
* No platform key. The model is called with a workspace-scoped, non-admin user
  context, so ``llm_client`` reads the workspace key from Vault and never falls
  back to ``ANTHROPIC_API_KEY``. A workspace with no key, a Vault failure, a
  provider error or a timeout all store the TEMPLATE narrative.
* Advisory only. The job writes ``metadata.narrative`` and nothing else; it
  executes nothing and changes no status.

This module never raises. Failures are isolated per workspace and per alert and
reported by exception class name only.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services import llm_client, scheduled_runtime
from app.services.db_scope import scoped_db
from app.services.intelligence.alert_narrative import (
    NARRATIVE_METADATA_KEY,
    attest_narrative,
    narrate_alert,
    published_narrative,
    verified_stored_narrative,
)
from app.services.intelligence.narrative_service import (
    NARRATOR_VERSION,
    STATUS_READY,
)

logger = logging.getLogger(__name__)

MAX_ALERTS_PER_TICK = 20
MAX_LLM_CALLS_PER_DAY = 50
CLAIM_TTL_SECONDS = 300
# Wall-clock budget for one tick. Each alert is bounded by the narrator's own
# 8 s model timeout, so without this a tick with several busy workspaces would
# outlive the Airflow request. Alerts not reached are narrated on a later tick.
TICK_BUDGET_SECONDS = 180.0

NARRATABLE_STATUSES: tuple[str, ...] = (
    "open",
    "in_review",
    "decision_created",
    "approved",
)
LLM_CALL_EVENT_TYPE = "agent_alert_narrative_llm_call"
CLAIM_METADATA_KEY = "narrative_claim"

# The least-privileged role that still yields a signed, workspace-scoped
# security context: Vault's secret read only requires a trusted context with a
# tenant and a workspace, and llm_client only uses the platform key for
# owner/super_admin/admin.
NARRATOR_ROLE = "workspace_user"

_LLM_SYSTEM_PROMPT = (
    "Eres un redactor de negocio. Respondes con una sola frase en espanol, "
    "sin cifras, sin comillas y sin recomendar acciones."
)
_LLM_MAX_TOKENS = 200
_LLM_TEMPERATURE = 0.2

_CLAIM_LOST = "lost"
_CLAIM_CURRENT = "current"
_CLAIM_ACQUIRED = "acquired"

_monotonic = time.monotonic

# ── SQL ──────────────────────────────────────────────────────────────────────
# Static statements with positional parameters only. Every statement filters by
# tenant_id AND workspace_id on top of the RLS scope set by scoped_db.

CANDIDATES_SQL = """
SELECT item_id, domain, severity, metadata
  FROM control_room_items
 WHERE tenant_id = $1::uuid
   AND workspace_id = $2::uuid
   AND item_kind = 'agent_alert'
   AND status = ANY($3::text[])
   AND metadata ? 'analysis_evidence'
 ORDER BY last_seen_at DESC, item_id DESC
 LIMIT $4
"""

# Claims the row unless a live claim exists. Deliberately NOT conditioned on the
# stored narrative: that field is writable outside console, so whether it is
# current is decided after the claim, by verifying its attestation. The claim
# timestamp is the database clock, and a malformed claim is treated as expired;
# CASE is used so the numeric cast only runs on a JSON number.
CLAIM_SQL = """
UPDATE control_room_items
   SET metadata = COALESCE(metadata, jsonb_build_object())
       || jsonb_build_object(
              'narrative_claim',
              jsonb_build_object(
                  'token', $4::text,
                  'claimed_epoch', EXTRACT(EPOCH FROM clock_timestamp())
              )
          )
 WHERE tenant_id = $1::uuid
   AND workspace_id = $2::uuid
   AND item_id = $3
   AND item_kind = 'agent_alert'
   AND CASE
         WHEN jsonb_typeof(metadata->'narrative_claim') IS DISTINCT FROM 'object'
           THEN TRUE
         WHEN jsonb_typeof(metadata->'narrative_claim'->'claimed_epoch')
              IS DISTINCT FROM 'number'
           THEN TRUE
         ELSE (metadata->'narrative_claim'->>'claimed_epoch')::double precision
              <= EXTRACT(EPOCH FROM clock_timestamp()) - $5::double precision
       END
RETURNING item_id, domain, severity, metadata
"""

# Serialises the count-then-insert of the daily budget for one workspace, held
# only for the short claim transaction.
BUDGET_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"

BUDGET_COUNT_SQL = """
SELECT COUNT(*)
  FROM control_room_item_events
 WHERE tenant_id = $1::uuid
   AND workspace_id = $2::uuid
   AND event_type = 'agent_alert_narrative_llm_call'
   AND created_at >= $3::timestamptz
   AND created_at < $4::timestamptz
"""

BUDGET_RESERVE_SQL = """
INSERT INTO control_room_item_events (
    tenant_id, workspace_id, item_id, event_type,
    actor_id, actor_email, metadata
)
SELECT tenant_id, workspace_id, item_id, 'agent_alert_narrative_llm_call',
       NULL, NULL, $4::jsonb
  FROM control_room_items
 WHERE tenant_id = $1::uuid
   AND workspace_id = $2::uuid
   AND item_id = $3
"""

WRITE_SQL = """
UPDATE control_room_items
   SET metadata = (COALESCE(metadata, jsonb_build_object()) - 'narrative_claim')
       || jsonb_build_object('narrative', $4::jsonb)
 WHERE tenant_id = $1::uuid
   AND workspace_id = $2::uuid
   AND item_id = $3
   AND item_kind = 'agent_alert'
   AND metadata->'narrative_claim'->>'token' = $5::text
RETURNING item_id
"""

SQL_STATEMENTS: tuple[str, ...] = (
    CANDIDATES_SQL,
    CLAIM_SQL,
    BUDGET_LOCK_SQL,
    BUDGET_COUNT_SQL,
    BUDGET_RESERVE_SQL,
    WRITE_SQL,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _metadata(value: Any) -> Mapping[str, Any]:
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, Mapping) else {}


def _narrative_json(narrative: Mapping[str, Any]) -> str:
    return json.dumps(dict(narrative), ensure_ascii=False, sort_keys=True)


def verified_current_narrative(
    *,
    domain: Any,
    severity: Any,
    metadata: Mapping[str, Any],
    item_id: str,
    tenant_id: str,
    workspace_id: str,
) -> dict[str, Any] | None:
    """The stored narrative if console signed it for this item and scope AND it
    describes the alert as it is now; otherwise None.

    An unsigned, forged or foreign-scope narrative is None regardless of its
    fingerprint, so it can never make the job skip an alert.
    """
    verified = verified_stored_narrative(
        metadata.get(NARRATIVE_METADATA_KEY),
        item_id=item_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if verified is None:
        return None
    current = published_narrative(
        domain=domain,
        severity=severity,
        metadata=metadata,
        item_id=item_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if verified.get("source_fingerprint") != current.get("source_fingerprint"):
        return None
    return verified


def _utc_day(now: datetime | None) -> tuple[datetime, datetime]:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    start = current.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def narrator_user_context(tenant_id: str, workspace_id: str) -> dict[str, Any]:
    """Least-privileged workspace context for the narrator's model call.

    Not an interactive user and never an admin role: ``llm_client`` falls back
    to the platform key only for owner/super_admin/admin, and Vault authorises a
    workspace secret read from the signed tenant/workspace alone. No cartridge
    access is carried.
    """
    return {
        "id": None,
        "email": "",
        "role": NARRATOR_ROLE,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "allowed_cartridges": [],
    }


async def _no_tool(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    return {"error": "tools are disabled for the narrator"}


def default_llm_caller_factory(
    tenant_id: str, workspace_id: str
) -> Callable[[str], Any]:
    """Async caller bound to one workspace's own LLM key.

    Exceptions propagate to ``build_narrative``, which turns them into a
    template narrative with a closed reason code.
    """
    user_context = narrator_user_context(tenant_id, workspace_id)

    async def _call(prompt: str) -> str:
        reply, _viewer_urls, _messages = await llm_client.chat(
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            invoke_tool=_no_tool,
            tool_server_map={},
            max_tokens=_LLM_MAX_TOKENS,
            temperature=_LLM_TEMPERATURE,
            user_context=dict(user_context),
        )
        return reply

    return _call


def _failure(workspace_id: str, exc: BaseException) -> dict[str, str]:
    return {"workspace_id": workspace_id, "error_code": type(exc).__name__}


# ── Per alert ────────────────────────────────────────────────────────────────


async def _claim(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    item_id: str,
    token: str,
    day: tuple[datetime, datetime],
) -> tuple[str, Mapping[str, Any] | None, bool]:
    """Claim the row and reserve one model call.

    Returns ``(outcome, claimed_row, may_call_llm)`` where outcome is
    ``lost`` (someone else holds a live claim), ``current`` (a verified,
    current narrative was already stored; the claim was released and no budget
    was touched) or ``acquired``.
    """
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            CLAIM_SQL,
            tenant_id,
            workspace_id,
            item_id,
            token,
            float(CLAIM_TTL_SECONDS),
        )
        if row is None:
            return _CLAIM_LOST, None, False

        # Re-check on the row as claimed: another runner may have finished
        # between selection and claim. Only a verified narrative counts.
        current = verified_current_narrative(
            domain=row["domain"],
            severity=row["severity"],
            metadata=_metadata(row["metadata"]),
            item_id=item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if current is not None:
            # Release the claim by writing the verified narrative back under
            # our token, in this same transaction. No budget, no model call.
            await conn.fetchrow(
                WRITE_SQL,
                tenant_id,
                workspace_id,
                item_id,
                _narrative_json(current),
                token,
            )
            return _CLAIM_CURRENT, None, False

        await conn.execute(
            BUDGET_LOCK_SQL,
            f"control_room_narrative_budget:{tenant_id}:{workspace_id}",
        )
        used = await conn.fetchval(
            BUDGET_COUNT_SQL, tenant_id, workspace_id, day[0], day[1]
        )
        if int(used or 0) >= MAX_LLM_CALLS_PER_DAY:
            return _CLAIM_ACQUIRED, row, False
        await conn.execute(
            BUDGET_RESERVE_SQL,
            tenant_id,
            workspace_id,
            item_id,
            json.dumps({"narrator_version": NARRATOR_VERSION}, sort_keys=True),
        )
        return _CLAIM_ACQUIRED, row, True


async def _write(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    item_id: str,
    narrative: Mapping[str, Any],
    token: str,
) -> bool:
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            WRITE_SQL,
            tenant_id,
            workspace_id,
            item_id,
            _narrative_json(narrative),
            token,
        )
    return row is not None


async def _narrate_one(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    candidate: Mapping[str, Any],
    caller: Callable[[str], Any] | None,
    day: tuple[datetime, datetime],
    counts: dict[str, Any],
) -> None:
    item_id = str(candidate["item_id"] or "")
    if (
        verified_current_narrative(
            domain=candidate["domain"],
            severity=candidate["severity"],
            metadata=_metadata(candidate["metadata"]),
            item_id=item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        is not None
    ):
        counts["skipped_current"] += 1
        return

    token = secrets.token_hex(16)
    outcome, claimed, may_call = await _claim(
        pool,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item_id=item_id,
        token=token,
        day=day,
    )
    if outcome == _CLAIM_LOST:
        counts["lost_claim"] += 1
        return
    if outcome == _CLAIM_CURRENT or claimed is None:
        counts["skipped_current"] += 1
        return
    if not may_call:
        counts["budget_exhausted"] += 1

    # No transaction is open here: the claim is committed and the row is free.
    narrative = await narrate_alert(
        domain=claimed["domain"],
        severity=claimed["severity"],
        metadata=_metadata(claimed["metadata"]),
        llm_caller=caller if may_call else None,
    )
    signed = attest_narrative(
        narrative,
        item_id=item_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )

    written = await _write(
        pool,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item_id=item_id,
        narrative=signed,
        token=token,
    )
    if not written:
        counts["lost_claim"] += 1
    elif narrative.get("status") == STATUS_READY:
        counts["narrated_ready"] += 1
    else:
        counts["narrated_template"] += 1


# ── Entry point ──────────────────────────────────────────────────────────────


async def narrate_pending_alerts(
    pool: Any,
    *,
    llm_caller_factory: Callable[[str, str], Callable[[str], Any] | None]
    | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Narrate open monitor alerts across active workspaces. Never raises."""
    counts: dict[str, Any] = {
        "status": "ready",
        "workspaces": 0,
        "candidates": 0,
        "narrated_ready": 0,
        "narrated_template": 0,
        "skipped_current": 0,
        "budget_exhausted": 0,
        "lost_claim": 0,
        "deferred": 0,
        "failures": [],
    }
    factory = llm_caller_factory or default_llm_caller_factory
    day = _utc_day(now)
    started = _monotonic()

    try:
        scopes = await scheduled_runtime._active_workspace_scopes(pool)
    except Exception as exc:  # noqa: BLE001 - never raise, report class only
        counts["status"] = "unavailable"
        counts["failures"].append(_failure("", exc))
        return counts
    counts["workspaces"] = len(scopes)
    if not scopes:
        counts["status"] = "no_eligible_workspaces"
        return counts

    for scope in scopes:
        tenant_id = str(scope.get("tenant_id") or "")
        workspace_id = str(scope.get("workspace_id") or "")
        try:
            async with scoped_db(pool, tenant_id, workspace_id) as conn:
                candidates = list(
                    await conn.fetch(
                        CANDIDATES_SQL,
                        tenant_id,
                        workspace_id,
                        list(NARRATABLE_STATUSES),
                        MAX_ALERTS_PER_TICK,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - isolate scopes, class only
            counts["failures"].append(_failure(workspace_id, exc))
            continue
        counts["candidates"] += len(candidates)
        if not candidates:
            continue

        try:
            caller = factory(tenant_id, workspace_id)
        except Exception as exc:  # noqa: BLE001 - template, never platform key
            logger.warning(
                "narrative_job: caller factory failed error_code=%s",
                type(exc).__name__,
            )
            caller = None

        for candidate in candidates:
            if _monotonic() - started > TICK_BUDGET_SECONDS:
                counts["deferred"] += 1
                continue
            try:
                await _narrate_one(
                    pool,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    candidate=candidate,
                    caller=caller,
                    day=day,
                    counts=counts,
                )
            except Exception as exc:  # noqa: BLE001 - isolate alerts, class only
                logger.warning(
                    "narrative_job: alert narration failed error_code=%s",
                    type(exc).__name__,
                )
                counts["failures"].append(_failure(workspace_id, exc))

    if counts["failures"]:
        counts["status"] = "partial"
    return counts


__all__ = (
    "CLAIM_TTL_SECONDS",
    "LLM_CALL_EVENT_TYPE",
    "MAX_ALERTS_PER_TICK",
    "MAX_LLM_CALLS_PER_DAY",
    "NARRATABLE_STATUSES",
    "NARRATOR_ROLE",
    "SQL_STATEMENTS",
    "TICK_BUDGET_SECONDS",
    "default_llm_caller_factory",
    "narrate_pending_alerts",
    "narrator_user_context",
    "verified_current_narrative",
)
