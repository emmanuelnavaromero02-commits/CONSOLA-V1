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
  * Rama entidad-UUID acotada al UUID de SCOPE (tenant/workspace): nunca barre
    una señal generic legítima cuya entidad de negocio sea un UUID no-scope.
  * Scoped por (tenant, workspace) con RLS (``set_config`` app.tenant_id/workspace_id).
    Ejecutar con rol RLS-bound (omega_console/omega_workspace), NUNCA postgres/
    superusuario (ignoraría RLS y podría fugar el scope); el script avisa si detecta
    un superusuario.
  * Transaccional por workspace (recolección + borrado en la misma transacción);
    idempotente (re-ejecutar no encuentra nada).
  * Cascada: las hijas por ``signal_id`` (evidence_*, hypotheses, decision_options,
    prediction_outcomes, metric_baselines) se borran a mano en orden hijas->padres;
    decision_intelligence_snapshots y control_room_item_events se limpian solas por
    FK ON DELETE CASCADE (verificado en prod) y se reportan para transparencia.

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


# Belt-and-suspenders: un ``WHERE workspace_id = $1`` EXPLÍCITO además de la RLS,
# para que un rol mal configurado (superusuario que ignora RLS) NUNCA barra otra
# workspace. ``$1`` = workspace del scope actual.
async def _garbage_signal_ids(conn: asyncpg.Connection, scope_ids: tuple) -> list[str]:
    rows = await conn.fetch(
        f"SELECT signal_id, metric, entity_id FROM intelligence_signals "
        f"WHERE workspace_id = $1::uuid AND {_LIKE_GENERIC}",
        scope_ids[1],
    )
    return [
        r["signal_id"]
        for r in rows
        if is_stale_generic_signal(r["metric"], r["entity_id"], scope_ids)
    ]


async def _garbage_item_ids(conn: asyncpg.Connection, scope_ids: tuple) -> list[str]:
    # Intencional: la clase basura del fallback generic-Gold se republica SIEMPRE
    # como item_kind='intelligence_signal'. Los agent_alert (monitores) no son de
    # esta clase; el filtro de lectura los cubre por robustez pero la purga no los
    # toca (verificado en prod: 0 agent_alert con anomaly_type generic_).
    rows = await conn.fetch(
        "SELECT item_id, anomaly_type, entity_id FROM control_room_items "
        "WHERE workspace_id = $1::uuid AND item_kind='intelligence_signal' "
        r"AND anomaly_type LIKE 'generic\_%' ESCAPE '\'",
        scope_ids[1],
    )
    return [
        r["item_id"]
        for r in rows
        if is_stale_generic_signal(r["anomaly_type"], r["entity_id"], scope_ids)
    ]


async def _garbage_baseline_ids(conn: asyncpg.Connection, scope_ids: tuple) -> list[int]:
    rows = await conn.fetch(
        f"SELECT id, metric, entity_id FROM metric_baselines "
        f"WHERE workspace_id = $1::uuid AND {_LIKE_GENERIC}",
        scope_ids[1],
    )
    return [
        r["id"]
        for r in rows
        if is_stale_generic_signal(r["metric"], r["entity_id"], scope_ids)
    ]


async def _count(conn: asyncpg.Connection, table: str, sids: list[str]) -> int:
    if not sids:
        return 0
    return int(
        await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE signal_id = ANY($1::text[])", sids
        )
    )


# Tablas que el DB borra AUTOMÁTICAMENTE por FK ON DELETE CASCADE (verificado en
# prod). Por signal_id -> intelligence_signals; por item_id -> control_room_items.
_CASCADE_BY_SIGNAL = ("decision_intelligence_snapshots",)
_CASCADE_BY_ITEM = ("control_room_item_events", "action_runs", "control_room_action_executions")


async def _cascade_counts(
    conn: asyncpg.Connection, sids: list[str], item_ids: list[str]
) -> dict[str, int]:
    """Cuenta las filas que cascadean por FK al borrar señales/items, para
    transparencia total del impacto (aunque el DB las borra solo)."""
    counts: dict[str, int] = {}
    for table in _CASCADE_BY_SIGNAL:
        counts[table] = (
            int(await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE signal_id = ANY($1::text[])", sids
            )) if sids else 0
        )
    for table in _CASCADE_BY_ITEM:
        counts[table] = (
            int(await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE item_id = ANY($1::text[])", item_ids
            )) if item_ids else 0
        )
    return counts


async def _delete_garbage(
    conn: asyncpg.Connection, sids: list[str], item_ids: list[str], baseline_ids: list[int]
) -> None:
    """Borra hijas-por-signal_id, baselines, luego las SEÑALES y por último los ITEMS.

    Orden crítico: intelligence_signals se borra ANTES que control_room_items. Así
    decision_intelligence_snapshots cae por su FK (workspace_id, signal_id) ON DELETE
    CASCADE y desaparece primero; si se borraran los items antes, la FK COMPUESTA
    (workspace_id, control_room_item_id) -> control_room_items ON DELETE SET NULL
    pondría NULL en workspace_id (NOT NULL) de las snapshots aún vivas y violaría la
    constraint. control_room_item_events cascadea igual al borrar los items.
    """
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
    if sids:
        # PRIMERO las señales: decision_intelligence_snapshots cascadea por
        # (workspace_id, signal_id) ON DELETE CASCADE y se elimina antes de tocar
        # los items -> evita el SET NULL de la FK compuesta sobre workspace_id.
        await conn.execute(
            "DELETE FROM intelligence_signals WHERE signal_id = ANY($1::text[])", sids
        )
    if item_ids:
        # Ya sin snapshots que referencien estos items; control_room_item_events
        # cascadea por FK ON DELETE CASCADE.
        await conn.execute(
            "DELETE FROM control_room_items WHERE item_id = ANY($1::text[])", item_ids
        )


