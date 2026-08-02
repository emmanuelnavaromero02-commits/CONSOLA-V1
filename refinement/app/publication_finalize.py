from __future__ import annotations

from typing import Any

try:
    from app.publication_semantics import snapshot_public_semantics
except ModuleNotFoundError:
    from refinement.app.publication_semantics import snapshot_public_semantics


class PublicationFinalizeMixin:
    """Verify, attest and atomically publish one frozen materialization."""

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
        state = self._state()
        if not state:
            return super()._update_catalog(
                name,
                layer,
                cartridge,
                schema_fields,
                column_mapping,
                description,
                user_context,
            )
        if not state["object_uri"] or not state["object_checksum"]:
            raise RuntimeError("materialization object was not durably prepared")
        if layer == "gold" and state.get("gold_schema"):
            schema_fields = [
                {"name": item[0], "type": item[1]}
                for item in state["gold_schema"].values()
            ]
        if not schema_fields or not state["lineage"]:
            raise RuntimeError("materialization schema and lineage are required")
        self._assert_inputs_unchanged(state["dataset"], state["user_context"])
        row_count, catalog = self._verify_parquet_evidence(
            object_uri=state["object_uri"],
            object_checksum=state["object_checksum"],
            row_count=state["row_count"],
            expected_columns=[str(field.get("name") or "") for field in schema_fields],
        )
        state["row_count"] = row_count
        semantics = snapshot_public_semantics(
            self._pg_conn if self.pg_url else None,
            tenant_id=state["identity"].scope.tenant_id,
            workspace_id=state["identity"].scope.workspace_id,
            dataset=state["dataset"],
        )
        for field in catalog:
            field.update(semantics["columns"].get(field["name"], {}))
        state["lineage"]["public_sources"] = semantics["sources"]
        state["lineage"]["public_metadata"] = {
            "description": semantics["description"],
            "relationships": semantics["relationships"],
        }
        lineage, catalog = self._evidence_store.prepare(
            state["identity"],
            object_uri=state["object_uri"],
            object_checksum=state["object_checksum"],
            row_count=row_count,
            schema_fields=catalog,
            lineage=state["lineage"],
        )
        self._publication_store.mark_prepared(
            state["identity"],
            object_uri=state["object_uri"],
            object_checksum=state["object_checksum"],
            row_count=row_count,
            staging_table=state["staging_table"],
            lineage=lineage,
            catalog=catalog,
        )
        self._verify_prepared_object(state)
        try:
            state["receipt"] = self._publication_store.publish(
                state["identity"], state["expected_head"]
            )
        except Exception as exc:
            if getattr(exc, "pgcode", None) == "40001":
                self._publication_store.abandon(state["identity"])
            raise
        self._mark_publication_replayed(bool(state["receipt"].get("replayed")))
        state["published"] = True


__all__ = ["PublicationFinalizeMixin"]
