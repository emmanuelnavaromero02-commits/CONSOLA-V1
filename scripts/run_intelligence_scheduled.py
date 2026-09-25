#!/usr/bin/env python3

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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "intelligence-scheduled"
MARKER = "OMEGA_INTELLIGENCE_SCHEDULED_JSON="


CONTAINER_PYTHON = r"""
import asyncio
import json
import os
import sys

from app.services.intelligence_engine import run_intelligence


def _as_int(value):
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def main():
    if _truthy(os.environ.get("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK")):
        raise SystemExit("REFUSE: external write-back enabled")
    tenant_id = os.environ.get("OMEGA_INTELLIGENCE_TENANT_ID") or os.environ.get("OMEGA_SEED_TENANT_ID")
    workspace_id = os.environ.get("OMEGA_INTELLIGENCE_WORKSPACE_ID") or os.environ.get("OMEGA_SEED_WORKSPACE_ID")
    if not workspace_id:
        raise SystemExit("OMEGA_INTELLIGENCE_WORKSPACE_ID is required")
    source_system = (os.environ.get("OMEGA_INTELLIGENCE_SOURCE_SYSTEM") or "replicon").strip().lower()
    if not source_system:
        raise SystemExit("OMEGA_INTELLIGENCE_SOURCE_SYSTEM is required")
    dry_run = _truthy(os.environ.get("OMEGA_INTELLIGENCE_DRY_RUN"))
    actor_email = os.environ.get("OMEGA_INTELLIGENCE_ACTOR_EMAIL") or "omega-scheduler@example.invalid"
    user = {
        "id": _as_int(os.environ.get("OMEGA_INTELLIGENCE_ACTOR_ID")) or 0,
        "email": actor_email,
        "role": os.environ.get("OMEGA_INTELLIGENCE_ACTOR_ROLE") or "admin",
        "workspace_role": os.environ.get("OMEGA_INTELLIGENCE_WORKSPACE_ROLE") or "workspace_admin",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "allowed_cartridges": [source_system],
    }
    body = {
        "cartridge_id": source_system,
        "include_external": False,
        "dry_run": dry_run,
        "run_mode": "scheduled",
        "horizon_days": [7, 21],
    }
    result = await run_intelligence(user, body, persist=True)
    signals = result.get("signals") or []
    sample = []
    for signal in signals[:10]:
        decision = signal.get("decision_intelligence") if isinstance(signal, dict) else {}
        if not isinstance(decision, dict):
            decision = {}
        sample.append({
            "signal_id": signal.get("signal_id"),
            "dataset": signal.get("dataset"),
            "metric": signal.get("metric"),
            "severity": signal.get("severity"),
            "recommended_decision": decision.get("recommended_decision"),
        })
    summary = {
        "ok": True,
        "target": os.environ.get("OMEGA_INTELLIGENCE_TARGET") or "local",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "source_system": source_system,
        "run_mode": result.get("run_mode"),
        "dry_run": bool(result.get("dry_run")),
        "intelligence_run_id": result.get("intelligence_run_id"),
        "run_ref": result.get("run_ref"),
        "signals": len(signals),
        "skipped": len(result.get("skipped") or []),
        "dataset_unavailable_count": result.get("dataset_unavailable_count"),
        "insufficient_history_count": result.get("insufficient_history_count"),
        "skipped_counts": result.get("skipped_counts") or {},
        "sample_signals": sample,
        "external_writeback": False,
    }
    print("OMEGA_INTELLIGENCE_SCHEDULED_JSON=" + json.dumps(summary, sort_keys=True))


asyncio.run(main())
"""


