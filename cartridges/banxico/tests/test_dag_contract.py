from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dag_is_manual_bronze_only_and_serial():
    source = (ROOT / "dags" / "banxico_extract.py").read_text(encoding="utf-8")
    assert "schedule=None" in source
    assert "max_active_runs=1" in source
    assert "dataset_refresh_chain" not in source
    assert "BANXICO_URL" in source


def test_new_python_files_stay_under_300_lines():
    for path in ROOT.rglob("*.py"):
        if "/tests/" in str(path):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 300, f"{path} has {len(lines)} lines"
