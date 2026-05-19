"""Sprint v1.44.1 (Tarea F) — first-time-user onboarding state.

Two endpoints:
  * GET  /api/system/onboarding/state    — am I done with the tour?
  * POST /api/system/onboarding/complete — mark me done.

The wizard JS (next session) calls /state on every page load; if
``completed=false`` it triggers the 5-step tour. The dropdown
"Mostrar tour" menu item posts to /complete (well, actually it
RESETS completion — that's a separate concern) and re-runs the
wizard locally.

State lives in users.onboarding_completed (added by
infra/init/48_users_onboarding_completed.sql).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.dependencies import require_authenticated
from app.services import audit_service, auth
from app.services.csrf import require_csrf


router = APIRouter(prefix="/api/system/onboarding", tags=["Onboarding"])

# Single source of truth for the wizard step count. If the wizard grows
# from 5 steps to 6, change this constant — the UI reads it.
TOTAL_STEPS = 5


@router.get("/state")
async def onboarding_state(user: dict = Depends(require_authenticated)):
    """Returns ``{completed, current_step, total_steps}``.

    ``current_step`` is intentionally 0 in this initial version —
    we don't persist per-step progress yet, the wizard restarts
    from the beginning every time. Keeps the schema migration
    additive (just a boolean column). A future iteration can
    promote ``current_step`` to a column if needed.
    """
    completed = bool(user.get("onboarding_completed"))
    return {
        "completed":   completed,
        "current_step": 0 if not completed else TOTAL_STEPS,
        "total_steps":  TOTAL_STEPS,
    }


@router.post("/complete", dependencies=[Depends(require_csrf)])
async def onboarding_complete(
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Mark the current user's onboarding as done. Idempotent —
    calling /complete twice flips the bit once and audits once per
    call (so we can spot the "operator re-runs the tour repeatedly"
    pattern in analytics if it matters)."""
    pool = await auth.pool()
    await pool.execute(
        "UPDATE users SET onboarding_completed = TRUE WHERE id = $1",
        user["id"],
    )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="onboarding.complete",
        resource_type="user",
        resource_id=str(user["id"]),
        status="success",
    )
    return {"ok": True, "completed": True}
