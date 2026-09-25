from __future__ import annotations

import os
import re
from typing import Any

import asyncpg

from app.services.intelligence.contracts import load_contracts
from app.services.intelligence.utils import allowed_cartridges

_PUBLISHED_RELATION_RE = re.compile(
    r"^(omega_publication_gold\.run_[0-9a-f]{32}|public\.gold_[A-Za-z_][A-Za-z0-9_]{0,127})$"
)


def _qualified_published_relation(
    status: str, gold_table: str, *, relation_exists: bool
) -> str | None:
    if not relation_exists:
        return None
    if status == "published":
        relation = f"omega_publication_gold.{gold_table}"
    elif status == "legacy_unverified":
        relation = f"public.{gold_table}"
    else:
        return None
    return relation if _PUBLISHED_RELATION_RE.fullmatch(relation) else None


def _normalize_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _gold_dsn() -> str:
    return _normalize_dsn(
        os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    )


def _operational_dsn() -> str:
    return _normalize_dsn(os.environ.get("DATABASE_URL") or "")


def _workspace_scope(user: dict | None) -> tuple[str | None, str | None]:
    if not user:
        return None, None
    tenant_id = user.get("active_tenant_id") or user.get("tenant_id")
    workspace_id = user.get("active_workspace_id") or user.get("workspace_id")
    return (str(tenant_id) if tenant_id else None), (
        str(workspace_id) if workspace_id else None
    )


