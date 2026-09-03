from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services import auth, control_room_service, llm_client
from app.services.control_room.business_evidence_signing import (
    EvidenceSigningConfigurationError,
    active_evidence_signing_key_id,
    sign_control_room_evidence,
    verify_control_room_evidence,
)
from app.services.db_scope import run_with_db_scope


MODEL = "claude-sonnet-4-6"
RULESET_VERSION = "control-room-grounding-v1"
VERIFIER_VERSION = "control-room-grounding-verifier-v2"
SCHEMA_VERSION = "analysis-envelope-v1"
_ANALYSIS_AGENT_SLUG = "sap_successfactors_talent_monitor"
_ANALYSIS_CARTRIDGE_ID = "sap_successfactors"
_TALENT_DATASET = re.compile(r"^sap_successfactors_talent_[a-z0-9_]+$")
_ATTESTATION_PURPOSE = "control-room-evidence-pack-v1"
_OUTPUT_ATTESTATION_PURPOSE = "control-room-grounded-output-v1"
_EVIDENCE_PATH_PATTERN = r"^(?:data|metadata)\.[a-z0-9_]{1,80}$"
_READY = frozenset({"ready", "gold_ready", "materialized", "complete"})
_PII_KEY = re.compile(
    r"(?i)(?:^|_)(?:full_?name|first_?name|last_?name|display_?name|email|"
    r"pernr|person(?:nel)?_?(?:id|number)|employee_?(?:id|key|number)|"
    r"user_?id|phone|address|salary|compensation|paycompvalue|ssn|curp|rfc)(?:$|_)"
)
_DIRECT_IDENTIFIER = re.compile(
    r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:\d[ -]?){10,}\b)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w.])")
_WORD = re.compile(r"[a-záéíóúñü]{4,}", re.IGNORECASE)
_SAFE_FACT_KEYS = frozenset(
    {
        "affected_count",
        "blocked_count",
        "box_count",
        "box_id",
        "cohort",
        "cohort_count",
        "completeness",
        "count",
        "data_status",
        "headcount",
        "invalid_count",
        "materialized_at",
        "population",
        "population_total",
        "ready_count",
        "readiness_status",
        "row_count",
        "sample_count",
        "source_row_count",
        "total",
    }
)
_SAFE_STRING_FACT_KEYS = frozenset(
    {
        "box_id",
        "cohort",
        "completeness",
        "data_status",
        "materialized_at",
        "readiness_status",
    }
)
_GENERATIVE_CITABLE_FACT_KEYS = frozenset(
    {"box_id", "cohort", "completeness", "data_status", "readiness_status"}
)
_PUBLICATION_FIELDS = (
    "publication_dataset",
    "publication_pipeline_run_id",
    "publication_materialization_run_id",
    "publication_receipt_id",
    "publication_head_generation",
    "publication_object_uri",
    "publication_object_version",
    "publication_object_checksum",
    "publication_schema_digest",
    "publication_evidence_digest",
    "publication_row_count",
    "publication_published_at",
    "publication_outcome_digest",
)
_COUNT_FACT_KEYS = frozenset(
    {
        "affected_count",
        "blocked_count",
        "box_count",
        "cohort_count",
        "count",
        "headcount",
        "invalid_count",
        "population",
        "population_total",
        "ready_count",
        "row_count",
        "sample_count",
        "source_row_count",
        "total",
    }
)
_POPULATION_FACT_KEYS = frozenset(
    {"population", "population_total", "source_row_count", "row_count", "sample_count"}
)
_PROHIBITED_EMPLOYMENT_ACTION = re.compile(
    r"(?i)\b(?:promoci[oó]n|promotion|demotion|desped\w*|fire|firing|"
    r"termina(?:ci[oó]n|r)|terminate|termination|pip|performance improvement|"
    r"compensaci[oó]n|compensation|salario|salary|transfer|writeback|write-back|"
    r"mover autom[aá]ticamente)\b"
)
_BOX_IDS = frozenset(
    {
        "enigma",
        "crecimiento",
        "estrella",
        "dilema",
        "core",
        "alto_impacto",
        "riesgo",
        "efectivo",
        "experto",
    }
)
_BAND_VALUES = frozenset({"high", "medium", "low"})
_STATUS_VALUES = frozenset(
    {
        "ready",
        "gold_ready",
        "materialized",
        "complete",
        "partial",
        "blocked",
        "insufficient_data",
        "unknown",
    }
)
_GENERATIVE_SAFE_TERMS = frozenset(
    {
        "actualizar",
        "analizar",
        "antes",
        "calidad",
        "captura",
        "completitud",
        "confirmar",
        "consultar",
        "datos",
        "decidir",
        "documentar",
        "evidencia",
        "explicar",
        "fuente",
        "hipótesis",
        "hipotesis",
        "investigar",
        "metadatos",
        "podría",
        "podria",
        "recolectar",
        "requiere",
        "revisar",
        "validar",
        "calculation",
        "capture",
        "check",
        "complete",
        "data",
        "evidence",
        "investigate",
        "metadata",
        "quality",
        "review",
        "source",
        "validate",
    }
)
_GENERATIVE_STOP_TERMS = frozenset(
    {
        "como",
        "contra",
        "cuando",
        "desde",
        "esta",
        "este",
        "estos",
        "para",
        "pero",
        "porque",
        "sobre",
        "tiene",
        "with",
        "from",
        "that",
        "this",
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _GeneratedEvidenceRef(_StrictModel):
    evidence_item_id: int = Field(gt=0)
    path: str = Field(pattern=_EVIDENCE_PATH_PATTERN)


class _GeneratedHypothesis(_StrictModel):
    statement: str = Field(min_length=1, max_length=1_000)
    evidence_refs: list[_GeneratedEvidenceRef] = Field(min_length=1, max_length=12)


class _GeneratedOption(_StrictModel):
    label: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=1_000)
    evidence_refs: list[_GeneratedEvidenceRef] = Field(min_length=1, max_length=12)


class _GeneratedAssumption(_StrictModel):
    statement: str = Field(min_length=1, max_length=1_000)
    evidence_refs: list[_GeneratedEvidenceRef] = Field(min_length=1, max_length=12)


class _GeneratedAnalysis(_StrictModel):
    hypotheses: list[_GeneratedHypothesis] = Field(default_factory=list, max_length=10)
    options: list[_GeneratedOption] = Field(default_factory=list, max_length=6)
    assumptions: list[_GeneratedAssumption] = Field(default_factory=list, max_length=12)
    blockers: list[str] = Field(default_factory=list, max_length=12)


ModelCaller = Callable[[str, list[dict[str, Any]], dict[str, Any]], Awaitable[Any]]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _safe_text(value: Any, *, limit: int = 1_000) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:limit]


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _pack_id(item: Mapping[str, Any]) -> int | None:
    candidates = [item.get("evidence_pack_id")]
    metadata = _json_object(item.get("metadata"))
    candidates.append(metadata.get("evidence_pack_id"))
    intelligence = _json_object(item.get("intelligence"))
    metadata_intelligence = _json_object(metadata.get("intelligence"))
    for source in (
        item.get("evidence_pack"),
        metadata.get("evidence_pack"),
        intelligence.get("evidence_pack"),
        metadata_intelligence.get("evidence_pack"),
    ):
        if isinstance(source, Mapping):
            candidates.append(source.get("id"))
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _safe_facts(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    facts: list[dict[str, Any]] = []
    blockers: list[str] = []
    min_cell_size = max(
        2, min(int(os.environ.get("CONTROL_ROOM_MIN_AGGREGATE_CELL_SIZE", "5")), 50)
    )
    for item in items:
        item_id = int(item["id"])
        if str(item.get("source_type") or "").strip().lower() != "gold":
            continue
        for container_name in ("data", "metadata"):
            container = _json_object(item.get(container_name))
            for raw_key, value in sorted(container.items()):
                key = str(raw_key).strip().lower()
                if _PII_KEY.search(key):
                    blockers.append("pii_field_blocked")
                    continue
                if key not in _SAFE_FACT_KEYS or not _finite_scalar(value):
                    continue
                if key in _COUNT_FACT_KEYS and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                ):
                    blockers.append("invalid_aggregate_count")
                    continue
                if key in _COUNT_FACT_KEYS and 0 < value < min_cell_size:
                    blockers.append("small_cell_suppressed")
                    continue
                if isinstance(value, str) and (
                    key not in _SAFE_STRING_FACT_KEYS
                    or _DIRECT_IDENTIFIER.search(value)
                ):
                    blockers.append("unsafe_text_fact_blocked")
                    continue
                if isinstance(value, str):
                    normalized = value.strip().lower()
                    allowed = (
                        normalized in _BOX_IDS
                        if key == "box_id"
                        else normalized in _BAND_VALUES
                        if key == "cohort"
                        else normalized in _STATUS_VALUES
                        if key in {"completeness", "data_status", "readiness_status"}
                        else _parse_time(normalized) is not None
                        if key == "materialized_at"
                        else False
                    )
                    if not allowed:
                        blockers.append("unsafe_text_fact_blocked")
                        continue
                if isinstance(value, str) and len(value) > 120:
                    continue
                facts.append(
                    {
                        "evidence_item_id": item_id,
                        "path": f"{container_name}.{key}",
                        "key": key,
                        "value": value,
                    }
                )
    return facts, sorted(set(blockers))


