from __future__ import annotations

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
    surface_section_title,
)
from control_room_surface_fixtures import business_item, snapshot


@pytest.mark.parametrize(
    "technical_title",
    (
        "analytics.gold_people",
        "tenant_gold_people",
        "tenant-gold-people",
        "tenant gold people",
        "tenant[gold_people]",
        "tenantGoldPeople",
        "TenantGoldPeople",
        "tenantSilverPeople",
        "tenantBronzePeople",
        "tenantRawPeople",
        "tenantStagingPeople",
        "tenant/gold/people",
        "tenant:gold:people",
        r"tenant\gold\people",
    ),
)
def test_transformed_technical_identifier_never_becomes_title(
    technical_title: str,
) -> None:
    item = business_item(
        module=technical_title,
        module_label=technical_title,
        module_name=technical_title,
        business_label=technical_title,
        cartridge_label=technical_title,
        connector_label=technical_title,
        domain_label=technical_title,
        source_dataset=technical_title,
        module_id=technical_title,
        domain="People",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "People"
    assert section.title != technical_title


def test_safe_business_label_wins_after_technical_module_candidates() -> None:
    item = business_item(
        module="TalentCpaScores",
        module_label="talent-cpa-scores",
        business_label="Workforce health",
        source_dataset="talent_cpa_scores",
        module_id="talent_cpa_scores",
        domain="People",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "Workforce health"


def test_technical_domain_falls_back_to_controlled_business_context() -> None:
    item = business_item(
        module="TalentCpaScores",
        business_label="TalentCpaScores",
        source_dataset="talent_cpa_scores",
        module_id="talent_cpa_scores",
        domain="TalentCpaScores",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "Business context"


@pytest.mark.parametrize(
    ("business_title", "technical_id"),
    (
        ("Supplier Raw Materials", "raw_materials"),
        ("Customer Gold Segment", "gold_segment"),
        ("Olympic Bronze Medal Sales", "bronze_medal_sales"),
        ("Silver anniversary cohort", "anniversary_cohort"),
        ("Gold customer retention", "customer_retention"),
    ),
)
def test_business_title_containing_tier_word_is_not_rejected(
    business_title: str,
    technical_id: str,
) -> None:
    item = business_item(
        module=business_title,
        source_dataset=technical_id,
        module_id=technical_id,
        domain="People",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == business_title


@pytest.mark.parametrize(
    ("visible_title", "technical_id"),
    (
        ("TalentCpaScores", "talent_cpa_scores"),
        ("Employee360", "employee_360"),
        ("TenantGoldPeople", "gold_people"),
        ("tenant gold people", "gold_people"),
    ),
)
def test_visible_title_matching_transformed_technical_id_is_rejected(
    visible_title: str,
    technical_id: str,
) -> None:
    item = business_item(
        module=visible_title,
        source_dataset=technical_id,
        module_id=technical_id,
        domain="People",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "People"


@pytest.mark.parametrize(
    "identifier_field",
    ("module_id", "source_dataset", "dataset", "gold_table"),
)
def test_each_technical_identifier_field_uses_same_canonicalization(
    identifier_field: str,
) -> None:
    values: dict[str, object] = {
        "module": "TalentCpaScores",
        "module_id": "people_overview",
        "source_dataset": "people_overview",
        "domain": "People",
        identifier_field: "talent_cpa_scores",
    }
    item = business_item(**values)
    identity = resolve_business_surface_identity(item)

    assert identity is not None
    assert surface_section_title(item, identity) == "People"
