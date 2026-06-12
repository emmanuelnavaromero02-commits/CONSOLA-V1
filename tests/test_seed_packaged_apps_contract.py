from __future__ import annotations

import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)
sys.path.insert(0, str(REPO / "console"))

from app.services.seed_packaged_apps import _declared_datasets  # noqa: E402


def test_packaged_app_seed_extracts_singular_dataset_and_data_bind():
    meta = {"dataset": "forecast_mensual"}
    html = '<tbody data-bind="forecast_mensual"></tbody>'

    assert _declared_datasets(meta, html) == ["forecast_mensual"]


def test_hubspot_pipeline_forecast_declares_gold_dataset():
    meta = (REPO / "cartridges/hubspot/apps/pipeline_forecast_dashboard.json").read_text(encoding="utf-8")
    html = (REPO / "cartridges/hubspot/apps/pipeline_forecast_dashboard.html").read_text(encoding="utf-8")

    assert '"datasets_used": ["forecast_mensual"]' in meta
    assert "/api/data/forecast_mensual" in html
