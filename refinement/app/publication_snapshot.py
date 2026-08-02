from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import psycopg2

try:
    from app.publication_contract import PublicationScope
except ModuleNotFoundError:
    from refinement.app.publication_contract import PublicationScope


def _dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


@dataclass(frozen=True)
class PublicationSnapshot:
    scope: PublicationScope
    head: dict[str, Any]
    evidence: dict[str, Any] | None

    @property
    def run_id(self) -> str:
        return str(self.head["materialization_run_id"])

    def validate_snapshot(self) -> None:
        if self.head.get("status") == "legacy_unverified":
            return
        evidence = self.evidence or {}
        exact = (
            "object_uri",
            "object_version",
            "object_checksum",
            "schema_digest",
            "evidence_digest",
            "row_count",
        )
        if self.head.get("receipt_id") is None or any(
            self.head.get(key) is None for key in exact
        ):
            raise RuntimeError("published snapshot authority is incomplete")
        if str(evidence.get("materialization_run_id") or "") != self.run_id or any(
            evidence.get(key) != self.head.get(key) for key in exact
        ):
            raise RuntimeError("published snapshot authority mismatch")


class PublicationSnapshotResolver:
    """Pins head, receipt and evidence in one repeatable-read transaction."""

    def __init__(self, storage: Any | None = None, database_url: str | None = None):
        self.storage = storage
        self.database_url = _dsn(
            database_url
            or os.environ.get("GOLD_DATABASE_URL", "")
            or os.environ.get("DATABASE_URL", "")
        )
        if not self.database_url:
            raise RuntimeError(
                "GOLD_DATABASE_URL is required for publication snapshots"
            )

    @staticmethod
    def _scope(cur: Any, scope: PublicationScope) -> None:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (scope.tenant_id,))
        cur.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (scope.workspace_id,),
        )

    def _read(self, scope: PublicationScope) -> PublicationSnapshot | None:
        conn = psycopg2.connect(self.database_url)
        try:
            conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
            with conn.cursor() as cur:
                self._scope(cur, scope)
                cur.execute(
                    """
                    SELECT h.materialization_run_id::text,h.generation,h.published_at,
                           r.status,r.gold_table,r.input_digest,r.contract_digest,
                           e.object_uri,e.object_version,e.object_checksum,e.row_count,
                           e.schema_digest,e.evidence_digest,e.lineage,e.catalog,e.created_at,
                           rec.receipt_id::text
                      FROM omega_publication.dataset_publication_heads h
                      JOIN omega_publication.materialization_runs r
                        ON r.materialization_run_id=h.materialization_run_id
                       AND r.tenant_id=h.tenant_id AND r.workspace_id=h.workspace_id
                       AND r.dataset=h.dataset AND r.layer=h.layer
                      LEFT JOIN omega_publication.materialization_receipts rec
                        ON rec.materialization_run_id=h.materialization_run_id
                       AND rec.tenant_id=h.tenant_id AND rec.workspace_id=h.workspace_id
                       AND rec.dataset=h.dataset AND rec.layer=h.layer
                       AND rec.generation=h.generation
                      LEFT JOIN omega_publication.materialization_evidence e
                        ON e.materialization_run_id=h.materialization_run_id
                       AND e.tenant_id=h.tenant_id AND e.workspace_id=h.workspace_id
                       AND e.dataset=h.dataset AND e.layer=h.layer
                       AND e.object_version=rec.object_version
                       AND e.object_checksum=rec.object_checksum
                       AND e.schema_digest=rec.schema_digest
                       AND e.evidence_digest=rec.evidence_digest
                       AND e.row_count=rec.row_count
                     WHERE h.tenant_id=%s AND h.workspace_id=%s
                       AND h.dataset=%s AND h.layer=%s
                    """,
                    (scope.tenant_id, scope.workspace_id, scope.dataset, scope.layer),
                )
                row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        if not row:
            return None
        exact = {
            "object_uri": row[7],
            "object_version": row[8],
            "object_checksum": row[9],
            "row_count": row[10],
            "schema_digest": row[11],
            "evidence_digest": row[12],
        }
        head = {
            "materialization_run_id": row[0],
            "generation": int(row[1]),
            "published_at": row[2],
            "status": row[3],
            "gold_table": row[4],
            "input_digest": row[5],
            "contract_digest": row[6],
            **exact,
            "receipt_id": row[16],
        }
        evidence = (
            None
            if row[13] is None
            else {
                "materialization_run_id": row[0],
                **exact,
                "lineage": row[13],
                "catalog": row[14],
                "created_at": row[15],
            }
        )
        return PublicationSnapshot(scope, head, evidence)

    def _validate_object(self, snapshot: PublicationSnapshot) -> None:
        if not self.storage or snapshot.head.get("status") == "legacy_unverified":
            return
        uri = str(snapshot.head["object_uri"])
        parsed = urlsplit(uri)
        key = parsed.path.lstrip("/")
        if parsed.scheme not in {"s3", "gs"} or not key:
            raise RuntimeError("published snapshot object is outside managed storage")
        if self.storage.uri_for(key) != uri:
            raise RuntimeError("published snapshot object scope mismatch")
        version = str(snapshot.head["object_version"])
        digest = hashlib.sha256()
        try:
            for chunk in self.storage.iter_chunks(key, expected_version=version):
                digest.update(chunk)
        except Exception as exc:
            raise RuntimeError("published snapshot object is unavailable") from exc
        if digest.hexdigest() != snapshot.head["object_checksum"]:
            raise RuntimeError("published snapshot object checksum mismatch")

    def published_snapshot(
        self, ds: dict[str, Any], context: dict[str, str]
    ) -> PublicationSnapshot | None:
        scope = PublicationScope(
            context["tenant_id"],
            context["workspace_id"],
            str(ds.get("name") or ""),
            str(ds.get("layer") or "silver"),
        )
        snapshot = self._read(scope)
        if not snapshot:
            return None
        snapshot.validate_snapshot()
        self._validate_object(snapshot)
        return snapshot
