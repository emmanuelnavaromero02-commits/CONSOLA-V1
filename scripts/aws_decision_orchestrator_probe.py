#!/usr/bin/env python3
"""Probe the Prompt 19A decision orchestrator on AWS via SSM."""

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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-decision-orchestrator-probe"


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
  printf 'OMEGA_DECISION_ORCHESTRATOR_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
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
import asyncio
import os
import uuid

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import decision_orchestrator


def require(condition, message):
    if not condition:
        raise SystemExit(message)


async def expect_error(coro, status_code, label):
    try:
        await coro
    except decision_orchestrator.DecisionOrchestratorError as exc:
        require(exc.status_code == status_code, f"{label}:expected_{status_code}_got_{exc.status_code}")
        return
    raise SystemExit(f"{label}:expected_{status_code}_not_raised")


async def main():
    pool = await auth.pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            '''
            SELECT u.id, u.email, COALESCE(u.role, 'admin') AS role,
                   w.id AS workspace_id, w.tenant_id AS tenant_id
              FROM users u
              JOIN user_workspace_roles uwr ON uwr.user_id = u.id
              JOIN roles r ON r.id = uwr.role_id
              JOIN workspaces w ON w.id = uwr.workspace_id
             WHERE u.is_active = TRUE
             ORDER BY CASE WHEN r.name IN ('admin', 'workspace_admin', 'tenant_admin') THEN 0 ELSE 1 END,
                      u.id ASC
             LIMIT 1
            '''
        )
        require(row, "no_active_workspace_user")
        cartridge_id = await conn.fetchval("SELECT id FROM cartridges ORDER BY id LIMIT 1")
        require(cartridge_id, "no_cartridge_for_control_room_source")
        tenant_name = "Decision Orchestrator Probe Tenant " + str(uuid.uuid4())
        tenant_slug = "decision-orchestrator-probe-" + str(uuid.uuid4())[:8]
        tenant_has_slug = await conn.fetchval(
            '''
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'tenants'
                   AND column_name = 'slug'
            )
            '''
        )
        if tenant_has_slug:
            tenant_b = await conn.fetchval(
                '''
                INSERT INTO tenants(name, slug)
                VALUES ($1, $2)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                ''',
                tenant_name,
                tenant_slug,
            )
        else:
            tenant_b = await conn.fetchval(
                '''
                INSERT INTO tenants(name)
                VALUES ($1)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                ''',
                tenant_name,
            )
        workspace_b = await conn.fetchval(
            '''
            INSERT INTO workspaces(tenant_id, name)
            VALUES ($1, $2)
            ON CONFLICT (tenant_id, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            ''',
            tenant_b,
            "Decision Orchestrator Probe Workspace",
        )

    base = {
        "id": int(row["id"]),
        "email": row["email"],
        "role": "workspace_admin",
        "active_tenant_id": str(row["tenant_id"]),
        "active_workspace_id": str(row["workspace_id"]),
    }
    tenant_b_user = dict(
        base,
        active_tenant_id=str(tenant_b),
        active_workspace_id=str(workspace_b),
    )

    suffix = str(uuid.uuid4())
    risk_item = "decision-orchestrator-risk-" + suffix
    action_item = "decision-orchestrator-action-" + suffix
    async with scoped_db_for_user(pool, base) as (conn, tenant_id, workspace_id):
        await conn.execute(
            '''
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id, domain,
                item_kind, title, severity, status, metadata
            )
            VALUES ($1, $2, $3, $4, 'Operacion', 'intelligence_signal',
                    'Forecast anomaly probability breach', 'high', 'open',
                    $5::jsonb)
            ON CONFLICT (workspace_id, item_id) DO UPDATE
            SET title = EXCLUDED.title,
                metadata = EXCLUDED.metadata,
                last_seen_at = NOW()
            ''',
            tenant_id,
            workspace_id,
            risk_item,
            cartridge_id,
            '{"evidence_refs":[{"type":"control_room_item","id":"risk"}]}',
        )
        await conn.execute(
            '''
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id, domain,
                item_kind, title, severity, status, metadata
            )
            VALUES ($1, $2, $3, $4, 'Operacion', 'intelligence_signal',
                    'Notify owner and create task', 'medium', 'open', '{}'::jsonb)
            ON CONFLICT (workspace_id, item_id) DO UPDATE
            SET title = EXCLUDED.title,
                metadata = EXCLUDED.metadata,
                last_seen_at = NOW()
            ''',
            tenant_id,
            workspace_id,
            action_item,
            cartridge_id,
        )

    risk = await decision_orchestrator.orchestrate(
        base,
        {"source_type": "control_room_item", "source_id": risk_item},
    )
    run = risk["orchestration"]
    require(run["problem_type"] == "risk_forecast", "risk_problem_type")
    engine_names = [item["name"] for item in run["recommended_engines"]]
    require("monte_carlo" in engine_names, "monte_carlo_recommended")
    require("bayesian_calibration" in engine_names, "bayes_recommended")
    require(run["engine_plan"]["candidate_engines_executed"] is False, "candidate_not_executed")
    require(run["engine_plan"]["available_engines_executed"] is False, "engines_not_auto_executed")
    require(run["external_action_id"] is None, "action_flag_off_by_default")

    hidden = await decision_orchestrator.list_orchestrations(tenant_b_user)
    require(all(item["orchestration_id"] != run["orchestration_id"] for item in hidden["orchestrations"]), "tenant_b_list_leak")
    await expect_error(
        decision_orchestrator.get_orchestration(tenant_b_user, run["orchestration_id"]),
        404,
        "tenant_b_get_hidden",
    )
    await expect_error(
        decision_orchestrator.orchestrate(
            base,
            {
                "source_type": "control_room_item",
                "source_id": risk_item,
                "metrics": {"tenant_id": "malicious"},
            },
        ),
        422,
        "scope_injection_rejected",
    )

    os.environ["DECISION_ORCHESTRATOR_CREATE_ACTIONS"] = "true"
    action = await decision_orchestrator.orchestrate(
        base,
        {"source_type": "control_room_item", "source_id": action_item},
    )
    action_run = action["orchestration"]
    require(action_run["action_recommended"] is True, "action_recommended")
    require(action_run["external_action_id"], "external_action_created")
    external_action = action_run.get("external_action") or {}
    require(external_action.get("status") == "pending_approval", "action_pending_approval")
    require(external_action.get("adapter_name") == "sandbox", "action_sandbox")
    metadata = external_action.get("metadata") or {}
    require(metadata.get("created_by_orchestrator") is True, "metadata_created_by_orchestrator")
    require(metadata.get("requires_human_approval") is True, "metadata_requires_human")
    require(metadata.get("orchestration_id") == action_run["orchestration_id"], "metadata_orchestration_id")
    require(not external_action.get("execution_result"), "action_not_executed")
    print("decision_orchestrator=PASS")


