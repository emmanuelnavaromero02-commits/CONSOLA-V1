#!/usr/bin/env python3
"""Rollback AWS to IMAGE_TAG_OLD/DEPLOY_REF_OLD through SSM."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_stamp, write_json


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "rollback-aws"


def _remote_script(target_tag: str, run_smoke: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
cd "$DEPLOY_DIR"
RUN_BACKUP_BEFORE_ROLLBACK="${{RUN_BACKUP_BEFORE_ROLLBACK:-1}}" RUN_SMOKE={json.dumps(run_smoke)} bash rollback.sh {json.dumps(target_tag)}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rollback AWS by immutable image/deploy tag through SSM.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or "")
    parser.add_argument("--target-tag", default=os.environ.get("DEPLOY_REF_OLD") or os.environ.get("IMAGE_TAG_OLD") or "")
    parser.add_argument("--run-smoke", default=os.environ.get("RUN_SMOKE", "1"))
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("OMEGA_AWS_ROLLBACK_TIMEOUT_SECONDS", "1800")))
    args = parser.parse_args(argv)
    if not args.target_tag:
        raise SystemExit("DEPLOY_REF_OLD or IMAGE_TAG_OLD is required for rollback-aws")
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(args.target_tag, args.run_smoke),
        comment="omega-rollback-aws",
        timeout_seconds=args.timeout_seconds,
    )
    status = "PASS" if remote.status == "Success" else "FAIL"
    summary = {
        "status": status,
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "target_tag": args.target_tag,
        "response_code": remote.response_code,
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(redact(remote.stdout), encoding="utf-8")
    (evidence_dir / "remote_stderr_redacted.txt").write_text(redact(remote.stderr), encoding="utf-8")
    (evidence_dir / "REPORT.md").write_text(
        f"# AWS Rollback Evidence\n\n- status: `{status}`\n- ssm_command_id: `{remote.command_id}`\n- target_tag: `{args.target_tag}`\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir), "ssm_command_id": remote.command_id}, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

