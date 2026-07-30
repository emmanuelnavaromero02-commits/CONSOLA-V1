from __future__ import annotations

import base64
import hashlib
import io
import os
import uuid

import bcrypt
import psycopg
from minio import Minio


PASSWORD = "E2E-Only-Password-47a54133!"


def _password_hash(password: str) -> str:
    digest = hashlib.sha256(password.encode()).digest()
    prepared = base64.urlsafe_b64encode(digest)
    return bcrypt.hashpw(prepared, bcrypt.gensalt(rounds=12)).decode()


def _dataset_sql(scope: dict[str, str]) -> tuple[str, str]:
    marker = f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}"
    silver = f"""
        SELECT proyecto, project_name, mes, margen_bruto_usd,
               margen_bruto_pct, wip_usd, revenue_usd, revenue_manager,
               tenant_id, workspace_id
          FROM read_parquet(
            's3://lakehouse/raw/replicon/OperationalTruthProbe/{marker}/**/*.parquet',
            hive_partitioning=true, union_by_name=true
          )
    """
    gold = f"""
        SELECT proyecto, project_name, mes, margen_bruto_usd,
               margen_bruto_pct, wip_usd, revenue_usd, revenue_manager,
               tenant_id, workspace_id
          FROM read_parquet(
            's3://lakehouse/silver/replicon/operational_truth_silver/{marker}/**/*.parquet',
            hive_partitioning=true, union_by_name=true
          )
    """
    return silver, gold


def _seed_scope(conn: psycopg.Connection, label: str) -> dict[str, str]:
    tenant_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    email = f"operational-truth-{label}-{uuid.uuid4().hex[:8]}@invalid.local"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants(id,name,slug,status) VALUES(%s,%s,%s,'active')",
            (
                tenant_id,
                f"Operational Truth {label}",
                f"operational-truth-{label}-{tenant_id[:8]}",
            ),
        )
        cur.execute(
            "INSERT INTO workspaces(id,tenant_id,name) VALUES(%s,%s,%s)",
            (workspace_id, tenant_id, f"Operational Truth {label}"),
        )
        cur.execute(
            """
            INSERT INTO users(email,name,password_hash,role,is_active,must_change_password,tenant_id)
            VALUES(%s,%s,%s,'admin',TRUE,FALSE,%s) RETURNING id
            """,
            (email, f"E2E {label}", _password_hash(PASSWORD), tenant_id),
        )
        user_id = int(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO user_workspace_roles(user_id,workspace_id,role_id)
            SELECT %s,%s,id FROM roles WHERE name='workspace_admin'
            """,
            (user_id, workspace_id),
        )
        cur.execute(
            """
            INSERT INTO cartridge_installations(
                id,tenant_id,workspace_id,cartridge_id,product_id,status,
                current_step,install_fingerprint,created_by_id,ready_at
            ) VALUES(%s,%s,%s,'replicon','replicon','ready','activated',%s,%s,NOW())
            """,
            (
                f"operational-truth-{workspace_id}",
                tenant_id,
                workspace_id,
                f"operational-truth:{workspace_id}",
                user_id,
            ),
        )
    return {
        "label": label,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "email": email,
        "user_id": str(user_id),
    }


def _seed_datasets(conn: psycopg.Connection, scope: dict[str, str]) -> None:
    silver_sql, gold_sql = _dataset_sql(scope)
    rows = [
        (
            "operational_truth_silver",
            "silver",
            '["raw/replicon/OperationalTruthProbe"]',
            silver_sql,
        ),
        (
            "pnl_mensual",
            "gold",
            '["silver/replicon/operational_truth_silver"]',
            gold_sql,
        ),
    ]
    with conn.cursor() as cur:
        for name, layer, sources, sql_def in rows:
            cur.execute(
                """
                INSERT INTO datasets(
                    name,description,layer,cartridge,sources,sql_def,column_mapping,
                    workspace_id,tenant_id,scope_status
                ) VALUES(%s,'Operational truth E2E',%s,'replicon',%s::jsonb,%s,
                         '{}'::jsonb,%s,%s,'scoped')
                """,
                (
                    name,
                    layer,
                    sources,
                    sql_def,
                    scope["workspace_id"],
                    scope["tenant_id"],
                ),
            )


def seed_two_workspaces() -> list[dict[str, str]]:
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn:
        scopes = [_seed_scope(conn, "a"), _seed_scope(conn, "b")]
        for scope in scopes:
            _seed_datasets(conn, scope)
        conn.commit()
    return scopes


def upload_csv(scope: dict[str, str]) -> str:
    project = f"project-{scope['label']}"
    manager = f"Manager {scope['label'].upper()}"
    csv = (
        "proyecto,project_name,mes,margen_bruto_usd,margen_bruto_pct,wip_usd,revenue_usd,revenue_manager\n"
        f"{project},Proyecto {scope['label'].upper()},2026-01-01,100,100,0,100,{manager}\n"
        f"{project},Proyecto {scope['label'].upper()},2026-02-01,100,100,0,100,{manager}\n"
        f"{project},Proyecto {scope['label'].upper()},2026-03-01,10,10,0,100,{manager}\n"
    ).encode()
    client = Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )
    if not client.bucket_exists("lakehouse"):
        client.make_bucket("lakehouse")
    key = (
        "uploads/replicon/"
        f"tenant_id={scope['tenant_id']}/workspace_id={scope['workspace_id']}/"
        "in/operational-truth.csv"
    )
    client.put_object(
        "lakehouse", key, io.BytesIO(csv), len(csv), content_type="text/csv"
    )
    return key
