from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

# Import the current console runtime namespace, including private helper
# functions used by legacy handlers. Handlers are rebound to app.main's
# namespace before registration so existing tests and monkeypatches that
# patch app.main.<helper> continue to affect the handler at runtime.
globals().update(_console_main.__dict__)
router = APIRouter()


def _bind_to_main(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _console_main.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _console_main.__name__
    _console_main.__dict__[fn.__name__] = rebound
    return rebound

# /datasets
@router.get("/datasets", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def list_datasets(user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("list_datasets", {}, user=user)

# /datasets/{name}/schema
@router.get("/datasets/{name}/schema", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def dataset_schema(name: str, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("get_schema", {"name": name}, user=user)

# /api/datasets
@router.get("/api/datasets", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_list_datasets_alias(user: dict = Depends(require_authenticated)):
    return await list_datasets(user)

# /api/datasets/{name}/schema
@router.get("/api/datasets/{name}/schema", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_dataset_schema_alias(name: str, user: dict = Depends(require_authenticated)):
    return await dataset_schema(name, user)

# /datasets/{name}/data
@router.get("/datasets/{name}/data", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def dataset_data(name: str, request: Request, limit: int = 100):
    # Forward user context so refinement can apply RLS. Without it the GOLD
    # tables fall through to the empty-tenant filter (or the revenue_manager
    # 'N/D' fallback) and any authenticated user could read cross-tenant rows.
    user = getattr(request.state, "user", None) or {}
    return await _refinement_invoke(
        "query_dataset",
        {"name": name, "limit": limit, "user_context": _rls_user_context(user)},
        timeout=30,
        user=user,
    )

# /datasets/{name}/refresh
@router.post("/datasets/{name}/refresh", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))])
@_bind_to_main
async def refresh_dataset(name: str, user: dict = Depends(require_permission("datasets.write"))):
    return await _refinement_invoke("materialize", {"name": name}, timeout=120, user=user)

# /api/schema
@router.get("/api/schema", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_schema(source: str, user: dict = Depends(require_authenticated)):
    partitions = await _refinement_invoke(
        "get_source_partitions",
        {"source": source},
        timeout=30,
        user=user,
    )
    preview = await _refinement_invoke(
        "preview_source",
        {"source": source, "limit": 5},
        timeout=30,
        user=user,
    )
    return {"partitions": partitions, "preview": preview}

# /api/sources
@router.get("/api/sources", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_sources(user: dict = Depends(require_authenticated)):
    data = await _refinement_invoke("list_sources", {}, timeout=60, user=user)
    # Normalize: result may be {"result": [...]} or {"sources": [...]}
    sources = data.get("result") or data.get("sources") or []
    if isinstance(sources, list):
        return {"sources": sources}
    return {"sources": []}

# /api/datasets/save
@router.post("/api/datasets/save", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))])
@_bind_to_main
async def api_dataset_save(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("save_dataset", body, user))
        r.raise_for_status()
    return r.json()

# /api/datasets/{name}/detail
@router.get("/api/datasets/{name}/detail", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_dataset_detail(name: str, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("get_dataset_definition", {"name": name}, user=user)

# /api/bronze/query
@router.post("/api/bronze/query", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def api_bronze_query(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    # Restricted to datasets.write because this endpoint accepts arbitrary SQL.
    # Read-only roles (viewer) must use the dataset-scoped endpoints below,
    # which build SQL server-side instead of trusting client input.
    sql     = body.get("sql", "").strip()
    limit   = min(int(body.get("limit", 200)), 2000)
    sources = body.get("sources") or []
    if not sql:
        raise HTTPException(400, "sql is required")
    sql = _rewrite_bronze_logical_paths(sql, user)
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=120) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "preview_transform",
                {
                    "sql": sql,
                    "limit": limit,
                    "sources": sources,
                    "user_context": _rls_user_context(user),
                },
                user,
            ),
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _upstream_error_detail(r, "Refinement query failed"))
    return r.json()

# /api/datasets
@router.delete("/api/datasets", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.delete"))])
@_bind_to_main
async def api_delete_dataset(name: str, user: dict = Depends(require_permission("datasets.delete"))):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("delete_dataset", {"name": name}, user))
    if r.status_code == 404:
        raise HTTPException(404, f"Dataset '{name}' not found")
    return r.json()

# /api/datasets/{name}/lineage
@router.get("/api/datasets/{name}/lineage", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_dataset_lineage(name: str, user: dict = Depends(require_authenticated)):
    try:
        async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
            r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                             json=_mcp_payload("get_lineage", {"name": name, "limit": 20}, user))
    except httpx.TimeoutException:
        return {"name": name, "lineage": [], "degraded": True, "error": "lineage_timeout"}
    if r.status_code >= 500:
        return {"name": name, "lineage": [], "degraded": True, "error": "lineage_unavailable"}
    return r.json()

# /api/explorer/buckets
@router.get("/api/explorer/buckets", dependencies=[Depends(require_permission("pipelines.read"))])
@_bind_to_main
async def api_explorer_buckets(user: dict = Depends(require_authenticated)):
    ctx = build_security_context(user)
    buckets = _EXPLORER_DEFAULT_BUCKETS if _is_security_admin_context(ctx) else [
        item for item in _EXPLORER_DEFAULT_BUCKETS if item.get("id") == "lakehouse"
    ]
    quicklinks = [
        item for item in _EXPLORER_QUICKLINKS
        if _explorer_path_allowed(item.get("prefix", ""), user)
        and (_is_security_admin_context(ctx) or item.get("bucket") == "lakehouse")
    ]
    return {"buckets": buckets, "quicklinks": quicklinks}

# /api/explorer/list
@router.get("/api/explorer/list", dependencies=[Depends(require_permission("pipelines.read"))])
@_bind_to_main
async def api_explorer_list(
    bucket: str,
    prefix: str = "",
    max_keys: int = 200,
    continuation_token: str | None = None,
    user: dict = Depends(require_authenticated),
):
    s3 = _s3_client()
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(prefix, user):
        raise HTTPException(403, "prefix not allowed")
    kwargs = {
        "Bucket": bucket_name,
        "Prefix": prefix,
        "MaxKeys": min(max(max_keys, 1), 1000),
        "Delimiter": "/",
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token
    try:
        resp = await asyncio.to_thread(s3.list_objects_v2, **kwargs)
    except Exception as exc:
        raise HTTPException(502, "object storage list failed") from exc
    objects = [
        {"key": o["Key"], "size": o["Size"], "last_modified": o["LastModified"].isoformat()}
        for o in resp.get("Contents", [])
        if o.get("Key") != prefix
    ]
    folders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    return {
        "bucket": bucket_name,
        "prefix": prefix,
        "folders": folders,
        "objects": objects,
        "next_token": resp.get("NextContinuationToken"),
        "is_truncated": bool(resp.get("IsTruncated", False)),
    }

# /api/explorer/download
@router.get("/api/explorer/download", dependencies=[Depends(require_permission("pipelines.read"))])
@_bind_to_main
async def api_explorer_download(
    request: Request,
    bucket: str = Query(...),
    key: str = Query(...),
    expires: int = 300,
    user: dict = Depends(require_authenticated),
):
    s3 = _s3_client()
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(key, user):
        raise HTTPException(403, "object not allowed")
    expires_in = min(max(int(expires), 60), 3600)
    try:
        url = await asyncio.to_thread(
            s3.generate_presigned_url,
            "get_object",
            Params={"Bucket": bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception as exc:
        raise HTTPException(502, "object storage download failed") from exc
    await _audit.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="explorer.object.download",
        resource_type="object",
        resource_id=f"{bucket_name}/{key}",
        ip=request.client.host if request and request.client else None,
        status="success",
        metadata={"expires_in": expires_in},
    )
    return {"url": url, "expires_in": expires_in}

# /api/explorer/object
@router.delete(
    "/api/explorer/object",
    dependencies=[Depends(require_csrf), Depends(require_permission("pipelines.write"))],
)
@_bind_to_main
async def api_explorer_delete(
    bucket: str,
    key: str,
    request: Request,
    confirm: str = Query(...),
    user: dict = Depends(require_authenticated),
):
    s3 = _s3_client()
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(key, user):
        raise HTTPException(403, "object not allowed")
    if confirm != key:
        raise HTTPException(400, "strong confirmation required")
    try:
        await asyncio.to_thread(s3.delete_object, Bucket=bucket_name, Key=key)
    except Exception as exc:
        raise HTTPException(502, "object storage delete failed") from exc
    await _audit.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="explorer.object.delete",
        resource_type="object",
        resource_id=f"{bucket_name}/{key}",
        ip=request.client.host if request.client else None,
        status="success",
    )
    return {"deleted": True, "bucket": bucket_name, "key": key}

# /api/lineage
@router.get("/api/lineage", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_lineage(cartridge: str | None = None, user: dict = Depends(require_permission("datasets.read"))):
    """Global lineage graph across raw sources and silver/gold datasets."""
    payload = await _refinement_invoke("list_datasets", {}, timeout=15, user=user)
    datasets = (payload or {}).get("datasets") or []
    if cartridge:
        datasets = [d for d in datasets if d.get("cartridge") == cartridge]

    by_name = {d["name"]: d for d in datasets if d.get("name")}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for d in datasets:
        name = d.get("name")
        if not name:
            continue
        nid = f"ds:{name}"
        nodes[nid] = {
            "id": nid,
            "label": name,
            "type": d.get("layer", "silver"),
            "cartridge": d.get("cartridge", ""),
            "is_stale": bool(d.get("is_stale")),
            "staleness_reason": d.get("staleness_reason"),
            "row_count": d.get("row_count"),
            "last_refresh": d.get("last_refresh"),
        }
        for src in (d.get("sources") or []):
            source = (src or "").strip()
            source_lower = source.lower()
            if source_lower.startswith("raw/"):
                rid = f"raw:{source[4:]}"
                if rid not in nodes:
                    parts = source[4:].split("/", 1)
                    nodes[rid] = {
                        "id": rid,
                        "label": parts[-1] if parts else source,
                        "type": "raw",
                        "cartridge": parts[0] if len(parts) > 1 else "",
                    }
                edges.append({"from": rid, "to": nid})
                continue
            candidates = [source, source.replace("silver_", "", 1), source.replace("gold_", "", 1)]
            if "/" in source:
                candidates.append(source.rsplit("/", 1)[-1])
            matched = next((candidate for candidate in candidates if candidate in by_name), None)
            if matched:
                edges.append({"from": f"ds:{matched}", "to": nid})

    return {"nodes": list(nodes.values()), "edges": edges}

# /api/data/{dataset}
@router.get("/api/data/{dataset}", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_data(
    dataset: str,
    request: Request,
    limit: int = 5000,
    user: dict = Depends(require_authenticated),
):
    """Return dataset rows as JSON array for use by analytic apps."""
    _validate_dataset_name(dataset)

    # Prefer already-materialized, workspace-scoped Gold tables. This keeps
    # production reads on the same path as the intelligence readiness gate and
    # avoids failing analytic views when the legacy S3 parquet dependency is
    # unavailable but the scoped Gold table is present.
    try:
        from app.services.intelligence.gold_fetcher import query_gold_dataset_rows

        return await query_gold_dataset_rows(dataset, user, limit)
    except HTTPException as exc:
        if exc.status_code not in {404, 503}:
            raise
    except Exception:
        pass

    data = await _refinement_invoke(
        "query_dataset",
        {"name": dataset, "limit": limit, "user_context": _rls_user_context(user)},
        timeout=60,
        user=user,
    )
    return data.get("data", data)

# /api/data/{dataset}/options
@router.get("/api/data/{dataset}/options")
@_bind_to_main
async def api_data_options(dataset: str, columns: str = "", user: dict = Depends(require_authenticated)):
    """Return distinct values per column for building filter selectors."""
    _validate_dataset_name(dataset)
    cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else []
    if not cols:
        raise HTTPException(400, "columns param required, e.g. ?columns=revenue_manager,cliente")

    # Validate column names (alphanumeric + underscore only)
    import re as _re
    for col in cols:
        if not _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            raise HTTPException(400, f"Invalid column name: {col}")

    sqls = [f"SELECT DISTINCT {col} AS val, '{col}' AS col FROM pggold.gold_{dataset} WHERE {col} IS NOT NULL"
            for col in cols]
    union_sql = " UNION ALL ".join(sqls) + f" ORDER BY col, val"

    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload(
                             "preview_transform",
                             {
                                 "sql": union_sql,
                                 "limit": 5000,
                                 "user_context": _rls_user_context(user),
                             },
                             user,
                         ))
    result = r.json()
    rows = result.get("data", [])

    # Group by column name
    options: dict = {col: [] for col in cols}
    for row in rows:
        col_key = row.get("col")
        if col_key in options and row.get("val") is not None:
            options[col_key].append(str(row["val"]))
    return options

# /api/data/{dataset}/query
@router.post("/api/data/{dataset}/query", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_data_query_filtered(dataset: str, body: dict, request: Request):
    """
    Execute a filtered query against a gold dataset.
    Body: {"filters": {"revenue_manager": "X", "fiscal_year": 2025,
                        "cliente": "Y", "proyecto": "Z"},
           "limit": 1000, "columns": ["col1", "col2"]}
    fiscal_year uses March-February logic automatically.
    """
    _validate_dataset_name(dataset)
    import re as _re
    filters   = body.get("filters", {})
    limit     = min(int(body.get("limit", 2000)), 10000)
    columns   = body.get("columns", ["*"])

    # Forward the authenticated user's context so refinement can apply RLS.
    _user = getattr(request.state, "user", None) or {}
    _user_context = _rls_user_context(_user)

    # Validate column names
    safe_cols = []
    for col in columns:
        if col == "*" or _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            safe_cols.append(col)
    select_clause = ", ".join(safe_cols) if safe_cols else "*"

    if not isinstance(filters, dict):
        raise HTTPException(400, "filters must be an object")
    if len(filters) > 20:
        raise HTTPException(400, "Too many filters (max 20)")

    # Build a parameterized query — values go into `params`, never interpolated into SQL.
    # Column/table identifiers are allowlisted via regex; only values are parametrized.
    params: list = []

    def _add_param(v) -> str:
        """Append value to params list and return a DuckDB positional placeholder."""
        s = str(v)
        if len(s) > 500:
            raise HTTPException(400, "Filter value too long (max 500 chars)")
        params.append(s)
        return "?"

    conditions = []
    for key, val in filters.items():
        if val is None or val == "" or val == []:
            continue
        if key == "fiscal_year":
            # March-February fiscal year: month<=2 belongs to previous calendar year.
            # fiscal_year values must be integers — cast before adding to params.
            fy_expr = "(CASE WHEN EXTRACT(MONTH FROM mes)<=2 THEN EXTRACT(YEAR FROM mes)-1 ELSE EXTRACT(YEAR FROM mes) END)"
            vals = val if isinstance(val, list) else [val]
            if len(vals) > 50:
                raise HTTPException(400, "Too many fiscal_year values (max 50)")
            placeholders = ",".join(_add_param(int(v)) for v in vals)
            conditions.append(f"{fy_expr} IN ({placeholders})")
        elif _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
            vals = val if isinstance(val, list) else [val]
            if len(vals) > 100:
                raise HTTPException(400, f"Too many values for filter '{key}' (max 100)")
            if len(vals) == 1:
                conditions.append(f"{key} = {_add_param(vals[0])}")
            else:
                placeholders = ",".join(_add_param(v) for v in vals)
                conditions.append(f"{key} IN ({placeholders})")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT {select_clause} FROM pggold.gold_{dataset} {where} LIMIT {limit}"

    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=60) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload(
                             "preview_transform",
                             {"sql": sql, "params": params, "limit": limit, "user_context": _user_context},
                             _user,
                         ))
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Query failed")
    result = r.json()
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result.get("data", [])

# /explorer
@router.get("/explorer", dependencies=[Depends(require_permission("pipelines.read"))])
@_bind_to_main
async def explorer_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "explorer/index.html")

# /viewer/lineage
@router.get("/viewer/lineage", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def viewer_lineage(request: Request):
    return _viewer_redirect(request, "lineage")

# /api/semantic
@router.get("/api/semantic", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_semantic(cartridge: str = "replicon", user: dict = Depends(require_authenticated)):
    from app.services import cartridge_service as _cs
    _require_cartridge_visible(user, cartridge)
    manifest = await _cs.get_cartridge(cartridge)
    if manifest:
        # Pass through all entity fields so Studio can render display_name, dag_id, etc.
        entities = manifest.get("entities") or []
        return {"cartridge": cartridge, "server": manifest, "entities": entities}

    # Fallback: Pattern A — invoke via MCP server
    servers = await mcp_registry.list_servers()
    srv = next((s for s in servers if s["id"] == cartridge), None)
    if not srv:
        raise HTTPException(404, f"Cartridge '{cartridge}' not registered")
    entities = await mcp_registry.invoke(cartridge, "list_entities", {}, user=user)
    return {"cartridge": cartridge, "server": srv, "entities": entities}

# /api/catalog
@router.get("/api/catalog", dependencies=[Depends(require_authenticated)])
@_bind_to_main
async def api_catalog_get(
    layer: str = "",
    cartridge: str = "",
    tags: str = "",
    datasets: str = "",
    user: dict = Depends(require_authenticated),
):
    args: dict = {}
    if layer:    args["layer"]    = layer
    if cartridge: args["cartridge"] = cartridge
    if tags:     args["tags"]     = [t.strip() for t in tags.split(",") if t.strip()]
    if datasets: args["datasets"] = [d.strip() for d in datasets.split(",") if d.strip()]
    result = await _refinement_invoke("get_data_catalog", args, user=user)
    return result

# /api/catalog/entries
@router.post("/api/catalog/entries", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
@_bind_to_main
async def api_catalog_upsert(body: dict, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("upsert_catalog_entries", body, user=user)

# /api/catalog/relationships
@router.post("/api/catalog/relationships", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
@_bind_to_main
async def api_catalog_relationship(body: dict, user: dict = Depends(require_authenticated)):
    return await _refinement_invoke("register_relationship", body, user=user)
