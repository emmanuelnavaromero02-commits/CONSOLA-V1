#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Thresholds:
    p95_ms: float
    p99_ms: float
    max_error_rate: float


PROFILE_DEFAULT_USERS = {
    "smoke": 25,
    "local": 10,
    "beta": 100,
    "spike": 1000,
    "breakpoint": 2000,
    "soak-24h": 250,
    "write-heavy": 100,
    "production": 500,
}


def _float(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _required_finite_number(row: dict[str, str], key: str) -> float:
    raw = row.get(key)
    if raw in (None, ""):
        raise ValueError(f"Locust aggregate {key!r} is missing")
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Locust aggregate {key!r} is not numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"Locust aggregate {key!r} is not finite")
    return value


def _required_count(row: dict[str, str], key: str) -> int:
    value = _required_finite_number(row, key)
    if value < 0 or not value.is_integer():
        raise ValueError(f"Locust aggregate {key!r} is not a non-negative integer")
    return int(value)


def _minimum_request_count(profile: str) -> int:
    configured = os.environ.get("OMEGA_STRESS_USERS")
    if configured not in (None, ""):
        try:
            users = int(configured)
        except ValueError as exc:
            raise ValueError("OMEGA_STRESS_USERS must be a positive integer") from exc
        if users <= 0:
            raise ValueError("OMEGA_STRESS_USERS must be a positive integer")
        return users
    return PROFILE_DEFAULT_USERS.get(profile, 1)


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


def _read_failure_rows(path: Path) -> list[dict[str, str]]:
    failures_csv = path / "locust_failures.csv"
    if not failures_csv.exists():
        return []
    return list(csv.DictReader(failures_csv.open(encoding="utf-8")))


def _is_waf_403_error(error: str) -> bool:
    lowered = error.lower()
    return (
        "returned 403" in lowered
        and "<title>403 forbidden</title>" in lowered
        and "<center><h1>403 forbidden</h1></center>" in lowered
    )


def _blocked_by_public_waf(path: Path, *, failures: int) -> bool:
    if failures <= 0:
        return False
    rows = _read_failure_rows(path)
    if not rows:
        return False
    waf_occurrences = 0
    total_occurrences = 0
    saw_healthz = False
    for row in rows:
        occurrences = int(_float(row.get("Occurrences"), 0.0))
        total_occurrences += occurrences
        error = str(row.get("Error") or "")
        if _is_waf_403_error(error):
            waf_occurrences += occurrences
            if "read:healthz" in str(row.get("Name") or "") or "/healthz returned 403" in error:
                saw_healthz = True
    if total_occurrences <= 0:
        return False
    return saw_healthz and (waf_occurrences / total_occurrences) >= 0.9


def summarize(
    path: Path,
    *,
    profile: str,
    workload: str,
    thresholds: Thresholds,
    minimum_requests: int | None = None,
) -> dict[str, object]:
    row = _read_stats(path)
    requests = _required_count(row, "Request Count")
    failures = _required_count(row, "Failure Count")
    p95 = _required_finite_number(row, "95%")
    p99 = _required_finite_number(row, "99%")
    rps = _required_finite_number(row, "Requests/s")
    if requests <= 0:
        raise ValueError("Locust aggregate contains zero requests")
    if failures > requests:
        raise ValueError("Locust aggregate failures exceed requests")
    if rps <= 0:
        raise ValueError("Locust aggregate Requests/s must be greater than zero")
    if p95 <= 0 or p99 <= 0:
        raise ValueError("Locust aggregate percentiles must be greater than zero")
    if (
        not math.isfinite(thresholds.p95_ms)
        or not math.isfinite(thresholds.p99_ms)
        or not math.isfinite(thresholds.max_error_rate)
        or thresholds.p95_ms <= 0
        or thresholds.p99_ms <= 0
        or not 0 <= thresholds.max_error_rate <= 1
    ):
        raise ValueError("stress thresholds are invalid")
    required_requests = (
        _minimum_request_count(profile)
        if minimum_requests is None
        else minimum_requests
    )
    if required_requests <= 0:
        raise ValueError("minimum request count must be positive")
    error_rate = failures / requests
    violations: list[str] = []
    if requests < required_requests:
        violations.append(f"request_count {requests} < {required_requests}")
    if p95 > thresholds.p95_ms:
        violations.append(f"p95 {p95:.1f}ms > {thresholds.p95_ms:.1f}ms")
    if p99 > thresholds.p99_ms:
        violations.append(f"p99 {p99:.1f}ms > {thresholds.p99_ms:.1f}ms")
    if error_rate > thresholds.max_error_rate:
        violations.append(f"error_rate {error_rate:.4f} > {thresholds.max_error_rate:.4f}")
    waf_blocked = _blocked_by_public_waf(path, failures=failures)
    status = "FAIL" if violations else "PASS"
    unblock = None
    error = None
    if waf_blocked:
        status = "BLOCKED"
        error = "Public ALB AWS WAF rate limit returned global 403 Forbidden HTML"
        unblock = (
            "Run this load stage against a dedicated staging/internal endpoint, "
            "or temporarily raise/allowlist the Terraform WAF rule "
            "`public_alb_waf_rate_limit` for the test source IP, then rerun."
        )
    return {
        "profile": profile,
        "workload": workload,
        "request_count": requests,
        "minimum_request_count": required_requests,
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
        "status": status,
        "blocked_by": "aws_waf_rate_limit" if waf_blocked else None,
        "error": error,
        "unblock": unblock,
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
    if summary["status"] == "FAIL":
        return 1
    if summary["status"] == "BLOCKED":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
