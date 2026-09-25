#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import timezone
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


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "beta-smoke-aws"
PUBLIC_ENDPOINTS = ("/healthz", "/readyz", "/readyz?require_data=1")
REQUIRED_REPLICON_GOLD_TABLES = (
    "gold_consultor_mensual",
    "gold_pnl_mensual",
    "gold_forecast_mensual",
)


@dataclass
class Check:
    layer: str
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _short(text: str, limit: int = 700) -> str:
    compact = " ".join(redact(text).split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _http(
    url: str, *, timeout: int = 10, attempts: int = 3
) -> tuple[int, str, Any | None]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    last_error = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                payload = json.loads(body) if body else None
                return int(response.status), body, payload
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body) if body else None
            except json.JSONDecodeError:
                payload = None
            return int(exc.code), body, payload
        except Exception as exc:  # noqa: BLE001 - smoke evidence path.
            last_error = type(exc).__name__
            if attempt < attempts:
                time.sleep(1)
    return 0, last_error, None


def _local_version() -> str:
    path = REPO / "VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _public_checks(public_url: str, expected_version: str) -> list[Check]:
    checks: list[Check] = []
    base = public_url.rstrip("/")
    for endpoint in PUBLIC_ENDPOINTS:
        status, body, payload = _http(f"{base}{endpoint}")
        ok = 200 <= status < 300
        if endpoint == "/healthz":
            health_version = (
                payload.get("version") if isinstance(payload, dict) else None
            )
            ok = ok and (not expected_version or health_version == expected_version)
            evidence = f"status={status} version={health_version} expected={expected_version or '<not-set>'}"
        else:
            evidence = f"status={status} body={_short(body, 220)}"
        checks.append(
            Check(
                layer="public-alb",
                name=f"public {endpoint}",
                status=PASS if ok else FAIL,
                evidence=evidence,
                unblock="Check ALB/listener/target group routing and public security groups.",
            )
        )
    return checks


def _remote_script(
    *,
    expected_version: str,
    expected_deploy_ref: str,
    expected_image_tag: str,
    require_hubspot: bool,
) -> str:
    required_tables = " ".join(REQUIRED_REPLICON_GOLD_TABLES)
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x

REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"
EXPECTED_VERSION={json.dumps(expected_version)}
EXPECTED_DEPLOY_REF={json.dumps(expected_deploy_ref)}
EXPECTED_IMAGE_TAG={json.dumps(expected_image_tag)}
REQUIRE_HUBSPOT={json.dumps("1" if require_hubspot else "0")}
REQUIRED_TABLES=({required_tables})

emit() {{
  local name="$1"
  local status="$2"
  local evidence="$3"
  evidence="$(printf '%s' "$evidence" | tr '\\t\\r\\n' '   ')"
  printf 'OMEGA_CHECK\\t%s\\t%s\\t%s\\n' "$name" "$status" "$evidence"
}}

env_value() {{
  local key="$1"
  if [ ! -f "${{DEPLOY_DIR}}/.env" ]; then
    return 0
  fi
  awk -F= -v key="$key" '$1 == key {{print substr($0, index($0, "=") + 1)}}' "${{DEPLOY_DIR}}/.env" | tail -n 1 | sed "s/^['\\"]//; s/['\\"]$//"
}}

compose_files() {{
  printf -- '-f docker-compose.aws.yml '
  if [ "$(env_value DEPLOY_CARTRIDGES_SAME_HOST || true)" != "false" ]; then
    printf -- '-f docker-compose.cartridges.yml '
  fi
}}

cd "${{DEPLOY_DIR}}"

host_version=""
if [ -f "${{REPO_DIR}}/VERSION" ]; then
  host_version="$(tr -d '\\r\\n' < "${{REPO_DIR}}/VERSION")"
fi
if [ -n "$host_version" ] && {{ [ -z "$EXPECTED_VERSION" ] || [ "$host_version" = "$EXPECTED_VERSION" ]; }}; then
  emit "host VERSION file" "PASS" "path=${{REPO_DIR}}/VERSION version=${{host_version}} expected=${{EXPECTED_VERSION:-<not-set>}}"
else
  emit "host VERSION file" "FAIL" "path=${{REPO_DIR}}/VERSION version=${{host_version:-<missing>}} expected=${{EXPECTED_VERSION:-<not-set>}}"
fi

deploy_ref="$(env_value DEPLOY_REF)"
image_tag="$(env_value IMAGE_TAG)"
app_env="$(env_value APP_ENV)"
if [ -n "$deploy_ref" ] && {{ [ -z "$EXPECTED_DEPLOY_REF" ] || [ "$deploy_ref" = "$EXPECTED_DEPLOY_REF" ]; }}; then
  emit "DEPLOY_REF matches expected" "PASS" "DEPLOY_REF=${{deploy_ref}} expected=${{EXPECTED_DEPLOY_REF:-<not-set>}}"
