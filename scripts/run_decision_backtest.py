#!/usr/bin/env python3
"""Run Decision Intelligence backtests locally or through AWS SSM.

This is an operational gate: it executes inside the Console container, avoids
external write-back, and persists redacted evidence under docs/release-evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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


DEFAULT_EVIDENCE_ROOT = (
    REPO / "docs" / "release-evidence" / "decision-backtesting-calibration"
)
MARKER = "OMEGA_DECISION_BACKTEST_JSON="


CONTAINER_PYTHON = r"""
import asyncio
import json
import os

from app.services.intelligence.backtesting import run_backtest


def _as_int(value):
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def main():
    if _truthy(os.environ.get("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK")):
        raise SystemExit("REFUSE: external write-back enabled")
    tenant_id = os.environ.get("OMEGA_BACKTEST_TENANT_ID") or os.environ.get("OMEGA_SEED_TENANT_ID")
    workspace_id = os.environ.get("OMEGA_BACKTEST_WORKSPACE_ID") or os.environ.get("OMEGA_SEED_WORKSPACE_ID")
    if not workspace_id:
        raise SystemExit("OMEGA_BACKTEST_WORKSPACE_ID is required")
    source_system = (os.environ.get("OMEGA_BACKTEST_SOURCE_SYSTEM") or "replicon").strip().lower()
    metric = (os.environ.get("OMEGA_BACKTEST_METRIC") or "billable_hours").strip()
    mode = (os.environ.get("OMEGA_BACKTEST_MODE") or "historical_replay").strip()
    actor_email = os.environ.get("OMEGA_BACKTEST_ACTOR_EMAIL") or "omega-backtest@example.invalid"
    user = {
        "id": _as_int(os.environ.get("OMEGA_BACKTEST_ACTOR_ID")) or 0,
        "email": actor_email,
        "role": os.environ.get("OMEGA_BACKTEST_ACTOR_ROLE") or "admin",
        "workspace_role": os.environ.get("OMEGA_BACKTEST_WORKSPACE_ROLE") or "workspace_admin",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "allowed_cartridges": [source_system],
    }
    body = {
        "source_system": source_system,
        "metric": metric,
        "mode": mode,
        "labels_required": _as_int(os.environ.get("OMEGA_BACKTEST_LABELS_REQUIRED")) or 10,
        "limit": _as_int(os.environ.get("OMEGA_BACKTEST_LIMIT")) or 5000,
        "result_limit": _as_int(os.environ.get("OMEGA_BACKTEST_RESULT_LIMIT")) or 20,
    }
    for env_name, key in {
        "OMEGA_BACKTEST_SOURCE_DATASET": "source_dataset",
        "OMEGA_BACKTEST_START_PERIOD": "start_period",
        "OMEGA_BACKTEST_END_PERIOD": "end_period",
    }.items():
        value = os.environ.get(env_name)
        if value:
            body[key] = value
    result = await run_backtest(user, body)
    run = result.get("run") or {}
    summary = result.get("summary") or {}
    payload = {
        "ok": summary.get("status") in {"ok", "insufficient_labeled_data"},
        "target": os.environ.get("OMEGA_BACKTEST_TARGET") or "local",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "source_system": source_system,
        "metric": metric,
        "mode": mode,
        "run_id": run.get("id"),
        "run_ref": run.get("run_ref"),
        "status": summary.get("status"),
        "total_results": summary.get("total_results"),
        "total_labeled": summary.get("total_labeled"),
        "brier_score": summary.get("brier_score"),
        "precision": summary.get("precision"),
        "recall": summary.get("recall"),
        "external_writeback": False,
    }
    print("OMEGA_DECISION_BACKTEST_JSON=" + json.dumps(payload, sort_keys=True))


