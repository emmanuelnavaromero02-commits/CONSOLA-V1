from __future__ import annotations

from fastapi import FastAPI
import json
import pytest

from app.routers import control_room as routes
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_projection import project_business_item
from app.services.control_room.business_state_overlay import overlay_business_state
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_eligibility_provenance,
)
from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
)
from control_room_surface_fixtures import (
    WORKSPACE_ID,
    business_item,
    snapshot,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence


@pytest.mark.parametrize(
    "technical_title",
    (
        "talent_cpa_scores",
        "talentCpaScores",
        "TalentCpaScores",
        "talent-cpa-scores",
        "talent/cpa/scores",
        "talent.cpa.scores",
        "tenant[talent_cpa_scores]",
        "analyticsTalentCpaScores",
        "talent／cpa／scores",
        "talent∕cpa∕scores",
        "talent‐cpa‐scores",
        "talent️_cpa_scores",
        "talent͏_cpa_scores",
        "prod/talent_cpa_scores",
        "prod:talent_cpa_scores",
    ),
)
def test_technical_fact_title_drops_fact(technical_title: str) -> None:
    item = business_item(
        title=technical_title,
        module_id="talent_cpa_scores",
        source_dataset="talent_cpa_scores",
    )

    assert build_business_experience(snapshot(items=(item,))).sections == []


def test_all_unsafe_facts_drop_the_parent_section() -> None:
    items = (
        business_item("missing", title="missing"),
        business_item("materialization", title="dataset no materializado"),
    )

    assert build_business_experience(snapshot(items=items)).sections == []


@pytest.mark.parametrize(
    "diagnostic_title",
    (
        "source_state",
        "missing",
        "blocked",
        "stub",
        "error",
        "no_permission",
        "empty",
        "schema_only",
        "unavailable",
        "invalid_schema",
        "insufficient_data",
        "N/D",
        "faltan datos",
        "dataset no materializado",
        "Dataset requerido no registrado",
        "Fuente operativa no disponible",
        "Error: source unavailable",
        "Error: source unavailable 503",
    ),
)
def test_diagnostic_only_title_drops_ready_fact(diagnostic_title: str) -> None:
    item = business_item(title=diagnostic_title, data_status="ready")

    assert build_business_experience(snapshot(items=(item,))).sections == []


def test_unsafe_section_candidate_uses_next_safe_copy() -> None:
    item = business_item(
        module="Bearer abcdefghijklmnopqrstuvwxyz",
        module_label="Programa Gold de liderazgo",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "Programa Gold de liderazgo"


@pytest.mark.parametrize(
    "label",
    (
        "Programa Gold de liderazgo",
        "Nivel Silver de reconocimiento",
        "Supplier Raw Materials",
        "Customer Gold Segment",
    ),
)
def test_legitimate_tier_words_remain_visible(label: str) -> None:
    item = business_item(module=label, module_id="raw_materials")

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == label


def test_legitimate_spanish_and_unicode_copy_round_trips() -> None:
    item = business_item(
        title="Rotación voluntaria en España",
        entity_label="María José",
        module="Plantilla por ubicación",
        metric_name="Índice de retención",
        unit="días",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]
    fact = section.facts[0]

    assert section.title == "Plantilla por ubicación"
    assert fact.title == "Rotación voluntaria en España"
    assert fact.entity_label == "María José"
    assert fact.metric
    assert fact.metric.name == "Índice de retención"
    assert fact.metric.unit == "días"


def test_unsafe_optional_copy_is_omitted_or_uses_typed_fallback() -> None:
    item = business_item(
        title="Rotación observada",
        entity_label="people_overview",
        metric_name="people_overview",
        unit="people_overview",
    )

    fact = build_business_experience(snapshot(items=(item,))).sections[0].facts[0]

    assert fact.entity_label is None
    assert fact.metric
    assert fact.metric.name == "count"
    assert fact.metric.unit is None


def test_absent_metric_never_becomes_observed_zero() -> None:
    item = business_item()
    item.pop("metric_type")
    item.pop("observed_value")
    item = bind_runtime_row_evidence(
        item,
        locator_field="entity_id",
        observed_at=str(item["detected_at"]),
    )

    payload = build_business_experience(snapshot(items=(item,)))

    assert payload.sections == []
    assert '"value":0' not in payload.model_dump_json()


def test_zero_stale_and_verified_decision_are_preserved() -> None:
    zero = business_item(
        "zero",
        observed_value=0,
        population_count=8,
        status="open",
    )
    provenance = workflow_eligibility_provenance(
        zero,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id=WORKSPACE_ID,
        decision_id=42,
    )
    projected = overlay_business_state(
        [zero],
        {
            str(zero["id"]): {
                "status": "decision_created",
                "decision_id": 42,
                "metadata": {
                    DECISION_PROVENANCE_KEY: provenance,
                    CURRENT_ELIGIBILITY_FINGERPRINT_KEY: (
                        business_observation_fingerprint(zero)
                    ),
                },
            }
        },
        item_statuses={"open", "decision_created", "approved", "resolved"},
        projector=project_business_item,
        sort_key=lambda value: str(value.get("id")),
    )
    stale = business_item(
        "stale",
        title="Observación histórica",
        data_status="stale",
        observed_value=3,
    )

    facts = {
        fact.title: fact
        for fact in build_business_experience(snapshot(items=(*projected, stale)))
        .sections[0]
        .facts
    }

    assert facts["Observación histórica"].stale is True
    assert facts["Observed business condition"].metric
    assert facts["Observed business condition"].metric.value == 0
    assert facts["Observed business condition"].decision
    assert facts["Observed business condition"].decision.reference == 42


def test_policy_is_typed_and_structural_contract_is_unchanged() -> None:
    item = business_item()
    identity = resolve_business_surface_identity(item)
    assert identity is not None

    result = classify_visible_business_copy(
        "missing",
        item=item,
        identity=identity,
        max_length=240,
    )
    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY
    assert result.text is None
    assert section.cartridge_id == "sap_hcm"
    assert section.module_id == "people_overview"


def test_schema_version_and_openapi_shape_do_not_change() -> None:
    payload = build_business_experience(snapshot(items=(business_item(),))).model_dump(
        mode="json", exclude_none=True
    )
    app = FastAPI()
    app.include_router(routes.router)
    schema = app.openapi()["components"]["schemas"]

    assert payload["schema_version"] == "control-room-experience/v1"
    assert set(payload["sections"][0]) == {
        "id",
        "cartridge_id",
        "module_id",
        "title",
        "domain",
        "facts",
    }
    assert "cta" not in schema["ExperienceFact"]["properties"]


def test_truncation_cannot_create_structured_or_technical_copy() -> None:
    structured = json.dumps({"value": "A" * 228}, separators=(",", ":"))
    assert len(structured) == 240
    items = (
        business_item(
            "structured",
            title=f"{structured} business",
        ),
        business_item(
            "technical",
            title=f"{'x' * 240} business",
            module_id="x" * 240,
        ),
    )

    assert build_business_experience(snapshot(items=items)).sections == []


def test_sensitive_structural_identity_drops_section_without_changing_schema() -> None:
    item = business_item(domain=f"ghp_{'a' * 36}")

    assert build_business_experience(snapshot(items=(item,))).sections == []
