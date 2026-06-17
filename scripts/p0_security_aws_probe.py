#!/usr/bin/env python3
"""Probe P0 security hotfix controls on AWS via SSM."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_now, utc_stamp, write_json


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "p0-security-aws-probe"


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
    return r"""#!/usr/bin/env bash
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
  printf 'P0_SECURITY_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
}

cd "$DEPLOY_DIR"

console_probe="$(docker compose $(compose_files) exec -T console python - <<'PY' 2>&1 || true
import asyncio
import inspect
import json
import os
import urllib.error
import urllib.request


def emit(name, status, evidence, unblock=""):
    print("P0_SECURITY_CHECK\t{}\t{}\t{}\t{}".format(name, status, evidence, unblock))


async def assistant_probe():
    from app.services import assistant_tool_gate, mcp_registry

    async def should_not_invoke(*_args, **_kwargs):
        raise RuntimeError("mcp invoked")

    mcp_registry.invoke = should_not_invoke
    user = {
        "id": "probe",
        "role": "viewer",
        "active_tenant_id": "tenant-probe",
        "active_workspace_id": "workspace-probe",
    }
    catalog = {
        "mcp-infra__postgres_execute_query": {
            "server_id": "mcp-infra",
            "tool": "postgres_execute_query",
            "input_schema": {"type": "object", "properties": {}},
        }
    }
    result = await assistant_tool_gate.invoke(
        "mcp-infra",
        "postgres_execute_query",
        {"sql": "DROP TABLE users"},
        user,
        catalog,
    )
    if isinstance(result, dict) and result.get("error") == assistant_tool_gate.DENIED_ERROR:
        emit("assistant destructive tool denied", "PASS", result.get("message", "denied"))
    else:
        emit("assistant destructive tool denied", "FAIL", json.dumps(result, default=str)[:300])


def ssrf_probe():
    from app.services import egress_guard

    try:
        egress_guard.validate_url("https://169.254.169.254/latest/meta-data", label="probe URL")
    except egress_guard.EgressGuardError as exc:
        emit("SSRF IMDS blocked", "PASS", str(exc))
        return
    emit("SSRF IMDS blocked", "FAIL", "IMDS URL was accepted")


def scheduled_probe():
    import app.main as main

    source = inspect.getsource(main.api_agents_invoke_scheduled)
    if "user_context=user" in source:
        emit("scheduled agent no NameError", "FAIL", "scheduled endpoint still references user_context=user")
        return
    if "scheduled agent requires tenant/workspace scope" not in source:
        emit("scheduled agent no NameError", "FAIL", "scheduled endpoint does not fail closed on missing scope")
        return
    emit("scheduled agent no NameError", "PASS", "scheduled endpoint loads agent without undefined user and checks scope")


def forged_cartridge_probe():
    api_key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE") or os.environ.get("INTERNAL_API_KEY") or ""
    if not api_key:
        emit("forged cartridge context rejected", "BLOCKED", "missing cartridge internal API key")
        return
    candidates = [
        os.environ.get("SAP_SUCCESSFACTORS_URL") or "http://sap-successfactors:8203",
        os.environ.get("SAP_HCM_URL") or "http://sap-hcm:8202",
        os.environ.get("SAP_S4HANA_URL") or "http://sap-s4hana:8204",
        os.environ.get("SALESFORCE_URL") or "http://salesforce:8205",
        os.environ.get("HUBSPOT_URL") or "http://hubspot:8210",
        os.environ.get("REPLICON_URL") or "http://replicon:8201",
    ]
    payload = json.dumps({
        "tool": "list_entities",
        "args": {},
        "security_context": {
            "trusted": True,
            "tenant_id": "tenant-probe",
            "workspace_id": "workspace-probe",
        },
    }).encode("utf-8")
    attempts = []
    for cartridge_url in candidates:
        cartridge_url = (cartridge_url or "").rstrip("/")
        if not cartridge_url:
            continue
        req = urllib.request.Request(
            cartridge_url + "/mcp/invoke",
            data=payload,
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "x-internal-service": "console",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read().decode("utf-8", errors="replace")
                emit("forged cartridge context rejected", "FAIL", f"url={cartridge_url} status={response.status} body={body[:300]}")
                return
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403:
                emit("forged cartridge context rejected", "PASS", f"url={cartridge_url} status=403 body={body[:300]}")
                return
            emit("forged cartridge context rejected", "FAIL", f"url={cartridge_url} status={exc.code} body={body[:300]}")
            return
        except Exception as exc:
            attempts.append(f"{cartridge_url}:{type(exc).__name__}")
    emit("forged cartridge context rejected", "BLOCKED", "; ".join(attempts) or "no cartridge URLs attempted")


asyncio.run(assistant_probe())
ssrf_probe()
scheduled_probe()
forged_cartridge_probe()
PY
)"

printf '%s\n' "$console_probe"
"""


def _parse_checks(output: str) -> list[Check]:
    checks: list[Check] = []
    for line in output.splitlines():
        if not line.startswith("P0_SECURITY_CHECK\t"):
            continue
        parts = line.split("\t", 4)
        if len(parts) < 4:
            continue
        _tag, name, status, evidence, *rest = parts
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
        comment="omega-p0-security-aws-probe",
        timeout_seconds=600,
    )
    output = command.stdout + "\n" + command.stderr
    checks = _parse_checks(output)
    if not checks:
        checks = [Check("p0 probe harness", BLOCKED, _short(output), "Check SSM/docker compose access.")]

    status = PASS if checks and all(check.status == PASS for check in checks) else FAIL
    report = {
        "probe": "p0-security-aws-probe",
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
        write_json(args.evidence_root / f"p0-security-aws-probe-{utc_stamp()}.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
