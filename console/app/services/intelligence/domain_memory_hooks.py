from __future__ import annotations

import logging
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.domains.agentops.finance_monitor import (
    FINANCE_MONITOR_CARTRIDGE,
    FINANCE_MONITOR_SLUG,
)

FINANCE_CONVERSATIONAL_SLUG = "sap_s4hana_controller_financiero"
from app.services.intelligence import agent_memory
from app.services.intelligence.domain_aggregate_support import (
    GOLD_SCOPE_PREDICATE,
    open_gold_scope,
    resolve_relation,
)

logger = logging.getLogger(__name__)

COST_CENTER_BUDGET_SUBJECT = "cost_center_budget"

COST_CENTER_EXPENSE_DATASET = "cost_center_expense"
_EXPENSE_REQUIRED = frozenset({"cost_center", "total_expense"})

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
    verified = True
    try:
        gap_is_real, evidence = await _expense_column_is_empty(user)
    except HTTPException as exc:
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
    prior = await agent_memory.check_prior_findings(
        COST_CENTER_BUDGET_SUBJECT, user, limit=1
    )
    if not prior:
        return None
    finding = prior[0]
    author = finding.get("recorded_by") or finding.get("agent_name") or "otro agente"
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
