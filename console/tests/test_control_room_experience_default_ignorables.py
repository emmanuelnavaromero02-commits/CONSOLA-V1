from __future__ import annotations

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
)
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from control_room_surface_fixtures import business_item, snapshot


_DEFAULT_IGNORABLES = ("\u115f", "\u1160", "\u3164", "\uffa0", "\u2065")
_SENSITIVE_TEMPLATES = (
    "AKIAIOSFODNN7{mark}EXAMPLE",
    f"AIza{{mark}}{'A' * 35}",
    f"ghp_{{mark}}{'a' * 36}",
    "Bea{mark}rer abcdefghijklmnopqrstuvwxyz",
    "eyJhb{mark}GciOiJIUzI1NiJ9.e30.c2lnbmF0dXJl",
    "-----BEGIN PRI{mark}VATE KEY-----secret-----END PRIVATE KEY-----",
    "owner{mark}@example.com",
    "+52 55 12{mark}34 5678",
    "GODE56{mark}1231HDFRRN09",
    "GODE56{mark}1231GR8",
)


def _classify(value: str):
    item = business_item(
        module_id="talent_cpa_scores",
        source_dataset="talent_cpa_scores",
    )
    identity = resolve_business_surface_identity(item)
    assert identity is not None
    return classify_visible_business_copy(
        value,
        item=item,
        identity=identity,
        max_length=240,
    )


@pytest.mark.parametrize("character", _DEFAULT_IGNORABLES)
@pytest.mark.parametrize("template", _SENSITIVE_TEMPLATES)
def test_default_ignorables_cannot_hide_sensitive_copy(
    character: str,
    template: str,
) -> None:
    result = _classify(template.format(mark=character))

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.SENSITIVE


@pytest.mark.parametrize("character", _DEFAULT_IGNORABLES)
def test_default_ignorables_cannot_hide_technical_identifier(
    character: str,
) -> None:
    result = _classify(f"talent{character}_cpa_scores")

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.TECHNICAL_IDENTIFIER


@pytest.mark.parametrize("character", _DEFAULT_IGNORABLES)
def test_default_ignorables_cannot_hide_diagnostic_state(character: str) -> None:
    result = _classify(f"source_state m{character}issing")

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.DIAGNOSTIC_ONLY


@pytest.mark.parametrize("character", _DEFAULT_IGNORABLES)
def test_default_ignorable_secret_after_character_240_is_rejected(
    character: str,
) -> None:
    secret = f"ghp_{character}{'a' * 36}"
    item = business_item(title=f"{'A' * 260} {secret}")

    response = build_business_experience(snapshot(items=(item,)))

    assert response.sections == []
    assert secret not in response.model_dump_json()


def test_legitimate_nfd_and_emoji_copy_remain_byte_for_byte() -> None:
    title = "Rotacio\u0301n y tendencia ❤️ estable"
    item = business_item(title=title)

    fact = build_business_experience(snapshot(items=(item,))).sections[0].facts[0]

    assert fact.title == title
