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
        "required_groups": ("performance",),
        "candidates": (
            {
                "entity": "FormHeader",
                "extract_entity": "PerformanceReview",
                "group": "performance",
                "fields_required": ("formDataId", "formSubjectId", "overallRating", "lastModifiedDateTime"),
                "fields_optional": ("formTemplateId", "status", "potentialRating", "formStartDate", "formEndDate"),
            },
            {
                "entity": "PerformanceReview",
                "extract_entity": "PerformanceReview",
                "group": "performance",
                "fields_required": ("userId", "formDataId", "rating", "lastModifiedDateTime"),
            },
            {
                "entity": "GoalPlan",
                "extract_entity": "GoalPlan",
                "group": "goals",
                "fields_required": ("id", "userId", "state", "lastModifiedDateTime"),
                "fields_optional": ("name", "percentComplete", "startDate", "dueDate"),
            },
        ),
    },
    {
        "id": "competency",
        "label": "Competencias",
        "component": "C",
        "required": True,
        "required_groups": ("employee_skill",),
        "candidates": (
            {
                "entity": "CompetencyEntity",
                "extract_entity": "CompetencyEntity",
                "group": "skill_catalog",
                "fields_required": ("externalCode", "name", "lastModifiedDateTime"),
                "fields_optional": ("description", "status"),
            },
            {
                "entity": "SkillProfile",
                "extract_entity": "SkillProfile",
                "group": "employee_skill",
                "fields_required": ("userId", "skill", "proficiency", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "skillName", "rating"),
            },
            {
                "entity": "UserSkill",
                "extract_entity": "UserSkill",
                "group": "employee_skill",
                "fields_required": ("userId", "skill", "proficiency", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "skillName", "rating"),
            },
        ),
    },
    {
        "id": "aspiration",
        "label": "Aspiración",
        "component": "A",
        "required": True,
        "required_groups": ("aspiration",),
        "candidates": (
            {
                "entity": "CareerWorksheet",
                "extract_entity": "CareerWorksheet",
                "group": "aspiration",
                "fields_required": ("userId", "role", "readiness", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "jobRole"),
            },
            {
                "entity": "CareerInterest",
                "extract_entity": "CareerInterest",
                "group": "aspiration",
                "fields_required": ("userId", "jobRole", "interest", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "mobilityPreference"),
            },
            {
                "entity": "SuccessionNomination",
                "extract_entity": "SuccessionNomination",
                "group": "aspiration",
                "fields_required": ("userId", "position", "readiness", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "nominationStatus"),
            },
        ),
    },
    {
        "id": "role_requirements",
        "label": "Requisitos de rol",
        "component": "role",
        "required": False,
        "required_groups": ("role",),
        "candidates": (
            {
                "entity": "Position",
                "extract_entity": "Position",
                "group": "role",
                "fields_required": ("code", "department", "lastModifiedDateTime"),
                "fields_optional": ("positionCode", "jobCode", "externalName_defaultValue", "location", "costCenter"),
            },
            {
                "entity": "FOJobCode",
                "extract_entity": "FOJobCode",
                "group": "role",
                "fields_required": ("externalCode", "lastModifiedDateTime"),
                "fields_optional": ("name", "name_defaultValue", "status"),
            },
        ),
    },
    {
        "id": "learning",
        "label": "Aprendizaje",
        "component": "learning",
        "required": False,
        "required_groups": ("learning",),
        "candidates": (
            {
                "entity": "Item",
                "extract_entity": "LearningItem",
                "group": "learning_catalog",
                "fields_required": ("learningItemId", "title", "lastModifiedDateTime"),
                "fields_optional": ("itemId", "status", "creditHours", "duration", "expirationDate"),
            },
            {
                "entity": "LearningAssignment",
                "extract_entity": "LearningAssignment",
                "group": "learning",
                "fields_required": ("assignmentId", "userId", "itemId", "status", "lastModifiedDateTime"),
                "fields_optional": ("dueDate", "completionDate"),
            },
            {
                "entity": "LearningHistory",
                "extract_entity": "LearningHistory",
                "group": "learning",
                "fields_required": ("historyId", "userId", "itemId", "completionDate", "lastModifiedDateTime"),
                "fields_optional": ("creditHours", "status"),
            },
        ),
    },
    {
        "id": "recruiting",
        "label": "Reclutamiento",
        "component": "recruiting",
        "required": False,
        "required_groups": ("application",),
        "candidates": (
            {
                "entity": "JobApplication",
                "extract_entity": "JobApplication",
                "group": "application",
                "fields_required": ("applicationId", "jobReqId", "candidateId", "applicationStatus", "lastModifiedDateTime"),
                "fields_optional": ("source",),
            },
            {
                "entity": "JobRequisition",
                "extract_entity": "JobRequisition",
                "group": "requisition",
                "fields_required": ("jobReqId", "status", "lastModifiedDateTime"),
                "fields_optional": ("jobTitle", "department", "location"),
            },
            {
                "entity": "Candidate",
                "extract_entity": "Candidate",
                "group": "candidate",
                "fields_required": ("candidateId", "lastModifiedDateTime"),
                "fields_optional": ("firstName", "lastName"),
            },
        ),
    },
    {
        "id": "movement_events",
        "label": "Eventos de movimiento",
        "component": "movement",
        "required": False,
        "required_groups": ("event_reason",),
        "candidates": (
            {
                "entity": "FOEventReason",
                "extract_entity": "FOEventReason",
                "group": "event_reason",
                "fields_required": ("externalCode", "name_defaultValue", "lastModifiedDateTime"),
                "fields_optional": ("event", "eventReasonCategory", "status"),
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
    required = tuple(str(field) for field in (candidate.get("fields_required") or ()) if field)
    if not required:
        required = tuple(str(field) for field in (candidate.get("fields_any") or ()) if field)
    optional = tuple(str(field) for field in (candidate.get("fields_optional") or ()) if field)
    fields = metadata_entities.get(entity)
    if fields is None:
        return {
            "entity": entity,
            "extract_entity": extract_entity,
            "group": str(candidate.get("group") or ""),
            "status": "missing",
            "available": False,
            "fields_present": [],
            "fields_missing": list(required),
            "fields_optional_present": [],
            "fields_optional_missing": list(optional),
            "sample_status": "not_checked",
        }

    present_required = [field for field in required if field in fields]
    missing_required = [field for field in required if field not in fields]
    present_optional = [field for field in optional if field in fields]
    available = not missing_required
    item: dict[str, Any] = {
        "entity": entity,
        "extract_entity": extract_entity,
        "group": str(candidate.get("group") or ""),
        "status": "metadata_ready" if available else "field_blocked",
        "available": available,
        "fields_present": [*present_required, *present_optional],
        "fields_missing": missing_required,
        "fields_required_present": present_required,
        "fields_required_missing": missing_required,
        "fields_optional_present": present_optional,
        "fields_optional_missing": [field for field in optional if field not in fields],
        "sample_status": "not_checked",
    }
    if not available or not sample:
        return item

    try:
        rows = client.fetch_entity(entity, select=item["fields_present"][: min(3, len(item["fields_present"]))], page_size=1)
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


def _empty_talent_summary() -> dict[str, int]:
    required_total = sum(1 for item in _TALENT_CPA_REQUIREMENTS if item.get("required"))
    optional_total = sum(1 for item in _TALENT_CPA_REQUIREMENTS if not item.get("required"))
    return {
        "required_ready": 0,
        "required_total": required_total,
        "optional_ready": 0,
        "optional_total": optional_total,
    }


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
            "summary": _empty_talent_summary(),
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
            "summary": _empty_talent_summary(),
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
        ready_candidates = [item for item in candidates if item["status"] in {"ready", "metadata_ready"}]
        ready_group_ids = {str(item.get("group") or "") for item in ready_candidates if item.get("group")}
        required_groups = set(str(group) for group in requirement.get("required_groups", ()) if group)
        ready = next(iter(ready_candidates), None)
        required = bool(requirement.get("required"))
        has_required_groups = not required_groups or required_groups <= ready_group_ids
        status = (
            "ready"
            if has_required_groups
            else "partial"
            if ready_candidates and not required
            else "blocked"
            if required
            else "partial"
        )
        component = {
            "id": requirement["id"],
            "label": requirement["label"],
            "component": requirement["component"],
            "required": required,
            "status": status,
            "selected_entity": ready.get("entity") if ready else None,
            "ready_groups": sorted(ready_group_ids),
            "required_groups": sorted(required_groups),
            "candidates": candidates,
        }
        components.append(component)
        if required and not has_required_groups:
            blockers.append(
                {
                    "component": requirement["id"],
                    "reason": "metadata_or_permission_missing",
                    "missing_groups": sorted(required_groups - ready_group_ids),
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
