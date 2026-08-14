from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "stress_summary.py"


def _write_stats(
    path: Path,
    *,
    requests: int | str = 1000,
    failures: int | str = 0,
    rps: float | str = 42.5,
    p95: float | str = 250.0,
    p99: float | str = 900.0,
) -> None:
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
                "Requests/s": rps,
                "95%": p95,
                "99%": p99,
            }
        )


def _write_waf_failures(path: Path, *, occurrences: int = 2500) -> None:
    with (path / "locust_failures.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Method", "Name", "Error", "Occurrences"])
        writer.writeheader()
        writer.writerow(
            {
                "Method": "GET",
                "Name": "read:healthz",
                "Error": "CatchResponseError('/healthz returned 403: <html>\\r\\n<head><title>403 Forbidden</title></head>\\r\\n<body>\\r\\n<center><h1>403 Forbidden</h1></center>\\r\\n</body>\\r\\n</html>\\r\\n')",
                "Occurrences": str(occurrences),
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


def test_stress_summary_fails_threshold_violations(tmp_path: Path):
    _write_stats(tmp_path, failures=25, p95=1200.0, p99=3000.0)
    result = _run(tmp_path)
    assert result.returncode == 1, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "FAIL"
    assert "p95" in " ".join(summary["violations"])
    assert "p99" in " ".join(summary["violations"])
    assert "error_rate" in " ".join(summary["violations"])


def test_stress_summary_blocks_zero_request_false_green(tmp_path: Path):
    _write_stats(tmp_path, requests=0, failures=0, rps=0, p95=0, p99=0)

    result = _run(tmp_path)

    assert result.returncode == 2, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "BLOCKED"
    assert "zero requests" in summary["error"]


def test_stress_summary_requires_at_least_the_profile_user_count(tmp_path: Path):
    _write_stats(tmp_path, requests=24)

    result = _run(tmp_path)

    assert result.returncode == 1, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["minimum_request_count"] == 25
    assert "request_count 24 < 25" in summary["violations"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("rps", "nan"),
        ("rps", "inf"),
        ("p95", "nan"),
        ("p95", "inf"),
        ("p99", "nan"),
        ("p99", "inf"),
        ("p95", 0),
        ("p99", 0),
    ),
)
def test_stress_summary_blocks_nonfinite_or_zero_rate_evidence(
    tmp_path: Path, field: str, value: float | str
) -> None:
    _write_stats(tmp_path, **{field: value})

    result = _run(tmp_path)

    assert result.returncode == 2, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "BLOCKED"


def test_stress_summary_missing_locust_csv_is_blocked(tmp_path: Path):
    result = _run(tmp_path)
    assert result.returncode == 2
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "BLOCKED"
    assert "locust_stats.csv" in summary["error"]


def test_stress_summary_classifies_public_waf_global_403_as_blocked(tmp_path: Path):
    _write_stats(tmp_path, requests=3000, failures=2500, p95=250.0, p99=800.0)
    _write_waf_failures(tmp_path, occurrences=2500)

    result = _run(tmp_path)

    assert result.returncode == 2, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "BLOCKED"
    assert summary["blocked_by"] == "aws_waf_rate_limit"
    assert "public_alb_waf_rate_limit" in summary["unblock"]
    assert "error_rate" in " ".join(summary["violations"])
