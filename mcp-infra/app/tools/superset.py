"""
Superset MCP tools — wraps Superset REST API v1.
Handles database connections, datasets, charts and dashboards.
"""
from __future__ import annotations

import json

import httpx

from app.config import settings
from app.registry import tool

_BASE = settings.superset_url.rstrip("/")


# ── Auth ───────────────────────────────────────────────────────────────────────

async def _token() -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_BASE}/api/v1/security/login",
            json={
                "username": settings.superset_user,
                "password": settings.superset_password,
                "provider": "db",
                "refresh":  True,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def _hdrs() -> dict:
    tok = await _token()
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ── Database connections ───────────────────────────────────────────────────────

@tool(
    name="superset_list_databases",
    description="List database connections registered in Superset.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def superset_list_databases() -> dict:
    h = await _hdrs()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{_BASE}/api/v1/database/", headers=h)
        r.raise_for_status()
        dbs = r.json().get("result", [])
    return {
        "databases": [
            {"id": d["id"], "name": d["database_name"], "backend": d.get("backend")}
            for d in dbs
        ]
    }


@tool(
    name="superset_create_database",
    description=(
        "Register a database connection in Superset. "
        "Use this when setting up a new cartridge's Gold tables."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name":         {"type": "string", "description": "Display name"},
            "sqlalchemy_uri": {"type": "string", "description": "SQLAlchemy connection URI"},
        },
        "required": ["name", "sqlalchemy_uri"],
    },
)
async def superset_create_database(name: str, sqlalchemy_uri: str) -> dict:
    h = await _hdrs()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_BASE}/api/v1/database/",
            headers=h,
            json={"database_name": name, "sqlalchemy_uri": sqlalchemy_uri},
        )
        r.raise_for_status()
        data = r.json()
    return {"database_id": data["id"], "name": name}


# ── Datasets ───────────────────────────────────────────────────────────────────

@tool(
    name="superset_list_datasets",
    description="List datasets (virtual tables) registered in Superset.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def superset_list_datasets() -> dict:
    h = await _hdrs()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{_BASE}/api/v1/dataset/", headers=h)
        r.raise_for_status()
        ds = r.json().get("result", [])
    return {
        "datasets": [
            {"id": d["id"], "name": d["table_name"], "schema": d.get("schema")}
            for d in ds
        ]
    }


@tool(
    name="superset_create_dataset",
    description="Create a Superset dataset from a Gold PostgreSQL table.",
    input_schema={
        "type": "object",
        "properties": {
            "database_id": {"type": "integer", "description": "Superset DB connection ID"},
            "table_name":  {"type": "string"},
            "schema":      {"type": "string", "description": "Schema (default: public)"},
        },
        "required": ["database_id", "table_name"],
    },
)
async def superset_create_dataset(
    database_id: int, table_name: str, schema: str = "public"
) -> dict:
    h = await _hdrs()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_BASE}/api/v1/dataset/",
            headers=h,
            json={"database": database_id, "schema": schema, "table_name": table_name},
        )
        r.raise_for_status()
        data = r.json()
    return {"dataset_id": data["id"], "table": table_name, "schema": schema}


# ── Charts ─────────────────────────────────────────────────────────────────────