else
  emit "DEPLOY_REF matches expected" "FAIL" "DEPLOY_REF=${{deploy_ref:-<missing>}} expected=${{EXPECTED_DEPLOY_REF:-<not-set>}}"
fi
if [ -n "$image_tag" ] && {{ [ -z "$EXPECTED_IMAGE_TAG" ] || [ "$image_tag" = "$EXPECTED_IMAGE_TAG" ]; }}; then
  emit "IMAGE_TAG matches expected" "PASS" "IMAGE_TAG=${{image_tag}} expected=${{EXPECTED_IMAGE_TAG:-<not-set>}}"
else
  emit "IMAGE_TAG matches expected" "FAIL" "IMAGE_TAG=${{image_tag:-<missing>}} expected=${{EXPECTED_IMAGE_TAG:-<not-set>}}"
fi
emit "APP_ENV captured" "PASS" "APP_ENV=${{app_env:-<unset>}}"

repo_head="$(git -C "${{REPO_DIR}}" rev-parse --short HEAD 2>/dev/null || true)"
repo_desc="$(git -C "${{REPO_DIR}}" describe --tags --always --dirty 2>/dev/null || true)"
emit "remote git ref captured" "PASS" "head=${{repo_head:-<unknown>}} describe=${{repo_desc:-<unknown>}}"

if docker compose $(compose_files) config --quiet >/dev/null 2>&1; then
  emit "AWS compose config validates" "PASS" "docker compose config --quiet returned 0"
else
  emit "AWS compose config validates" "FAIL" "docker compose config --quiet failed"
fi

running="$(docker compose $(compose_files) ps --status running --services 2>/dev/null | paste -sd, - || true)"
if printf '%s' "$running" | grep -q 'console'; then
  emit "console container running" "PASS" "running_services=${{running}}"
else
  emit "console container running" "FAIL" "running_services=${{running:-<none>}}"
fi

curl_check() {{
  local name="$1"
  local url="$2"
  local body status version
  body="$(curl -fsS --max-time 10 "$url" 2>&1)" && status=0 || status=$?
  if [ "$status" -eq 0 ]; then
    if [ "$name" = "internal /healthz" ]; then
      version="$(printf '%s' "$body" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {{}}).get("version",""))' 2>/dev/null || true)"
      if [ -z "$EXPECTED_VERSION" ] || [ "$version" = "$EXPECTED_VERSION" ]; then
        emit "$name" "PASS" "status=200 version=${{version}} expected=${{EXPECTED_VERSION:-<not-set>}}"
      else
        emit "$name" "FAIL" "status=200 version=${{version:-<missing>}} expected=${{EXPECTED_VERSION:-<not-set>}}"
      fi
    else
      emit "$name" "PASS" "status=200"
    fi
  else
    emit "$name" "FAIL" "curl_exit=${{status}} body=${{body:0:180}}"
  fi
}}

curl_check "internal /healthz" "http://127.0.0.1:8000/healthz"
curl_check "internal /readyz" "http://127.0.0.1:8000/readyz"
curl_check "internal /readyz require_data" "http://127.0.0.1:8000/readyz?require_data=1"
curl_check "internal /readyz require_intelligence" "http://127.0.0.1:8000/readyz?require_data=1&require_intelligence=1"
curl_check "internal superset health" "http://127.0.0.1:8088/health"

psql_op() {{
  docker compose $(compose_files) exec -T postgres psql -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\\r'
}}

psql_gold() {{
  docker compose $(compose_files) exec -T postgres_gold psql -U postgres -p 5433 -d modecissions_gold -tAc "$1" 2>&1 | tr -d '\\r'
}}

lineage="$(psql_gold "SELECT COALESCE(COUNT(*),0)::text || '|' || COALESCE(SUM(row_count),0)::text FROM omega_publication.published_lineage WHERE layer='gold' AND row_count > 0 AND lineage->>'cartridge_id'='replicon';")"
lineage_entries="${{lineage%%|*}}"
lineage_rows="${{lineage##*|}}"
if [ "${{lineage_entries:-0}}" -gt 0 ] 2>/dev/null && [ "${{lineage_rows:-0}}" -gt 0 ] 2>/dev/null; then
  emit "Replicon Gold lineage" "PASS" "entries=${{lineage_entries}} rows=${{lineage_rows}}"
else
  emit "Replicon Gold lineage" "FAIL" "raw=${{lineage}}"
fi

