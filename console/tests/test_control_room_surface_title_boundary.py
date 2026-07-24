from __future__ import annotations

import pytest

from app.services.control_room.business_experience import build_business_experience
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
        module="tenantGoldPeople",
        module_label="tenant[gold_people]",
        business_label="Workforce health",
        source_dataset="tenant_gold_people",
        module_id="tenantGoldPeople",
        domain="People",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "Workforce health"


def test_technical_domain_falls_back_to_controlled_business_context() -> None:
    item = business_item(
        module="tenantGoldPeople",
        business_label="tenantSilverPeople",
        source_dataset="tenant_gold_people",
        module_id="tenantGoldPeople",
        domain="tenantRawPeople",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "Business context"


def test_nontechnical_word_containing_tier_letters_is_not_rejected() -> None:
    item = business_item(
        module="Golden opportunities",
        source_dataset="tenant_gold_people",
        module_id="tenantGoldPeople",
        domain="People",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.title == "Golden opportunities"
