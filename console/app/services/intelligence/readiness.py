from __future__ import annotations

import os
import re
from typing import Any

import asyncpg

from app.services.intelligence.contracts import load_contracts
from app.services.intelligence.utils import allowed_cartridges


_SAFE_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _normalize_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _gold_dsn() -> str:
    return _normalize_dsn(os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL") or "")


def _operational_dsn() -> str:
    return _normalize_dsn(os.environ.get("DATABASE_URL") or "")


def _workspace_scope(user: dict | None) -> tuple[str | None, str | None]:
    if not user:
        return None, None
    tenant_id = user.get("active_tenant_id") or user.get("tenant_id")
    workspace_id = user.get("active_workspace_id") or user.get("workspace_id")
    return (str(tenant_id) if tenant_id else None), (str(workspace_id) if workspace_id else None)


async def _default_workspace_scope() -> tuple[str | None, str | None]:
    """Return a deterministic service-readiness scope when no user exists.

    Gold tables are protected with FORCE RLS, so service-level readiness probes
    must still set a tenant/workspace context before counting rows. This keeps
    readiness honest without granting the Gold role BYPASSRLS.
    """
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
        return str(row["tenant_id"]) if row["tenant_id"] else None, str(row["workspace_id"])
    except Exception:
        return None, None
    finally:
        await conn.close()


async def _effective_readiness_scope(user: dict | None) -> tuple[str | None, str | None]:
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
    """Return unique Gold datasets referenced by loaded intelligence contracts."""
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


async def _table_columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = $1
        """,
        table,
    )
    return {str(row["column_name"]) for row in rows}


async def _lineage_gold_counts(
    requirements: list[dict[str, Any]],
    user: dict | None,
    scope: tuple[str | None, str | None] | None = None,
) -> dict[str, int]:
    dsn = _operational_dsn()
    datasets = [str(req.get("dataset") or "") for req in requirements if req.get("dataset")]
    if not dsn or not datasets:
        return {}
    tenant_id, workspace_id = scope or _workspace_scope(user)
    conn = await asyncpg.connect(dsn, command_timeout=5)
    try:
        if workspace_id:
            scope_pattern = f"%tenant_id={tenant_id or ''}/workspace_id={workspace_id}/%"
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (silver_name)
                       silver_name,
                       COALESCE(row_count, 0)::bigint AS row_count
                  FROM silver_lineage
                 WHERE layer = 'gold'
                   AND silver_name = ANY($1::text[])
                   AND COALESCE(row_count, 0) > 0
                   AND storage_uri LIKE $2
                 ORDER BY silver_name, created_at DESC
                """,
                datasets,
                scope_pattern,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (silver_name)
                       silver_name,
                       COALESCE(row_count, 0)::bigint AS row_count
                  FROM silver_lineage
                 WHERE layer = 'gold'
                   AND silver_name = ANY($1::text[])
                   AND COALESCE(row_count, 0) > 0
                 ORDER BY silver_name, created_at DESC
                """,
                datasets,
            )
        return {str(row["silver_name"]): int(row["row_count"] or 0) for row in rows}
    except Exception:
        return {}
    finally:
        await conn.close()


async def _gold_counts(requirements: list[dict[str, Any]], user: dict | None) -> list[dict[str, Any]]:
    tenant_id, workspace_id = await _effective_readiness_scope(user)
    lineage_counts = await _lineage_gold_counts(requirements, user, (tenant_id, workspace_id))
    dsn = _gold_dsn()
    if not dsn:
        return [
            {
                **req,
                "status": "ready" if lineage_counts.get(str(req.get("dataset"))) else "unavailable",
                "row_count": lineage_counts.get(str(req.get("dataset")), 0),
                "reason": "" if lineage_counts.get(str(req.get("dataset"))) else "gold_database_url_missing",
                "source": "silver_lineage" if lineage_counts.get(str(req.get("dataset"))) else "pggold",
            }
            for req in requirements
        ]
    conn = await asyncpg.connect(dsn, command_timeout=5)
    try:
        results: list[dict[str, Any]] = []
        async with conn.transaction():
            if workspace_id:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                    tenant_id or "",
                    workspace_id,
                )
            for req in requirements:
                dataset = str(req["dataset"])
                table = str(req["table"])
                if not _SAFE_DATASET_RE.fullmatch(dataset):
                    results.append({**req, "status": "invalid", "row_count": 0, "reason": "invalid_dataset_name"})
                    continue
                exists = bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"))
                if not exists:
                    results.append({**req, "status": "missing", "row_count": 0, "reason": "gold_table_missing"})
                    continue
                columns = await _table_columns(conn, table)
                if workspace_id and {"tenant_id", "workspace_id"}.issubset(columns):
                    row_count = int(await conn.fetchval(
                        f'SELECT COUNT(*) FROM public."{table}" WHERE tenant_id::text = $1 AND workspace_id::text = $2',
                        tenant_id,
                        workspace_id,
                    ) or 0)
                    scoped = True
                else:
                    row_count = int(await conn.fetchval(f'SELECT COUNT(*) FROM public."{table}"') or 0)
                    scoped = False
                lineage_row_count = lineage_counts.get(dataset, 0)
                if row_count <= 0 and lineage_row_count > 0:
                    results.append({
                        **req,
                        "status": "ready",
                        "row_count": lineage_row_count,
                        "scoped": scoped,
                        "source": "silver_lineage",
                        "reason": "",
                    })
                    continue
                results.append({
                    **req,
                    "status": "ready" if row_count > 0 else "empty",
                    "row_count": row_count,
                    "scoped": scoped,
                    "source": "pggold",
                    "reason": "" if row_count > 0 else "gold_table_empty",
                })
        return results
    finally:
        await conn.close()


async def _signal_stats(user: dict | None) -> dict[str, Any]:
    dsn = _operational_dsn()
    if not dsn:
        return {"signal_count": 0, "last_signal_at": None, "error": "database_url_missing"}
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
            "last_signal_at": row["last_signal_at"].isoformat() if row and row["last_signal_at"] else None,
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
    missing = [
        row
        for row in dataset_rows
        if row.get("status") not in {"ready"}
    ]
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
