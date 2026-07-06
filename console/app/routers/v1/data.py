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
@router.get("/datasets", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def list_datasets(user: dict = Depends(require_permission("datasets.read"))):
    payload = await _refinement_invoke("list_datasets", {}, user=user)
    return _sanitize_datasets_payload_for_user(user, payload)

# /datasets/{name}/schema
@router.get("/datasets/{name}/schema", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def dataset_schema(name: str, user: dict = Depends(require_permission("datasets.read"))):
    return await _refinement_invoke("get_schema", {"name": name}, user=user)

# /api/datasets
@router.get("/api/datasets", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_list_datasets_alias(user: dict = Depends(require_permission("datasets.read"))):
    return await list_datasets(user)

# /api/datasets/{name}/schema
@router.get("/api/datasets/{name}/schema", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_dataset_schema_alias(name: str, user: dict = Depends(require_permission("datasets.read"))):
    return await dataset_schema(name, user)

# /datasets/{name}/data
@router.get("/datasets/{name}/data", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def dataset_data(
    name: str,
    request: Request,
    limit: int = 100,
    user: dict = Depends(require_permission("datasets.read")),
):
    # Forward user context so refinement can apply RLS. Without it the GOLD
    # tables fall through to the empty-tenant filter (or the revenue_manager
    # 'N/D' fallback) and any authenticated user could read cross-tenant rows.
    user = user or getattr(request.state, "user", None) or {}
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
@router.get("/api/schema", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_schema(source: str, user: dict = Depends(require_permission("datasets.read"))):
    _require_technical_source_access(user, source)
    if _gold_dataset_from_source(source):
        return await _gold_schema_payload(source, user)
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
@router.get("/api/sources", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_sources(user: dict = Depends(require_permission("datasets.read"))):
    return await _sources_response_payload_impl(
        user=user,
        refinement_invoke=_refinement_invoke,
        gold_sources_from_catalog=_gold_sources_from_catalog,
        filter_technical_sources=_filter_technical_sources,
    )

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
@router.get("/api/datasets/{name}/detail", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_dataset_detail(name: str, user: dict = Depends(require_permission("datasets.read"))):
    definition = await _refinement_invoke("get_dataset_definition", {"name": name}, user=user)
    schema_payload = None
    schema_error = None
    try:
        schema_payload = await _refinement_invoke("get_schema", {"name": name}, user=user)
    except HTTPException as exc:
        schema_error = str(exc.detail or "Dataset schema unavailable")
    return _normalize_dataset_detail(definition, schema_payload, schema_error, user)

# /api/bronze/query
@router.post(
    "/api/bronze/query",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))],
)
@_bind_to_main
async def api_bronze_query(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    # Restricted to datasets.write because this endpoint accepts arbitrary SQL.
    # Read-only roles (viewer) must use the dataset-scoped endpoints below,
    # which build SQL server-side instead of trusting client input.
    sql   = body.get("sql", "").strip()
    limit = min(int(body.get("limit", 200)), 2000)
    if not sql:
        raise HTTPException(400, "sql is required")
    sources = _merge_declared_and_inferred_bronze_sources(body.get("sources"), sql)
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
@router.get("/api/datasets/{name}/lineage", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_dataset_lineage(name: str, user: dict = Depends(require_permission("datasets.read"))):
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
    buckets = _explorer_visible_buckets(
        _EXPLORER_DEFAULT_BUCKETS,
        is_security_admin=_is_security_admin_context(ctx),
    )
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
    kwargs = _explorer_list_kwargs(
        bucket_name=bucket_name,
        prefix=prefix,
        max_keys=max_keys,
        continuation_token=continuation_token,
    )
    try:
        resp = await asyncio.to_thread(s3.list_objects_v2, **kwargs)
    except Exception as exc:
        raise HTTPException(502, "object storage list failed") from exc
    objects = [
        _explorer_object_row(o)
        for o in resp.get("Contents", [])
        if o.get("Key") != prefix
        and _explorer_path_allowed(o.get("Key", ""), user, object_access=True)
    ]
    folders = [
        p["Prefix"]
        for p in resp.get("CommonPrefixes", [])
        if _explorer_path_allowed(p.get("Prefix", ""), user)
    ]
    return _explorer_list_response(
        bucket_name=bucket_name,
        prefix=prefix,
        folders=folders,
        objects=objects,
        response=resp,
    )

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
    if not _explorer_path_allowed(key, user, object_access=True):
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
    return _explorer_download_response(url, expires_in=expires_in)

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
    if not _explorer_path_allowed(key, user, object_access=True):
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
    return _explorer_delete_response(bucket_name=bucket_name, key=key)

# /api/lineage
@router.get("/api/lineage", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_lineage(cartridge: str | None = None, user: dict = Depends(require_permission("datasets.read"))):
    """Global lineage graph across raw sources and silver/gold datasets."""
    if cartridge:
        _require_technical_cartridge_access(user, cartridge)
    payload = await _refinement_invoke("list_datasets", {}, timeout=15, user=user)
    datasets = _sanitize_datasets_payload_for_user(user, payload or {}).get("datasets") or []
    allowed = _user_allowed_cartridges(user)
    if allowed is not None:
        datasets = [
            d for d in datasets
            if str(d.get("cartridge") or "").strip() in allowed
        ]
    if cartridge:
        datasets = [d for d in datasets if d.get("cartridge") == cartridge]

    def source_visible(source: str) -> bool:
        if not _dataset_source_visible_for_user(user, source):
            return False
        return True

    return _lineage_graph_payload(datasets, source_visible=source_visible)

# /api/data/{dataset}
@router.get("/api/data/{dataset}", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_data(
    dataset: str,
    request: Request,
    limit: int = 5000,
    user: dict = Depends(require_permission("datasets.read")),
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
@router.get("/api/data/{dataset}/options", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_data_options(dataset: str, columns: str = "", user: dict = Depends(require_permission("datasets.read"))):
    """Return distinct values per column for building filter selectors."""
    # SQL produced by _data_api_options_sql targets pggold.gold_<dataset> via Refinement.
    # It invokes preview_transform with _rls_user_context(user) through the shared helper.
    return await _data_options_payload_impl(
        dataset=dataset,
        columns=columns,
        user=user,
        refinement_url=REFINEMENT_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload_factory=_mcp_payload,
        rls_user_context=_rls_user_context,
        upstream_error_detail=_upstream_error_detail,
    )

# /api/data/{dataset}/query
@router.post("/api/data/{dataset}/query", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_data_query_filtered(dataset: str, body: dict, request: Request):
    """
    Execute a filtered query against a gold dataset.
    Body: {"filters": {"revenue_manager": "X", "fiscal_year": 2025,
                        "cliente": "Y", "proyecto": "Z"},
           "limit": 1000, "columns": ["col1", "col2"]}
    fiscal_year uses March-February logic automatically.
    """
    # Forward the authenticated user's context so refinement can apply RLS.
    _user = getattr(request.state, "user", None) or {}
    return await _filtered_data_query_payload_impl(
        dataset=dataset,
        body=body,
        user=_user,
        refinement_url=REFINEMENT_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload_factory=_mcp_payload,
        rls_user_context=_rls_user_context,
    )

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
@router.get("/api/semantic", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_semantic(cartridge: str = "", user: dict = Depends(require_permission("datasets.read"))):
    from app.services import cartridge_service as _cs
    cartridge, _active = await _resolve_scoped_operation_cartridge(
        user,
        cartridge,
        fallback="sap_successfactors",
    )
    manifest = await _cs.get_cartridge(cartridge)
    if manifest:
        # Pass through all entity fields so Studio can render display_name, dag_id, etc.
        return _semantic_manifest_response(
            cartridge=cartridge,
            manifest=manifest,
            catalog_entities=await _gold_semantic_entities_from_catalog(cartridge, user),
        )

    # Fallback: Pattern A — invoke via MCP server
    servers = await mcp_registry.list_servers()
    srv = next((s for s in servers if s["id"] == cartridge), None)
    if not srv:
        raise HTTPException(404, f"Cartridge '{cartridge}' not registered")
    entities = await mcp_registry.invoke(cartridge, "list_entities", {}, user=user)
    return {"cartridge": cartridge, "server": srv, "entities": entities}

# /api/catalog
@router.get("/api/catalog", dependencies=[Depends(require_permission("datasets.read"))])
@_bind_to_main
async def api_catalog_get(
    layer: str = "",
    cartridge: str = "",
    tags: str = "",
    datasets: str = "",
    user: dict = Depends(require_permission("datasets.read")),
):
    args: dict = {}
    if layer:    args["layer"]    = layer
    cartridge = await _scope_catalog_cartridge_arg(user, cartridge)
    if not cartridge and _user_allowed_cartridges(user) is not None:
        return _empty_catalog_payload()
    if cartridge: args["cartridge"] = cartridge
    if tags:     args["tags"]     = [t.strip() for t in tags.split(",") if t.strip()]
    if datasets: args["datasets"] = [d.strip() for d in datasets.split(",") if d.strip()]
    result = await _refinement_invoke("get_data_catalog", args, user=user)
    return result

# /api/catalog/entries
@router.post(
    "/api/catalog/entries",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
@_bind_to_main
async def api_catalog_upsert(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    return await _refinement_invoke("upsert_catalog_entries", body, user=user)

# /api/catalog/relationships
@router.post(
    "/api/catalog/relationships",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
@_bind_to_main
async def api_catalog_relationship(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    return await _refinement_invoke("register_relationship", body, user=user)
