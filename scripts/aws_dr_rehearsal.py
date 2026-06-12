#!/usr/bin/env python3
"""Guarded AWS DR rehearsal wrapper."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_stamp, write_json


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "dr-rehearsal-aws"


def _remote_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
set +x
cd "${REPO_DIR:-/opt/modecissions}"
OMEGA_DR_REHEARSAL_EXECUTE=1 bash scripts/run_dr_rehearsal.sh
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run guarded AWS DR rehearsal through SSM.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or "")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("OMEGA_AWS_DR_TIMEOUT_SECONDS", "2400")))
    args = parser.parse_args(argv)
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if os.environ.get("OMEGA_DR_REHEARSAL_EXECUTE") != "1":
        summary = {
            "status": "BLOCKED",
            "reason": "Set OMEGA_DR_REHEARSAL_EXECUTE=1 to run a mutating AWS DR rehearsal.",
        }
        write_json(evidence_dir / "summary.json", summary)
        (evidence_dir / "REPORT.md").write_text(
            "# AWS DR Rehearsal Evidence\n\n- status: `BLOCKED`\n- unblock: `OMEGA_DR_REHEARSAL_EXECUTE=1 make dr-rehearsal-aws`\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "BLOCKED", "evidence_dir": str(evidence_dir)}, indent=2))
        return 2
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-dr-rehearsal-aws",
        timeout_seconds=args.timeout_seconds,
    )
    status = "PASS" if remote.status == "Success" else "FAIL"
    summary = {
        "status": status,
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "response_code": remote.response_code,
    }
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(redact(remote.stdout), encoding="utf-8")
    (evidence_dir / "remote_stderr_redacted.txt").write_text(redact(remote.stderr), encoding="utf-8")
    (evidence_dir / "REPORT.md").write_text(
        f"# AWS DR Rehearsal Evidence\n\n- status: `{status}`\n- ssm_command_id: `{remote.command_id}`\n- instance_id: `{instance_id}`\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir), "ssm_command_id": remote.command_id}, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

