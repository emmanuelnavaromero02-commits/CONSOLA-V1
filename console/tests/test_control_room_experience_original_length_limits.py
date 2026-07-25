from __future__ import annotations

import pytest

from app.schemas.control_room_surfaces import EXPERIENCE_SCHEMA_VERSION
from app.services.control_room import business_visible_copy
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import BusinessSurfaceIdentity
from app.services.control_room.business_visible_copy import (
    MAX_VISIBLE_COPY_SCAN_LENGTH,
    VisibleCopyCause,
    classify_visible_business_copy,
)
from control_room_surface_fixtures import business_item, snapshot


_IDENTITY_FIELDS = ("domain", "cartridge_id", "module_id")
_INVALID_ORIGINALS = (
    " " + "x" * 238 + " ",
    "x" * 241,
    "x" * 240 + " ",
    " " + "x" * 240,
    "x" * 240 + "\t",
    "x" * 240 + "\n",
)
_FALLBACKS = {
    "domain": None,
    "cartridge_id": "sap_hcm",
    "module_id": "gold_business_observations",
}


def _classify(value: str, *, max_length: int):
    item = business_item()
    identity = BusinessSurfaceIdentity("People", "sap_hcm", "people_overview")
    return classify_visible_business_copy(
        value,
        item=item,
        identity=identity,
        max_length=max_length,
    )


def test_visible_copy_exact_scan_limit_is_preserved() -> None:
    value = "R" * MAX_VISIBLE_COPY_SCAN_LENGTH

    result = _classify(value, max_length=MAX_VISIBLE_COPY_SCAN_LENGTH)

    assert result.allowed is True
    assert result.text == value


def test_visible_copy_over_original_scan_limit_is_rejected() -> None:
    value = "R" * (MAX_VISIBLE_COPY_SCAN_LENGTH + 1)

    result = _classify(value, max_length=MAX_VISIBLE_COPY_SCAN_LENGTH + 1)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.TOO_LONG
    assert result.text is None


def test_visible_copy_padded_original_at_limit_remains_valid() -> None:
    value = " " * (MAX_VISIBLE_COPY_SCAN_LENGTH - 1) + "R"

    result = _classify(value, max_length=MAX_VISIBLE_COPY_SCAN_LENGTH)

    assert result.allowed is True
    assert result.text == "R"


def test_visible_copy_padded_original_over_limit_is_rejected() -> None:
    value = " " * MAX_VISIBLE_COPY_SCAN_LENGTH + "R"

    result = _classify(value, max_length=MAX_VISIBLE_COPY_SCAN_LENGTH)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.TOO_LONG


def test_visible_copy_length_is_checked_before_strip_or_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = " " * 9000 + "Rotación"

    def unexpected_analysis(*_args: object, **_kwargs: object) -> None:
        pytest.fail("oversized original copy reached hazard analysis")

    monkeypatch.setattr(
        business_visible_copy,
        "_rejection_cause",
        unexpected_analysis,
    )

    result = _classify(value, max_length=240)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.TOO_LONG


def test_oversized_section_title_uses_later_valid_fallback() -> None:
    oversized = " " * MAX_VISIBLE_COPY_SCAN_LENGTH + "Technical title"
    item = business_item(module=oversized, module_label="Valid module label")

    response = build_business_experience(snapshot(items=(item,)))

    assert response.sections[0].title == "Valid module label"
    assert oversized not in response.model_dump_json()


def test_oversized_metric_name_uses_later_valid_fallback() -> None:
    oversized = " " * MAX_VISIBLE_COPY_SCAN_LENGTH + "Technical metric"
    item = business_item(metric_name=oversized, metric="Valid metric")

    response = build_business_experience(snapshot(items=(item,)))

    metric = response.sections[0].facts[0].metric
    assert metric is not None
    assert metric.name == "Valid metric"
    assert oversized not in response.model_dump_json()


def test_invalid_module_id_prefers_later_module_id_before_dataset_fallback() -> None:
    item = business_item(
        module_id="x" * 241,
        metadata={"module_id": "nested_business_module"},
    )

    response = build_business_experience(snapshot(items=(item,)))

    assert response.sections[0].module_id == "nested_business_module"


@pytest.mark.parametrize("field", _IDENTITY_FIELDS)
def test_structural_identity_exactly_240_is_preserved_byte_for_byte(
    field: str,
) -> None:
    value = "x" * 240
    item = business_item(**{field: value})

    response = build_business_experience(snapshot(items=(item,)))

    section = response.sections[0]
    assert getattr(section, field) == value
    assert response.schema_version == EXPERIENCE_SCHEMA_VERSION
    expected = BusinessSurfaceIdentity(
        domain=value if field == "domain" else "People",
        cartridge_id=value if field == "cartridge_id" else "sap_hcm",
        module_id=value if field == "module_id" else "people_overview",
    )
    assert section.id == expected.section_id


@pytest.mark.parametrize("field", _IDENTITY_FIELDS)
@pytest.mark.parametrize("candidate", _INVALID_ORIGINALS)
def test_invalid_original_identity_is_never_trimmed_or_published(
    field: str,
    candidate: str,
) -> None:
    item = business_item(**{field: candidate})

    response = build_business_experience(snapshot(items=(item,)))

    fallback = _FALLBACKS[field]
    if fallback is None:
        assert response.sections == []
        return
    section = response.sections[0]
    assert getattr(section, field) == fallback
    assert getattr(section, field) != candidate
