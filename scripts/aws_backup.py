#!/usr/bin/env python3
"""Run the AWS backup script through SSM and capture redacted evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_stamp, write_json


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "backup-aws"


def _remote_script(backup_id: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
cd "$DEPLOY_DIR"
BACKUP_ID={json.dumps(backup_id)} bash backup.sh
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AWS backup through SSM.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or "")
    parser.add_argument("--backup-id", default=os.environ.get("BACKUP_ID") or f"manual-{utc_stamp()}")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("OMEGA_AWS_BACKUP_TIMEOUT_SECONDS", "1800")))
    args = parser.parse_args(argv)
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(args.backup_id),
        comment="omega-backup-aws",
        timeout_seconds=args.timeout_seconds,
    )
    status = "PASS" if remote.status == "Success" else "FAIL"
    summary = {
        "status": status,
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "backup_id": args.backup_id,
        "response_code": remote.response_code,
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(redact(remote.stdout), encoding="utf-8")
    (evidence_dir / "remote_stderr_redacted.txt").write_text(redact(remote.stderr), encoding="utf-8")
    (evidence_dir / "REPORT.md").write_text(
        "\n".join(
            [
                "# AWS Backup Evidence",
                "",
                f"- status: `{status}`",
                f"- ssm_command_id: `{remote.command_id}`",
                f"- instance_id: `{instance_id}`",
                f"- region: `{args.region}`",
                f"- backup_id: `{args.backup_id}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir), "ssm_command_id": remote.command_id}, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

