#!/usr/bin/env python3
"""Tenant A/B end-to-end isolation harness for the Replicon AWS beta path."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_now, utc_stamp, write_json


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
DEFAULT_LOCAL_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "tenant-ab-local"
DEFAULT_AWS_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "tenant-ab-aws"
psycopg2: Any = None
sql: Any = None
Json: Any = None
_load_env_file: Any = None
replicon_beta_gold_scope_fingerprint: Any = None
seed_replicon_beta_gold: Any = None


@dataclass
class Check:
    layer: str
    name: str
    status: str
    evidence: str


@dataclass
class Scope:
    label: str
    tenant_id: str
    workspace_id: str
    user_id: int
    email: str
    signal_id: str
    item_id: str


def _short(text: str, limit: int = 700) -> str:
    compact = " ".join(redact(text).split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _env_file(path: Path = REPO / "infra" / ".env") -> None:
    _load_db_modules()
    _load_env_file(path)


def _load_db_modules() -> None:
    global Json, _load_env_file, psycopg2, replicon_beta_gold_scope_fingerprint, seed_replicon_beta_gold, sql
    if psycopg2 is not None:
        return
    import psycopg2 as _psycopg2  # noqa: PLC0415
    from psycopg2 import sql as _sql  # noqa: PLC0415
    from psycopg2.extras import Json as _Json  # noqa: PLC0415
    from seed_replicon_beta_gold import (  # noqa: PLC0415
        _load_env_file as _seed_load_env_file,
        replicon_beta_gold_scope_fingerprint as _fingerprint,
        seed_replicon_beta_gold as _seed,
    )

    psycopg2 = _psycopg2
    sql = _sql
    Json = _Json
    _load_env_file = _seed_load_env_file
    replicon_beta_gold_scope_fingerprint = _fingerprint
    seed_replicon_beta_gold = _seed


def _normalize_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _admin_dsn(database: str, port_env: str, default_port: str) -> str:
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not password:
        return ""
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get(port_env, default_port)
    return f"postgresql://postgres:{password}@{host}:{port}/{database}"


def _operational_dsn() -> str:
    return _normalize_dsn(os.environ.get("DATABASE_URL") or _admin_dsn("modecissions", "POSTGRES_PORT", "15432"))


def _gold_admin_dsn() -> str:
    return _normalize_dsn(os.environ.get("GOLD_DATABASE_URL") or _admin_dsn("modecissions_gold", "POSTGRES_GOLD_PORT", "15433"))


def _role_dsn(base_dsn: str, role: str, password: str | None) -> str:
    if not password:
        return ""
    parsed = urllib.parse.urlsplit(base_dsn)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    userinfo = f"{urllib.parse.quote(role)}:{urllib.parse.quote(password)}"
    return urllib.parse.urlunsplit((parsed.scheme or "postgresql", f"{userinfo}@{host}", parsed.path, parsed.query, parsed.fragment))


def _table_columns(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema='public'
           AND table_name=%s
        """,
        (table,),
    )
    return {str(row[0]) for row in cur.fetchall()}


