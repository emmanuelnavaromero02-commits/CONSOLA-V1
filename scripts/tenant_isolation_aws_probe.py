#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import DEFAULT_REGION, REPO, redact, resolve_instance_id, send_ssm_script, utc_now, utc_stamp, write_json


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

postgres_password="$(env_value POSTGRES_PASSWORD)"
console_probe="$(docker compose $(compose_files) exec -T -e POSTGRES_PASSWORD="$postgres_password" console python - <<'PY' 2>&1 || true
import asyncio
import hashlib
import json
import os
import uuid
import urllib.error
import urllib.request
from datetime import timedelta


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


async def table_exists(conn, table):
    return bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"))


async def columns(conn, table):
    rows = await conn.fetch(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = $1
        """,
        table,
    )
    return {row["column_name"] for row in rows}


async def db_contract():
    from app.services import auth

    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT c.relname,
               c.relrowsecurity,
               c.relforcerowsecurity,
               COALESCE(bool_or(
                   lower(regexp_replace(COALESCE(pg_get_expr(p.polqual, p.polrelid), ''), '\\s+', '', 'g'))
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
        SELECT d.defaclobjtype, acl.privilege_type
          FROM pg_default_acl d
          CROSS JOIN LATERAL aclexplode(d.defaclacl) acl
         WHERE d.defaclnamespace = 'public'::regnamespace
           AND acl.grantee = 'omega_console'::regrole
        """
    )
    wide = [
        f"{r['defaclobjtype']}:{r['privilege_type']}"
        for r in defaults
        if r["defaclobjtype"] == "r"
        and r["privilege_type"] in {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}
    ]
    if wide:
        emit("default privileges no broad console DML", "FAIL", json.dumps(wide))
    else:
        emit("default privileges no broad console DML", "PASS", "omega_console default table DML not broad")
    return pool


async def set_scope(conn, scope):
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
        scope["tenant_id"],
        scope["workspace_id"],
    )


async def role_id(conn, role):
    value = await conn.fetchval("SELECT id FROM roles WHERE name = $1", role)
    if value:
        return int(value)
    return int(await conn.fetchval("INSERT INTO roles (name, description) VALUES ($1, $2) RETURNING id", role, f"20B probe {role}"))


async def insert_dynamic(conn, table, values, conflict_sql=""):
    table_cols = await columns(conn, table)
    chosen = [(key, value) for key, value in values.items() if key in table_cols]
    names = ", ".join(key for key, _value in chosen)
    placeholders = ", ".join(f"${idx}" for idx in range(1, len(chosen) + 1))
    sql = f"INSERT INTO {table} ({names}) VALUES ({placeholders}) {conflict_sql}"
    return await conn.fetchrow(sql, *[value for _key, value in chosen])


async def create_scope(conn, suffix, label):
    tenant_cols = await columns(conn, "tenants")
    tenant_values = {"name": f"tenant-isolation-20b-{suffix}-{label}"}
    if "slug" in tenant_cols:
        tenant_values["slug"] = f"tenant-isolation-20b-{suffix.lower()}-{label.lower()}"
    tenant = await insert_dynamic(conn, "tenants", tenant_values, "RETURNING id")
    tenant_id = str(tenant["id"])
    workspace = await conn.fetchrow(
        "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
        tenant_id,
        f"Tenant Isolation 20B {label} {suffix}",
    )
    workspace_id = str(workspace["id"])
    email = f"tenant-isolation-20b-{suffix}-{label.lower()}@example.invalid"
    user_values = {
        "email": email,
        "name": f"Tenant Isolation 20B {label}",
        "password_hash": "tenant-isolation-disabled-password",
        "role": "auditor",
        "tenant_id": tenant_id,
        "is_active": True,
        "must_change_password": False,
    }
    user = await insert_dynamic(conn, "users", user_values, "RETURNING id")
    user_id = int(user["id"])
    await conn.execute(
        "INSERT INTO user_workspace_roles (user_id, workspace_id, role_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        user_id,
        workspace_id,
        await role_id(conn, "viewer"),
    )
    session_material = uuid.uuid4().hex + uuid.uuid4().hex
    session_digest = hashlib.sha256(session_material.encode("utf-8")).hexdigest()
    await conn.fetchval(
        "SELECT omega_auth_create_session($1, $2, NOW() + INTERVAL '2 hours', $3)",
        session_digest,
        user_id,
        f"10.20.{1 if label == 'A' else 2}.20",
    )
    await conn.execute(
        "INSERT INTO login_attempts (email, ip, success, created_at) VALUES ($1, $2, TRUE, NOW())",
        email,
        f"10.20.{1 if label == 'A' else 2}.21",
    )
    return {
        "label": label,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "email": email,
        "dataset": f"tenant_isolation_20b_{suffix.lower()}_{label.lower()}",
        "draft_title": f"20B {label} draft {suffix}",
        "workflow_intent": f"20B {label} workflow {suffix}",
        "fact": f"20B {label} fact {suffix}",
        "pref": f"20B {label} pref {suffix}",
        "summary": f"20B {label} memory summary {suffix}",
    }


