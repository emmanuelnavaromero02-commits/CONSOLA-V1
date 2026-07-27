from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from app.routers import control_room
from app.schemas.control_room_business_responses import PublicMetricRow
from app.schemas.control_room_talent_responses import WorkforceSeries
from app.services import control_room_service
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
)
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from control_room_public_http_harness import DATASET_READER, client

SECRET = "UNICODE-SECRET-SENTINEL"
IDENTITY = BusinessSurfaceIdentity(
    domain="public",
    cartridge_id="public",
    module_id="public",
)


def _classify(value: str):
    return classify_visible_business_copy(
        value,
        item={},
        identity=IDENTITY,
        max_length=240,
    )


def test_unicode_sensitive_keys_redact_values_at_every_nesting_level() -> None:
    sensitive_keys = (
        "ｃｌｉｅｎｔＳｅｃｒｅｔｓ",
        "pa\u200bsswords",
        "сlіentЅecrets",
        "api꞉keys",
        "access∶tokens",
        "раѕѕԝогԁ",
        "AWS_SECRET_ACCESS_KEYS",
    )
    raw = {
        "safe": {
            "inputTo\u200bkens": 0,
            "items": [{key: {"opaque": SECRET}} for key in sensitive_keys],
        }
    }

    redacted = redact_diagnostic_value(raw)

    assert redacted["safe"]["inputTo\u200bkens"] == 0
    assert redacted["safe"]["items"] == [{key: "[REDACTED]"} for key in sensitive_keys]
    assert SECRET not in json.dumps(redacted, ensure_ascii=False)


@pytest.mark.parametrize(
    "message",
    (
        f"ｐａｓｓｗｏｒｄ﹕ {SECRET}",
        f"pa\u200bssword꞉ {SECRET}",
        f"сlіentЅecrets∶ {SECRET}",
        f"раѕѕԝогԁ: {SECRET}",
        f"ACCESS＿TOKENS﹕ {SECRET}",
    ),
)
def test_unicode_sensitive_assignments_never_echo_obfuscated_values(
    message: str,
) -> None:
    redacted = redact_diagnostic_value({"outer": [message]})

    assert SECRET not in json.dumps(redacted, ensure_ascii=False)
    assert redacted == {"outer": ["[REDACTED]"]}


@pytest.mark.parametrize("control", ("\x00", "\x09", "\x1f", "\x85"))
def test_cc_controls_cannot_split_sensitive_names_in_nested_values(
    control: str,
) -> None:
    raw = {
        "outer": [
            f"pa{control}ssword: {SECRET}",
            {"nested": [{f"pa{control}ssword": {"opaque": SECRET}}]},
        ]
    }

    redacted = redact_diagnostic_value(raw)

    assert redacted == {
        "outer": ["[REDACTED]", {"nested": [{f"pa{control}ssword": "[REDACTED]"}]}]
    }
    assert SECRET not in json.dumps(redacted, ensure_ascii=False)


def test_safe_multiline_copy_is_not_changed_by_cc_detection_normalization() -> None:
    copy = "Tendencia estable\nSin incidencias"

    assert redact_diagnostic_value(copy) == copy


@pytest.mark.parametrize(
    "message",
    (
        f"ｐａｓｓｗｏｒｄ﹕ {SECRET}",
        f"сlіentЅecrets∶ {SECRET}",
    ),
)
def test_visible_copy_uses_the_same_unicode_sensitive_boundary(message: str) -> None:
    result = _classify(message)

    assert result.allowed is False
    assert result.cause is VisibleCopyCause.SENSITIVE


def test_safe_nfd_copy_and_token_counters_remain_unchanged() -> None:
    copy = "Rotacio\u0301n y tendencia ❤️ estable"
    raw = {
        "copy": copy,
        "inputTo\u200bkens": 0,
        "cached_tokens": None,
        "status": "stale",
    }

    assert redact_diagnostic_value(raw) == raw
    result = _classify(copy)
    assert result.allowed is True
    assert result.text == copy


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_public_scalar_projection_replaces_non_finite_numbers_with_null(
    value: float,
) -> None:
    row = PublicMetricRow.project(
        {
            "value": value,
            "contractor_count": 0,
            "risk_factor": value,
            "status": "stale",
        }
    )

    assert row.value is None
    assert row.risk_factor is None
    assert row.contractor_count == 0
    assert row.status == "stale"
    serialized = row.model_dump_json()
    assert "NaN" not in serialized and "Infinity" not in serialized


def test_non_finite_series_keep_positions_as_null_and_preserve_zero() -> None:
    series = WorkforceSeries.project(
        {
            "months": ["2026-01"],
            "headcount": [0, None, float("nan"), float("inf"), float("-inf"), 1.5],
        }
    )

    assert series.headcount == [0, None, None, None, None, 1.5]
    serialized = series.model_dump_json()
    assert "NaN" not in serialized and "Infinity" not in serialized


def test_real_http_projection_returns_null_instead_of_500_for_non_finite() -> None:
    raw = {
        "widgets": [
            {
                "id": "sf_contractor_risk",
                "title": "Métrica estable",
                "value": float("nan"),
                "contractor_count": 0,
                "risk_factor": float("inf"),
                "status": "ready",
                "rows": [
                    {
                        "label": "Riesgo controlado",
                        "value": float("-inf"),
                        "contractor_count": 0,
                        "risk_factor": float("nan"),
                        "status": "ready",
                    }
                ],
            }
        ]
    }
    control_room._CONTROL_ROOM_READ_CACHE.clear()

    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=raw),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert widget["value"] is None and widget["risk_factor"] is None
    assert widget["rows"][0]["value"] is None
    assert widget["rows"][0]["risk_factor"] is None
    assert widget["contractor_count"] == widget["rows"][0]["contractor_count"] == 0
    assert widget["status"] == widget["rows"][0]["status"] == "ready"
    assert "NaN" not in response.text and "Infinity" not in response.text


def test_diagnostic_containers_also_replace_non_finite_values_with_null() -> None:
    redacted = redact_diagnostic_value(
        {
            "zero": 0,
            "null": None,
            "status": "stale",
            "series": [float("nan"), float("inf"), float("-inf")],
        }
    )

    assert redacted == {
        "zero": 0,
        "null": None,
        "status": "stale",
        "series": [None, None, None],
    }
    json.dumps(redacted, allow_nan=False)
