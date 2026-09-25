from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd


BASE_CURRENCY_COLUMNS = (
    "effective_from",
    "effective_to",
    "currency",
    "authority_source",
    "verified_at",
)


def base_currency_frame(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(list(rows), columns=BASE_CURRENCY_COLUMNS)
    for column in BASE_CURRENCY_COLUMNS:
        frame[column] = frame[column].astype(object)
    return frame


__all__ = ("BASE_CURRENCY_COLUMNS", "base_currency_frame")
