#!/usr/bin/env python3
"""Probe Superset health and Gold/RLS posture on AWS via SSM."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-superset-probe"


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _remote_script() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"
emit() {
  local name="$1"
  local status="$2"
  local evidence="${3:-}"
  local unblock="${4:-}"
  evidence="${evidence//$'\t'/ }"; evidence="${evidence//$'\r'/ }"; evidence="${evidence//$'\n'/ }"
  unblock="${unblock//$'\t'/ }"; unblock="${unblock//$'\r'/ }"; unblock="${unblock//$'\n'/ }"
  printf 'OMEGA_SUPERSET_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
}
env_value() {
  local key="$1"
  if [ -f "${DEPLOY_DIR}/.env" ]; then
    awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1)}' "${DEPLOY_DIR}/.env" | tail -n 1 | sed "s/^[ '\"]//; s/[ '\"]$//"
  fi
}
compose_files() {
  printf -- '-f docker-compose.aws.yml '
  if [ "$(env_value DEPLOY_CARTRIDGES_SAME_HOST)" = "true" ] && [ -f docker-compose.cartridges.yml ]; then
    printf -- '-f docker-compose.cartridges.yml '
  fi
}
cd "$DEPLOY_DIR"
curl -fsS --max-time 10 http://127.0.0.1:8088/health >/tmp/omega-superset-health.out 2>/tmp/omega-superset-health.err && emit "Superset health" "PASS" "status=200" || emit "Superset health" "FAIL" "$(cat /tmp/omega-superset-health.err /tmp/omega-superset-health.out 2>/dev/null | head -c 220)"
psql_gold() { docker compose $(compose_files) exec -T postgres_gold psql -U postgres -p 5433 -d modecissions_gold -tAc "$1" 2>&1 | tr -d '\r'; }
gold_conn="$(psql_gold "SELECT 1;")"
[ "$gold_conn" = "1" ] && emit "Gold connection" "PASS" "SELECT 1" || emit "Gold connection" "FAIL" "$gold_conn"
role="$(psql_gold "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_gold_reader') THEN 'omega_gold_reader' ELSE 'omega_refinement_gold' END;")"
bypass="$(psql_gold "SELECT COALESCE((SELECT rolbypassrls::text FROM pg_roles WHERE rolname='${role}'), 'missing');")"
[ "$bypass" = "false" ] && emit "Superset Gold role NOBYPASSRLS" "PASS" "role=$role rolbypassrls=false" || emit "Superset Gold role NOBYPASSRLS" "FAIL" "role=${role:-missing} rolbypassrls=${bypass:-unknown}"
weak="$(psql_gold "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relname LIKE 'gold\_%' ESCAPE '\' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);")"
[ "$weak" = "0" ] && emit "Gold FORCE RLS" "PASS" "weak_gold_tables=0" || emit "Gold FORCE RLS" "FAIL" "weak_gold_tables=${weak:-unknown}"
datasets="$(docker compose $(compose_files) exec -T postgres psql -U postgres -d superset -tAc "SELECT COUNT(*) FROM tables WHERE table_name LIKE 'gold_%';" 2>&1 | tr -d '\r' || true)"
if [ -n "$datasets" ] && [ "${datasets:-0}" -gt 0 ] 2>/dev/null; then
  emit "Superset datasets registered" "PASS" "gold_datasets=$datasets"
else
  emit "Superset datasets registered" "BLOCKED" "gold_datasets=${datasets:-unknown}" "Register Gold datasets through Superset service user if dashboards are required."
fi
emit "Tenant context propagation" "BLOCKED" "Superset health/Gold role checked; per-user tenant context requires dashboard/session probe." "P1: add Superset embedded/session tenant context probe before enterprise."
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_SUPERSET_CHECK\t"):
            continue
        _prefix, name, status, evidence, unblock = (line.split("\t", 4) + [""])[:5]
        checks.append(
            Check(
                name=name,
                status=status,
                evidence=redact(evidence),
                unblock=redact(unblock),
            )
        )
    return checks


def _write_report(evidence_dir: Path, summary: dict) -> None:
    lines = [
        "# AWS Superset Probe",
        "",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- ssm_command_id: `{summary.get('ssm_command_id')}`",
        "",
        "| Check | Status | Evidence | Unblock |",
        "|---|---|---|---|",
    ]
    for check in summary["checks"]:
        evidence = str(check["evidence"]).replace("|", "\\|")
        unblock = str(check.get("unblock") or "").replace("|", "\\|")
        lines.append(
            f"| {check['name']} | {check['status']} | {evidence} | {unblock} |"
        )
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe Superset AWS health and Gold/RLS posture."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_SUPERSET_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-aws-superset-probe",
        timeout_seconds=args.timeout_seconds,
    )
    checks = _parse(remote.stdout)
    checks.append(
        Check(
            "SSM command completed",
            "PASS"
            if remote.status == "Success" and remote.response_code == 0
            else "FAIL",
            f"command_id={remote.command_id} status={remote.status} response_code={remote.response_code}",
        )
    )
    status = (
        "FAIL"
        if any(c.status == "FAIL" for c in checks)
        else "BLOCKED"
        if any(c.status == "BLOCKED" for c in checks)
        else "PASS"
    )
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "instance_id": instance_id,
        "region": args.region,
        "ssm_command_id": remote.command_id,
        "ssm_status": remote.status,
        "ssm_response_code": remote.response_code,
        "checks": [asdict(check) for check in checks],
    }
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(
        redact(remote.stdout), encoding="utf-8"
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(remote.stderr), encoding="utf-8"
    )
    _write_report(evidence_dir, summary)
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
    return 0 if status == "PASS" else 2 if status == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
