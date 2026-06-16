#!/usr/bin/env python3
"""Probe the deployed Bayesian probability loop on AWS via SSM."""

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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-bayesian-loop-probe"


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
  printf 'OMEGA_BAYESIAN_LOOP_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
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
from app.services.intelligence import calibration
from app.services.intelligence.baseline import build_metric_artifacts

def metric():
    return {
        "id": "forecast_weighted",
        "name": "Forecast ponderado",
        "dataset": "forecast_mensual",
        "entity": {"kind": "seller", "id_field": "owner_id", "label_field": "vendedor"},
        "time_field": "mes",
        "value_field": "forecast_ponderado_usd",
        "expected_behavior": "higher_is_good",
        "baseline": {"method": "moving_average", "minimum_history": 2, "window": 6},
        "impact": {"currency": "USD", "unit_value": 20},
        "signal_rules": {"warning_pct": 0.20, "critical_pct": 0.45},
        "action_templates": [],
    }

rows = [
    {"mes": "2026-01-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 100},
    {"mes": "2026-02-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 120},
    {"mes": "2026-03-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 110},
    {"mes": "2026-04-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 200},
]
group = calibration.source_type_calibration_group("hubspot", "forecast_weighted")
state = {
    "calibration_group": group,
    "posterior": {"alpha": 12.0, "beta": 18.0, "mean": 0.4},
    "metrics": {"sample_count": 30, "confidence_score": 1.0, "partial_pooling_applied": True, "prior_source": "global"},
}
artifacts, skipped = build_metric_artifacts(
    {"cartridge": "hubspot", "domain": "Ventas"},
    metric(),
    rows,
    calibration_states={group: state},
)
if skipped or not artifacts:
    raise SystemExit("fixture_no_signal")
decision = artifacts[0]["decision_intelligence"]
metadata = decision.get("calibration") or {}
if metadata.get("calibration_applied") is not True:
    raise SystemExit("calibration_not_applied")
if metadata.get("raw_probability") == metadata.get("calibrated_probability"):
    raise SystemExit("calibration_no_delta")
if decision.get("anomaly_probability") != metadata.get("calibrated_probability"):
    raise SystemExit("live_probability_not_calibrated")
if "not a calibrated Bayesian posterior" in str(decision.get("rationale")):
    raise SystemExit("stale_raw_disclaimer")
fallback = calibration.apply_calibration_to_probability(0.72, None)
if fallback["calibration_applied"] or fallback["calibrated_probability"] != fallback["raw_probability"]:
    raise SystemExit("fallback_not_raw")
parent = {
    "calibration_group": calibration.global_calibration_group("forecast_weighted"),
    "posterior": {"alpha": 71.0, "beta": 31.0, "mean": 71 / 102},
    "metrics": {"sample_count": 100},
}
prior = calibration.derive_partial_pooling_prior(parent, parent_calibration_group=parent["calibration_group"], prior_source="global")
if prior.get("partial_pooling_applied") is not True:
    raise SystemExit("partial_pooling_not_applied")
print("loop=PASS")
PY
)"
echo "$console_probe" | grep -q "loop=PASS" \
  && emit "Console Bayesian live loop" "PASS" "posterior applied to new Decision Intelligence payload in console container" \
  || emit "Console Bayesian live loop" "FAIL" "$console_probe" "Deploy the Console image containing Prompt 17.5."

psql_main() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }

for table in calibration_observations calibration_states; do
  rls="$(psql_main "SELECT (relrowsecurity AND relforcerowsecurity)::text FROM pg_class WHERE oid='public.${table}'::regclass;")"
  [ "$rls" = "true" ] \
    && emit "${table} FORCE RLS" "PASS" "relrowsecurity=true relforcerowsecurity=true" \
    || emit "${table} FORCE RLS" "FAIL" "$rls" "Enable and FORCE RLS on ${table}."
  true_policies="$(psql_main "SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='${table}' AND (qual='true' OR with_check='true');")"
  [ "$true_policies" = "0" ] \
    && emit "${table} no permissive policy" "PASS" "USING/WITH CHECK true policies=0" \
    || emit "${table} no permissive policy" "FAIL" "true_policies=${true_policies:-unknown}" "Replace permissive policies with GUC-scoped policies."
done

bypass="$(psql_main "SELECT rolbypassrls::text FROM pg_roles WHERE rolname='omega_console';")"
[ "$bypass" = "false" ] \
  && emit "omega_console NOBYPASSRLS" "PASS" "rolbypassrls=false" \
  || emit "omega_console NOBYPASSRLS" "FAIL" "rolbypassrls=${bypass:-unknown}" "ALTER ROLE omega_console NOBYPASSRLS."
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_BAYESIAN_LOOP_CHECK\t"):
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
        "# AWS Bayesian Loop Probe",
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
    parser = argparse.ArgumentParser(description="Probe Bayesian live loop on AWS.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or "")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_BAYESIAN_LOOP_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-aws-bayesian-loop-probe",
        timeout_seconds=args.timeout_seconds,
    )
    checks = _parse(remote.stdout)
    checks.append(
        Check(
            "SSM command completed",
            "PASS" if remote.status == "Success" and remote.response_code == 0 else "FAIL",
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
        redact(remote.stdout),
        encoding="utf-8",
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(remote.stderr),
        encoding="utf-8",
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
