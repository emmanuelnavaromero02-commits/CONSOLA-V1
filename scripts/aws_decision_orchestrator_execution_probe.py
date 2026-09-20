#!/usr/bin/env python3
"""Probe Prompt 19B decision orchestrator engine execution on AWS via SSM."""

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
    REPO / "docs" / "release-evidence" / "aws-decision-orchestrator-execution-probe"
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
  printf 'OMEGA_DECISION_ORCHESTRATOR_EXECUTION_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
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
import json
import uuid

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import (
    calibration,
    decision_orchestrator,
    orchestrator_execution,
)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


async def expect_error(coro, status_code, label):
    try:
        await coro
    except Exception as exc:
        actual = getattr(exc, "status_code", None)
        require(actual == status_code, f"{label}:expected_{status_code}_got_{actual}")
        return
    raise SystemExit(f"{label}:expected_{status_code}_not_raised")


CREATED: dict = {}


async def _cleanup():
    # Undo every write this probe made. These probes run against a REAL
    # deployment. The sibling calibration probe wraps its writes in
    # BEGIN/ROLLBACK; that is impossible here because the orchestrator opens its
    # own pooled connections and would not see uncommitted rows. So the rows are
    # committed and removed here, and the probe reports whether it left the
    # database clean.
    if not CREATED:
        return
    pool = await auth.pool()
    base = CREATED.get("base")
    items = [value for value in (CREATED.get("items") or []) if value]
    signal_id = CREATED.get("signal_id")
    state_id = CREATED.get("calibration_state_id")
    sources = [value for value in [*items, signal_id] if value]
    leftovers = []
    if base and sources:
        async with scoped_db_for_user(pool, base) as (conn, _tenant_id, _workspace_id):
            await conn.execute(
                '''
                DELETE FROM decision_orchestration_executions
                 WHERE orchestration_id IN (
                     SELECT orchestration_id FROM decision_orchestration_runs
                      WHERE source_id = ANY($1::text[])
                 )
                ''',
                sources,
            )
            await conn.execute(
                'DELETE FROM decision_orchestration_runs WHERE source_id = ANY($1::text[])',
                sources,
            )
            if signal_id:
                await conn.execute(
                    'DELETE FROM monte_carlo_simulations WHERE source_id = $1', signal_id
                )
                await conn.execute(
                    'DELETE FROM decision_options WHERE signal_id = $1', signal_id
                )
            if state_id:
                await conn.execute(
                    'DELETE FROM calibration_states WHERE state_id = $1', state_id
                )
            if items:
                await conn.execute(
                    'DELETE FROM control_room_items WHERE item_id = ANY($1::text[])',
                    items,
                )
            if signal_id:
                await conn.execute(
                    'DELETE FROM intelligence_signals WHERE signal_id = $1', signal_id
                )
            if state_id and await conn.fetchval(
                'SELECT count(*) FROM calibration_states WHERE state_id = $1', state_id
            ):
                leftovers.append('calibration_states')
            if signal_id and await conn.fetchval(
                'SELECT count(*) FROM intelligence_signals WHERE signal_id = $1',
                signal_id,
            ):
                leftovers.append('intelligence_signals')
            if items and await conn.fetchval(
                'SELECT count(*) FROM control_room_items WHERE item_id = ANY($1::text[])',
                items,
            ):
                leftovers.append('control_room_items')
    async with pool.acquire() as conn:
        if CREATED.get("workspace_b"):
            await conn.execute(
                'DELETE FROM workspaces WHERE id = $1', CREATED["workspace_b"]
            )
        if CREATED.get("tenant_b"):
            await conn.execute('DELETE FROM tenants WHERE id = $1', CREATED["tenant_b"])
    print("probe_cleanup=" + ("OK" if not leftovers else "INCOMPLETE:" + ",".join(leftovers)))


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
        require(cartridge_id, "no_cartridge_for_sources")
        tenant_name = "Decision Orchestrator Execution Probe Tenant " + str(uuid.uuid4())
        tenant_slug = "decision-orchestrator-exec-probe-" + str(uuid.uuid4())[:8]
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
            "Decision Orchestrator Execution Probe Workspace",
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
    signal_id = "decision-orchestrator-exec-signal-" + suffix
    risk_item = "decision-orchestrator-exec-risk-" + suffix
    temporal_item = "decision-orchestrator-exec-temporal-" + suffix
    calibration_group = "probe-risk:" + suffix[:8]
    model_version = calibration.MODEL_VERSION
    CREATED.update(
        {
            "base": base,
            "signal_id": signal_id,
            "items": [risk_item, temporal_item],
            "calibration_state_id": "probe-state-" + suffix,
            "tenant_b": tenant_b,
            "workspace_b": workspace_b,
        }
    )

    async with scoped_db_for_user(pool, base) as (conn, tenant_id, workspace_id):
        await conn.execute(
            '''
            INSERT INTO intelligence_signals (
                signal_id, tenant_id, workspace_id, cartridge_id, dataset, domain,
                entity_kind, entity_id, entity_label, metric, period_key,
                actual_value, expected_value, deviation_value, deviation_pct,
                severity, signal_type, status, confidence, summary, metadata
            )
            VALUES (
                $1, $2, $3, $4, 'probe_dataset', 'Operacion',
                'account', 'probe-account', 'Probe Account', 'delay_cost', '2026-06',
                130, 100, 30, 0.30,
                'high', 'risk', 'open', 0.82, 'Probe risk signal',
                '{}'::jsonb
            )
            ON CONFLICT (workspace_id, signal_id) DO UPDATE
            SET actual_value = EXCLUDED.actual_value,
                expected_value = EXCLUDED.expected_value,
                deviation_value = EXCLUDED.deviation_value,
                summary = EXCLUDED.summary,
                updated_at = NOW()
            ''',
            signal_id,
            tenant_id,
            workspace_id,
            cartridge_id,
        )
        await conn.execute(
            '''
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id, domain,
                item_kind, title, severity, status, metadata
            )
            VALUES ($1, $2, $3, $4, 'Operacion', 'intelligence_signal',
                    'Forecast anomaly probability breach with Monte Carlo uncertainty',
                    'high', 'open', $5::jsonb)
            ON CONFLICT (workspace_id, item_id) DO UPDATE
            SET title = EXCLUDED.title,
                metadata = EXCLUDED.metadata,
                last_seen_at = NOW()
            ''',
            tenant_id,
            workspace_id,
            risk_item,
            cartridge_id,
            json.dumps({"evidence_refs": [{"type": "intelligence_signal", "id": signal_id}]}),
        )
        await conn.execute(
            '''
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id, domain,
                item_kind, title, severity, status, metadata
            )
            VALUES ($1, $2, $3, $4, 'Operacion', 'intelligence_signal',
                    'Real-time feedback control dynamic trajectory lead time',
                    'high', 'open', '{}'::jsonb)
            ON CONFLICT (workspace_id, item_id) DO UPDATE
            SET title = EXCLUDED.title,
                metadata = EXCLUDED.metadata,
                last_seen_at = NOW()
            ''',
            tenant_id,
            workspace_id,
            temporal_item,
            cartridge_id,
        )
        await conn.execute(
            '''
            INSERT INTO calibration_states (
                state_id, tenant_id, workspace_id, calibration_group, model_version,
                prior, posterior, metrics, sample_count, hit_count, miss_count,
                partial_count, unknown_count, confidence_score, reproducibility_hash
            )
            VALUES (
                $1, $2, $3, $4, $5,
                '{"alpha":1,"beta":1}'::jsonb,
                '{"alpha":19,"beta":7,"mean":0.730769}'::jsonb,
                '{"sample_count":24,"confidence_score":0.74}'::jsonb,
                24, 18, 6, 0, 0, 0.74, $6
            )
            ON CONFLICT (workspace_id, calibration_group, model_version) DO UPDATE
            SET posterior = EXCLUDED.posterior,
                metrics = EXCLUDED.metrics,
                sample_count = EXCLUDED.sample_count,
                hit_count = EXCLUDED.hit_count,
                miss_count = EXCLUDED.miss_count,
                confidence_score = EXCLUDED.confidence_score,
                updated_at = NOW()
            ''',
            "probe-state-" + suffix,
            tenant_id,
            workspace_id,
            calibration_group,
            model_version,
            "probe-hash-" + suffix,
        )

    risk = await decision_orchestrator.orchestrate(
        base,
        {"source_type": "control_room_item", "source_id": risk_item},
    )
    run = risk["orchestration"]
    require(run["problem_type"] == "risk_forecast", "risk_classification")
    result = await orchestrator_execution.execute_engines(
        base,
        run["orchestration_id"],
        {
            "engine_inputs": {
                "monte_carlo": {
                    "source_type": "signal",
                    "source_id": signal_id,
                    "horizon_days": 30,
                    "iterations": 200,
                    "input_variables": {
                        "baseline_value": {"type": "fixed", "value": 100},
                        "expected_delta": {"type": "fixed", "value": -12},
                        "delay_days": {"type": "triangular", "low": 1, "mode": 3, "high": 7},
                        "cost_per_day": {"type": "fixed", "value": 4},
                    },
                    "output_metric": "net_value",
                    "breach_threshold": 90,
                    "breach_direction": "below",
                },
                "bayesian_calibration": {
                    "calibration_group": calibration_group,
                    "model_version": model_version,
                },
            }
        },
    )
    aggregate = result["aggregate"]
    require(aggregate["executed_engines"] == ["monte_carlo", "bayesian_calibration"], "executed_engines")
    evidence_types = {item["type"] for item in aggregate["evidence_refs"]}
    require("monte_carlo_simulation" in evidence_types, "mc_evidence")
    require("calibration_state" in evidence_types, "bayes_evidence")
    require(not aggregate["failed_engines"], "no_failed_engines")

    repeat = await orchestrator_execution.execute_engines(
        base,
        run["orchestration_id"],
        {
            "engine_inputs": {
                "monte_carlo": {
                    "source_type": "signal",
                    "source_id": signal_id,
                    "input_variables": {"baseline_value": {"type": "fixed", "value": 100}},
                },
                "bayesian_calibration": {"calibration_group": calibration_group},
            }
        },
    )
    require(len(repeat["executions"]) == len(result["executions"]), "idempotent_execution_budget")

    await expect_error(
        orchestrator_execution.list_executions(tenant_b_user, run["orchestration_id"]),
        404,
        "tenant_b_execution_hidden",
    )
    await expect_error(
        orchestrator_execution.execute_engines(
            base,
            run["orchestration_id"],
            {"engine_inputs": {"monte_carlo": {"tenant_id": "malicious"}}},
        ),
        422,
        "scope_injection_rejected",
    )

    temporal = await decision_orchestrator.orchestrate(
        base,
        {"source_type": "control_room_item", "source_id": temporal_item},
    )
    temporal_run = temporal["orchestration"]
    require(temporal_run["problem_type"] == "temporal_control", "temporal_classification")
    temporal_result = await orchestrator_execution.execute_engines(
        base,
        temporal_run["orchestration_id"],
        {"engine_inputs": {}},
    )
    require(
        {"engine": "mpc_candidate", "status": "candidate_only"}
        in temporal_result["aggregate"]["candidate_engines"],
        "mpc_candidate_only",
    )
    require(temporal_run.get("external_action_id") is None, "no_external_action_created")

    print("decision_orchestrator_execution=PASS")


