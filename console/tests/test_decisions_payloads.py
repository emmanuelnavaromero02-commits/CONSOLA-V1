from datetime import date, datetime, timezone

from app.domains.decisions.payloads import (
    coerce_date,
    coerce_datetime,
    decision_row_to_dict,
)


def test_coerce_date_accepts_empty_iso_and_date_values():
    assert coerce_date(None) is None
    assert coerce_date("") is None
    existing = date(2026, 7, 3)
    assert coerce_date(existing) is existing
    assert coerce_date("2026-07-03T10:15:00Z") == date(2026, 7, 3)


def test_coerce_datetime_accepts_empty_iso_and_datetime_values():
    assert coerce_datetime(None) is None
    assert coerce_datetime("") is None
    existing = datetime(2026, 7, 3, 10, 15, tzinfo=timezone.utc)
    assert coerce_datetime(existing) is existing
    assert coerce_datetime("2026-07-03T10:15:00Z") == existing


def test_decision_row_to_dict_serializes_dates_and_json_kpis():
    row = {
        "id": 7,
        "created_at": datetime(2026, 7, 3, 10, 0),
        "closed_at": None,
        "commitment_date": date(2026, 7, 4),
        "kpis": '[{"name":"margin"}]',
    }

    assert decision_row_to_dict(row) == {
        "id": 7,
        "created_at": "2026-07-03T10:00:00",
        "closed_at": None,
        "commitment_date": "2026-07-04",
        "kpis": [{"name": "margin"}],
    }


def test_decision_row_to_dict_uses_empty_kpis_for_invalid_json():
    assert decision_row_to_dict({"kpis": "not-json"}) == {"kpis": []}

