from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_sec_edgar_packaged_datasets_are_registered_for_pr4():
    datasets = sorted((REPO / "cartridges/sec_edgar/datasets").glob("*.sql"))
    names = [path.stem for path in datasets]

    assert names == [
        "sec_company_facts_normalized",
        "sec_company_metadata_latest",
        "sec_company_quality",
        "sec_market_context",
    ]
    for path in datasets:
        text = path.read_text(encoding="utf-8")
        assert "cartridge: sec_edgar" in text
        assert "managed_by_sec_edgar_materializer" in text


def test_dataset_refresh_chain_skips_intelligence_for_sec_edgar():
    source = (REPO / "airflow/dags/dataset_refresh_chain.py").read_text(encoding="utf-8")

    assert '"sec_edgar"' in source
    assert "skip_intelligence" in source
    assert "intelligence skipped" in source
