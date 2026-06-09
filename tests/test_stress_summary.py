from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "stress_summary.py"


def _write_stats(path: Path, *, requests: int = 1000, failures: int = 0, p95: float = 250.0, p99: float = 900.0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with (path / "locust_stats.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Type", "Name", "Request Count", "Failure Count", "Requests/s", "95%", "99%"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Type": "",
                "Name": "Aggregated",
                "Request Count": requests,
                "Failure Count": failures,
                "Requests/s": 42.5,
                "95%": p95,
                "99%": p99,
            }
        )


def _write_exceptions(path: Path, *, count: int = 3, message: str = "dataset response is not a list: dict") -> None:
    path.mkdir(parents=True, exist_ok=True)
    with (path / "locust_exceptions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Count", "Message", "Traceback", "Nodes"])
        writer.writeheader()
        writer.writerow(
            {
                "Count": count,
                "Message": message,
                "Traceback": "Traceback trimmed for test",
                "Nodes": "node-1",
            }
        )


def _run(path: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--profile", "smoke", "--workload", "sap_successfactors"],
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_stress_summary_passes_under_thresholds(tmp_path: Path):
    _write_stats(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "PASS"
    assert summary["workload"] == "sap_successfactors"
    assert summary["p95_ms"] == 250.0
    assert summary["p99_ms"] == 900.0
    assert summary["exception_count"] == 0
    assert summary["total_failure_count"] == 0


def test_stress_summary_fails_threshold_violations(tmp_path: Path):
    _write_stats(tmp_path, failures=25, p95=1200.0, p99=3000.0)
    result = _run(tmp_path)
    assert result.returncode == 1, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "FAIL"
    assert "p95" in " ".join(summary["violations"])
    assert "p99" in " ".join(summary["violations"])
    assert "error_rate" in " ".join(summary["violations"])
    assert summary["total_failure_count"] == 25


def test_stress_summary_fails_locust_exceptions(tmp_path: Path):
    _write_stats(tmp_path, requests=100, failures=0, p95=250.0, p99=900.0)
    _write_exceptions(tmp_path, count=3)
    result = _run(tmp_path)
    assert result.returncode == 1, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    report = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert summary["status"] == "FAIL"
    assert summary["failure_count"] == 0
    assert summary["exception_count"] == 3
    assert summary["total_failure_count"] == 3
    assert summary["error_rate"] == 0.03
    assert "locust_exceptions 3 > 0" in summary["violations"]
    assert "dataset response is not a list: dict" in report


def test_stress_summary_missing_locust_csv_is_blocked(tmp_path: Path):
    result = _run(tmp_path)
    assert result.returncode == 2
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "BLOCKED"
    assert "locust_stats.csv" in summary["error"]