def _source_status(pack: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    metadata = _json_object(pack.get("metadata"))
    item_metadata = _json_object(item.get("metadata"))
    nested = _json_object(item.get("evidence_pack"))
    for candidate in (
        metadata.get("readiness_status"),
        metadata.get("data_status"),
        item.get("readiness_status"),
        item.get("data_status"),
        item_metadata.get("readiness_status"),
        item_metadata.get("data_status"),
        nested.get("readiness_status"),
    ):
        value = str(candidate or "").strip().lower()
        if value:
            return value
    return "unknown"


def _evidence_as_of(
    pack: Mapping[str, Any], items: list[dict[str, Any]], _item: Mapping[str, Any]
) -> datetime | None:
    pack_meta = _json_object(pack.get("metadata"))
    values: list[Any] = [
        pack_meta.get("freshness_at"),
        pack_meta.get("materialized_at"),
    ]
    for evidence in items:
        if str(evidence.get("source_type") or "").strip().lower() != "gold":
            continue
        metadata = _json_object(evidence.get("metadata"))
        data = _json_object(evidence.get("data"))
        values.extend((metadata.get("materialized_at"), data.get("materialized_at")))
    parsed = [timestamp for value in values if (timestamp := _parse_time(value))]
    # A pack spanning multiple observations is only as fresh as its oldest
    # exact Gold snapshot.  Wrapper/pack creation time must never renew data.
    return min(parsed) if parsed else None


def _publication_binding(pack: Mapping[str, Any]) -> dict[str, Any]:
    nested = _json_object(_json_object(pack.get("metadata")).get("publication"))
    return {
        field.removeprefix("publication_"): (
            pack.get(field)
            if pack.get(field) not in (None, "")
            else nested.get(field.removeprefix("publication_"))
        )
        for field in _PUBLICATION_FIELDS
    }


def _is_supported_talent_pack(pack: Mapping[str, Any]) -> bool:
    metadata = _json_object(pack.get("metadata"))
    source_system = str(metadata.get("source_system") or "").strip().lower()
    dataset = str(metadata.get("dataset") or "").strip().lower()
    return source_system == _ANALYSIS_CARTRIDGE_ID and bool(
        _TALENT_DATASET.fullmatch(dataset)
    )


def _evidence_document(
    *,
    tenant_id: str,
    workspace_id: str,
    item_id: str,
    pack: Mapping[str, Any],
    items: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "item_id": item_id,
        "evidence_pack_id": int(pack["id"]),
        "signal_id": str(pack.get("signal_id") or ""),
        "as_of": as_of.isoformat(),
        "source_types": sorted({str(item.get("source_type") or "") for item in items}),
        "items": [
            {
                "id": int(source["id"]),
                "source_type": str(source.get("source_type") or ""),
                "source_ref": str(source.get("source_ref") or ""),
                "data_digest": _digest(_json_object(source.get("data"))),
                "metadata_digest": _digest(_json_object(source.get("metadata"))),
            }
            for source in items
        ],
        "publication": _publication_binding(pack),
        "facts": facts,
    }


def _attest(document: dict[str, Any]) -> dict[str, str]:
    key_id = active_evidence_signing_key_id()
    payload = _canonical(document)
    signature = sign_control_room_evidence(
        payload, purpose=_ATTESTATION_PURPOSE, key_id=key_id
    )
    if not verify_control_room_evidence(
        payload,
        purpose=_ATTESTATION_PURPOSE,
        key_id=key_id,
        signature=signature,
    ):
        raise EvidenceSigningConfigurationError("collector attestation did not verify")
    return {"key_id": key_id, "signature": signature}


def _claim_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _output_claim_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical verifier record; public projections are derived from this ledger."""

    evidence_refs = _public_evidence_refs(
        row.get("evidence_item_ids"), row.get("evidence_paths")
    )
    return {
        "id": str(row.get("id") or row.get("claim_id") or ""),
        "claim_key": str(row.get("claim_key") or ""),
        "claim_type": str(row.get("claim_type") or ""),
        "statement": str(row.get("statement") or ""),
        "value": _claim_value(row.get("value")),
        "unit": row.get("unit"),
        "population": row.get("population"),
        "evidence_refs": evidence_refs,
        "evidence_item_ids": [ref["evidence_item_id"] for ref in evidence_refs],
        "evidence_paths": [ref["path"] for ref in evidence_refs],
        "formula": row.get("formula"),
        "ruleset_version": row.get("ruleset_version"),
        "verification_status": str(row.get("verification_status") or ""),
        "verification_reason": row.get("verification_reason"),
    }


def _verified_output_document(
    claims: Sequence[Mapping[str, Any]],
    *,
    blockers: Sequence[str],
    model: str,
    ruleset_version: str,
) -> dict[str, Any]:
    records = sorted(
        (_output_claim_record(claim) for claim in claims),
        key=lambda claim: (claim["claim_key"], claim["id"]),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "claims": records,
        "blockers": sorted(set(str(value) for value in blockers)),
        "model": model,
        "ruleset_version": ruleset_version,
        "verifier_version": VERIFIER_VERSION,
    }


def _attest_verified_output(document: dict[str, Any]) -> dict[str, str]:
    payload = _canonical(document)
    digest = hashlib.sha256(payload).hexdigest()
    key_id = active_evidence_signing_key_id()
    signature = sign_control_room_evidence(
        payload, purpose=_OUTPUT_ATTESTATION_PURPOSE, key_id=key_id
    )
    if not verify_control_room_evidence(
        payload,
        purpose=_OUTPUT_ATTESTATION_PURPOSE,
        key_id=key_id,
        signature=signature,
    ):
        raise EvidenceSigningConfigurationError(
            "verifier output attestation did not verify"
        )
    return {"key_id": key_id, "digest": digest, "signature": signature}


def _verified_output_attestation_valid(
    document: dict[str, Any], metadata: Mapping[str, Any], output_digest: Any
) -> bool:
    attestation = _json_object(metadata.get("verifier_attestation"))
    payload = _canonical(document)
    digest = hashlib.sha256(payload).hexdigest()
    return (
        str(output_digest or "") == digest
        and str(attestation.get("digest") or "") == digest
        and verify_control_room_evidence(
            payload,
            purpose=_OUTPUT_ATTESTATION_PURPOSE,
            key_id=str(attestation.get("key_id") or ""),
            signature=str(attestation.get("signature") or ""),
        )
    )


def _validate_source(
    pack: Mapping[str, Any],
    items: list[dict[str, Any]],
    item: Mapping[str, Any],
    facts: list[dict[str, Any]],
    as_of: datetime | None,
) -> list[str]:
    blockers: list[str] = []
    source_types = {
        str(source.get("source_type") or "").strip().lower() for source in items
    }
    if "gold" not in source_types:
        blockers.append("gold_evidence_required")
    pack_metadata = _json_object(pack.get("metadata"))
    status = _source_status(pack, item)
    if status not in _READY:
        blockers.append(f"source_not_ready:{status}")
    if str(pack_metadata.get("completeness") or "").strip().lower() != "complete":
        blockers.append("source_not_complete")
    for evidence in items:
        if str(evidence.get("source_type") or "").strip().lower() != "gold":
            continue
        metadata = _json_object(evidence.get("metadata"))
        data = _json_object(evidence.get("data"))
        item_statuses = {
            str(value or "").strip().lower()
            for value in (
                metadata.get("readiness_status"),
                metadata.get("data_status"),
                data.get("readiness_status"),
                data.get("data_status"),
            )
            if str(value or "").strip()
        }
        if not item_statuses or not item_statuses <= _READY:
            blockers.append("gold_item_not_ready")
        if not (
            _parse_time(metadata.get("materialized_at"))
            or _parse_time(data.get("materialized_at"))
        ):
            blockers.append("gold_item_timestamp_missing")
        item_population = data.get("source_row_count", metadata.get("source_row_count"))
        if (
            not isinstance(item_population, int)
            or isinstance(item_population, bool)
            or item_population < 0
        ):
            blockers.append("gold_item_population_missing")
    if not facts:
        blockers.append("aggregate_evidence_missing")
    if as_of is None:
        blockers.append("evidence_timestamp_missing")
    else:
        ttl_hours = max(
            1, min(int(os.environ.get("CONTROL_ROOM_ANALYSIS_TTL_HOURS", "24")), 168)
        )
        if as_of > datetime.now(UTC) + timedelta(minutes=5):
            blockers.append("evidence_timestamp_in_future")
        if as_of + timedelta(hours=ttl_hours) <= datetime.now(UTC):
            blockers.append("evidence_expired")
    publication = _publication_binding(pack)
    required_publication = {
        key: value for key, value in publication.items() if key != "row_count"
    }
    if not all(required_publication.values()):
        blockers.append("gold_publication_binding_incomplete")
    object_uri = str(publication.get("object_uri") or "")
    if (
        not object_uri.startswith("gs://")
        or "*" in object_uri
        or "?" in object_uri
        or "[" in object_uri
    ):
        blockers.append("gold_publication_object_invalid")
    row_count = publication.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        blockers.append("gold_publication_row_count_invalid")
    for field in (
        "object_checksum",
        "schema_digest",
        "evidence_digest",
        "outcome_digest",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(publication.get(field) or "")):
            blockers.append("gold_publication_binding_invalid")
            break
    return blockers


async def _load_pack(
    conn: Any,
    tenant_id: str,
    workspace_id: str,
    item_id: str,
    preferred_pack_id: int | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    pack_id = preferred_pack_id
    if pack_id is None:
        metadata_value = await conn.fetchval(
            """
            SELECT COALESCE(
                ci.metadata->>'evidence_pack_id',
                ci.metadata->'evidence_pack'->>'id'
            )
              FROM control_room_items ci
             WHERE ci.tenant_id = $1::uuid
               AND ci.workspace_id = $2::uuid
               AND ci.item_id = $3
            """,
            tenant_id,
            workspace_id,
            item_id,
        )
        try:
            parsed_pack_id = int(metadata_value)
        except (TypeError, ValueError):
            parsed_pack_id = 0
        pack_id = parsed_pack_id if parsed_pack_id > 0 else None
    if pack_id is None:
        pack_id = await conn.fetchval(
            """
            SELECT id
              FROM evidence_packs
             WHERE tenant_id = $1::uuid
               AND workspace_id = $2::uuid
               AND signal_id = $3
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            tenant_id,
            workspace_id,
            item_id,
        )
    if pack_id is None:
        return None, []
    pack_row = await conn.fetchrow(
        """
        SELECT pack.id, pack.tenant_id::text AS tenant_id,
               pack.workspace_id::text AS workspace_id, pack.signal_id,
               pack.summary, pack.confidence, pack.metadata, pack.created_at,
               pack.attestation_key_id, pack.attestation_signature,
               pack.attestation_digest, pack.sealed_at,
               binding.dataset AS publication_dataset,
               binding.expected_run_id AS publication_pipeline_run_id,
               binding.materialization_run_id::text AS publication_materialization_run_id,
               binding.receipt_id::text AS publication_receipt_id,
               binding.head_generation AS publication_head_generation,
               binding.object_version AS publication_object_version,
               binding.object_checksum AS publication_object_checksum,
               binding.schema_digest AS publication_schema_digest,
               binding.evidence_digest AS publication_evidence_digest,
               binding.outcome_digest AS publication_outcome_digest
          FROM evidence_packs pack
          LEFT JOIN LATERAL (
              SELECT bound.*
                FROM operational_outcome_bindings bound
               WHERE bound.tenant_id = pack.tenant_id
                 AND bound.workspace_id = pack.workspace_id
                 AND bound.intelligence_run_id::text =
                     pack.metadata->>'intelligence_run_id'
                 AND bound.dataset = pack.metadata->>'dataset'
               ORDER BY bound.created_at DESC
               LIMIT 1
          ) binding ON TRUE
         WHERE pack.tenant_id = $1::uuid
           AND pack.workspace_id = $2::uuid
           AND pack.id = $3
         FOR UPDATE OF pack
        """,
        tenant_id,
        workspace_id,
        int(pack_id),
    )
    if pack_row is None:
        return None, []
    rows = await conn.fetch(
        """
        SELECT id, source_type, source_ref, data, supports_hypothesis,
               strength, metadata, created_at
          FROM evidence_items
         WHERE tenant_id = $1::uuid
           AND workspace_id = $2::uuid
           AND evidence_pack_id = $3
         ORDER BY id
        """,
        tenant_id,
        workspace_id,
        int(pack_id),
    )
    return dict(pack_row), [dict(row) for row in rows]


