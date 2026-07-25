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


_MARKS = ("\u034f", "\ufe0f")
_OBFUSCATED_SECRETS = (
    "AKI{mark}AIOSFODNN7EXAMPLE",
    f"AIz{{mark}}a{'A' * 35}",
    f"gh{{mark}}p_{'a' * 36}",
    "Bea{mark}rer abcdefghijklmnopqrstuvwxyz",
    "eyJhb{mark}GciOiJIUzI1NiJ9.e30.c2lnbmF0dXJl",
    "-----BEGIN PRI{mark}VATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
    "owner{mark}@example.com",
    "+52 55 12{mark}34 5678",
    "GODE56{mark}1231HDFRRN09",
    "GODE56{mark}1231GR8",
)


@pytest.mark.parametrize("mark", _MARKS)
@pytest.mark.parametrize("template", _OBFUSCATED_SECRETS)
def test_unicode_marks_cannot_hide_sensitive_copy(
    mark: str,
    template: str,
) -> None:
    secret = template.format(mark=mark)
    item = business_item(title=f"Rotación observada {secret}")

    response = build_business_experience(snapshot(items=(item,)))

    assert response.sections == []
    assert secret not in response.model_dump_json()


@pytest.mark.parametrize("mark", _MARKS)
def test_obfuscated_secret_after_visible_limit_is_scanned(mark: str) -> None:
    secret = f"gh{mark}p_{'a' * 36}"
    item = business_item(title=f"{'A' * 260} {secret}")

    response = build_business_experience(snapshot(items=(item,)))

    assert response.sections == []
    assert secret not in response.model_dump_json()


@pytest.mark.parametrize(
    ("field", "expected_name", "expected_optional"),
    (
        ("entity_label", "count", None),
        ("metric_name", "count", "Observed employee"),
        ("unit", "count", "Observed employee"),
    ),
)
def test_obfuscated_sensitive_optional_copy_is_not_published(
    field: str,
    expected_name: str,
    expected_optional: str | None,
) -> None:
    secret = f"gh\u034fp_{'a' * 36}"
    item = business_item(**{field: secret})

    fact = build_business_experience(snapshot(items=(item,))).sections[0].facts[0]

    assert secret not in fact.model_dump_json()
    assert fact.metric
    assert fact.metric.name == expected_name
    assert fact.entity_label == expected_optional
    if field == "unit":
        assert fact.metric.unit is None


def test_legitimate_nfd_copy_round_trips_without_normalization() -> None:
    title = "Rotacio\u0301n voluntaria"
    entity = "Mari\u0301a Jose\u0301"
    metric = "Retencio\u0301n"
    unit = "di\u0301as"
    section_title = "Plantilla por ubicacio\u0301n"
    item = business_item(
        title=title,
        entity_label=entity,
        metric_name=metric,
        unit=unit,
        module=section_title,
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]
    fact = section.facts[0]

    assert section.title == section_title
    assert fact.title == title
    assert fact.entity_label == entity
    assert fact.metric
    assert fact.metric.name == metric
    assert fact.metric.unit == unit


def test_legitimate_variation_selector_is_preserved_when_not_sensitive() -> None:
    title = "Tendencia ❤️ estable"
    item = business_item(title=title)

    fact = build_business_experience(snapshot(items=(item,))).sections[0].facts[0]

    assert fact.title == title


def test_sensitive_classification_reports_typed_cause() -> None:
    item = business_item()
    identity = resolve_business_surface_identity(item)
    assert identity is not None

    result = classify_visible_business_copy(
        f"gh\u034fp_{'a' * 36}",
        item=item,
        identity=identity,
        max_length=240,
    )

    assert result.allowed is False
    assert result.text is None
    assert result.cause is VisibleCopyCause.SENSITIVE
