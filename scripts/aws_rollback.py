#!/usr/bin/env python3
"""Rollback/rehearse AWS immutable-tag rollback through SSM.

Default is dry-run. A real rollback requires CONFIRM_ROLLBACK=1.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aws_ssm import (
    DEFAULT_REGION,
    REPO,
    redact,
    resolve_instance_id,
    send_ssm_script,
    utc_now,
    utc_stamp,
    write_json,
)


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "rollback-aws"


def _remote_script(target_tag: str, run_smoke: str, confirm: bool) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
TARGET_TAG={json.dumps(target_tag)}
RUN_SMOKE_VALUE={json.dumps(run_smoke)}
CONFIRM_ROLLBACK={json.dumps("1" if confirm else "0")}
cd "$DEPLOY_DIR"
emit() {{
  local name="$1"
  local status="$2"
  local evidence="${{3:-}}"
  evidence="${{evidence//$'\\t'/ }}"
  evidence="${{evidence//$'\\r'/ }}"
  evidence="${{evidence//$'\\n'/ }}"
  printf 'OMEGA_ROLLBACK_CHECK\\t%s\\t%s\\t%s\\n' "$name" "$status" "$evidence"
}}
env_value() {{
  local key="$1"
  awk -F= -v key="$key" '$1 == key {{print substr($0, index($0, "=") + 1)}}' "$DEPLOY_DIR/.env" | tail -n 1 | sed "s/^[ '\\"]//; s/[ '\\"]$//"
}}
if [[ -z "$TARGET_TAG" || "$TARGET_TAG" == "latest" || ! "$TARGET_TAG" =~ ^v[0-9] ]]; then
  emit "target immutable tag" "FAIL" "target=$TARGET_TAG"
  exit 20
fi
emit "target immutable tag" "PASS" "target=$TARGET_TAG"
owner="$(env_value GHCR_OWNER)"
if [ -z "$owner" ]; then owner="emmanuelnavaromero02-commits"; fi
if docker manifest inspect "ghcr.io/${{owner}}/console:${{TARGET_TAG}}" >/dev/null 2>&1; then
  emit "target console image exists" "PASS" "ghcr.io/${{owner}}/console:${{TARGET_TAG}}"
else
  emit "target console image exists" "FAIL" "ghcr.io/${{owner}}/console:${{TARGET_TAG}} missing"
  exit 21
fi
current_ref="$(env_value DEPLOY_REF)"
current_tag="$(env_value IMAGE_TAG)"
emit "current release captured" "PASS" "deploy_ref=${{current_ref:-<missing>}} image_tag=${{current_tag:-<missing>}}"
if [ "$CONFIRM_ROLLBACK" != "1" ]; then
  emit "rollback dry-run" "PASS" "CONFIRM_ROLLBACK not set; no changes applied"
  printf 'OMEGA_ROLLBACK_JSON={{"status":"dry_run","target_tag":"%s","destructive":false}}\\n' "$TARGET_TAG"
  exit 0
fi
RUN_BACKUP_BEFORE_ROLLBACK="${{RUN_BACKUP_BEFORE_ROLLBACK:-1}}" RUN_SMOKE="$RUN_SMOKE_VALUE" bash rollback.sh "$TARGET_TAG"
emit "rollback executed" "PASS" "target=$TARGET_TAG"
printf 'OMEGA_ROLLBACK_JSON={{"status":"executed","target_tag":"%s","destructive":true}}\\n' "$TARGET_TAG"
"""


def _parse(stdout: str) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    payload: dict = {}
    for line in stdout.splitlines():
        if line.startswith("OMEGA_ROLLBACK_CHECK\t"):
            _prefix, name, status, evidence = (line.split("\t", 3) + [""])[:4]
            checks.append(
                {"name": name, "status": status, "evidence": redact(evidence)}
            )
        elif line.startswith("OMEGA_ROLLBACK_JSON="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                payload = {}
    return checks, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rollback AWS by immutable image/deploy tag through SSM."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument(
        "--target-tag",
        default=os.environ.get("DEPLOY_REF_OLD")
        or os.environ.get("IMAGE_TAG_OLD")
        or "",
    )
    parser.add_argument("--run-smoke", default=os.environ.get("RUN_SMOKE", "1"))
    parser.add_argument(
        "--confirm",
        action="store_true",
        default=os.environ.get("CONFIRM_ROLLBACK") == "1",
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_ROLLBACK_TIMEOUT_SECONDS", "1800")),
    )
    args = parser.parse_args(argv)
    if not args.target_tag:
        raise SystemExit("DEPLOY_REF_OLD or IMAGE_TAG_OLD is required for rollback-aws")
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(args.target_tag, args.run_smoke, args.confirm),
        comment="omega-rollback-aws",
        timeout_seconds=args.timeout_seconds,
    )
    checks, payload = _parse(remote.stdout)
    checks.append(
        {
            "name": "SSM command completed",
            "status": "PASS"
            if remote.status == "Success" and remote.response_code == 0
            else "FAIL",
            "evidence": f"command_id={remote.command_id} status={remote.status} response_code={remote.response_code}",
        }
    )
    status = "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "target_tag": args.target_tag,
        "dry_run": not args.confirm,
        "confirm_rollback": args.confirm,
        "response_code": remote.response_code,
        "checks": checks,
        "payload": payload,
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(
        redact(remote.stdout), encoding="utf-8"
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(remote.stderr), encoding="utf-8"
    )
    check_rows = []
    for check in checks:
        evidence = str(check["evidence"]).replace("|", "\\|")
        check_rows.append(f"| {check['name']} | {check['status']} | {evidence} |")
    (evidence_dir / "REPORT.md").write_text(
        "\n".join(
            [
                "# AWS Rollback Evidence",
                "",
                f"- status: `{status}`",
                f"- dry_run: `{not args.confirm}`",
                f"- ssm_command_id: `{remote.command_id}`",
                f"- target_tag: `{args.target_tag}`",
                "",
                "| Check | Status | Evidence |",
                "|---|---|---|",
                *check_rows,
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
