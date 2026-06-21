#!/usr/bin/env python3
"""Probe the automatic Gold -> Control Room loop through /internal/intelligence/gold-refresh."""

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


DEFAULT_EVIDENCE_ROOT = (
    REPO / "docs" / "release-evidence" / "control-room-gold-engine-aws"
)


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
  printf 'OMEGA_CONTROL_ROOM_GOLD_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
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
psql_main() {
  docker compose $(compose_files) exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'
}
cd "$DEPLOY_DIR"

writeback="$(env_value CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK | tr '[:upper:]' '[:lower:]')"
case "$writeback" in
  ""|"0"|"false"|"disabled")
    emit "external writeback disabled" "PASS" "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=${writeback:-<unset>}"
    ;;
  *)
    emit "external writeback disabled" "FAIL" "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=$writeback" "Disable external writeback before probing Control Room automation."
    ;;
esac

scope="$(psql_main "
SELECT tenant_id::text || '|' || workspace_id::text
  FROM public.gold_consultor_mensual
 WHERE tenant_id IS NOT NULL
   AND workspace_id IS NOT NULL
 GROUP BY tenant_id, workspace_id
 ORDER BY COUNT(*) DESC
 LIMIT 1;
" || true)"
scope="$(echo "$scope" | sed '/^$/d' | head -n 1)"
if [ -z "$scope" ] || ! echo "$scope" | grep -q "|"; then
  emit "Replicon Gold scope" "FAIL" "${scope:-<empty>}" "Seed Replicon Gold rows before running the probe."
  exit 0
fi
tenant_id="${scope%%|*}"
workspace_id="${scope#*|}"
emit "Replicon Gold scope" "PASS" "tenant=${tenant_id} workspace=${workspace_id}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
dag_run_id="control-room-gold-engine-probe-${stamp}"
run_ref="gold-refresh:${workspace_id}:replicon:${dag_run_id}"

run_endpoint() {
  TENANT_ID="$tenant_id" WORKSPACE_ID="$workspace_id" DAG_RUN_ID="$dag_run_id" \
  docker compose $(compose_files) exec -T \
    -e TENANT_ID -e WORKSPACE_ID -e DAG_RUN_ID \
    console python - <<'PY' 2>&1 || true
import asyncio
import json
import os

from app.routers import intelligence as intelligence_router


async def main() -> None:
    body = intelligence_router.GoldRefreshIntelligenceRequest(
        tenant_id=os.environ["TENANT_ID"],
        workspace_id=os.environ["WORKSPACE_ID"],
        cartridge_id="replicon",
        airflow_dag_run_id=os.environ["DAG_RUN_ID"],
        pipeline_run_id=f"control-room-gold-engine-probe:{os.environ['DAG_RUN_ID']}",
        materialization_status="success",
        datasets=["consultor_mensual", "pnl_mensual"],
        finished_at="2026-06-21T00:00:00+00:00",
    )
    result = await intelligence_router.intelligence_gold_refresh_internal(
        body,
        internal_service="airflow",
    )
    print("OMEGA_CONTROL_ROOM_GOLD_ENDPOINT_JSON=" + json.dumps(result, sort_keys=True))


asyncio.run(main())
PY
}

first_probe="$(run_endpoint)"
first_json="$(echo "$first_probe" | grep 'OMEGA_CONTROL_ROOM_GOLD_ENDPOINT_JSON=' | tail -n 1 | cut -d= -f2-)"
if echo "$first_json" | grep -q '"ok": true' && echo "$first_json" | grep -q '"signals": [1-9]' && echo "$first_json" | grep -q '"idempotent": false'; then
  emit "Gold refresh endpoint first run" "PASS" "$first_json"
else
  emit "Gold refresh endpoint first run" "FAIL" "${first_probe:-<empty>}" "Inspect /internal/intelligence/gold-refresh and Replicon Gold readiness."
fi

events_first="$(psql_main "SELECT COUNT(*) FROM control_room_item_events WHERE workspace_id::text = '${workspace_id}' AND metadata->>'run_ref' = '${run_ref}';" || true)"
events_first="$(echo "$events_first" | sed '/^$/d' | tail -n 1)"

second_probe="$(run_endpoint)"
second_json="$(echo "$second_probe" | grep 'OMEGA_CONTROL_ROOM_GOLD_ENDPOINT_JSON=' | tail -n 1 | cut -d= -f2-)"
if echo "$second_json" | grep -q '"ok": true' && echo "$second_json" | grep -q '"idempotent": true' && echo "$second_json" | grep -q '"signals": 0'; then
  emit "Gold refresh endpoint retry idempotent" "PASS" "$second_json"
else
  emit "Gold refresh endpoint retry idempotent" "FAIL" "${second_probe:-<empty>}" "Run_ref retries must short-circuit without persisting duplicate signals."
fi

events_second="$(psql_main "SELECT COUNT(*) FROM control_room_item_events WHERE workspace_id::text = '${workspace_id}' AND metadata->>'run_ref' = '${run_ref}';" || true)"
events_second="$(echo "$events_second" | sed '/^$/d' | tail -n 1)"
if [ "${events_first:-0}" = "${events_second:-0}" ] && [ "${events_second:-0}" -gt 0 ] 2>/dev/null; then
  emit "Control Room retry does not duplicate events" "PASS" "events=${events_second}"
else
  emit "Control Room retry does not duplicate events" "FAIL" "first=${events_first:-unknown} second=${events_second:-unknown}" "Ensure duplicate run_ref returns before persist_artifacts."
fi

valid_items="$(psql_main "
SELECT COUNT(*)
  FROM control_room_items
 WHERE workspace_id::text = '${workspace_id}'
   AND metadata->>'run_ref' = '${run_ref}'
   AND metadata->>'run_mode' = 'gold_refresh'
   AND metadata->>'control_origin' IN ('intelligence_signal', 'generic_gold_signal')
   AND metadata ? 'math_provenance'
   AND metadata->'math_provenance'->>'ruleset_version' = 'control_room_gold_signal.v1'
   AND metadata ? 'priority'
   AND jsonb_typeof(metadata->'priority'->'drivers') = 'object'
   AND metadata ? 'monte_carlo'
   AND COALESCE(metadata->'monte_carlo'->>'status', '') <> ''
   AND metadata ? 'bayesian_calibration'
   AND metadata->'bayesian_calibration'->>'status' IN ('calibrated', 'not_calibrated')
   AND metadata ? 'evidence_pack';
" || true)"
valid_items="$(echo "$valid_items" | sed '/^$/d' | tail -n 1)"
if [ "${valid_items:-0}" -gt 0 ] 2>/dev/null; then
  emit "Control Room Gold metadata" "PASS" "items=${valid_items} run_ref=${run_ref}"
else
  emit "Control Room Gold metadata" "FAIL" "items=${valid_items:-unknown} run_ref=${run_ref}" "Persist control_origin, math_provenance, priority, monte_carlo, bayesian_calibration and evidence_pack."
fi
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_CONTROL_ROOM_GOLD_CHECK\t"):
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
        "# AWS Control Room Gold Engine Probe",
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
        description="Probe the AWS Gold refresh to Control Room engine loop."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_CONTROL_ROOM_GOLD_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-control-room-gold-engine-probe",
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
