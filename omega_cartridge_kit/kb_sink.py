from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

KB_SCHEMA = "knowledge_bits"
KB_POLICY = "kb_workspace_scope"
SCOPE_COLUMNS = ("tenant_id", "workspace_id")
_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SCOPE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SCOPE_PREDICATE = "omega_rls_workspace_text_matches(tenant_id::text, workspace_id::text)"


class KbSinkError(RuntimeError):
    pass


def _table_name(table: object) -> str:
    if not isinstance(table, str) or not _TABLE_RE.fullmatch(table):
        raise ValueError("knowledge bit table name must be a lowercase SQL identifier")
    return table


def _scope_value(value: object, name: str) -> str:
    text_value = str(value or "").strip()
    if not _SCOPE_VALUE_RE.fullmatch(text_value):
        raise PermissionError(f"{name} scope is required for knowledge bit tables")
    return text_value


def write_scoped_kb_table(
    engine: Any,
    *,
    table: str,
    df: Any,
    tenant_id: str,
    workspace_id: str,
) -> int:
    """Replace one tenant/workspace slice of knowledge_bits.<table> under FORCE RLS in one transaction."""
    name = _table_name(table)
    tenant = _scope_value(tenant_id, "tenant")
    workspace = _scope_value(workspace_id, "workspace")
    frame = df.copy()
    frame["tenant_id"] = tenant
    frame["workspace_id"] = workspace
    qualified = f'{KB_SCHEMA}."{name}"'
    with engine.begin() as conn:
        conn.execute(
            text(
                "SELECT set_config('app.tenant_id', :tenant, true), "
                "set_config('app.workspace_id', :workspace, true)"
            ),
            {"tenant": tenant, "workspace": workspace},
        )
        if conn.execute(text("SELECT to_regnamespace(:schema)"), {"schema": KB_SCHEMA}).scalar() is None:
            raise KbSinkError("knowledge_bits schema is not provisioned")
        existing = conn.execute(
            text(
                "SELECT rel.relkind, pg_get_userbyid(rel.relowner) = current_user "
                "FROM pg_class rel JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
                "WHERE ns.nspname = :schema AND rel.relname = :table"
            ),
            {"schema": KB_SCHEMA, "table": name},
        ).first()
        if existing is not None:
            relkind, owned = existing
            if relkind != "r":
                raise KbSinkError("knowledge bit target is not a plain table")
            if not owned:
                raise KbSinkError("knowledge bit table is owned by another role")
        else:
            frame.head(0).to_sql(name=name, con=conn, schema=KB_SCHEMA, if_exists="append", index=False)
        for column in SCOPE_COLUMNS:
            conn.execute(text(f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS {column} TEXT"))
        conn.execute(text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"REVOKE ALL ON TABLE {qualified} FROM PUBLIC"))
        conn.execute(text(f"DROP POLICY IF EXISTS {KB_POLICY} ON {qualified}"))
        conn.execute(
            text(
                f"CREATE POLICY {KB_POLICY} ON {qualified} "
                f"USING ({_SCOPE_PREDICATE}) WITH CHECK ({_SCOPE_PREDICATE})"
            )
        )
        conn.execute(
            text(
                f"DELETE FROM {qualified} "
                "WHERE tenant_id::text = :tenant AND workspace_id::text = :workspace"
            ),
            {"tenant": tenant, "workspace": workspace},
        )
        frame.to_sql(name=name, con=conn, schema=KB_SCHEMA, if_exists="append", index=False)
    return len(frame)


__all__ = ["KB_POLICY", "KB_SCHEMA", "KbSinkError", "write_scoped_kb_table"]
