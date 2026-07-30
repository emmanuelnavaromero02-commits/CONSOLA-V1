"""Refinement engine that makes lineage and catalog part of success."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

try:
    from app.duckdb_engine import DuckDBEngine, validate_safe_identifier
    from app.materialization_evidence import persist_materialization_evidence
except ModuleNotFoundError:  # local tests import refinement.app.*
    from refinement.app.duckdb_engine import DuckDBEngine, validate_safe_identifier
    from refinement.app.materialization_evidence import persist_materialization_evidence


_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar(
    "materialization_scope", default=None
)
_LINEAGE: ContextVar[dict[str, Any] | None] = ContextVar(
    "materialization_lineage", default=None
)


class OperationalDuckDBEngine(DuckDBEngine):
    """Require scoped, atomic lineage/catalog evidence for every success."""

    def materialize(self, ds: dict, user_context: dict | None = None) -> dict:
        scope_token = _SCOPE.set(dict(user_context or {}))
        lineage_token = _LINEAGE.set(None)
        try:
            result = super().materialize(ds, user_context)
            if _LINEAGE.get() is not None:
                raise RuntimeError("materialization evidence unavailable")
            return result
        finally:
            _LINEAGE.reset(lineage_token)
            _SCOPE.reset(scope_token)

    def _write_lineage(self, **lineage: Any) -> None:
        if _LINEAGE.get() is not None:
            raise RuntimeError("materialization evidence unavailable")
        _LINEAGE.set(dict(lineage))

    def _update_catalog(
        self,
        name: str,
        layer: str,
        cartridge: str,
        schema_fields: list[dict],
        column_mapping: dict,
        description: str = "",
        user_context: dict | None = None,
    ) -> None:
        lineage = _LINEAGE.get()
        scope = _SCOPE.get()
        if lineage is None or scope is None or dict(user_context or {}) != scope:
            raise RuntimeError("materialization evidence unavailable")
        persist_materialization_evidence(
            self._pg_conn,
            scope=scope,
            lineage=lineage,
            catalog={
                "name": name,
                "layer": layer,
                "cartridge": cartridge,
                "schema_fields": schema_fields,
                "column_mapping": column_mapping,
                "description": description,
            },
        )
        _LINEAGE.set(None)

    def _latest_materialized_uri(
        self,
        layer: str,
        cartridge: str,
        name: str,
        user_context: dict | None = None,
    ) -> str | None:
        if layer not in {"silver", "gold"}:
            return None
        validate_safe_identifier(cartridge, "cartridge")
        validate_safe_identifier(name, "dataset")
        tenant_id, workspace_id = self._scope_values(user_context)
        if not tenant_id or not workspace_id:
            raise RuntimeError("materialization lineage scope unavailable")
        connection = self._pg_conn()
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true), "
                    "set_config('app.workspace_id', %s, true)",
                    (tenant_id, workspace_id),
                )
                cur.execute(
                    """
                    SELECT storage_uri
                      FROM silver_lineage
                     WHERE tenant_id = %s::uuid
                       AND workspace_id = %s::uuid
                       AND scope_status = 'scoped'
                       AND silver_name = %s
                       AND cartridge_id = %s
                       AND layer = %s
                       AND storage_uri IS NOT NULL
                       AND storage_uri <> ''
                     ORDER BY created_at DESC, id DESC
                     LIMIT 1
                    """,
                    (tenant_id, workspace_id, name, cartridge, layer),
                )
                row = cur.fetchone()
            return str(row[0]) if row and row[0] else None
        except Exception as exc:  # noqa: BLE001 - sanitize storage metadata failure
            raise RuntimeError("materialization lineage unavailable") from exc
        finally:
            connection.close()


__all__ = ["OperationalDuckDBEngine"]
