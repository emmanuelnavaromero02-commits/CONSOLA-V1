from __future__ import annotations

import json
import os
from typing import Any

import psycopg2

try:
    from app.publication_contract import PublicationIdentity
except ModuleNotFoundError:
    from refinement.app.publication_contract import (
        PublicationIdentity,
    )


def _dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


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
            for key in ("description", "tags", "is_key", "is_metric", "example_values"):
                if key in field:
                    item[key] = field[key]
            catalog.append(item)
        if not catalog or any(not item["name"] or not item["type"] for item in catalog):
            raise ValueError("materialization catalog is incomplete")
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id',%s,true),"
                "set_config('app.workspace_id',%s,true)",
                (identity.scope.tenant_id, identity.scope.workspace_id),
            )
            cur.execute(
                "SELECT omega_publication.record_attestation("
                "%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                (
                    str(identity.materialization_run_id),
                    object_uri,
                    object_checksum,
                    row_count,
                    json.dumps(lineage, sort_keys=True, default=str),
                    json.dumps(catalog, sort_keys=True, default=str),
                ),
            )
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
                """SELECT lineage, catalog, row_count, created_at
                     FROM omega_publication.materialization_evidence
                    WHERE materialization_run_id=%s""",
                (materialization_run_id,),
            )
            row = cur.fetchone()
            return (
                {
                    "lineage": row[0],
                    "catalog": row[1],
                    "row_count": row[2],
                    "created_at": row[3],
                }
                if row
                else None
            )
