from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_banxico_packaged_datasets_are_registered_for_pr2b():
    datasets = sorted((REPO / "cartridges/banxico/datasets").glob("*.sql"))
    names = [path.stem for path in datasets]

    assert names == [
        "banxico_market_context",
        "banxico_series_metadata_latest",
        "banxico_series_observations_normalized",
        "banxico_series_quality",
    ]
    for path in datasets:
        text = path.read_text(encoding="utf-8")
        assert "cartridge: banxico" in text
        assert "managed_by_banxico_materializer" in text


def test_dataset_refresh_chain_skips_intelligence_for_banxico():
    source = (REPO / "airflow/dags/dataset_refresh_chain.py").read_text(encoding="utf-8")

    assert 'cartridge_id == "banxico"' in source
    assert "skip_intelligence" in source
    assert "intelligence skipped" in source