def _container_env(args: argparse.Namespace, target: str) -> dict[str, str]:
    env = {
        "OMEGA_INTELLIGENCE_TARGET": target,
        "OMEGA_INTELLIGENCE_SOURCE_SYSTEM": args.source_system,
        "OMEGA_INTELLIGENCE_DRY_RUN": "1" if args.dry_run else "0",
    }
    for name, value in {
        "OMEGA_INTELLIGENCE_TENANT_ID": args.tenant_id,
        "OMEGA_INTELLIGENCE_WORKSPACE_ID": args.workspace_id,
        "OMEGA_INTELLIGENCE_ACTOR_ID": args.actor_id,
        "OMEGA_INTELLIGENCE_ACTOR_EMAIL": args.actor_email,
        "OMEGA_INTELLIGENCE_ACTOR_ROLE": args.actor_role,
        "OMEGA_INTELLIGENCE_WORKSPACE_ROLE": args.workspace_role,
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
    raise RuntimeError("scheduled intelligence summary marker not found")


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
        "dry_run": args.dry_run,
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
    env = _container_env(args, "local")
    command = _docker_exec_command(env)
    result = subprocess.run(
        command,
        input=CONTAINER_PYTHON,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=args.timeout,
    )
    summary: dict[str, Any] | None = None
    if result.returncode == 0:
        summary = _parse_summary(result.stdout)
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
  -e OMEGA_INTELLIGENCE_TARGET \\
  -e OMEGA_INTELLIGENCE_SOURCE_SYSTEM \\
  -e OMEGA_INTELLIGENCE_DRY_RUN \\
  -e OMEGA_INTELLIGENCE_TENANT_ID \\
  -e OMEGA_INTELLIGENCE_WORKSPACE_ID \\
  -e OMEGA_INTELLIGENCE_ACTOR_ID \\
  -e OMEGA_INTELLIGENCE_ACTOR_EMAIL \\
  -e OMEGA_INTELLIGENCE_ACTOR_ROLE \\
  -e OMEGA_INTELLIGENCE_WORKSPACE_ROLE \\
  mode_console python - <<'PY'
{CONTAINER_PYTHON}
PY
"""


def run_aws(args: argparse.Namespace, evidence_dir: Path) -> int:
    region = args.region
    instance_id = resolve_instance_id(region, args.instance_id)
    env = _container_env(args, "aws")
    remote = send_ssm_script(
        region=region,
        instance_id=instance_id,
        script=_remote_script(env),
        comment="omega-intelligence-scheduled",
        timeout_seconds=args.timeout,
    )
    summary: dict[str, Any] | None = None
    if remote.status == "Success" and remote.response_code == 0:
        summary = _parse_summary(remote.stdout)
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
    parser = argparse.ArgumentParser(
        description="Run controlled scheduled Intelligence"
    )
    parser.add_argument(
        "--target",
        choices={"local", "aws"},
        default=os.environ.get("OMEGA_INTELLIGENCE_TARGET") or "local",
    )
    parser.add_argument(
        "--source-system",
        default=os.environ.get("OMEGA_INTELLIGENCE_SOURCE_SYSTEM") or "replicon",
    )
    parser.add_argument(
        "--tenant-id",
        default=os.environ.get("OMEGA_INTELLIGENCE_TENANT_ID")
        or os.environ.get("OMEGA_SEED_TENANT_ID")
        or "",
    )
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("OMEGA_INTELLIGENCE_WORKSPACE_ID")
        or os.environ.get("OMEGA_SEED_WORKSPACE_ID")
        or "",
    )
    parser.add_argument(
        "--actor-id", default=os.environ.get("OMEGA_INTELLIGENCE_ACTOR_ID") or ""
    )
    parser.add_argument(
        "--actor-email",
        default=os.environ.get("OMEGA_INTELLIGENCE_ACTOR_EMAIL")
        or "omega-scheduler@example.invalid",
    )
    parser.add_argument(
        "--actor-role",
        default=os.environ.get("OMEGA_INTELLIGENCE_ACTOR_ROLE") or "admin",
    )
    parser.add_argument(
        "--workspace-role",
        default=os.environ.get("OMEGA_INTELLIGENCE_WORKSPACE_ROLE")
        or "workspace_admin",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("OMEGA_INTELLIGENCE_DRY_RUN", "").lower()
        in {"1", "true", "yes", "on"},
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
        default=int(os.environ.get("OMEGA_INTELLIGENCE_TIMEOUT_SECONDS", "600")),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.workspace_id:
        raise SystemExit(
            "OMEGA_INTELLIGENCE_WORKSPACE_ID or --workspace-id is required"
        )
    evidence_dir = args.evidence_root / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if args.target == "aws":
        return run_aws(args, evidence_dir)
    return run_local(args, evidence_dir)


if __name__ == "__main__":
    raise SystemExit(main())
