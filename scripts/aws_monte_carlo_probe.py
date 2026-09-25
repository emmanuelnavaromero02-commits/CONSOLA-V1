#!/usr/bin/env python3

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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-monte-carlo-probe"


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
  printf 'OMEGA_MONTE_CARLO_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
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

console_probe="$(docker compose $(compose_files) exec -T console python - <<'PY' 2>&1 || true
from fastapi import HTTPException
from app.services.intelligence import monte_carlo
from app.services.intelligence import monte_carlo_service

payload = {
    "source_type": "manual_fixture",
    "source_id": "aws-probe",
    "iterations": 200,
    "seed": 42,
    "input_variables": {
        "benefit": {"distribution": "normal", "mean": 120.0, "stddev": 5.0},
        "cost": {"distribution": "fixed", "value": 40.0},
    },
    "options": [
        {
            "option_id": "a",
            "label": "A",
            "input_variables": {
                "benefit": {"distribution": "normal", "mean": 120.0, "stddev": 5.0}
            },
        },
        {
            "option_id": "b",
            "label": "B",
            "input_variables": {
                "benefit": {"distribution": "normal", "mean": 90.0, "stddev": 5.0}
            },
        },
    ],
}
first = monte_carlo.run_monte_carlo(payload)
second = monte_carlo.run_monte_carlo(payload)
if first["reproducibility_hash"] != second["reproducibility_hash"]:
    raise SystemExit("engine_hash_mismatch")
if first["option_comparison"]["options"][0]["option_id"] != "a":
    raise SystemExit("option_order_mismatch")
print("engine=PASS")

try:
    monte_carlo_service._validate_payload(dict(payload))
except HTTPException as exc:
    if exc.status_code == 403:
        print("manual_fixture_gate=PASS")
    else:
        raise SystemExit(f"manual_fixture_unexpected_status={exc.status_code}")
else:
    raise SystemExit("manual_fixture_allowed_without_flag")
PY
)"
echo "$console_probe" | grep -q "engine=PASS" \
  && emit "Console Monte Carlo engine" "PASS" "deterministic hash and option ordering verified in console container" \
  || emit "Console Monte Carlo engine" "FAIL" "$console_probe" "Build/deploy the Console image containing monte_carlo.py."
echo "$console_probe" | grep -q "manual_fixture_gate=PASS" \
  && emit "manual_fixture AWS gate" "PASS" "manual_fixture rejected without explicit synthetic flag" \
  || emit "manual_fixture AWS gate" "FAIL" "$console_probe" "Block manual_fixture in beta/prod unless MONTE_CARLO_ALLOW_SYNTHETIC=true."

psql_main() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }

exists="$(psql_main "SELECT (to_regclass('public.monte_carlo_simulations') IS NOT NULL)::text;")"
[ "$exists" = "true" ] \
  && emit "Monte Carlo table exists" "PASS" "public.monte_carlo_simulations" \
  || emit "Monte Carlo table exists" "FAIL" "$exists" "Apply infra/init/99q_monte_carlo_simulations.sql."

rls="$(psql_main "SELECT (relrowsecurity AND relforcerowsecurity)::text FROM pg_class WHERE oid='public.monte_carlo_simulations'::regclass;")"
[ "$rls" = "true" ] \
  && emit "Monte Carlo FORCE RLS" "PASS" "relrowsecurity=true relforcerowsecurity=true" \
  || emit "Monte Carlo FORCE RLS" "FAIL" "$rls" "Enable and FORCE RLS on monte_carlo_simulations."

bypass="$(psql_main "SELECT rolbypassrls::text FROM pg_roles WHERE rolname='omega_console';")"
[ "$bypass" = "false" ] \
  && emit "omega_console NOBYPASSRLS" "PASS" "rolbypassrls=false" \
  || emit "omega_console NOBYPASSRLS" "FAIL" "rolbypassrls=${bypass:-unknown}" "ALTER ROLE omega_console NOBYPASSRLS."

true_policies="$(psql_main "SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='monte_carlo_simulations' AND (qual='true' OR with_check='true');")"
[ "$true_policies" = "0" ] \
  && emit "Monte Carlo no permissive policy" "PASS" "USING/WITH CHECK true policies=0" \
  || emit "Monte Carlo no permissive policy" "FAIL" "true_policies=${true_policies:-unknown}" "Replace permissive policies with GUC-scoped policies."

workspace_count="$(psql_main "SELECT COUNT(*) FROM workspaces WHERE tenant_id IS NOT NULL;")"
if [ "${workspace_count:-0}" -lt 2 ] 2>/dev/null; then
  emit "Monte Carlo DB A/B rollback probe" "BLOCKED" "need at least two scoped workspaces; found ${workspace_count:-unknown}" "Create tenant A/B workspaces and rerun."
else
  ab_probe="$(psql_main "
BEGIN;
CREATE TEMP TABLE _mc_scope AS
  SELECT id::uuid workspace_id, tenant_id::uuid tenant_id, ROW_NUMBER() OVER (ORDER BY id) rn
  FROM workspaces
  WHERE tenant_id IS NOT NULL
  ORDER BY id
  LIMIT 2;
INSERT INTO monte_carlo_simulations (
  simulation_id, tenant_id, workspace_id, source_type, source_id, horizon_days,
  iterations, seed, model_version, input_variables, assumptions, output_metric,
  distribution_summary, sensitivity, option_comparison, evidence_refs,
  reproducibility_hash
)
SELECT
  'aws-probe-' || rn,
  tenant_id,
  workspace_id,
  'manual_fixture',
  'aws-probe',
  30,
  100,
  123,
  'monte_carlo.v1',
  '{\"x\":{\"distribution\":\"fixed\",\"value\":1}}'::jsonb,
  '{}'::jsonb,
  'net_value',
  '{}'::jsonb,
  '[]'::jsonb,
  '[]'::jsonb,
  '[]'::jsonb,
  'aws-probe-hash-' || rn
FROM _mc_scope;
	SELECT set_config('app.tenant_id', (SELECT tenant_id::text FROM _mc_scope WHERE rn=1), true);
	SELECT set_config('app.workspace_id', (SELECT workspace_id::text FROM _mc_scope WHERE rn=1), true);
	SET ROLE omega_console;
	SELECT 'visible=' || COUNT(*)::text FROM monte_carlo_simulations WHERE source_id='aws-probe';
RESET ROLE;
ROLLBACK;
" || true)"
  echo "$ab_probe" | grep -q "visible=1" \
    && emit "Monte Carlo DB A/B rollback probe" "PASS" "scope A saw exactly one temporary simulation; transaction rolled back" \
    || emit "Monte Carlo DB A/B rollback probe" "FAIL" "$ab_probe" "Inspect RLS policy and app.tenant_id/app.workspace_id GUC usage."
fi
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_MONTE_CARLO_CHECK\t"):
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


def _overall_status(checks: list[Check]) -> str:
    if any(check.status == "FAIL" for check in checks):
        return "FAIL"
    if any(check.status == "BLOCKED" for check in checks):
        return "BLOCKED"
    return "PASS"


def _write_report(evidence_dir: Path, summary: dict) -> None:
    lines = [
        "# AWS Monte Carlo Probe",
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
        description="Probe Monte Carlo runtime and RLS posture on AWS."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_MONTE_CARLO_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-aws-monte-carlo-probe",
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
    status = _overall_status(checks)
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
