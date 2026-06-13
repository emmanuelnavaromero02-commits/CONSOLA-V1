#!/usr/bin/env python3
"""Generate a low-cost AWS observability report through public HTTP + SSM."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
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


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-observability-report"


@dataclass
class Check:
    layer: str
    name: str
    status: str
    evidence: str


def _short(text: str, limit: int = 800) -> str:
    compact = " ".join(redact(text).split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _http_json(url: str) -> tuple[str, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return "PASS" if 200 <= int(
                response.status
            ) < 300 else "FAIL", response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return "FAIL", exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - evidence capture path
        return "FAIL", type(exc).__name__


def _remote_script() -> str:
    return r"""#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"

emit() {
  local layer="$1"
  local name="$2"
  local status="$3"
  local evidence="${4:-}"
  evidence="${evidence//$'\t'/ }"
  evidence="${evidence//$'\r'/ }"
  evidence="${evidence//$'\n'/ }"
  printf 'OMEGA_OBS_CHECK\t%s\t%s\t%s\t%s\n' "$layer" "$name" "$status" "$evidence"
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
emit "release" "VERSION" "PASS" "version=$(tr -d '\r\n' < "$REPO_DIR/VERSION" 2>/dev/null || true)"
emit "release" "DEPLOY_REF" "PASS" "deploy_ref=$(env_value DEPLOY_REF)"
emit "release" "IMAGE_TAG" "PASS" "image_tag=$(env_value IMAGE_TAG)"
writeback_state="$(docker compose $(compose_files) exec -T console sh -lc 'case "${CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK:-false}" in 1|true|yes|on) echo enabled;; *) echo disabled;; esac' 2>/dev/null || echo unknown)"
if [ "$writeback_state" = "disabled" ]; then
  emit "release" "external writeback disabled" "PASS" "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK disabled"
else
  emit "release" "external writeback disabled" "FAIL" "state=$writeback_state"
fi

for target in \
  "console http://127.0.0.1:8000/readyz" \
  "workspace http://127.0.0.1:8001/healthz" \
  "refinement http://127.0.0.1:8500/healthz" \
  "vault http://127.0.0.1:8300/healthz" \
  "mcp-infra http://127.0.0.1:8010/healthz" \
  "superset http://127.0.0.1:8088/health"; do
  name="${target%% *}"
  url="${target#* }"
  if curl -fsS --max-time 8 "$url" >/tmp/omega-obs-http.out 2>/tmp/omega-obs-http.err; then
    emit "internal-health" "$name" "PASS" "status=200"
  else
    emit "internal-health" "$name" "FAIL" "$(cat /tmp/omega-obs-http.err /tmp/omega-obs-http.out 2>/dev/null | head -c 220)"
  fi
done

running="$(docker compose $(compose_files) ps --status running --services | paste -sd, - || true)"
emit "docker" "running services" "PASS" "$running"
restarts="$(docker inspect $(docker compose $(compose_files) ps -q 2>/dev/null) --format '{{.Name}}={{.RestartCount}}' 2>/dev/null | paste -sd, - || true)"
emit "docker" "container restarts" "PASS" "$restarts"

docker compose $(compose_files) exec -T postgres pg_isready -U postgres -d modecissions >/tmp/omega-obs-pg.out 2>/tmp/omega-obs-pg.err && emit "db" "operational postgres" "PASS" "pg_isready ok" || emit "db" "operational postgres" "FAIL" "$(cat /tmp/omega-obs-pg.err /tmp/omega-obs-pg.out 2>/dev/null | head -c 220)"
docker compose $(compose_files) exec -T postgres_gold pg_isready -U postgres -p 5433 -d modecissions_gold >/tmp/omega-obs-gold.out 2>/tmp/omega-obs-gold.err && emit "db" "gold postgres" "PASS" "pg_isready ok" || emit "db" "gold postgres" "FAIL" "$(cat /tmp/omega-obs-gold.err /tmp/omega-obs-gold.out 2>/dev/null | head -c 220)"
docker compose $(compose_files) exec -T redis redis-cli ping >/tmp/omega-obs-redis.out 2>/tmp/omega-obs-redis.err && emit "cache" "redis ping" "PASS" "$(cat /tmp/omega-obs-redis.out | head -c 80)" || emit "cache" "redis ping" "FAIL" "$(cat /tmp/omega-obs-redis.err /tmp/omega-obs-redis.out 2>/dev/null | head -c 220)"

psql_op() { docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\r'; }
psql_gold() { docker compose $(compose_files) exec -T postgres_gold psql -U postgres -p 5433 -d modecissions_gold -tAc "$1" 2>&1 | tr -d '\r'; }
gold_tables="$(psql_gold "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'gold_%';")"
weak_rls="$(psql_gold "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relname LIKE 'gold\_%' ESCAPE '\' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);")"
emit "data" "gold tables" "$([ "${gold_tables:-0}" -gt 0 ] 2>/dev/null && echo PASS || echo FAIL)" "gold_tables=${gold_tables:-unknown}"
emit "security" "gold force rls" "$([ "${weak_rls:-1}" = "0" ] && echo PASS || echo FAIL)" "weak_gold_tables=${weak_rls:-unknown}"
emit "ops" "failed action runs 24h" "PASS" "$(psql_op "SELECT CASE WHEN to_regclass('public.action_runs') IS NULL THEN 'missing' ELSE (SELECT COUNT(*)::text FROM action_runs WHERE status IN ('failed','blocked') AND created_at > NOW() - INTERVAL '24 hours') END;")"
emit "ops" "failed intelligence runs 24h" "PASS" "$(psql_op "SELECT CASE WHEN to_regclass('public.intelligence_runs') IS NULL THEN 'missing' ELSE (SELECT COUNT(*)::text FROM intelligence_runs WHERE status IN ('failed','error') AND started_at > NOW() - INTERVAL '24 hours') END;")"
emit "ops" "failed backtest runs 24h" "PASS" "$(psql_op "SELECT CASE WHEN to_regclass('public.backtest_runs') IS NULL THEN 'missing' ELSE (SELECT COUNT(*)::text FROM backtest_runs WHERE status='failed' AND started_at > NOW() - INTERVAL '24 hours') END;")"

if command -v aws >/dev/null 2>&1; then
  bucket="$(env_value S3_BUCKET_NAME)"
  region="$(env_value AWS_REGION)"
  if [ -n "$bucket" ]; then
    backups="$(aws s3 ls "s3://${bucket}/backups/" --region "${region:-us-east-1}" 2>/dev/null | tail -n 1 | awk '{print $2}' | tr -d '/' || true)"
    lake_objects="$(aws s3 ls "s3://${bucket}/" --region "${region:-us-east-1}" --recursive 2>/dev/null | grep -v '/backups/' | wc -l | tr -d ' ' || true)"
    emit "lakehouse" "s3 object listing" "$([ "${lake_objects:-0}" -gt 0 ] 2>/dev/null && echo PASS || echo FAIL)" "objects=${lake_objects:-0}"
    emit "backup" "latest backup prefix" "$([ -n "$backups" ] && echo PASS || echo BLOCKED)" "latest=${backups:-<none>}"
  else
    emit "lakehouse" "s3 bucket configured" "FAIL" "S3_BUCKET_NAME missing"
  fi
else
  emit "lakehouse" "aws cli available" "BLOCKED" "aws cli missing on host"
fi

emit "host" "disk usage" "PASS" "$(df -h / | tail -n 1)"
emit "host" "memory" "PASS" "$(free -m 2>/dev/null | awk '/Mem:/ {print "mem_total_mb="$2" mem_used_mb="$3" mem_available_mb="$7}' || true)"
errors="$(docker logs --tail 2000 mode_console 2>&1 | grep -Eic 'error|exception|traceback' || true)"
emit "logs" "recent console errors" "PASS" "error_like_lines=${errors:-0}"
"""


def _parse_checks(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_OBS_CHECK\t"):
            continue
        _prefix, layer, name, status, evidence = (line.split("\t", 4) + [""])[:5]
        checks.append(
            Check(layer=layer, name=name, status=status, evidence=_short(evidence))
        )
    return checks


def _write_report(evidence_dir: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# AWS Observability Report",
        "",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- ssm_command_id: `{summary.get('ssm_command_id') or '<not-run>'}`",
        f"- instance_id: `{summary['instance_id']}`",
        f"- region: `{summary['region']}`",
        f"- public_url: `{summary.get('public_url') or '<not-set>'}`",
        "",
        "| Layer | Check | Status | Evidence |",
        "|---|---|---|---|",
    ]
    for check in summary["checks"]:
        evidence = str(check["evidence"]).replace("|", "\\|")
        lines.append(
            f"| {check['layer']} | {check['name']} | {check['status']} | {evidence} |"
        )
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate low-cost AWS observability evidence."
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
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_OBSERVABILITY_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)

    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    checks: list[Check] = []
    public_url = args.public_url.rstrip("/")
    if public_url:
        for endpoint in ("/healthz", "/readyz", "/readyz?require_data=1"):
            status, body = _http_json(f"{public_url}{endpoint}")
            checks.append(Check("public", endpoint, status, _short(body, 260)))
    else:
        checks.append(
            Check(
                "public",
                "public url configured",
                "BLOCKED",
                "PUBLIC_CONSOLE_URL not set",
            )
        )

    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-aws-observability-report",
        timeout_seconds=args.timeout_seconds,
    )
    checks.append(
        Check(
            "ssm",
            "command completed",
            "PASS"
            if remote.status == "Success" and remote.response_code == 0
            else "FAIL",
            f"command_id={remote.command_id} status={remote.status} response_code={remote.response_code}",
        )
    )
    checks.extend(_parse_checks(remote.stdout))
    status = (
        "FAIL"
        if any(check.status == "FAIL" for check in checks)
        else "BLOCKED"
        if any(check.status == "BLOCKED" for check in checks)
        else "PASS"
    )
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "instance_id": instance_id,
        "region": args.region,
        "public_url": public_url,
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
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 2 if status == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