def _ensure_replicon_entitlement(cur, tenant_id: str, workspace_id: str, user_id: int, suffix: str) -> None:
    cur.execute("SELECT to_regclass('public.marketplace_products')")
    if not cur.fetchone()[0]:
        return
    cur.execute(
        "SELECT id FROM marketplace_products WHERE cartridge_id='replicon' AND status IN ('active','internal') LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return
    product_id = row[0]
    cur.execute("SELECT to_regclass('public.tenant_entitlements')")
    if cur.fetchone()[0]:
        cur.execute(
            """
            INSERT INTO tenant_entitlements (
                tenant_id, workspace_id, cartridge_id, product_id, status, activated_by_id
            )
            VALUES (%s, %s, 'replicon', %s, 'active', %s)
            ON CONFLICT (tenant_id, workspace_id, cartridge_id) DO UPDATE
              SET status='active', product_id=EXCLUDED.product_id, activated_by_id=EXCLUDED.activated_by_id
            """,
            (tenant_id, workspace_id, product_id, user_id),
        )
    cur.execute("SELECT to_regclass('public.cartridge_installations')")
    if cur.fetchone()[0]:
        cur.execute(
            """
            INSERT INTO cartridge_installations (
                id, tenant_id, workspace_id, cartridge_id, product_id, status,
                current_step, install_fingerprint, created_by_id, ready_at
            )
            VALUES (%s, %s, %s, 'replicon', %s, 'ready', 'tenant_ab_seeded', %s, %s, NOW())
            ON CONFLICT (workspace_id, cartridge_id) DO UPDATE
              SET status='ready', product_id=EXCLUDED.product_id, current_step='tenant_ab_seeded', ready_at=NOW(), updated_at=NOW()
            """,
            (f"tenant_ab_{suffix}_{workspace_id[:8]}", tenant_id, workspace_id, product_id, f"tenant-ab:{suffix}:{workspace_id}", user_id),
        )


def _seed_operational_artifacts(cur, scope: Scope) -> None:
    cur.execute("SELECT EXISTS (SELECT 1 FROM cartridges WHERE id='replicon')")
    if not cur.fetchone()[0]:
        raise RuntimeError("cartridge replicon is missing; cannot seed Control Room item")
    cur.execute(
        """
        INSERT INTO intelligence_signals (
            signal_id, tenant_id, workspace_id, cartridge_id, dataset, domain,
            entity_kind, entity_id, entity_label, metric, period_key,
            actual_value, expected_value, deviation_value, deviation_pct,
            severity, signal_type, confidence, summary, owner_user_id, metadata
        )
        VALUES (%s, %s, %s, 'replicon', 'consultor_mensual', 'tenant_ab',
                'workspace', %s, %s, 'margin_leakage', '2026-06',
                120, 100, 20, 0.20, 'medium', 'opportunity', 0.90,
                %s, %s, %s)
        ON CONFLICT (workspace_id, signal_id) DO UPDATE
          SET summary=EXCLUDED.summary, metadata=EXCLUDED.metadata, owner_user_id=EXCLUDED.owner_user_id
        """,
        (
            scope.signal_id,
            scope.tenant_id,
            scope.workspace_id,
            scope.workspace_id,
            f"Workspace {scope.label}",
            f"Tenant {scope.label} isolation signal",
            scope.user_id,
            Json({"tenant_ab": scope.label}),
        ),
    )
    columns = _table_columns(cur, "control_room_items")
    insert_cols = [
        "tenant_id",
        "workspace_id",
        "item_id",
        "cartridge_id",
        "domain",
        "source_dataset",
        "item_kind",
        "title",
        "severity",
        "status",
        "entity_kind",
        "entity_id",
        "entity_label",
        "anomaly_type",
        "metadata",
    ]
    values: list[Any] = [
        scope.tenant_id,
        scope.workspace_id,
        scope.item_id,
        "replicon",
        "tenant_ab",
        "consultor_mensual",
        "intelligence_signal",
        f"Tenant {scope.label} control item",
        "medium",
        "open",
        "workspace",
        scope.workspace_id,
        f"Workspace {scope.label}",
        "tenant_ab_isolation",
        Json({"tenant_ab": scope.label, "signal_id": scope.signal_id}),
    ]
    if "owner_user_id" in columns:
        insert_cols.insert(2, "owner_user_id")
        values.insert(2, scope.user_id)
    cur.execute(
        sql.SQL(
            """
            INSERT INTO control_room_items ({cols})
            VALUES ({vals})
            ON CONFLICT (workspace_id, item_id) DO UPDATE
              SET title=EXCLUDED.title,
                  metadata=control_room_items.metadata || EXCLUDED.metadata,
                  last_seen_at=NOW()
            """
        ).format(
            cols=sql.SQL(", ").join(sql.Identifier(col) for col in insert_cols),
            vals=sql.SQL(", ").join(sql.Placeholder() for _ in insert_cols),
        ),
        values,
    )


def _create_scope(cur, suffix: str, label: str) -> Scope:
    tenant_id = str(cur.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"tenant-ab-{suffix}-{label}",)) or cur.fetchone()[0])
    workspace_id = str(cur.execute("INSERT INTO workspaces (tenant_id, name) VALUES (%s, %s) RETURNING id", (tenant_id, f"Tenant AB {label} {suffix}")) or cur.fetchone()[0])
    cur.execute("SELECT id FROM roles WHERE name='tenant_admin' LIMIT 1")
    role_row = cur.fetchone()
    if not role_row:
        raise RuntimeError("tenant_admin role is missing")
    email = f"tenant-ab-{suffix}-{label.lower()}@example.invalid"
    cur.execute(
        """
        INSERT INTO users (email, name, password_hash, role, tenant_id, is_active, must_change_password)
        VALUES (%s, %s, 'tenant-ab-disabled-password', 'user', %s, TRUE, FALSE)
        RETURNING id
        """,
        (email, f"Tenant AB {label}", tenant_id),
    )
    user_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO user_workspace_roles (user_id, workspace_id, role_id) VALUES (%s, %s, %s)",
        (user_id, workspace_id, int(role_row[0])),
    )
    _ensure_replicon_entitlement(cur, tenant_id, workspace_id, user_id, suffix)
    return Scope(
        label=label,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        email=email,
        signal_id=f"tenant-ab-{suffix}-{label.lower()}-signal",
        item_id=f"tenant-ab-{suffix}-{label.lower()}-item",
    )


