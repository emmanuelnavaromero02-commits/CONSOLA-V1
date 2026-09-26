from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

INDICATORS_PATH = Path(__file__).resolve().parents[1] / "config" / "indicators.yaml"


@lru_cache(maxsize=1)
def indicators() -> tuple[dict[str, Any], ...]:
    data = yaml.safe_load(INDICATORS_PATH.read_text(encoding="utf-8")) or {}
    return tuple(dict(item) for item in data.get("indicators", []))
