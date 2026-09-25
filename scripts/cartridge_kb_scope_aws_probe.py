#!/usr/bin/env python3
"""Probe Prompt 20C cartridge KB scope controls on AWS via SSM."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_now, utc_stamp, write_json


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "cartridge-kb-scope-aws-probe"


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _short(text: str, limit: int = 700) -> str:
    compact = " ".join(redact(text).split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _remote_script() -> str:
    return r'''#!/usr/bin/env bash
set -euo pipefail
set +x

REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"

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

emit() {
  local name="$1"
  local status="$2"
  local evidence="${3:-}"
  local unblock="${4:-}"
  evidence="${evidence//$'\t'/ }"; evidence="${evidence//$'\r'/ }"; evidence="${evidence//$'\n'/ }"
  unblock="${unblock//$'\t'/ }"; unblock="${unblock//$'\r'/ }"; unblock="${unblock//$'\n'/ }"
  printf 'CARTRIDGE_KB_SCOPE_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
}

require_pattern() {
  local file="$1"
  local pattern="$2"
  if [ ! -f "${REPO_DIR}/${file}" ]; then
    return 2
  fi
  grep -Fq "$pattern" "${REPO_DIR}/${file}"
}

forbid_pattern() {
  local file="$1"
  local pattern="$2"
  if [ ! -f "${REPO_DIR}/${file}" ]; then
    return 2
  fi
  ! grep -Fq "$pattern" "${REPO_DIR}/${file}"
}

check_cartridge_contract() {
  local missing=()
  for cartridge in hubspot replicon sap_hcm sap_s4hana sap_successfactors sap_b1; do
    require_pattern "cartridges/${cartridge}/app/core/request_context.py" "def require_tenant_workspace_scope" || missing+=("${cartridge}:request_context")
    require_pattern "cartridges/${cartridge}/app/services/kb_service.py" "security_context = require_tenant_workspace_scope" || missing+=("${cartridge}:kb_service")
    require_pattern "cartridges/${cartridge}/app/core/sql_guard.py" "required_scope: str | None = None" || missing+=("${cartridge}:sql_guard_scope")
    require_pattern "cartridges/${cartridge}/app/core/sql_guard.py" "def _canonical_s3_path" || missing+=("${cartridge}:sql_guard_canonical")
    require_pattern "cartridges/${cartridge}/app/mcp_server.py" "security_context_denied" || missing+=("${cartridge}:mcp_deny")
    require_pattern "cartridges/${cartridge}/app/main.py" "x-security-context" || missing+=("${cartridge}:mcp_header_guard")
  done
  if [ "${#missing[@]}" -eq 0 ]; then
    emit "cartridge KB signed scope contract" "PASS" "hubspot/replicon/sap_hcm/sap_s4hana/sap_successfactors/sap_b1 fail closed with signed scope"
  else
    emit "cartridge KB signed scope contract" "FAIL" "${missing[*]}"
  fi
}

check_gateway_contract() {
  local missing=()
  require_pattern "mcp-infra/app/main.py" "def _canonical_storage_key" || missing+=("canonical_storage_key")
  require_pattern "mcp-infra/app/main.py" "def _key_has_exact_scope" || missing+=("exact_scope")
  require_pattern "mcp-infra/app/main.py" "cartridge tool lacks tenancy metadata" || missing+=("metadata_default_deny")
  require_pattern "mcp-infra/app/main.py" "cartridge tools require tenant/workspace scope" || missing+=("signed_scope_required")
  if [ "${#missing[@]}" -eq 0 ]; then
    emit "mcp-infra cartridge gateway scoped" "PASS" "gateway canonicalizes paths and denies cartridge tools without tenancy metadata"
  else
    emit "mcp-infra cartridge gateway scoped" "FAIL" "${missing[*]}"
  fi
}

check_lessons_and_lineage() {
  local missing=()
  require_pattern "console/app/services/lessons_service.py" 'scope = "workspace_global"' || missing+=("lessons_global_mapping")
  forbid_pattern "console/app/services/lessons_service.py" "OR scope='global'" || missing+=("lessons_global_or")
  require_pattern "refinement/app/dataset_store.py" "dataset name already exists outside the active workspace" || missing+=("dataset_name_guard")
  require_pattern "refinement/app/main.py" 'if tool == "get_lineage":' || missing+=("lineage_tool")
  require_pattern "refinement/app/main.py" 'store.get_dataset(args["name"], **_dataset_store_scope(sec))' || missing+=("lineage_scope_check")
  if [ "${#missing[@]}" -eq 0 ]; then
    emit "lessons and name-based reads scoped" "PASS" "global lessons remap and dataset/app/lineage reads validate workspace scope"
  else
    emit "lessons and name-based reads scoped" "FAIL" "${missing[*]}"
  fi
}

check_egress_contract() {
  local missing=()
  require_pattern "console/app/services/egress_guard.py" "def pinned_request_sync" || missing+=("sync_pinned_request")
  require_pattern "console/app/services/adapters/http_writeback.py" "await egress_guard.pinned_request(" || missing+=("http_writeback_pinned")
  require_pattern "console/app/services/adapters/replicon_adapter.py" "egress_guard.pinned_request_sync(" || missing+=("replicon_pinned")
  require_pattern "console/app/services/adapters/sap_hcm_adapter.py" "egress_guard.pinned_request_sync(" || missing+=("sap_pinned")
  forbid_pattern "console/app/services/adapters/http_writeback.py" "httpx.AsyncClient" || missing+=("http_writeback_httpx")
  forbid_pattern "console/app/services/adapters/replicon_adapter.py" "httpx.Client" || missing+=("replicon_httpx")
  forbid_pattern "console/app/services/adapters/sap_hcm_adapter.py" "httpx.Client" || missing+=("sap_httpx")
  if [ "${#missing[@]}" -eq 0 ]; then
    emit "configured writeback egress pinned" "PASS" "writeback adapters use DNS-pinned requests before sending secrets"
  else
    emit "configured writeback egress pinned" "FAIL" "${missing[*]}"
  fi
}

check_agents_policy() {
  local missing=()
  require_pattern "infra/init/99s_remaining_operational_rls.sql" "agents_console_refinement_scheduled_read_rls" || missing+=("policy_missing")
  require_pattern "infra/init/99s_remaining_operational_rls.sql" "omega_rls_workspace_matches(tenant_id, workspace_id)" || missing+=("workspace_match_missing")
  if grep -A20 -F "agents_console_refinement_scheduled_read_rls" "${REPO_DIR}/infra/init/99s_remaining_operational_rls.sql" | grep -Fq "workspace_id IS NOT NULL"; then
    missing+=("workspace_id_is_not_null_grant")
  fi
  if [ "${#missing[@]}" -eq 0 ]; then
    emit "scheduled agents residual RLS scoped" "PASS" "scheduled policy uses omega_rls_workspace_matches"
  else
    emit "scheduled agents residual RLS scoped" "FAIL" "${missing[*]}"
  fi
}

check_observability_db() {
  cd "$DEPLOY_DIR"
  docker compose $(compose_files) exec -T console python - <<'PY' 2>&1 || true
import asyncio
import json


TABLES = ("jobs", "run_logs", "extraction_runs", "kb_runs")


def emit(name, status, evidence, unblock=""):
    print("CARTRIDGE_KB_SCOPE_CHECK\t{}\t{}\t{}\t{}".format(name, status, evidence, unblock))


async def main():
    try:
        from app.services import auth
        pool = await auth.pool()
        rows = await pool.fetch(
            """
            SELECT c.relname,
                   c.relrowsecurity,
                   c.relforcerowsecurity,
                   COALESCE(bool_or(
                       lower(regexp_replace(COALESCE(pg_get_expr(p.polqual, p.polrelid), ''), '\\s+', '', 'g'))
                       IN ('true', '(true)')
                   ), false) AS permissive_true,
                   COUNT(p.polname) AS policies
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              LEFT JOIN pg_policy p ON p.polrelid = c.oid
             WHERE n.nspname = 'public'
               AND c.relname = ANY($1::text[])
             GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
            """,
            list(TABLES),
        )
        by_name = {row["relname"]: dict(row) for row in rows}
        missing = [table for table in TABLES if table not in by_name]
        bad = [
            table for table, row in by_name.items()
            if not row["relrowsecurity"]
            or not row["relforcerowsecurity"]
            or row["permissive_true"]
            or int(row["policies"] or 0) == 0
        ]
        if missing or bad:
            emit("observability run tables scoped RLS", "FAIL", json.dumps({"missing": missing, "bad": bad}, default=str))
        else:
            emit("observability run tables scoped RLS", "PASS", json.dumps(sorted(by_name)))
    except Exception as exc:
        emit("observability run tables scoped RLS", "BLOCKED", type(exc).__name__ + ": " + str(exc), "check console DB connectivity")


asyncio.run(main())
PY
}

check_cartridge_contract
check_gateway_contract
check_lessons_and_lineage
check_egress_contract
check_agents_policy
check_observability_db
'''


def _parse_checks(output: str) -> list[Check]:
    checks: list[Check] = []
    for line in output.splitlines():
        if not line.startswith("CARTRIDGE_KB_SCOPE_CHECK\t"):
            continue
        _prefix, name, status, evidence, *rest = line.split("\t")
        checks.append(Check(name=name, status=status, evidence=_short(evidence), unblock=_short(rest[0]) if rest else ""))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", default="", help="EC2 instance id. Defaults to tag/env resolver.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--no-evidence", action="store_true")
    args = parser.parse_args()

    instance_id = args.instance_id or resolve_instance_id(args.region)
    command = send_ssm_script(
        instance_id=instance_id,
        region=args.region,
        script=_remote_script(),
        comment="omega-cartridge-kb-scope-aws-probe",
        timeout_seconds=420,
    )
    output = command.stdout + "\n" + command.stderr
    checks = _parse_checks(output)
    if not checks:
        checks = [Check("cartridge KB scope probe harness", BLOCKED, _short(output), "Check SSM/docker compose access.")]

    status = PASS if checks and all(check.status == PASS for check in checks) else FAIL
    report = {
        "probe": "cartridge-kb-scope-aws-probe",
        "status": status,
        "generated_at": utc_now().isoformat(),
        "region": args.region,
        "instance_id": instance_id,
        "ssm_command_id": command.command_id,
        "checks": [asdict(check) for check in checks],
        "raw_output": _short(output, 4000),
    }
    if not args.no_evidence:
        args.evidence_root.mkdir(parents=True, exist_ok=True)
        write_json(args.evidence_root / f"cartridge-kb-scope-aws-probe-{utc_stamp()}.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
