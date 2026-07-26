from __future__ import annotations

from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from app.schemas.control_room_legacy_responses import PublicProjectionModel
from app.schemas.control_room_legacy_responses import ControlRoomBusinessSummaryResponse


def _annotation_nodes(annotation):
    yield annotation
    for child in get_args(annotation):
        yield from _annotation_nodes(child)


def test_public_model_hierarchy_has_no_free_dict_or_any_fields():
    pending = list(PublicProjectionModel.__subclasses__())
    seen = set()
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.add(model)
        pending.extend(model.__subclasses__())
        assert model.model_config.get("extra") == "forbid"
        for field in model.model_fields.values():
            nodes = tuple(_annotation_nodes(field.annotation))
            assert Any not in nodes
            assert all(get_origin(node) is not dict for node in nodes)


def test_projection_discards_unknown_before_strict_validation():
    model = ControlRoomBusinessSummaryResponse
    with pytest.raises(ValidationError):
        model.model_validate({"unknown": "password-sentinel"})

    projected = model.project({"total_anomalies": 0, "unknown": "password-sentinel"})
    assert projected.total_anomalies == 0
    assert "unknown" not in projected.model_dump()