def _b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _jwt(scope: Scope) -> str:
    secret = os.environ.get("JWT_SECRET_KEY", "")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY missing for API isolation probes")
    now = int(time.time())
    claims = {
        "sub": str(scope.user_id),
        "email": scope.email,
        "role": "user",
        "iat": now,
        "exp": now + int(timedelta(minutes=15).total_seconds()),
        "jti": uuid.uuid4().hex,
    }
    header = {"alg": os.environ.get("JWT_ALGORITHM", "HS256"), "typ": "JWT"}
    if header["alg"] != "HS256":
        raise RuntimeError("tenant_ab_e2e only supports JWT_ALGORITHM=HS256")
    signing_input = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64url(
        json.dumps(claims, separators=(",", ":")).encode()
    )
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64url(signature)


def _http_json(
    base_url: str,
    path: str,
    scope: Scope,
    *,
    workspace_id: str | None = None,
    expected: set[int] | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {_jwt(scope)}",
        "x-workspace-id": workspace_id or scope.workspace_id,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            status = int(response.status)
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read().decode("utf-8", errors="replace")
    allowed = expected or {200}
    if status not in allowed:
        raise RuntimeError(f"{method.upper()} {path} returned {status}: {_short(body)}")
    try:
        return status, json.loads(body) if body else {}
    except json.JSONDecodeError:
        return status, body


def _contains(value: Any, needle: str) -> bool:
    return needle in json.dumps(value, sort_keys=True, default=str)


def _run_gold_checks(a: Scope, b: Scope) -> list[Check]:
    checks: list[Check] = []
    gold_admin = _gold_admin_dsn()
    gold_role = "omega_gold_reader" if os.environ.get("OMEGA_GOLD_READER_PASSWORD") else "omega_refinement_gold"
    gold_password = os.environ.get("OMEGA_GOLD_READER_PASSWORD") or os.environ.get("OMEGA_REFINEMENT_GOLD_PASSWORD")
    reader = _role_dsn(gold_admin, gold_role, gold_password)
    if not reader:
        return [Check("gold", "Gold read role credentials", BLOCKED, "OMEGA_GOLD_READER_PASSWORD or OMEGA_REFINEMENT_GOLD_PASSWORD missing")]
    for own, other in ((a, b), (b, a)):
        conn = psycopg2.connect(reader)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                    (own.tenant_id, own.workspace_id),
                )
                cur.execute(
                    """
                    SELECT COUNT(*) FROM public.gold_consultor_mensual
                     WHERE tenant_id::text=%s AND workspace_id::text=%s
                    """,
                    (own.tenant_id, own.workspace_id),
                )
                own_rows = int(cur.fetchone()[0])
                cur.execute(
                    """
                    SELECT COUNT(*) FROM public.gold_consultor_mensual
                     WHERE tenant_id::text=%s OR workspace_id::text=%s
                    """,
                    (other.tenant_id, other.workspace_id),
                )
                leaked_rows = int(cur.fetchone()[0])
        finally:
            conn.close()
        checks.append(
            Check(
                "gold",
                f"{own.label} sees own Replicon Gold",
                PASS if own_rows > 0 else FAIL,
                f"role={gold_role} rows={own_rows}",
            )
        )
        checks.append(
            Check(
                "gold",
                f"{own.label} forbidden Gold query for {other.label}",
                PASS if leaked_rows == 0 else FAIL,
                f"role={gold_role} cross_rows={leaked_rows}",
            )
        )
    return checks


