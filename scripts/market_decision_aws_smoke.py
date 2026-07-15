#!/usr/bin/env python3
"""Run the governed market-decision validation inside AWS Console over SSM."""

from __future__ import annotations

import argparse
import json
import shlex
from typing import Any
from uuid import UUID

if __package__:
    from scripts.aws_ssm import DEFAULT_REGION, resolve_instance_id, send_ssm_script
else:
    from aws_ssm import DEFAULT_REGION, resolve_instance_id, send_ssm_script


SUMMARY_PREFIX = "OMEGA_MARKET_SMOKE\t"


def _scope_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("scope must be a UUID") from exc


def _remote_script(*, tenant_id: str, workspace_id: str) -> str:
    tenant = shlex.quote(tenant_id)
    workspace = shlex.quote(workspace_id)
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x

docker exec -i \
  -e OMEGA_SMOKE_TENANT_ID={tenant} \
  -e OMEGA_SMOKE_WORKSPACE_ID={workspace} \
  mode_console python - <<'PY'
import asyncio
import hashlib
import json
import os

from app.services.intelligence import market_decision_validation as validation


def state_hash(states):
    payload = json.dumps(states, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def main():
    user = {{
        "id": None,
        "active_tenant_id": os.environ["OMEGA_SMOKE_TENANT_ID"],
        "active_workspace_id": os.environ["OMEGA_SMOKE_WORKSPACE_ID"],
        "allowed_cartridges": ["sap_successfactors", "banxico"],
    }}
    before = await validation._bayes_states(user)
    result = await validation.run_validation(user)
    persisted = await validation.get_validation(user)
    after = await validation._bayes_states(user)
    policy = result.get("policy") or {{}}
    orchestration = result.get("orchestration") or {{}}
    market = result.get("market_context") or {{}}
    simulation = result.get("simulation") or {{}}
    summary = {{
        "status": result.get("status"),
        "persisted_status": persisted.get("status"),
        "source_mode": (result.get("source") or {{}}).get("source_mode"),
        "market_provider": market.get("provider"),
        "market_metric": market.get("metric_name"),
        "market_freshness": market.get("freshness_status"),
        "market_evidence_count": simulation.get("market_evidence_count"),
        "simulation_id": simulation.get("simulation_id"),
        "orchestration_id": orchestration.get("orchestration_id"),
        "action_recommended": orchestration.get("action_recommended"),
        "external_action_id": orchestration.get("external_action_id"),
        "automatic_action": policy.get("automatic_action"),
        "external_writeback": policy.get("external_writeback"),
        "creates_calibration_observation": policy.get(
            "creates_calibration_observation"
        ),
        "bayes_state_count_before": len(before),
        "bayes_state_count_after": len(after),
        "bayes_unchanged": state_hash(before) == state_hash(after),
    }}
    print("OMEGA_MARKET_SMOKE\t" + json.dumps(summary, sort_keys=True))


asyncio.run(main())
PY
"""


def _parse_summary(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith(SUMMARY_PREFIX):
            payload = json.loads(line.removeprefix(SUMMARY_PREFIX))
            if isinstance(payload, dict):
                return payload
    raise RuntimeError("AWS smoke did not return a structured summary")


def _validate_summary(summary: dict[str, Any]) -> None:
    failures: list[str] = []
    if summary.get("status") not in {"ready", "partial"}:
        failures.append("validation did not produce a governed result")
    if summary.get("persisted_status") not in {"ready", "partial"}:
        failures.append("validation result was not readable after persistence")
    if summary.get("market_provider") != "banxico":
        failures.append("Banxico context was not used")
    if summary.get("market_metric") != "usd_mxn_fix":
        failures.append("usd_mxn_fix context was not used")
    if summary.get("market_freshness") != "ready":
        failures.append("market context is not fresh")
    if int(summary.get("market_evidence_count") or 0) < 1:
        failures.append("market evidence was not attached")
    if not summary.get("simulation_id") or not summary.get("orchestration_id"):
        failures.append("simulation or orchestration was not persisted")
    for key in (
        "action_recommended",
        "automatic_action",
        "external_writeback",
        "creates_calibration_observation",
    ):
        if summary.get(key) is not False:
            failures.append(f"{key} must remain false")
    if summary.get("external_action_id") is not None:
        failures.append("an external action was created")
    if summary.get("bayes_unchanged") is not True:
        failures.append("Bayes state changed during read-only evidence validation")
    if failures:
        raise RuntimeError("; ".join(failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=_scope_id)
    parser.add_argument("--workspace-id", required=True, type=_scope_id)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default="")
    args = parser.parse_args(argv)

    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    result = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(
            tenant_id=args.tenant_id,
            workspace_id=args.workspace_id,
        ),
        comment="omega-market-decision-smoke",
        timeout_seconds=300,
    )
    if result.status != "Success" or result.response_code != 0:
        detail = result.stderr or result.stdout or result.status
        raise RuntimeError(f"AWS smoke command failed: {detail[-1200:]}")
    summary = _parse_summary(result.stdout)
    _validate_summary(summary)
    print(
        json.dumps(
            {
                "status": "PASS",
                "command_id": result.command_id,
                "instance_id": instance_id,
                "result": summary,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
