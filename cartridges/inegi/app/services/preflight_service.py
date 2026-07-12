from __future__ import annotations

import re
from typing import Any

from app.core.inegi_client import INEGIClient, MetadataDriftError
from app.services.config_loader import SeriesConfig


def validate_metadata(client: INEGIClient, series: tuple[SeriesConfig, ...]) -> list[dict[str, Any]]:
    payload = client.get_metadata(
        [item.series_id for item in series],
        source_by_id={item.series_id: item.source_dataset for item in series},
    )
    return validate_metadata_payload(payload, series)


def validate_metadata_payload(payload: dict[str, Any], series: tuple[SeriesConfig, ...]) -> list[dict[str, Any]]:
    received = {
        str(item.get("id") or ""): item
        for item in payload.get("inegi", {}).get("metadata", [])
        if isinstance(item, dict)
    }
    evidence: list[dict[str, Any]] = []
    for expected in series:
        actual = received.get(expected.series_id)
        if not actual:
            raise MetadataDriftError(f"INEGI series missing: {expected.series_id}")
        title = str(actual.get("title") or "").strip()
        if _canonical_title(title) != _canonical_title(expected.expected_title):
            raise MetadataDriftError(f"INEGI metadata drift: {expected.series_id}")
        unit_code = str(actual.get("unit_code") or "")
        frequency_code = str(actual.get("frequency_code") or "")
        if expected.expected_unit_code and unit_code != expected.expected_unit_code:
            raise MetadataDriftError(f"INEGI unit drift: {expected.series_id}")
        if expected.expected_frequency_code and frequency_code != expected.expected_frequency_code:
            raise MetadataDriftError(f"INEGI frequency drift: {expected.series_id}")
        evidence.append(
            {
                "series_id": expected.series_id,
                "title": title,
                "metric_name": expected.metric_name,
                "unit": expected.unit,
                "frequency": expected.frequency,
                "unit_code": unit_code,
                "frequency_code": frequency_code,
            }
        )
    return evidence


def _canonical_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
