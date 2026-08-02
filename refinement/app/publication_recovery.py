from __future__ import annotations

import io
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def _recovery_reason(error: Exception) -> str:
    text = str(error or "").lower()
    for token, reason in (
        ("expired", "attestation_expired"),
        ("consumed", "attestation_consumed"),
        ("stage", "gold_stage_missing"),
        ("missing", "object_unavailable"),
        ("unavailable", "object_unavailable"),
        ("checksum", "object_corrupt"),
        ("mismatch", "evidence_mismatch"),
    ):
        if token in text:
            return reason
    return "evidence_mismatch"


def _postgres_type(field: pa.Field) -> str:
    value = field.type
    if pa.types.is_boolean(value):
        return "BOOLEAN"
    if pa.types.is_int8(value) or pa.types.is_int16(value):
        return "SMALLINT"
    if pa.types.is_int32(value):
        return "INTEGER"
    if pa.types.is_int64(value):
        return "BIGINT"
    if pa.types.is_float32(value):
        return "REAL"
    if pa.types.is_floating(value):
        return "DOUBLE PRECISION"
    if pa.types.is_date(value):
        return "DATE"
    if pa.types.is_timestamp(value):
        return "TIMESTAMPTZ" if value.tz else "TIMESTAMP"
    if pa.types.is_decimal(value):
        return f"DECIMAL({value.precision},{value.scale})"
    if pa.types.is_string(value) or pa.types.is_large_string(value):
        return "TEXT"
    raise RuntimeError("prepared Gold schema cannot be reconstructed")


class PublicationRecoveryMixin:
    """Revalidates frozen prepared state instead of recomputing it."""

    def _rebuild_gold_stage(self, identity: Any, values: dict[str, Any]) -> str:
        key = self._s3_object_key(str(values["object_uri"]))
        if not key:
            raise RuntimeError("prepared materialization object is outside storage")
        raw = self.storage.get_bytes(
            key, expected_version=str(values["object_version"])
        )
        table = pq.read_table(io.BytesIO(raw))
        if table.num_rows != int(values["row_count"]):
            raise RuntimeError("prepared materialization row count mismatch")
        columns = [
            {"name": field.name, "type": _postgres_type(field)}
            for field in table.schema
        ]
        rows = [
            tuple(item.get(field.name) for field in table.schema)
            for item in table.to_pylist()
        ]
        stage, count, _ = self._publication_store.stage_gold(identity, columns, rows)
        if count != int(values["row_count"]):
            raise RuntimeError("reconstructed Gold stage row count mismatch")
        return stage

    def _recover_prepared(self, identity: Any, error: Exception) -> dict[str, Any]:
        reason = _recovery_reason(error)
        try:
            values = self._publication_store.reopen_prepared(identity, reason)
        except Exception as reopen_error:
            self._publication_store.quarantine_prepared(identity, reason)
            raise RuntimeError(
                "prepared materialization recovery is unavailable"
            ) from reopen_error
        try:
            self._verify_prepared_object(values)
            if identity.scope.layer == "gold" and reason == "gold_stage_missing":
                values["staging_table"] = self._rebuild_gold_stage(identity, values)
            candidate_id = self._candidate_store.submit(
                identity,
                object_uri=values["object_uri"],
                object_version=values["object_version"],
                object_checksum=values["object_checksum"],
                row_count=values["row_count"],
                lineage=values["lineage"],
                catalog=values["catalog"],
            )
            self._publication_verifier.verify(candidate_id)
            self._publication_store.mark_prepared(
                identity,
                object_uri=values["object_uri"],
                object_version=values["object_version"],
                object_checksum=values["object_checksum"],
                row_count=values["row_count"],
                staging_table=values["staging_table"],
                lineage=values["lineage"],
                catalog=values["catalog"],
            )
            return self._publication_store.publish(
                identity, values["expected_head_run_id"]
            )
        except Exception as recovery_error:
            current = self._publication_store.run(identity) or {}
            if current.get("status") != "prepared":
                self._publication_store.abandon(identity)
            raise RuntimeError(
                "prepared materialization is recoverable; retry is required"
            ) from recovery_error


__all__ = ["PublicationRecoveryMixin"]
