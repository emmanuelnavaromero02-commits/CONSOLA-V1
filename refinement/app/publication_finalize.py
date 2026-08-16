from __future__ import annotations

from typing import Any

try:
    from app.publication_semantics import snapshot_public_semantics
except ModuleNotFoundError:
    from refinement.app.publication_semantics import snapshot_public_semantics


# F8: the per-column profile the engine attaches at materialization
# (null_rate/distinct_count/min_value/max_value) must SURVIVE the staged
# rebuilds below — the gold_schema reconstruction and the parquet-derived
# catalog both used to flatten fields back to name+type, so no reader ever
# saw quality stats and relationship discovery (which needs distinct_count
# in data_catalog) stayed empty.
_PROFILE_STAT_KEYS = ("null_rate", "distinct_count", "min_value", "max_value")


def _merge_profile_stats(
    target: list[dict], source_fields: list[dict]
) -> list[dict]:
    """Carry profile stats from source fields onto same-named target items."""
    stats_by_name = {
        str(field.get("name") or ""): {
            key: field[key] for key in _PROFILE_STAT_KEYS if key in field
        }
        for field in source_fields
    }
    for item in target:
        extra = stats_by_name.get(str(item.get("name") or ""))
        if extra:
            for key, value in extra.items():
                item.setdefault(key, value)
    return target


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
        if (
            not state["object_uri"]
            or not state["object_checksum"]
            or not state["object_version"]
        ):
            raise RuntimeError("materialization object was not durably prepared")
        if layer == "gold" and state.get("gold_schema"):
            # F8 (punto B): the rebuild is authoritative for names/types but
            # must not drop the profile the engine attached to schema_fields.
            schema_fields = _merge_profile_stats(
                [
                    {"name": item[0], "type": item[1]}
                    for item in state["gold_schema"].values()
                ],
                schema_fields,
            )
        if not schema_fields or not state["lineage"]:
            raise RuntimeError("materialization schema and lineage are required")
        self._assert_inputs_unchanged(state["dataset"], state["user_context"])
        row_count, catalog = self._verify_parquet_evidence(
            object_uri=state["object_uri"],
            object_checksum=state["object_checksum"],
            object_version=state["object_version"],
            row_count=state["row_count"],
            expected_columns=[str(field.get("name") or "") for field in schema_fields],
        )
        state["row_count"] = row_count
        # F8 (punto C): the parquet-derived catalog is authoritative for the
        # verified shape; re-attach the profile stats by column name.
        catalog = _merge_profile_stats(catalog, schema_fields)
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
            object_version=state["object_version"],
            object_checksum=state["object_checksum"],
            row_count=row_count,
            schema_fields=catalog,
            lineage=state["lineage"],
        )
        candidate_id = self._candidate_store.submit(
            state["identity"],
            object_uri=state["object_uri"],
            object_version=state["object_version"],
            object_checksum=state["object_checksum"],
            row_count=row_count,
            lineage=lineage,
            catalog=catalog,
        )
        self._publication_verifier.verify(candidate_id)
        self._publication_store.mark_prepared(
            state["identity"],
            object_uri=state["object_uri"],
            object_version=state["object_version"],
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
            state["receipt"] = self._recover_prepared(state["identity"], exc)
        self._mark_publication_replayed(bool(state["receipt"].get("replayed")))
        state["published"] = True
        # F8 (punto A, raíz): the staged path never reached the base
        # data_catalog writer, so profile stats were never persisted and
        # relationship discovery (distinct_count) stayed empty. Upsert the
        # enriched catalog now that the publication succeeded; data_catalog is
        # descriptive metadata, so a failure here must never fail an
        # already-published run (the base writer is itself best-effort).
        try:
            super()._update_catalog(
                name,
                layer,
                cartridge,
                catalog,
                column_mapping,
                description,
                user_context,
            )
        except Exception:
            pass


__all__ = ["PublicationFinalizeMixin"]