async def seed_entitlement(conn, scope, suffix):
    if not all([await table_exists(conn, "marketplace_products"), await table_exists(conn, "tenant_entitlements"), await table_exists(conn, "cartridge_installations")]):
        return
    product_id = await conn.fetchval("SELECT id FROM marketplace_products WHERE cartridge_id='replicon' AND status IN ('active','internal') LIMIT 1")
    if not product_id:
        return
    await conn.execute(
        """
        INSERT INTO tenant_entitlements (tenant_id, workspace_id, cartridge_id, product_id, status, activated_by_id)
        VALUES ($1, $2, 'replicon', $3, 'active', $4)
        ON CONFLICT (tenant_id, workspace_id, cartridge_id) DO UPDATE
          SET status='active', product_id=EXCLUDED.product_id, activated_by_id=EXCLUDED.activated_by_id
        """,
        scope["tenant_id"],
        scope["workspace_id"],
        product_id,
        scope["user_id"],
    )
    await conn.execute(
        """
        INSERT INTO cartridge_installations (
            id, tenant_id, workspace_id, cartridge_id, product_id, status,
            current_step, install_fingerprint, created_by_id, ready_at
        )
        VALUES ($1, $2, $3, 'replicon', $4, 'ready', 'tenant_isolation_probe', $5, $6, NOW())
        ON CONFLICT (workspace_id, cartridge_id) DO UPDATE
          SET status='ready', product_id=EXCLUDED.product_id, current_step='tenant_isolation_probe',
              install_fingerprint=EXCLUDED.install_fingerprint, ready_at=NOW(), updated_at=NOW()
        """,
        f"tenant_iso_20b_{suffix}_{scope['label'].lower()}",
        scope["tenant_id"],
        scope["workspace_id"],
        product_id,
        f"tenant-isolation-20b:{suffix}:{scope['label']}",
        scope["user_id"],
    )


