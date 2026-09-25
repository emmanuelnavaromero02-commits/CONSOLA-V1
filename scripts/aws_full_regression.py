#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-full-regression"


@dataclass
class Step:
    name: str
    status: str
    evidence: str
    returncode: int | None = None
    critical: bool = True


def _run_step(
    name: str,
    cmd: list[str],
    env: dict[str, str],
    evidence_dir: Path,
    *,
    critical: bool = True,
    blocked_ok: bool = False,
    timeout: int = 1800,
) -> Step:
    result = subprocess.run(
        cmd,
        cwd=REPO,
        env={**os.environ, **env},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    step_dir = evidence_dir / name.replace(" ", "-")
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "stdout_redacted.txt").write_text(
        redact(result.stdout), encoding="utf-8"
    )
    (step_dir / "stderr_redacted.txt").write_text(
        redact(result.stderr), encoding="utf-8"
    )
    summary = {}
    try:
        last = next(
            line
            for line in reversed(result.stdout.splitlines())
            if line.strip().startswith("{")
        )
        summary = json.loads(last)
    except Exception:
        summary = {}
    child_status = str(summary.get("status") or "").upper()
    if result.returncode == 0:
        status = "PASS"
    elif blocked_ok and (result.returncode == 2 or child_status == "BLOCKED"):
        status = "BLOCKED"
    else:
        status = "FAIL"
    evidence = (
        summary.get("evidence_dir") or (result.stderr or result.stdout).strip()[:400]
    )
    return Step(
        name=name,
        status=status,
        evidence=redact(str(evidence)),
        returncode=result.returncode,
        critical=critical,
    )


