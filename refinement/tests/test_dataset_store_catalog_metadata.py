from __future__ import annotations

from pathlib import Path


def test_get_dataset_returns_catalog_refresh_metadata():
    source = Path("refinement/app/dataset_store.py").read_text()
    get_dataset_section = source.split("def get_dataset", 1)[1].split("def save_dataset", 1)[0]

    assert "last_refresh, row_count" in get_dataset_section
    assert '"last_refresh":' in get_dataset_section
    assert '"row_count":' in get_dataset_section


def test_data_catalog_surfaces_refresh_metadata_from_the_live_reader():
    source = Path("refinement/app/main.py").read_text()
    catalog_section = source.split("def _get_data_catalog", 1)[1].split(
        "def _upsert_catalog_entries", 1
    )[0]
    assert "store.list_datasets(**store_scope)" in catalog_section
    assert "return published_catalog(" in catalog_section
    assert "import json as _json" not in catalog_section, (
        "the unreachable legacy reader must stay deleted"
    )

    live = Path("refinement/app/publication_public.py").read_text()
    catalog_fn = live.split("def published_catalog", 1)[1]
    assert '"row_count": head.get("row_count")' in catalog_fn
    assert 'head["published_at"].isoformat()' in catalog_fn
    assert "if tags and not columns:" in catalog_fn