async def process_workspace(
    conn: asyncpg.Connection, tenant: str | None, ws: str, *, apply: bool
) -> dict:
    await _set_scope(conn, tenant, ws)
    scope_ids = (tenant, ws)

    async def _collect() -> tuple[list[str], list[str], list[int]]:
        return (
            await _garbage_signal_ids(conn, scope_ids),
            await _garbage_item_ids(conn, scope_ids),
            await _garbage_baseline_ids(conn, scope_ids),
        )

    if apply:
        # Recolectar Y borrar dentro de la MISMA transacción: cierra la ventana de
        # carrera con el motor (aunque #475 ya no genera basura nueva).
        async with conn.transaction():
            sids, item_ids, baseline_ids = await _collect()
            child_counts = {t: await _count(conn, t, sids) for t in _CHILD_BY_SIGNAL}
            cascade = await _cascade_counts(conn, sids, item_ids)
            await _delete_garbage(conn, sids, item_ids, baseline_ids)
        action = "PURGADO (transacción aplicada)"
    else:
        sids, item_ids, baseline_ids = await _collect()
        child_counts = {t: await _count(conn, t, sids) for t in _CHILD_BY_SIGNAL}
        cascade = await _cascade_counts(conn, sids, item_ids)
        action = (
            "nada que purgar"
            if not (sids or item_ids or baseline_ids)
            else "DRY-RUN (no se borró nada)"
        )

    return {
        "workspace": ws,
        "signals": len(sids),
        "control_room_items": len(item_ids),
        "metric_baselines": len(baseline_ids),
        **{f"child_{t}": child_counts[t] for t in _CHILD_BY_SIGNAL},
        "cascade": cascade,
        "action": action,
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Ejecuta el borrado (default: dry-run)")
    ap.add_argument("--workspace", action="append", default=[], help="workspace_id (repetible)")
    ap.add_argument("--tenant", default=None, help="tenant_id (con --workspace)")
    ap.add_argument(
        "--force-superuser",
        action="store_true",
        help="Permite --apply aun si el rol es superusuario (RLS ignorada). NO recomendado.",
    )
    args = ap.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        who = await conn.fetchval("SELECT current_user")
        is_super = await conn.fetchval("SELECT usesuper FROM pg_user WHERE usename = current_user")
        # Guard duro: un superusuario ignora RLS; con --apply podría barrer basura
        # de TODAS las workspaces en la primera iteración. El WHERE workspace_id
        # explícito ya lo acota, pero abortamos salvo override explícito.
        if is_super and args.apply and not args.force_superuser:
            raise SystemExit(
                f"ABORTADO: el rol '{who}' es SUPERUSUARIO (ignora RLS). Ejecuta con un rol "
                "RLS-bound (omega_console/omega_workspace) o pasa --force-superuser si lo asumes."
            )
        if args.workspace:
            targets = [(args.tenant, w) for w in args.workspace]
        else:
            targets = await _workspaces(conn)
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"=== purga señales generic stale — modo {mode} — rol={who} — {len(targets)} workspace(s) ===")
        if is_super:
            print("!! ADVERTENCIA: rol SUPERUSUARIO — RLS ignorada; el WHERE workspace_id explícito es el único freno.")
        cascade_tables = _CASCADE_BY_SIGNAL + _CASCADE_BY_ITEM
        totals = {"signals": 0, "control_room_items": 0, "metric_baselines": 0}
        cascade_totals = {t: 0 for t in cascade_tables}
        for tenant, ws in targets:
            rep = await process_workspace(conn, tenant, ws, apply=args.apply)
            if rep["signals"] or rep["control_room_items"] or rep["metric_baselines"]:
                casc = "/".join(f"{t.split('_')[-1]}={rep['cascade'][t]}" for t in cascade_tables)
                print(
                    f"  ws {ws[:8]}: signals={rep['signals']} items={rep['control_room_items']} "
                    f"baselines={rep['metric_baselines']} "
                    f"hijas(ev/hyp/opt/pred)={rep['child_evidence_packs']}/{rep['child_hypotheses']}/"
                    f"{rep['child_decision_options']}/{rep['child_prediction_outcomes']} "
                    f"cascada-FK[{casc}] -> {rep['action']}"
                )
                for k in totals:
                    totals[k] += rep[k]
                for t in cascade_tables:
                    cascade_totals[t] += rep["cascade"][t]
        casc_tot = " ".join(f"{t}={cascade_totals[t]}" for t in cascade_tables)
        print(
            f"=== TOTAL: signals={totals['signals']} items={totals['control_room_items']} "
            f"baselines={totals['metric_baselines']} | cascada-FK: {casc_tot} ==="
        )
        if not args.apply and any(totals.values()):
            print("(dry-run: re-ejecuta con --apply para borrar)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
