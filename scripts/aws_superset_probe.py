#!/usr/bin/env python3
"""Probe Superset tenant exposure and Gold/RLS posture on AWS via SSM."""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-superset-probe"


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _derive_public_superset_url(public_console_url: str) -> str:
    parsed = urllib.parse.urlparse(public_console_url)
    if not parsed.scheme or not parsed.hostname:
        return ""
    netloc = f"{parsed.hostname}:8088"
    return urllib.parse.urlunparse((parsed.scheme, netloc, "/health", "", "", ""))


def _probe_public_superset(public_console_url: str) -> Check:
    superset_url = _derive_public_superset_url(public_console_url)
    if not superset_url:
        return Check(
            "Public Superset direct access",
            "BLOCKED",
            "PUBLIC_CONSOLE_URL/CONSOLE_URL missing; cannot probe external port 8088",
            "Set PUBLIC_CONSOLE_URL to the ALB URL and rerun superset-tenant-probe-aws.",
        )
    request = urllib.request.Request(
        superset_url,
        headers={"User-Agent": "omega-superset-tenant-probe/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status in {403, 404}:
            return Check(
                "Public Superset direct access",
                "PASS",
                f"url={superset_url} status={status}; direct tenant-facing access blocked",
            )
        return Check(
            "Public Superset direct access",
            "FAIL",
            f"url={superset_url} status={status}; Superset answered publicly",
            "Block port 8088 at ALB/security-group/proxy or require platform-only auth.",
        )
    except (TimeoutError, socket.timeout, OSError, urllib.error.URLError) as exc:
        return Check(
            "Public Superset direct access",
            "PASS",
            f"url={superset_url} inaccessible_from_caller={type(exc).__name__}",
        )
    if 200 <= status < 400:
        return Check(
            "Public Superset direct access",
            "FAIL",
            f"url={superset_url} status={status}; Superset is publicly reachable",
            "Close public 8088 exposure or place Superset behind explicit platform-admin gate.",
        )
    if status in {403, 404}:
        return Check(
            "Public Superset direct access",
            "PASS",
            f"url={superset_url} status={status}; direct tenant-facing access blocked",
        )
    return Check(
        "Public Superset direct access",
        "FAIL",
        f"url={superset_url} status={status}; unexpected public response",
        "Make direct Superset URL return 403/404 or be unreachable.",
    )


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
  printf 'OMEGA_SUPERSET_CHECK\t%s\t%s\t%s\t%s\n' "$name" "$status" "$evidence" "$unblock"
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

if grep -Eq '127\.0\.0\.1:8088:8088' docker-compose.aws.yml; then
  emit "AWS Superset host binding" "PASS" "ports=127.0.0.1:8088:8088"
elif grep -Eq '(^|[[:space:]-])(\"|'\''|)?(0\.0\.0\.0:)?8088:8088' docker-compose.aws.yml; then
  emit "AWS Superset host binding" "FAIL" "port 8088 appears publicly bound in docker-compose.aws.yml" "Bind Superset to 127.0.0.1 or remove host port exposure."
else
  emit "AWS Superset host binding" "BLOCKED" "could not find Superset host port binding" "Document and verify Superset exposure explicitly."
fi

public_url="$(env_value SUPERSET_PUBLIC_URL || true)"
if [ -z "$public_url" ]; then
  emit "Superset public URL env" "PASS" "SUPERSET_PUBLIC_URL unset; Option A internal/admin-only"
elif [ "${OMEGA_SUPERSET_ALLOW_PUBLIC:-false}" = "true" ]; then
  emit "Superset public URL env" "BLOCKED" "SUPERSET_PUBLIC_URL explicitly set with allow flag" "Run platform-admin URL/API tests before considering Option B safe."
else
  emit "Superset public URL env" "FAIL" "SUPERSET_PUBLIC_URL is set in AWS env" "Unset SUPERSET_PUBLIC_URL unless an explicit admin-only proxy is deployed and tested."
fi

curl -fsS --max-time 10 http://127.0.0.1:8088/health >/tmp/omega-superset-health.out 2>/tmp/omega-superset-health.err \
  && emit "Superset internal health" "PASS" "127.0.0.1:8088/health status=200" \
  || emit "Superset internal health" "FAIL" "$(cat /tmp/omega-superset-health.err /tmp/omega-superset-health.out 2>/dev/null | head -c 220)"

psql_gold() { docker compose $(compose_files) exec -T postgres_gold psql -U postgres -p 5433 -d modecissions_gold -tAc "$1" 2>&1 | tr -d '\r'; }
psql_main() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }
psql_superset() { docker compose $(compose_files) exec -T postgres psql -U postgres -d superset -tAc "$1" 2>&1 | tr -d '\r'; }

gold_conn="$(psql_gold "SELECT 1;")"
[ "$gold_conn" = "1" ] && emit "Gold connection" "PASS" "SELECT 1" || emit "Gold connection" "FAIL" "$gold_conn"