async def _run():
    try:
        await main()
    finally:
        try:
            await _cleanup()
        except Exception as exc:  # noqa: BLE001 - report, never mask the result
            print("probe_cleanup=FAILED:" + type(exc).__name__)


asyncio.run(_run())
PY
)"
echo "$console_probe" | grep -q "decision_orchestrator_execution=PASS" \
  && emit "Console decision orchestrator execution" "PASS" "classification, MC execution, Bayes lookup, candidate-only and tenant isolation verified" \
  || emit "Console decision orchestrator execution" "FAIL" "$console_probe" "Deploy Console image containing Prompt 19B and migration 99v."


echo "$console_probe" | grep -q "probe_cleanup=OK" \
  && emit "Probe left no rows behind" "PASS" "probe_cleanup=OK" \
  || emit "Probe left no rows behind" "FAIL" "cleanup incomplete" "Delete the leftover probe rows (decision-orchestrator-exec-*) by hand."

psql_main() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }

rls="$(psql_main "SELECT (relrowsecurity AND relforcerowsecurity)::text FROM pg_class WHERE oid='public.decision_orchestration_executions'::regclass;")"
[ "$rls" = "true" ] \
  && emit "decision_orchestration_executions FORCE RLS" "PASS" "relrowsecurity=true relforcerowsecurity=true" \
  || emit "decision_orchestration_executions FORCE RLS" "FAIL" "$rls" "Enable and FORCE RLS on decision_orchestration_executions."

