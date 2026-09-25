#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
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
GHCR_AUTH_RUNNER = REPO / "infra" / "terraform" / "deploy" / "ghcr-auth-run.sh"

RELEASE_SERVICES = (
    "console",
    "workspace",
    "refinement",
    "vault",
    "mcp-infra",
    "airflow",
    "replicon",
    "hubspot",
    "salesforce",
    "banxico",
    "inegi",
    "sec-edgar",
    "sap-hcm",
    "sap-successfactors",
    "sap-s4hana",
    "sap-b1",
)

RELEASE_IMAGES = (
    "console",
    "workspace",
    "refinement",
    "vault",
    "mcp-infra",
    "airflow",
    "replicon",
    "hubspot",
    "salesforce",
    "banxico",
    "inegi",
    "sec_edgar",
    "sap_hcm",
    "sap_successfactors",
    "sap_s4hana",
    "sap_b1",
)


def _remote_script(target_tag: str, run_smoke: str, confirm: bool) -> str:
    auth_runner_b64 = base64.b64encode(GHCR_AUTH_RUNNER.read_bytes()).decode("ascii")
    release_services = " ".join(RELEASE_SERVICES)
    release_images = " ".join(RELEASE_IMAGES)
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
TARGET_TAG={json.dumps(target_tag)}
RUN_SMOKE_VALUE={json.dumps(run_smoke)}
CONFIRM_ROLLBACK={json.dumps("1" if confirm else "0")}
AUTH_RUNNER_B64={json.dumps(auth_runner_b64)}
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
if [ ! -f "$DEPLOY_DIR/.env" ]; then
  emit "deploy env exists" "FAIL" "$DEPLOY_DIR/.env missing"
  exit 19
fi
if [[ -z "$TARGET_TAG" || "$TARGET_TAG" == "latest" || ! "$TARGET_TAG" =~ ^v[0-9] ]]; then
  emit "target immutable tag" "FAIL" "target=$TARGET_TAG"
  exit 20
fi
emit "target immutable tag" "PASS" "target=$TARGET_TAG"

# The host may still run a release that predates authenticated pulls.  Ship
# this candidate's public helper source through SSM; credentials themselves
# are fetched only on EC2 from Secrets Manager via the instance profile.
auth_workdir="$(mktemp -d /tmp/omega-rollback-auth.XXXXXX)"
cleanup_auth_workdir() {{
  case "$auth_workdir" in
    /tmp/omega-rollback-auth.*) rm -rf -- "$auth_workdir" ;;
    *) return 70 ;;
  esac
}}
trap cleanup_auth_workdir EXIT
REMOTE_AUTH_RUNNER="$auth_workdir/ghcr-auth-run.sh"
printf '%s' "$AUTH_RUNNER_B64" | base64 --decode > "$REMOTE_AUTH_RUNNER"
chmod 700 "$REMOTE_AUTH_RUNNER"
unset AUTH_RUNNER_B64

preflight_env="$auth_workdir/rollback.env"
cp "$DEPLOY_DIR/.env" "$preflight_env"
chmod 600 "$preflight_env"
python3 - "$preflight_env" "$TARGET_TAG" <<'PYPREFLIGHT'
from pathlib import Path
import sys

path = Path(sys.argv[1])
target = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
out = []
seen = False
for line in lines:
    if line.startswith("IMAGE_TAG="):
        out.append(f"IMAGE_TAG={{target}}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"IMAGE_TAG={{target}}")
path.write_text("\\n".join(out) + "\\n", encoding="utf-8")
PYPREFLIGHT

COMPOSE_FILES=(-f docker-compose.aws.yml -f docker-compose.cartridges.yml)
RELEASE_SERVICES=({release_services})
RELEASE_IMAGES=({release_images})
if [[ "${{#RELEASE_SERVICES[@]}}" -ne 16 || "${{#RELEASE_IMAGES[@]}}" -ne 16 ]]; then
  emit "rollback release image inventory" "FAIL" "expected exactly 16 services and images"
  exit 21
fi
available_services="$(docker compose --env-file "$preflight_env" "${{COMPOSE_FILES[@]}}" config --services)"
for service in "${{RELEASE_SERVICES[@]}}"; do
  if ! grep -qx "$service" <<< "$available_services"; then
    emit "rollback release image inventory" "FAIL" "missing compose service=$service"
    exit 21
  fi
done
if OMEGA_DEPLOY_ENV_FILE="$preflight_env" \
  bash "$REMOTE_AUTH_RUNNER" \
    docker compose --env-file "$preflight_env" "${{COMPOSE_FILES[@]}}" \
      pull --quiet "${{RELEASE_SERVICES[@]}}" \
      >"$auth_workdir/pull.out" 2>"$auth_workdir/pull.err"; then
  emit "rollback release images pull" "PASS" "16/16 tag=$TARGET_TAG images=${{RELEASE_IMAGES[*]}}"
else
  emit "rollback release images pull" "FAIL" "less than 16/16 images pullable for tag=$TARGET_TAG"
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
OMEGA_DEPLOY_ENV_FILE="$preflight_env" \
  bash "$REMOTE_AUTH_RUNNER" env \
    RUN_BACKUP_BEFORE_ROLLBACK="${{RUN_BACKUP_BEFORE_ROLLBACK:-1}}" \
    RUN_SMOKE="$RUN_SMOKE_VALUE" \
    bash rollback.sh "$TARGET_TAG"
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
