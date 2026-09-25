"""Where the domain aggregates meet the shared agent memory.

Mission 4, part B4. This is the concrete case the shared memory exists for, and
it is a real one rather than a demonstration: Mission 1 could not ship
``finance.budget_vs_actual_by_cost_center`` because no cartridge has a budget, and
it could not ship ``risk.cost_center_overrun`` for exactly the same reason
(no budget data in any cartridge). Two different agents therefore hit the same
wall from two directions, and without shared memory each of them rediscovers it on
every run and reports it as news.

The detection is genuine, not a hardcoded assertion. ``cost_center_expense`` IS a
published Gold dataset; what is missing is its numbers — the dataset ships
``CAST(NULL AS DECIMAL(15,2)) AS total_expense`` with a TODO, because the
purchase-to-cost-centre link lives in an entity that is not extracted. So the hook
counts rows and non-null expenses: if the column is entirely NULL the gap is still
real today, and the finding carries those counts as evidence. If someone later
lands the account assignment and the column fills in, the hook stops recording and
the stale finding can be retired by setting its ``expires_at``.

These hooks live outside ``finance_aggregates`` / ``risk_aggregates`` on purpose.
Those modules are cap-free SQL readers with one job; a memory write is a side
effect on a different database, and folding it in would make every aggregate a
writer. The domain views call the hooks instead, which is also where the wiring is
visible to a reader.

Nothing here can fail a caller. ``record_finding`` already swallows and logs, and
the detection wraps its own Gold read the same way the aggregates do.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.domains.agentops.finance_monitor import (
    FINANCE_MONITOR_CARTRIDGE,
    FINANCE_MONITOR_SLUG,
)

# The global conversational template, seeded by infra/init/86_sap_s4hana_agents_seed.sql.
FINANCE_CONVERSATIONAL_SLUG = "sap_s4hana_controller_financiero"
from app.services.intelligence import agent_memory
from app.services.intelligence.domain_aggregate_support import (
    GOLD_SCOPE_PREDICATE,
    open_gold_scope,
    resolve_relation,
)

logger = logging.getLogger(__name__)

# The subject both agents agree on. Finance writes it, Risk reads it; it is the
# join key, so it must stay stable and identical in both places.
COST_CENTER_BUDGET_SUBJECT = "cost_center_budget"

COST_CENTER_EXPENSE_DATASET = "cost_center_expense"
_EXPENSE_REQUIRED = frozenset({"cost_center", "total_expense"})

# Two summaries, because only one branch actually proves the second half. Writing
# one constant for both would have the finding assert as fact something the code
# never checked — the exact over-promising this repository is built to avoid.
COST_CENTER_BUDGET_SUMMARY = (
    "No hay presupuesto por centro de costo en ningun cartucho, y el gasto real "
    "por centro de costo llega vacio en el origen. Comparar presupuesto contra "
    "real, o medir sobregiro, no se puede calcular con los datos actuales."
)
COST_CENTER_BUDGET_SUMMARY_UNVERIFIED = (
    "No hay presupuesto por centro de costo en ningun cartucho, asi que comparar "
    "presupuesto contra real, o medir sobregiro, no se puede calcular. El estado "
    "del gasto real no se pudo verificar en esta corrida."
)


async def _expense_column_is_empty(user: dict | None) -> tuple[bool, dict[str, Any]]:
    """Count rows and non-null expenses in the cost-centre expense dataset.

    Returns ``(gap_is_real, evidence)``. ``gap_is_real`` is True when the dataset
    is missing, unreadable, or present with zero non-null expenses. Only
    aggregates travel back: two counts, never a row.
    """
    async with open_gold_scope(user) as scope:
        resolved = await resolve_relation(
            scope,
            COST_CENTER_EXPENSE_DATASET,
            required=_EXPENSE_REQUIRED,
        )
        if resolved.missing_required:
            return True, {
                "reason": "missing_columns",
                "missing": list(resolved.missing_required),
            }
        row = await scope.conn.fetchrow(
            f"""
            SELECT COUNT(*)::bigint AS rows_total,
                   COUNT(total_expense)::bigint AS rows_with_expense
              FROM {resolved.relation.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
            """,
            scope.workspace_id,
            scope.tenant_id,
        )
    rows_total = int((row or {}).get("rows_total") or 0)
    with_expense = int((row or {}).get("rows_with_expense") or 0)
    return with_expense == 0, {
        "reason": "expense_column_empty" if with_expense == 0 else "expense_present",
        "cost_centres_counted": rows_total,
        "cost_centres_with_expense": with_expense,
    }


async def record_cost_center_budget_gap(user: dict | None) -> bool:
    """Detect the budget gap and, if it is real, record it once for other agents.

    Attributed to the Finance monitor when that row exists in the workspace, and
    otherwise to the global Controller Financiero template, because the
    conversational agent is what answered the question that surfaced the gap.
    """
    verified = True
    try:
        gap_is_real, evidence = await _expense_column_is_empty(user)
    except HTTPException as exc:
        # No published head, no scope, no permission. The metric still cannot be
        # computed, so the gap is real, but the expense column was NOT inspected —
        # so the finding must not claim it was.
        verified = False
        gap_is_real = True
        evidence = {"reason": "dataset_unavailable", "detail": exc.detail}
    except (asyncpg.PostgresError, OSError) as exc:
        logger.warning(
            "agent memory hook: could not check the cost-centre expense dataset: %s",
            exc,
        )
        return False

    if not gap_is_real:
        return False

    return await agent_memory.record_finding(
        user,
        subject=COST_CENTER_BUDGET_SUBJECT,
        finding_type="data_gap",
        summary=(
            COST_CENTER_BUDGET_SUMMARY
            if verified
            else COST_CENTER_BUDGET_SUMMARY_UNVERIFIED
        ),
        agent_cartridge_id=FINANCE_MONITOR_CARTRIDGE,
        # The monitor row first, then the conversational template. The template is
        # a global seed row so it always exists; without it the write would be
        # silently dropped in any workspace the monitor seed never reached.
        agent_slug=(FINANCE_MONITOR_SLUG, FINANCE_CONVERSATIONAL_SLUG),
        severity="high",
        detail={
            "affected_metrics": [
                "finance.budget_vs_actual_by_cost_center",
                "risk.cost_center_overrun",
            ],
            "evidence": evidence,
        },
    )


async def cost_center_overrun_note(user: dict | None) -> str | None:
    """What Risk should say about cost-centre overrun, citing Finance's finding.

    Returns a business-Spanish note when another agent has already recorded the
    gap, and None when nobody has. Risk appends it to its own payload, so the LLM
    cites shared memory instead of rediscovering the limitation.
    """
    prior = await agent_memory.check_prior_findings(
        COST_CENTER_BUDGET_SUBJECT, user, limit=1
    )
    if not prior:
        return None
    finding = prior[0]
    author = finding.get("recorded_by") or finding.get("agent_name") or "otro agente"
    # Short on purpose. The public projection redacts any string longer than 64
    # word tokens to "[REDACTED]", so inlining the finding's full summary here
    # would destroy the whole note instead of shortening it. The citation points
    # at shared memory; the detail is one tool call away.
    return (
        "sobregiro por centro de costo no se puede calcular: "
        f"{author} ya registro ese hallazgo en la memoria compartida entre agentes"
    )


__all__ = (
    "COST_CENTER_BUDGET_SUBJECT",
    "COST_CENTER_BUDGET_SUMMARY",
    "COST_CENTER_BUDGET_SUMMARY_UNVERIFIED",
    "FINANCE_CONVERSATIONAL_SLUG",
    "COST_CENTER_EXPENSE_DATASET",
    "cost_center_overrun_note",
    "record_cost_center_budget_gap",
)