asyncio.run(main())
"""


def _container_env(args: argparse.Namespace, target: str) -> dict[str, str]:
    env = {
        "OMEGA_BACKTEST_TARGET": target,
        "OMEGA_BACKTEST_SOURCE_SYSTEM": args.source_system,
        "OMEGA_BACKTEST_METRIC": args.metric,
        "OMEGA_BACKTEST_MODE": args.mode,
        "OMEGA_BACKTEST_LABELS_REQUIRED": str(args.labels_required),
        "OMEGA_BACKTEST_LIMIT": str(args.limit),
        "OMEGA_BACKTEST_RESULT_LIMIT": str(args.result_limit),
    }
    for name, value in {
        "OMEGA_BACKTEST_TENANT_ID": args.tenant_id,
        "OMEGA_BACKTEST_WORKSPACE_ID": args.workspace_id,
        "OMEGA_BACKTEST_SOURCE_DATASET": args.source_dataset,
        "OMEGA_BACKTEST_START_PERIOD": args.start_period,
        "OMEGA_BACKTEST_END_PERIOD": args.end_period,
        "OMEGA_BACKTEST_ACTOR_ID": args.actor_id,
        "OMEGA_BACKTEST_ACTOR_EMAIL": args.actor_email,
        "OMEGA_BACKTEST_ACTOR_ROLE": args.actor_role,
        "OMEGA_BACKTEST_WORKSPACE_ROLE": args.workspace_role,
    }.items():
        if value:
            env[name] = value
    return env


def _docker_exec_command(container_env: dict[str, str]) -> list[str]:
    command = ["docker", "exec", "-i"]
    for key, value in sorted(container_env.items()):
        command.extend(["-e", f"{key}={value}"])
    command.extend(["mode_console", "python", "-"])
    return command


def _parse_summary(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    raise RuntimeError("decision backtest summary marker not found")


def _write_evidence(
    evidence_dir: Path,
    *,
    args: argparse.Namespace,
    target: str,
    summary: dict[str, Any] | None,
    stdout: str,
    stderr: str,
    returncode: int | None,
    remote: Any | None = None,
) -> None:
    payload = {
        "generated_at_utc": utc_now().isoformat(),
        "target": target,
        "source_system": args.source_system,
        "metric": args.metric,
        "mode": args.mode,
        "tenant_id": args.tenant_id,
        "workspace_id": args.workspace_id,
        "summary": summary or {},
        "returncode": returncode,
    }
    if remote is not None:
        payload.update(
            {
                "ssm_command_id": remote.command_id,
                "instance_id": remote.instance_id,
                "region": remote.region,
                "ssm_status": remote.status,
                "ssm_response_code": remote.response_code,
            }
        )
    write_json(evidence_dir / "summary.json", payload)
    (evidence_dir / "stdout_redacted.txt").write_text(redact(stdout), encoding="utf-8")
    (evidence_dir / "stderr_redacted.txt").write_text(redact(stderr), encoding="utf-8")


def run_local(args: argparse.Namespace, evidence_dir: Path) -> int:
    result = subprocess.run(
        _docker_exec_command(_container_env(args, "local")),
        input=CONTAINER_PYTHON,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=args.timeout,
    )
    summary = _parse_summary(result.stdout) if result.returncode == 0 else None
    _write_evidence(
        evidence_dir,
        args=args,
        target="local",
        summary=summary,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )
    print(
        json.dumps(
            summary or {"ok": False, "evidence_dir": str(evidence_dir)}, sort_keys=True
        )
    )
    return result.returncode


def _remote_script(container_env: dict[str, str]) -> str:
    env_exports = "\n".join(
        f"export {key}={json.dumps(value)}"
        for key, value in sorted(container_env.items())
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x
cd /opt/modecissions
{env_exports}
if docker exec mode_console sh -lc 'case "${{CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK:-false}}" in 1|true|yes|on) exit 42;; *) exit 0;; esac'; then
  :
else
  echo "REFUSE: external write-back enabled" >&2
  exit 42
fi
docker exec -i \\
  -e OMEGA_BACKTEST_TARGET \\
  -e OMEGA_BACKTEST_SOURCE_SYSTEM \\
  -e OMEGA_BACKTEST_METRIC \\
  -e OMEGA_BACKTEST_MODE \\
  -e OMEGA_BACKTEST_LABELS_REQUIRED \\
  -e OMEGA_BACKTEST_LIMIT \\
  -e OMEGA_BACKTEST_RESULT_LIMIT \\
  -e OMEGA_BACKTEST_TENANT_ID \\
  -e OMEGA_BACKTEST_WORKSPACE_ID \\
  -e OMEGA_BACKTEST_SOURCE_DATASET \\
  -e OMEGA_BACKTEST_START_PERIOD \\
  -e OMEGA_BACKTEST_END_PERIOD \\
  -e OMEGA_BACKTEST_ACTOR_ID \\
  -e OMEGA_BACKTEST_ACTOR_EMAIL \\
  -e OMEGA_BACKTEST_ACTOR_ROLE \\
  -e OMEGA_BACKTEST_WORKSPACE_ROLE \\
  mode_console python - <<'PY'
{CONTAINER_PYTHON}
PY
"""


