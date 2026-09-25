#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _evidence_dir(target: str) -> Path:
    run_id = os.environ.get("OMEGA_ENTERPRISE_RUN_ID", "manual")
    raw = os.environ.get("OMEGA_CHAOS_EVIDENCE_DIR")
    if raw:
        return Path(raw)
    return Path("docs/release-evidence/enterprise-readiness") / run_id / f"chaos-{target}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("local", "aws"), required=True)
    args = parser.parse_args()
    root = _evidence_dir(args.target)
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    if args.target == "local":
        rows.append(
            {
                "name": "local non-destructive chaos plan",
                "status": "BLOCKED",
                "evidence": "Wire local compose pause/restart probes with recovery checks before enabling.",
                "unblock": "OMEGA_CHAOS_LOCAL_EXECUTE=1 make chaos-local",
            }
        )
        status = "BLOCKED"
    else:
        confirmed = (
            os.environ.get("OMEGA_V1_STRESS_TARGET") == "staging"
            and os.environ.get("OMEGA_V1_STRESS_ALLOW_CHAOS") == "1"
            and os.environ.get("OMEGA_V1_GA_DEDICATED_STAGING") == "1"
        )
        if confirmed:
            rows.append(
                {
                    "name": "aws dedicated staging chaos",
                    "status": "BLOCKED",
                    "evidence": "Staging confirmed, but ECS/RDS/S3 fault commands are not wired in this safe gate.",
                    "unblock": "Configure SSM/ECS/RDS fault runner, then OMEGA_V1_GA_EXECUTE_CHAOS=1 make chaos-aws",
                }
            )
        else:
            rows.append(
                {
                    "name": "aws production destructive chaos guard",
                    "status": "PASS",
                    "evidence": "Destructive chaos refused outside dedicated staging.",
                    "unblock": "OMEGA_V1_STRESS_TARGET=staging OMEGA_V1_STRESS_ALLOW_CHAOS=1 OMEGA_V1_GA_DEDICATED_STAGING=1 make chaos-aws",
                }
            )
        status = "BLOCKED" if confirmed else "PASS"
    payload = {"status": status, "checks": rows}
    (root / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = ["# Chaos Gate", "", f"- status: `{status}`", "", "| Check | Status | Evidence | Unblock |", "|---|---|---|---|"]
    for row in rows:
        evidence = row["evidence"].replace("|", "\\|")
        unblock = row["unblock"].replace("|", "\\|")
        lines.append(f"| {row['name']} | {row['status']} | {evidence} | {unblock} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "evidence_dir": str(root)}, indent=2))
    return 2 if status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
