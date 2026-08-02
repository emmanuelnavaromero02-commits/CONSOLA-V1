from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def _text(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 500 or any(ord(char) < 32 for char in text):
        return ""
    return text


def _examples(value: object) -> list[object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [
        item for item in value[:20] if item is None or type(item) in {bool, int, float}
    ]


def _column(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": _text(row.get("description")),
        "tags": [
            str(tag)
            for tag in list(row.get("tags") or [])[:16]
            if _TAG.fullmatch(str(tag))
        ],
        "is_key": bool(row.get("is_key")),
        "is_metric": bool(row.get("is_metric")),
        "example_values": _examples(row.get("example_values")),
    }


def _relationship(row: dict[str, Any], dataset: str) -> dict[str, str] | None:
    values = {
        "from_dataset": dataset,
        "from_column": str(row.get("from_column") or ""),
        "to_dataset": str(row.get("to_dataset") or ""),
        "to_column": str(row.get("to_column") or ""),
        "join_hint": str(row.get("join_hint") or "").upper(),
        "description": _text(row.get("description")),
    }
    if not all(
        _IDENTIFIER.fullmatch(values[key])
        for key in ("from_dataset", "from_column", "to_dataset", "to_column")
    ):
        return None
    if values["join_hint"] not in {"", "INNER", "LEFT", "RIGHT", "FULL"}:
        values["join_hint"] = ""
    return values


def snapshot_public_semantics(
    connection_factory: Callable[[], Any] | None,
    *,
    tenant_id: str,
    workspace_id: str,
    dataset: dict[str, Any],
) -> dict[str, Any]:
    name = str(dataset.get("name") or "")
    columns: dict[str, dict[str, Any]] = {}
    relationships = [
        value
        for row in list(dataset.get("relationships") or [])
        if (value := _relationship(row, name)) is not None
    ]
    if connection_factory is not None:
        conn = connection_factory()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id',%s,true),"
                    "set_config('app.workspace_id',%s,true)",
                    (tenant_id, workspace_id),
                )
                cur.execute(
                    """SELECT column_name,description,example_values,tags,
                              is_key,is_metric
                         FROM data_catalog
                        WHERE tenant_id=%s::uuid AND workspace_id=%s::uuid
                          AND dataset=%s AND scope_status='scoped'""",
                    (tenant_id, workspace_id, name),
                )
                columns = {
                    _row[0]: _column(
                        dict(zip([item.name for item in cur.description], _row))
                    )
                    for _row in cur.fetchall()
                }
                cur.execute(
                    """SELECT from_column,to_dataset,to_column,join_hint,description
                         FROM data_relationships
                        WHERE tenant_id=%s::uuid AND workspace_id=%s::uuid
                          AND from_dataset=%s AND scope_status='scoped'""",
                    (tenant_id, workspace_id, name),
                )
                relationships = [
                    value
                    for row in cur.fetchall()
                    if (
                        value := _relationship(
                            dict(zip([item.name for item in cur.description], row)),
                            name,
                        )
                    )
                    is not None
                ]
        finally:
            conn.close()
    return {
        "columns": columns,
        "description": _text(dataset.get("description")),
        "relationships": relationships,
        "sources": list(dataset.get("sources") or []),
    }
