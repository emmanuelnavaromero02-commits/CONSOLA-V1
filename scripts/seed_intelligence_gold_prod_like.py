#!/usr/bin/env python3
"""Seed prod-like Gold datasets for the operational intelligence demo.

This is a local/prod-like fixture, not provider data. It creates the four
Gold tables used by the default intelligence readiness gate and scopes every
row to one tenant/workspace so cross-tenant tests can assert isolation.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import psycopg2
from psycopg2 import sql


DATASETS: dict[str, dict[str, Any]] = {
    "forecast_mensual": {
        "columns": [
            ("owner_id", "TEXT"),
            ("vendedor", "TEXT"),
            ("mes", "DATE"),
            ("forecast_ponderado_usd", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("seller-001", "Sofia Reyes", date(2026, 1, 1), 118000),
            ("seller-001", "Sofia Reyes", date(2026, 2, 1), 121000),
            ("seller-001", "Sofia Reyes", date(2026, 3, 1), 124000),
            ("seller-001", "Sofia Reyes", date(2026, 4, 1), 181000),
            ("seller-002", "Diego Luna", date(2026, 1, 1), 92000),
            ("seller-002", "Diego Luna", date(2026, 2, 1), 94000),
            ("seller-002", "Diego Luna", date(2026, 3, 1), 91000),
            ("seller-002", "Diego Luna", date(2026, 4, 1), 67000),
        ],
    },
    "deals_estancados": {
        "columns": [
            ("deal_id", "TEXT"),
            ("dealname", "TEXT"),
            ("fecha_cierre_esperada", "DATE"),
            ("monto_usd", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("deal-101", "ERP rollout norte", date(2026, 1, 20), 26000),
            ("deal-101", "ERP rollout norte", date(2026, 2, 20), 28000),
            ("deal-101", "ERP rollout norte", date(2026, 3, 20), 29000),
            ("deal-101", "ERP rollout norte", date(2026, 4, 20), 52000),
            ("deal-202", "Analytics renewal", date(2026, 1, 18), 34000),
            ("deal-202", "Analytics renewal", date(2026, 2, 18), 31000),
            ("deal-202", "Analytics renewal", date(2026, 3, 18), 32000),
            ("deal-202", "Analytics renewal", date(2026, 4, 18), 17000),
        ],
    },
    "pnl_mensual": {
        "columns": [
            ("proyecto", "TEXT"),
            ("project_name", "TEXT"),
            ("mes", "DATE"),
            ("margen_bruto_usd", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("proj-alpha", "Retail optimization", date(2026, 1, 1), 54000),
            ("proj-alpha", "Retail optimization", date(2026, 2, 1), 56000),
            ("proj-alpha", "Retail optimization", date(2026, 3, 1), 55000),
            ("proj-alpha", "Retail optimization", date(2026, 4, 1), 33000),
            ("proj-beta", "People analytics", date(2026, 1, 1), 31000),
            ("proj-beta", "People analytics", date(2026, 2, 1), 33000),
            ("proj-beta", "People analytics", date(2026, 3, 1), 35000),
            ("proj-beta", "People analytics", date(2026, 4, 1), 52000),
        ],
    },
    "consultor_timesheet_semanal": {
        "columns": [
            ("consultor", "TEXT"),
            ("semana", "DATE"),
            ("horas_facturables", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("Andrea Morales", date(2026, 4, 6), 32),
            ("Andrea Morales", date(2026, 4, 13), 34),
            ("Andrea Morales", date(2026, 4, 20), 33),
            ("Andrea Morales", date(2026, 4, 27), 21),
            ("Mateo Cruz", date(2026, 4, 6), 28),
            ("Mateo Cruz", date(2026, 4, 13), 29),
            ("Mateo Cruz", date(2026, 4, 20), 30),
            ("Mateo Cruz", date(2026, 4, 27), 43),
        ],
    },
    "salesforce_pipeline_forecast": {
        "columns": [
            ("owner_id", "TEXT"),
            ("vendedor", "TEXT"),
            ("mes", "DATE"),
            ("forecast_ponderado_usd", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("sf-seller-001", "Sofia Reyes", date(2026, 1, 1), 142000),
            ("sf-seller-001", "Sofia Reyes", date(2026, 2, 1), 148000),
            ("sf-seller-001", "Sofia Reyes", date(2026, 3, 1), 151000),
            ("sf-seller-001", "Sofia Reyes", date(2026, 4, 1), 226000),
            ("sf-seller-002", "Diego Luna", date(2026, 1, 1), 98000),
            ("sf-seller-002", "Diego Luna", date(2026, 2, 1), 101000),
            ("sf-seller-002", "Diego Luna", date(2026, 3, 1), 99000),
            ("sf-seller-002", "Diego Luna", date(2026, 4, 1), 71000),
        ],
    },
    "salesforce_forecast_vs_capacidad": {
        "columns": [
            ("mes", "DATE"),
            ("holgura_horas", "NUMERIC(18,4)"),
        ],
        "rows": [
            (date(2026, 1, 1), 420),
            (date(2026, 2, 1), 390),
            (date(2026, 3, 1), 360),
            (date(2026, 4, 1), 185),
        ],
    },
    "absence_by_type_and_month": {
        "columns": [
            ("absence_type", "TEXT"),
            ("absence_month", "DATE"),
            ("total_days_workable", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("sick_leave", date(2026, 1, 1), 18),
            ("sick_leave", date(2026, 2, 1), 20),
            ("sick_leave", date(2026, 3, 1), 19),
            ("sick_leave", date(2026, 4, 1), 35),
            ("vacation", date(2026, 1, 1), 28),
            ("vacation", date(2026, 2, 1), 27),
            ("vacation", date(2026, 3, 1), 30),
            ("vacation", date(2026, 4, 1), 21),
        ],
    },
    "headcount_by_department": {
        "columns": [
            ("org_id", "TEXT"),
            ("org_name", "TEXT"),
            ("snapshot_month", "DATE"),
            ("headcount", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("dept-delivery", "Delivery", date(2026, 1, 1), 122),
            ("dept-delivery", "Delivery", date(2026, 2, 1), 125),
            ("dept-delivery", "Delivery", date(2026, 3, 1), 124),
            ("dept-delivery", "Delivery", date(2026, 4, 1), 103),
            ("dept-sales", "Sales", date(2026, 1, 1), 44),
            ("dept-sales", "Sales", date(2026, 2, 1), 45),
            ("dept-sales", "Sales", date(2026, 3, 1), 46),
            ("dept-sales", "Sales", date(2026, 4, 1), 58),
        ],
    },
    "workforce_cost_monthly": {
        "columns": [
            ("cost_center", "TEXT"),
            ("cost_month", "DATE"),
            ("active_headcount", "NUMERIC(18,4)"),
        ],
        "rows": [
            ("cc-delivery", date(2026, 1, 1), 86),
            ("cc-delivery", date(2026, 2, 1), 88),
            ("cc-delivery", date(2026, 3, 1), 87),
            ("cc-delivery", date(2026, 4, 1), 67),
            ("cc-growth", date(2026, 1, 1), 28),
            ("cc-growth", date(2026, 2, 1), 29),
            ("cc-growth", date(2026, 3, 1), 30),
            ("cc-growth", date(2026, 4, 1), 43),
        ],
    },
}


def _normalize_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _operational_conn():
    dsn = _normalize_dsn(os.environ.get("DATABASE_URL", ""))
    if not dsn:
        raise SystemExit("DATABASE_URL is required to resolve tenant/workspace")
    return psycopg2.connect(dsn)


def _gold_conn():
    dsn = _normalize_dsn(os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL", ""))
    if not dsn:
        raise SystemExit("GOLD_DATABASE_URL or DATABASE_URL is required")
    return psycopg2.connect(dsn)


def _resolve_scope() -> tuple[str | None, str]:
    tenant = os.environ.get("OMEGA_SEED_TENANT_ID", "").strip() or None
    workspace = os.environ.get("OMEGA_SEED_WORKSPACE_ID", "").strip() or None
    if workspace:
        return tenant, workspace
    conn = _operational_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tenant_id::text, id::text
                  FROM workspaces
                 ORDER BY created_at ASC NULLS LAST, id ASC
                 LIMIT 1
                """
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit("No workspace found. Set OMEGA_SEED_WORKSPACE_ID explicitly.")
    return row[0], row[1]