def run_aws(args: argparse.Namespace, evidence_dir: Path) -> int:
    region = args.region
    instance_id = resolve_instance_id(region, args.instance_id)
    remote = send_ssm_script(
        region=region,
        instance_id=instance_id,
        script=_remote_script(_container_env(args, "aws")),
        comment="omega-decision-backtest",
        timeout_seconds=args.timeout,
    )
    summary = (
        _parse_summary(remote.stdout)
        if remote.status == "Success" and remote.response_code == 0
        else None
    )
    _write_evidence(
        evidence_dir,
        args=args,
        target="aws",
        summary=summary,
        stdout=remote.stdout,
        stderr=remote.stderr,
        returncode=remote.response_code,
        remote=remote,
    )
    print(
        json.dumps(
            {
                "ok": remote.status == "Success" and remote.response_code == 0,
                "ssm_command_id": remote.command_id,
                "instance_id": remote.instance_id,
                "region": remote.region,
                "evidence_dir": str(evidence_dir),
                "summary": summary or {},
            },
            sort_keys=True,
        )
    )
    return 0 if remote.status == "Success" and remote.response_code == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Decision Intelligence backtest")
    parser.add_argument(
        "--target",
        choices={"local", "aws"},
        default=os.environ.get("OMEGA_BACKTEST_TARGET") or "local",
    )
    parser.add_argument(
        "--source-system",
        default=os.environ.get("OMEGA_BACKTEST_SOURCE_SYSTEM") or "replicon",
    )
    parser.add_argument(
        "--source-dataset",
        default=os.environ.get("OMEGA_BACKTEST_SOURCE_DATASET") or "",
    )
    parser.add_argument(
        "--metric", default=os.environ.get("OMEGA_BACKTEST_METRIC") or "billable_hours"
    )
    parser.add_argument(
        "--mode",
        choices={"historical_replay", "outcome_linked", "fixture_validation"},
        default=os.environ.get("OMEGA_BACKTEST_MODE") or "historical_replay",
    )
    parser.add_argument(
        "--start-period", default=os.environ.get("OMEGA_BACKTEST_START_PERIOD") or ""
    )
    parser.add_argument(
        "--end-period", default=os.environ.get("OMEGA_BACKTEST_END_PERIOD") or ""
    )
    parser.add_argument(
        "--labels-required",
        type=int,
        default=int(os.environ.get("OMEGA_BACKTEST_LABELS_REQUIRED", "10")),
    )
    parser.add_argument(
        "--limit", type=int, default=int(os.environ.get("OMEGA_BACKTEST_LIMIT", "5000"))
    )
    parser.add_argument(
        "--result-limit",
        type=int,
        default=int(os.environ.get("OMEGA_BACKTEST_RESULT_LIMIT", "20")),
    )
    parser.add_argument(
        "--tenant-id",
        default=os.environ.get("OMEGA_BACKTEST_TENANT_ID")
        or os.environ.get("OMEGA_SEED_TENANT_ID")
        or "",
    )
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("OMEGA_BACKTEST_WORKSPACE_ID")
        or os.environ.get("OMEGA_SEED_WORKSPACE_ID")
        or "",
    )
    parser.add_argument(
        "--actor-id", default=os.environ.get("OMEGA_BACKTEST_ACTOR_ID") or ""
    )
    parser.add_argument(
        "--actor-email",
        default=os.environ.get("OMEGA_BACKTEST_ACTOR_EMAIL")
        or "omega-backtest@example.invalid",
    )
    parser.add_argument(
        "--actor-role", default=os.environ.get("OMEGA_BACKTEST_ACTOR_ROLE") or "admin"
    )
    parser.add_argument(
        "--workspace-role",
        default=os.environ.get("OMEGA_BACKTEST_WORKSPACE_ROLE") or "workspace_admin",
    )
    parser.add_argument(
        "--region", default=os.environ.get("AWS_REGION") or DEFAULT_REGION
    )
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("OMEGA_BACKTEST_TIMEOUT_SECONDS", "600")),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.workspace_id:
        raise SystemExit("OMEGA_BACKTEST_WORKSPACE_ID or --workspace-id is required")
    evidence_dir = args.evidence_root / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return (
        run_aws(args, evidence_dir)
        if args.target == "aws"
        else run_local(args, evidence_dir)
    )


if __name__ == "__main__":
    raise SystemExit(main())
