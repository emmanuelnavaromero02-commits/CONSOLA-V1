#!/usr/bin/env python3
"""Rollback de la purga Fase A — re-inserta las filas del respaldo JSON.

Recupera EXACTAMENTE lo que la purga borró (respaldo generado antes de --apply).
Inserta en orden FK padres->hijas, casteando cada valor al tipo real de su columna
(uuid/jsonb/timestamptz/numeric…), con RLS por workspace e idempotente
(ON CONFLICT DO NOTHING). DRY-RUN por defecto; --apply para restaurar de verdad.

Uso:
  # descargar el respaldo desde S3 primero, p.ej.:
  #   aws s3 cp s3://modecissions-lakehouse-783792/backups/fase-a-purge/<ts>/<file>.json backup.json
  python scripts/restore_stale_generic_signals.py --backup backup.json          # dry-run
  python scripts/restore_stale_generic_signals.py --backup backup.json --apply   # restaura
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg  # noqa: E402

_RESTORE_ORDER = (
    "metric_baselines",
    "intelligence_signals",
    "control_room_items",
    "evidence_packs",
    "prediction_outcomes",
    "evidence_items",
    "hypotheses",
    "decision_options",
    "decision_intelligence_snapshots",
    "control_room_item_events",
    "action_runs",
    "control_room_action_executions",
)
_CAST = {"uuid": "uuid", "jsonb": "jsonb", "json": "json", "timestamptz": "timestamptz",
         "timestamp": "timestamp", "numeric": "numeric", "date": "date", "_int4": "int4[]",
         "_text": "text[]", "int8": "int8", "int4": "int4", "bool": "bool"}


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or ""
    if not raw:
        raise SystemExit("DATABASE_URL no definido")
    return raw.replace("postgresql+psycopg2://", "postgresql://")


async def _coltypes(conn: asyncpg.Connection, table: str) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT column_name, udt_name FROM information_schema.columns WHERE table_name=$1", table
    )
    return {r["column_name"]: r["udt_name"] for r in rows}


async def _insert_rows(conn, table, rows, coltypes, apply) -> int:
    if not rows:
        return 0
    if not apply:
        return len(rows)
    n = 0
    for row in rows:
        cols = list(row.keys())
        placeholders = []
        values = []
        for i, c in enumerate(cols, start=1):
            udt = coltypes.get(c, "")
            cast = _CAST.get(udt)
            placeholders.append(f"${i}::{cast}" if cast else f"${i}")
            values.append(row[c])
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) ON CONFLICT DO NOTHING"
        )
        await conn.execute(sql, *values)
        n += 1
    return n


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backup", required=True, help="ruta al JSON de respaldo")
    ap.add_argument("--apply", action="store_true", help="Ejecuta la restauración (default: dry-run)")
    args = ap.parse_args()

    backup = json.loads(open(args.backup, encoding="utf-8").read())
    conn = await asyncpg.connect(_dsn())
    try:
        who = await conn.fetchval("SELECT current_user")
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"=== restore Fase A — modo {mode} — rol={who} — backup {backup.get('created_at')} ===")
        totals: dict[str, int] = {}
        for ws, wd in backup["workspaces"].items():
            t = wd["tenant_id"]
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,false), set_config('app.workspace_id',$2,false)", t, ws
            )
            for table in _RESTORE_ORDER:
                rows = wd["tables"].get(table, [])
                if not rows:
                    continue
                ct = await _coltypes(conn, table)
                if args.apply:
                    async with conn.transaction():
                        n = await _insert_rows(conn, table, rows, ct, apply=True)
                else:
                    n = await _insert_rows(conn, table, rows, ct, apply=False)
                totals[table] = totals.get(table, 0) + n
            print(f"  ws {ws[:8]}: " + " ".join(f"{k}={len([r for r in wd['tables'].get(k, [])])}" for k in _RESTORE_ORDER if wd['tables'].get(k)))
        print(f"=== TOTAL {'restaurado' if args.apply else '(dry-run) a restaurar'}: "
              + ", ".join(f"{k}={v}" for k, v in totals.items()) + " ===")
        if not args.apply:
            print("(dry-run: re-ejecuta con --apply para restaurar)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
