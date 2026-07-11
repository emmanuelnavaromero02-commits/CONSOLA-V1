from __future__ import annotations

import re
from typing import Any

from app.core.banxico_client import BanxicoClient, MetadataDriftError
from app.services.config_loader import SeriesConfig


def validate_metadata(client: BanxicoClient, series: tuple[SeriesConfig, ...]) -> list[dict[str, Any]]:
    payload = client.get_metadata([item.series_id for item in series])
    return validate_metadata_payload(payload, series)


def validate_metadata_payload(payload: dict[str, Any], series: tuple[SeriesConfig, ...]) -> list[dict[str, Any]]:
    received = {
        str(item.get("idSerie") or ""): item
        for item in payload.get("bmx", {}).get("series", [])
        if isinstance(item, dict)
    }
    evidence: list[dict[str, Any]] = []
    for expected in series:
        actual = received.get(expected.series_id)
        if not actual:
            raise MetadataDriftError(f"Banxico series missing: {expected.series_id}")
        title = str(actual.get("titulo") or "").strip()
        if _canonical_title(title) != _canonical_title(expected.expected_title):
            raise MetadataDriftError(f"Banxico metadata drift: {expected.series_id}")
        evidence.append(
            {
                "series_id": expected.series_id,
                "title": title,
                "metric_name": expected.metric_name,
                "unit": expected.unit,
                "frequency": expected.frequency,
            }
        )
    return evidence


def _canonical_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
