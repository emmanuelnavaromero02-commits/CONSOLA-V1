from __future__ import annotations

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import (
    resolve_bounded_business_surface_identity,
    resolve_business_surface_identity,
)
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from control_room_surface_fixtures import business_item, snapshot


_STRUCTURED_COPY = (
    '｛"rows":123｝',
    '［"ready"］',
    '｛"status":"ready"｝',
    '{"status"："ready"}',
    "[1,2",
    "[true,false",
    "[null,1",
    "{foo:1",
    "raw {status:missing",
    'raw {"":',
    "raw [None, 1",
)
_SAFE_BRACKETED_COPY = (
    "Rotación [México]",
    "Crecimiento {estimado}",
    "[Preliminar] Rotación voluntaria",
)


def _classify(value: str):
    item = business_item()
    identity = resolve_business_surface_identity(item)
    assert identity is not None
    return classify_visible_business_copy(
        value,
        item=item,
        identity=identity,
        max_length=240,
    )


@pytest.mark.parametrize("structured", _STRUCTURED_COPY)
def test_unicode_and_incomplete_structured_copy_is_rejected(
    structured: str,
) -> None:
    result = _classify(structured)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.STRUCTURED


@pytest.mark.parametrize("structured", _STRUCTURED_COPY)
def test_structured_fact_and_parent_section_are_removed(structured: str) -> None:
    item = business_item(title=structured)

    assert build_business_experience(snapshot(items=(item,))).sections == []


@pytest.mark.parametrize("business_copy", _SAFE_BRACKETED_COPY)
def test_legitimate_bracketed_copy_remains_visible(business_copy: str) -> None:
    result = _classify(business_copy)

    assert result.allowed is True
    assert result.text == business_copy


def _identity_item(field: str, length: int) -> tuple[dict[str, object], str]:
    value = "x" * length
    return business_item(**{field: value}), value


@pytest.mark.parametrize("field", ("domain", "cartridge_id", "module_id"))
def test_structural_identity_exactly_240_is_preserved(
    field: str,
) -> None:
    item, value = _identity_item(field, 240)

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    identity = resolve_bounded_business_surface_identity(item, max_length=240)
    assert identity is not None
    assert getattr(identity, field) == value
    if field == "domain":
        assert section.domain == value
    else:
        assert value not in section.model_dump_json()


@pytest.mark.parametrize("field", ("domain", "cartridge_id", "module_id"))
def test_structural_identity_over_240_uses_only_valid_fallback(field: str) -> None:
    item, value = _identity_item(field, 241)

    response = build_business_experience(snapshot(items=(item,)))
    fallback = {
        "domain": None,
        "cartridge_id": "sap_hcm",
        "module_id": "gold_business_observations",
    }[field]
    if fallback is None:
        assert response.sections == []
        return
    identity = resolve_bounded_business_surface_identity(item, max_length=240)
    assert identity is not None
    assert getattr(identity, field) == fallback
    assert value not in response.model_dump_json()
    assert fallback not in response.model_dump_json()
