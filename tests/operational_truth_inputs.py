from __future__ import annotations

import io
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq


def pnl_dataset() -> dict:
    return {
        "name": "pnl_mensual",
        "layer": "gold",
        "cartridge": "replicon",
        "sources": ["raw/replicon/pnl_mensual"],
        "sql_def": (
            "SELECT proyecto,project_name,mes,margen_bruto_usd,margen_bruto_pct,"
            "wip_usd,revenue_usd,revenue_manager FROM read_parquet("
            "'s3://lakehouse/raw/replicon/pnl_mensual/**/*.parquet')"
        ),
    }


def seed_pnl_input(stack, scope: dict[str, str], rows: list[tuple]) -> None:
    records = [
        {
            "proyecto": project,
            "project_name": name,
            "mes": date.fromisoformat(month),
            "margen_bruto_usd": margin,
            "margen_bruto_pct": margin,
            "wip_usd": 0.0,
            "revenue_usd": 100.0,
            "revenue_manager": f"RM {project[-1].upper()}",
        }
        for project, name, month, margin in rows
    ]
    payload = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(records), payload)
    key = (
        "raw/replicon/pnl_mensual/"
        f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
        f"load_date=2026-07-31/batch_id={scope['workspace_id']}/input.parquet"
    )
    stack.s3.put_object(Bucket="lakehouse", Key=key, Body=payload.getvalue())
