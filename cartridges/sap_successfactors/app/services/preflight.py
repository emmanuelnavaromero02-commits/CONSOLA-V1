"""
Pre-flight configuration checks for the sap_successfactors cartridge.

Validates the three environments the cartridge depends on (SAP, Postgres,
object storage) and returns a structured ``degraded`` report when any of them
is incomplete.  Storage is checked with an authenticated bucket request before
an extraction is allowed to contact SuccessFactors.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.minio_client import check_storage_access, resolve_storage_config
from app.core.sap_client import SapSfClient

_PG_REQUIRED = ("database_url",)
_ALIAS_ENGINE = None

_SAFE_METADATA_FAILURE_CODES = frozenset(
    {
        "metadata_access_denied",
        "metadata_not_found",
        "metadata_query_invalid",
        "metadata_rate_limited",
        "metadata_upstream_unavailable",
    }
)


def _metadata_http_status(exc: Exception) -> int | None:
    """Extract only an allowlisted HTTP status; never return exception text."""

    current: BaseException | None = exc
    for _ in range(4):
        if current is None:
            break
        response = getattr(current, "response", None)
        candidate = getattr(response, "status_code", None) or getattr(
            current, "status_code", None
        )
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            status = None
        if status in {400, 401, 403, 404, 408, 429, 500, 502, 503, 504}:
            return status
        current = current.__cause__ or current.__context__

    # SAPClientError currently wraps RequestException without typed fields.
    # Inspecting the message is only for classification; it is never emitted.
    match = re.search(r"\b(400|401|403|404|408|429|500|502|503|504)\b", str(exc))
    return int(match.group(1)) if match else None


def _safe_metadata_failure_code(exc: Exception) -> str:
    status = _metadata_http_status(exc)
    if status in {401, 403}:
        return "metadata_access_denied"
    if status == 404:
        return "metadata_not_found"
    if status == 400:
        return "metadata_query_invalid"
    if status == 429:
        return "metadata_rate_limited"
    return "metadata_upstream_unavailable"


def _safe_candidate_failure(exc: Exception) -> tuple[str, str, str]:
    code = _safe_metadata_failure_code(exc)
    status, reason = {
        "metadata_access_denied": ("permission_blocked", "permission_denied"),
        "metadata_not_found": ("missing", "entity_not_exposed_in_sap"),
        "metadata_query_invalid": ("query_blocked", "invalid_query"),
        "metadata_rate_limited": ("upstream_blocked", "upstream_unavailable"),
        "metadata_upstream_unavailable": (
            "upstream_blocked",
            "upstream_unavailable",
        ),
    }[code]
    return status, reason, code

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
                "scope": "standard_pmgm",
                "fields_required": ("formDataId", "formSubjectId"),
                "fields_optional": ("formTemplateId", "status", "potentialRating", "formStartDate", "formEndDate"),
            },
            {
                "entity": "PerformanceReview",
                "extract_entity": "PerformanceReview",
                "group": "performance",
                "scope": "tenant_alias",
                "fields_required": ("userId", "formDataId", "rating", "lastModifiedDateTime"),
            },
            {
                "entity": "FormPerfPotSummarySection",
                "extract_entity": "FormPerfPotSummarySection",
                "group": "performance",
                "scope": "standard_pmgm",
                "fields_required": ("formDataId", "formSubjectId"),
                "fields_optional": ("performanceRating", "potentialRating", "lastModifiedDateTime"),
            },
            {
                "entity": "GoalPlan",
                "extract_entity": "GoalPlan",
                "group": "goals",
                "scope": "standard_pmgm",
                "fields_required": ("id", "userId", "state", "lastModifiedDateTime"),
                "fields_optional": ("name", "percentComplete", "startDate", "dueDate"),
            },
            {
                "entity": "SimpleGoal",
                "extract_entity": "SimpleGoal",
                "group": "goals",
                "scope": "standard_pmgm",
                "fields_required": ("id", "userId"),
                "fields_optional": ("name", "state", "percentComplete", "startDate", "dueDate", "lastModifiedDateTime"),
            },
            {
                "entity": "FormObjective",
                "extract_entity": "FormObjective",
                "group": "goals",
                "scope": "standard_pmgm",
                "fields_required": ("formDataId",),
                "fields_optional": ("objectiveId", "userId", "status", "percentComplete", "lastModifiedDateTime"),
            },
            {
                "entity": "FormObjectiveDetails",
                "extract_entity": "FormObjectiveDetails",
                "group": "goals",
                "scope": "standard_pmgm",
                "fields_required": ("formDataId",),
                "fields_optional": ("objectiveDetailId", "objectiveId", "userId", "status", "percentComplete", "lastModifiedDateTime"),
            },
            {
                "entity": "GoalAchievements",
                "extract_entity": "GoalAchievements",
                "group": "goals",
                "scope": "standard_pmgm",
                "fields_required": ("goalId",),
                "fields_optional": ("achievementId", "userId", "status", "achievementPercent", "lastModifiedDateTime"),
            },
            {
                "entity": "CalibrationSession",
                "extract_entity": "CalibrationSession",
                "group": "calibration",
                "scope": "standard_calibration",
                "fields_required": ("sessionId",),
                "fields_optional": ("name", "status", "startDate", "endDate", "lastModifiedDateTime"),
            },
            {
                "entity": "CalibrationSessionSubject",
                "extract_entity": "CalibrationSessionSubject",
                "group": "calibration",
                "scope": "standard_calibration",
                "fields_required": ("sessionId", "userId"),
                "fields_optional": ("subjectId", "performanceRating", "potentialRating", "lastModifiedDateTime"),
            },
            {
                "entity": "CalibrationSubjectRank",
                "extract_entity": "CalibrationSubjectRank",
                "group": "calibration",
                "scope": "standard_calibration",
                "fields_required": ("sessionId", "userId"),
                "fields_optional": ("rankId", "subjectId", "rank", "lastModifiedDateTime"),
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
                "scope": "tenant_mdf",
                "fields_required": ("externalCode", "name", "lastModifiedDateTime"),
                "fields_optional": ("description", "status"),
            },
            {
                "entity": "SkillEntity",
                "extract_entity": "SkillEntity",
                "group": "skill_catalog",
                "scope": "talent_intelligence_hub",
                "fields_required": ("externalCode",),
                "fields_optional": ("name", "description", "status", "lastModifiedDateTime"),
            },
            {
                "entity": "SkillProfile",
                "extract_entity": "SkillProfile",
                "group": "employee_skill",
                "scope": "tenant_mdf",
                "fields_required": ("userId", "skill", "proficiency", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "skillName", "rating"),
            },
            {
                "entity": "UserSkill",
                "extract_entity": "UserSkill",
                "group": "employee_skill",
                "scope": "tenant_mdf",
                "fields_required": ("userId", "skill", "proficiency", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "skillName", "rating"),
            },
            {
                "entity": "WorkerCompetencyAssessment",
                "extract_entity": "WorkerCompetencyAssessment",
                "group": "employee_skill",
                "scope": "standard_or_mdf",
                "fields_required": ("userId",),
                "fields_optional": ("externalCode", "competency", "competencyName", "rating", "proficiency", "lastModifiedDateTime"),
            },
            {
                "entity": "FormCompetency",
                "extract_entity": "FormCompetency",
                "group": "employee_skill",
                "scope": "standard_pmgm",
                "fields_required": ("formDataId",),
                "fields_optional": ("formCompetencyId", "userId", "competency", "competencyName", "rating", "lastModifiedDateTime"),
            },
            {
                "entity": "SysOverallCompetency",
                "extract_entity": "SysOverallCompetency",
                "group": "employee_skill",
                "scope": "standard_pmgm",
                "fields_required": ("userId",),
                "fields_optional": ("externalCode", "competency", "rating", "lastModifiedDateTime"),
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
                "entity": "DevGoal",
                "extract_entity": "DevGoal",
                "group": "aspiration",
                "scope": "standard_career_development",
                "fields_required": ("userId",),
                "fields_optional": ("id", "name", "status", "startDate", "dueDate", "lastModifiedDateTime"),
            },
            {
                "entity": "DevGoalCompetency",
                "extract_entity": "DevGoalCompetency",
                "group": "aspiration",
                "scope": "standard_career_development",
                "fields_required": ("userId",),
                "fields_optional": ("externalCode", "devGoalId", "competency", "competencyName", "lastModifiedDateTime"),
            },
            {
                "entity": "TalentPoolNav",
                "extract_entity": "TalentPoolNav",
                "group": "aspiration",
                "scope": "standard_succession",
                "fields_required": ("userId",),
                "fields_optional": ("externalCode", "poolId", "readiness", "status", "lastModifiedDateTime"),
            },
            {
                "entity": "TalentPool",
                "extract_entity": "TalentPool",
                "group": "succession_catalog",
                "scope": "standard_succession",
                "fields_required": ("poolId",),
                "fields_optional": ("name", "status", "lastModifiedDateTime"),
            },
            {
                "entity": "SuccessionNomination",
                "extract_entity": "SuccessionNomination",
                "group": "aspiration",
                "scope": "standard_succession",
                "fields_required": ("userId", "position"),
                "fields_optional": ("externalCode", "readiness", "nominationStatus", "lastModifiedDateTime"),
            },
            {
                "entity": "CareerWorksheet",
                "extract_entity": "CareerWorksheet",
                "group": "custom_aspiration",
                "scope": "custom_enrichment",
                "standard": False,
                "fields_required": ("userId", "role", "readiness", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "jobRole"),
            },
            {
                "entity": "CareerInterest",
                "extract_entity": "CareerInterest",
                "group": "custom_aspiration",
                "scope": "custom_enrichment",
                "standard": False,
                "fields_required": ("userId", "jobRole", "interest", "lastModifiedDateTime"),
                "fields_optional": ("externalCode", "mobilityPreference"),
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
            {
                "entity": "SkillEntity",
                "extract_entity": "SkillEntity",
                "group": "role_skill_catalog",
                "scope": "talent_intelligence_hub",
                "fields_required": ("externalCode",),
                "fields_optional": ("name", "description", "status", "lastModifiedDateTime"),
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
            {
                "entity": "UserCourses",
                "extract_entity": "UserCourses",
                "group": "learning",
                "scope": "learning_v4",
                "service_family": "learning_v4",
                "fields_required": ("assignmentId", "userId"),
                "fields_optional": ("courseId", "status", "dueDate", "completionDate", "lastModifiedDateTime"),
            },
            {
                "entity": "UserPrograms",
                "extract_entity": "UserPrograms",
                "group": "learning",
                "scope": "learning_v4",
                "service_family": "learning_v4",
                "fields_required": ("assignmentId", "userId"),
                "fields_optional": ("programId", "status", "dueDate", "completionDate", "lastModifiedDateTime"),
            },
            {
                "entity": "LearningEvents",
                "extract_entity": "LearningEvents",
                "group": "learning",
                "scope": "learning_v4",
                "service_family": "learning_v4",
                "fields_required": ("eventId", "userId"),
                "fields_optional": ("itemId", "completionDate", "status", "lastModifiedDateTime"),
            },
            {
                "entity": "Curricula",
                "extract_entity": "Curricula",
                "group": "learning_catalog",
                "scope": "learning_v4",
                "service_family": "learning_v4",
                "fields_required": ("curriculumId",),
                "fields_optional": ("title", "status", "expirationDate", "lastModifiedDateTime"),
            },
            {
                "entity": "CatalogsFeed",
                "extract_entity": "CatalogsFeed",
                "group": "learning_catalog",
                "scope": "learning_v4",
                "service_family": "learning_v4",
                "fields_required": ("itemId",),
                "fields_optional": ("title", "status", "duration", "creditHours", "lastModifiedDateTime"),
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

_TALENT_COMPONENT_ALIAS_GROUP: dict[str, str] = {
    "performance": "performance",
    "competency": "employee_skill",
    "aspiration": "aspiration",
    "role_requirements": "role",
    "learning": "learning",
    "recruiting": "application",
    "movement_events": "event_reason",
}

_DISCOVERY_COMMON_USER_FIELDS = (
    "userId",
    "personIdExternal",
    "personId",
    "employeeId",
    "worker",
    "workerId",
    "subjectUserId",
    "formSubjectId",
)
_DISCOVERY_COMMON_WATERMARK_FIELDS = (
    "lastModifiedDateTime",
    "lastModifiedOn",
    "lastModifiedDate",
    "modifiedDateTime",
    "modifiedDate",
    "createdDateTime",
    "createdDate",
)

_TALENT_METADATA_DISCOVERY_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "component": "performance",
        "extract_entity": "PerformanceReview",
        "group": "performance",
        "name_terms": ("performance", "performancereview", "perf", "formheader", "formperf", "rating", "review"),
        "exclude_terms": ("payment", "paycomponent", "compensation", "payroll"),
        "required_aliases": {
            "formSubjectId": _DISCOVERY_COMMON_USER_FIELDS,
            "overallRating": (
                "overallRating",
                "performanceRating",
                "perfRating",
                "calibratedRating",
                "rating",
                "score",
            ),
        },
        "optional_aliases": {
            "formDataId": ("formDataId", "formId", "reviewId", "externalCode", "id"),
            "potentialRating": ("potentialRating", "potRating", "potential", "potentialScore"),
            "status": ("status", "formStatus", "reviewStatus"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("formDataId", "externalCode", "reviewId", "id"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "performance",
        "extract_entity": "GoalPlan",
        "group": "goals",
        "name_terms": ("goal", "objective"),
        "exclude_terms": ("developmentgoal", "devgoal"),
        "required_aliases": {
            "userId": _DISCOVERY_COMMON_USER_FIELDS,
            "id": ("id", "goalId", "objectiveId", "externalCode"),
        },
        "optional_aliases": {
            "state": ("state", "status", "goalStatus"),
            "percentComplete": ("percentComplete", "completionPercent", "progress", "achievementPercent"),
            "name": ("name", "title", "goalName", "objectiveName"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("id", "goalId", "objectiveId", "externalCode"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "competency",
        "extract_entity": "SkillProfile",
        "group": "employee_skill",
        "name_terms": ("skillprofile", "userskill", "skill", "competencyassessment", "competencyrating"),
        "required_aliases": {
            "userId": _DISCOVERY_COMMON_USER_FIELDS,
            "skill": ("skill", "skillId", "competency", "competencyId", "externalCode"),
        },
        "optional_aliases": {
            "proficiency": ("proficiency", "proficiencyLevel", "rating", "score", "level"),
            "skillName": ("skillName", "competencyName", "name", "title"),
            "externalCode": ("externalCode", "id", "recordId"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("externalCode", "id", "recordId"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "competency",
        "extract_entity": "CompetencyEntity",
        "group": "skill_catalog",
        "name_terms": ("competency", "competence", "skillentity", "skillcatalog", "skilllibrary"),
        "required_aliases": {
            "externalCode": ("externalCode", "id", "skill", "skillId", "competency", "competencyId"),
            "name": ("name", "skillName", "competencyName", "externalName_defaultValue", "title"),
        },
        "optional_aliases": {
            "description": ("description", "desc", "longDescription"),
            "status": ("status", "mdfSystemStatus"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("externalCode", "id", "skillId", "competencyId"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "aspiration",
        "extract_entity": "CareerInterest",
        "group": "aspiration",
        "name_terms": ("career", "aspiration", "interest", "succession", "nomination", "talentpool", "devgoal"),
        "required_aliases": {
            "userId": _DISCOVERY_COMMON_USER_FIELDS,
            "interest": ("interest", "careerInterest", "aspiration", "readiness", "jobRole", "role", "position"),
        },
        "optional_aliases": {
            "jobRole": ("jobRole", "role", "targetRole", "position", "positionCode"),
            "mobilityPreference": ("mobilityPreference", "mobility", "relocation", "locationPreference"),
            "externalCode": ("externalCode", "id", "recordId", "nominationId"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("externalCode", "id", "recordId", "nominationId"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "role_requirements",
        "extract_entity": "Position",
        "group": "role",
        "name_terms": ("position", "jobcode", "role", "jobprofile", "requirements"),
        "required_aliases": {
            "code": ("code", "positionCode", "externalCode", "jobCode", "roleId"),
        },
        "optional_aliases": {
            "department": ("department", "departmentCode", "departmentNav"),
            "jobCode": ("jobCode", "jobClassification", "jobCodeNav"),
            "externalName_defaultValue": ("externalName_defaultValue", "name", "title", "jobTitle"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("code", "positionCode", "externalCode", "jobCode", "roleId"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "learning",
        "extract_entity": "LearningAssignment",
        "group": "learning",
        "name_terms": ("learning", "course", "assignment", "history", "curricula", "catalog", "item"),
        "required_aliases": {
            "userId": _DISCOVERY_COMMON_USER_FIELDS,
            "itemId": ("itemId", "learningItemId", "courseId", "programId", "curriculumId"),
        },
        "optional_aliases": {
            "assignmentId": ("assignmentId", "historyId", "eventId", "externalCode", "id"),
            "status": ("status", "assignmentStatus", "completionStatus"),
            "completionDate": ("completionDate", "completedDate", "finishDate"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("assignmentId", "historyId", "eventId", "externalCode", "id"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "recruiting",
        "extract_entity": "JobApplication",
        "group": "application",
        "name_terms": ("jobapplication", "application", "jobrequisition", "candidate", "recruit"),
        "required_aliases": {
            "applicationId": ("applicationId", "id", "externalCode"),
            "jobReqId": ("jobReqId", "requisitionId", "jobRequisitionId", "reqId"),
            "candidateId": ("candidateId", "candidate", "applicantId", "personIdExternal"),
        },
        "optional_aliases": {
            "applicationStatus": ("applicationStatus", "status", "candidateStatus", "appStatus"),
            "source": ("source", "sourceLabel"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("applicationId", "id", "externalCode"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
    {
        "component": "movement_events",
        "extract_entity": "FOEventReason",
        "group": "event_reason",
        "name_terms": ("eventreason", "event", "movement", "reason"),
        "required_aliases": {
            "externalCode": ("externalCode", "code", "id"),
        },
        "optional_aliases": {
            "name_defaultValue": ("name_defaultValue", "name", "externalName_defaultValue", "label"),
            "event": ("event", "eventCode", "eventCategory"),
            "status": ("status", "mdfSystemStatus"),
            "lastModifiedDateTime": _DISCOVERY_COMMON_WATERMARK_FIELDS,
        },
        "primary_key_candidates": ("externalCode", "code", "id"),
        "watermark_candidates": _DISCOVERY_COMMON_WATERMARK_FIELDS,
    },
)


def _get_alias_engine():
    global _ALIAS_ENGINE
    if _ALIAS_ENGINE is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        _ALIAS_ENGINE = create_engine(settings.database_url, future=True, pool_pre_ping=True)
    return _ALIAS_ENGINE


def _security_scope(security_context: dict[str, Any] | str | None) -> tuple[str, str]:
    if isinstance(security_context, str):
        try:
            parsed = json.loads(security_context)
        except json.JSONDecodeError:
            parsed = {}
        security_context = parsed if isinstance(parsed, dict) else {}
    if not isinstance(security_context, dict):
        return "", ""
    return (
        str(security_context.get("tenant_id") or "").strip(),
        str(security_context.get("workspace_id") or "").strip(),
    )


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    parsed = value
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return []
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in text_value.split(",")]
    if isinstance(parsed, (list, tuple, set)):
        return [str(item).strip() for item in parsed if str(item or "").strip()]
    return [str(parsed).strip()] if str(parsed or "").strip() else []


def _json_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    parsed = value
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return {}
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(val) for key, val in parsed.items() if str(key or "").strip() and str(val or "").strip()}


def _metadata_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _metadata_name_matches(entity_name: str, profile: dict[str, Any]) -> bool:
    normalized = _metadata_key(entity_name)
    exclude_terms = {_metadata_key(term) for term in profile.get("exclude_terms") or ()}
    if any(term and term in normalized for term in exclude_terms):
        return False
    return any(_metadata_key(term) in normalized for term in profile.get("name_terms") or ())


def _field_lookup(fields: set[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for field in fields:
        key = _metadata_key(field)
        if key and key not in lookup:
            lookup[key] = field
    return lookup


def _pick_metadata_field(
    fields: set[str],
    alternatives: tuple[str, ...] | list[str],
) -> str:
    lookup = _field_lookup(fields)
    for alternative in alternatives:
        exact = str(alternative or "").strip()
        if exact in fields:
            return exact
        normalized = _metadata_key(exact)
        if normalized in lookup:
            return lookup[normalized]
    return ""


def _discovery_fields(
    fields: set[str],
    profile: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, str], str, str]:
    required_actual: list[str] = []
    optional_actual: list[str] = []
    field_aliases: dict[str, str] = {}
    for canonical, alternatives in (profile.get("required_aliases") or {}).items():
        actual = _pick_metadata_field(fields, tuple(alternatives))
        if not actual:
            return [], [], {}, "", ""
        required_actual.append(actual)
        if actual != canonical:
            field_aliases[str(canonical)] = actual
    for canonical, alternatives in (profile.get("optional_aliases") or {}).items():
        actual = _pick_metadata_field(fields, tuple(alternatives))
        if not actual or actual in required_actual or actual in optional_actual:
            continue
        optional_actual.append(actual)
        if actual != canonical:
            field_aliases[str(canonical)] = actual
    primary_key = _pick_metadata_field(fields, tuple(profile.get("primary_key_candidates") or ()))
    if not primary_key:
        primary_key = required_actual[0] if required_actual else ""
    watermark = _pick_metadata_field(fields, tuple(profile.get("watermark_candidates") or ()))
    return required_actual, optional_actual, field_aliases, primary_key, watermark


def _discover_talent_alias_candidates(
    metadata_entities: dict[str, set[str]],
) -> dict[str, list[dict[str, Any]]]:
    """Infer safe runtime mappings from live $metadata.

    Discovery is intentionally conservative: it only proposes candidates when
    both the entity name and the minimum field shape match a known Talent
    component. It does not persist config and does not fabricate scores.
    """

    discovered: dict[str, list[dict[str, Any]]] = {}
    for entity, fields in metadata_entities.items():
        if not fields:
            continue
        for profile in _TALENT_METADATA_DISCOVERY_PROFILES:
            if not _metadata_name_matches(entity, profile):
                continue
            required, optional, field_aliases, primary_key, watermark = _discovery_fields(fields, profile)
            if not required:
                continue
            component = str(profile["component"])
            candidate = {
                "entity": entity,
                "odata_entity": entity,
                "extract_entity": str(profile["extract_entity"]),
                "primary_key": primary_key,
                "watermark_field": watermark,
                "group": str(profile["group"]),
                "scope": "metadata_discovery",
                "standard": False,
                "fields_required": tuple(required),
                "fields_optional": tuple(optional),
                "field_aliases": field_aliases,
                "alias_id": "",
                "alias_source": "metadata_discovery",
                "discovery_reason": "entity_name_and_required_fields_matched",
            }
            discovered.setdefault(component, []).append(candidate)
    for component, candidates in discovered.items():
        candidates.sort(
            key=lambda item: (
                str(item.get("alias_source") or ""),
                str(item.get("entity") or ""),
                str(item.get("extract_entity") or ""),
            )
        )
    return discovered


def _merge_talent_alias_candidates(
    *sources: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for source in sources:
        for component, candidates in (source or {}).items():
            for candidate in candidates:
                key = (
                    str(component or ""),
                    str(candidate.get("extract_entity") or candidate.get("entity") or ""),
                    str(candidate.get("odata_entity") or candidate.get("entity") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.setdefault(str(component), []).append(candidate)
    return merged


def _load_talent_alias_candidates(
    *,
    security_context: dict[str, Any] | str | None,
) -> dict[str, list[dict[str, Any]]]:
    """Load approved tenant/workspace mappings for custom SAP MDF/entity names.

    Older deployments do not have the alias table yet; in that case the live
    preflight falls back to the built-in candidate list instead of failing.
    """

    tenant_id, workspace_id = _security_scope(security_context)
    try:
        engine = _get_alias_engine()
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            conn.execute(
                text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
                {"workspace_id": workspace_id},
            )
            exists = conn.execute(
                text("SELECT to_regclass('public.sap_successfactors_tenant_entity_aliases')")
            ).scalar()
            if not exists:
                return {}
            rows = conn.execute(
                text(
                    """
                    SELECT
                        id,
                        tenant_id::TEXT AS tenant_id,
                        workspace_id::TEXT AS workspace_id,
                        component,
                        group_name,
                        canonical_entity,
                        odata_entity,
                        primary_key,
                        watermark_field,
                        required_fields,
                        optional_fields,
                        select_fields,
                        field_aliases,
                        source
                    FROM sap_successfactors_tenant_entity_aliases
                    WHERE cartridge_id = 'sap_successfactors'
                      AND enabled = TRUE
                      AND approved = TRUE
                      AND (tenant_id IS NULL OR tenant_id::TEXT = :tenant_id)
                      AND (workspace_id IS NULL OR workspace_id::TEXT = :workspace_id)
                    ORDER BY
                      CASE WHEN workspace_id::TEXT = :workspace_id THEN 0 ELSE 1 END,
                      CASE WHEN tenant_id::TEXT = :tenant_id THEN 0 ELSE 1 END,
                      updated_at DESC
                    """
                ),
                {"tenant_id": tenant_id, "workspace_id": workspace_id},
            ).mappings().all()
    except Exception:
        return {}

    aliases: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        component = str(row.get("component") or "").strip()
        canonical_entity = str(row.get("canonical_entity") or "").strip()
        odata_entity = str(row.get("odata_entity") or "").strip()
        if not component or not canonical_entity or not odata_entity:
            continue
        required_fields = _json_list(row.get("required_fields")) or _json_list(row.get("select_fields"))
        optional_fields = _json_list(row.get("optional_fields"))
        aliases.setdefault(component, []).append(
            {
                "entity": odata_entity,
                "odata_entity": odata_entity,
                "extract_entity": canonical_entity,
                "primary_key": str(row.get("primary_key") or ""),
                "watermark_field": str(row.get("watermark_field") or ""),
                "group": str(row.get("group_name") or _TALENT_COMPONENT_ALIAS_GROUP.get(component) or component),
                "scope": "tenant_config_alias",
                "standard": False,
                "fields_required": tuple(required_fields),
                "fields_optional": tuple(optional_fields),
                "field_aliases": _json_dict(row.get("field_aliases")),
                "alias_id": str(row.get("id") or ""),
                "alias_source": str(row.get("source") or "tenant_config"),
            }
        )
    return aliases


def _requirement_candidates_with_aliases(
    requirement: dict[str, Any],
    aliases_by_component: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    component_id = str(requirement.get("id") or "")
    aliases = aliases_by_component.get(component_id) or []
    if not aliases:
        return list(requirement["candidates"])
    seen = {str(candidate.get("entity") or "") for candidate in aliases}
    built_in = [
        candidate
        for candidate in requirement["candidates"]
        if str(candidate.get("entity") or "") not in seen
    ]
    return [*aliases, *built_in]


def _candidate_blocker_reason(candidates: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in candidates}
    reasons = {str(item.get("reason") or "") for item in candidates}
    if "permission_blocked" in statuses or "permission_denied" in reasons:
        return "permission_denied"
    if "field_blocked" in statuses or "invalid_select_field" in reasons:
        return "invalid_select_field"
    if "query_blocked" in statuses or "invalid_query" in reasons:
        return "invalid_query"
    if "upstream_blocked" in statuses or "upstream_unavailable" in reasons:
        return "upstream_unavailable"
    if "metadata_ready" in statuses or "ready" in statuses:
        return "missing_required_group"
    if "missing" in statuses or "entity_not_exposed_in_sap" in reasons:
        return "entity_not_exposed_in_sap"
    return "missing_metadata"


def _candidate_blocker_detail(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    fields_missing: list[str] = []
    missing_entities: list[str] = []
    failure_codes: list[str] = []
    invalid_field_entities: list[str] = []
    for item in candidates:
        entity = str(item.get("odata_entity") or item.get("entity") or "").strip()
        if str(item.get("status") or "") == "missing" and entity:
            missing_entities.append(entity)
        if str(item.get("status") or "") == "field_blocked" and entity:
            invalid_field_entities.append(entity)
        failure_code = str(item.get("failure_code") or "").strip()
        if failure_code in _SAFE_METADATA_FAILURE_CODES:
            failure_codes.append(failure_code)
        for field in item.get("fields_missing") or []:
            field_value = str(field or "").strip()
            if field_value and field_value not in fields_missing:
                fields_missing.append(field_value)
    return {
        "fields_missing": fields_missing,
        "missing_entities": sorted(set(missing_entities)),
        "invalid_field_entities": sorted(set(invalid_field_entities)),
        "failure_codes": sorted(set(failure_codes)),
    }


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
    """Configuration-only compatibility check; no provider error is exposed."""

    try:
        resolve_storage_config()
    except Exception as exc:  # StoragePreflightError string is an allowlisted code.
        code = str(exc)
        if code not in {
            "storage_credentials_missing",
            "storage_signature_invalid",
            "storage_access_denied",
            "storage_bucket_missing",
        }:
            code = "storage_credentials_missing"
        return {
            "component": "minio",
            "configured": False,
            "missing": [],
            "code": code,
        }
    return {"component": "minio", "configured": True, "missing": []}


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
        # This is an authenticated object-list probe for cloud storage (bucket
        # creation remains local-MinIO-only). It runs before any OData download.
        check_storage_access(),
    ]
    failing = [c for c in components if not c.get("configured", True)]
    if not failing:
        return None
    codes = [str(c["code"]) for c in failing if c.get("code")]
    return {
        "status": "degraded",
        "configured": False,
        "missing": [m for c in failing for m in (c.get("missing") or [])],
        **({"code": codes[0]} if len(codes) == 1 else {}),
        "codes": codes,
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
    odata_entity = str(candidate.get("odata_entity") or entity)
    extract_entity = str(candidate.get("extract_entity") or entity)
    required = tuple(str(field) for field in (candidate.get("fields_required") or ()) if field)
    if not required:
        required = tuple(str(field) for field in (candidate.get("fields_any") or ()) if field)
    optional = tuple(str(field) for field in (candidate.get("fields_optional") or ()) if field)
    fields = metadata_entities.get(odata_entity)
    field_aliases = _json_dict(candidate.get("field_aliases"))
    if fields is None:
        return {
            "entity": odata_entity,
            "canonical_entity": extract_entity,
            "extract_entity": extract_entity,
            "odata_entity": odata_entity,
            "group": str(candidate.get("group") or ""),
            "scope": str(candidate.get("scope") or ""),
            "service_family": str(candidate.get("service_family") or ""),
            "standard": bool(candidate.get("standard", True)),
            "alias_id": str(candidate.get("alias_id") or ""),
            "alias_source": str(candidate.get("alias_source") or ""),
            "field_aliases": field_aliases,
            "primary_key": str(candidate.get("primary_key") or ""),
            "watermark_field": str(candidate.get("watermark_field") or ""),
            "discovery_reason": str(candidate.get("discovery_reason") or ""),
            "status": "missing",
            "reason": "entity_not_exposed_in_sap",
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
        "entity": odata_entity,
        "canonical_entity": extract_entity,
        "extract_entity": extract_entity,
        "odata_entity": odata_entity,
        "group": str(candidate.get("group") or ""),
        "scope": str(candidate.get("scope") or ""),
        "service_family": str(candidate.get("service_family") or ""),
        "standard": bool(candidate.get("standard", True)),
        "status": "metadata_ready" if available else "field_blocked",
        "reason": "ready" if available else "invalid_select_field",
        "available": available,
        "fields_present": [*present_required, *present_optional],
        "fields_missing": missing_required,
        "fields_required_present": present_required,
        "fields_required_missing": missing_required,
        "fields_optional_present": present_optional,
        "fields_optional_missing": [field for field in optional if field not in fields],
        "alias_id": str(candidate.get("alias_id") or ""),
        "alias_source": str(candidate.get("alias_source") or ""),
        "field_aliases": field_aliases,
        "primary_key": str(candidate.get("primary_key") or ""),
        "watermark_field": str(candidate.get("watermark_field") or ""),
        "discovery_reason": str(candidate.get("discovery_reason") or ""),
        "sample_status": "not_checked",
    }
    if not available or not sample:
        return item

    try:
        rows = client.fetch_entity(odata_entity, select=item["fields_present"][: min(3, len(item["fields_present"]))], page_size=1)
        item["sample_status"] = "ready" if rows else "empty"
        item["sample_rows"] = min(len(rows), 1)
        if rows:
            item["status"] = "ready"
            item["reason"] = "ready"
    except Exception as exc:  # noqa: BLE001 - upstream/permission errors are blockers, not crashes.
        status, reason, code = _safe_candidate_failure(exc)
        item["status"] = status
        item["available"] = False
        item["sample_status"] = "blocked"
        item["reason"] = reason
        item["failure_code"] = code
    return item


def _empty_summary(requirements: tuple[dict[str, Any], ...]) -> dict[str, int]:
    required_total = sum(1 for item in requirements if item.get("required"))
    optional_total = sum(1 for item in requirements if not item.get("required"))
    return {
        "required_ready": 0,
        "required_total": required_total,
        "optional_ready": 0,
        "optional_total": optional_total,
    }


def _entity_metadata_readiness(
    *,
    requirements: tuple[dict[str, Any], ...],
    conn_id: str | None = None,
    security_context: dict[str, Any] | str | None = None,
    sample: bool = True,
    use_aliases: bool = True,
) -> dict[str, Any]:
    """Live SuccessFactors metadata + read preflight over a requirement set.

    Generic engine shared by the talent C/P/A preflight and the people-master
    preflight. Entity/field availability comes from live ``$metadata``; optional
    sample reads (page_size=1) validate OData read permission per entity — a
    401/403 surfaces as ``permission_blocked`` without inventing anything.
    ``use_aliases`` enables the talent-only custom-entity alias discovery; it is
    off for standard-named entities (people master).
    """
    sap_status = check_sap(conn_id=conn_id, security_context=security_context)
    if not sap_status.get("configured"):
        return {
            "status": "blocked",
            "configured": False,
            "connection_id": conn_id,
            "components": [],
            "summary": _empty_summary(requirements),
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
    except Exception as exc:  # noqa: BLE001 - metadata failures are stable blockers.
        failure_code = _safe_metadata_failure_code(exc)
        return {
            "status": "blocked",
            "configured": True,
            "connection_id": conn_id,
            "components": [],
            "summary": _empty_summary(requirements),
            "blockers": [
                {
                    "component": "metadata",
                    "reason": "metadata_unavailable",
                    "failure_code": failure_code,
                }
            ],
        }

    if use_aliases:
        configured_aliases_by_component = _load_talent_alias_candidates(security_context=security_context)
        discovered_aliases_by_component = _discover_talent_alias_candidates(metadata_entities)
        aliases_by_component = _merge_talent_alias_candidates(
            configured_aliases_by_component,
            discovered_aliases_by_component,
        )
    else:
        configured_aliases_by_component = {}
        discovered_aliases_by_component = {}
        aliases_by_component = {}
    components: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for requirement in requirements:
        candidate_definitions = _requirement_candidates_with_aliases(requirement, aliases_by_component)
        candidates = [
            _candidate_status(
                candidate=candidate,
                metadata_entities=metadata_entities,
                client=client,
                sample=sample,
            )
            for candidate in candidate_definitions
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
            "entity": str(ready.get("extract_entity") or ready.get("entity") or "") if ready else None,
            "odata_entity": str(ready.get("entity") or "") if ready else None,
            "fields_found": ready.get("fields_present") if ready else [],
            "fields_missing": [
                field
                for candidate in candidates
                for field in (candidate.get("fields_missing") or [])
                if field
            ],
            "ready_to_extract": bool(ready),
            "ready_groups": sorted(ready_group_ids),
            "required_groups": sorted(required_groups),
            "candidates": candidates,
        }
        components.append(component)
        if required and not has_required_groups:
            blocker_detail = _candidate_blocker_detail(candidates)
            blockers.append(
                {
                    "component": requirement["id"],
                    "reason": _candidate_blocker_reason(candidates),
                    "missing_groups": sorted(required_groups - ready_group_ids),
                    "entities_checked": [item["entity"] for item in candidates],
                    "candidate_statuses": [
                        {
                            "entity": item.get("entity"),
                            "odata_entity": item.get("odata_entity"),
                            "extract_entity": item.get("extract_entity"),
                            "status": item.get("status"),
                            "reason": item.get("reason"),
                            "fields_missing": item.get("fields_missing") or [],
                            "sample_status": item.get("sample_status"),
                            "alias_source": item.get("alias_source") or "",
                        }
                        for item in candidates
                    ],
                    **blocker_detail,
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
                    "ready_to_extract": candidate["status"] == "ready",
                    "sample_status": candidate.get("sample_status"),
                    "scope": candidate.get("scope"),
                    "service_family": candidate.get("service_family"),
                    "standard": candidate.get("standard", True),
                    "fields_present": candidate.get("fields_present") or [],
                    "fields_found": candidate.get("fields_present") or [],
                    "fields_missing": candidate.get("fields_missing") or [],
                    "alias_id": candidate.get("alias_id") or "",
                    "alias_source": candidate.get("alias_source") or "",
                    "field_aliases": candidate.get("field_aliases") or {},
                    "primary_key": candidate.get("primary_key") or "",
                    "watermark_field": candidate.get("watermark_field") or "",
                    "discovery_reason": candidate.get("discovery_reason") or "",
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
            "configured_aliases": sum(len(items) for items in configured_aliases_by_component.values()),
            "discovered_aliases": sum(len(items) for items in discovered_aliases_by_component.values()),
        },
        "components": components,
        "extraction_targets": extraction_targets,
        "blockers": blockers,
        "privacy": {
            "pii_exposed": False,
            "sample_values_returned": False,
        },
    }


# People-master / Employee Central foundation entities. These feed employee_360
# (silver EmpEmployment/EmpJob/PerPersonal -> gold) and therefore the Employee
# Central anomaly signal and the whole talent 9-box. They are standard-named
# OData entities (no alias discovery needed). fields_required are the join/key
# fields the downstream silver/gold actually depends on; the rest are optional.
# User/EmpEmployment/EmpJob are the required linchpin the SAP admin must grant
# OData read on; PerPersonal/PerPerson/FOJobCode/Position enrich but do not block.
_PEOPLE_MASTER_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "user",
        "label": "User (maestro de usuarios)",
        "component": "PM",
        "required": True,
        "required_groups": ("user",),
        "candidates": (
            {
                "entity": "User",
                "extract_entity": "User",
                "group": "user",
                "scope": "standard_ec",
                "fields_required": ("userId", "lastModifiedDateTime"),
                "fields_optional": (
                    "username", "email", "status", "firstName", "lastName",
                    "department", "division", "location", "manager", "hireDate",
                ),
            },
        ),
    },
    {
        "id": "emp_employment",
        "label": "EmpEmployment (empleo / puente persona-usuario)",
        "component": "PM",
        "required": True,
        "required_groups": ("emp_employment",),
        "candidates": (
            {
                "entity": "EmpEmployment",
                "extract_entity": "EmpEmployment",
                "group": "emp_employment",
                "scope": "standard_ec",
                "fields_required": ("personIdExternal", "userId", "lastModifiedDateTime"),
                "fields_optional": ("startDate", "endDate", "assignmentClass", "originalStartDate"),
            },
        ),
    },
    {
        "id": "emp_job",
        "label": "EmpJob (puesto / organización)",
        "component": "PM",
        "required": True,
        "required_groups": ("emp_job",),
        "candidates": (
            {
                "entity": "EmpJob",
                "extract_entity": "EmpJob",
                "group": "emp_job",
                "scope": "standard_ec",
                "fields_required": ("userId", "jobCode", "lastModifiedDateTime"),
                "fields_optional": (
                    "startDate", "endDate", "position", "department", "division",
                    "location", "businessUnit", "company", "costCenter",
                    "managerId", "eventReason",
                ),
            },
        ),
    },
    {
        "id": "per_personal",
        "label": "PerPersonal (datos personales efectivos)",
        "component": "PM",
        "required": False,
        "required_groups": ("per_personal",),
        "candidates": (
            {
                "entity": "PerPersonal",
                "extract_entity": "PerPersonal",
                "group": "per_personal",
                "scope": "standard_ec",
                "fields_required": ("personIdExternal", "startDate"),
                "fields_optional": (
                    "endDate", "firstName", "middleName", "lastName", "gender",
                    "maritalStatus", "nationality", "lastModifiedDateTime",
                ),
            },
        ),
    },
    {
        "id": "per_person",
        "label": "PerPerson (persona core)",
        "component": "PM",
        "required": False,
        "required_groups": ("per_person",),
        "candidates": (
            {
                "entity": "PerPerson",
                "extract_entity": "PerPerson",
                "group": "per_person",
                "scope": "standard_ec",
                "fields_required": ("personIdExternal",),
                "fields_optional": (
                    "personId", "dateOfBirth", "countryOfBirth",
                    "createdDateTime", "lastModifiedDateTime",
                ),
            },
        ),
    },
    {
        "id": "fo_jobcode",
        "label": "FOJobCode (catálogo de puestos)",
        "component": "PM",
        "required": False,
        "required_groups": ("fo_jobcode",),
        "candidates": (
            {
                "entity": "FOJobCode",
                "extract_entity": "FOJobCode",
                "group": "fo_jobcode",
                "scope": "standard_ec",
                "fields_required": ("externalCode",),
                "fields_optional": (
                    "name_defaultValue", "status", "startDate", "endDate",
                    "lastModifiedDateTime",
                ),
            },
        ),
    },
    {
        "id": "position",
        "label": "Position (posiciones)",
        "component": "PM",
        "required": False,
        "required_groups": ("position",),
        "candidates": (
            {
                "entity": "Position",
                "extract_entity": "Position",
                "group": "position",
                "scope": "standard_ec",
                "fields_required": ("code",),
                "fields_optional": (
                    "externalName_defaultValue", "department", "location",
                    "costCenter", "lastModifiedDateTime",
                ),
            },
        ),
    },
)


def talent_metadata_readiness(
    *,
    conn_id: str | None = None,
    security_context: dict[str, Any] | str | None = None,
    sample: bool = True,
) -> dict[str, Any]:
    """Live SuccessFactors C/P/A metadata preflight for WB-TALENTO.

    Answers "can we calculate real C/P/A and 9-box from this tenant yet?"
    without inventing scores. Delegates to the generic engine over the talent
    requirement set (with custom-entity alias discovery enabled).
    """
    return _entity_metadata_readiness(
        requirements=_TALENT_CPA_REQUIREMENTS,
        conn_id=conn_id,
        security_context=security_context,
        sample=sample,
        use_aliases=True,
    )


def people_master_readiness(
    *,
    conn_id: str | None = None,
    security_context: dict[str, Any] | str | None = None,
    sample: bool = True,
) -> dict[str, Any]:
    """Live people-master (Employee Central foundation) permission preflight.

    Read-only. For each people-master entity (User/EmpEmployment/EmpJob required;
    PerPersonal/PerPerson/FOJobCode/Position optional) it checks live ``$metadata``
    field availability and does a page_size=1 sample read to prove OData read
    permission. A 401/403 surfaces as ``permission_blocked`` per entity, giving
    the owner an exact, per-entity checklist to hand the SAP admin BEFORE any
    extraction runs. This is the switch that flips the SF path from blocked to
    ready the instant the people-master grant lands.
    """
    return _entity_metadata_readiness(
        requirements=_PEOPLE_MASTER_REQUIREMENTS,
        conn_id=conn_id,
        security_context=security_context,
        sample=sample,
        use_aliases=False,
    )
