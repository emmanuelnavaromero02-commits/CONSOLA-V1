from __future__ import annotations

from typing import Any, NoReturn

import pyarrow as pa

try:
    from app.publication_objects import (
        PreparedObjectCorrupt,
        PreparedObjectUnavailable,
    )
    from app.publication_snapshot import (
        PublicationRecoveryPending,
        PublicationRejected,
    )
except ModuleNotFoundError:
    from refinement.app.publication_objects import (
        PreparedObjectCorrupt,
        PreparedObjectUnavailable,
    )
    from refinement.app.publication_snapshot import (
        PublicationRecoveryPending,
        PublicationRejected,
    )


_HEAD_CAS_LOST_SQLSTATE = "40001"
_DETERMINISTIC_SQLSTATE_CLASSES = frozenset({"22", "23", "42"})
_REASON_BY_SQLSTATE = {
    "42804": "legacy_type_conflict",
    "55000": "gold_stage_missing",
    "23514": "publication_integrity_rejected",
}
_REASON_BY_TYPE = (
    (PreparedObjectUnavailable, "object_unavailable"),
    (PreparedObjectCorrupt, "object_corrupt"),
)
_RETRY_REQUIRED = "prepared materialization is recoverable; retry is required"
_RECOVERY_UNAVAILABLE = "prepared materialization recovery is unavailable"


def _sqlstate(error: BaseException) -> str:
    return str(getattr(error, "pgcode", None) or "")


def _is_deterministic(sqlstate: str) -> bool:
    return len(sqlstate) == 5 and sqlstate[:2] in _DETERMINISTIC_SQLSTATE_CLASSES


def _recovery_reason(error: Exception) -> str:
    sqlstate = _sqlstate(error)
    constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
    if sqlstate.startswith("23") and constraint:
        return "integrity_constraint_violation"
    if sqlstate in _REASON_BY_SQLSTATE:
        return _REASON_BY_SQLSTATE[sqlstate]
    if _is_deterministic(sqlstate):
        return "publication_rejected"
    for kind, reason in _REASON_BY_TYPE:
        if isinstance(error, kind):
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

    def _rebuild_gold_stage(self, identity: Any, values: dict[str, Any]) -> str:
        table = self._read_published_table(
            str(values["object_uri"]), str(values["object_version"])
        )
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

    def _quarantine_head_cas_loser(self, identity: Any, error: Exception) -> NoReturn:
        try:
            self._publication_store.quarantine_prepared(
                identity, "publication_head_cas_lost"
            )
        except Exception as quarantine_error:
            raise PublicationRecoveryPending(_RETRY_REQUIRED) from quarantine_error
        raise PublicationRecoveryPending(
            "publication head conflict; prepared run was quarantined"
        ) from error

    def _settle_failed_publication(self, identity: Any, error: Exception) -> NoReturn:
        sqlstate = _sqlstate(error)
        if sqlstate == _HEAD_CAS_LOST_SQLSTATE:
            self._quarantine_head_cas_loser(identity, error)
        if not _is_deterministic(sqlstate):
            raise PublicationRecoveryPending(_RETRY_REQUIRED) from error
        try:
            self._publication_store.quarantine_prepared(
                identity, _recovery_reason(error)
            )
        except Exception as quarantine_error:
            raise PublicationRecoveryPending(_RETRY_REQUIRED) from quarantine_error
        raise PublicationRejected(
            "prepared materialization was rejected by the publication authority"
        ) from error

    def _settle_failed_reopen(
        self, identity: Any, reason: str, error: Exception
    ) -> dict[str, Any]:
        if not _is_deterministic(_sqlstate(error)):
            raise PublicationRecoveryPending(_RECOVERY_UNAVAILABLE) from error
        try:
            current = self._publication_store.run(identity) or {}
            if current.get("status") == "published":
                receipt = self._publication_store.publish(
                    identity, current.get("expected_head_run_id")
                )
                return {**receipt, "replayed": False}
            self._publication_store.quarantine_prepared(identity, reason)
        except Exception as settle_error:
            raise PublicationRecoveryPending(_RECOVERY_UNAVAILABLE) from settle_error
        raise PublicationRecoveryPending(_RECOVERY_UNAVAILABLE) from error

    def _recover_prepared(self, identity: Any, error: Exception) -> dict[str, Any]:
        reason = _recovery_reason(error)
        try:
            values = self._publication_store.reopen_prepared(identity, reason)
        except Exception as reopen_error:
            return self._settle_failed_reopen(identity, reason, reopen_error)
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
        except Exception as recovery_error:
            try:
                current = self._publication_store.run(identity) or {}
                if current.get("status") != "prepared":
                    self._publication_store.abandon(identity)
            except Exception as cleanup_error:
                raise PublicationRecoveryPending(_RETRY_REQUIRED) from cleanup_error
            raise PublicationRecoveryPending(_RETRY_REQUIRED) from recovery_error
        try:
            return self._publication_store.publish(
                identity, values["expected_head_run_id"]
            )
        except Exception as publish_error:
            self._settle_failed_publication(identity, publish_error)


__all__ = ["PublicationRecoveryMixin"]
