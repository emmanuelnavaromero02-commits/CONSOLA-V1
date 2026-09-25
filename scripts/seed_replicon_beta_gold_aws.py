#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_now, utc_stamp, write_json


PASS = "PASS"
FAIL = "FAIL"
DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "seed-replicon-beta-gold-aws"


@dataclass
class Check:
    name: str
    status: str
    evidence: str


def _short(text: str, limit: int = 900) -> str:
    compact = " ".join(redact(text).split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _remote_script(*, tenant_id: str, workspace_id: str, verify_runs: int) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x

REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
TENANT_ID={json.dumps(tenant_id)}
WORKSPACE_ID={json.dumps(workspace_id)}
VERIFY_RUNS={int(verify_runs)}

emit() {{
  local name="$1"
  local status="$2"
  local evidence="$3"
  evidence="$(printf '%s' "$evidence" | tr '\\t\\r\\n' '   ')"
  printf 'OMEGA_CHECK\\t%s\\t%s\\t%s\\n' "$name" "$status" "$evidence"
}}

cd "$DEPLOY_DIR"
if [ ! -f .env ]; then
  emit "AWS deploy env exists" "FAIL" ".env missing in $DEPLOY_DIR"
  exit 20
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

network="$(docker network ls --format '{{{{.Name}}}}' | grep 'modecissions_net$' | head -n 1 || true)"
if [ -z "$network" ]; then
  emit "compose network exists" "FAIL" "no docker network ending in modecissions_net"
  exit 21
fi
emit "compose network exists" "PASS" "network=$network"

docker run --rm \\
  --network "$network" \\
  -v "$REPO_DIR:/work:ro" \\
  -w /work \\
  -e POSTGRES_PASSWORD \\
  -e OMEGA_SEED_TENANT_ID="$TENANT_ID" \\
  -e OMEGA_SEED_WORKSPACE_ID="$WORKSPACE_ID" \\
  -e OMEGA_SEED_DATABASE_URL="postgresql://postgres:${{POSTGRES_PASSWORD}}@postgres:5432/modecissions" \\
  -e OMEGA_SEED_GOLD_DATABASE_URL="postgresql://postgres:${{POSTGRES_PASSWORD}}@postgres_gold:5433/modecissions_gold" \\
  -e OMEGA_SEED_VERIFY_IDEMPOTENT_RUNS="$VERIFY_RUNS" \\
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 \\
  python:3.12-slim \\
  bash -lc 'python -m pip install -q psycopg2-binary && python scripts/seed_replicon_beta_gold.py --verify-idempotent-runs "${{OMEGA_SEED_VERIFY_IDEMPOTENT_RUNS}}" --json' \\
  > /tmp/omega_replicon_seed_result.json

python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("/tmp/omega_replicon_seed_result.json").read_text())
latest = payload["runs"][-1]
status = "PASS" if payload.get("idempotent") else "FAIL"
print(
    "OMEGA_CHECK\\tReplicon seed idempotent\\t%s\\truns=%s rows=%s checksum=%s before_equals_after=%s"
    % (status, len(payload["runs"]), latest["row_count"], latest["checksum"], payload.get("before_equals_after"))
)
for dataset, details in sorted(payload["after"]["datasets"].items()):
    table = details["table"]
    rows = details["row_count"]
    checksum = details["checksum"]
    state = "PASS" if rows > 0 else "FAIL"
    print("OMEGA_CHECK\\t%s scoped checksum\\t%s\\trows=%s checksum=%s" % (table, state, rows, checksum))
print("OMEGA_SEED_JSON=" + json.dumps(payload, sort_keys=True, default=str))
PY
rm -f /tmp/omega_replicon_seed_result.json
"""


def _parse_checks(stdout: str) -> tuple[list[Check], dict | None]:
    checks: list[Check] = []
    payload: dict | None = None
    for line in stdout.splitlines():
        if line.startswith("OMEGA_CHECK\t"):
            _prefix, name, status, evidence = (line.split("\t", 3) + [""])[:4]
            checks.append(Check(name=name, status=status, evidence=_short(evidence)))
        elif line.startswith("OMEGA_SEED_JSON="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                payload = None
    return checks, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed AWS Replicon beta Gold scope via SSM.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or "")
    parser.add_argument("--tenant-id", default=os.environ.get("OMEGA_SEED_TENANT_ID") or "")
    parser.add_argument("--workspace-id", default=os.environ.get("OMEGA_SEED_WORKSPACE_ID") or "")
    parser.add_argument("--verify-runs", type=int, default=int(os.environ.get("OMEGA_SEED_VERIFY_IDEMPOTENT_RUNS", "3")))
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("OMEGA_AWS_SEED_TIMEOUT_SECONDS", "1200")))
    args = parser.parse_args(argv)

    if not args.tenant_id or not args.workspace_id:
        raise SystemExit("OMEGA_SEED_TENANT_ID and OMEGA_SEED_WORKSPACE_ID are required for AWS seed")
    if args.verify_runs < 3:
        raise SystemExit("--verify-runs must be >= 3 for AWS idempotency evidence")

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(tenant_id=args.tenant_id, workspace_id=args.workspace_id, verify_runs=args.verify_runs),
        comment="omega-seed-replicon-beta-gold",
        timeout_seconds=args.timeout_seconds,
    )
    checks, payload = _parse_checks(remote.stdout)
    checks.append(
        Check(
            name="SSM command completed",
            status=PASS if remote.status == "Success" else FAIL,
            evidence=f"command_id={remote.command_id} status={remote.status} response_code={remote.response_code}",
        )
    )
    status = FAIL if any(check.status == FAIL for check in checks) else PASS
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "tenant_id": args.tenant_id,
        "workspace_id": args.workspace_id,
        "verify_runs": args.verify_runs,
        "checks": [asdict(check) for check in checks],
        "seed_result": payload,
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(redact(remote.stdout), encoding="utf-8")
    (evidence_dir / "remote_stderr_redacted.txt").write_text(redact(remote.stderr), encoding="utf-8")
    lines = [
        "# AWS Replicon Beta Gold Seed Evidence",
        "",
        f"- status: `{status}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- ssm_command_id: `{remote.command_id}`",
        f"- instance_id: `{instance_id}`",
        f"- region: `{args.region}`",
        f"- tenant_id: `{args.tenant_id}`",
        f"- workspace_id: `{args.workspace_id}`",
        f"- verify_runs: `{args.verify_runs}`",
        "",
        "| Check | Status | Evidence |",
        "|---|---|---|",
    ]
    for check in checks:
        evidence = check.evidence.replace("|", "\\|")
        lines.append(f"| {check.name} | {check.status} | {evidence} |")
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir), "ssm_command_id": remote.command_id}, indent=2))
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