def _create_table(cur, table_name: str, columns: list[tuple[str, str]]) -> None:
    column_sql = [
        sql.SQL("tenant_id UUID"),
        sql.SQL("workspace_id UUID NOT NULL"),
        *[
            sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(kind))
            for name, kind in columns
        ],
    ]
    cur.execute(
        sql.SQL("CREATE TABLE IF NOT EXISTS public.{} ({})").format(
            sql.Identifier(table_name),
            sql.SQL(", ").join(column_sql),
        )
    )
    cur.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON public.{} (workspace_id)").format(
            sql.Identifier(f"{table_name}_workspace_idx"),
            sql.Identifier(table_name),
        )
    )


def _seed_dataset(cur, dataset: str, payload: dict[str, Any], tenant: str | None, workspace: str) -> int:
    table_name = f"gold_{dataset}"
    columns = payload["columns"]
    _create_table(cur, table_name, columns)
    cur.execute(
        sql.SQL("DELETE FROM public.{} WHERE workspace_id = %s AND (%s::uuid IS NULL OR tenant_id = %s::uuid)").format(
            sql.Identifier(table_name)
        ),
        (workspace, tenant, tenant),
    )
    target_columns = ["tenant_id", "workspace_id", *[name for name, _ in columns]]
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in target_columns)
    query = sql.SQL("INSERT INTO public.{} ({}) VALUES ({})").format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(name) for name in target_columns),
        placeholders,
    )
    rows = [(tenant, workspace, *row) for row in payload["rows"]]
    cur.executemany(query, rows)
    return len(rows)


def main() -> None:
    tenant, workspace = _resolve_scope()
    conn = _gold_conn()
    try:
        with conn.cursor() as cur:
            total = 0
            for dataset, payload in DATASETS.items():
                total += _seed_dataset(cur, dataset, payload, tenant, workspace)
        conn.commit()
    finally:
        conn.close()
    print(f"seeded {total} intelligence Gold rows for workspace={workspace} tenant={tenant or 'null'}")


if __name__ == "__main__":
    main()
