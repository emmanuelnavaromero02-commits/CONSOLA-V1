from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_extraction_runlog_mirrors_to_pipeline_runs():
    source = (ROOT / "app" / "services" / "runlog_service.py").read_text(encoding="utf-8")

    assert "def _mirror_pipeline_run" in source
    assert "INSERT INTO pipeline_runs" in source
    assert "record_count" in source
    assert "tenant_id" in source
    assert "workspace_id" in source
    assert "extraction_runs_mirror" in source
    assert "_mirror_pipeline_run(run_id, status=status)" in source


def test_successfactors_extraction_touches_watermark_for_every_attempt():
    watermark_source = (ROOT / "app" / "services" / "watermark_service.py").read_text(
        encoding="utf-8"
    )
    extraction_source = (ROOT / "app" / "services" / "extraction_service.py").read_text(
        encoding="utf-8"
    )

    assert "def touch_watermark_attempt" in watermark_source
    assert "last_watermark_value, last_run_id" in watermark_source
    assert "touch_watermark_attempt(" in extraction_source
    assert "sap_successfactors_watermark_attempt_touch_failed" in extraction_source