for table in "${{REQUIRED_TABLES[@]}}"; do
  count="$(psql_gold "SELECT CASE WHEN to_regclass('public.${{table}}') IS NULL THEN 'missing' ELSE (SELECT COUNT(*)::text FROM public.\\"${{table}}\\") END;")"
  if [ "$count" != "missing" ] && [ "${{count:-0}}" -gt 0 ] 2>/dev/null; then
    emit "Gold table $table has rows" "PASS" "rows=${{count}}"
  else
    emit "Gold table $table has rows" "FAIL" "rows=${{count:-<error>}}"
  fi
done

if [ "$REQUIRE_HUBSPOT" = "1" ]; then
  hubspot_lineage="$(psql_gold "SELECT COALESCE(COUNT(*),0)::text || '|' || COALESCE(SUM(row_count),0)::text FROM omega_publication.published_lineage WHERE layer='gold' AND row_count > 0 AND lineage->>'cartridge_id'='hubspot';")"
  hubspot_entries="${{hubspot_lineage%%|*}}"
  hubspot_rows="${{hubspot_lineage##*|}}"
  if [ "${{hubspot_entries:-0}}" -gt 0 ] 2>/dev/null && [ "${{hubspot_rows:-0}}" -gt 0 ] 2>/dev/null; then
    emit "HubSpot optional Gold lineage" "PASS" "entries=${{hubspot_entries}} rows=${{hubspot_rows}}"
  else
    emit "HubSpot optional Gold lineage" "FAIL" "raw=${{hubspot_lineage}}"
  fi
  hubspot_deals="$(psql_gold "SELECT CASE WHEN to_regclass('public.gold_deals_estancados') IS NULL THEN 'missing' ELSE (SELECT COUNT(*)::text FROM public.\\"gold_deals_estancados\\") END;")"
  if [ "$hubspot_deals" != "missing" ] && [ "${{hubspot_deals:-0}}" -gt 0 ] 2>/dev/null; then
    emit "HubSpot optional gold_deals_estancados" "PASS" "rows=${{hubspot_deals}}"
  else
    emit "HubSpot optional gold_deals_estancados" "FAIL" "rows=${{hubspot_deals:-<error>}}"
  fi
else
  emit "HubSpot optional golden path" "PASS" "OMEGA_BETA_REQUIRE_HUBSPOT=0; AWS beta blocks on Replicon Gold"
fi

weak="$(psql_gold "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relname LIKE 'gold\\_%' ESCAPE '\\' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);")"
if [ "${{weak:-1}}" = "0" ]; then
  emit "Gold FORCE RLS" "PASS" "weak_gold_tables=0"
else
  emit "Gold FORCE RLS" "FAIL" "weak_gold_tables=${{weak:-<error>}}"
fi

gold_read_role="$(psql_gold "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_gold_reader') THEN 'omega_gold_reader' ELSE 'omega_refinement_gold' END;")"
bypass="$(psql_gold "SELECT COALESCE((SELECT rolbypassrls::text FROM pg_roles WHERE rolname='${{gold_read_role}}'), 'missing');")"
if [ "$bypass" = "false" ]; then
  emit "Gold read role NOBYPASSRLS" "PASS" "role=${{gold_read_role}} rolbypassrls=false"
else
  emit "Gold read role NOBYPASSRLS" "FAIL" "role=${{gold_read_role:-<missing>}} rolbypassrls=${{bypass:-<error>}}"
fi

signals="$(psql_op "SELECT CASE WHEN to_regclass('public.intelligence_signals') IS NULL THEN 'missing' ELSE (SELECT COUNT(*)::text FROM intelligence_signals WHERE cartridge_id='replicon') END;")"
if [ "$signals" != "missing" ] && [ "${{signals:-0}}" -gt 0 ] 2>/dev/null; then
  emit "Replicon Intelligence signals" "PASS" "rows=${{signals}}"
else
  emit "Replicon Intelligence signals" "FAIL" "rows=${{signals:-<error>}}"
fi

items="$(psql_op "SELECT CASE WHEN to_regclass('public.control_room_items') IS NULL THEN 'missing' ELSE (SELECT COUNT(*)::text FROM control_room_items WHERE cartridge_id='replicon' AND COALESCE(item_kind,'') <> 'source_state') END;")"
if [ "$items" != "missing" ] && [ "${{items:-0}}" -gt 0 ] 2>/dev/null; then
  emit "Replicon Control Room items" "PASS" "rows=${{items}}"
else
  emit "Replicon Control Room items" "FAIL" "rows=${{items:-<error>}}"
fi

writeback="$(docker compose $(compose_files) exec -T console printenv CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK 2>/dev/null || true)"
case "$(printf '%s' "$writeback" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) emit "external write-back disabled" "FAIL" "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=true" ;;
  *) emit "external write-back disabled" "PASS" "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=${{writeback:-<unset>}}" ;;
