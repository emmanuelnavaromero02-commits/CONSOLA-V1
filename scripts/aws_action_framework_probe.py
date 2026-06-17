#!/usr/bin/env python3
"""Probe the Prompt 18A external action framework on AWS via SSM."""

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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-action-framework-probe"


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
  printf 'OMEGA_ACTION_FRAMEWORK_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
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

from fastapi import HTTPException

from app.services import auth, external_actions


def require(condition, message):
    if not condition:
        raise SystemExit(message)


async def expect_http(coro, status_code, label):
    try:
        await coro
    except HTTPException as exc:
        require(
            exc.status_code == status_code,
            f"{label}:expected_http_{status_code}_got_{exc.status_code}",
        )
        return
    raise SystemExit(f"{label}:expected_http_{status_code}_not_raised")


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
        tenant_name = "Action Framework Probe Tenant " + str(uuid.uuid4())
        tenant_slug = "action-framework-probe-" + str(uuid.uuid4())[:8]
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
            "Action Framework Probe Workspace",
        )

    base = {
        "id": int(row["id"]),
        "email": row["email"],
        "role": "workspace_admin",
        "active_tenant_id": str(row["tenant_id"]),
        "active_workspace_id": str(row["workspace_id"]),
    }
    admin = dict(base, role="admin")
    tenant_b_user = dict(
        base,
        active_tenant_id=str(tenant_b),
        active_workspace_id=str(workspace_b),
    )

    suffix = str(uuid.uuid4())
    await expect_http(
        external_actions.propose(
            base,
            {
                "source_type": "control_room",
                "source_id": "probe-non-sandbox-" + suffix,
                "action_type": "external_write",
                "adapter_name": "real_writeback_probe",
                "payload": {},
                "idempotency_key": "probe-non-sandbox-" + suffix,
            },
        ),
        409,
        "non_sandbox_blocked",
    )
    await expect_http(
        external_actions.propose(
            base,
            {
                "source_type": "control_room",
                "source_id": "probe-secret-" + suffix,
                "action_type": "sandbox_notify",
                "payload": {"api_key": "redaction-test-value"},
                "idempotency_key": "probe-secret-" + suffix,
            },
        ),
        400,
        "secret_payload_rejected",
    )

    proposed = await external_actions.propose(
        base,
        {
            "source_type": "control_room",
            "source_id": "probe-" + suffix,
            "action_type": "sandbox_notify",
            "adapter_name": "sandbox",
            "payload": {"sandbox_outcome": "success", "message": "probe"},
            "idempotency_key": "probe-propose-" + suffix,
        },
    )
    action_id = proposed["action"]["id"]
    require(proposed["action"]["status"] == "pending_approval", "proposal_status")
    require(proposed["action"]["payload"]["message"] == "probe", "payload_roundtrip")

    hidden_list = await external_actions.list_actions(tenant_b_user)
    require(all(item["id"] != action_id for item in hidden_list["actions"]), "tenant_b_list_leak")
    await expect_http(
        external_actions.get_action(tenant_b_user, action_id),
        404,
        "tenant_b_get_hidden",
    )

    dry_run = await external_actions.dry_run(
        base,
        action_id,
        {"idempotency_key": "probe-dry-run-" + suffix},
    )
    require(dry_run["action"]["status"] == "dry_run_ready", "dry_run_status")
    require(dry_run["dry_run_result"]["external_write"] is False, "dry_run_external_write")

    await expect_http(
        external_actions.approve(base, action_id, {"idempotency_key": "probe-self-approve-" + suffix}),
        403,
        "maker_checker_self_approval",
    )
    approved = await external_actions.approve(
        admin,
        action_id,
        {"idempotency_key": "probe-approve-" + suffix},
    )
    require(approved["action"]["status"] == "approved", "approved_status")

    executed = await external_actions.execute(
        admin,
        action_id,
        {"idempotency_key": "probe-execute-" + suffix},
    )
    require(executed["action"]["status"] == "succeeded", "execute_status")
    require(executed["execution_result"]["external_write"] is False, "execute_external_write")
    replay = await external_actions.execute(
        admin,
        action_id,
        {"idempotency_key": "probe-execute-" + suffix},
    )
    require(replay["action"]["id"] == action_id, "execute_idempotency_replay")

    second = await external_actions.propose(
        base,
        {
            "source_type": "control_room",
            "source_id": "probe-expired-" + suffix,
            "action_type": "sandbox_notify",
            "adapter_name": "sandbox",
            "payload": {},
            "idempotency_key": "probe-propose-expired-" + suffix,
        },
    )
    expired_action_id = second["action"]["id"]
    await external_actions.dry_run(
        base,
        expired_action_id,
        {"idempotency_key": "probe-dry-expired-" + suffix},
    )
    await external_actions.approve(
        admin,
        expired_action_id,
        {"idempotency_key": "probe-approve-expired-" + suffix},
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
            base["active_tenant_id"],
            base["active_workspace_id"],
        )
        update_status = await conn.execute(
            "UPDATE external_actions SET expires_at = NOW() - INTERVAL '1 second' WHERE id = $1::uuid",
            expired_action_id,
        )
        require(update_status.endswith(" 1"), "expire_update_scoped")
    await expect_http(
        external_actions.execute(
            admin,
            expired_action_id,
            {"idempotency_key": "probe-execute-expired-" + suffix},
        ),
        409,
        "expired_execute_blocked",
    )

    blocked = await external_actions.propose(
        base,
        {
            "source_type": "control_room",
            "source_id": "probe-disabled-" + suffix,
            "action_type": "sandbox_notify",
            "payload": {},
            "idempotency_key": "probe-propose-disabled-" + suffix,
        },
    )
    blocked_id = blocked["action"]["id"]
    await external_actions.dry_run(
        base,
        blocked_id,
        {"idempotency_key": "probe-dry-disabled-" + suffix},
    )
    await external_actions.approve(
        admin,
        blocked_id,
        {"idempotency_key": "probe-approve-disabled-" + suffix},
    )
    old_flag = os.environ.get("EXTERNAL_ACTION_SANDBOX_ENABLED")
    os.environ["EXTERNAL_ACTION_SANDBOX_ENABLED"] = "false"
    try:
        await expect_http(
            external_actions.execute(
                admin,
                blocked_id,
                {"idempotency_key": "probe-execute-disabled-" + suffix},
            ),
            409,
            "sandbox_flag_disabled",
        )
    finally:
        if old_flag is None:
            os.environ.pop("EXTERNAL_ACTION_SANDBOX_ENABLED", None)
        else:
            os.environ["EXTERNAL_ACTION_SANDBOX_ENABLED"] = old_flag

    async with pool.acquire() as conn:
        raw_secret_count = await conn.fetchval(
            "SELECT COUNT(*) FROM external_actions WHERE payload::text LIKE '%redaction-test-value%'"
        )
        terminal_events = await conn.fetchval(
            '''
            SELECT COUNT(*)
              FROM external_action_events
             WHERE action_id = $1::uuid
               AND event_type = 'execute_succeeded'
            ''',
            action_id,
        )
    require(raw_secret_count == 0, "secret_payload_persisted")
    require(terminal_events == 1, "duplicate_execute_terminal_event")
    print("action_framework=PASS")


