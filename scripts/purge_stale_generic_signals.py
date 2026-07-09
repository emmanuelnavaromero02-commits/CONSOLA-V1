#!/usr/bin/env python3
"""Fase A — purga quirúrgica de las señales generic-Gold *stale* (clase pre-#475).

Contexto: antes de #475 el fallback genérico de Gold escribía "señales" tratando
columnas identificadoras/estructurales (``user_id``, ``department_id``,
``display_order`` …) o el UUID de scope como si fueran KPIs. #475 (v1.45.152)
detiene la GENERACIÓN de esa basura, pero el motor es *upsert-only* y nunca borra,
así que las filas viejas quedaron congeladas (``status=open``) y se muestran en la
Zona de Decisiones. Este script las elimina de forma quirúrgica.

Seguridad:
  * DRY-RUN por defecto: sin ``--apply`` NO borra nada; sólo reporta el impacto exacto.
  * Usa el MISMO predicado de lectura (``is_stale_generic_signal``) — nunca toca
    señales legítimas (``control_origin='sap_successfactors_talent_signal'`` u otras)
    ni señales genéricas legítimas futuras (KPI real con serie temporal).
  * Scoped por (tenant, workspace) con RLS (``set_config`` app.tenant_id/workspace_id).
  * Transaccional por workspace; idempotente (re-ejecutar no encuentra nada).
  * Cascada explícita: las tablas hijas cuelgan de ``signal_id`` (no hay FK a
    intelligence_signals), por eso se borran a mano en orden.

Uso:
  python scripts/purge_stale_generic_signals.py                 # dry-run, todos los workspaces
  python scripts/purge_stale_generic_signals.py --workspace <uuid> --tenant <uuid>
  python scripts/purge_stale_generic_signals.py --apply         # EJECUTA el borrado
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "console"))

import asyncpg  # noqa: E402

from app.services.intelligence.gold_control_room import (  # noqa: E402
    is_stale_generic_signal,
)

# Tablas hijas ligadas por signal_id (evidence_items cuelga de evidence_packs por FK).
_CHILD_BY_SIGNAL = ("evidence_packs", "hypotheses", "decision_options", "prediction_outcomes")
_LIKE_GENERIC = r"metric LIKE 'generic\_%' ESCAPE '\'"


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("OMEGA_DATABASE_URL") or ""
    if not raw:
        raise SystemExit("DATABASE_URL no definido")
    return raw.replace("postgresql+psycopg2://", "postgresql://")


async def _set_scope(conn: asyncpg.Connection, tenant: str | None, ws: str) -> None:
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, false), set_config('app.workspace_id', $2, false)",
        str(tenant or ""),
        str(ws),
    )


async def _workspaces(conn: asyncpg.Connection) -> list[tuple[str | None, str]]:
    rows = await conn.fetch("SELECT id::text AS ws, tenant_id::text AS tenant FROM workspaces")
    return [(r["tenant"], r["ws"]) for r in rows]


async def _garbage_signal_ids(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        f"SELECT signal_id, metric, entity_id FROM intelligence_signals WHERE {_LIKE_GENERIC}"
    )
    return [
        r["signal_id"]
        for r in rows
        if is_stale_generic_signal(r["metric"], r["entity_id"])
    ]


async def _garbage_item_ids(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT item_id, anomaly_type, entity_id "
        r"FROM control_room_items WHERE item_kind='intelligence_signal' "
        r"AND anomaly_type LIKE 'generic\_%' ESCAPE '\'"
    )
    return [
        r["item_id"]
        for r in rows
        if is_stale_generic_signal(r["anomaly_type"], r["entity_id"])
    ]


async def _garbage_baseline_ids(conn: asyncpg.Connection) -> list[int]:
    rows = await conn.fetch(
        f"SELECT id, metric, entity_id FROM metric_baselines WHERE {_LIKE_GENERIC}"
    )
    return [
        r["id"]
        for r in rows
        if is_stale_generic_signal(r["metric"], r["entity_id"])
    ]


async def _count(conn: asyncpg.Connection, table: str, sids: list[str]) -> int:
    if not sids:
        return 0
    return int(
        await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE signal_id = ANY($1::text[])", sids
        )
    )


async def process_workspace(
    conn: asyncpg.Connection, tenant: str | None, ws: str, *, apply: bool
) -> dict:
    await _set_scope(conn, tenant, ws)
    sids = await _garbage_signal_ids(conn)
    item_ids = await _garbage_item_ids(conn)
    baseline_ids = await _garbage_baseline_ids(conn)
    child_counts = {t: await _count(conn, t, sids) for t in _CHILD_BY_SIGNAL}
    report = {
        "workspace": ws,
        "signals": len(sids),
        "control_room_items": len(item_ids),
        "metric_baselines": len(baseline_ids),
        **{f"child_{t}": child_counts[t] for t in _CHILD_BY_SIGNAL},
    }
    if not sids and not item_ids and not baseline_ids:
        report["action"] = "nada que purgar"
        return report
    if not apply:
        report["action"] = "DRY-RUN (no se borró nada)"
        return report

    async with conn.transaction():
        if sids:
            await conn.execute(
                "DELETE FROM evidence_items WHERE evidence_pack_id IN "
                "(SELECT id FROM evidence_packs WHERE signal_id = ANY($1::text[]))",
                sids,
            )
            for table in _CHILD_BY_SIGNAL:
                await conn.execute(
                    f"DELETE FROM {table} WHERE signal_id = ANY($1::text[])", sids
                )
        if baseline_ids:
            await conn.execute(
                "DELETE FROM metric_baselines WHERE id = ANY($1::bigint[])", baseline_ids
            )
        if item_ids:
            await conn.execute(
                "DELETE FROM control_room_items WHERE item_id = ANY($1::text[])", item_ids
            )
        if sids:
            await conn.execute(
                "DELETE FROM intelligence_signals WHERE signal_id = ANY($1::text[])", sids
            )
    report["action"] = "PURGADO (transacción aplicada)"
    return report


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Ejecuta el borrado (default: dry-run)")
    ap.add_argument("--workspace", action="append", default=[], help="workspace_id (repetible)")
    ap.add_argument("--tenant", default=None, help="tenant_id (con --workspace)")
    args = ap.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        if args.workspace:
            targets = [(args.tenant, w) for w in args.workspace]
        else:
            targets = await _workspaces(conn)
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"=== purga señales generic stale — modo {mode} — {len(targets)} workspace(s) ===")
        totals = {"signals": 0, "control_room_items": 0, "metric_baselines": 0}
        for tenant, ws in targets:
            rep = await process_workspace(conn, tenant, ws, apply=args.apply)
            if rep["signals"] or rep["control_room_items"] or rep["metric_baselines"]:
                print(
                    f"  ws {ws[:8]}: signals={rep['signals']} items={rep['control_room_items']} "
                    f"baselines={rep['metric_baselines']} "
                    f"hijas(ev/hyp/opt/pred)={rep['child_evidence_packs']}/{rep['child_hypotheses']}/"
                    f"{rep['child_decision_options']}/{rep['child_prediction_outcomes']} -> {rep['action']}"
                )
                for k in totals:
                    totals[k] += rep[k]
        print(f"=== TOTAL: signals={totals['signals']} items={totals['control_room_items']} baselines={totals['metric_baselines']} ===")
        if not args.apply and any(totals.values()):
            print("(dry-run: re-ejecuta con --apply para borrar)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