async def _seal_or_verify_pack(
    conn: Any,
    pack: Mapping[str, Any],
    document: dict[str, Any],
) -> dict[str, str]:
    payload = _canonical(document)
    digest = hashlib.sha256(payload).hexdigest()
    if pack.get("sealed_at") is None:
        raise ValueError("evidence pack was not sealed by the collector")
    key_id = str(pack.get("attestation_key_id") or "")
    signature = str(pack.get("attestation_signature") or "")
    if str(pack.get("attestation_digest") or "") != digest:
        raise ValueError("sealed evidence digest mismatch")
    if not verify_control_room_evidence(
        payload,
        purpose=_ATTESTATION_PURPOSE,
        key_id=key_id,
        signature=signature,
    ):
        raise ValueError("sealed evidence signature mismatch")
    return {"key_id": key_id, "signature": signature}


async def seal_talent_evidence_packs(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    intelligence_run_id: int,
    publication_bindings: list[dict[str, Any]],
) -> dict[str, int]:
    """Seal Talent packs in the deterministic Gold finalization transaction."""

    rows = await conn.fetch(
        """
        SELECT id, signal_id, metadata
          FROM evidence_packs
         WHERE tenant_id = $1::uuid
           AND workspace_id = $2::uuid
           AND metadata->>'source_system' = 'sap_successfactors'
           AND left(
                   metadata->>'dataset',
                   length('sap_successfactors_talent_')
               ) = 'sap_successfactors_talent_'
           AND metadata->>'intelligence_run_id' = $3
           AND sealed_at IS NULL
         ORDER BY id
        """,
        tenant_id,
        workspace_id,
        str(intelligence_run_id),
    )
    sealed = 0
    blocked = 0
    publications = {
        str(binding.get("dataset") or ""): dict(binding)
        for binding in publication_bindings
        if isinstance(binding, Mapping)
    }
    for row in rows:
        item_id = str(row["signal_id"] or "")
        pack_metadata = _json_object(row.get("metadata"))
        dataset = str(pack_metadata.get("dataset") or "")
        publication = publications.get(dataset)
        if publication is None:
            blocked += 1
            continue
        object_uri = str(publication.get("object_uri") or "")
        row_count = publication.get("row_count")
        if (
            not object_uri.startswith("gs://")
            or any(token in object_uri for token in ("*", "?", "["))
            or isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or row_count < 0
        ):
            blocked += 1
            continue
        normalized_publication = {
            "dataset": dataset,
            "pipeline_run_id": str(publication.get("pipeline_run_id") or ""),
            "materialization_run_id": str(
                publication.get("materialization_run_id") or ""
            ),
            "receipt_id": str(publication.get("receipt_id") or ""),
            "head_generation": publication.get("head_generation"),
            "object_uri": object_uri,
            "object_version": str(publication.get("object_version") or ""),
            "object_checksum": str(publication.get("object_checksum") or ""),
            "schema_digest": str(publication.get("schema_digest") or ""),
            "evidence_digest": str(publication.get("evidence_digest") or ""),
            "row_count": row_count,
            "published_at": str(publication.get("published_at") or ""),
            "outcome_digest": str(publication.get("outcome_digest") or ""),
        }
        required_publication = {
            key: value
            for key, value in normalized_publication.items()
            if key != "row_count"
        }
        if not all(required_publication.values()):
            blocked += 1
            continue
        await conn.execute(
            """
            UPDATE evidence_packs
               SET metadata = metadata || jsonb_build_object(
                   'publication', $2::jsonb,
                   'readiness_status', 'ready',
                   'data_status', 'ready',
                   'completeness', 'complete',
                   'materialized_at', $3::text,
                   'source_row_count', $4::bigint
               )
             WHERE id = $1 AND sealed_at IS NULL
            """,
            int(row["id"]),
            json.dumps(normalized_publication),
            normalized_publication["published_at"],
            row_count,
        )
        # Older unsealed baseline packs used the generic ``dataset`` label.
        # Normalize only the exact Gold table citation created by our
        # deterministic collector, never arbitrary/external evidence.
        await conn.execute(
            """
            UPDATE evidence_items
               SET source_type = 'gold',
                   source_ref = $2 || ':baseline:' ||
                       COALESCE(metadata->>'entity_id', id::text)
             WHERE evidence_pack_id = $1
               AND source_type IN ('dataset', 'gold')
               AND source_ref = $2
               AND metadata->>'dataset' = $2
               AND metadata->>'gold_table' = 'gold_' || $2
            """,
            int(row["id"]),
            dataset,
        )
        await conn.execute(
            """
            UPDATE evidence_items
               SET metadata = metadata || jsonb_build_object(
                       'publication', $2::jsonb,
                       'readiness_status', 'ready',
                       'data_status', 'ready',
                       'completeness', 'complete',
                       'materialized_at', $4::text,
                       'source_row_count', $5::bigint
                   ),
                   data = data || jsonb_build_object(
                       'readiness_status', 'ready',
                       'data_status', 'ready',
                       'materialized_at', $4::text,
                       'source_row_count', $5::bigint
                   )
             WHERE evidence_pack_id = $1
               AND source_type = 'gold'
               AND source_ref LIKE $3
            """,
            int(row["id"]),
            json.dumps(normalized_publication),
            dataset + ":%",
            normalized_publication["published_at"],
            row_count,
        )
        pack, evidence_items = await _load_pack(
            conn, tenant_id, workspace_id, item_id, int(row["id"])
        )
        if pack is None or str(pack.get("signal_id") or "") != item_id:
            blocked += 1
            continue
        facts, _privacy_blockers = _safe_facts(evidence_items)
        as_of = _evidence_as_of(pack, evidence_items, {})
        publication = _publication_binding(pack)
        source_types = {
            str(item.get("source_type") or "").strip().lower()
            for item in evidence_items
        }
        exact_binding = bool(
            all(value for key, value in publication.items() if key != "row_count")
        ) and all(
            re.fullmatch(r"[0-9a-f]{64}", str(publication.get(field) or ""))
            for field in (
                "object_checksum",
                "schema_digest",
                "evidence_digest",
                "outcome_digest",
            )
        )
        gold_items_complete = all(
            str(item.get("source_ref") or "").strip()
            and str(item.get("source_ref") or "").startswith(dataset + ":")
            and _json_object(_json_object(item.get("metadata")).get("publication"))
            == normalized_publication
            and (
                _parse_time(_json_object(item.get("metadata")).get("materialized_at"))
                or _parse_time(_json_object(item.get("data")).get("materialized_at"))
            )
            for item in evidence_items
            if str(item.get("source_type") or "").strip().lower() == "gold"
        )
        if (
            "gold" not in source_types
            or as_of is None
            or not exact_binding
            or not gold_items_complete
        ):
            blocked += 1
            continue
        document = _evidence_document(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_id=item_id,
            pack=pack,
            items=evidence_items,
            facts=facts,
            as_of=as_of,
        )
        attestation = _attest(document)
        outcome = await conn.execute(
            """
            UPDATE evidence_packs
               SET attestation_key_id = $2,
                   attestation_signature = $3,
                   attestation_digest = $4,
                   sealed_at = clock_timestamp()
             WHERE id = $1 AND sealed_at IS NULL
            """,
            int(pack["id"]),
            attestation["key_id"],
            attestation["signature"],
            _digest(document),
        )
        if outcome != "UPDATE 1":
            raise RuntimeError("evidence pack collector seal was not persisted")
        sealed += 1
    return {"candidates": len(rows), "sealed": sealed, "blocked": blocked}


_PUBLIC_EVIDENCE_PATH = re.compile(_EVIDENCE_PATH_PATTERN)