def _run_api_checks(a: Scope, b: Scope) -> list[Check]:
    checks: list[Check] = []
    base_url = os.environ.get("CONSOLE_URL", "http://127.0.0.1:8000")
    for own, other in ((a, b), (b, a)):
        _status, signals = _http_json(base_url, f"/api/intelligence/signals?limit=500&tenant_id={other.tenant_id}&workspace_id={other.workspace_id}", own)
        sees_own = _contains(signals, own.signal_id)
        sees_other = _contains(signals, other.signal_id) or _contains(signals, other.workspace_id)
        checks.append(Check("api", f"{own.label} API sees own signal", PASS if sees_own else FAIL, f"signal_id={own.signal_id}"))
        checks.append(
            Check(
                "api",
                f"{own.label} API explicit forbidden tenant/workspace {other.label}",
                PASS if not sees_other else FAIL,
                f"other_signal_present={sees_other}",
            )
        )
        status, _payload = _http_json(base_url, "/api/intelligence/signals?limit=20", own, workspace_id=other.workspace_id, expected={403})
        checks.append(Check("api", f"{own.label} x-workspace-id {other.label} rejected", PASS if status == 403 else FAIL, f"status={status}"))
        _status, dashboard = _http_json(base_url, f"/api/control-room/dashboard?tenant_id={other.tenant_id}&workspace_id={other.workspace_id}", own)
        control_leak = _contains(dashboard, other.item_id) or _contains(dashboard, other.workspace_id)
        checks.append(
            Check(
                "control-room",
                f"{own.label} Control Room explicit forbidden {other.label}",
                PASS if not control_leak else FAIL,
                f"other_item_present={control_leak}",
            )
        )
        status, _payload = _http_json(base_url, "/api/copilot/briefing/v2", own, workspace_id=other.workspace_id, expected={403})
        checks.append(Check("copilot", f"{own.label} Copilot workspace {other.label} rejected", PASS if status == 403 else FAIL, f"status={status}"))
        mutation_probes = (
            (
                "decision",
                f"/api/control-room/items/{urllib.parse.quote(other.item_id)}/decision",
                {},
            ),
            (
                "outcome",
                f"/api/control-room/items/{urllib.parse.quote(other.item_id)}/outcomes",
                {"action_taken": "tenant_ab_forbidden_probe", "outcome_summary": "must not cross scope"},
            ),
            (
                "lesson",
                f"/api/control-room/items/{urllib.parse.quote(other.item_id)}/lessons",
                {"rule": "Tenant A/B forbidden lesson probe must not cross workspace."},
            ),
            (
                "execute",
                f"/api/control-room/items/{urllib.parse.quote(other.item_id)}/execute",
                {"template_id": "create_followup_task", "confirm_execute": True, "idempotency_key": f"tenant-ab-forbidden-{own.label}-{other.label}"},
            ),
        )
        for probe_name, path, body in mutation_probes:
            status, payload = _http_json(
                base_url,
                path,
                own,
                expected={403, 404},
                method="POST",
                body=body,
            )
            checks.append(
                Check(
                    "control-room",
                    f"{own.label} forbidden {probe_name} mutation on {other.label}",
                    PASS if status in {403, 404} else FAIL,
                    f"status={status} body={_short(json.dumps(payload, sort_keys=True, default=str))}",
                )
            )
    return checks


