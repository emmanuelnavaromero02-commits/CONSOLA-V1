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

from app.services.intelligence.stale_signal_purge import (  # noqa: E402
    _CASCADE_BY_ITEM,
    _CASCADE_BY_SIGNAL,
    _CHILD_BY_SIGNAL,
    _workspaces,
    process_workspace,
)


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("OMEGA_DATABASE_URL") or ""
    if not raw:
        raise SystemExit("DATABASE_URL no definido")
    return raw.replace("postgresql+psycopg2://", "postgresql://")


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