def _public_evidence_paths(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else []
    return sorted(
        {
            str(path)
            for path in values
            if _PUBLIC_EVIDENCE_PATH.fullmatch(str(path))
        }
    )


def _public_evidence_refs(item_ids: Any, paths: Any) -> list[dict[str, Any]]:
    """Return only unambiguous, one-to-one item/path citations.

    Parallel arrays are the durable SQL representation.  Treat any legacy or
    tampered cardinality mismatch as unpublished instead of guessing which
    path belongs to which evidence item.
    """

    if not isinstance(item_ids, (list, tuple)) or not isinstance(paths, (list, tuple)):
        return []
    if not item_ids or len(item_ids) != len(paths):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for raw_item_id, raw_path in zip(item_ids, paths, strict=True):
        if isinstance(raw_item_id, bool) or not isinstance(raw_item_id, int):
            return []
        path = str(raw_path)
        pair = (raw_item_id, path)
        if raw_item_id <= 0 or not _PUBLIC_EVIDENCE_PATH.fullmatch(path) or pair in seen:
            return []
        seen.add(pair)
        refs.append({"evidence_item_id": raw_item_id, "path": path})
    return sorted(refs, key=lambda ref: (ref["evidence_item_id"], ref["path"]))


def _generated_evidence_refs(
    values: Sequence[_GeneratedEvidenceRef],
) -> list[dict[str, Any]]:
    refs = [
        {"evidence_item_id": value.evidence_item_id, "path": value.path}
        for value in values
    ]
    return sorted(refs, key=lambda ref: (ref["evidence_item_id"], ref["path"]))


def _claim_evidence_arrays(
    refs: Sequence[Mapping[str, Any]],
) -> tuple[list[int], list[str]]:
    return (
        [int(ref["evidence_item_id"]) for ref in refs],
        [str(ref["path"]) for ref in refs],
    )


def _public_claim(
    row: Mapping[str, Any], *, as_of: str | None, completeness: str
) -> dict[str, Any]:
    value = _claim_value(row.get("value"))
    evidence_refs = _public_evidence_refs(
        row.get("evidence_item_ids"), row.get("evidence_paths")
    )
    return {
        "claim_id": str(row.get("id") or row.get("claim_id") or ""),
        "claim_type": str(row.get("claim_type") or "hypothesis"),
        "statement": _safe_text(row.get("statement")),
        "value": value if _finite_scalar(value) else None,
        "unit": _safe_text(row.get("unit"), limit=80) or None,
        "population": row.get("population"),
        "as_of": as_of,
        "completeness": completeness,
        "evidence_refs": evidence_refs,
        "evidence_item_ids": [ref["evidence_item_id"] for ref in evidence_refs],
        "evidence_paths": [ref["path"] for ref in evidence_refs],
        "verification_status": str(row.get("verification_status") or "pending"),
        "verification_reason": _safe_text(row.get("verification_reason")) or None,
    }


async def _envelope(conn: Any, handoff_id: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT handoff.id, handoff.tenant_id::text AS tenant_id,
               handoff.workspace_id::text AS workspace_id, handoff.item_id,
               handoff.status, handoff.evidence_pack_id,
               handoff.as_of, handoff.grounding_status, handoff.output_digest,
               handoff.blockers,
               handoff.expires_at, handoff.model, handoff.ruleset_version,
               handoff.metadata, pack.sealed_at AS evidence_sealed_at
          FROM agent_handoffs handoff
          JOIN evidence_packs pack ON pack.id = handoff.evidence_pack_id
         WHERE handoff.id = $1::uuid
        """,
        handoff_id,
    )
    if row is None:
        raise HTTPException(404, "analysis not found")
    data = dict(row)
    attestation_valid = False
    facts: list[dict[str, Any]] = []
    pack, evidence_items = await _load_pack(
        conn,
        str(data["tenant_id"]),
        str(data["workspace_id"]),
        str(data["item_id"]),
        int(data["evidence_pack_id"]),
    )
    if pack is not None:
        facts, _ = _safe_facts(evidence_items)
        observed_as_of = _evidence_as_of(pack, evidence_items, {})
        if observed_as_of is not None:
            document = _evidence_document(
                tenant_id=str(data["tenant_id"]),
                workspace_id=str(data["workspace_id"]),
                item_id=str(data["item_id"]),
                pack=pack,
                items=evidence_items,
                facts=facts,
                as_of=observed_as_of,
            )
            try:
                await _seal_or_verify_pack(conn, pack, document)
                attestation_valid = True
            except (EvidenceSigningConfigurationError, ValueError):
                attestation_valid = False
    metadata = _json_object(data.get("metadata"))
    completeness = str(metadata.get("completeness") or "unknown")
    if completeness not in {"complete", "partial", "unknown"}:
        completeness = "unknown"
    claims = await conn.fetch(
        """
        SELECT id, claim_key, claim_type, statement, value, unit, population,
               evidence_item_ids, evidence_paths, formula, ruleset_version,
               verification_status, verification_reason
          FROM agent_claims
         WHERE handoff_id = $1::uuid
         ORDER BY claim_type, claim_key
        """,
        handoff_id,
    )
    as_of = data["as_of"].isoformat() if data.get("as_of") else None
    public_claims = [
        _public_claim(dict(claim), as_of=as_of, completeness=completeness)
        for claim in claims
    ]
    output_blockers = [
        _safe_text(value)
        for value in _json_array(data.get("blockers"))
        if isinstance(value, str)
    ]
    output_document = _verified_output_document(
        [dict(claim) for claim in claims],
        blockers=output_blockers,
        model=str(data.get("model") or ""),
        ruleset_version=str(data.get("ruleset_version") or ""),
    )
    output_digest_valid = str(data.get("output_digest") or "") == _digest(
        output_document
    )
    output_attestation_valid = _verified_output_attestation_valid(
        output_document, metadata, data.get("output_digest")
    )
    valid_evidence_refs = {
        (int(fact["evidence_item_id"]), str(fact["path"])) for fact in facts
    }
    invalid_generated_claim_ids: set[str] = set()
    for claim_row, public_claim in zip(claims, public_claims, strict=True):
        claim_type = public_claim["claim_type"]
        if claim_type not in {"hypothesis", "option", "assumption"}:
            continue
        text = public_claim["statement"]
        if claim_type == "option" and isinstance(public_claim.get("value"), str):
            text = f"{public_claim['value']} {text}"
        reason = _validate_generated_text(
            text,
            public_claim["evidence_refs"],
            facts=facts,
            valid_refs=valid_evidence_refs,
        )
        if reason is not None:
            invalid_generated_claim_ids.add(
                str(claim_row.get("id") or claim_row.get("claim_id") or "")
            )
    verified_public_claims = [
        claim
        for claim in public_claims
        if claim["verification_status"] == "verified"
        and claim["evidence_refs"]
        and claim["claim_id"] not in invalid_generated_claim_ids
    ]
    core_claims = [
        claim
        for claim in verified_public_claims
        if claim["claim_type"] in {"observed", "computed", "hypothesis"}
    ]
    options = [
        {
            "label": _safe_text(claim.get("value"), limit=200),
            "rationale": claim["statement"],
            "evidence_refs": claim["evidence_refs"],
            "evidence_item_ids": claim["evidence_item_ids"],
            "evidence_paths": claim["evidence_paths"],
        }
        for claim in verified_public_claims
        if claim["claim_type"] == "option" and isinstance(claim.get("value"), str)
    ]
    assumptions = [
        {
            "statement": claim["statement"],
            "evidence_refs": claim["evidence_refs"],
            "evidence_item_ids": claim["evidence_item_ids"],
            "evidence_paths": claim["evidence_paths"],
        }
        for claim in verified_public_claims
        if claim["claim_type"] == "assumption"
    ]
    expires_at = data.get("expires_at")
    runtime_expired = bool(expires_at and expires_at <= datetime.now(UTC))
    persisted_status = str(data["status"])
    persisted_grounding = str(data["grounding_status"])
    evidence_sealed = data.get("evidence_sealed_at") is not None and attestation_valid
    verification_inconsistent = (persisted_status == "verified") != (
        persisted_grounding == "verified"
    ) or (
        persisted_status == "verified"
        and (
            not evidence_sealed
            or not output_digest_valid
            or not output_attestation_valid
            or len(verified_public_claims) != len(public_claims)
        )
    )
    publish_verified = (
        persisted_status == "verified"
        and persisted_grounding == "verified"
        and evidence_sealed
        and not verification_inconsistent
        and not runtime_expired
    )
    public_status = (
        "expired"
        if runtime_expired
        else "insufficient_data"
        if verification_inconsistent
        else persisted_status
    )
    public_grounding = (
        "insufficient_data"
        if runtime_expired or verification_inconsistent
        else persisted_grounding
    )
    public_blockers = output_blockers
    if runtime_expired:
        public_blockers = sorted(set(public_blockers + ["evidence_expired"]))
    if verification_inconsistent:
        public_blockers = sorted(
            set(
                public_blockers
                + (["evidence_attestation_invalid"] if not evidence_sealed else [])
                + (["analysis_output_digest_invalid"] if not output_digest_valid else [])
                + (
                    ["verifier_output_attestation_invalid"]
                    if not output_attestation_valid
                    else []
                )
                + (
                    ["unverified_claim_in_verified_handoff"]
                    if len(verified_public_claims) != len(public_claims)
                    else []
                )
                + (
                    ["claim_exact_evidence_validation_failed"]
                    if invalid_generated_claim_ids
                    else []
                )
            )
        )
    return {
        "analysis_run_id": str(data["id"]),
        "status": public_status,
        "evidence_pack_id": int(data["evidence_pack_id"]),
        "as_of": as_of,
        "grounding_status": public_grounding,
        "claims": core_claims if publish_verified else [],
        "hypotheses": [
            claim["statement"]
            for claim in verified_public_claims
            if claim["claim_type"] == "hypothesis"
            and claim["verification_status"] == "verified"
        ]
        if publish_verified
        else [],
        "options": options if publish_verified else [],
        "assumptions": assumptions if publish_verified else [],
        "blockers": public_blockers,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "model": data.get("model"),
        "ruleset_version": data.get("ruleset_version"),
        "recommendation_only": True,
        "no_writeback": True,
    }


def _fact_claims(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    population_by_item: dict[int, int] = {}
    for fact in facts:
        value = fact["value"]
        if (
            fact["key"] in _POPULATION_FACT_KEYS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            population_by_item[fact["evidence_item_id"]] = value
    for index, fact in enumerate(facts[:80]):
        value = fact["value"]
        population = population_by_item.get(fact["evidence_item_id"])
        if isinstance(value, int) and not isinstance(value, bool):
            if fact["key"] in _POPULATION_FACT_KEYS:
                population = value
            elif population is None:
                # A visible number must always carry its population.
                continue
        if fact["key"] in _COUNT_FACT_KEYS:
            unit = "records"
        elif fact["key"] == "materialized_at":
            unit = "timestamp"
        else:
            unit = "category"
        claims.append(
            {
                "id": str(uuid.uuid4()),
                "claim_key": f"observed:{index}:{fact['evidence_item_id']}:{fact['key']}",
                "claim_type": "observed",
                "statement": f"{fact['key']} observado",
                "value": value,
                "unit": unit,
                "population": population,
                "evidence_item_ids": [fact["evidence_item_id"]],
                "evidence_paths": [fact["path"]],
                "verification_status": "verified",
                "verification_reason": "exact_value_from_scoped_evidence_item",
                "formula": None,
                "ruleset_version": None,
            }
        )
    return claims


def _computed_claims(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_item: dict[int, dict[str, dict[str, Any]]] = {}
    for fact in facts:
        by_item.setdefault(int(fact["evidence_item_id"]), {})[str(fact["key"])] = fact
    claims: list[dict[str, Any]] = []
    for item_id, item_facts in sorted(by_item.items()):
        ready = item_facts.get("ready_count")
        population_fact = next(
            (
                item_facts[key]
                for key in (
                    "population_total",
                    "population",
                    "source_row_count",
                    "row_count",
                )
                if key in item_facts
            ),
            None,
        )
        if ready is None or population_fact is None:
            continue
        numerator = ready["value"]
        denominator = population_fact["value"]
        if (
            not isinstance(numerator, int)
            or isinstance(numerator, bool)
            or not isinstance(denominator, int)
            or isinstance(denominator, bool)
            or denominator <= 0
            or numerator < 0
            or numerator > denominator
        ):
            continue
        claims.append(
            {
                "id": str(uuid.uuid4()),
                "claim_key": f"computed:coverage_ratio:{item_id}",
                "claim_type": "computed",
                "statement": "coverage_ratio calculado de forma determinista",
                "value": round(numerator / denominator, 8),
                "unit": "ratio",
                "population": denominator,
                "evidence_item_ids": [item_id, item_id],
                "evidence_paths": [ready["path"], population_fact["path"]],
                "formula": f"ready_count / {population_fact['key']}",
                "ruleset_version": RULESET_VERSION,
                "verification_status": "verified",
                "verification_reason": "recomputed_from_exact_scoped_evidence",
            }
        )
    return claims


def _cited_values(
    facts: Sequence[Mapping[str, Any]],
) -> tuple[set[str], set[str]]:
    numbers: set[str] = set()
    terms: set[str] = set()
    for fact in facts:
        terms.update(_WORD.findall(str(fact["key"]).replace("_", " ").lower()))
        terms.update(_WORD.findall(str(fact["value"]).lower()))
        for token in _NUMBER.findall(str(fact["value"])):
            numbers.add(token.replace(",", "."))
    return numbers, terms


def _literal_fact_token(text: str, value: Any) -> bool:
    normalized = str(value).strip().lower()
    return bool(
        normalized
        and re.search(
            rf"(?<![\w]){re.escape(normalized)}(?![\w])",
            text.lower(),
        )
    )


def _facts_for_exact_refs(
    facts: Sequence[Mapping[str, Any]],
    evidence_refs: Sequence[Mapping[str, Any]],
    valid_refs: set[tuple[int, str]],
) -> tuple[list[Mapping[str, Any]], str | None]:
    requested: list[tuple[int, str]] = []
    for ref in evidence_refs:
        raw_item_id = ref.get("evidence_item_id")
        path = str(ref.get("path") or "")
        if isinstance(raw_item_id, bool) or not isinstance(raw_item_id, int):
            return [], "cross_pack_or_missing_evidence"
        requested.append((raw_item_id, path))
    if not requested or len(requested) != len(set(requested)):
        return [], "cross_pack_or_missing_evidence"
    valid_item_ids = {item_id for item_id, _path in valid_refs}
    if any(item_id not in valid_item_ids for item_id, _path in requested):
        return [], "cross_pack_or_missing_evidence"
    if any(pair not in valid_refs for pair in requested):
        return [], "citation_path_missing_or_unsafe"
    by_ref = {
        (int(fact["evidence_item_id"]), str(fact["path"])): fact for fact in facts
    }
    cited_facts = [by_ref[pair] for pair in requested if pair in by_ref]
    if len(cited_facts) != len(requested):
        return [], "citation_path_missing_or_unsafe"
    return cited_facts, None


def _validate_generated_text(
    text: str,
    evidence_refs: Sequence[Mapping[str, Any]],
    *,
    facts: list[dict[str, Any]],
    valid_refs: set[tuple[int, str]],
) -> str | None:
    cited_facts, reference_error = _facts_for_exact_refs(
        facts, evidence_refs, valid_refs
    )
    if reference_error is not None:
        return reference_error
    if _PII_KEY.search(text) or _DIRECT_IDENTIFIER.search(text):
        return "pii_in_generated_output"
    allowed_numbers, evidence_terms = _cited_values(cited_facts)
    claimed_numbers = {token.replace(",", ".") for token in _NUMBER.findall(text)}
    if not claimed_numbers <= allowed_numbers:
        return "number_without_exact_evidence"
    if claimed_numbers:
        return "numeric_claim_requires_deterministic_projection"
    if _PROHIBITED_EMPLOYMENT_ACTION.search(text):
        return "prohibited_employment_action"
    statement_terms = set(_WORD.findall(text.lower()))
    if evidence_terms and not statement_terms.intersection(evidence_terms):
        return "citation_not_relevant"
    if any(
        str(fact["key"]) not in _GENERATIVE_CITABLE_FACT_KEYS
        or not isinstance(fact["value"], str)
        for fact in cited_facts
    ):
        return "generative_citation_requires_categorical_fact"
    cited_keys = {str(fact["key"]) for fact in cited_facts}
    mentioned_keys = {
        key for key in _SAFE_FACT_KEYS if _literal_fact_token(text, key)
    }
    if not mentioned_keys <= cited_keys:
        return "citation_field_without_exact_reference"
    if any(not _literal_fact_token(text, fact["key"]) for fact in cited_facts):
        return "citation_field_not_stated"
    cited_values = {str(fact["value"]).strip().lower() for fact in cited_facts}
    mentioned_values = {
        value
        for value in (_STATUS_VALUES | _BOX_IDS | _BAND_VALUES)
        if _literal_fact_token(text, value)
    }
    if not mentioned_values <= cited_values:
        return "citation_value_mismatch"
    if any(not _literal_fact_token(text, fact["value"]) for fact in cited_facts):
        return "citation_value_mismatch"
    unsupported_terms = (
        statement_terms
        - evidence_terms
        - _GENERATIVE_SAFE_TERMS
        - _GENERATIVE_STOP_TERMS
    )
    if unsupported_terms:
        return "unsupported_generated_concept"
    return None


async def _default_model_caller(
    system: str, messages: list[dict[str, Any]], user: dict[str, Any]
) -> Any:
    return await llm_client.chat(
        system,
        messages,
        [],
        lambda *_args, **_kwargs: None,
        {},
        model=MODEL,
        max_tokens=2_500,
        temperature=0.0,
        user_context=user,
    )


def _model_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, tuple) and result and isinstance(result[0], str):
        return result[0]
    raise ValueError("model did not return text")


def _prompt(facts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = (
        "Eres un analista de talento de solo recomendación. Recibes únicamente hechos "
        "agregados, verificados y sin PII. Devuelve SOLO JSON estricto con las claves "
        "hypotheses, options, assumptions y blockers. Cada hypothesis y assumption "
        "tiene statement y evidence_refs; cada referencia tiene evidence_item_id y "
        "path exacto. Cada option tiene label, rationale y evidence_refs con la misma "
        "estructura. Solo puedes citar hechos categóricos y debes escribir literalmente "
        "en el texto tanto la clave como el valor exacto de cada path citado. "
        "No inventes números, no cites IDs o paths ausentes, no propongas promociones, PIP, "
        "terminaciones, compensación, movimientos automáticos ni writeback. Si la "
        "evidencia no basta, deja hypotheses/options vacíos y explica blockers. Para "
        "probar relevancia, usa citas item+path; no mezcles el valor de otro campo del "
        "mismo item (por ejemplo readiness_status=blocked nunca autoriza complete)."
    )
    payload = {"verified_aggregate_facts": facts, "recommendation_only": True}
    return system, [
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    ]


async def create_item_analysis(
    item_id: str,
    user: dict[str, Any],
    *,
    model_caller: ModelCaller | None = None,
) -> dict[str, Any]:
    """Create or reuse a durable analysis job without invoking the model.

    ``model_caller`` is retained only for source compatibility with older
    callers.  Model execution is exclusively owned by
    :func:`process_item_analysis`, which is dispatched after the HTTP response.
    """

    del model_caller
    item_id = _safe_text(item_id, limit=240)
    if not item_id:
        raise HTTPException(404, "control room item not found")
    item = await control_room_service.get_item(item_id, user)
    if str(item.get("cartridge") or item.get("cartridge_id") or "").strip() \
            != _ANALYSIS_CARTRIDGE_ID:
        raise HTTPException(409, "grounded analysis is limited to Talent evidence")
    pool = await auth.pool()

    async def _prepare(
        conn: Any, tenant_id: str | None, workspace_id: str
    ) -> dict[str, Any]:
        if not tenant_id:
            raise HTTPException(403, "active tenant is required")
        pack, evidence_items = await _load_pack(
            conn, tenant_id, workspace_id, item_id, _pack_id(item)
        )
        if pack is None:
            raise HTTPException(409, "verified evidence pack is required")
        if str(pack.get("signal_id") or "") != item_id:
            raise HTTPException(409, "evidence pack does not belong to this item")
        if not _is_supported_talent_pack(pack):
            raise HTTPException(409, "unsupported evidence pack for Talent analysis")
        facts, privacy_blockers = _safe_facts(evidence_items)
        as_of = _evidence_as_of(pack, evidence_items, item)
        source_blockers = _validate_source(pack, evidence_items, item, facts, as_of)
        if as_of is None:
            as_of = datetime.now(UTC)
        ttl_hours = max(
            1, min(int(os.environ.get("CONTROL_ROOM_ANALYSIS_TTL_HOURS", "24")), 168)
        )
        expires_at = as_of + timedelta(hours=ttl_hours)
        evidence_document = _evidence_document(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_id=item_id,
            pack=pack,
            items=evidence_items,
            facts=facts,
            as_of=as_of,
        )
        input_digest = _digest(
            {
                "evidence": evidence_document,
                "ruleset_version": RULESET_VERSION,
                "verifier_version": VERIFIER_VERSION,
            }
        )
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"grounded-analysis:{tenant_id}:{workspace_id}:{item_id}",
        )
        existing = await conn.fetchrow(
            """
            SELECT id, status, expires_at, lease_owner, lease_expires_at,
                   analysis_run_id, verifier_run_id
              FROM agent_handoffs
             WHERE tenant_id = $1::uuid
               AND workspace_id = $2::uuid
               AND item_id = $3
               AND evidence_pack_id = $4
               AND input_digest = $5
             ORDER BY version DESC
             LIMIT 1
            """,
            tenant_id,
            workspace_id,
            item_id,
            int(pack["id"]),
            input_digest,
        )
        attestation: dict[str, str] = {}
        try:
            attestation = await _seal_or_verify_pack(conn, pack, evidence_document)
        except EvidenceSigningConfigurationError:
            source_blockers.append("evidence_signing_unavailable")
        except ValueError:
            source_blockers.append("evidence_attestation_invalid")
        blockers = sorted(set(source_blockers + privacy_blockers))
        if existing and str(existing["status"]) == "verified":
            return await _envelope(conn, str(existing["id"]))

        if (
            existing
            and str(existing["status"]) in {"analyzing", "verifying"}
            and existing.get("lease_owner") is not None
            and existing.get("lease_expires_at") is not None
            and existing["lease_expires_at"] > datetime.now(UTC)
        ):
            return await _envelope(conn, str(existing["id"]))

        if existing and str(existing["status"]) in {"analyzing", "verifying"}:
            abandoned_run_ids = [
                int(run_id)
                for run_id in (
                    existing.get("analysis_run_id"),
                    existing.get("verifier_run_id"),
                )
                if run_id is not None
            ]
            if abandoned_run_ids:
                await conn.execute(
                    """
                    UPDATE agent_runs
                       SET status = 'error', finished_at = clock_timestamp(),
                           error_message = 'grounded_analysis_lease_expired'
                     WHERE id = ANY($1::bigint[]) AND status = 'running'
                    """,
                    abandoned_run_ids,
                )

        status = "insufficient_data" if blockers else "ready"
        grounding = "insufficient_data" if blockers else "pending"
        version = int(
            await conn.fetchval(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                  FROM agent_handoffs
                 WHERE tenant_id = $1::uuid
                   AND workspace_id = $2::uuid
                   AND item_id = $3
                """,
                tenant_id,
                workspace_id,
                item_id,
            )
        )
        handoff_id = str(existing["id"]) if existing else str(uuid.uuid4())
        if existing:
            await conn.execute(
                "DELETE FROM agent_claims WHERE handoff_id = $1::uuid",
                handoff_id,
            )
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = $2,
                       grounding_status = $3,
                       blockers = $4::jsonb,
                       output_digest = NULL,
                       hypotheses = '[]'::jsonb,
                       options = '[]'::jsonb,
                       assumptions = '[]'::jsonb,
                       verification_reason = NULL,
                       verified_at = NULL,
                       analysis_run_id = NULL,
                       verifier_run_id = NULL,
                       lease_owner = NULL,
                       lease_expires_at = NULL,
                       expires_at = $5,
                       metadata = $6::jsonb,
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
                status,
                grounding,
                json.dumps(blockers),
                expires_at,
                json.dumps(
                    {
                        "collector_attestation": attestation,
                        "evidence_document_digest": _digest(evidence_document),
                        "verifier_version": VERIFIER_VERSION,
                        "completeness": "complete" if not blockers else "partial",
                        "recommendation_only": True,
                        "no_writeback": True,
                    }
                ),
            )
        else:
            await conn.execute(
                """
                INSERT INTO agent_handoffs (
                    id, tenant_id, workspace_id, item_id, signal_id,
                    evidence_pack_id, consumer_role, status, schema_version,
                    version, input_digest, model, prompt_digest,
                    tool_transcript_digest, ruleset_version, grounding_status,
                    blockers, metadata, as_of, expires_at, created_by_user_id
                ) VALUES (
                    $1::uuid, $2::uuid, $3::uuid, $4, $5, $6,
                    'control_room_analyst', $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16::jsonb, $17::jsonb, $18, $19, $20
                )
                """,
                handoff_id,
                tenant_id,
                workspace_id,
                item_id,
                str(pack.get("signal_id") or item_id),
                int(pack["id"]),
                "ready",
                SCHEMA_VERSION,
                version,
                input_digest,
                MODEL,
                _digest(_prompt(facts)[0]),
                _digest([]),
                RULESET_VERSION,
                "pending",
                json.dumps(blockers),
                json.dumps(
                    {
                        "collector_attestation": attestation,
                        "evidence_document_digest": _digest(evidence_document),
                        "verifier_version": VERIFIER_VERSION,
                        "completeness": "complete" if not blockers else "partial",
                        "recommendation_only": True,
                        "no_writeback": True,
                    }
                ),
                as_of,
                expires_at,
                user.get("id"),
            )
            if status != "ready":
                await conn.execute(
                    """
                    UPDATE agent_handoffs
                       SET status = $2, grounding_status = $3,
                           verification_reason = 'source_validation_failed',
                           updated_at = clock_timestamp()
                     WHERE id = $1::uuid AND status = 'ready'
                    """,
                    handoff_id,
                    status,
                    grounding,
                )
        return await _envelope(conn, handoff_id)

    return await run_with_db_scope(pool, user, _prepare)


