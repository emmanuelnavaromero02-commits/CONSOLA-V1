#!/usr/bin/env python3
"""Summarize Locust evidence and enforce enterprise load thresholds."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Thresholds:
    p95_ms: float
    p99_ms: float
    max_error_rate: float


def _float(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _read_stats(path: Path) -> dict[str, str]:
    stats_csv = path / "locust_stats.csv"
    if not stats_csv.exists():
        raise FileNotFoundError(f"{stats_csv} not found")
    rows = list(csv.DictReader(stats_csv.open(encoding="utf-8")))
    if not rows:
        raise ValueError(f"{stats_csv} has no rows")
    for row in rows:
        name = str(row.get("Name") or "").strip().lower()
        if name == "aggregated":
            return row
    return rows[-1]


def summarize(path: Path, *, profile: str, workload: str, thresholds: Thresholds) -> dict[str, object]:
    row = _read_stats(path)
    requests = int(_float(row.get("Request Count")))
    failures = int(_float(row.get("Failure Count")))
    p95 = _float(row.get("95%"))
    p99 = _float(row.get("99%"))
    rps = _float(row.get("Requests/s"))
    error_rate = (failures / requests) if requests else 0.0
    violations: list[str] = []
    if p95 > thresholds.p95_ms:
        violations.append(f"p95 {p95:.1f}ms > {thresholds.p95_ms:.1f}ms")
    if p99 > thresholds.p99_ms:
        violations.append(f"p99 {p99:.1f}ms > {thresholds.p99_ms:.1f}ms")
    if error_rate > thresholds.max_error_rate:
        violations.append(f"error_rate {error_rate:.4f} > {thresholds.max_error_rate:.4f}")
    return {
        "profile": profile,
        "workload": workload,
        "request_count": requests,
        "failure_count": failures,
        "error_rate": round(error_rate, 6),
        "requests_per_second": round(rps, 3),
        "p95_ms": p95,
        "p99_ms": p99,
        "thresholds": {
            "p95_ms": thresholds.p95_ms,
            "p99_ms": thresholds.p99_ms,
            "max_error_rate": thresholds.max_error_rate,
        },
        "violations": violations,
        "status": "FAIL" if violations else "PASS",
    }


def _write_report(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# Stress Summary",
        "",
        f"- profile: `{summary['profile']}`",
        f"- workload: `{summary['workload']}`",
        f"- status: `{summary['status']}`",
        f"- requests: `{summary.get('request_count', 'n/a')}`",
        f"- failures: `{summary.get('failure_count', 'n/a')}`",
        f"- error_rate: `{summary.get('error_rate', 'n/a')}`",
        f"- p95_ms: `{summary.get('p95_ms', 'n/a')}`",
        f"- p99_ms: `{summary.get('p99_ms', 'n/a')}`",
        "",
    ]
    if summary.get("error"):
        lines.append(f"- error: `{summary['error']}`")
    if summary.get("unblock"):
        lines.append(f"- unblock: `{summary['unblock']}`")
    if summary.get("error") or summary.get("unblock"):
        lines.append("")
    violations = summary.get("violations") or []
    if violations:
        lines.append("## Violations")
        lines.extend(f"- {item}" for item in violations)
        lines.append("")
    (path / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--profile", default=os.environ.get("OMEGA_STRESS_PROFILE", "local"))
    parser.add_argument("--workload", default=os.environ.get("OMEGA_STRESS_WORKLOAD", "hubspot"))
    args = parser.parse_args(argv)
    thresholds = Thresholds(
        p95_ms=_float(os.environ.get("OMEGA_STRESS_P95_MS"), 800.0),
        p99_ms=_float(os.environ.get("OMEGA_STRESS_P99_MS"), 2500.0),
        max_error_rate=_float(os.environ.get("OMEGA_STRESS_MAX_ERROR_RATE"), 0.01),
    )
    try:
        summary = summarize(args.artifact_dir, profile=args.profile, workload=args.workload, thresholds=thresholds)
    except Exception as exc:
        summary = {
            "profile": args.profile,
            "workload": args.workload,
            "status": "BLOCKED",
            "error": str(exc),
            "unblock": "Run Locust successfully so locust_stats.csv exists, then rerun scripts/stress_summary.py",
        }
        args.artifact_dir.mkdir(parents=True, exist_ok=True)
        (args.artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        _write_report(args.artifact_dir, summary)
        print(json.dumps(summary, indent=2))
        return 2
    (args.artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _write_report(args.artifact_dir, summary)
    print(json.dumps(summary, indent=2))
    return 1 if summary["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
