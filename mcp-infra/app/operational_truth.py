from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


_WIP_MARKERS = ("wip_mensual", "wip_resumen")
_NONCURRENT_SCHEMAS = ("knowledge_bits_history", "knowledge_bits_quarantine")


def replicon_artifact_block_reason(
    sql: str,
    *,
    cartridge_id: str | None = None,
    postgres: bool = False,
    reserved_markers: Iterable[str] = (),
) -> str | None:
    lowered = str(sql or "").lower()
    if cartridge_id is not None and str(cartridge_id).lower() != "replicon":
        return None
    if any(schema in lowered for schema in _NONCURRENT_SCHEMAS):
        return "noncurrent_replicon_wip_artifact"
    markers = _WIP_MARKERS + tuple(
        str(marker).strip().lower()
        for marker in reserved_markers
        if str(marker).strip()
    )
    if not any(marker in lowered for marker in markers):
        return None
    if postgres:
        return None
    return "noncurrent_replicon_wip_artifact"


def replicon_generic_query_block_reason(
    sql: str,
    *,
    cartridge_id: str,
    connection_factory: Callable[..., Any],
) -> str | None:
    if str(cartridge_id).lower() != "replicon":
        return None
    try:
        with connection_factory() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT pg_table, output_path FROM kb_config
                    WHERE cartridge_id='replicon'
                      AND kb_id IN ('kb_wip_mensual', 'kb_wip_resumen')"""
            )
            markers = [value for row in cur.fetchall() for value in row if value]
    except Exception:
        return "replicon_wip_provenance_unavailable"
    return replicon_artifact_block_reason(
        sql, cartridge_id=cartridge_id, reserved_markers=markers
    )


__all__ = (
    "replicon_artifact_block_reason",
    "replicon_generic_query_block_reason",
)