asyncio.run(main())
PY
)"
echo "$console_probe" | grep -q "decision_orchestrator=PASS" \
  && emit "Console decision orchestrator" "PASS" "classification, RLS isolation and pending sandbox proposal verified in console container" \
  || emit "Console decision orchestrator" "FAIL" "$console_probe" "Deploy the Console image containing Prompt 19A and migration 99u."

psql_main() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }

rls="$(psql_main "SELECT (relrowsecurity AND relforcerowsecurity)::text FROM pg_class WHERE oid='public.decision_orchestration_runs'::regclass;")"
[ "$rls" = "true" ] \
  && emit "decision_orchestration_runs FORCE RLS" "PASS" "relrowsecurity=true relforcerowsecurity=true" \
  || emit "decision_orchestration_runs FORCE RLS" "FAIL" "$rls" "Enable and FORCE RLS on decision_orchestration_runs."

true_policies="$(psql_main "SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='decision_orchestration_runs' AND (qual='true' OR with_check='true');")"
[ "$true_policies" = "0" ] \
  && emit "decision_orchestration_runs no permissive policy" "PASS" "USING/WITH CHECK true policies=0" \
  || emit "decision_orchestration_runs no permissive policy" "FAIL" "true_policies=${true_policies:-unknown}" "Replace permissive policies with GUC-scoped policies."

bypass="$(psql_main "SELECT rolbypassrls::text FROM pg_roles WHERE rolname='omega_console';")"
[ "$bypass" = "false" ] \
  && emit "omega_console NOBYPASSRLS" "PASS" "rolbypassrls=false" \
  || emit "omega_console NOBYPASSRLS" "FAIL" "rolbypassrls=${bypass:-unknown}" "ALTER ROLE omega_console NOBYPASSRLS."
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_DECISION_ORCHESTRATOR_CHECK\t"):
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
        "# AWS Decision Orchestrator Probe",
        "",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- instance_id: `{summary['instance_id']}`",
        f"- region: `{summary['region']}`",
        "",
        "| Check | Status | Evidence | Unblock |",
        "| --- | --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(
            "| {name} | `{status}` | {evidence} | {unblock} |".format(
                name=check["name"],
                status=check["status"],
                evidence=check["evidence"] or "",
                unblock=check["unblock"] or "",
            )
        )
    (evidence_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_INSTANCE_ID"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION))
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    args = parser.parse_args()

    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    evidence_dir = args.evidence_root / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    remote = send_ssm_script(
        instance_id=instance_id,
        region=args.region,
        script=_remote_script(),
        comment="omega-decision-orchestrator-aws-probe",
        timeout_seconds=900,
    )
    stdout = redact(remote.stdout)
    stderr = redact(remote.stderr)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(stdout, encoding="utf-8")
    (evidence_dir / "remote_stderr_redacted.txt").write_text(stderr, encoding="utf-8")

    checks = _parse(stdout)
    if not checks:
        checks = [
            Check(
                name="Probe output",
                status="FAIL",
                evidence="No OMEGA_DECISION_ORCHESTRATOR_CHECK lines found.",
                unblock="Inspect remote stdout/stderr evidence.",
            )
        ]
    summary = {
        "status": _overall_status(checks),
        "generated_at_utc": utc_now(),
        "instance_id": instance_id,
        "region": args.region,
        "ssm_command_id": remote.command_id,
        "checks": [asdict(check) for check in checks],
    }
    write_json(evidence_dir / "summary.json", summary)
    _write_report(evidence_dir, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
