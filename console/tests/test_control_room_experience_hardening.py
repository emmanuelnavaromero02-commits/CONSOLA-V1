from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI

from app.routers import control_room as routes
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_projection import project_business_item
from app.services.control_room.business_state_overlay import overlay_business_state
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_eligibility_provenance,
)
from app.services.control_room.operational_diagnostics import (
    build_operational_diagnostics,
)
from control_room_surface_fixtures import (
    WORKSPACE_ID,
    business_item,
    snapshot,
)


def _state(item: dict[str, object], decision_id: int = 42) -> dict[str, object]:
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id=WORKSPACE_ID,
        decision_id=decision_id,
    )
    return {
        "status": "decision_created",
        "decision_id": decision_id,
        "metadata": {
            DECISION_PROVENANCE_KEY: provenance,
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
        },
    }


def _overlay(
    items: list[dict[str, object]],
    states: dict[str, dict[str, object]],
) -> tuple[dict[str, object], ...]:
    return tuple(
        overlay_business_state(
            items,
            states,
            item_statuses={"open", "decision_created", "approved", "resolved"},
            projector=project_business_item,
            sort_key=lambda value: str(value.get("id")),
        )
    )


def test_equal_labels_from_distinct_cartridges_create_distinct_sections():
    first = business_item(
        "first",
        cartridge="sap_hcm",
        module_id="org_structure",
        module="Organization",
    )
    second = business_item(
        "second",
        cartridge="sap_successfactors",
        module_id="org_structure",
        module="Organization",
    )

    sections = build_business_experience(snapshot(items=(first, second))).sections

    assert len(sections) == 2
    assert {section.cartridge_id for section in sections} == {
        "sap_hcm",
        "sap_successfactors",
    }
    assert len({section.id for section in sections}) == 2


def test_structural_ids_and_output_are_independent_of_input_order():
    first = business_item("first", title="Later title")
    second = business_item("second", title="Earlier title")

    forward = build_business_experience(snapshot(items=(first, second))).model_dump(
        mode="json"
    )
    reverse = build_business_experience(snapshot(items=(second, first))).model_dump(
        mode="json"
    )

    assert forward == reverse


def test_source_dataset_is_stable_module_fallback():
    item = business_item(module_id="", source_dataset="gold_people_facts")

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.module_id == "gold_people_facts"
    assert section.id.startswith("business-section-")


def test_item_without_structural_identity_is_diagnostics_only():
    item = business_item(domain="")

    assert build_business_experience(snapshot(items=(item,))).sections == []
    diagnostics = build_operational_diagnostics(snapshot(items=(item,)))
    assert [row.title for row in diagnostics.diagnostic_items] == [
        "Observed business condition"
    ]


def test_forged_live_decision_marker_and_cta_are_not_exposed():
    item = business_item(
        status="decision_created",
        decision_id=42,
        _surface_workflow_provenance_verified=True,
        resolved_cta={
            "label": "Run",
            "href": "/api/control-room/items/business-1/run",
        },
    )

    payload = build_business_experience(snapshot(items=(item,))).model_dump(
        mode="json",
        exclude_none=True,
    )

    fact = payload["sections"][0]["facts"][0]
    assert "decision" not in fact
    assert "cta" not in fact
    assert "_surface_workflow_provenance_verified" not in str(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "fingerprint",
        "workspace",
        "item_id",
        "kind",
        "policy",
        "decision_id",
        "stage",
        "quarantine",
    ],
)
def test_invalid_persisted_provenance_never_exposes_decision(mutation):
    item = business_item(status="open")
    state = _state(item)
    metadata = deepcopy(state["metadata"])
    provenance = deepcopy(metadata[DECISION_PROVENANCE_KEY])
    if mutation == "fingerprint":
        provenance["fingerprint"] = "forged"
    elif mutation == "workspace":
        provenance["workspace_id"] = "foreign-workspace"
    elif mutation == "item_id":
        provenance["item_id"] = "foreign-item"
    elif mutation == "kind":
        provenance["kind"] = "source_state"
    elif mutation == "policy":
        provenance["policy_version"] = "legacy-policy"
    elif mutation == "decision_id":
        provenance["decision_id"] = 99
    elif mutation == "stage":
        provenance["stage"] = WorkflowStage.OPTION_SELECTED.value
    else:
        metadata[WORKFLOW_QUARANTINE_KEY] = {"invalid": True}
    metadata[DECISION_PROVENANCE_KEY] = provenance
    state["metadata"] = metadata

    projected = _overlay([item], {str(item["id"]): state})
    fact = build_business_experience(snapshot(items=projected)).sections[0].facts[0]

    assert fact.decision is None


def test_valid_provenance_is_verified_per_item_in_mixed_snapshot():
    verified = business_item("verified", title="Verified decision", status="open")
    forged = business_item(
        "forged",
        title="Forged decision",
        status="decision_created",
        decision_id=77,
    )
    projected = _overlay(
        [verified, forged],
        {str(verified["id"]): _state(verified)},
    )

    facts = {
        fact.title: fact
        for section in build_business_experience(snapshot(items=projected)).sections
        for fact in section.facts
    }

    assert facts["Verified decision"].decision is not None
    assert facts["Forged decision"].decision is None
    decisions = [
        fact.decision
        for section in build_business_experience(snapshot(items=projected)).sections
        for fact in section.facts
    ]
    assert sum(decision is not None for decision in decisions) == 1
    verified_item = next(item for item in projected if item["id"] == "verified")
    for invalid_reference in (-1, "42", 42.5, True):
        verified_item["decision_id"] = invalid_reference
        invalid = (
            build_business_experience(snapshot(items=(verified_item,)))
            .sections[0]
            .facts[0]
        )
        assert invalid.decision is None


def test_experience_openapi_has_no_cta_contract():
    app = FastAPI()
    app.include_router(routes.router)

    schemas = app.openapi()["components"]["schemas"]

    assert "ExperienceCta" not in schemas
    assert "cta" not in schemas["ExperienceFact"]["properties"]
    assert set(schemas["ExperienceSection"]["required"]) == {
        "id",
        "cartridge_id",
        "module_id",
        "title",
        "domain",
        "facts",
    }
