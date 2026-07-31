from __future__ import annotations

import hashlib
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException
import asyncpg

from app.services.gold_publication_relation import resolve_published_gold_relation
from app.services.intelligence.utils import workspace_scope


def _scope(user: dict | None) -> tuple[str, str]:
    tenant, workspace = workspace_scope(user)
    if not tenant or not workspace:
        raise HTTPException(400, "Superset Gold requires tenant/workspace scope")
    return str(tenant), str(workspace)


def scoped_gold_database_name(user: dict | None) -> str:
    tenant, workspace = _scope(user)
    digest = hashlib.sha256(f"{tenant}:{workspace}".encode()).hexdigest()[:20]
    return f"modecissions_gold_{digest}"


def scoped_gold_sqlalchemy_uri(raw: str, user: dict | None) -> str:
    tenant, workspace = _scope(user)
    parsed = urlsplit(str(raw or "").strip())
    if parsed.scheme not in {"postgresql", "postgresql+psycopg2"} or not parsed.netloc:
        raise HTTPException(503, "Superset Gold connection is unavailable")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["options"] = f"-c app.tenant_id={tenant} -c app.workspace_id={workspace}"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


async def resolve_scoped_database_id(client, user: dict | None, requested_id) -> int:
    scoped_name = scoped_gold_database_name(user)
    databases = await client.list_databases()
    match = next((item for item in databases if item.get("name") == scoped_name), None)
    if requested_id is not None:
        if not match or str(match.get("id")) != str(requested_id):
            raise HTTPException(400, "database_id is not registered for this workspace")
        return int(match["id"])
    if not match:
        gold_uri = os.environ.get("SUPERSET_GOLD_SQLALCHEMY_URI", "").strip()
        if not gold_uri:
            raise HTTPException(503, "No Superset database connection registered")
        match = await client.create_database(
            scoped_name, scoped_gold_sqlalchemy_uri(gold_uri, user)
        )
    return int(match["id"])


async def resolve_superset_gold_relation(user: dict | None, dataset: str):
    tenant, workspace = _scope(user)
    dsn = (
        os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    ).replace("postgresql+psycopg2://", "postgresql://")
    if not dsn:
        raise HTTPException(503, "Superset Gold connection is unavailable")
    connection = await asyncpg.connect(dsn, command_timeout=10)
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            await connection.execute(
                "SELECT set_config('app.tenant_id',$1,true), "
                "set_config('app.workspace_id',$2,true)",
                tenant,
                workspace,
            )
            relation = await resolve_published_gold_relation(
                connection, tenant, workspace, dataset
            )
            if relation.schema == "public":
                return relation
            view = (
                "v_"
                + hashlib.md5(
                    f"{tenant}:{workspace}:{dataset}".encode(), usedforsecurity=False
                ).hexdigest()
            )
            return type(relation)(
                schema="omega_publication_views",
                table=view,
                run_id=relation.run_id,
                generation=relation.generation,
            )
    finally:
        await connection.close()
