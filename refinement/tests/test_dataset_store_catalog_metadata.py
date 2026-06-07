from __future__ import annotations

from pathlib import Path


def test_get_dataset_returns_catalog_refresh_metadata():
    source = Path("refinement/app/dataset_store.py").read_text()
    get_dataset_section = source.split("def get_dataset", 1)[1].split("def save_dataset", 1)[0]

    assert "last_refresh, row_count" in get_dataset_section
    assert '"last_refresh":' in get_dataset_section
    assert '"row_count":' in get_dataset_section
