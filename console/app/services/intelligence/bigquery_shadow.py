"""Aggregate-only PostgreSQL baseline for the Talent 9-Box shadow pilot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import asyncpg
from fastapi import HTTPException

from app.services import auth
from app.services.gold_publication_relation import (
    PublishedGoldRelation,
    published_relation_columns,
)
from app.services.intelligence.successfactors_talent_population import (
    NINE_BOX_DATASET,
    _NINE_BOX_REQUIRED,
    _benchmark_verdict,
    _gold_dsn,
    _not_degraded_sql,
    _score_ok,
)
from app.services.intelligence.talent_population_backend import (
    public_talent_population_backend,
)


CARTRIDGE_ID = "sap_successfactors"
BOX_KEYS = (
    "enigma",
    "crecimiento",
    "estrella",
    "dilema",
    "core",
    "alto_impacto",
    "riesgo",
    "efectivo",
    "experto",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,511}$")
_RUN_TABLE = re.compile(r"^run_[0-9a-f]{32}$")


def canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def shadow_enabled() -> bool:
    return str(
        os.environ.get("BIGQUERY_TALENT_9BOX_SHADOW_ENABLED") or "false"
    ).strip().lower() in {"1", "true", "yes", "on"}


def _allowlist() -> set[str]:
    return {
        item.strip().lower()
        for item in str(
            os.environ.get("BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST")
            or ""
        ).split(",")
        if item.strip()
    }


def _tenant_allowlist() -> set[str]:
    return {
        item.strip().lower()
        for item in str(
            os.environ.get("BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST") or ""
        ).split(",")
        if item.strip()
    }


def _require_uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except ValueError as exc:
        raise HTTPException(400, f"invalid {field}") from exc


def _iso(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        raise HTTPException(409, "Gold publication timestamp is incomplete")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _validate_publication_uri(
    uri: str,
    *,
    tenant_id: str,
    workspace_id: str,
    dataset: str,
    source_run_id: str,
    checksum: str,
) -> tuple[str, str]:
    parsed = urlsplit(uri)
    key = parsed.path.lstrip("/")
    expected_key = (
        f"gold/{CARTRIDGE_ID}/{dataset}/tenant_id={tenant_id}/"
        f"workspace_id={workspace_id}/_snapshots/_pending/"
        f"{uuid.UUID(source_run_id).hex}/{checksum}.parquet"
    )
    if (
        parsed.scheme != "gs"
        or not parsed.netloc
        or key != expected_key
        or any(token in uri for token in ("*", "?", "\\", ".."))
    ):
        raise HTTPException(409, "Gold publication object is not an exact GCS snapshot")
    return parsed.netloc, key


def _manifest(
    relation: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    pipeline_run_id: str,
) -> dict[str, Any]:
    if relation.schema != "omega_publication_gold":
        raise HTTPException(409, "legacy Gold snapshots are not eligible for shadowing")
    try:
        source_run_id = str(uuid.UUID(str(relation.run_id)))
        receipt_id = str(uuid.UUID(str(relation.receipt_id)))
        object_version = str(relation.object_version)
        numeric_object_version = int(object_version)
        row_count = int(relation.row_count)
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, "Gold publication authority is incomplete") from exc
    if numeric_object_version <= 0 or row_count < 0 or int(relation.generation) <= 0:
        raise HTTPException(409, "Gold publication authority is incomplete")
    digests = {
        "checksum": str(relation.object_checksum or "").lower(),
        "schema_digest": str(relation.schema_digest or "").lower(),
        "evidence_digest": str(relation.evidence_digest or "").lower(),
    }
    if any(not _SHA256.fullmatch(value) for value in digests.values()):
        raise HTTPException(409, "Gold publication digests are incomplete")
    uri = str(relation.object_uri or "")
    bucket, object_key = _validate_publication_uri(
        uri,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset=NINE_BOX_DATASET,
        source_run_id=source_run_id,
        checksum=digests["checksum"],
    )
    published_at = _iso(relation.published_at)
    max_age = max(
        60,
        int(
            os.environ.get("BIGQUERY_TALENT_9BOX_MAX_SNAPSHOT_AGE_SECONDS")
            or 86_400
        ),
    )
    stamp = relation.published_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age_seconds = (
        datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
    ).total_seconds()
    if age_seconds < -300:
        raise HTTPException(409, "Gold publication timestamp is in the future")
    if age_seconds > max_age:
        raise HTTPException(409, "Gold publication snapshot is stale")
    return {
        "pipeline_run_id": pipeline_run_id,
        "source_run_id": source_run_id,
        "head_generation": int(relation.generation),
        "object_version": object_version,
        "receipt_id": receipt_id,
        "uri": uri,
        "bucket": bucket,
        "object_key": object_key,
        **digests,
        "row_count": row_count,
        "published_at": published_at,
    }


def _validate_cell_counts(cells: dict[str, dict[str, Any]], population: int) -> None:
    classified = 0
    assigned = 0
    for box_key in BOX_KEYS:
        item = cells[box_key]
        values = {
            key: int(item[key])
            for key in (
                "employee_count",
                "ready_count",
                "benchmark_count",
                "blocked_count",
            )
        }
        if any(value < 0 for value in values.values()):
            raise HTTPException(409, "Gold 9-Box contains negative counts")
        if values["ready_count"] > values["employee_count"]:
            raise HTTPException(409, "Gold 9-Box ready count exceeds its cell")
        if values["benchmark_count"] > values["ready_count"]:
            raise HTTPException(409, "Gold 9-Box benchmark count is inconsistent")
        if values["blocked_count"] != values["employee_count"] - values["ready_count"]:
            raise HTTPException(409, "Gold 9-Box blocked count is inconsistent")
        classified += values["ready_count"]
        assigned += values["employee_count"]
    if classified > population or assigned > population:
        raise HTTPException(409, "Gold 9-Box counts exceed physical population")


async def _workspace_allowed(conn: Any, tenant_id: str, workspace_id: str) -> None:
    row = await conn.fetchrow(
        """
        SELECT id::text AS workspace_id
          FROM workspaces
         WHERE id=$1::uuid AND tenant_id=$2::uuid
        """,
        workspace_id,
        tenant_id,
    )
    allowed = _allowlist()
    if (
        tenant_id.lower() not in _tenant_allowlist()
        or not row
        or str(row["workspace_id"]).lower() not in allowed
    ):
        raise HTTPException(403, "workspace is not enabled for the shadow pilot")


async def _resolve_shadow_publication(
    conn: Any, tenant_id: str, workspace_id: str, dataset: str
) -> PublishedGoldRelation:
    """Resolve head+run+receipt+evidence as one exact authority tuple."""
    row = await conn.fetchrow(
        """
        SELECT h.materialization_run_id::text AS run_id,
               h.generation, h.published_at, r.gold_table,
               r.object_uri, r.object_version, r.object_checksum,
               r.row_count, r.schema_digest, r.evidence_digest,
               rec.receipt_id::text AS receipt_id
          FROM omega_publication.dataset_publication_heads h
          JOIN omega_publication.materialization_runs r
            ON r.materialization_run_id=h.materialization_run_id
           AND r.tenant_id=h.tenant_id AND r.workspace_id=h.workspace_id
           AND r.dataset=h.dataset AND r.layer=h.layer
           AND r.status='published'
          JOIN omega_publication.materialization_receipts rec
            ON rec.materialization_run_id=h.materialization_run_id
           AND rec.tenant_id=h.tenant_id AND rec.workspace_id=h.workspace_id
           AND rec.dataset=h.dataset AND rec.layer=h.layer
           AND rec.generation=h.generation
           AND rec.input_digest=r.input_digest
           AND rec.contract_digest=r.contract_digest
           AND rec.object_version=r.object_version
           AND rec.object_checksum=r.object_checksum
           AND rec.row_count=r.row_count
           AND rec.schema_digest=r.schema_digest
           AND rec.evidence_digest=r.evidence_digest
          JOIN omega_publication.materialization_evidence e
            ON e.materialization_run_id=h.materialization_run_id
           AND e.tenant_id=h.tenant_id AND e.workspace_id=h.workspace_id
           AND e.dataset=h.dataset AND e.layer=h.layer
           AND e.object_uri=r.object_uri
           AND e.object_version=r.object_version
           AND e.object_checksum=r.object_checksum
           AND e.row_count=r.row_count
           AND e.schema_digest=r.schema_digest
           AND e.evidence_digest=r.evidence_digest
           AND e.attestation_id IS NOT NULL
         WHERE h.tenant_id=$1::uuid AND h.workspace_id=$2::uuid
           AND h.dataset=$3 AND h.layer='gold'
        """,
        tenant_id,
        workspace_id,
        dataset,
    )
    if not row or not _RUN_TABLE.fullmatch(str(row["gold_table"] or "")):
        raise HTTPException(409, "verified Gold publication authority is unavailable")
    return PublishedGoldRelation(
        schema="omega_publication_gold",
        table=str(row["gold_table"]),
        run_id=str(row["run_id"]),
        generation=int(row["generation"]),
        receipt_id=str(row["receipt_id"]),
        object_checksum=str(row["object_checksum"]),
        evidence_digest=str(row["evidence_digest"]),
        object_uri=str(row["object_uri"]),
        object_version=str(row["object_version"]),
        schema_digest=str(row["schema_digest"]),
        row_count=int(row["row_count"]),
        published_at=row["published_at"],
    )


async def build_talent_9box_baseline(
    *,
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str,
    dataset: str,
    pipeline_run_id: str,
    source_run_id: str,
) -> dict[str, Any]:
    if not shadow_enabled():
        raise HTTPException(404, "BigQuery Talent shadow is disabled")
    try:
        public_talent_population_backend()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    tenant_id = _require_uuid(tenant_id, "tenant_id")
    workspace_id = _require_uuid(workspace_id, "workspace_id")
    source_run_id = _require_uuid(source_run_id, "source_run_id")
    if cartridge_id != CARTRIDGE_ID or dataset != NINE_BOX_DATASET:
        raise HTTPException(400, "invalid Talent shadow target")
    if not _RUN_REF.fullmatch(str(pipeline_run_id or "")):
        raise HTTPException(400, "invalid pipeline_run_id")

    pool = await auth.pool()
    async with pool.acquire() as operational_conn:
        async with operational_conn.transaction(
            isolation="repeatable_read", readonly=True
        ):
            await operational_conn.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                " set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            await _workspace_allowed(operational_conn, tenant_id, workspace_id)
            pipeline = await operational_conn.fetchrow(
                """
                SELECT status, cartridge_id, extra
                  FROM pipeline_runs
                 WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid
                   AND run_id=$3 AND dag_id='dataset_refresh_chain'
                   AND entity='DatasetRefreshChain'
                 LIMIT 1
                """,
                tenant_id,
                workspace_id,
                pipeline_run_id,
            )
            if (
                not pipeline
                or str(pipeline["status"]) != "success"
                or str(pipeline["cartridge_id"]) != CARTRIDGE_ID
            ):
                raise HTTPException(409, "successful source pipeline run is unavailable")

            raw_extra = pipeline["extra"]
            if isinstance(raw_extra, str):
                try:
                    raw_extra = json.loads(raw_extra)
                except json.JSONDecodeError:
                    raw_extra = None
            pipeline_extra = raw_extra if isinstance(raw_extra, dict) else {}
            source_bindings = [
                item
                for item in (pipeline_extra.get("results") or [])
                if isinstance(item, dict)
                and item.get("ok") is True
                and item.get("name") == dataset
            ]
            if len(source_bindings) != 1 or str(
                source_bindings[0].get("publication_run_id") or ""
            ) != source_run_id:
                raise HTTPException(409, "source pipeline is not bound to this Gold head")

    gold_dsn = _gold_dsn()
    if not gold_dsn:
        raise HTTPException(503, "gold database unavailable")
    # The baseline is one aggregate-only scan but can cover millions of rows.
    # Keep a bounded analytical timeout rather than inheriting the 20-second
    # interactive-query default.
    gold_conn = await asyncpg.connect(gold_dsn, command_timeout=120)
    try:
        async with gold_conn.transaction(isolation="repeatable_read", readonly=True):
            await gold_conn.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                " set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            relation = await _resolve_shadow_publication(
                gold_conn, tenant_id, workspace_id, dataset
            )
            if str(relation.run_id) != source_run_id:
                raise HTTPException(409, "Gold publication head changed")
            columns = set(await published_relation_columns(gold_conn, relation))
            if not _NINE_BOX_REQUIRED.issubset(columns):
                raise HTTPException(409, "Gold 9-Box schema is incomplete")
            manifest = _manifest(
                relation,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                pipeline_run_id=pipeline_run_id,
            )
            authority_valid, benchmark_head = await _benchmark_verdict(
                gold_conn, tenant_id, workspace_id
            )
            ready_pred = (
                "invalid_score_input IS FALSE"
                f" AND {_score_ok('performance_score')}"
                f" AND {_score_ok('potential_score')}"
                " AND LOWER(COALESCE(box_status,''))='ready'"
            )
            benchmark_pred = (
                f"{ready_pred} AND source_mode='benchmark_internal'"
                if "source_mode" in columns
                else "FALSE"
            )
            band_column = (
                "CASE WHEN performance_band_available IN ('high','medium','low') "
                "THEN performance_band_available ELSE NULL END"
                if "performance_band_available" in columns
                else "NULL"
            )
            projection_candidates = _NINE_BOX_REQUIRED | {
                "source_mode",
                "readiness_status",
                "benchmark_raw_score",
                "benchmark_score",
                "readiness_benchmark_count",
                "benchmark_count",
                "benchmark_provenance_status",
                "benchmark_approval_valid",
                "benchmark_materialization_head",
                "performance_band_available",
            }
            projection = ", ".join(
                sorted(column for column in projection_candidates if column in columns)
            )
            # One physical relation scan feeds every aggregate. MATERIALIZED is
            # intentional: PostgreSQL must not inline and rescan the immutable
            # Gold table separately for population, cells and cohorts.
            aggregate_rows = await gold_conn.fetch(
                f"""
                WITH scoped AS MATERIALIZED (
                    SELECT {projection}
                      FROM {relation.sql}
                     WHERE tenant_id::text=$3 AND workspace_id::text=$4
                ),
                cells AS (
                    SELECT box_key::text AS bucket,
                           COUNT(*)::bigint AS employee_count,
                           COUNT(*) FILTER (WHERE {ready_pred})::bigint AS ready_count,
                           COUNT(*) FILTER (WHERE {benchmark_pred})::bigint AS benchmark_count
                      FROM scoped
                     WHERE box_key IS NOT NULL
                       AND {_not_degraded_sql(columns)}
                     GROUP BY box_key
                ),
                eligible AS (
                    SELECT COALESCE(
                        {band_column},
                        CASE WHEN (CASE WHEN performance_score > 5
                                        THEN performance_score / 20.0
                                        ELSE performance_score END) >= 4 THEN 'high'
                             WHEN (CASE WHEN performance_score > 5
                                        THEN performance_score / 20.0
                                        ELSE performance_score END) >= 3 THEN 'medium'
                             ELSE 'low' END
                    ) AS band
                      FROM scoped
                     WHERE invalid_score_input IS FALSE
                       AND {_score_ok('performance_score')}
                ),
                cohorts AS (
                    SELECT band::text AS bucket,
                           COUNT(*)::bigint AS employee_count
                      FROM eligible
                     GROUP BY band
                )
                SELECT 'population'::text AS kind, ''::text AS bucket,
                       COUNT(*)::bigint AS employee_count,
                       0::bigint AS ready_count,
                       0::bigint AS benchmark_count
                  FROM scoped
                UNION ALL
                SELECT 'cell', bucket, employee_count, ready_count, benchmark_count
                  FROM cells
                UNION ALL
                SELECT 'cohort', bucket, employee_count, 0::bigint, 0::bigint
                  FROM cohorts
                """,
                authority_valid,
                benchmark_head,
                tenant_id,
                workspace_id,
            )
            cells: dict[str, dict[str, Any]] = {
                key: {
                    "employee_count": 0,
                    "ready_count": 0,
                    "benchmark_count": 0,
                    "blocked_count": 0,
                    "box_status": "empty",
                }
                for key in BOX_KEYS
            }
            cohorts = {"high": 0, "medium": 0, "low": 0}
            physical_counts: list[int] = []
            seen_cells: set[str] = set()
            seen_cohorts: set[str] = set()
            for row in aggregate_rows:
                kind = str(row["kind"] or "")
                bucket = str(row["bucket"] or "").strip()
                employee = int(row["employee_count"])
                ready = int(row["ready_count"])
                benchmark = int(row["benchmark_count"])
                if min(employee, ready, benchmark) < 0:
                    raise HTTPException(409, "Gold 9-Box contains negative counts")
                if kind == "population":
                    physical_counts.append(employee)
                elif kind == "cell":
                    if bucket == "insufficient_data":
                        continue
                    if bucket not in cells or bucket in seen_cells:
                        raise HTTPException(409, "Gold 9-Box contains an unknown or duplicate cell")
                    seen_cells.add(bucket)
                    cells[bucket] = {
                        "employee_count": employee,
                        "ready_count": ready,
                        "benchmark_count": benchmark,
                        "blocked_count": employee - ready,
                        "box_status": (
                            "benchmark_internal"
                            if benchmark and ready
                            else "ready"
                            if ready
                            else "blocked"
                        ),
                    }
                elif kind == "cohort":
                    if bucket not in cohorts or bucket in seen_cohorts:
                        raise HTTPException(409, "Gold 9-Box contains an unknown or duplicate cohort")
                    seen_cohorts.add(bucket)
                    cohorts[bucket] = employee
                else:
                    raise HTTPException(409, "Gold 9-Box aggregate is incomplete")
            if len(physical_counts) != 1:
                raise HTTPException(409, "Gold 9-Box population aggregate is incomplete")
            physical_count = physical_counts[0]
            if physical_count != manifest["row_count"]:
                raise HTTPException(409, "Gold publication row count is inconsistent")
            _validate_cell_counts(cells, physical_count)
    finally:
        await gold_conn.close()

    classified = sum(int(item["ready_count"]) for item in cells.values())
    assigned = sum(int(item["employee_count"]) for item in cells.values())
    cell_blocked = sum(int(item["blocked_count"]) for item in cells.values())
    envelope = {
        "manifest": manifest,
        "nine_box_counts": cells,
        "cohorts": {
            "high": cohorts["high"],
            "medium": cohorts["medium"],
            "low": cohorts["low"],
        },
        "totals": {
            "population": physical_count,
            "assigned_to_cell": assigned,
            "classified": classified,
            "unclassified": physical_count - classified,
        },
        "blockers": {
            "not_classified": physical_count - classified,
            "assigned_cell_not_ready": cell_blocked,
        },
        "verification_context": {
            "ruleset": "successfactors_talent_population/v1",
            "benchmark_authority_valid": bool(authority_valid),
            "benchmark_head": benchmark_head,
            "relation_schema_digest": manifest["schema_digest"],
        },
    }
    return {**envelope, "digest": canonical_digest(envelope)}


__all__ = (
    "BOX_KEYS",
    "build_talent_9box_baseline",
    "canonical_digest",
    "shadow_enabled",
)
