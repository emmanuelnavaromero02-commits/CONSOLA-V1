from __future__ import annotations

from pathlib import Path


LEGACY_JS = Path("console/app/static/js/studio/legacy.js")


def test_studio_refine_preview_includes_registered_dataset_sources():
    source = LEGACY_JS.read_text(encoding="utf-8")
    current_sources_section = source.split("function currentEditorSources()", 1)[1].split(
        "function currentTemplateSource()", 1
    )[0]

    assert "state._selectedDSDetail?.sources" in current_sources_section
    assert "normalizeStorageSourceRef(match[1])" in current_sources_section
    assert "['raw', 'silver', 'gold'].includes(parts[0])" in source


def test_studio_refine_save_preserves_declared_sources():
    source = LEGACY_JS.read_text(encoding="utf-8")
    save_section = source.split("export async function saveDS()", 1)[1].split(
        "export async function saveThenMaterialize()", 1
    )[0]

    assert "const sources = currentEditorSources();" in save_section
    assert "entity && cart ? [`raw/${cart}/${entity}`] : []" not in save_section
