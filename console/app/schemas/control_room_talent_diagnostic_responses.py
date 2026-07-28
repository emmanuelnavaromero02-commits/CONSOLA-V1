from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from app.schemas.control_room_public_projection import PublicProjectionModel


_COMPONENTS = {
    "performance": ("Desempeno", "Evaluacion de desempeno"),
    "competency": ("Competencias", "Cobertura de competencias"),
    "aspiration": ("Aspiracion", "Preferencias de desarrollo"),
    "roles": ("Roles", "Perfiles de puesto"),
    "learning": ("Aprendizaje", "Planes de aprendizaje"),
    "recruiting": ("Reclutamiento", "Cobertura de vacantes"),
}


class TalentDiagnosticSummary(PublicProjectionModel):
    cpa_ready_employees: int = 0
    cpa_insufficient_employees: int = 0
    components: int = 0
    blocked_components: int = 0
    required_sources_ready: int = 0
    required_sources_total: int = 0


class TalentDiagnosticComponent(PublicProjectionModel):
    component: str | None = None
    purpose: str | None = None
    status: str | None = None
    ready_to_extract: bool | None = None


class TalentDiagnosticBlocker(PublicProjectionModel):
    status: str | None = None
    title: str | None = None
    detail: str | None = None


class TalentDiagnosticSourceCheck(PublicProjectionModel):
    status: str | None = None
    required_ready: int = 0
    required_total: int = 0


def _summary(value: object) -> dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    return {
        "cpa_ready_employees": source.get("cpa_ready_employees"),
        "cpa_insufficient_employees": source.get("cpa_insufficient_employees"),
        "components": source.get("entities"),
        "blocked_components": source.get("blocked_entities"),
        "required_sources_ready": source.get("live_required_ready"),
        "required_sources_total": source.get("live_required_total"),
    }


def _components(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        business = _COMPONENTS.get(str(item.get("id") or "").casefold())
        if business is None:
            continue
        rows.append(
            {
                "component": business[0],
                "purpose": business[1],
                "status": item.get("status"),
                "ready_to_extract": item.get("ready_to_extract"),
            }
        )
    return rows


def _blockers(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        {
            "status": item.get("status"),
            "title": item.get("title"),
            "detail": item.get("detail"),
        }
        for item in value
        if isinstance(item, Mapping)
    ]


def _source_check(raw: Mapping[object, object]) -> dict[str, object]:
    summary = raw.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    preflight = raw.get("live_preflight")
    preflight = preflight if isinstance(preflight, Mapping) else {}
    return {
        "status": preflight.get("status") or summary.get("live_status"),
        "required_ready": summary.get("live_required_ready"),
        "required_total": summary.get("live_required_total"),
    }


class ControlRoomTalentMetadataReadinessResponse(PublicProjectionModel):
    generated_at: str | None = None
    status: str = ""
    summary: TalentDiagnosticSummary = Field(default_factory=TalentDiagnosticSummary)
    components: list[TalentDiagnosticComponent] = Field(default_factory=list)
    blockers: list[TalentDiagnosticBlocker] = Field(default_factory=list)
    source_check: TalentDiagnosticSourceCheck = Field(
        default_factory=TalentDiagnosticSourceCheck
    )

    @classmethod
    def project(cls, value: object) -> ControlRoomTalentMetadataReadinessResponse:
        raw = value if isinstance(value, Mapping) else {}
        source = {
            **raw,
            "summary": _summary(raw.get("summary")),
            "components": _components(raw.get("entities")),
            "blockers": _blockers(raw.get("blockers")),
            "source_check": _source_check(raw),
        }
        return super().project(source)  # type: ignore[return-value]


__all__ = ("ControlRoomTalentMetadataReadinessResponse",)
