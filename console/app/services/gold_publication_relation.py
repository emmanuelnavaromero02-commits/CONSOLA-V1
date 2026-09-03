from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException


_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_RUN_TABLE_RE = re.compile(r"^run_[0-9a-f]{32}$")
_LEGACY_TABLE_RE = re.compile(r"^gold_[A-Za-z_][A-Za-z0-9_]{0,127}$")


@dataclass(frozen=True)
class PublishedGoldRelation:
    schema: str
    table: str
    run_id: str
    generation: int
    receipt_id: str | None
    object_checksum: str | None
    evidence_digest: str | None
    object_uri: str | None = None
    object_version: str | None = None
    schema_digest: str | None = None
    row_count: int | None = None
    published_at: datetime | None = None

    @property
    def sql(self) -> str:
        return f'"{self.schema}"."{self.table}"'


async def resolve_published_gold_relation(
    conn, tenant_id: str, workspace_id: str, dataset: str
) -> PublishedGoldRelation:
    if not _DATASET_RE.fullmatch(str(dataset or "")):
        raise HTTPException(400, "invalid intelligence dataset")
    row = await conn.fetchrow(
        """SELECT h.materialization_run_id::text AS run_id, h.generation,
                  r.status, r.gold_table, rec.receipt_id::text,
                  r.object_checksum, r.evidence_digest,r.object_uri,
                  r.object_version,r.schema_digest,r.row_count,h.published_at
             FROM omega_publication.dataset_publication_heads h
             JOIN omega_publication.materialization_runs r
               ON r.materialization_run_id=h.materialization_run_id
             LEFT JOIN omega_publication.materialization_receipts rec
               ON rec.materialization_run_id=h.materialization_run_id
            WHERE h.tenant_id=$1 AND h.workspace_id=$2
              AND h.dataset=$3 AND h.layer='gold'""",
        tenant_id,
        workspace_id,
        dataset,
    )
    if not row:
        raise HTTPException(404, f"dataset unavailable: {dataset}")
    status = str(row["status"])
    table = str(row["gold_table"] or "")
    if status == "published" and _RUN_TABLE_RE.fullmatch(table):
        schema = "omega_publication_gold"
    elif status == "legacy_unverified" and _LEGACY_TABLE_RE.fullmatch(table):
        schema = "public"
    else:
        raise HTTPException(404, f"dataset unavailable: {dataset}")
    return PublishedGoldRelation(
        schema=schema,
        table=table,
        run_id=str(row["run_id"]),
        generation=int(row["generation"]),
        receipt_id=str(row["receipt_id"]) if row["receipt_id"] else None,
        object_checksum=str(row["object_checksum"]) if row["object_checksum"] else None,
        evidence_digest=str(row["evidence_digest"]) if row["evidence_digest"] else None,
        object_uri=str(row["object_uri"]) if row["object_uri"] else None,
        object_version=str(row["object_version"]) if row["object_version"] else None,
        schema_digest=str(row["schema_digest"]) if row["schema_digest"] else None,
        row_count=int(row["row_count"]) if row["row_count"] is not None else None,
        published_at=row["published_at"],
    )


async def published_relation_columns(conn, relation: PublishedGoldRelation) -> set[str]:
    return set(await published_relation_column_types(conn, relation))


async def published_relation_column_types(
    conn, relation: PublishedGoldRelation
) -> dict[str, str]:
    rows = await conn.fetch(
        """SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema=$1 AND table_name=$2""",
        relation.schema,
        relation.table,
    )
    return {str(row["column_name"]): str(row["data_type"]) for row in rows}