def _run_local(evidence_dir: Path, *, suffix: str | None = None) -> int:
    _env_file()
    suffix = suffix or utc_stamp().lower()
    checks: list[Check] = []
    op = psycopg2.connect(_operational_dsn())
    try:
        with op:
            with op.cursor() as cur:
                a = _create_scope(cur, suffix, "A")
                b = _create_scope(cur, suffix, "B")
    finally:
        op.close()

    old_tenant = os.environ.get("OMEGA_SEED_TENANT_ID")
    old_workspace = os.environ.get("OMEGA_SEED_WORKSPACE_ID")
    old_update_catalog = os.environ.get("OMEGA_SEED_UPDATE_CATALOG")
    try:
        for scope in (a, b):
            os.environ["OMEGA_SEED_TENANT_ID"] = scope.tenant_id
            os.environ["OMEGA_SEED_WORKSPACE_ID"] = scope.workspace_id
            os.environ["OMEGA_SEED_UPDATE_CATALOG"] = "0"
            result = seed_replicon_beta_gold()
            fingerprint = result["scope_fingerprint"]
            checks.append(
                Check(
                    "seed",
                    f"seed {scope.label} Replicon Gold",
                    PASS if fingerprint["row_count"] > 0 else FAIL,
                    f"rows={fingerprint['row_count']} checksum={fingerprint['checksum']}",
                )
            )
        op = psycopg2.connect(_operational_dsn())
        try:
            with op:
                with op.cursor() as cur:
                    _seed_operational_artifacts(cur, a)
                    _seed_operational_artifacts(cur, b)
        finally:
            op.close()
    finally:
        if old_tenant is None:
            os.environ.pop("OMEGA_SEED_TENANT_ID", None)
        else:
            os.environ["OMEGA_SEED_TENANT_ID"] = old_tenant
        if old_workspace is None:
            os.environ.pop("OMEGA_SEED_WORKSPACE_ID", None)
        else:
            os.environ["OMEGA_SEED_WORKSPACE_ID"] = old_workspace
        if old_update_catalog is None:
            os.environ.pop("OMEGA_SEED_UPDATE_CATALOG", None)
        else:
            os.environ["OMEGA_SEED_UPDATE_CATALOG"] = old_update_catalog

    checks.extend(_run_gold_checks(a, b))
    checks.extend(_run_api_checks(a, b))
    a_fp = replicon_beta_gold_scope_fingerprint(a.tenant_id, a.workspace_id)
    b_fp = replicon_beta_gold_scope_fingerprint(b.tenant_id, b.workspace_id)
    status = FAIL if any(check.status == FAIL for check in checks) else BLOCKED if any(check.status == BLOCKED for check in checks) else PASS
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "suffix": suffix,
        "tenant_a": asdict(a),
        "tenant_b": asdict(b),
        "fingerprints": {"A": a_fp, "B": b_fp},
        "checks": [asdict(check) for check in checks],
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    lines = [
        "# Tenant A/B Isolation Evidence",
        "",
        f"- status: `{status}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- suffix: `{suffix}`",
        "",
        "| Layer | Check | Status | Evidence |",
        "|---|---|---|---|",
    ]
    for check in checks:
        evidence = check.evidence.replace("|", "\\|")
        lines.append(f"| {check.layer} | {check.name} | {check.status} | {evidence} |")
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("OMEGA_TENANT_AB_JSON=" + json.dumps(summary, sort_keys=True, default=str))
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir)}, indent=2))
    return 0 if status == PASS else 1