def analysis_worker_context(user: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only immutable auth scope needed by an after-response worker."""

    return {
        key: user[key]
        for key in (
            "id",
            "email",
            "role",
            "workspace_role",
            "active_tenant_id",
            "tenant_id",
            "active_workspace_id",
            "workspace_id",
            "active_project_id",
            "project_id",
            "allowed_cartridges",
            "cartridges",
        )
        if user.get(key) is not None
    }


def _lease_seconds() -> int:
    try:
        configured = int(os.environ.get("CONTROL_ROOM_ANALYSIS_LEASE_SECONDS", "300"))
    except ValueError:
        configured = 300
    return max(30, min(configured, 3_600))


async def reconcile_abandoned_analyses(user: dict[str, Any], *, limit: int = 25) -> int:
    """Return expired worker leases to the queue, scoped by Postgres RLS."""

    pool = await auth.pool()

    async def _reconcile(conn: Any, tenant_id: str | None, workspace_id: str) -> int:
        if not tenant_id:
            raise HTTPException(403, "active tenant is required")
        rows = await conn.fetch(
            """
            SELECT id, expires_at, analysis_run_id, verifier_run_id
              FROM agent_handoffs
             WHERE tenant_id = $1::uuid
               AND workspace_id = $2::uuid
               AND status IN ('analyzing', 'verifying')
               AND lease_expires_at <= clock_timestamp()
             ORDER BY lease_expires_at, id
             FOR UPDATE SKIP LOCKED
             LIMIT $3
            """,
            tenant_id,
            workspace_id,
            max(1, min(int(limit), 100)),
        )
        for row in rows:
            run_ids = [
                int(run_id)
                for run_id in (row.get("analysis_run_id"), row.get("verifier_run_id"))
                if run_id is not None
            ]
            if run_ids:
                await conn.execute(
                    """
                    UPDATE agent_runs
                       SET status = 'error', finished_at = clock_timestamp(),
                           error_message = 'grounded_analysis_lease_expired'
                     WHERE id = ANY($1::bigint[]) AND status = 'running'
                    """,
                    run_ids,
                )
            evidence_expired = row["expires_at"] <= datetime.now(UTC)
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = $2,
                       grounding_status = $3,
                       analysis_run_id = NULL,
                       verifier_run_id = NULL,
                       lease_owner = NULL,
                       lease_expires_at = NULL,
                       blockers = CASE WHEN $2 = 'expired'
                           THEN blockers || '["evidence_expired"]'::jsonb
                           ELSE blockers END,
                       metadata = metadata || jsonb_build_object(
                           'last_lease_reconciled_at', clock_timestamp()
                       ),
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                str(row["id"]),
                "expired" if evidence_expired else "ready",
                "insufficient_data" if evidence_expired else "pending",
            )
        return len(rows)

    return await run_with_db_scope(pool, analysis_worker_context(user), _reconcile)


async def _active_analysis_scopes(pool: Any) -> list[dict[str, str]]:
    """Enumerate active scopes without reading handoffs outside their RLS scope."""

    rows = await pool.fetch(
        """
        SELECT w.tenant_id::text AS tenant_id, w.id::text AS workspace_id
          FROM workspaces w
          JOIN tenants t ON t.id = w.tenant_id
         WHERE t.status = 'active'
         ORDER BY w.created_at, w.id
        """
    )
    return [
        {
            "tenant_id": str(row["tenant_id"]),
            "workspace_id": str(row["workspace_id"]),
        }
        for row in rows
    ]


def _system_analysis_context(scope: Mapping[str, str]) -> dict[str, Any]:
    return {
        "active_tenant_id": scope["tenant_id"],
        "tenant_id": scope["tenant_id"],
        "active_workspace_id": scope["workspace_id"],
        "workspace_id": scope["workspace_id"],
        "role": "system",
        "allowed_cartridges": ["sap_successfactors"],
    }


def _round_robin_analysis_scopes(
    scopes: Sequence[dict[str, str]], *, at: datetime | None = None
) -> list[dict[str, str]]:
    """Rotate the deterministic workspace order once per Agent Runner window.

    The scheduler runs every five minutes.  Deriving the cursor from that
    durable time window avoids a process-local cursor that resets on deploy,
    while ensuring a budget smaller than the number of workspaces does not
    always favor the oldest workspace.
    """

    ordered = list(scopes)
    if len(ordered) < 2:
        return ordered
    moment = at or datetime.now(UTC)
    five_minute_window = int(moment.timestamp()) // (5 * 60)
    start = five_minute_window % len(ordered)
    return [*ordered[start:], *ordered[:start]]


async def _ready_analysis_ids(
    pool: Any, user: dict[str, Any], *, limit: int
) -> list[str]:
    async def _load(
        conn: Any, tenant_id: str | None, workspace_id: str
    ) -> list[str]:
        if not tenant_id:
            return []
        rows = await conn.fetch(
            """
            SELECT id::text AS id
              FROM agent_handoffs
             WHERE tenant_id = $1::uuid
               AND workspace_id = $2::uuid
               AND status = 'ready'
               AND expires_at > clock_timestamp()
             ORDER BY updated_at, id
             FOR UPDATE SKIP LOCKED
             LIMIT $3
            """,
            tenant_id,
            workspace_id,
            max(1, min(int(limit), 25)),
        )
        return [str(row["id"]) for row in rows]

    return await run_with_db_scope(pool, user, _load)


async def process_pending_analyses(*, limit: int = 10) -> dict[str, Any]:
    """Autonomously drain durable grounded jobs from the Agent Runner cadence.

    The HTTP request that creates a handoff is only a latency optimization.  A
    process restart cannot strand work: the five-minute Agent Runner tick
    reconciles expired leases and re-dispatches ready rows under each exact RLS
    scope.  Failures are isolated by workspace and reported only by class.
    """

    budget = max(1, min(int(limit), 25))
    remaining = budget
    pool = await auth.pool()
    scopes = _round_robin_analysis_scopes(await _active_analysis_scopes(pool))
    dispatched = 0
    reconciled = 0
    failures: list[dict[str, str]] = []
    reconcile_limit = max(1, (budget + max(len(scopes), 1) - 1) // max(len(scopes), 1))
    pending_scopes = [
        (scope, _system_analysis_context(scope), False) for scope in scopes
    ]
    seen_handoffs: set[str] = set()

    # At most one handoff per workspace is dispatched in each pass.  A busy
    # first workspace therefore cannot consume the full cadence budget before
    # every other active workspace has had a turn.
    while remaining > 0 and pending_scopes:
        next_round: list[tuple[dict[str, str], dict[str, Any], bool]] = []
        for scope, user, was_reconciled in pending_scopes:
            if remaining <= 0:
                break
            try:
                if not was_reconciled:
                    reconciled += await reconcile_abandoned_analyses(
                        user, limit=reconcile_limit
                    )
                handoff_ids = await _ready_analysis_ids(pool, user, limit=1)
                handoff_id = next(
                    (
                        candidate
                        for candidate in handoff_ids
                        if candidate not in seen_handoffs
                    ),
                    None,
                )
                if handoff_id is None:
                    continue
                seen_handoffs.add(handoff_id)
                await process_item_analysis(handoff_id, user)
                dispatched += 1
                remaining -= 1
                next_round.append((scope, user, True))
            except Exception as exc:  # noqa: BLE001 - isolate scope, never leak details
                failures.append(
                    {
                        "workspace_id": scope["workspace_id"],
                        "error_code": type(exc).__name__,
                    }
                )
        pending_scopes = next_round
    return {
        "status": "partial" if failures else "ready",
        "dispatched": dispatched,
        "reconciled": reconciled,
        "failures": failures,
    }


async def _heartbeat_analysis_lease(
    handoff_id: str,
    owner_id: str,
    user: dict[str, Any],
    stop: asyncio.Event,
) -> None:
    interval = max(10, _lease_seconds() // 3)
    pool = await auth.pool()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass

        async def _renew(conn: Any, tenant_id: str | None, workspace_id: str) -> bool:
            if not tenant_id:
                return False
            outcome = await conn.execute(
                """
                UPDATE agent_handoffs
                   SET lease_expires_at = clock_timestamp()
                       + ($4::integer * interval '1 second'),
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                   AND tenant_id = $2::uuid
                   AND workspace_id = $3::uuid
                   AND lease_owner = $5::uuid
                   AND status IN ('analyzing', 'verifying')
                """,
                handoff_id,
                tenant_id,
                workspace_id,
                _lease_seconds(),
                owner_id,
            )
            return outcome == "UPDATE 1"

        try:
            renewed = await run_with_db_scope(pool, user, _renew)
        except Exception:
            return
        if not renewed:
            return


async def process_item_analysis(
    handoff_id: str,
    user: dict[str, Any],
    *,
    model_caller: ModelCaller | None = None,
    worker_id: str | None = None,
) -> None:
    """Claim and execute one persisted analysis job.

    A database lease is the authority boundary.  Concurrent dispatches are
    harmless: only one worker can transition ``ready`` to ``analyzing``.
    """

    try:
        handoff_id = str(uuid.UUID(str(handoff_id)))
    except (TypeError, ValueError, AttributeError):
        return
    scoped_user = analysis_worker_context(user)
    try:
        owner_id = str(uuid.UUID(worker_id)) if worker_id else str(uuid.uuid4())
    except (TypeError, ValueError, AttributeError):
        return
    pool = await auth.pool()
    caller = model_caller or _default_model_caller

    try:
        await reconcile_abandoned_analyses(scoped_user)
    except Exception:
        # Claiming the requested row still provides a safe, target-specific
        # retry path when a broad reconciliation scan cannot run.
        pass

    async def _claim(
        conn: Any, tenant_id: str | None, workspace_id: str
    ) -> dict[str, Any] | None:
        if not tenant_id:
            return None
        row = await conn.fetchrow(
            """
            SELECT id, item_id, evidence_pack_id, input_digest, expires_at,
                   status, lease_owner, lease_expires_at,
                   analysis_run_id, verifier_run_id
              FROM agent_handoffs
             WHERE id = $1::uuid
               AND tenant_id = $2::uuid
               AND workspace_id = $3::uuid
             FOR UPDATE
            """,
            handoff_id,
            tenant_id,
            workspace_id,
        )
        if row is None:
            return None
        current_status = str(row["status"])
        if (
            current_status in {"analyzing", "verifying"}
            and row.get("lease_expires_at") is not None
            and row["lease_expires_at"] <= datetime.now(UTC)
        ):
            run_ids = [
                int(run_id)
                for run_id in (
                    row.get("analysis_run_id"),
                    row.get("verifier_run_id"),
                )
                if run_id is not None
            ]
            if run_ids:
                await conn.execute(
                    """
                    UPDATE agent_runs
                       SET status = 'error', finished_at = clock_timestamp(),
                           error_message = 'grounded_analysis_lease_expired'
                     WHERE id = ANY($1::bigint[]) AND status = 'running'
                    """,
                    run_ids,
                )
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = CASE WHEN expires_at <= clock_timestamp()
                                     THEN 'expired' ELSE 'ready' END,
                       grounding_status = CASE WHEN expires_at <= clock_timestamp()
                                               THEN 'insufficient_data' ELSE 'pending' END,
                       analysis_run_id = NULL, verifier_run_id = NULL,
                       lease_owner = NULL, lease_expires_at = NULL,
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
            )
            if row["expires_at"] <= datetime.now(UTC):
                return None
            current_status = "ready"
        if current_status != "ready":
            return None
        if row["expires_at"] <= datetime.now(UTC):
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = 'expired', grounding_status = 'insufficient_data',
                       blockers = blockers || '["evidence_expired"]'::jsonb,
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
            )
            return None

        item_id = str(row["item_id"])
        pack, evidence_items = await _load_pack(
            conn, tenant_id, workspace_id, item_id, int(row["evidence_pack_id"])
        )
        blockers: list[str] = []
        facts: list[dict[str, Any]] = []
        if pack is None or str(pack.get("signal_id") or "") != item_id:
            blockers.append("evidence_pack_scope_invalid")
        else:
            facts, privacy_blockers = _safe_facts(evidence_items)
            as_of = _evidence_as_of(pack, evidence_items, {})
            blockers.extend(_validate_source(pack, evidence_items, {}, facts, as_of))
            blockers.extend(privacy_blockers)
            if as_of is not None:
                document = _evidence_document(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    item_id=item_id,
                    pack=pack,
                    items=evidence_items,
                    facts=facts,
                    as_of=as_of,
                )
                expected_digest = _digest(
                    {
                        "evidence": document,
                        "ruleset_version": RULESET_VERSION,
                        "verifier_version": VERIFIER_VERSION,
                    }
                )
                if expected_digest != str(row["input_digest"]):
                    blockers.append("analysis_input_digest_mismatch")
                try:
                    await _seal_or_verify_pack(conn, pack, document)
                except EvidenceSigningConfigurationError:
                    blockers.append("evidence_signing_unavailable")
                except ValueError:
                    blockers.append("evidence_attestation_invalid")
        blockers = sorted(set(blockers))
        if blockers:
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = 'insufficient_data',
                       grounding_status = 'insufficient_data',
                       blockers = $2::jsonb,
                       verification_reason = 'source_revalidation_failed',
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
                json.dumps(blockers),
            )
            return None

        agent = await conn.fetchrow(
            """
            SELECT id::text AS id, model, temperature
              FROM agents
             WHERE cartridge_id = $1
               AND slug = $2
               AND is_active = TRUE
               AND (workspace_id = $3::uuid OR workspace_id IS NULL)
               AND (tenant_id = $4::uuid OR tenant_id IS NULL)
             ORDER BY (workspace_id = $3::uuid) DESC,
                      (tenant_id = $4::uuid) DESC, id
             LIMIT 1
            """,
            _ANALYSIS_CARTRIDGE_ID,
            _ANALYSIS_AGENT_SLUG,
            workspace_id,
            tenant_id,
        )
        if not agent:
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = 'insufficient_data',
                       grounding_status = 'insufficient_data',
                       blockers = '["analysis_agent_unavailable"]'::jsonb,
                       verification_reason = 'analysis_agent_unavailable',
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
            )
            return None
        configured_model = str(agent.get("model") or "").strip()
        provider = llm_client._current_provider()
        resolved_model = llm_client._resolve_chat_model(configured_model)
        if (
            provider != "anthropic"
            or configured_model != MODEL
            or resolved_model != MODEL
            or float(agent.get("temperature") or 0.0) != 0.0
        ):
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = 'insufficient_data',
                       grounding_status = 'insufficient_data',
                       blockers = '["analysis_model_provider_not_allowed"]'::jsonb,
                       verification_reason = 'analysis_model_provider_not_allowed',
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
            )
            return None
        agent_id = str(agent["id"])
        user_id = user.get("id")
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            user_id = None
        analysis_run_id = await conn.fetchval(
            """
            INSERT INTO agent_runs (
                agent_id, user_id, input_messages, tenant_id, workspace_id
            ) VALUES ($1::uuid, $2, $3::jsonb, $4::uuid, $5::uuid)
            RETURNING id
            """,
            agent_id,
            user_id,
            json.dumps(
                [
                    {
                        "role": "system",
                        "content": "grounded_control_room_analysis",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "handoff_id": handoff_id,
                                "input_digest": str(row["input_digest"]),
                            },
                            sort_keys=True,
                        ),
                    },
                ]
            ),
            tenant_id,
            workspace_id,
        )
        outcome = await conn.execute(
            """
            UPDATE agent_handoffs
               SET status = 'analyzing', grounding_status = 'pending',
                   analysis_run_id = $2, verifier_run_id = NULL,
                   producer_agent_id = $5::uuid, producer_run_id = $2,
                   lease_owner = $3::uuid,
                   lease_expires_at = clock_timestamp()
                       + ($4::integer * interval '1 second'),
                   attempt_count = attempt_count + 1,
                   metadata = metadata || jsonb_build_object(
                       'last_analysis_started_at', clock_timestamp(),
                       'producer_role', 'talent_analyst',
                       'producer_provider', $6::text,
                       'producer_model', $7::text
                   ),
                   updated_at = clock_timestamp()
             WHERE id = $1::uuid AND status = 'ready'
            """,
            handoff_id,
            int(analysis_run_id),
            owner_id,
            _lease_seconds(),
            str(agent_id),
            provider,
            resolved_model,
        )
        if outcome != "UPDATE 1":
            await conn.execute(
                """
                UPDATE agent_runs
                   SET status = 'cancelled', finished_at = clock_timestamp(),
                       error_message = 'analysis_claim_lost'
                 WHERE id = $1 AND status = 'running'
                """,
                int(analysis_run_id),
            )
            return None
        return {
            "agent_id": str(agent_id),
            "analysis_run_id": int(analysis_run_id),
            "facts": facts,
            "evidence_refs": {
                (int(fact["evidence_item_id"]), str(fact["path"])) for fact in facts
            },
        }

    prepared = await run_with_db_scope(pool, scoped_user, _claim)
    if prepared is None:
        return

    stop_heartbeat = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_analysis_lease(handoff_id, owner_id, scoped_user, stop_heartbeat)
    )
    try:
        facts = prepared["facts"]
        system, messages = _prompt(facts)
        generated: _GeneratedAnalysis | None = None
        failure: str | None = None
        raw_text = ""
        try:
            raw_text = _model_text(await caller(system, messages, scoped_user))
            generated = _GeneratedAnalysis.model_validate_json(raw_text)
        except (
            ValidationError,
            ValueError,
            TypeError,
            llm_client.LLMConfigurationError,
            llm_client.LLMProviderError,
        ):
            failure = "model_output_unavailable_or_invalid"

        async def _begin_verification(
            conn: Any, tenant_id: str | None, workspace_id: str
        ) -> int | None:
            if not tenant_id:
                return None
            locked = await conn.fetchrow(
                """
                SELECT status, lease_owner
                  FROM agent_handoffs
                 WHERE id = $1::uuid
                   AND tenant_id = $2::uuid
                   AND workspace_id = $3::uuid
                 FOR UPDATE
                """,
                handoff_id,
                tenant_id,
                workspace_id,
            )
            if (
                locked is None
                or str(locked["status"]) != "analyzing"
                or str(locked.get("lease_owner") or "") != owner_id
            ):
                return None
            await conn.execute(
                """
                UPDATE agent_runs
                   SET status = $2, finished_at = clock_timestamp(),
                       output_text = $3, error_message = $4
                 WHERE id = $1 AND status = 'running'
                """,
                prepared["analysis_run_id"],
                "ok" if generated is not None else "error",
                f"model_output_sha256:{_digest(raw_text)}" if raw_text else "",
                failure,
            )
            verifier_run_id = await conn.fetchval(
                """
                INSERT INTO agent_runs (
                    agent_id, user_id, input_messages, tenant_id, workspace_id
                ) VALUES ($1::uuid, $2, $3::jsonb, $4::uuid, $5::uuid)
                RETURNING id
                """,
                prepared["agent_id"],
                user.get("id") if isinstance(user.get("id"), int) else None,
                json.dumps(
                    [
                        {
                            "role": "system",
                            "content": "deterministic_grounding_verifier",
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "handoff_id": handoff_id,
                                    "model_output_digest": _digest(raw_text),
                                },
                                sort_keys=True,
                            ),
                        },
                    ]
                ),
                tenant_id,
                workspace_id,
            )
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = 'verifying', verifier_run_id = $2,
                       lease_expires_at = clock_timestamp()
                           + ($3::integer * interval '1 second'),
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
                int(verifier_run_id),
                _lease_seconds(),
            )
            return int(verifier_run_id)

        verifier_run_id = await run_with_db_scope(
            pool, scoped_user, _begin_verification
        )
        if verifier_run_id is None:
            return

        observed_claims = _fact_claims(facts)
        computed_claims = _computed_claims(facts)
        hypothesis_claims: list[dict[str, Any]] = []
        option_claims: list[dict[str, Any]] = []
        assumption_claims: list[dict[str, Any]] = []
        output_blockers: list[str] = []
        if generated is not None:
            valid_refs = set(prepared["evidence_refs"])
            for index, hypothesis in enumerate(generated.hypotheses):
                evidence_refs = _generated_evidence_refs(hypothesis.evidence_refs)
                reason = _validate_generated_text(
                    hypothesis.statement,
                    evidence_refs,
                    facts=facts,
                    valid_refs=valid_refs,
                )
                if reason:
                    output_blockers.append(reason)
                    continue
                evidence_item_ids, evidence_paths = _claim_evidence_arrays(
                    evidence_refs
                )
                hypothesis_claims.append(
                    {
                        "id": str(uuid.uuid4()),
                        "claim_key": f"hypothesis:{index}",
                        "claim_type": "hypothesis",
                        "statement": hypothesis.statement,
                        "value": None,
                        "unit": None,
                        "population": None,
                        "evidence_item_ids": evidence_item_ids,
                        "evidence_paths": evidence_paths,
                        "verification_status": "verified",
                        "verification_reason": "exact_item_path_value_verified",
                        "formula": None,
                        "ruleset_version": None,
                    }
                )
            for index, option in enumerate(generated.options):
                evidence_refs = _generated_evidence_refs(option.evidence_refs)
                reason = _validate_generated_text(
                    f"{option.label} {option.rationale}",
                    evidence_refs,
                    facts=facts,
                    valid_refs=valid_refs,
                )
                if reason:
                    output_blockers.append(reason)
                    continue
                evidence_item_ids, evidence_paths = _claim_evidence_arrays(
                    evidence_refs
                )
                option_claims.append(
                    {
                        "id": str(uuid.uuid4()),
                        "claim_key": f"option:{index}",
                        "claim_type": "option",
                        "statement": option.rationale,
                        "value": option.label,
                        "unit": None,
                        "population": None,
                        "evidence_item_ids": evidence_item_ids,
                        "evidence_paths": evidence_paths,
                        "verification_status": "verified",
                        "verification_reason": "exact_item_path_value_verified",
                        "formula": None,
                        "ruleset_version": None,
                    }
                )
            for index, assumption in enumerate(generated.assumptions):
                evidence_refs = _generated_evidence_refs(assumption.evidence_refs)
                reason = _validate_generated_text(
                    assumption.statement,
                    evidence_refs,
                    facts=facts,
                    valid_refs=valid_refs,
                )
                if reason:
                    output_blockers.append(reason)
                    continue
                evidence_item_ids, evidence_paths = _claim_evidence_arrays(
                    evidence_refs
                )
                assumption_claims.append(
                    {
                        "id": str(uuid.uuid4()),
                        "claim_key": f"assumption:{index}",
                        "claim_type": "assumption",
                        "statement": assumption.statement,
                        "value": None,
                        "unit": None,
                        "population": None,
                        "evidence_item_ids": evidence_item_ids,
                        "evidence_paths": evidence_paths,
                        "verification_status": "verified",
                        "verification_reason": "exact_item_path_value_verified",
                        "formula": None,
                        "ruleset_version": None,
                    }
                )
            if generated.blockers:
                output_blockers.append("model_reported_insufficient_data")
            if generated.hypotheses and not hypothesis_claims:
                output_blockers.append("no_hypothesis_passed_verification")
            if generated.options and not option_claims:
                output_blockers.append("no_option_passed_verification")
        if failure:
            output_blockers.append(failure)

        all_claims = (
            observed_claims
            + computed_claims
            + hypothesis_claims
            + option_claims
            + assumption_claims
        )
        grounding_status: Literal["verified", "insufficient_data"] = (
            "verified"
            if generated is not None and not output_blockers
            else "insufficient_data"
        )
        status = "verified" if grounding_status == "verified" else "insufficient_data"
        output_document = _verified_output_document(
            all_claims,
            blockers=output_blockers,
            model=MODEL,
            ruleset_version=RULESET_VERSION,
        )
        verifier_attestation: dict[str, str] = {}
        if grounding_status == "verified":
            try:
                verifier_attestation = _attest_verified_output(output_document)
            except EvidenceSigningConfigurationError:
                output_blockers.append("verifier_output_signing_unavailable")
                grounding_status = "insufficient_data"
                status = "insufficient_data"
                output_document = _verified_output_document(
                    all_claims,
                    blockers=output_blockers,
                    model=MODEL,
                    ruleset_version=RULESET_VERSION,
                )

        async def _finish(conn: Any, tenant_id: str | None, workspace_id: str) -> None:
            if not tenant_id:
                return
            locked = await conn.fetchrow(
                """
                SELECT status, lease_owner, verifier_run_id
                  FROM agent_handoffs
                 WHERE id = $1::uuid
                   AND tenant_id = $2::uuid
                   AND workspace_id = $3::uuid
                 FOR UPDATE
                """,
                handoff_id,
                tenant_id,
                workspace_id,
            )
            if (
                locked is None
                or str(locked["status"]) != "verifying"
                or str(locked.get("lease_owner") or "") != owner_id
                or int(locked.get("verifier_run_id") or 0) != verifier_run_id
            ):
                return
            for claim in all_claims:
                await conn.execute(
                    """
                    INSERT INTO agent_claims (
                        id, tenant_id, workspace_id, handoff_id, claim_key,
                        claim_type, statement, value, unit, population,
                        evidence_item_ids, evidence_paths, formula, ruleset_version,
                        verification_status, verification_reason, verified_at
                    ) VALUES (
                        $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7,
                        $8::jsonb, $9, $10, $11::bigint[], $12::text[], $13,
                        $14, $15, $16, clock_timestamp()
                    )
                    ON CONFLICT (handoff_id, claim_key) DO NOTHING
                    """,
                    claim["id"],
                    tenant_id,
                    workspace_id,
                    handoff_id,
                    claim["claim_key"],
                    claim["claim_type"],
                    claim["statement"],
                    json.dumps(claim["value"], allow_nan=False),
                    claim["unit"],
                    claim["population"],
                    claim["evidence_item_ids"],
                    claim["evidence_paths"],
                    claim.get("formula"),
                    claim.get("ruleset_version"),
                    claim["verification_status"],
                    claim["verification_reason"],
                )
            await conn.execute(
                """
                UPDATE agent_runs
                   SET status = 'ok', finished_at = clock_timestamp(),
                       output_text = $2, error_message = NULL
                 WHERE id = $1 AND status = 'running'
                """,
                verifier_run_id,
                f"grounding_status:{grounding_status}",
            )
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = $2, grounding_status = $3, output_digest = $4,
                       hypotheses = '[]'::jsonb, options = '[]'::jsonb,
                       assumptions = '[]'::jsonb, blockers = $5::jsonb,
                       metadata = CASE
                           WHEN $6::jsonb = '{}'::jsonb
                           THEN metadata - 'verifier_attestation'
                           ELSE (metadata - 'verifier_attestation')
                                || jsonb_build_object(
                                    'verifier_attestation', $6::jsonb
                                )
                       END,
                       verification_reason = $7,
                       verified_at = CASE WHEN $2 = 'verified'
                                          THEN clock_timestamp() END,
                       lease_owner = NULL, lease_expires_at = NULL,
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
                status,
                grounding_status,
                _digest(output_document),
                json.dumps(sorted(set(output_blockers))),
                json.dumps(verifier_attestation),
                "all_claims_verified"
                if status == "verified"
                else "unverifiable_output_rejected",
            )

        await run_with_db_scope(pool, scoped_user, _finish)
    except Exception:

        async def _fail(conn: Any, tenant_id: str | None, workspace_id: str) -> None:
            if not tenant_id:
                return
            row = await conn.fetchrow(
                """
                SELECT analysis_run_id, verifier_run_id
                  FROM agent_handoffs
                 WHERE id = $1::uuid
                   AND tenant_id = $2::uuid
                   AND workspace_id = $3::uuid
                   AND lease_owner = $4::uuid
                   AND status IN ('analyzing', 'verifying')
                 FOR UPDATE
                """,
                handoff_id,
                tenant_id,
                workspace_id,
                owner_id,
            )
            if row is None:
                return
            run_ids = [
                int(run_id)
                for run_id in (row.get("analysis_run_id"), row.get("verifier_run_id"))
                if run_id is not None
            ]
            if run_ids:
                await conn.execute(
                    """
                    UPDATE agent_runs
                       SET status = 'error', finished_at = clock_timestamp(),
                           error_message = 'grounded_analysis_worker_failed'
                     WHERE id = ANY($1::bigint[]) AND status = 'running'
                    """,
                    run_ids,
                )
            await conn.execute(
                """
                UPDATE agent_handoffs
                   SET status = 'insufficient_data',
                       grounding_status = 'insufficient_data',
                       blockers = blockers || '["analysis_worker_failed"]'::jsonb,
                       verification_reason = 'analysis_worker_failed',
                       lease_owner = NULL, lease_expires_at = NULL,
                       updated_at = clock_timestamp()
                 WHERE id = $1::uuid
                """,
                handoff_id,
            )

        try:
            await run_with_db_scope(pool, scoped_user, _fail)
        except Exception:
            pass
    finally:
        stop_heartbeat.set()
        await heartbeat


async def get_item_analysis(
    item_id: str,
    user: dict[str, Any],
    *,
    analysis_run_id: str | None = None,
) -> dict[str, Any]:
    item_id = _safe_text(item_id, limit=240)
    if not item_id:
        raise HTTPException(404, "control room item not found")
    # RLS isolates the workspace, but item visibility can be narrower for a
    # non-admin owner.  Reuse the canonical Control Room reader before looking
    # up a handoff so a guessed item or handoff UUID cannot bypass that owner
    # boundary.
    await control_room_service.get_item(item_id, user)
    pool = await auth.pool()

    async def _load(
        conn: Any, tenant_id: str | None, workspace_id: str
    ) -> dict[str, Any]:
        if not tenant_id:
            raise HTTPException(403, "active tenant is required")
        if analysis_run_id is None:
            handoff_id = await conn.fetchval(
                """
                SELECT id::text
                  FROM agent_handoffs
                 WHERE tenant_id = $1::uuid
                   AND workspace_id = $2::uuid
                   AND item_id = $3
                 ORDER BY version DESC
                 LIMIT 1
                """,
                tenant_id,
                workspace_id,
                item_id,
            )
        else:
            handoff_id = await conn.fetchval(
                """
                SELECT id::text
                  FROM agent_handoffs
                 WHERE tenant_id = $1::uuid
                   AND workspace_id = $2::uuid
                   AND item_id = $3
                   AND id = $4::uuid
                """,
                tenant_id,
                workspace_id,
                item_id,
                analysis_run_id,
            )
        if not handoff_id:
            raise HTTPException(404, "analysis not found")
        return await _envelope(conn, str(handoff_id))

    return await run_with_db_scope(pool, user, _load)


__all__ = (
    "analysis_worker_context",
    "create_item_analysis",
    "get_item_analysis",
    "process_item_analysis",
    "process_pending_analyses",
    "reconcile_abandoned_analyses",
    "seal_talent_evidence_packs",
)
