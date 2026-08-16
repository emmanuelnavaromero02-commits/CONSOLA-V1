"""Surgical purge of stale generic-Gold signals (pre-#475 class).

F11: the engine is upsert-only and never deletes, so the garbage "signals"
written before #475 (identifier/structural columns or scope UUIDs presented
as KPIs) stayed frozen at status=open. This module is the REPRODUCIBLE purge
step: the same read predicate (``is_stale_generic_signal``) drives both the
dry-run report and the transactional delete, scoped per workspace with an
explicit ``WHERE workspace_id`` on top of RLS. The CLI wrapper lives in
``scripts/purge_stale_generic_signals.py``; the regression test in
``tests/test_stale_generic_signal_purge.py`` seeds both classes against a
real PostgreSQL and proves the stale class reaches zero while legitimate
signals survive.
"""

from __future__ import annotations

import asyncpg

from app.services.intelligence.gold_control_room import is_stale_generic_signal

# Tablas hijas ligadas por signal_id (evidence_items cuelga de evidence_packs por FK).
_CHILD_BY_SIGNAL = ("evidence_packs", "hypotheses", "decision_options", "prediction_outcomes")
_LIKE_GENERIC = r"metric LIKE 'generic\_%' ESCAPE '\'"

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



__all__ = (
    "process_workspace",
    "workspaces",
)


async def workspaces(conn: asyncpg.Connection) -> list[tuple[str | None, str]]:
    return await _workspaces(conn)