asyncio.run(main())
PY
)"
echo "$console_probe" | grep -q "action_framework=PASS" \
  && emit "Console external action framework" "PASS" "sandbox lifecycle, maker/checker, idempotency, expiration, isolation and payload safety passed in console container" \
  || emit "Console external action framework" "FAIL" "$console_probe" "Deploy the Console image and migration containing Prompt 18A."

psql_main() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }

force_count="$(psql_main "SELECT COUNT(*) FROM pg_class WHERE oid IN ('public.external_actions'::regclass, 'public.external_action_events'::regclass, 'public.external_action_idempotency_keys'::regclass) AND relrowsecurity AND relforcerowsecurity;")"
[ "$force_count" = "3" ] \
  && emit "external action tables FORCE RLS" "PASS" "tables_with_force_rls=3" \
  || emit "external action tables FORCE RLS" "FAIL" "tables_with_force_rls=${force_count:-unknown}" "Apply 99t_external_action_framework.sql."

true_policies="$(psql_main "SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename IN ('external_actions','external_action_events','external_action_idempotency_keys') AND (qual='true' OR with_check='true');")"
[ "$true_policies" = "0" ] \
  && emit "external action no permissive RLS policy" "PASS" "USING/WITH CHECK true policies=0" \
  || emit "external action no permissive RLS policy" "FAIL" "true_policies=${true_policies:-unknown}" "Replace permissive policies with workspace-scoped policies."

bypass="$(psql_main "SELECT COUNT(*) FROM pg_roles WHERE rolname LIKE 'omega_%' AND rolbypassrls;")"
[ "$bypass" = "0" ] \
  && emit "omega roles NOBYPASSRLS" "PASS" "omega roles with BYPASSRLS=0" \
  || emit "omega roles NOBYPASSRLS" "FAIL" "bypass_roles=${bypass:-unknown}" "ALTER ROLE omega_* NOBYPASSRLS."
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_ACTION_FRAMEWORK_CHECK\t"):
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
        "# AWS Action Framework Probe",
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
        lines.append(f"| {check['name']} | {check['status']} | {evidence} | {unblock} |")
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe external action framework on AWS.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or "")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_ACTION_FRAMEWORK_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-aws-action-framework-probe",
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
    (evidence_dir / "remote_stdout_redacted.txt").write_text(redact(remote.stdout), encoding="utf-8")
    (evidence_dir / "remote_stderr_redacted.txt").write_text(redact(remote.stderr), encoding="utf-8")
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