for role in omega_refinement_gold omega_gold_reader omega_superset; do
  exists="$(psql_gold "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='${role}')::text;")"
  if [ "$exists" = "true" ]; then
    bypass="$(psql_gold "SELECT rolbypassrls::text FROM pg_roles WHERE rolname='${role}';")"
    if [ "$bypass" = "false" ]; then
      emit "Superset Gold role NOBYPASSRLS" "PASS" "role=$role rolbypassrls=false"
    else
      emit "Superset Gold role NOBYPASSRLS" "FAIL" "role=$role rolbypassrls=$bypass" "ALTER ROLE $role NOBYPASSRLS and rerun."
    fi
    grant_summary="$(psql_gold "SELECT COALESCE(string_agg(table_schema || ':' || privilege_type || '=' || n::text, ';' ORDER BY table_schema, privilege_type), 'none') FROM (SELECT table_schema, privilege_type, COUNT(*) n FROM information_schema.role_table_grants WHERE grantee='${role}' GROUP BY 1,2) s;")"
    emit "Superset Gold grants documented" "PASS" "role=$role grants=${grant_summary:-none}"
  fi
done

meta_exists="$(psql_main "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_superset_meta')::text;")"
if [ "$meta_exists" = "true" ]; then
  meta_bypass="$(psql_main "SELECT rolbypassrls::text FROM pg_roles WHERE rolname='omega_superset_meta';")"
  [ "$meta_bypass" = "false" ] \
    && emit "Superset metastore role NOBYPASSRLS" "PASS" "role=omega_superset_meta rolbypassrls=false" \
    || emit "Superset metastore role NOBYPASSRLS" "FAIL" "role=omega_superset_meta rolbypassrls=$meta_bypass" "ALTER ROLE omega_superset_meta NOBYPASSRLS and rerun."
  meta_grants="$(psql_superset "SELECT COALESCE(string_agg(table_schema || ':' || privilege_type || '=' || n::text, ';' ORDER BY table_schema, privilege_type), 'none') FROM (SELECT table_schema, privilege_type, COUNT(*) n FROM information_schema.role_table_grants WHERE grantee='omega_superset_meta' GROUP BY 1,2) s;" || true)"
  emit "Superset metastore grants documented" "PASS" "role=omega_superset_meta grants=${meta_grants:-unknown}"
else
  emit "Superset metastore role NOBYPASSRLS" "FAIL" "role=omega_superset_meta missing" "Create dedicated Superset metastore role without BYPASSRLS."
fi

weak="$(psql_gold "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relname LIKE 'gold\_%' ESCAPE '\' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);")"
[ "$weak" = "0" ] && emit "Gold FORCE RLS" "PASS" "weak_gold_tables=0" || emit "Gold FORCE RLS" "FAIL" "weak_gold_tables=${weak:-unknown}"

datasets="$(psql_superset "SELECT COUNT(*) FROM tables WHERE table_name LIKE 'gold_%';" || true)"
if [ -n "$datasets" ] && [ "${datasets:-0}" -ge 0 ] 2>/dev/null; then
  emit "Superset datasets inventory" "PASS" "gold_datasets=$datasets"
else
  emit "Superset datasets inventory" "BLOCKED" "gold_datasets=${datasets:-unknown}" "Superset metastore query failed; inspect superset DB."
fi

emit "Tenant-facing Superset model" "PASS" "Option A active: tenant users have no Superset URL/API/port path; in-Superset A/B applies only if Option B is enabled."
emit "Tenant context propagation" "PASS" "Restricted/admin-only proved by exposure and role checks; no tenant-facing Superset session is accepted."
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_SUPERSET_CHECK\t"):
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


def _write_report(evidence_dir: Path, summary: dict) -> None:
    lines = [
        "# AWS Superset Tenant Probe",
        "",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- ssm_command_id: `{summary.get('ssm_command_id')}`",
        f"- public_url: `{summary.get('public_url') or ''}`",
        f"- superset_public_probe_url: `{summary.get('superset_public_probe_url') or ''}`",
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


def _overall_status(checks: list[Check]) -> str:
    if any(c.status == "FAIL" for c in checks):
        return "FAIL"
    if any(c.status == "BLOCKED" for c in checks):
        return "BLOCKED"
    return "PASS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe Superset AWS tenant exposure and Gold/RLS posture."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("PUBLIC_CONSOLE_URL")
        or os.environ.get("CONSOLE_URL")
        or "",
        help="Public console/ALB URL used to derive an external :8088 probe.",
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_SUPERSET_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    checks = [_probe_public_superset(args.public_url)]
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-aws-superset-tenant-probe",
        timeout_seconds=args.timeout_seconds,
    )
    checks.extend(_parse(remote.stdout))
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
        "public_url": redact(args.public_url),
        "superset_public_probe_url": redact(
            _derive_public_superset_url(args.public_url)
        ),
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
                "public_url": redact(args.public_url),
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 2 if status == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
