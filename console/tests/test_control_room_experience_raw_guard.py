from __future__ import annotations

import unicodedata

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
    is_technical_surface_copy,
    surface_section_title,
)
from app.services.control_room.business_visible_copy import (
    MAX_VISIBLE_COPY_SCAN_LENGTH,
)
from control_room_surface_fixtures import business_item, snapshot


class StripBomb(str):
    def strip(self, *_args: object, **_kwargs: object) -> str:
        raise AssertionError("oversized raw copy reached strip")


def _oversized_copy() -> StripBomb:
    return StripBomb("x" * (MAX_VISIBLE_COPY_SCAN_LENGTH + 1))


@pytest.fixture
def reject_oversized_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    original = unicodedata.normalize

    def guarded_normalize(form: str, value: str) -> str:
        if isinstance(value, StripBomb):
            raise AssertionError("oversized raw copy reached normalization")
        return original(form, value)

    monkeypatch.setattr(unicodedata, "normalize", guarded_normalize)


def test_oversized_module_uses_valid_module_label_without_strip(
    reject_oversized_normalization: None,
) -> None:
    item = business_item(module=_oversized_copy(), module_label="Valid module label")

    response = build_business_experience(snapshot(items=(item,)))

    assert response.sections[0].title == "Valid module label"


def test_legacy_title_projection_skips_oversized_and_empty_candidates() -> None:
    identity = BusinessSurfaceIdentity("People", "sap_hcm", "people_overview")
    item = {
        "module": _oversized_copy(),
        "module_label": " ",
        "module_name": "Valid module name",
        "module_id": "people_overview",
    }

    assert surface_section_title(item, identity) == "Valid module name"


def test_oversized_metric_name_uses_valid_metric_without_strip(
    reject_oversized_normalization: None,
) -> None:
    item = business_item(metric_name=_oversized_copy(), metric="Valid metric")

    response = build_business_experience(snapshot(items=(item,)))

    metric = response.sections[0].facts[0].metric
    assert metric is not None
    assert metric.name == "Valid metric"


def test_oversized_module_id_uses_valid_dataset_fallback_without_strip(
    reject_oversized_normalization: None,
) -> None:
    item = business_item(module_id=_oversized_copy())

    response = build_business_experience(snapshot(items=(item,)))

    assert response.sections[0].module_id == "gold_business_observations"


@pytest.mark.parametrize(
    "technical_field",
    ("module_id", "source_dataset", "dataset", "gold_table"),
)
def test_oversized_technical_id_is_not_canonicalized(
    reject_oversized_normalization: None,
    technical_field: str,
) -> None:
    identity = BusinessSurfaceIdentity("People", "sap_hcm", "people_overview")
    item = {
        "module_id": "people_overview",
        technical_field: _oversized_copy(),
    }

    assert is_technical_surface_copy(item, identity, "Business label") is False