async def _default_workspace_scope() -> tuple[str | None, str | None]:
    dsn = _operational_dsn()
    if not dsn:
        return None, None
    conn = await asyncpg.connect(dsn, command_timeout=5)
    try:
        row = await conn.fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, id::text AS workspace_id
              FROM workspaces
             ORDER BY created_at ASC NULLS LAST, id ASC
             LIMIT 1
            """
        )
        if not row:
            return None, None
        return str(row["tenant_id"]) if row["tenant_id"] else None, str(
            row["workspace_id"]
        )
    except Exception:
        return None, None
    finally:
        await conn.close()


async def _effective_readiness_scope(
    user: dict | None,
) -> tuple[str | None, str | None]:
    tenant_id, workspace_id = _workspace_scope(user)
    if workspace_id:
        return tenant_id, workspace_id
    return await _default_workspace_scope()


def _contract_filter(user: dict | None) -> set[str] | None:
    if user:
        return allowed_cartridges(user)
    raw = os.environ.get("INTELLIGENCE_READINESS_CARTRIDGES", "hubspot,replicon")
    values = {part.strip() for part in raw.split(",") if part.strip()}
    return values or None


def required_datasets(user: dict | None = None) -> list[dict[str, Any]]:
    contracts = load_contracts(_contract_filter(user))
    grouped: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        cartridge = str(contract.get("cartridge") or "").strip()
        for metric in contract.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            dataset = str(metric.get("dataset") or "").strip()
            metric_id = str(metric.get("id") or "").strip()
            if not dataset:
                continue
            entry = grouped.setdefault(
                dataset,
                {
                    "dataset": dataset,
                    "table": f"gold_{dataset}",
                    "cartridges": [],
                    "metrics": [],
                },
            )
            if cartridge and cartridge not in entry["cartridges"]:
                entry["cartridges"].append(cartridge)
            if metric_id and metric_id not in entry["metrics"]:
                entry["metrics"].append(metric_id)
    return sorted(grouped.values(), key=lambda item: item["dataset"])


async def _gold_counts(
    requirements: list[dict[str, Any]], user: dict | None
) -> list[dict[str, Any]]:
    tenant_id, workspace_id = await _effective_readiness_scope(user)
    dsn = _gold_dsn()
    if not dsn or not tenant_id or not workspace_id:
        return [
            {
                **req,
                "status": "unavailable",
                "row_count": 0,
                "reason": "published_gold_scope_unavailable",
                "source": "publication_head",
            }
            for req in requirements
        ]
    conn = await asyncpg.connect(dsn, command_timeout=5)
    try:
        results: list[dict[str, Any]] = []
        async with conn.transaction():
            await conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            for req in requirements:
                dataset = str(req["dataset"])
                head = await conn.fetchrow(
                    """SELECT r.status, r.gold_table,
                              to_regclass(CASE
                                WHEN r.status='published' AND r.gold_table ~ '^run_[0-9a-f]{32}$'
                                  THEN 'omega_publication_gold.'||r.gold_table
                                WHEN r.status='legacy_unverified' AND r.gold_table ~ '^gold_[A-Za-z0-9_]+$'
                                  THEN 'public.'||r.gold_table END) IS NOT NULL AS relation_exists
                          FROM omega_publication.dataset_publication_heads h
                          JOIN omega_publication.materialization_runs r
                            ON r.materialization_run_id=h.materialization_run_id
                         WHERE h.tenant_id=$1 AND h.workspace_id=$2
                           AND h.dataset=$3 AND h.layer='gold'""",
                    tenant_id,
                    workspace_id,
                    dataset,
                )
                relation = (
                    _qualified_published_relation(
                        str(head["status"] or ""),
                        str(head["gold_table"] or ""),
                        relation_exists=bool(head["relation_exists"]),
                    )
                    if head
                    else None
                )
                if not relation:
                    results.append(
                        {
                            **req,
                            "status": "missing",
                            "row_count": 0,
                            "reason": "published_head_missing",
                            "source": "publication_head",
                        }
                    )
                    continue
                row_count = int(
                    await conn.fetchval(
                        f"SELECT COUNT(*) FROM {relation} "  # nosec B608
                        "WHERE tenant_id::text = $1 AND workspace_id::text = $2",
                        tenant_id,
                        workspace_id,
                    )
                    or 0
                )
                results.append(
                    {
                        **req,
                        "status": "ready" if row_count > 0 else "empty",
                        "row_count": row_count,
                        "scoped": True,
                        "source": "publication_head",
                        "reason": "" if row_count > 0 else "published_gold_empty",
                    }
                )
        return results
    finally:
        await conn.close()


async def _signal_stats(user: dict | None) -> dict[str, Any]:
    dsn = _operational_dsn()
    if not dsn:
        return {
            "signal_count": 0,
            "last_signal_at": None,
            "error": "database_url_missing",
        }
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        conn = await asyncpg.connect(dsn, command_timeout=5)
    except Exception as exc:  # noqa: BLE001
        return {"signal_count": 0, "last_signal_at": None, "error": type(exc).__name__}
    try:
        if workspace_id:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                    tenant_id or "",
                    workspace_id,
                )
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS count, MAX(updated_at) AS last_signal_at
                      FROM intelligence_signals
                     WHERE workspace_id = $1
                       AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
                    """,
                    workspace_id,
                    tenant_id,
                )
        else:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS count, MAX(updated_at) AS last_signal_at
                  FROM intelligence_signals
                """
            )
        return {
            "signal_count": int(row["count"] or 0) if row else 0,
            "last_signal_at": row["last_signal_at"].isoformat()
            if row and row["last_signal_at"]
            else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"signal_count": 0, "last_signal_at": None, "error": type(exc).__name__}
    finally:
        await conn.close()


async def intelligence_readiness(
    user: dict | None = None,
    *,
    require_data: bool = True,
) -> dict[str, Any]:
    requirements = required_datasets(user)
    if not require_data:
        return {
            "status": "up",
            "required": False,
            "contracts_loaded": len(load_contracts(_contract_filter(user))),
            "required_datasets": [str(row["dataset"]) for row in requirements],
            "missing_datasets": [],
            "datasets": [],
            "signal_count": 0,
            "last_signal_at": None,
            "reason": "data_check_not_required",
        }

    dataset_rows = await _gold_counts(requirements, user)
    missing = [row for row in dataset_rows if row.get("status") not in {"ready"}]
    stats = await _signal_stats(user)
    ok = not requirements or not missing
    if require_data and not requirements:
        ok = False
    status = "up" if ok else "degraded"
    return {
        "status": status,
        "required": require_data,
        "contracts_loaded": len(load_contracts(_contract_filter(user))),
        "required_datasets": [row["dataset"] for row in requirements],
        "missing_datasets": [row["dataset"] for row in missing],
        "datasets": dataset_rows,
        **stats,
        "reason": "" if ok else "intelligence_gold_datasets_missing_or_empty",
    }
