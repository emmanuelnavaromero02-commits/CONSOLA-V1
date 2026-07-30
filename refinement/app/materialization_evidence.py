"""Atomic, scoped evidence persistence for materialized datasets."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any


def _required_scope(scope: dict[str, Any]) -> tuple[str, str]:
    try:
        tenant_id = str(uuid.UUID(str(scope.get("tenant_id") or "")))
        workspace_id = str(uuid.UUID(str(scope.get("workspace_id") or "")))
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("materialization evidence unavailable") from exc
    return tenant_id, workspace_id


def _write_lineage(cur: Any, scope: tuple[str, str], lineage: dict[str, Any]) -> None:
    tenant_id, workspace_id = scope
    cur.execute(
        """
        INSERT INTO silver_lineage (
            silver_name, cartridge_id, source_entity, source_load_date,
            source_batch_id, sql_def, column_mapping, layer, row_count,
            storage_uri, tenant_id, workspace_id, scope_status
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::uuid,%s::uuid,'scoped')
        """,
        (
            lineage["silver_name"],
            lineage["cartridge_id"],
            lineage["source_entity"],
            lineage.get("source_load_date"),
            lineage.get("source_batch_id"),
            lineage["sql_def"],
            json.dumps(lineage.get("column_mapping") or {}, sort_keys=True),
            lineage["layer"],
            int(lineage["row_count"]),
            lineage["storage_uri"],
            tenant_id,
            workspace_id,
        ),
    )


def _write_catalog(cur: Any, scope: tuple[str, str], catalog: dict[str, Any]) -> None:
    tenant_id, workspace_id = scope
    fields = catalog.get("schema_fields")
    if not isinstance(fields, list) or not fields:
        raise RuntimeError("materialization evidence unavailable")
    mapping = catalog.get("column_mapping") or {}
    for field in fields:
        column = str(field.get("name") or "").strip()
        data_type = str(field.get("type") or "").strip()
        if not column or not data_type:
            raise RuntimeError("materialization evidence unavailable")
        cur.execute(
            """
            INSERT INTO data_catalog (
                dataset, layer, cartridge, column_name, data_type, description,
                tenant_id, workspace_id, scope_status, updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s::uuid,%s::uuid,'scoped',NOW())
            ON CONFLICT (workspace_id, dataset, column_name)
                WHERE workspace_id IS NOT NULL
            DO UPDATE SET
                data_type = EXCLUDED.data_type,
                layer = EXCLUDED.layer,
                cartridge = EXCLUDED.cartridge,
                description = CASE
                    WHEN EXCLUDED.description <> '' THEN EXCLUDED.description
                    ELSE data_catalog.description
                END,
                tenant_id = EXCLUDED.tenant_id,
                scope_status = 'scoped',
                updated_at = NOW()
            """,
            (
                catalog["name"],
                catalog["layer"],
                catalog["cartridge"],
                column,
                data_type,
                str(mapping.get(column) or ""),
                tenant_id,
                workspace_id,
            ),
        )


def persist_materialization_evidence(
    connection_factory: Callable[[], Any],
    *,
    scope: dict[str, Any],
    lineage: dict[str, Any],
    catalog: dict[str, Any],
) -> None:
    """Commit lineage and catalog together or expose one sanitized failure."""
    scoped = _required_scope(scope)
    connection = connection_factory()
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.workspace_id', %s, true)",
                scoped,
            )
            _write_lineage(cur, scoped, lineage)
            _write_catalog(cur, scoped, catalog)
        connection.commit()
    except Exception as exc:  # noqa: BLE001 - DB boundary is sanitized
        connection.rollback()
        raise RuntimeError("materialization evidence unavailable") from exc
    finally:
        connection.close()


__all__ = ["persist_materialization_evidence"]