esac
"""


def _parse_remote_checks(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_CHECK\t"):
            continue
        _prefix, name, status, evidence = (line.split("\t", 3) + [""])[:4]
        checks.append(
            Check(
                layer="internal-ec2",
                name=name,
                status=status,
                evidence=_short(evidence),
            )
        )
    return checks


def _write_evidence(
    *,
    evidence_dir: Path,
    metadata: dict[str, Any],
    checks: list[Check],
    remote_stdout: str,
    remote_stderr: str,
) -> str:
    status = (
        FAIL
        if any(check.status == FAIL for check in checks)
        else BLOCKED
        if any(check.status == BLOCKED for check in checks)
        else PASS
    )
    summary = {
        "status": status,
        "metadata": metadata,
        "checks": [asdict(check) for check in checks],
        "pass": sum(1 for check in checks if check.status == PASS),
        "fail": sum(1 for check in checks if check.status == FAIL),
        "blocked": sum(1 for check in checks if check.status == BLOCKED),
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(
        redact(remote_stdout), encoding="utf-8"
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(remote_stderr), encoding="utf-8"
    )
    lines = [
        "# AWS Beta Smoke Evidence",
        "",
        f"- status: `{status}`",
        f"- generated_at_utc: `{metadata['generated_at_utc']}`",
        f"- ssm_command_id: `{metadata.get('ssm_command_id') or '<not-sent>'}`",
        f"- instance_id: `{metadata['instance_id']}`",
        f"- region: `{metadata['region']}`",
        f"- deploy_ref: `{metadata.get('deploy_ref') or '<not-set>'}`",
        f"- image_tag: `{metadata.get('image_tag') or '<not-set>'}`",
        f"- public_url: `{metadata['public_url']}`",
        "",
        "| Layer | Check | Status | Evidence |",
        "|---|---|---|---|",
    ]
    for check in checks:
        lines.append(
            f"| {check.layer} | {check.name} | {check.status} | {check.evidence.replace('|', '\\|')} |"
        )
    rollback = metadata.get("rollback_command")
    if rollback:
        lines.extend(["", "## Minimum Rollback", "", f"`{rollback}`"])
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only AWS beta smoke checks over SSM."
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
    parser.add_argument("--deploy-ref", default=os.environ.get("DEPLOY_REF") or "")
    parser.add_argument("--image-tag", default=os.environ.get("IMAGE_TAG") or "")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_SMOKE_TIMEOUT_SECONDS", "900")),
    )
    args = parser.parse_args(argv)

    if not args.public_url:
        raise SystemExit("PUBLIC_CONSOLE_URL or --public-url is required")

    generated_at = utc_now().astimezone(timezone.utc).isoformat()
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    expected_version = _local_version()
    rollback_target = (
        os.environ.get("DEPLOY_REF_OLD") or os.environ.get("IMAGE_TAG_OLD") or ""
    )
    rollback_command = ""
    if rollback_target:
        rollback_command = (
            f"AWS_REGION={args.region} AWS_APP_INSTANCE_ID={instance_id} "
            f"DEPLOY_REF_OLD={rollback_target} IMAGE_TAG_OLD={rollback_target} make rollback-aws"
        )
    metadata: dict[str, Any] = {
        "generated_at_utc": generated_at,
        "instance_id": instance_id,
        "region": args.region,
        "deploy_ref": args.deploy_ref,
        "image_tag": args.image_tag,
        "public_url": args.public_url.rstrip("/"),
        "expected_version": expected_version,
        "golden_path": "replicon",
        "hubspot_required": os.environ.get("OMEGA_BETA_REQUIRE_HUBSPOT", "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"},
        "rollback_command": rollback_command,
    }

    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(
            expected_version=expected_version,
            expected_deploy_ref=args.deploy_ref,
            expected_image_tag=args.image_tag,
            require_hubspot=bool(metadata["hubspot_required"]),
        ),
        comment="omega-beta-smoke-aws",
        timeout_seconds=args.timeout_seconds,
    )
    metadata.update(
        {
            "ssm_command_id": remote.command_id,
            "ssm_status": remote.status,
            "ssm_response_code": remote.response_code,
        }
    )
    checks = _public_checks(args.public_url, expected_version)
    if remote.status != "Success":
        checks.append(
            Check(
                layer="internal-ec2",
                name="SSM command completed",
                status=FAIL,
                evidence=f"status={remote.status} response_code={remote.response_code}",
            )
        )
    else:
        checks.append(
            Check(
                layer="internal-ec2",
                name="SSM command completed",
                status=PASS,
                evidence=f"command_id={remote.command_id} response_code={remote.response_code}",
            )
        )
    checks.extend(_parse_remote_checks(remote.stdout))
    status = _write_evidence(
        evidence_dir=evidence_dir,
        metadata=metadata,
        checks=checks,
        remote_stdout=remote.stdout,
        remote_stderr=remote.stderr,
    )
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
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
