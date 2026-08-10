from __future__ import annotations

from pathlib import Path


LEGACY_JS = Path("console/app/static/js/studio/legacy.js")


def _exported_async_body(source: str, function_name: str) -> str:
    marker = f"export async function {function_name}("
    assert marker in source, f"{function_name} missing from Studio legacy.js"
    return source.split(marker, 1)[1].split("\n    export ", 1)[0]


def test_entity_list_bounds_required_and_optional_requests():
    source = LEGACY_JS.read_text(encoding="utf-8")
    body = _exported_async_body(source, "loadEntityList")

    assert "fetchWithTimeout" in body
    assert "/api/studio/entities?cartridge=" in body
    assert "10_000" in body
    assert "/api/pipeline_runs?cartridge=" in body
    assert "/api/studio/dags?cartridge=" in body
    assert body.count("5_000") >= 2
    assert body.count(".catch(() => null)") >= 2
    assert "Airflow no disponible" in body


def test_all_studio_dag_inventory_reads_have_a_five_second_budget():
    source = LEGACY_JS.read_text(encoding="utf-8")

    for function_name in ("_loadDagSelector", "loadDags"):
        body = _exported_async_body(source, function_name)
        assert "fetchWithTimeout" in body
        assert "5_000" in body
        assert "Airflow no disponible" in body
