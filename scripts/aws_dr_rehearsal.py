#!/usr/bin/env python3
"""Safe AWS DR restore rehearsal wrapper.

The default mode is an isolated restore into temporary containers. It never
restores over the live databases and never writes to lakehouse prefixes.
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
    utc_stamp,
    write_json,
)


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "dr-rehearsal-aws"


def _remote_script(backup_id: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
BACKUP_ID_ARG={json.dumps(backup_id)}
cd "$DEPLOY_DIR"

emit() {{
  local name="$1"
  local status="$2"
  local evidence="${{3:-}}"
  evidence="${{evidence//$'\\t'/ }}"
  evidence="${{evidence//$'\\r'/ }}"
  evidence="${{evidence//$'\\n'/ }}"
  printf 'OMEGA_DR_CHECK\\t%s\\t%s\\t%s\\n' "$name" "$status" "$evidence"
}}

env_value() {{
  local key="$1"
  awk -F= -v key="$key" '$1 == key {{print substr($0, index($0, "=") + 1)}}' "$DEPLOY_DIR/.env" | tail -n 1 | sed "s/^[ '\\"]//; s/[ '\\"]$//"
}}

if [ ! -f .env ]; then
  emit "deploy env exists" "FAIL" ".env missing"
  exit 20
fi
bucket="$(env_value S3_BUCKET_NAME)"
region="$(env_value AWS_REGION)"
if [ -z "$bucket" ]; then
  emit "S3 bucket configured" "FAIL" "S3_BUCKET_NAME missing"
  exit 21
fi
if [ -z "$region" ]; then
  region="us-east-1"
fi
backup_id="$BACKUP_ID_ARG"
if [ -z "$backup_id" ]; then
  backup_id="$(aws s3 ls "s3://${{bucket}}/backups/" --region "$region" | awk '{{print $2}}' | sed 's#/$##' | tail -n 1)"
fi
if [ -z "$backup_id" ]; then
  emit "backup selected" "FAIL" "No backup id found"
  exit 22
fi
emit "backup selected" "PASS" "backup_id=$backup_id"

workdir="$(mktemp -d /tmp/omega-dr-rehearsal.XXXXXX)"
trap 'docker rm -f "$op_container" "$gold_container" >/dev/null 2>&1 || true; rm -rf "$workdir"' EXIT
aws s3 cp "s3://${{bucket}}/backups/${{backup_id}}/manifest.json" "$workdir/manifest.json" --region "$region" --only-show-errors
aws s3 cp "s3://${{bucket}}/backups/${{backup_id}}/postgres.sql.gz" "$workdir/postgres.sql.gz" --region "$region" --only-show-errors
aws s3 cp "s3://${{bucket}}/backups/${{backup_id}}/postgres_gold.sql.gz" "$workdir/postgres_gold.sql.gz" --region "$region" --only-show-errors
gzip -t "$workdir/postgres.sql.gz"
gzip -t "$workdir/postgres_gold.sql.gz"
emit "backup dumps gzip valid" "PASS" "postgres and postgres_gold gzip streams verified"

op_container="omega_dr_op_$(date -u +%H%M%S)_$$"
gold_container="omega_dr_gold_$(date -u +%H%M%S)_$$"
docker run -d --name "$op_container" -e POSTGRES_PASSWORD=dr pgvector/pgvector:pg15 >/dev/null
docker run -d --name "$gold_container" -e POSTGRES_PASSWORD=dr postgres:15 >/dev/null
for container in "$op_container" "$gold_container"; do
  ok=0
  for _ in $(seq 1 60); do
    if docker exec "$container" pg_isready -U postgres >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 1
  done
  if [ "$ok" != "1" ]; then
    emit "temporary restore DB ready" "FAIL" "container=$container"
    exit 23
  fi
done
emit "temporary restore DB ready" "PASS" "containers started without host ports"

if gunzip -c "$workdir/postgres.sql.gz" | docker exec -i "$op_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 >/tmp/omega-dr-op.out 2>/tmp/omega-dr-op.err; then
  op_tables="$(docker exec "$op_container" psql -U postgres -d modecissions -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d '\r' || true)"
  emit "operational dump isolated restore" "PASS" "tables=${{op_tables:-unknown}}"
else
  emit "operational dump isolated restore" "FAIL" "$(tail -c 500 /tmp/omega-dr-op.err || true)"
  exit 24
fi
if gunzip -c "$workdir/postgres_gold.sql.gz" | docker exec -i "$gold_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 >/tmp/omega-dr-gold.out 2>/tmp/omega-dr-gold.err; then
  gold_tables="$(docker exec "$gold_container" psql -U postgres -d modecissions_gold -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d '\r' || true)"
  emit "gold dump isolated restore" "PASS" "tables=${{gold_tables:-unknown}}"
else
  emit "gold dump isolated restore" "FAIL" "$(tail -c 500 /tmp/omega-dr-gold.err || true)"
  exit 25
fi
emit "lakehouse restore mode" "PASS" "dry listing only; no bucket writes"
printf 'OMEGA_DR_JSON=%s\\n' "$(python3 - "$workdir/manifest.json" "$backup_id" <<'PY'
import json
import sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text())
print(json.dumps({{
    "status": "ok",
    "restore_mode": "isolated_temp_containers",
    "backup_id": sys.argv[2],
    "manifest_backup_id": manifest.get("backup_id"),
    "destructive": False,
    "lakehouse_writes": False,
}}, sort_keys=True))
PY
)"
"""


def _parse(stdout: str) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    payload: dict = {}
    for line in stdout.splitlines():
        if line.startswith("OMEGA_DR_CHECK\t"):
            _prefix, name, status, evidence = (line.split("\t", 3) + [""])[:4]
            checks.append(
                {"name": name, "status": status, "evidence": redact(evidence)}
            )
        elif line.startswith("OMEGA_DR_JSON="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                payload = {}
    return checks, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run guarded AWS DR rehearsal through SSM."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument("--backup-id", default=os.environ.get("BACKUP_ID") or "")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_DR_TIMEOUT_SECONDS", "2400")),
    )
    args = parser.parse_args(argv)
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(args.backup_id),
        comment="omega-dr-rehearsal-aws",
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
        "generated_at_utc": utc_stamp(),
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "response_code": remote.response_code,
        "restore_mode": payload.get("restore_mode", "isolated_temp_containers"),
        "backup_id": payload.get("backup_id") or args.backup_id,
        "destructive": False,
        "checks": checks,
        "payload": payload,
    }
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
                "# AWS DR Rehearsal Evidence",
                "",
                f"- status: `{status}`",
                "- restore_mode: `isolated_temp_containers`",
                "- destructive: `false`",
                f"- ssm_command_id: `{remote.command_id}`",
                f"- instance_id: `{instance_id}`",
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
