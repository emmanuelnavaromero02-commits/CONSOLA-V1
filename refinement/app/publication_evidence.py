from __future__ import annotations

import json
import os
import socket
from typing import Any
import uuid

import psycopg2

try:
    from app.publication_contract import PublicationIdentity
    from app.publication_store import PublicationStore
except ModuleNotFoundError:
    from refinement.app.publication_contract import (
        PublicationIdentity,
    )
    from refinement.app.publication_store import PublicationStore


def _dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


class PublicationVerifierClient:
    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = socket_path or os.environ.get(
            "PUBLICATION_VERIFIER_SOCKET", "/run/omega/publication-verifier.sock"
        )

    def _request(self, payload: dict[str, str]) -> dict[str, object]:
        raw = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(30)
            client.connect(self.socket_path)
            client.sendall(raw)
            result = json.loads(client.makefile("rb").readline())
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("publication verifier unavailable")
        return result

    def verify(self, candidate_id: uuid.UUID) -> None:
        self._request({"candidate_id": str(candidate_id)})

    def ready(self) -> bool:
        return self._request({"command": "PING"}).get("status") == "ready"


class PublicationCandidateStore:
    def __init__(self, store: PublicationStore) -> None:
        self.store = store

    def submit(
        self,
        identity: PublicationIdentity,
        *,
        object_uri: str,
        object_version: str,
        object_checksum: str,
        row_count: int,
        lineage: dict[str, Any],
        catalog: list[dict[str, Any]],
    ) -> uuid.UUID:
        with (
            psycopg2.connect(self.store._publisher_url()) as conn,
            conn.cursor() as cur,
        ):
            self.store._scope(cur, identity.scope)
            cur.execute(
                "SELECT omega_publication.submit_verification_candidate("
                "%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                (
                    str(identity.materialization_run_id),
                    object_uri,
                    object_version,
                    object_checksum,
                    row_count,
                    json.dumps(lineage, sort_keys=True),
                    json.dumps(catalog, sort_keys=True),
                ),
            )
            return uuid.UUID(str(cur.fetchone()[0]))


class PublicationEvidenceStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = _dsn(
            database_url
            or os.environ.get("GOLD_DATABASE_URL", "")
            or os.environ.get("DATABASE_URL", "")
        )
        if not self.database_url:
            raise RuntimeError("GOLD_DATABASE_URL is required for publication evidence")

    def prepare(
        self,
        identity: PublicationIdentity,
        *,
        object_uri: str,
        object_version: str,
        object_checksum: str,
        row_count: int,
        schema_fields: list[dict[str, Any]],
        lineage: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        catalog = []
        for field in schema_fields:
            item: dict[str, Any] = {
                "name": str(field.get("name") or ""),
                "type": str(field.get("type") or ""),
            }
            for key in (
                "description",
                "tags",
                "is_key",
                "is_metric",
                "example_values",
                "null_rate",
                "distinct_count",
                "min_value",
                "max_value",
            ):
                if key in field:
                    item[key] = field[key]
            catalog.append(item)
        if not catalog or any(not item["name"] or not item["type"] for item in catalog):
            raise ValueError("materialization catalog is incomplete")
        del object_uri, object_version, object_checksum, row_count, identity
        return lineage, catalog

    def published(
        self, materialization_run_id: str, identity: PublicationIdentity
    ) -> dict[str, Any] | None:
        return self.read_exact(materialization_run_id, identity.scope)

    def read_exact(
        self, materialization_run_id: str, scope: Any
    ) -> dict[str, Any] | None:
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id', %s, true)", (scope.tenant_id,)
            )
            cur.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (scope.workspace_id,)
            )
            cur.execute(
                """SELECT materialization_run_id::text,lineage,catalog,row_count,
                          created_at,object_uri,object_version,object_checksum,
                          schema_digest,evidence_digest,attestation_id::text
                     FROM omega_publication.materialization_evidence
                    WHERE materialization_run_id=%s""",
                (materialization_run_id,),
            )
            row = cur.fetchone()
            return (
                {
                    "materialization_run_id": row[0],
                    "lineage": row[1],
                    "catalog": row[2],
                    "row_count": row[3],
                    "created_at": row[4],
                    "object_uri": row[5],
                    "object_version": row[6],
                    "object_checksum": row[7],
                    "schema_digest": row[8],
                    "evidence_digest": row[9],
                    "attestation_id": row[10],
                }
                if row
                else None
            )