async def seed_scoped_artifacts(pool, a, b, suffix):
    async with pool.acquire() as conn:
        for scope in (a, b):
            async with conn.transaction():
                await set_scope(conn, scope)
                await seed_entitlement(conn, scope, suffix)
                await insert_dynamic(
                    conn,
                    "datasets",
                    {
                        "name": scope["dataset"],
                        "description": f"20B {scope['label']} dataset",
                        "layer": "gold",
                        "cartridge": "replicon",
                        "sources": json.dumps([]),
                        "sql_def": "SELECT 1",
                        "column_mapping": json.dumps({}),
                        "workspace_id": scope["workspace_id"],
                        "tenant_id": scope["tenant_id"],
                        "created_by_id": scope["user_id"],
                    },
                    "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, workspace_id = COALESCE(EXCLUDED.workspace_id, datasets.workspace_id)",
                )
                await conn.execute(
                    """
                    INSERT INTO copilot_drafts (user_id, kind, title, body, tenant_id, workspace_id, scope_status)
                    VALUES ($1, 'note', $2, $3, $4, $5, 'scoped')
                    """,
                    scope["user_id"], scope["draft_title"], f"body {scope['label']}", scope["tenant_id"], scope["workspace_id"],
                )
                conversation_id = await conn.fetchval(
                    "INSERT INTO conversations (user_id, workspace_id, title) VALUES ($1, $2, $3) RETURNING id",
                    scope["user_id"], scope["workspace_id"], f"20B {scope['label']} conversation",
                )
                workflow_id = await conn.fetchval(
                    """
                    INSERT INTO workflow_runs (user_id, conversation_id, intent, tenant_id, workspace_id, scope_status)
                    VALUES ($1, $2, $3, $4, $5, 'scoped') RETURNING id
                    """,
                    scope["user_id"], conversation_id, scope["workflow_intent"], scope["tenant_id"], scope["workspace_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO workflow_steps (workflow_id, step_idx, description, tenant_id, workspace_id, scope_status)
                    VALUES ($1, 1, $2, $3, $4, 'scoped')
                    ON CONFLICT (workflow_id, step_idx) DO UPDATE SET description = EXCLUDED.description
                    """,
                    workflow_id, f"20B {scope['label']} workflow step", scope["tenant_id"], scope["workspace_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO user_facts (user_id, fact, tenant_id, workspace_id, scope_status)
                    VALUES ($1, $2, $3, $4, 'scoped')
                    ON CONFLICT DO NOTHING
                    """,
                    scope["user_id"], scope["fact"], scope["tenant_id"], scope["workspace_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO user_preferences (user_id, pref_key, pref_value, tenant_id, workspace_id, scope_status)
                    VALUES ($1, $2, $3, $4, $5, 'scoped')
                    ON CONFLICT DO NOTHING
                    """,
                    scope["user_id"], f"probe_{suffix}_{scope['label'].lower()}", scope["pref"], scope["tenant_id"], scope["workspace_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO conversation_memory_summary (conversation_id, summary, tenant_id, workspace_id, scope_status)
                    VALUES ($1, $2, $3, $4, 'scoped')
                    ON CONFLICT (conversation_id) DO UPDATE
                      SET summary=EXCLUDED.summary, tenant_id=EXCLUDED.tenant_id,
                          workspace_id=EXCLUDED.workspace_id, scope_status='scoped'
                    """,
                    conversation_id, scope["summary"], scope["tenant_id"], scope["workspace_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO data_catalog (dataset, layer, cartridge, column_name, data_type, description, tags, tenant_id, workspace_id, scope_status)
                    VALUES ($1, 'gold', 'replicon', 'probe_col', 'integer', $2, ARRAY['tenant-isolation-20b'], $3, $4, 'scoped')
                    ON CONFLICT (workspace_id, dataset, column_name) WHERE workspace_id IS NOT NULL
                    DO UPDATE SET description=EXCLUDED.description, tenant_id=EXCLUDED.tenant_id, scope_status='scoped'
                    """,
                    scope["dataset"], f"20B {scope['label']} catalog marker", scope["tenant_id"], scope["workspace_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO data_relationships (
                        from_dataset, from_column, to_dataset, to_column, join_hint, description,
                        tenant_id, workspace_id, scope_status
                    )
                    VALUES ($1, 'probe_col', $1, 'probe_col', 'SELF', $2, $3, $4, 'scoped')
                    ON CONFLICT (workspace_id, from_dataset, from_column, to_dataset, to_column) WHERE workspace_id IS NOT NULL
                    DO UPDATE SET description=EXCLUDED.description, tenant_id=EXCLUDED.tenant_id, scope_status='scoped'
                    """,
                    scope["dataset"], f"20B {scope['label']} relationship marker", scope["tenant_id"], scope["workspace_id"],
                )


async def seed_legacy_unscoped(a, suffix):
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not password:
        return ""
    import asyncpg

    conn = await asyncpg.connect(user="postgres", password=password, database="modecissions", host="postgres", port=5432)
    legacy_title = f"20B legacy draft {suffix}"
    try:
        await conn.execute(
            """
            INSERT INTO copilot_drafts (user_id, kind, title, body, tenant_id, workspace_id, scope_status)
            VALUES ($1, 'note', $2, 'legacy body', NULL, NULL, 'legacy_unscoped')
            """,
            a["user_id"], legacy_title,
        )
    finally:
        await conn.close()
    return legacy_title


async def count_marker(conn, sql, *args):
    return int(await conn.fetchval(sql, *args) or 0)


async def scoped_db_contract(pool, a, b, legacy_title):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await set_scope(conn, a)
            own_drafts = await count_marker(conn, "SELECT COUNT(*) FROM copilot_drafts WHERE title=$1", a["draft_title"])
            cross_drafts = await count_marker(conn, "SELECT COUNT(*) FROM copilot_drafts WHERE title=$1", b["draft_title"])
            own_workflows = await count_marker(conn, "SELECT COUNT(*) FROM workflow_runs WHERE intent=$1", a["workflow_intent"])
            cross_workflows = await count_marker(conn, "SELECT COUNT(*) FROM workflow_runs WHERE intent=$1", b["workflow_intent"])
            own_memory = await count_marker(conn, "SELECT COUNT(*) FROM user_facts WHERE fact=$1", a["fact"])
            cross_memory = await count_marker(conn, "SELECT COUNT(*) FROM user_facts WHERE fact=$1", b["fact"])
            own_catalog = await count_marker(conn, "SELECT COUNT(*) FROM data_catalog WHERE dataset=$1", a["dataset"])
            cross_catalog = await count_marker(conn, "SELECT COUNT(*) FROM data_catalog WHERE dataset=$1", b["dataset"])
            tenant_legacy = await count_marker(conn, "SELECT COUNT(*) FROM copilot_drafts WHERE title=$1", legacy_title) if legacy_title else 0
        platform_legacy = await count_marker(conn, "SELECT COUNT(*) FROM copilot_drafts WHERE title=$1", legacy_title) if legacy_title else 0

    copilot_ok = own_drafts > 0 and cross_drafts == 0 and own_memory > 0 and cross_memory == 0
    emit("Copilot tables tenant isolated", "PASS" if copilot_ok else "FAIL", json.dumps({
        "own_drafts": own_drafts, "cross_drafts": cross_drafts,
        "own_memory": own_memory, "cross_memory": cross_memory,
    }))
    workflow_ok = own_workflows > 0 and cross_workflows == 0
    emit("Workflow tables tenant isolated", "PASS" if workflow_ok else "FAIL", json.dumps({
        "own_workflows": own_workflows, "cross_workflows": cross_workflows,
    }))
    catalog_ok = own_catalog > 0 and cross_catalog == 0
    emit("Catalog tables tenant isolated", "PASS" if catalog_ok else "FAIL", json.dumps({
        "own_catalog": own_catalog, "cross_catalog": cross_catalog,
    }))
    legacy_ok = (not legacy_title) or (tenant_legacy == 0 and platform_legacy > 0)
    emit("legacy_unscoped tenant hidden platform audit visible", "PASS" if legacy_ok else "FAIL", json.dumps({
        "tenant_legacy": tenant_legacy, "platform_legacy": platform_legacy,
    }))


def http_json(path, token, workspace_id):
    req = urllib.request.Request(
        "http://console:8000" + path,
        headers={
            "Authorization": f"Bearer {token}",
            "x-workspace-id": workspace_id,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} returned {exc.code}: {body[:400]}") from exc


def endpoint_contract(a, b):
    from app.services.jwt_auth import create_access_token

    token_a = create_access_token({"sub": a["user_id"], "email": a["email"], "role": "auditor"}, expires_delta=timedelta(minutes=15))
    token_b = create_access_token({"sub": b["user_id"], "email": b["email"], "role": "auditor"}, expires_delta=timedelta(minutes=15))
    try:
        dashboard_a = http_json("/api/dashboard/kpis", token_a, a["workspace_id"])
        sessions_a = http_json("/security/sessions", token_a, a["workspace_id"])
        attempts_a = http_json("/security/login-attempts", token_a, a["workspace_id"])
        catalog_a = http_json(f"/api/catalog?cartridge=replicon&datasets={a['dataset']}", token_a, a["workspace_id"])
        catalog_cross = http_json(f"/api/catalog?cartridge=replicon&datasets={b['dataset']}", token_a, a["workspace_id"])
        dashboard_b = http_json("/api/dashboard/kpis", token_b, b["workspace_id"])
    except Exception as exc:
        emit("endpoint tenant A/B probes", "FAIL", str(exc))
        return
    dashboard_ok = dashboard_a.get("users", {}).get("total") == 1 and dashboard_b.get("users", {}).get("total") == 1
    emit("Dashboard endpoint scoped KPIs", "PASS" if dashboard_ok else "FAIL", json.dumps({
        "tenant_a_users": dashboard_a.get("users"),
        "tenant_b_users": dashboard_b.get("users"),
    }))
    sessions_blob = json.dumps(sessions_a, sort_keys=True, default=str)
    sessions_ok = a["email"] in sessions_blob and b["email"] not in sessions_blob
    emit("Security sessions endpoint scoped", "PASS" if sessions_ok else "FAIL", json.dumps({
        "contains_a": a["email"] in sessions_blob,
        "contains_b": b["email"] in sessions_blob,
    }))
    attempts_blob = json.dumps(attempts_a, sort_keys=True, default=str)
    attempts_ok = a["email"] in attempts_blob and b["email"] not in attempts_blob
    emit("Security login attempts endpoint scoped", "PASS" if attempts_ok else "FAIL", json.dumps({
        "contains_a": a["email"] in attempts_blob,
        "contains_b": b["email"] in attempts_blob,
    }))
    catalog_blob = json.dumps(catalog_a, sort_keys=True, default=str)
    catalog_cross_blob = json.dumps(catalog_cross, sort_keys=True, default=str)
    catalog_ok = a["dataset"] in catalog_blob and b["dataset"] not in catalog_blob and b["dataset"] not in catalog_cross_blob
    emit("Catalog endpoint scoped", "PASS" if catalog_ok else "FAIL", json.dumps({
        "contains_a_dataset": a["dataset"] in catalog_blob,
        "contains_b_dataset": b["dataset"] in catalog_blob or b["dataset"] in catalog_cross_blob,
    }))


async def main_probe():
    pool = await db_contract()
    suffix = uuid.uuid4().hex[:10]
    async with pool.acquire() as conn:
        async with conn.transaction():
            a = await create_scope(conn, suffix, "A")
            b = await create_scope(conn, suffix, "B")
    await seed_scoped_artifacts(pool, a, b, suffix)
    legacy_title = await seed_legacy_unscoped(a, suffix)
    await scoped_db_contract(pool, a, b, legacy_title)
    endpoint_contract(a, b)


asyncio.run(main_probe())
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

    instance_id = args.instance_id or resolve_instance_id(args.region)
    command = send_ssm_script(
        instance_id=instance_id,
        region=args.region,
        script=_remote_script(),
        comment="omega-tenant-isolation-aws-probe",
        timeout_seconds=420,
    )
    output = command.stdout + "\n" + command.stderr
    checks = _parse_checks(output)
    status = PASS if checks and all(check.status == PASS for check in checks) else FAIL
    summary = {
        "probe": "tenant-isolation-aws-probe",
        "status": status,
        "generated_at": utc_now().isoformat(),
        "region": args.region,
        "instance_id": instance_id,
        "ssm_command_id": command.command_id,
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
