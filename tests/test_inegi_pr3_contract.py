from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_inegi_packaged_datasets_are_registered_for_pr3():
    datasets = sorted((REPO / "cartridges/inegi/datasets").glob("*.sql"))
    names = [path.stem for path in datasets]

    assert names == [
        "inegi_indicator_metadata_latest",
        "inegi_indicator_observations_normalized",
        "inegi_indicator_quality",
        "inegi_market_context",
    ]
    for path in datasets:
        text = path.read_text(encoding="utf-8")
        assert "cartridge: inegi" in text
        assert "managed_by_inegi_materializer" in text


def test_dataset_refresh_chain_skips_intelligence_for_inegi():
    source = (REPO / "airflow/dags/dataset_refresh_chain.py").read_text(encoding="utf-8")

    assert 'cartridge_id == "inegi"' in source
    assert "skip_intelligence" in source
    assert "intelligence skipped" in source
