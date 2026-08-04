from __future__ import annotations

from collections.abc import Callable, Iterable
import re
from typing import Any


_WIP_MARKERS = ("wip_mensual", "wip_resumen")
_NONCURRENT_SCHEMAS = ("knowledge_bits_history", "knowledge_bits_quarantine")
_READ_PARQUET = re.compile(r"read_parquet\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)
_SINGLE_LITERAL = re.compile(r"\s*'([^']+)'\s*\Z", re.DOTALL)


def _reserved_path_prefixes(markers: Iterable[str]) -> tuple[str, ...]:
    prefixes: set[str] = set()
    for marker in markers:
        clean = str(marker or "").strip().lower().strip("/")
        if "/" in clean:
            prefixes.add(clean.rsplit("/", 1)[0] + "/")
    return tuple(sorted(prefixes))


def _wip_query_reason(
    sql: str, *, markers: Iterable[str], current_uris: Iterable[str]
) -> str | None:
    lowered = str(sql or "").lower()
    calls = _READ_PARQUET.findall(str(sql or ""))
    current = {str(uri).strip() for uri in current_uris if str(uri).strip()}
    reserved = tuple(str(item).strip().lower() for item in markers if str(item).strip())
    prefixes = _reserved_path_prefixes(reserved)
    for argument in calls:
        literal = _SINGLE_LITERAL.fullmatch(argument)
        if literal:
            path = literal.group(1)
            if path in current:
                continue
            path_lower = path.lower()
            if any(item in path_lower for item in reserved) or any(
                prefix in path_lower for prefix in prefixes
            ):
                return "noncurrent_replicon_wip_artifact"
            continue
        argument_lower = argument.lower()
        if (
            not argument.strip()
            or any(item in argument_lower for item in reserved)
            or any(prefix in argument_lower for prefix in prefixes)
            or any(uri in argument for uri in current)
            or not _SINGLE_LITERAL.fullmatch(argument)
        ):
            return "noncurrent_replicon_wip_artifact"
    if not calls and any(item in lowered for item in reserved):
        return "noncurrent_replicon_wip_artifact"
    return None


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
    security_context: dict[str, Any] | None = None,
) -> str | None:
    if str(cartridge_id).lower() != "replicon":
        return None
    try:
        with connection_factory() as conn, conn.cursor() as cur:
            scope = security_context if isinstance(security_context, dict) else {}
            tenant = str(scope.get("tenant_id") or "")
            workspace = str(scope.get("workspace_id") or "")
            if tenant and workspace:
                cur.execute(
                    """SELECT cfg.pg_table, cfg.output_path,
                              CASE WHEN head.state='current'
                                         AND run.status='completed'
                                         AND run.artifact_status='current'
                                   THEN run.storage_uri END
                         FROM kb_config cfg
                         LEFT JOIN kb_materialization_heads head
                           ON head.cartridge_id=cfg.cartridge_id
                          AND head.kb_id=cfg.kb_id
                          AND head.tenant_id=%s AND head.workspace_id=%s
                         LEFT JOIN kb_runs run
                           ON run.run_id=head.current_run_id
                          AND run.tenant_id=head.tenant_id
                          AND run.workspace_id=head.workspace_id
                        WHERE cfg.cartridge_id='replicon'
                          AND cfg.kb_id IN ('kb_wip_mensual','kb_wip_resumen')""",
                    (tenant, workspace),
                )
            else:
                cur.execute(
                    """SELECT pg_table, output_path FROM kb_config
                        WHERE cartridge_id='replicon'
                          AND kb_id IN ('kb_wip_mensual','kb_wip_resumen')"""
                )
            rows = cur.fetchall()
    except Exception:
        return "replicon_wip_provenance_unavailable"
    markers = [value for row in rows for value in row[:2] if value]
    current_uris = [row[2] for row in rows if len(row) > 2 and row[2]]
    return _wip_query_reason(
        sql,
        markers=_WIP_MARKERS + tuple(markers),
        current_uris=current_uris,
    )


__all__ = (
    "replicon_artifact_block_reason",
    "replicon_generic_query_block_reason",
)
