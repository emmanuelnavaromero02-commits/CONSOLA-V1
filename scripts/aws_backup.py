#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from aws_ssm import (
    DEFAULT_REGION,
    REPO,
    redact,
    resolve_instance_id,
    run_local,
    send_ssm_script,
    utc_now,
    utc_stamp,
    write_json,
)


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


def _parse_manifest(stdout: str) -> dict:
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_BACKUP_MANIFEST="):
            continue
        try:
            return json.loads(line.split("=", 1)[1])
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_manifest_s3_uri(stdout: str, backup_id: str) -> str:
    pattern = re.compile(rf"s3://[^/\s]+/backups/{re.escape(backup_id)}/manifest\.json")
    match = pattern.search(stdout)
    if match:
        return match.group(0)
    prefix_pattern = re.compile(rf"s3://([^/\s]+)/backups/{re.escape(backup_id)}/")
    match = prefix_pattern.search(stdout)
    if match:
        return f"s3://{match.group(1)}/backups/{backup_id}/manifest.json"
    return ""


def _fetch_manifest_from_s3(uri: str, region: str) -> dict:
    if not uri:
        return {}
    result = run_local(
        ["aws", "s3", "cp", uri, "-", "--region", region],
        timeout=120,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AWS backup through SSM.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument(
        "--backup-id", default=os.environ.get("BACKUP_ID") or f"manual-{utc_stamp()}"
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_BACKUP_TIMEOUT_SECONDS", "1800")),
    )
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
    manifest = _parse_manifest(remote.stdout)
    manifest_source = "ssm_stdout" if manifest else ""
    if not manifest:
        manifest = _fetch_manifest_from_s3(
            _parse_manifest_s3_uri(remote.stdout, args.backup_id), args.region
        )
        manifest_source = "s3_manifest" if manifest else ""
    if manifest:
        manifest = {**manifest, "ssm_command_id": remote.command_id}
    status = (
        "PASS" if remote.status == "Success" and manifest.get("artifacts") else "FAIL"
    )
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "backup_id": args.backup_id,
        "response_code": remote.response_code,
        "manifest": manifest,
        "manifest_source": manifest_source,
        "manifest_verifiable": bool(manifest.get("artifacts")),
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(
        redact(remote.stdout), encoding="utf-8"
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(remote.stderr), encoding="utf-8"
    )
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
                f"- manifest_source: `{manifest_source or '<missing>'}`",
                f"- manifest_verifiable: `{summary['manifest_verifiable']}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": status,
                "evidence_dir": str(evidence_dir),
                "ssm_command_id": remote.command_id,
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