def _remote_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
set +x
REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"
cd "$DEPLOY_DIR"
set -a
# shellcheck disable=SC1091
source .env
set +a
network="$(docker network ls --format '{{.Name}}' | grep 'modecissions_net$' | head -n 1 || true)"
if [ -z "$network" ]; then
  echo "OMEGA_CHECK	compose network exists	FAIL	no docker network ending in modecissions_net"
  exit 21
fi
docker run --rm \
  --network "$network" \
  -v "$REPO_DIR:/work:ro" \
  -w /work \
  -e POSTGRES_PASSWORD \
  -e OMEGA_CONSOLE_PASSWORD \
  -e OMEGA_GOLD_READER_PASSWORD \
  -e OMEGA_REFINEMENT_GOLD_PASSWORD \
  -e JWT_SECRET_KEY \
  -e JWT_ALGORITHM \
  -e DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/modecissions" \
  -e GOLD_DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@postgres_gold:5433/modecissions_gold" \
  -e OMEGA_SEED_DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/modecissions" \
  -e OMEGA_SEED_GOLD_DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@postgres_gold:5433/modecissions_gold" \
  -e CONSOLE_URL=http://console:8000 \
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  python:3.12-slim \
  bash -lc 'python -m pip install -q psycopg2-binary && PYTHONPATH=/work/scripts:/work/console:/work python scripts/tenant_ab_e2e.py --target local --evidence-dir /tmp/omega-tenant-ab'
"""


def _run_aws(args: argparse.Namespace) -> int:
    evidence_dir = args.evidence_dir or DEFAULT_AWS_EVIDENCE_ROOT / utc_stamp()
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=_remote_script(),
        comment="omega-tenant-ab-e2e",
        timeout_seconds=args.timeout_seconds,
    )
    payload: dict[str, Any] | None = None
    checks: list[Check] = [
        Check(
            "ssm",
            "SSM command completed",
            PASS if remote.status == "Success" else FAIL,
            f"command_id={remote.command_id} status={remote.status} response_code={remote.response_code}",
        )
    ]
    for line in remote.stdout.splitlines():
        if line.startswith("OMEGA_TENANT_AB_JSON="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                payload = None
        elif line.startswith("OMEGA_CHECK\t"):
            _prefix, name, status, evidence = (line.split("\t", 3) + [""])[:4]
            checks.append(Check("remote", name, status, _short(evidence)))
    if payload:
        checks.extend(Check(**item) for item in payload.get("checks", []))
    status = FAIL if any(check.status == FAIL for check in checks) else BLOCKED if any(check.status == BLOCKED for check in checks) else PASS
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "remote_summary": payload,
        "checks": [asdict(check) for check in checks],
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(redact(remote.stdout), encoding="utf-8")
    (evidence_dir / "remote_stderr_redacted.txt").write_text(redact(remote.stderr), encoding="utf-8")
    lines = [
        "# AWS Tenant A/B Isolation Evidence",
        "",
        f"- status: `{status}`",
        f"- ssm_command_id: `{remote.command_id}`",
        f"- instance_id: `{instance_id}`",
        f"- region: `{args.region}`",
        "",
        "| Layer | Check | Status | Evidence |",
        "|---|---|---|---|",
    ]
    for check in checks:
        evidence = check.evidence.replace("|", "\\|")
        lines.append(f"| {check.layer} | {check.name} | {check.status} | {evidence} |")
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir), "ssm_command_id": remote.command_id}, indent=2))
    return 0 if status == PASS else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run tenant A/B isolation checks locally or on AWS.")
    parser.add_argument("--target", choices={"local", "aws"}, default="local")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or "")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("OMEGA_TENANT_AB_TIMEOUT_SECONDS", "1200")))
    parser.add_argument("--suffix", default=os.environ.get("OMEGA_TENANT_AB_SUFFIX") or "")
    args = parser.parse_args(argv)
    if args.target == "aws":
        return _run_aws(args)
    return _run_local(args.evidence_dir or DEFAULT_LOCAL_EVIDENCE_ROOT / utc_stamp(), suffix=args.suffix or None)


if __name__ == "__main__":
    raise SystemExit(main())