def _control_room_proof_script() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"
emit() {
  local name="$1"
  local status="$2"
  local evidence="${3:-}"
  evidence="${evidence//$'\t'/ }"; evidence="${evidence//$'\r'/ }"; evidence="${evidence//$'\n'/ }"
  printf 'OMEGA_CONTROL_PROOF\t%s\t%s\t%s\n' "$name" "$status" "$evidence"
}
env_value() { key="$1"; awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1)}' "$DEPLOY_DIR/.env" | tail -n 1 | sed "s/^[ '\"]//; s/[ '\"]$//"; }
compose_files() { printf -- '-f docker-compose.aws.yml '; if [ "$(env_value DEPLOY_CARTRIDGES_SAME_HOST)" = "true" ] && [ -f docker-compose.cartridges.yml ]; then printf -- '-f docker-compose.cartridges.yml '; fi; }
cd "$DEPLOY_DIR"
psql_op() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }
check_count() {
  local name="$1"; shift
  local value
  value="$(psql_op "$*")"
  if [ "${value:-0}" -gt 0 ] 2>/dev/null; then emit "$name" "PASS" "rows=$value"; else emit "$name" "FAIL" "rows=${value:-unknown}"; fi
}
check_count "replicon intelligence signals" "SELECT CASE WHEN to_regclass('public.intelligence_signals') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM intelligence_signals WHERE cartridge_id='replicon') END;"
check_count "decision intelligence snapshots" "SELECT CASE WHEN to_regclass('public.decision_intelligence_snapshots') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM decision_intelligence_snapshots WHERE source_system='replicon') END;"
check_count "control room items" "SELECT CASE WHEN to_regclass('public.control_room_items') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM control_room_items WHERE cartridge_id='replicon' AND COALESCE(item_kind,'') <> 'source_state') END;"
check_count "decisions linked or stored" "SELECT CASE WHEN to_regclass('public.decisions') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM decisions) END;"
check_count "dry-run action runs" "SELECT CASE WHEN to_regclass('public.action_runs') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM action_runs WHERE mode='dry_run' AND status='dry_run_completed') END;"
check_count "execute internal action runs" "SELECT CASE WHEN to_regclass('public.action_runs') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM action_runs WHERE mode='execute' AND status='completed') END;"
check_count "prediction outcomes" "SELECT CASE WHEN to_regclass('public.prediction_outcomes') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM prediction_outcomes) END;"
check_count "control room lessons" "SELECT CASE WHEN to_regclass('public.control_room_lessons') IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM control_room_lessons) END;"
writeback="$(docker compose $(compose_files) exec -T console sh -lc 'case "${CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK:-false}" in 1|true|yes|on) echo enabled;; *) echo disabled;; esac' 2>/dev/null || echo unknown)"
[ "$writeback" = "disabled" ] && emit "external writeback disabled" "PASS" "disabled" || emit "external writeback disabled" "FAIL" "$writeback"
"""


def _control_room_proof(region: str, instance_id: str, evidence_dir: Path) -> Step:
    remote = send_ssm_script(
        region=region,
        instance_id=instance_id,
        script=_control_room_proof_script(),
        comment="omega-control-room-cycle-proof",
        timeout_seconds=600,
    )
    step_dir = evidence_dir / "control-room-cycle-proof"
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "stdout_redacted.txt").write_text(
        redact(remote.stdout), encoding="utf-8"
    )
    (step_dir / "stderr_redacted.txt").write_text(
        redact(remote.stderr), encoding="utf-8"
    )
    checks: list[dict[str, str]] = []
    for line in remote.stdout.splitlines():
        if line.startswith("OMEGA_CONTROL_PROOF\t"):
            _prefix, name, status, evidence = (line.split("\t", 3) + [""])[:4]
            checks.append(
                {"name": name, "status": status, "evidence": redact(evidence)}
            )
    write_json(
        step_dir / "summary.json",
        {
            "status": "PASS"
            if remote.status == "Success" and all(c["status"] == "PASS" for c in checks)
            else "FAIL",
            "ssm_command_id": remote.command_id,
            "checks": checks,
        },
    )
    status = (
        "PASS"
        if remote.status == "Success" and all(c["status"] == "PASS" for c in checks)
        else "FAIL"
    )
    return Step(
        "control-room-cycle-proof", status, str(step_dir), remote.response_code, True
    )


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# AWS Full Regression Evidence",
        "",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- deploy_ref: `{summary.get('deploy_ref') or '<not-set>'}`",
        f"- image_tag: `{summary.get('image_tag') or '<not-set>'}`",
        f"- public_url: `{summary.get('public_url') or '<not-set>'}`",
        "",
        "| Step | Status | Critical | Evidence |",
        "|---|---|---|---|",
    ]
    for step in summary["steps"]:
        evidence = str(step["evidence"]).replace("|", "\\|")
        lines.append(
            f"| {step['name']} | {step['status']} | {step['critical']} | {evidence} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run official AWS full regression gate."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("PUBLIC_CONSOLE_URL")
        or os.environ.get("CONSOLE_URL")
        or "",
    )
    parser.add_argument("--deploy-ref", default=os.environ.get("DEPLOY_REF") or "")
    parser.add_argument("--image-tag", default=os.environ.get("IMAGE_TAG") or "")
    parser.add_argument(
        "--tenant-id",
        default=os.environ.get("OMEGA_SEED_TENANT_ID")
        or os.environ.get("OMEGA_BACKTEST_TENANT_ID")
        or "",
    )
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("OMEGA_SEED_WORKSPACE_ID")
        or os.environ.get("OMEGA_BACKTEST_WORKSPACE_ID")
        or "",
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.public_url:
        raise SystemExit("PUBLIC_CONSOLE_URL or --public-url is required")
    if not args.tenant_id or not args.workspace_id:
        raise SystemExit("OMEGA_SEED_TENANT_ID/OMEGA_SEED_WORKSPACE_ID are required")

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    env = {
        "AWS_REGION": args.region,
        "AWS_APP_INSTANCE_ID": instance_id,
        "PUBLIC_CONSOLE_URL": args.public_url,
        "DEPLOY_REF": args.deploy_ref,
        "IMAGE_TAG": args.image_tag,
        "OMEGA_SEED_TENANT_ID": args.tenant_id,
        "OMEGA_SEED_WORKSPACE_ID": args.workspace_id,
        "OMEGA_BACKTEST_TENANT_ID": args.tenant_id,
        "OMEGA_BACKTEST_WORKSPACE_ID": args.workspace_id,
    }
    steps = [
        _run_step(
            "beta-smoke-aws",
            [
                "python3",
                "scripts/beta_smoke_aws.py",
                "--evidence-dir",
                str(evidence_dir / "beta-smoke-aws"),
            ],
            env,
            evidence_dir,
        ),
        _run_step(
            "tenant-ab-aws",
            [
                "python3",
                "scripts/tenant_ab_e2e.py",
                "--target",
                "aws",
                "--evidence-dir",
                str(evidence_dir / "tenant-ab-aws"),
            ],
            env,
            evidence_dir,
        ),
        _run_step(
            "decision-backtest-aws",
            [
                "python3",
                "scripts/run_decision_backtest.py",
                "--target",
                "aws",
                "--evidence-root",
                str(evidence_dir / "decision-backtest-aws"),
            ],
            env,
            evidence_dir,
        ),
        _control_room_proof(args.region, instance_id, evidence_dir),
        _run_step(
            "observability-report",
            [
                "python3",
                "scripts/aws_observability_report.py",
                "--evidence-dir",
                str(evidence_dir / "observability-report"),
            ],
            env,
            evidence_dir,
            blocked_ok=True,
        ),
        _run_step(
            "superset-probe",
            [
                "python3",
                "scripts/aws_superset_probe.py",
                "--evidence-dir",
                str(evidence_dir / "superset-probe"),
            ],
            env,
            evidence_dir,
            critical=False,
            blocked_ok=True,
        ),
        _run_step(
            "tls-status",
            [
                "python3",
                "scripts/aws_tls_status.py",
                "--evidence-dir",
                str(evidence_dir / "tls-status"),
            ],
            env,
            evidence_dir,
            critical=False,
            blocked_ok=True,
        ),
    ]
    status = (
        "FAIL"
        if any(step.critical and step.status == "FAIL" for step in steps)
        else "BLOCKED"
        if any(step.critical and step.status == "BLOCKED" for step in steps)
        else "PASS"
    )
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "instance_id": instance_id,
        "region": args.region,
        "public_url": args.public_url,
        "deploy_ref": args.deploy_ref,
        "image_tag": args.image_tag,
        "steps": [asdict(step) for step in steps],
    }
    write_json(evidence_dir / "summary.json", summary)
    _write_report(evidence_dir / "REPORT.md", summary)
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir)}, indent=2))
    return 0 if status == "PASS" else 2 if status == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
