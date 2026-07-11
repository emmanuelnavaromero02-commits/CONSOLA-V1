from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dag_is_manual_bronze_only_and_serial():
    source = (ROOT / "dags" / "inegi_extract.py").read_text(encoding="utf-8")
    assert "schedule=None" in source
    assert "max_active_runs=1" in source
    assert "dataset_refresh_chain" not in source
    assert "INEGI_URL" in source
    assert "X-Security-Context" in source
    assert "json.dumps(security_context" in source
    assert 'DEFAULT_CONN_ID = "default"' in source
    assert 'conf.get("conn_id") or DEFAULT_CONN_ID' in source
    assert "INEGI cartridge request failed status=" in source


def test_new_python_files_stay_under_300_lines():
    for path in ROOT.rglob("*.py"):
        if "/tests/" in str(path):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 300, f"{path} has {len(lines)} lines"
