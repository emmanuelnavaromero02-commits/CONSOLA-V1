"""
Pre-flight configuration checks for the sap_successfactors cartridge.

Validates the three environments the cartridge depends on (SAP, Postgres,
MinIO) and returns a structured ``degraded`` report when any of them is
incomplete. The checks are cheap (env / settings only — no network
calls) so they run before every extract / preview.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.sap_client import SAPClientError, SapSfClient

_PG_REQUIRED = ("database_url",)

_TALENT_CPA_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "performance",
        "label": "Desempeño",
        "component": "P",
        "required": True,
        "candidates": (
            {
                "entity": "FormHeader",
                "extract_entity": "PerformanceReview",
                "fields_any": ("formDataId", "formSubjectId", "formTemplateId", "status", "lastModifiedDateTime"),
            },
            {
                "entity": "PerformanceReview",
                "extract_entity": "PerformanceReview",
                "fields_any": ("userId", "formDataId", "rating", "lastModifiedDateTime"),
            },
            {
                "entity": "GoalPlan",
                "extract_entity": "GoalPlan",
                "fields_any": ("id", "userId", "state", "lastModifiedDateTime"),
            },
        ),
    },
    {
        "id": "competency",
        "label": "Competencias",
        "component": "C",
        "required": True,
        "candidates": (
            {
                "entity": "CompetencyEntity",
                "extract_entity": "CompetencyEntity",
                "fields_any": ("externalCode", "name", "lastModifiedDateTime"),
            },
            {
                "entity": "SkillProfile",
                "extract_entity": "SkillProfile",
                "fields_any": ("userId", "skill", "rating", "lastModifiedDateTime"),
            },
            {
                "entity": "UserSkill",
                "extract_entity": "UserSkill",
                "fields_any": ("userId", "skill", "proficiency", "lastModifiedDateTime"),
            },
        ),
    },
    {
        "id": "aspiration",
        "label": "Aspiración",
        "component": "A",
        "required": True,
        "candidates": (
            {
                "entity": "CareerWorksheet",
                "extract_entity": "CareerWorksheet",
                "fields_any": ("userId", "role", "readiness", "lastModifiedDateTime"),
            },
            {
                "entity": "CareerInterest",
                "extract_entity": "CareerInterest",
                "fields_any": ("userId", "jobRole", "interest", "lastModifiedDateTime"),
            },
            {
                "entity": "SuccessionNomination",
                "extract_entity": "SuccessionNomination",
                "fields_any": ("userId", "position", "readiness", "lastModifiedDateTime"),
            },
        ),
    },
    {
        "id": "role_requirements",
        "label": "Requisitos de rol",
        "component": "role",
        "required": False,
        "candidates": (
            {
                "entity": "Position",
                "extract_entity": "Position",
                "fields_any": ("code", "positionCode", "jobCode", "department", "lastModifiedDateTime"),
            },
            {
                "entity": "FOJobCode",
                "extract_entity": "FOJobCode",
                "fields_any": ("externalCode", "name", "lastModifiedDateTime"),
            },
        ),
    },
)


def _missing(*names: str) -> list[str]:
    return [n.upper() for n in names if not getattr(settings, n, "")]


def _serialized_security_context(security_context: dict[str, Any] | str | None) -> str | None:
    if isinstance(security_context, str):
        value = security_context.strip()
        return value or None
    if isinstance(security_context, dict):
        return json.dumps(security_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return None


def check_sap(
    *,
    conn_id: str | None = None,
    security_context: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    return SapSfClient(
        conn_id=conn_id,
        security_context=_serialized_security_context(security_context),
    ).configuration_status()


def check_postgres() -> dict[str, Any]:
    missing = _missing(*_PG_REQUIRED)
    return {"component": "postgres", "configured": not missing, "missing": missing}


def check_minio() -> dict[str, Any]:
    missing = _missing("minio_endpoint", "minio_bucket")
    endpoint = str(getattr(settings, "minio_endpoint", "") or "").lower()
    access_key = str(getattr(settings, "minio_access_key", "") or "").strip()
    secret_key = str(getattr(settings, "minio_secret_key", "") or "").strip()
    uses_aws_iam_provider = "amazonaws.com" in endpoint and not (access_key or secret_key)
    if not uses_aws_iam_provider:
        if not access_key:
            missing.append("MINIO_ACCESS_KEY")
        if not secret_key:
            missing.append("MINIO_SECRET_KEY")
    return {"component": "minio", "configured": not missing, "missing": missing}


def preflight_for_extract(
    *,
    conn_id: str | None = None,
    security_context: dict[str, Any] | str | None = None,
) -> dict[str, Any] | None:
    """Return ``None`` when ready, otherwise a degraded report.

    Reports each missing component separately so the caller can render a
    precise error to the user (avoids "Postgres connection failed" hiding
    a missing SAP credential).
    """
    components = [
        check_sap(conn_id=conn_id, security_context=security_context),
        check_postgres(),
        check_minio(),
    ]
    failing = [c for c in components if not c.get("configured", True)]
    if not failing:
        return None
    return {
        "status": "degraded",
        "configured": False,
        "missing": [m for c in failing for m in (c.get("missing") or [])],
        "components": components,
    }


def _candidate_status(
    *,
    candidate: dict[str, Any],
    metadata_entities: dict[str, set[str]],
    client: SapSfClient,
    sample: bool,
) -> dict[str, Any]:
    entity = str(candidate.get("entity") or "")
    extract_entity = str(candidate.get("extract_entity") or entity)
    expected = tuple(str(field) for field in (candidate.get("fields_any") or ()) if field)
    fields = metadata_entities.get(entity)
    if fields is None:
        return {
            "entity": entity,
            "extract_entity": extract_entity,
            "status": "missing",
            "available": False,
            "fields_present": [],
            "fields_missing": list(expected),
            "sample_status": "not_checked",
        }

    present = [field for field in expected if field in fields]
    item: dict[str, Any] = {
        "entity": entity,
        "extract_entity": extract_entity,
        "status": "metadata_ready" if present else "field_blocked",
        "available": bool(present),
        "fields_present": present,
        "fields_missing": [field for field in expected if field not in fields],
        "sample_status": "not_checked",
    }
    if not present or not sample:
        return item

    try:
        rows = client.fetch_entity(entity, select=present[: min(3, len(present))], page_size=1)
        item["sample_status"] = "ready" if rows else "empty"
        item["sample_rows"] = min(len(rows), 1)
        if rows:
            item["status"] = "ready"
    except Exception as exc:  # noqa: BLE001 - upstream/permission errors are blockers, not crashes.
        item["status"] = "permission_blocked"
        item["available"] = False
        item["sample_status"] = "blocked"
        item["error"] = str(exc)[:240]
    return item


def talent_metadata_readiness(
    *,
    conn_id: str | None = None,
    security_context: dict[str, Any] | str | None = None,
    sample: bool = True,
) -> dict[str, Any]:
    """Live SuccessFactors C/P/A metadata preflight for WB-TALENTO.

    This answers the product question "can we calculate real C/P/A and 9-box
    from this tenant yet?" without inventing scores. Entity/field availability
    comes from live ``$metadata``; optional sample reads validate permission.
    """
    sap_status = check_sap(conn_id=conn_id, security_context=security_context)
    if not sap_status.get("configured"):
        return {
            "status": "blocked",
            "configured": False,
            "connection_id": conn_id,
            "components": [],
            "summary": {
                "required_ready": 0,
                "required_total": 3,
                "optional_ready": 0,
                "optional_total": 1,
            },
            "blockers": [
                {
                    "component": "sap",
                    "reason": "configuration_incomplete",
                    "missing": sap_status.get("missing") or [],
                }
            ],
        }

    client = SapSfClient(
        conn_id=conn_id,
        security_context=_serialized_security_context(security_context),
    )
    try:
        metadata_entities = client.metadata_entities()
    except SAPClientError as exc:
        return {
            "status": "blocked",
            "configured": True,
            "connection_id": conn_id,
            "components": [],
            "summary": {
                "required_ready": 0,
                "required_total": 3,
                "optional_ready": 0,
                "optional_total": 1,
            },
            "blockers": [
                {
                    "component": "metadata",
                    "reason": "metadata_unavailable",
                    "error": str(exc)[:240],
                }
            ],
        }

    components: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for requirement in _TALENT_CPA_REQUIREMENTS:
        candidates = [
            _candidate_status(
                candidate=candidate,
                metadata_entities=metadata_entities,
                client=client,
                sample=sample,
            )
            for candidate in requirement["candidates"]
        ]
        ready = next((item for item in candidates if item["status"] in {"ready", "metadata_ready"}), None)
        required = bool(requirement.get("required"))
        status = "ready" if ready else "blocked" if required else "partial"
        component = {
            "id": requirement["id"],
            "label": requirement["label"],
            "component": requirement["component"],
            "required": required,
            "status": status,
            "selected_entity": ready.get("entity") if ready else None,
            "candidates": candidates,
        }
        components.append(component)
        if required and not ready:
            blockers.append(
                {
                    "component": requirement["id"],
                    "reason": "metadata_or_permission_missing",
                    "entities_checked": [item["entity"] for item in candidates],
                }
            )

    required_components = [item for item in components if item["required"]]
    optional_components = [item for item in components if not item["required"]]
    required_ready = sum(1 for item in required_components if item["status"] == "ready")
    optional_ready = sum(1 for item in optional_components if item["status"] == "ready")
    extraction_targets: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str]] = set()
    for component in components:
        for candidate in component["candidates"]:
            if candidate["status"] not in {"ready", "metadata_ready"}:
                continue
            extract_entity = str(candidate.get("extract_entity") or candidate["entity"])
            key = (str(component["id"]), extract_entity)
            if key in seen_targets:
                continue
            seen_targets.add(key)
            extraction_targets.append(
                {
                    "component": component["id"],
                    "component_label": component["label"],
                    "component_code": component["component"],
                    "required": bool(component["required"]),
                    "entity": extract_entity,
                    "odata_entity": candidate["entity"],
                    "status": "ready_to_extract"
                    if candidate["status"] == "ready"
                    else "metadata_ready",
                    "sample_status": candidate.get("sample_status"),
                    "fields_present": candidate.get("fields_present") or [],
                }
            )
    return {
        "status": "ready" if required_ready == len(required_components) else "partial",
        "configured": True,
        "connection_id": conn_id,
        "sample_checked": bool(sample),
        "summary": {
            "required_ready": required_ready,
            "required_total": len(required_components),
            "optional_ready": optional_ready,
            "optional_total": len(optional_components),
            "metadata_entities": len(metadata_entities),
        },
        "components": components,
        "extraction_targets": extraction_targets,
        "blockers": blockers,
        "privacy": {
            "pii_exposed": False,
            "sample_values_returned": False,
        },
    }