true_policies="$(psql_main "SELECT COUNT(*) FROM pg_policies WHERE schemaname='public' AND tablename='decision_orchestration_executions' AND (qual='true' OR with_check='true');")"
[ "$true_policies" = "0" ] \
  && emit "decision_orchestration_executions no permissive policy" "PASS" "USING/WITH CHECK true policies=0" \
  || emit "decision_orchestration_executions no permissive policy" "FAIL" "true_policies=${true_policies:-unknown}" "Replace permissive policies with GUC-scoped policies."

bypass="$(psql_main "SELECT rolbypassrls::text FROM pg_roles WHERE rolname='omega_console';")"
[ "$bypass" = "false" ] \
  && emit "omega_console NOBYPASSRLS" "PASS" "rolbypassrls=false" \
  || emit "omega_console NOBYPASSRLS" "FAIL" "rolbypassrls=${bypass:-unknown}" "ALTER ROLE omega_console NOBYPASSRLS."
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_DECISION_ORCHESTRATOR_EXECUTION_CHECK\t"):
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
        "# AWS Decision Orchestrator Execution Probe",
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
        comment="omega-decision-orchestrator-execution-aws-probe",
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
                evidence="No OMEGA_DECISION_ORCHESTRATOR_EXECUTION_CHECK lines found.",
                unblock="Inspect remote stdout/stderr evidence.",
            )
        ]
    summary = {
        "status": _overall_status(checks),
        "generated_at_utc": utc_now().isoformat(),
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
