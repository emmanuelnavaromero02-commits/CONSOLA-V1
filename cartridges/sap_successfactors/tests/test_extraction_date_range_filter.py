from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.services import extraction_service

_DAY_MS = 86_400_000
_2024_01_01 = 1_704_067_200_000


def _rows() -> list[dict]:
    return [
        {"id": "before", "startDate": f"/Date({_2024_01_01 - _DAY_MS})/"},
        {"id": "first-day", "startDate": f"/Date({_2024_01_01})/"},
        {"id": "offset", "startDate": f"/Date({_2024_01_01 + 10 * _DAY_MS}+0000)/"},
        {"id": "last-day", "startDate": f"/Date({_2024_01_01 + 30 * _DAY_MS})/"},
        {"id": "after", "startDate": f"/Date({_2024_01_01 + 31 * _DAY_MS})/"},
        {"id": "iso", "startDate": "2024-01-15"},
        {"id": "missing", "startDate": None},
        {"id": "blank", "startDate": ""},
    ]


def test_historical_range_compares_parsed_odata_dates():
    out = extraction_service._apply_date_range_filter(
        _rows(), "startDate", "2024-01-01", "2024-01-31"
    )

    assert [row["id"] for row in out] == ["first-day", "offset", "last-day", "iso"]


def test_open_ended_bounds_keep_every_dated_row_on_that_side():
    rows = _rows()

    since = extraction_service._apply_date_range_filter(
        rows, "startDate", "2024-01-31", None
    )
    until = extraction_service._apply_date_range_filter(
        rows, "startDate", None, "2024-01-01"
    )

    assert [row["id"] for row in since] == ["last-day", "after"]
    assert [row["id"] for row in until] == ["before", "first-day"]


def test_default_effective_window_keeps_extreme_odata_dates():
    rows = [
        {"id": "min", "startDate": "/Date(-2208988800000)/"},
        {"id": "max", "startDate": "/Date(253402214400000)/"},
    ]

    out = extraction_service._apply_date_range_filter(
        rows,
        "startDate",
        extraction_service.DEFAULT_EFFECTIVE_FROM_DATE,
        extraction_service.DEFAULT_EFFECTIVE_TO_DATE,
    )

    assert [row["id"] for row in out] == ["min", "max"]


def test_no_bounds_or_field_returns_rows_untouched():
    rows = _rows()

    assert (
        extraction_service._apply_date_range_filter(rows, None, "2024-01-01", None)
        is rows
    )
    assert (
        extraction_service._apply_date_range_filter(rows, "startDate", None, None)
        is rows
    )


@pytest.mark.parametrize(
    "bound",
    [
        "not-a-date",
        "31/01/2024",
        "20240131",
        "2024",
        "1704067200000",
        "2024-13-45",
        "9999-12-31T23:00:00-05:00",
    ],
)
@pytest.mark.parametrize("side", ["from", "to"])
def test_unparseable_bound_is_rejected(bound, side):
    lower, upper = (bound, None) if side == "from" else (None, bound)

    with pytest.raises(ValueError):
        extraction_service._apply_date_range_filter(_rows(), "startDate", lower, upper)


@pytest.mark.parametrize(
    "bound", [f"/Date({_2024_01_01})/", "2024-01-01T00:00:00Z", " 2024-01-01 "]
)
def test_iso_and_odata_bounds_are_accepted(bound):
    out = extraction_service._apply_date_range_filter(_rows(), "startDate", bound, None)

    assert [row["id"] for row in out] == ["first-day", "offset", "last-day", "after", "iso"]


def test_undated_rows_are_counted_without_their_values(caplog):
    rows = [
        {"id": "dated", "startDate": "2024-01-15"},
        {"id": "missing", "startDate": None},
        {"id": "garbage", "startDate": "secret-value-4411"},
        {"id": "overflow", "startDate": "9999-12-31T23:00:00-05:00"},
    ]

    with caplog.at_level("WARNING", logger=extraction_service.logger.name):
        out = extraction_service._apply_date_range_filter(
            rows, "startDate", "2024-01-01", None
        )

    assert [row["id"] for row in out] == ["dated"]
    messages = [record.getMessage() for record in caplog.records]
    assert any("field=startDate dropped=3" in message for message in messages)
    assert not any("secret-value-4411" in message for message in messages)


def _effective_rows() -> list[dict]:
    return [
        {"id": "overlaps", "startDate": "/Date(1685577600000)/", "endDate": "/Date(253402214400000)/"},
        {"id": "open", "startDate": "2023-06-01", "endDate": None},
        {"id": "ended-before", "startDate": "2023-01-01", "endDate": "2023-12-31"},
        {"id": "ends-first-day", "startDate": "2023-01-01", "endDate": "2024-01-01"},
        {"id": "inside", "startDate": "2024-01-10", "endDate": "2024-01-20"},
        {"id": "starts-last-day", "startDate": "2024-01-31", "endDate": None},
        {"id": "starts-after", "startDate": "2024-02-01", "endDate": None},
        {"id": "undated", "startDate": None, "endDate": None},
    ]


def test_effective_dated_records_are_kept_when_they_overlap_the_window():
    out = extraction_service._apply_date_range_filter(
        _effective_rows(), "startDate", "2024-01-01", "2024-01-31", "endDate"
    )

    assert [row["id"] for row in out] == [
        "overlaps",
        "open",
        "ends-first-day",
        "inside",
        "starts-last-day",
        "undated",
    ]


def test_only_effective_dated_entities_use_an_end_date():
    assert extraction_service._effective_end_field({"effective_dated": True}) == "endDate"
    assert (
        extraction_service._effective_end_field(
            {"effective_dated": True, "end_date_field": "validTo"}
        )
        == "validTo"
    )
    assert extraction_service._effective_end_field({"date_field": "payDate"}) is None


def test_overflowing_iso_values_parse_as_unrecognized():
    assert extraction_service._parse_watermark_datetime("9999-12-31T23:00:00-05:00") is None