@tool(
    name="superset_list_charts",
    description="List charts (slices) in Superset.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def superset_list_charts() -> dict:
    h = await _hdrs()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{_BASE}/api/v1/chart/", headers=h)
        r.raise_for_status()
        charts = r.json().get("result", [])
    return {
        "charts": [
            {"id": d["id"], "name": d["slice_name"], "viz_type": d.get("viz_type")}
            for d in charts
        ]
    }


@tool(
    name="superset_create_chart",
    description=(
        "Create a chart in Superset. "
        "viz_type options: bar, line, pie, table, big_number, big_number_total, "
        "dist_bar, area, scatter, histogram."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name":           {"type": "string", "description": "Chart title"},
            "viz_type":       {"type": "string", "description": "Visualization type"},
            "datasource_id":  {"type": "integer", "description": "Dataset ID"},
            "datasource_type":{"type": "string", "default": "table"},
            "params":         {"type": "object", "description": "Chart query/display params"},
        },
        "required": ["name", "viz_type", "datasource_id"],
    },
)
async def superset_create_chart(
    name: str,
    viz_type: str,
    datasource_id: int,
    datasource_type: str = "table",
    params: dict | None = None,
) -> dict:
    h = await _hdrs()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_BASE}/api/v1/chart/",
            headers=h,
            json={
                "slice_name":     name,
                "viz_type":       viz_type,
                "datasource_id":  datasource_id,
                "datasource_type": datasource_type,
                "params":         json.dumps(params or {}),
            },
        )
        r.raise_for_status()
        data = r.json()
    return {
        "chart_id": data["id"],
        "name":     name,
        "url":      f"{_BASE}/chart/edit/{data['id']}",
    }


# ── Dashboards ─────────────────────────────────────────────────────────────────

@tool(
    name="superset_list_dashboards",
    description="List dashboards in Superset.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def superset_list_dashboards() -> dict:
    h = await _hdrs()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{_BASE}/api/v1/dashboard/", headers=h)
        r.raise_for_status()
        dashes = r.json().get("result", [])
    return {
        "dashboards": [
            {
                "id":    d["id"],
                "title": d["dashboard_title"],
                "url":   f"{_BASE}/superset/dashboard/{d['id']}/",
            }
            for d in dashes
        ]
    }


@tool(
    name="superset_create_dashboard",
    description="Create a new dashboard in Superset.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "slug":  {"type": "string", "description": "URL-friendly identifier (optional)"},
        },
        "required": ["title"],
    },
)
async def superset_create_dashboard(title: str, slug: str | None = None) -> dict:
    h       = await _hdrs()
    payload: dict = {"dashboard_title": title, "published": True}
    if slug:
        payload["slug"] = slug
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_BASE}/api/v1/dashboard/", headers=h, json=payload)
        r.raise_for_status()
        data = r.json()
    dash_id = data["id"]
    return {
        "dashboard_id": dash_id,
        "title":        title,
        "url":          f"{_BASE}/superset/dashboard/{dash_id}/",
    }


@tool(
    name="superset_export_dashboard",
    description=(
        "Export a dashboard as a ZIP archive (Superset native format). "
        "Returns file contents as a dict keyed by filename — "
        "store in the cartridge's superset/ folder for portability."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "dashboard_id": {"type": "integer"},
        },
        "required": ["dashboard_id"],
    },
)
async def superset_export_dashboard(dashboard_id: int) -> dict:
    import io as _io
    import zipfile

    h = {**(await _hdrs()), "Accept": "application/zip"}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(
            f"{_BASE}/api/v1/dashboard/export/",
            headers=h,
            params={"q": json.dumps([dashboard_id])},
        )
        r.raise_for_status()
    with zipfile.ZipFile(_io.BytesIO(r.content)) as z:
        files = {name: z.read(name).decode("utf-8", errors="replace") for name in z.namelist()}
    return {"dashboard_id": dashboard_id, "files": files}


@tool(
    name="superset_import_dashboard",
    description=(
        "Import a dashboard from a previously exported ZIP dict "
        "(as returned by superset_export_dashboard)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "files":     {"type": "object", "description": "Dict of {filename: content}"},
            "passwords": {"type": "object", "description": "Optional DB password overrides"},
        },
        "required": ["files"],
    },
)
async def superset_import_dashboard(
    files: dict, passwords: dict | None = None
) -> dict:
    import io as _io
    import zipfile

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    buf.seek(0)

    tok = await _token()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{_BASE}/api/v1/dashboard/import/",
            headers={"Authorization": f"Bearer {tok}"},
            files={"formData": ("dashboard.zip", buf, "application/zip")},
            data={"passwords": json.dumps(passwords or {})},
        )
        r.raise_for_status()
    return {"imported": True, "status": r.status_code}
