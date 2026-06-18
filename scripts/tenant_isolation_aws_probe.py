#!/usr/bin/env python3
"""Probe Prompt 20B tenant isolation controls on AWS via SSM.

The default checks are read-only: RLS/FORCE/no permissive policy metadata for
the 20B tables. Endpoint probes run when TENANT_A_COOKIE/TENANT_B_COOKIE are
present in the remote deploy environment.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_stamp, write_json


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "tenant-isolation-aws-probe"


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
  printf 'TENANT_ISOLATION_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
}

cd "$DEPLOY_DIR"

console_probe="$(docker compose $(compose_files) exec -T console python - <<'PY' 2>&1 || true
import asyncio
import json
import os
import urllib.error
import urllib.request


TABLES = (
    "copilot_drafts",
    "workflow_runs",
    "workflow_steps",
    "user_facts",
    "user_preferences",
    "conversation_memory_summary",
    "analytic_apps",
    "data_catalog",
    "data_relationships",
)


def emit(name, status, evidence, unblock=""):
    print("TENANT_ISOLATION_CHECK\t{}\t{}\t{}\t{}".format(name, status, evidence, unblock))


async def db_contract():
    from app.services import auth

    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT c.relname,
               c.relrowsecurity,
               c.relforcerowsecurity,
               COALESCE(bool_or(
                   lower(regexp_replace(COALESCE(pg_get_expr(p.polqual, p.polrelid), ''), '\s+', '', 'g'))
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
    by_name = {r["relname"]: dict(r) for r in rows}
    missing = [t for t in TABLES if t not in by_name]
    bad = [
        t for t, r in by_name.items()
        if not r["relrowsecurity"] or not r["relforcerowsecurity"] or r["permissive_true"] or int(r["policies"] or 0) == 0
    ]
    if missing or bad:
        emit("RLS/FORCE/no permissive policy", "FAIL", json.dumps({"missing": missing, "bad": bad}, default=str))
    else:
        emit("RLS/FORCE/no permissive policy", "PASS", json.dumps(sorted(by_name)))

    defaults = await pool.fetch(
        """
        SELECT defaclacl::text AS acl
          FROM pg_default_acl
         WHERE defaclnamespace = 'public'::regnamespace
           AND defaclacl::text LIKE '%omega_console%'
        """
    )
    wide = [r["acl"] for r in defaults if any(token in str(r["acl"]) for token in ("arwd", "=r", "=a", "=w", "=d"))]
    if wide:
        emit("default privileges no broad console DML", "FAIL", json.dumps(wide))
    else:
        emit("default privileges no broad console DML", "PASS", "omega_console default table DML not broad")


def http_json(path, cookie):
    req = urllib.request.Request(
        "http://console:8000" + path,
        headers={"Cookie": cookie, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def optional_endpoint_contract():
    cookie_a = os.environ.get("TENANT_A_COOKIE", "").strip()
    cookie_b = os.environ.get("TENANT_B_COOKIE", "").strip()
    if not (cookie_a and cookie_b):
        emit(
            "endpoint tenant A/B probes",
            "BLOCKED",
            "TENANT_A_COOKIE/TENANT_B_COOKIE not configured on remote host",
            "set tenant probe cookies and rerun tenant-isolation-aws-probe",
        )
        return
    try:
        dashboard_a = http_json("/api/dashboard/kpis", cookie_a)
        sessions_a = http_json("/security/sessions", cookie_a)
        attempts_a = http_json("/security/login-attempts", cookie_a)
        catalog_a = http_json("/api/catalog", cookie_a)
        dashboard_b = http_json("/api/dashboard/kpis", cookie_b)
    except Exception as exc:
        emit("endpoint tenant A/B probes", "FAIL", str(exc))
        return
    leaked = []
    if dashboard_a == dashboard_b and dashboard_a.get("users", {}).get("total"):
        leaked.append("dashboard identical non-empty tenant payload")
    for label, payload in (("sessions", sessions_a), ("login_attempts", attempts_a)):
        if isinstance(payload, list) and any("@" in str(item) and "tenant-b" in str(item).lower() for item in payload):
            leaked.append(label)
    if isinstance(catalog_a, dict) and "legacy_unscoped" in json.dumps(catalog_a).lower():
        leaked.append("catalog legacy_unscoped visible")
    if leaked:
        emit("endpoint tenant A/B probes", "FAIL", json.dumps(leaked))
    else:
        emit("endpoint tenant A/B probes", "PASS", "dashboard/security/catalog tenant probes returned scoped payloads")


asyncio.run(db_contract())
optional_endpoint_contract()
PY
)"

printf '%s\n' "$console_probe"
'''


def _parse_checks(output: str) -> list[Check]:
    checks: list[Check] = []
    for line in output.splitlines():
        if not line.startswith("TENANT_ISOLATION_CHECK\t"):
            continue
        _prefix, name, status, evidence, *rest = line.split("\t")
        checks.append(Check(name=name, status=status, evidence=_short(evidence), unblock=_short(rest[0]) if rest else ""))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", default="", help="EC2 instance id. Defaults to AWS_INSTANCE_ID or tag lookup.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    args = parser.parse_args()

    instance_id = resolve_instance_id(args.instance_id, args.region)
    output = send_ssm_script(instance_id, _remote_script(), region=args.region, timeout_seconds=420)
    checks = _parse_checks(output)
    summary = {
        "probe": "tenant-isolation-aws-probe",
        "instance_id": instance_id,
        "checks": [asdict(c) for c in checks],
        "raw_output": _short(output, limit=4000),
    }
    out = args.evidence_root / f"{utc_stamp()}-tenant-isolation-aws-probe.json"
    write_json(out, summary)
    print(json.dumps(summary, indent=2))
    if not checks:
        return 2
    return 0 if all(c.status == PASS for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
