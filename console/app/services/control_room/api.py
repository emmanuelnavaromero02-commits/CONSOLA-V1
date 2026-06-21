from __future__ import annotations

import types

from app.services.control_room import core as _core


_RESERVED_GLOBALS = {
    "__name__",
    "__package__",
    "__loader__",
    "__spec__",
    "__file__",
    "__cached__",
    "__builtins__",
}
for _name, _value in _core.__dict__.items():
    if _name not in _RESERVED_GLOBALS:
        globals()[_name] = _value


def _bind_to_core(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _core.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _core.__name__
    _core.__dict__[fn.__name__] = rebound
    return rebound


# External API/data access and item aggregation.
@_bind_to_core
def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


@_bind_to_core
def _show_known_non_ready_sources() -> bool:
    explicit = os.environ.get("CONTROL_ROOM_SHOW_KNOWN_NON_READY")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    app_env = os.environ.get("APP_ENV", "production")
    return app_env.strip().lower() not in {"production", "prod"}


@_bind_to_core
def _source_has_ready_contract(source: ControlRoomSource) -> bool:
    return _readiness_contract(source).get("data_readiness") in DATA_READY_STATES


@_bind_to_core
def _internal_headers(server: str) -> dict[str, str]:
    pair = os.environ.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server}")
    if pair:
        key = pair
    elif _is_production_env():
        raise RuntimeError(
            f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server}; legacy fallback disabled in production"
        )
    else:
        key = get_internal_api_key()
    return {"x-api-key": key, "x-internal-service": "console"}


@_bind_to_core
def _mcp_payload(tool: str, args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool": tool, "args": args}
    if user is not None:
        payload["security_context"] = build_security_context(user)
    return payload


@_bind_to_core
async def query_dataset_rows(
    dataset: str, user: dict | None, limit: int = 1000
) -> list[dict[str, Any]]:
    try:
        from app.services.intelligence.gold_fetcher import query_gold_dataset_rows

        return await query_gold_dataset_rows(dataset, user, limit)
    except HTTPException as exc:
        if exc.status_code not in {404, 503}:
            raise
    except Exception:
        pass

    async with httpx.AsyncClient(
        headers=_internal_headers("REFINEMENT"), timeout=45
    ) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "query_dataset",
                {
                    "name": dataset,
                    "limit": limit,
                    "user_context": rls_user_context(user),
                },
                user,
            ),
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"dataset unavailable: {dataset}")
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error") and "data" not in payload:
        detail = (
            payload.get("error")
            or payload.get("code")
            or f"dataset unavailable: {dataset}"
        )
        raise HTTPException(503, str(detail))
    data = payload.get("data", payload.get("result", payload))
    if isinstance(data, dict):
        if data.get("error") and "data" not in data:
            detail = (
                data.get("error")
                or data.get("code")
                or f"dataset unavailable: {dataset}"
            )
            raise HTTPException(503, str(detail))
        data = data.get("data", [])
    if not isinstance(data, list):
        return []
    return [dict(row) for row in data if isinstance(row, dict)]


@_bind_to_core
async def sap_successfactors_gold_kpis(user: dict | None) -> dict[str, Any]:
    """Core SuccessFactors Gold widgets for the active tenant/workspace.

    Reads go through the scoped Gold fetcher. A user outside the FEMSA
    workspace simply receives empty widgets because native Gold RLS filters the
    rows before they reach this code path.
    """
    from app.services.intelligence.gold_fetcher import query_gold_dataset_rows

    sf_gold_datasets = {
        "employee_360": "sap_successfactors_employee_360",
        "headcount_by_company": "sap_successfactors_headcount_by_company",
        "headcount_by_location": "sap_successfactors_headcount_by_location",
        "headcount_by_department": "sap_successfactors_headcount_by_department",
    }

    def truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {
            "1",
            "true",
            "t",
            "yes",
            "y",
            "activo",
            "active",
        }

    def public_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def public_rows(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        return [
            {str(key): public_value(value) for key, value in row.items()}
            for row in rows[:limit]
        ]

    def dataset_href(dataset: str) -> str:
        return (
            "/data/catalog?layer=gold&cartridge=sap_successfactors"
            f"&datasets={dataset}"
        )

    def top_headcount_rows(
        rows: list[dict[str, Any]], label_keys: tuple[str, str], limit: int = 5
    ) -> list[dict[str, Any]]:
        id_key, name_key = label_keys
        top: list[dict[str, Any]] = []
        for row in rows:
            label = str(row.get(name_key) or row.get(id_key) or "Sin clasificar")
            top.append(
                {
                    "label": label,
                    "id": public_value(row.get(id_key)),
                    "headcount": int(row.get("headcount") or 0),
                }
            )
        return sorted(top, key=lambda item: (-int(item["headcount"]), item["label"]))[
            :limit
        ]

    def status_error(results: list[dict[str, Any]]) -> str | None:
        errors = [str(result.get("error")) for result in results if result.get("error")]
        return "; ".join(errors) if errors else None

    def usable_rows(result: dict[str, Any]) -> list[dict[str, Any]] | None:
        status = str(result.get("status") or "")
        if status in {"ready", "empty"}:
            return result.get("rows") if isinstance(result.get("rows"), list) else []
        return None

    def combine_widget_status(results: list[dict[str, Any]]) -> str:
        statuses = {str(result.get("status") or "unavailable") for result in results}
        usable = any(status in {"ready", "empty"} for status in statuses)
        if usable and any(
            status in {"no_permission", "unavailable", "missing"} for status in statuses
        ):
            return "partial"
        for status in ("no_permission", "unavailable", "missing", "empty", "ready"):
            if status in statuses:
                return status
        return "unavailable"

    async def gold_result(dataset: str, limit: int) -> dict[str, Any]:
        try:
            rows = await query_gold_dataset_rows(dataset, user, limit)
        except HTTPException as exc:
            status = {
                403: "no_permission",
                404: "missing",
                503: "unavailable",
            }.get(exc.status_code, "unavailable")
            return {
                "rows": [],
                "status": status,
                "error": str(exc.detail or f"{dataset} unavailable"),
            }
        except Exception as exc:
            return {
                "rows": [],
                "status": "unavailable",
                "error": str(exc),
            }
        clean_rows = [dict(row) for row in rows if isinstance(row, dict)]
        return {
            "rows": clean_rows,
            "status": "empty" if not clean_rows else "ready",
            "error": None,
        }

    employee_result = await gold_result(sf_gold_datasets["employee_360"], 5000)
    company_result = await gold_result(sf_gold_datasets["headcount_by_company"], 1000)
    location_result = await gold_result(sf_gold_datasets["headcount_by_location"], 1000)
    department_result = await gold_result(
        sf_gold_datasets["headcount_by_department"], 1000
    )

    employee_rows = usable_rows(employee_result)
    company_rows = usable_rows(company_result)
    location_rows = usable_rows(location_result)
    department_rows = usable_rows(department_result)

    company_headcount_total = (
        None
        if company_rows is None
        else sum(int(row.get("headcount") or 0) for row in company_rows)
    )
    employee_active_total = (
        None
        if employee_rows is None
        else sum(1 for row in employee_rows if truthy(row.get("is_active")))
    )
    if company_headcount_total and company_headcount_total > 0:
        active_headcount = company_headcount_total
    elif employee_active_total is not None:
        active_headcount = employee_active_total
    else:
        active_headcount = company_headcount_total
    generated_at = datetime.now(UTC).isoformat()
    tenant_id, workspace_id = _workspace_scope(user)

    widgets = [
        {
            "id": "sf_active_headcount",
            "title": "Headcount total activo",
            "value": active_headcount,
            "dataset": sf_gold_datasets["employee_360"],
            "href": dataset_href(sf_gold_datasets["employee_360"]),
            "rows": public_rows(employee_rows or [], 5),
            "status": combine_widget_status([employee_result, company_result]),
            "error": status_error([employee_result, company_result]),
        },
        {
            "id": "sf_headcount_by_company",
            "title": "Headcount por compania",
            "value": company_headcount_total,
            "dataset": sf_gold_datasets["headcount_by_company"],
            "href": dataset_href(sf_gold_datasets["headcount_by_company"]),
            "rows": top_headcount_rows(
                company_rows or [], ("company_id", "company_name")
            ),
            "status": company_result["status"],
            "error": company_result.get("error"),
        },
        {
            "id": "sf_headcount_by_location",
            "title": "Headcount por ubicacion",
            "value": None
            if location_rows is None
            else sum(int(row.get("headcount") or 0) for row in location_rows),
            "dataset": sf_gold_datasets["headcount_by_location"],
            "href": dataset_href(sf_gold_datasets["headcount_by_location"]),
            "rows": top_headcount_rows(
                location_rows or [], ("location_id", "location_name")
            ),
            "status": location_result["status"],
            "error": location_result.get("error"),
        },
        {
            "id": "sf_headcount_by_department",
            "title": "Headcount por departamento",
            "value": None
            if department_rows is None
            else sum(int(row.get("headcount") or 0) for row in department_rows),
            "dataset": sf_gold_datasets["headcount_by_department"],
            "href": dataset_href(sf_gold_datasets["headcount_by_department"]),
            "rows": top_headcount_rows(
                department_rows or [], ("department_id", "department_name")
            ),
            "status": department_result["status"],
            "error": department_result.get("error"),
        },
    ]
    return {
        "generated_at": generated_at,
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "widgets": widgets,
    }


@_bind_to_core
def _details(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": value}
        except json.JSONDecodeError:
            return {"raw": value}
    return {}


@_bind_to_core
def _encode_id(parts: dict[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@_bind_to_core
def _severity(value: Any) -> str:
    sev = str(value or "medium").strip().lower()
    if sev in {"critica", "critical", "critico"}:
        return "critical"
    if sev in {"alta", "high"}:
        return "high"
    if sev in {"media", "moderada", "medium"}:
        return "medium"
    if sev in {"baja", "low", "ok", "satisfactoria"}:
        return "low"
    return "medium"


@_bind_to_core
def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@_bind_to_core
def _copy_for(anomaly_type: str) -> dict[str, str]:
    return ANOMALY_COPY.get(
        anomaly_type,
        {
            "title": anomaly_type.replace("_", " ").strip().title()
            or "Senal operativa",
            "recommendation": "Revisar el registro, asignar responsable y documentar la decision tomada.",
            "root_cause": "La fuente genero una senal fuera de regla que requiere investigacion.",
        },
    )


@_bind_to_core
def _workspace_scope(user: dict | None) -> tuple[str | None, str]:
    workspace_id = (user or {}).get("active_workspace_id") or (user or {}).get(
        "workspace_id"
    )
    tenant_id = (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id")
    if not workspace_id:
        raise HTTPException(400, "active workspace is required")
    return (str(tenant_id) if tenant_id else None), str(workspace_id)


@_bind_to_core
def _workspace_id(user: dict | None) -> str:
    return _workspace_scope(user)[1]


@_bind_to_core
def _actor_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


@_bind_to_core
def _can_read_workspace_wide(user: dict | None) -> bool:
    role = str((user or {}).get("role") or "").strip()
    scoped = str(
        (user or {}).get("workspace_role") or (user or {}).get("platform_role") or ""
    ).strip()
    return role in {"admin", "owner", "super_admin"} or scoped in {
        "workspace_admin",
        "tenant_admin",
    }


@_bind_to_core
def _module_by_cartridge() -> dict[str, ControlRoomModule]:
    return {module.cartridge: module for module in MODULES}


@_bind_to_core
def _module_by_visible_id() -> dict[str, ControlRoomModule]:
    return {module.visible_id: module for module in MODULES}


@_bind_to_core
def _all_sources() -> tuple[ControlRoomSource, ...]:
    sources: list[ControlRoomSource] = []
    for module in MODULES:
        sources.extend(module.sources)
    return tuple(sources)


@_bind_to_core
def _allowed_from_user(user: dict | None) -> set[str] | None:
    ctx = build_security_context(user)
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if "*" in allowed:
        return None
    return allowed if allowed else None


@_bind_to_core
def _vault_headers_for_user(user: dict | None) -> dict[str, str]:
    return {
        **_internal_headers("VAULT"),
        "x-security-context": json.dumps(
            build_security_context(user), ensure_ascii=False
        ),
    }


@_bind_to_core
def _safe_connection_summary(connection: dict[str, Any]) -> dict[str, Any]:
    conn_id = str(connection.get("conn_id") or connection.get("id") or "").strip()
    out: dict[str, Any] = {"conn_id": conn_id}
    auth_method = str(connection.get("auth_method") or "").strip()
    if auth_method:
        out["auth_method"] = auth_method
    base_url = str(connection.get("base_url") or connection.get("url") or "").strip()
    if base_url:
        out["base_url"] = base_url
    return out


@_bind_to_core
async def _vault_connections_for_cartridge(
    cartridge_id: str, user: dict | None
) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(
            headers=_vault_headers_for_user(user), timeout=6.0
        ) as client:
            response = await client.get(
                f"{VAULT_URL}/connections/{quote(cartridge_id, safe='')}"
            )
    except Exception:
        return []
    if response.status_code in {404, 204}:
        return []
    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    raw_connections = payload.get("connections") if isinstance(payload, dict) else []
    if not isinstance(raw_connections, list):
        return []
    connections: list[dict[str, Any]] = []
    for raw in raw_connections:
        if not isinstance(raw, dict):
            continue
        summary = _safe_connection_summary(raw)
        if summary.get("conn_id"):
            connections.append(summary)
    return connections


@_bind_to_core
async def _filter_installations_by_scoped_connections(
    installations: list[dict[str, Any]],
    user: dict | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in installations:
        cartridge_id = str(row.get("cartridge_id") or "").strip()
        if not cartridge_id or cartridge_id in seen:
            continue
        seen.add(cartridge_id)
        status = str(row.get("installation_status") or "ready").strip().lower()
        if status not in ACTIVE_INSTALLATION_STATUSES:
            continue
        candidate = {
            **row,
            "installation_status": status,
            "connections": [],
            "connection_count": 0,
            "active_connection_ids": [],
            "connection_id": None,
            "auth_method": None,
        }
        connections = await _vault_connections_for_cartridge(cartridge_id, user)
        if not connections:
            candidates.append(candidate)
            continue
        first = connections[0]
        candidates.append(
            {
                **candidate,
                "connections": connections,
                "connection_count": len(connections),
                "active_connection_ids": [conn["conn_id"] for conn in connections],
                "connection_id": first.get("conn_id"),
                "auth_method": first.get("auth_method"),
            }
        )
    connected = [row for row in candidates if row.get("connection_count")]
    return connected or candidates


@_bind_to_core
def _attach_thresholds(
    item: dict[str, Any],
    thresholds_applied: list[dict[str, Any]],
    state: str,
) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    item["thresholds_applied"] = thresholds_applied
    item["threshold_state"] = state
    item["details"] = {
        **details,
        "thresholds": thresholds_applied,
        "threshold_state": state,
    }
    return item


@_bind_to_core
async def _installed_cartridges(user: dict | None) -> list[dict[str, Any]]:
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        pool = await auth.pool()
        async def _load(conn: Any, _tenant_id: str | None, _workspace_id: str) -> list[Any]:
            return await conn.fetch(
                """
                SELECT
                    ci.cartridge_id,
                    ci.status AS installation_status,
                    ci.current_step,
                    ci.error_message,
                    ci.ready_at,
                    COALESCE(mp.name, c.name, ci.cartridge_id) AS label,
                    COALESCE(c.category, 'cartridge') AS category
                FROM cartridge_installations ci
                LEFT JOIN cartridges c ON c.id = ci.cartridge_id
                LEFT JOIN marketplace_products mp ON mp.cartridge_id = ci.cartridge_id
                WHERE ci.tenant_id = $1
                  AND ci.workspace_id = $2
                  AND NOT EXISTS (
                      SELECT 1
                        FROM user_cartridge_overrides uco
                       WHERE uco.tenant_id = ci.tenant_id
                         AND uco.workspace_id = ci.workspace_id
                         AND uco.cartridge_id = ci.cartridge_id
                         AND uco.user_id = $3
                         AND uco.mode = 'deny'
                  )
                ORDER BY lower(COALESCE(mp.name, c.name, ci.cartridge_id))
                """,
                tenant_id,
                workspace_id,
                (user or {}).get("id"),
            )

        rows = await _run_with_db_scope(pool, user or {}, _load)
        return await _filter_installations_by_scoped_connections(
            [_row_to_public(row) for row in rows], user
        )
    except Exception:
        allowed = _allowed_from_user(user)
        fallback: dict[str, dict[str, Any]] = {}
        for module in MODULES:
            if allowed is not None and module.cartridge not in allowed:
                continue
            fallback.setdefault(
                module.cartridge,
                {
                    "cartridge_id": module.cartridge,
                    "installation_status": "ready",
                    "current_step": "fallback",
                    "label": module.label,
                    "category": "platform" if module.operational else "cartridge",
                },
            )
        return await _filter_installations_by_scoped_connections(
            list(fallback.values()), user
        )


@_bind_to_core
async def _fetch_source(
    source: ControlRoomSource,
    user: dict | None,
    fetcher: DatasetFetcher,
    limit_per_source: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checked_at = datetime.now(UTC).isoformat()
    try:
        rows = await fetcher(source.dataset, user, limit_per_source)
    except HTTPException as exc:
        status = "missing" if exc.status_code == 404 else "unavailable"
        return [], _source_status_payload(
            source,
            status,
            count=0,
            checked_at=checked_at,
            error=str(exc.detail),
        )
    except Exception as exc:
        return [], _source_status_payload(
            source,
            "unavailable",
            count=0,
            checked_at=checked_at,
            error=str(exc),
        )

    if rows and source.normalizer == "standard_anomaly":
        missing_contract = all(
            source.entity_id_field not in row and source.entity_label_field not in row
            for row in rows
        )
        if missing_contract:
            return [], _source_status_payload(
                source,
                "invalid_schema",
                count=len(rows),
                checked_at=checked_at,
                error=f"missing expected fields: {source.entity_id_field}/{source.entity_label_field}",
            )
    return rows, _source_status_payload(
        source,
        "empty" if not rows else "ok",
        count=len(rows),
        checked_at=checked_at,
    )


@_bind_to_core
def _base_item(
    source: ControlRoomSource,
    row: dict[str, Any],
    item_type: str,
    entity_id: str,
    label: str,
) -> dict[str, Any]:
    severity = _severity(row.get("severity"))
    details = _details(row.get("details"))
    detected_at = str(
        row.get("detected_at") or row.get("mes") or row.get("semana") or ""
    )
    escaped_entity = str(entity_id or label).replace("'", "''")
    technical_sql = (
        f"SELECT * FROM {source.dataset} "
        f"WHERE {source.entity_id_field} = '{escaped_entity}' "
        f"LIMIT 50"
    )
    return {
        "id": _encode_id(
            {
                "dataset": source.dataset,
                "type": item_type,
                "entity": entity_id or label,
            }
        ),
        "kind": source.kind,
        "domain": source.domain,
        "module": source.module_label,
        "module_id": source.visible_module_id,
        "cartridge": source.cartridge,
        "connector_id": source.cartridge,
        "source_dataset": source.dataset,
        "entity_kind": source.entity_kind,
        "entity_id": entity_id,
        "entity_label": label,
        "anomaly_type": item_type,
        "severity": severity,
        "severity_weight": SEVERITY_WEIGHT[severity],
        "detected_at": detected_at,
        "details": details,
        "sql": technical_sql,
        "status": "open",
        "decision_id": None,
        "thresholds_applied": [],
        "threshold_state": "default",
    }


@_bind_to_core
def _normalize_standard_anomaly(
    source: ControlRoomSource, row: dict[str, Any]
) -> dict[str, Any]:
    anomaly_type = str(row.get("anomaly_type") or "unknown").strip()
    entity_id = str(row.get(source.entity_id_field) or "").strip()
    label = str(row.get(source.entity_label_field) or entity_id or "Sin nombre").strip()
    details = _details(row.get("details"))
    copy = _copy_for(anomaly_type)
    item = _base_item(source, row, anomaly_type, entity_id, label)
    reason = str(details.get("reason") or "").strip()
    item.update(
        {
            "title": copy["title"],
            "description": reason
            or f"{source.entity_kind} {label} requiere revision operativa.",
            "recommendation": copy["recommendation"],
            "root_cause": str(details.get("root_cause") or copy["root_cause"]),
            "impact": str(
                details.get("impact") or f"Riesgo operativo en {source.domain}."
            ),
        }
    )
    return item


@_bind_to_core
def _normalize_replicon_allocation(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    pct = _num(row.get("pct_asignacion"))
    over_warning = _threshold_value(
        thresholds,
        "replicon",
        "over_allocation",
        "pct_asignacion",
        "warning_value",
        110,
    )
    over_critical = _threshold_value(
        thresholds,
        "replicon",
        "over_allocation",
        "pct_asignacion",
        "critical_value",
        130,
    )
    under_warning = _threshold_value(
        thresholds,
        "replicon",
        "under_allocation",
        "pct_asignacion",
        "warning_value",
        40,
    )
    under_critical = _threshold_value(
        thresholds,
        "replicon",
        "under_allocation",
        "pct_asignacion",
        "critical_value",
        20,
    )
    if pct is None or under_warning <= pct <= over_warning:
        return None
    consultor = str(row.get("consultor") or "Sin consultor").strip()
    proyecto = str(
        row.get("proyecto") or row.get("project_name") or "Sin proyecto"
    ).strip()
    item_type = "over_allocation" if pct > 110 else "under_allocation"
    if pct > over_warning:
        item_type = "over_allocation"
        threshold_state = "critical" if pct >= over_critical else "warning"
        severity = "high" if threshold_state == "critical" else "medium"
        threshold_refs = [
            _threshold_ref(
                thresholds,
                "replicon",
                "over_allocation",
                "pct_asignacion",
                warning_default=110,
                critical_default=130,
                currency="PCT",
            )
        ]
    else:
        item_type = "under_allocation"
        threshold_state = "critical" if pct <= under_critical else "warning"
        severity = "high" if threshold_state == "critical" else "medium"
        threshold_refs = [
            _threshold_ref(
                thresholds,
                "replicon",
                "under_allocation",
                "pct_asignacion",
                warning_default=40,
                critical_default=20,
                currency="PCT",
            )
        ]
    item = _base_item(
        source,
        {**row, "severity": severity},
        item_type,
        f"{consultor}:{proyecto}",
        consultor,
    )
    direction = "sobreasignacion" if item_type == "over_allocation" else "subasignacion"
    item.update(
        {
            "title": f"{consultor} con {direction} operativa",
            "description": f"{consultor} registra {pct:.1f}% de asignacion en {proyecto}.",
            "recommendation": "Rebalancear carga con el revenue manager y documentar excepcion si la asignacion es temporal.",
            "root_cause": "Diferencia entre capacidad mensual esperada y horas asignadas.",
            "impact": "Riesgo de capacidad, margen o entrega del proyecto.",
            "details": {**item["details"], **row},
        }
    )
    return _attach_thresholds(item, threshold_refs, threshold_state)


@_bind_to_core
def _normalize_replicon_timesheet(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    total = _num(row.get("horas_total")) or _num(row.get("horas_totales")) or 0
    no_billable = _num(row.get("horas_no_facturables")) or 0
    if total <= 0:
        return None
    ratio = no_billable / total
    warning_ratio = _threshold_value(
        thresholds,
        "replicon",
        "non_billable_ratio",
        "horas_no_facturables_ratio",
        "warning_value",
        0.35,
    )
    critical_ratio = _threshold_value(
        thresholds,
        "replicon",
        "non_billable_ratio",
        "horas_no_facturables_ratio",
        "critical_value",
        0.55,
    )
    if ratio < warning_ratio:
        return None
    consultor = str(row.get("consultor") or "Sin consultor").strip()
    proyecto = str(
        row.get("proyecto") or row.get("project_name") or "Sin proyecto"
    ).strip()
    threshold_state = "critical" if ratio >= critical_ratio else "warning"
    severity = "high" if threshold_state == "critical" else "medium"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "non_billable_ratio",
        f"{consultor}:{proyecto}",
        consultor,
    )
    period = row.get("semana") or row.get("mes")
    period_text = f" en {period}" if period else ""
    item.update(
        {
            "title": "Horas no facturables fuera de rango",
            "description": f"{consultor} tiene {ratio:.0%} de horas no facturables en {proyecto}{period_text}.",
            "recommendation": "Validar causa con PM/RM, reclasificar si procede y ajustar forecast de margen.",
            "root_cause": "Registro de tiempo no facturable alto frente al total reportado.",
            "impact": "Puede erosionar margen y ocultar demanda no planificada.",
            "details": {**item["details"], **row},
        }
    )
    return _attach_thresholds(
        item,
        [
            _threshold_ref(
                thresholds,
                "replicon",
                "non_billable_ratio",
                "horas_no_facturables_ratio",
                warning_default=0.35,
                critical_default=0.55,
                currency="PCT",
            )
        ],
        threshold_state,
    )


@_bind_to_core
def _normalize_replicon_pnl(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    margin = _num(row.get("margen_bruto_pct"))
    wip = _num(row.get("wip_usd")) or 0
    margin_warning = _threshold_value(
        thresholds, "replicon", "low_margin", "margen_bruto_pct", "warning_value", 20
    )
    margin_critical = _threshold_value(
        thresholds, "replicon", "low_margin", "margen_bruto_pct", "critical_value", 0
    )
    wip_warning = _threshold_value(
        thresholds, "replicon", "wip_variance", "wip_usd", "warning_value", 5000
    )
    wip_critical = _threshold_value(
        thresholds, "replicon", "wip_variance", "wip_usd", "critical_value", 25000
    )
    margin_breached = margin is not None and margin < margin_warning
    wip_breached = abs(wip) >= wip_warning
    if not margin_breached and not wip_breached:
        return None
    proyecto = str(
        row.get("proyecto") or row.get("project_name") or "Sin proyecto"
    ).strip()
    manager = str(row.get("revenue_manager") or "Sin RM").strip()
    if margin is not None and margin < margin_critical:
        severity = "critical"
        threshold_state = "critical"
    elif abs(wip) >= wip_critical:
        severity = "critical"
        threshold_state = "critical"
    elif margin_breached:
        severity = "high"
        threshold_state = "warning"
    else:
        severity = "medium"
        threshold_state = "warning"
    item_type = "low_margin" if margin_breached else "wip_variance"
    item = _base_item(
        source,
        {**row, "severity": severity},
        item_type,
        proyecto,
        str(row.get("project_name") or proyecto),
    )
    item.update(
        {
            "title": "Proyecto con margen o WIP fuera de control",
            "description": f"{proyecto} esta bajo {manager}; margen={margin if margin is not None else 'N/D'}%, WIP={wip:,.0f} USD.",
            "recommendation": "Revisar revenue, facturacion, costo hundido y compromiso de remediacion con finanzas.",
            "root_cause": "Desviacion entre ingreso reconocido, facturacion y costo total.",
            "impact": "Riesgo financiero directo en margen, cash flow o forecast.",
            "details": {**item["details"], **row},
        }
    )
    refs = []
    if margin_breached:
        refs.append(
            _threshold_ref(
                thresholds,
                "replicon",
                "low_margin",
                "margen_bruto_pct",
                warning_default=20,
                critical_default=0,
                currency="PCT",
            )
        )
    if wip_breached:
        refs.append(
            _threshold_ref(
                thresholds,
                "replicon",
                "wip_variance",
                "wip_usd",
                warning_default=5000,
                critical_default=25000,
            )
        )
    return _attach_thresholds(item, refs, threshold_state)


@_bind_to_core
def _normalize_replicon_skill_gap(
    source: ControlRoomSource, row: dict[str, Any]
) -> dict[str, Any] | None:
    level = str(row.get("brecha_nivel") or "").strip().upper()
    if level not in {"CRITICA", "MODERADA"}:
        return None
    manager = str(row.get("manager_name") or "Sin manager").strip()
    category = str(row.get("skill_category") or "Sin categoria").strip()
    severity = "high" if level == "CRITICA" else "medium"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "skill_gap",
        f"{manager}:{category}",
        manager,
    )
    item.update(
        {
            "title": "Brecha de skills en equipo",
            "description": f"{manager} tiene brecha {level.lower()} en {category}.",
            "recommendation": "Priorizar capacitacion, mentorias o staffing alterno antes de nuevas asignaciones criticas.",
            "root_cause": "Cobertura de habilidades insuficiente para la demanda operativa.",
            "impact": "Riesgo de delivery, calidad o dependencia de pocos expertos.",
            "details": {**item["details"], **row},
        }
    )
    return item


@_bind_to_core
def _normalize_s4_revenue(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    revenue = _num(row.get("revenue"))
    warning_revenue = _threshold_value(
        thresholds, "sap_s4hana", "negative_revenue", "revenue", "warning_value", 0
    )
    critical_revenue = _threshold_value(
        thresholds,
        "sap_s4hana",
        "negative_revenue",
        "revenue",
        "critical_value",
        -100000,
    )
    if revenue is None or revenue >= warning_revenue:
        return None
    customer = str(row.get("customer_code") or "Sin cliente").strip()
    month = str(row.get("revenue_month") or row.get("mes") or "").strip()
    severity = "critical" if revenue <= critical_revenue else "high"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "negative_revenue",
        f"{customer}:{month}",
        customer,
    )
    item.update(
        {
            "title": "Revenue negativo en ventas",
            "description": f"{customer} registra revenue negativo por {abs(revenue):,.0f} en {month or 'el periodo'}.",
            "recommendation": "Revisar notas de credito, anulaciones y conciliacion de facturacion antes del cierre.",
            "root_cause": "La facturacion neta del periodo quedo por debajo de cero.",
            "impact": "Riesgo de distorsion de revenue, forecast y margen comercial.",
            "details": {**item["details"], **row},
        }
    )
    return _attach_thresholds(
        item,
        [
            _threshold_ref(
                thresholds,
                "sap_s4hana",
                "negative_revenue",
                "revenue",
                warning_default=0,
                critical_default=-100000,
            )
        ],
        "critical" if severity == "critical" else "warning",
    )


@_bind_to_core
def _normalize_s4_backlog(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    age = _num(row.get("oldest_age_days")) or 0
    open_value = _num(row.get("open_value")) or 0
    age_warning = _threshold_value(
        thresholds,
        "sap_s4hana",
        "aged_sales_backlog",
        "oldest_age_days",
        "warning_value",
        45,
    )
    age_critical = _threshold_value(
        thresholds,
        "sap_s4hana",
        "aged_sales_backlog",
        "oldest_age_days",
        "critical_value",
        90,
    )
    value_warning = _threshold_value(
        thresholds,
        "sap_s4hana",
        "aged_sales_backlog",
        "open_value",
        "warning_value",
        50000,
    )
    value_critical = _threshold_value(
        thresholds,
        "sap_s4hana",
        "aged_sales_backlog",
        "open_value",
        "critical_value",
        250000,
    )
    age_breached = age >= age_warning
    value_breached = open_value >= value_warning
    if not age_breached and not value_breached:
        return None
    customer = str(row.get("customer_code") or "Sin cliente").strip()
    threshold_state = (
        "critical" if age >= age_critical or open_value >= value_critical else "warning"
    )
    severity = "critical" if threshold_state == "critical" else "high"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "aged_sales_backlog",
        customer,
        customer,
    )
    item.update(
        {
            "title": "Backlog comercial envejecido",
            "description": f"{customer} tiene pedidos abiertos por {open_value:,.0f} con antiguedad maxima de {age:.0f} dias.",
            "recommendation": "Validar bloqueo, entrega, facturacion pendiente y responsable comercial.",
            "root_cause": "Pedidos abiertos permanecen sin completar fuera del ciclo normal.",
            "impact": "Riesgo de cash flow, cumplimiento de entrega y forecast de ventas.",
            "details": {**item["details"], **row},
        }
    )
    refs = []
    if age_breached:
        refs.append(
            _threshold_ref(
                thresholds,
                "sap_s4hana",
                "aged_sales_backlog",
                "oldest_age_days",
                warning_default=45,
                critical_default=90,
                currency="DAYS",
            )
        )
    if value_breached:
        refs.append(
            _threshold_ref(
                thresholds,
                "sap_s4hana",
                "aged_sales_backlog",
                "open_value",
                warning_default=50000,
                critical_default=250000,
            )
        )
    return _attach_thresholds(item, refs, threshold_state)


@_bind_to_core
def _normalize_s4_supplier_spend(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    spend = _num(row.get("total_spend")) or 0
    warning_spend = _threshold_value(
        thresholds,
        "sap_s4hana",
        "supplier_spend_concentration",
        "total_spend",
        "warning_value",
        250000,
    )
    critical_spend = _threshold_value(
        thresholds,
        "sap_s4hana",
        "supplier_spend_concentration",
        "total_spend",
        "critical_value",
        750000,
    )
    if spend < warning_spend:
        return None
    supplier = str(row.get("supplier_code") or "Sin proveedor").strip()
    month = str(row.get("spend_month") or "").strip()
    threshold_state = "critical" if spend >= critical_spend else "warning"
    severity = "high" if threshold_state == "critical" else "medium"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "supplier_spend_concentration",
        f"{supplier}:{month}",
        supplier,
    )
    item.update(
        {
            "title": "Gasto alto concentrado en proveedor",
            "description": f"{supplier} concentra {spend:,.0f} de gasto en {month or 'el periodo'}.",
            "recommendation": "Revisar aprobaciones, categoria, contrato vigente y comparativo contra presupuesto.",
            "root_cause": "Concentracion de gasto relevante en compras del periodo.",
            "impact": "Riesgo de sobrepresupuesto, dependencia de proveedor o control de aprobaciones.",
            "details": {**item["details"], **row},
        }
    )
    return _attach_thresholds(
        item,
        [
            _threshold_ref(
                thresholds,
                "sap_s4hana",
                "supplier_spend_concentration",
                "total_spend",
                warning_default=250000,
                critical_default=750000,
            )
        ],
        threshold_state,
    )


@_bind_to_core
def _normalize_row(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    if source.normalizer == "standard_anomaly":
        return _normalize_standard_anomaly(source, row)
    if source.normalizer == "replicon_allocation":
        return _normalize_replicon_allocation(source, row, thresholds)
    if source.normalizer == "replicon_timesheet":
        return _normalize_replicon_timesheet(source, row, thresholds)
    if source.normalizer == "replicon_pnl":
        return _normalize_replicon_pnl(source, row, thresholds)
    if source.normalizer == "replicon_skill_gap":
        return _normalize_replicon_skill_gap(source, row)
    if source.normalizer == "s4_revenue":
        return _normalize_s4_revenue(source, row, thresholds)
    if source.normalizer == "s4_backlog":
        return _normalize_s4_backlog(source, row, thresholds)
    if source.normalizer == "s4_supplier_spend":
        return _normalize_s4_supplier_spend(source, row, thresholds)
    return None


@_bind_to_core
def _money_sum(rows: Iterable[dict[str, Any]], field: str) -> float:
    return round(sum(_num(row.get(field)) or 0 for row in rows), 2)


@_bind_to_core
def _ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 2)


@_bind_to_core
def _financial_metrics(
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    pnl_rows = rows_by_dataset.get("pnl_mensual", [])
    revenue_rows = rows_by_dataset.get("revenue_by_customer", [])
    backlog_rows = rows_by_dataset.get("open_sales_orders", [])
    purchase_rows = rows_by_dataset.get("purchase_spend_by_supplier", [])
    relevant = {
        source["dataset"]: {
            "status": source["status"],
            "count": int(source.get("count") or 0),
            "domain": source.get("domain"),
            "cartridge": source.get("cartridge"),
        }
        for source in sources
        if source["dataset"]
        in {
            "pnl_mensual",
            "revenue_by_customer",
            "open_sales_orders",
            "purchase_spend_by_supplier",
        }
    }

    revenue_usd = _money_sum(pnl_rows, "revenue_usd")
    billed_usd = _money_sum(pnl_rows, "facturacion_mes_usd")
    wip_usd = _money_sum(pnl_rows, "wip_usd")
    cost_usd = _money_sum(pnl_rows, "costo_total")
    margin_usd = _money_sum(pnl_rows, "margen_bruto_usd")
    sales_revenue = _money_sum(revenue_rows, "revenue")
    backlog_value = _money_sum(backlog_rows, "open_value")
    purchase_spend = _money_sum(purchase_rows, "total_spend")
    open_orders = int(_money_sum(backlog_rows, "open_orders"))
    oldest_backlog_days = int(
        max((_num(row.get("oldest_age_days")) or 0 for row in backlog_rows), default=0)
    )

    risk_rows: list[dict[str, Any]] = []
    for row in pnl_rows:
        margin_pct = _num(row.get("margen_bruto_pct"))
        wip = _num(row.get("wip_usd")) or 0
        if (margin_pct is not None and margin_pct < 20) or abs(wip) >= 5000:
            risk_rows.append(
                {
                    "label": str(
                        row.get("project_name") or row.get("proyecto") or "Proyecto"
                    ),
                    "owner": str(row.get("revenue_manager") or "N/D"),
                    "margin_pct": margin_pct,
                    "wip_usd": round(wip, 2),
                    "margin_usd": round(_num(row.get("margen_bruto_usd")) or 0, 2),
                }
            )
    risk_rows.sort(
        key=lambda row: (
            row["margin_pct"] if row["margin_pct"] is not None else 999,
            -abs(row["wip_usd"]),
        )
    )

    available = any(
        source["status"] == "ok" and source.get("count", 0)
        for source in relevant.values()
    )
    if available:
        status = "ok"
    elif any(
        source["status"] in {"unavailable", "invalid_schema"}
        for source in relevant.values()
    ):
        status = "unavailable"
    elif any(source["status"] == "missing" for source in relevant.values()):
        status = "missing"
    elif relevant:
        status = "empty"
    else:
        status = "not_installed"

    return {
        "status": status,
        "sources": relevant,
        "revenue_usd": revenue_usd,
        "billed_usd": billed_usd,
        "wip_usd": wip_usd,
        "cost_usd": cost_usd,
        "margin_usd": margin_usd,
        "margin_pct": _ratio(margin_usd, revenue_usd),
        "sales_revenue": sales_revenue,
        "backlog_value": backlog_value,
        "open_orders": open_orders,
        "oldest_backlog_days": oldest_backlog_days,
        "purchase_spend": purchase_spend,
        "risk_projects": risk_rows[:6],
    }


@_bind_to_core
def _cycle_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {step["id"]: 0 for step in OMEGA_STEPS}
    for item in items:
        status = str(item.get("status") or "open")
        counts["signals"] += 1
        if status in {"open", "in_review"}:
            counts["investigation"] += 1
        if status == "in_review" or item.get("selected_option_id"):
            counts["options"] += 1
        if item.get("decision_id") or status in {
            "decision_created",
            "approved",
            "resolved",
        }:
            counts["decision"] += 1
        if status in {"approved", "resolved"}:
            counts["execution"] += 1
            counts["control"] += 1
            counts["lessons"] += 1
        elif status == "dismissed":
            counts["control"] += 1
    return counts


@_bind_to_core
def _lessons_for_item(item: dict[str, Any]) -> list[str]:
    cleaned: list[str] = []
    for key in ("lessons", "learned_rules"):
        existing = item.get(key)
        if isinstance(existing, list):
            for rule in existing:
                text = str(rule).strip()
                if text and text not in cleaned:
                    cleaned.append(text)
    if cleaned:
        return cleaned
    return [
        f"Si {item.get('source_dataset')} genera {item.get('anomaly_type')}, abrir revision OMEGA.",
        "Toda aprobacion debe quedar ligada a decision_actions y audit_events.",
    ]


@_bind_to_core
async def _persisted_intelligence_items(user: dict | None) -> list[dict[str, Any]]:
    try:
        tenant_id, workspace_id = _workspace_scope(user)
        params: list[Any] = [workspace_id]
        tenant_clause = ""
        if tenant_id:
            params.append(tenant_id)
            tenant_clause = f"AND tenant_id::text = ${len(params)}"
        owner_clause = ""
        if not _can_read_workspace_wide(user):
            owner_id = _actor_id((user or {}).get("id"))
            if owner_id is None:
                return []
            params.append(owner_id)
            owner_clause = f"AND owner_user_id = ${len(params)}"
        pool = await auth.pool()
        # Contract: persisted Intelligence items remain scoped as
        # item_kind = 'intelligence_signal'; agent monitor alerts are added
        # advisory-only without replacing the Intelligence signal surface.
        async def _load(conn: Any, _tenant_id: str | None, _workspace_id: str) -> list[Any]:
            return await conn.fetch(
                f"""
                SELECT tenant_id, workspace_id, item_id, cartridge_id, domain, source_dataset, item_kind, title,
                       severity, status, decision_id, entity_kind, entity_id,
                       entity_label, anomaly_type, metadata, first_seen_at, last_seen_at,
                       resolved_at, dismissed_at, impact_estimate, impact_currency,
                       confidence, priority_score, selected_option_id, execution_status
                  FROM control_room_items
                 WHERE workspace_id = $1
                   {tenant_clause}
                   {owner_clause}
                   AND item_kind IN ('intelligence_signal', 'agent_alert')
                 ORDER BY priority_score DESC, last_seen_at DESC
                 LIMIT 200
                """,
                *params,
            )

        rows = await _run_with_db_scope(pool, user or {}, _load)
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        public_row = _row_to_public(row)
        metadata = _details(public_row.get("metadata"))
        severity = _severity(public_row.get("severity"))
        status = str(public_row.get("status") or "open")
        if status not in ITEM_STATUSES:
            status = "open"
        item_id = str(public_row.get("item_id") or "")
        kind = str(public_row.get("item_kind") or "intelligence_signal")
        is_agent_alert = kind == "agent_alert"
        escaped_item_id = item_id.replace("'", "''")
        source = str(metadata.get("source") or ("agent" if is_agent_alert else "intelligence"))
        occurrence_count = int(metadata.get("occurrence_count") or 1)
        intelligence = (
            metadata.get("intelligence")
            if isinstance(metadata.get("intelligence"), dict)
            else {}
        )
        decision_intelligence = metadata.get("decision_intelligence")
        if not isinstance(decision_intelligence, dict):
            decision_intelligence = intelligence.get("decision_intelligence")
        if not isinstance(decision_intelligence, dict):
            decision_intelligence = {}
        if decision_intelligence:
            intelligence = {
                **intelligence,
                "decision_intelligence": decision_intelligence,
            }
        items.append(
            {
                "id": item_id,
                "kind": kind,
                "tenant_id": public_row.get("tenant_id") or metadata.get("tenant_id"),
                "workspace_id": public_row.get("workspace_id")
                or metadata.get("workspace_id"),
                "domain": public_row.get("domain") or "Operacion",
                "module": metadata.get("module")
                or metadata.get("agent_name")
                or ("Agente monitor" if is_agent_alert else "Intelligence Engine"),
                "cartridge": public_row.get("cartridge_id") or "platform",
                "source_dataset": public_row.get("source_dataset")
                or "intelligence_signals",
                "source_system": metadata.get("source_system")
                or public_row.get("cartridge_id")
                or "platform",
                "dataset": metadata.get("dataset")
                or public_row.get("source_dataset")
                or "intelligence_signals",
                "gold_table": metadata.get("gold_table"),
                "freshness_at": metadata.get("freshness_at"),
                "freshness_field": metadata.get("freshness_field"),
                "data_status": metadata.get("data_status") or "gold_ready",
                "evidence_pack_id": metadata.get("evidence_pack_id"),
                "evidence_pack": metadata.get("evidence_pack")
                if isinstance(metadata.get("evidence_pack"), dict)
                else {},
                "entity_kind": public_row.get("entity_kind") or "Entidad",
                "entity_id": public_row.get("entity_id") or "",
                "entity_label": public_row.get("entity_label")
                or public_row.get("entity_id")
                or "Entidad",
                "anomaly_type": public_row.get("anomaly_type") or "intelligence_signal",
                "severity": severity,
                "severity_weight": SEVERITY_WEIGHT[severity],
                "detected_at": public_row.get("last_seen_at")
                or public_row.get("first_seen_at")
                or "",
                "details": metadata.get("details")
                if isinstance(metadata.get("details"), dict)
                else {},
                "title": public_row.get("title")
                or ("Alerta de agente monitor" if is_agent_alert else "Senal de inteligencia operativa"),
                "description": metadata.get("description")
                or public_row.get("title")
                or ("Alerta advisory de agente monitor" if is_agent_alert else "Senal de inteligencia operativa"),
                "recommendation": metadata.get("recommendation")
                or "Revisar evidencia y seleccionar una opcion supervisada.",
                "root_cause": metadata.get("root_cause")
                or ("Hipotesis generada por agente monitor." if is_agent_alert else "Desviacion contra baseline."),
                "impact": metadata.get("impact")
                or "Impacto operativo pendiente de validar.",
                "sql": metadata.get("sql")
                or (
                    f"SELECT * FROM control_room_items WHERE item_id = '{escaped_item_id}'"
                    if is_agent_alert
                    else f"SELECT * FROM intelligence_signals WHERE signal_id = '{escaped_item_id}'"
                ),
                "status": status,
                "decision_id": public_row.get("decision_id"),
                "impact_estimate": public_row.get("impact_estimate"),
                "impact_currency": public_row.get("impact_currency"),
                "confidence": public_row.get("confidence"),
                "priority_score": public_row.get("priority_score"),
                "thresholds_applied": metadata.get("thresholds_applied") or [],
                "threshold_state": metadata.get("threshold_state") or "default",
                "control_origin": metadata.get("control_origin"),
                "capabilities": metadata.get("capabilities")
                if isinstance(metadata.get("capabilities"), dict)
                else {},
                "math_provenance": metadata.get("math_provenance")
                if isinstance(metadata.get("math_provenance"), dict)
                else {},
                "monte_carlo": metadata.get("monte_carlo")
                if isinstance(metadata.get("monte_carlo"), dict)
                else {},
                "priority": metadata.get("priority")
                if isinstance(metadata.get("priority"), dict)
                else {},
                "selected_option_id": public_row.get("selected_option_id")
                or metadata.get("selected_option_id"),
                "execution_status": public_row.get("execution_status")
                or metadata.get("execution_status"),
                "alert_state": metadata.get("alert_state")
                if isinstance(metadata.get("alert_state"), dict)
                else {},
                "control_state": metadata.get("control_state")
                if isinstance(metadata.get("control_state"), dict)
                else {},
                "lessons": metadata.get("lessons"),
                "learned_rules": metadata.get("learned_rules"),
                "lesson_applications": metadata.get("lesson_applications")
                if isinstance(metadata.get("lesson_applications"), list)
                else [],
                "decision_intelligence": decision_intelligence,
                "intelligence": intelligence,
                "source": source,
                "advisory": bool(metadata.get("advisory")) or is_agent_alert,
                "agent_id": metadata.get("agent_id"),
                "agent_run_id": metadata.get("agent_run_id"),
                "deduped": occurrence_count > 1,
                "occurrence_count": occurrence_count,
                "hypothesis": metadata.get("hypothesis"),
                "expected_outcome": metadata.get("expected_outcome"),
                "first_seen_at": public_row.get("first_seen_at"),
                "last_seen_at": public_row.get("last_seen_at"),
                "resolved_at": public_row.get("resolved_at"),
                "dismissed_at": public_row.get("dismissed_at"),
            }
        )
    return [_with_omega(item) for item in items]


@_bind_to_core
async def _cleanup_obsolete_source_state_items(
    user: dict | None,
    sources: list[dict[str, Any]],
    current_items: list[dict[str, Any]],
) -> None:
    by_cartridge: dict[str, set[str]] = {}
    for source in sources:
        cartridge_id = str(
            source.get("cartridge") or source.get("connector_id") or ""
        ).strip()
        dataset = str(source.get("dataset") or "").strip()
        if cartridge_id and dataset:
            by_cartridge.setdefault(cartridge_id, set()).add(dataset)
    if not by_cartridge:
        return

    state_ids_by_cartridge: dict[str, set[str]] = {
        cartridge_id: set() for cartridge_id in by_cartridge
    }
    for item in current_items:
        if item.get("kind") != "source_state":
            continue
        cartridge_id = str(
            item.get("cartridge") or item.get("connector_id") or ""
        ).strip()
        item_id = str(item.get("id") or "").strip()
        if cartridge_id in state_ids_by_cartridge and item_id:
            state_ids_by_cartridge[cartridge_id].add(item_id)

    _, workspace_id = _workspace_scope(user)
    try:
        pool = await auth.pool()
        async def _delete_obsolete(conn: Any, _tenant_id: str | None, _workspace_id: str) -> None:
            for cartridge_id, datasets in by_cartridge.items():
                await conn.execute(
                    """
                    DELETE FROM control_room_items
                     WHERE workspace_id = $1
                       AND cartridge_id = $2
                       AND item_kind = 'source_state'
                       AND (
                           NOT (source_dataset = ANY($3::text[]))
                           OR NOT (item_id = ANY($4::text[]))
                       )
                    """,
                    workspace_id,
                    cartridge_id,
                    sorted(datasets),
                    sorted(state_ids_by_cartridge.get(cartridge_id, set())),
                )

        await _run_with_db_scope(pool, user or {}, _delete_obsolete)
    except Exception:
        return


@_bind_to_core
async def _collect_items(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
    include_source_state_items: bool = False,
    persist: bool = False,
    use_catalog: bool = True,
) -> dict[str, Any]:
    if use_catalog:
        installations = await _installed_cartridges(user)
    else:
        installations = [
            {
                "cartridge_id": module.cartridge,
                "installation_status": "ready",
                "current_step": "test_registry",
                "label": module.label,
                "category": "platform" if module.operational else "cartridge",
            }
            for module in MODULES
            if module.sources
        ]
    installation_by_cartridge = {
        str(row.get("cartridge_id")): row
        for row in installations
        if str(row.get("cartridge_id") or "").strip()
    }
    installed = set(installation_by_cartridge)
    active = {
        cartridge_id
        for cartridge_id, row in installation_by_cartridge.items()
        if str(row.get("installation_status") or "ready")
        in ACTIVE_INSTALLATION_STATUSES
    }
    modules = [module for module in MODULES if module.cartridge in installed]

    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    rows_by_dataset: dict[str, list[dict[str, Any]]] = {}
    threshold_rows = (
        await _load_threshold_rows(user)
        if use_catalog and (persist or include_source_state_items)
        else []
    )
    thresholds = _threshold_map(threshold_rows)
    for module in modules:
        installation = installation_by_cartridge.get(module.cartridge, {})
        if module.cartridge not in active:
            for source in module.sources:
                source_status = _source_status_payload(
                    source,
                    "blocked",
                    count=0,
                    checked_at=datetime.now(UTC).isoformat(),
                    error=str(
                        installation.get("error_message")
                        or installation.get("current_step")
                        or ""
                    ),
                )
                rows_by_dataset[source.dataset] = []
                sources.append(source_status)
                if include_source_state_items:
                    source_item = _source_state_item(
                        source,
                        "blocked",
                        source_status.get("error"),
                        source_status.get("data_readiness"),
                        source_status.get("readiness_reason"),
                        source_status.get("readiness_blockers"),
                    )
                    if source_item:
                        items.append(source_item)
            continue
        for source in module.sources:
            if not _show_known_non_ready_sources() and not _source_has_ready_contract(
                source
            ):
                source_status = _source_status_payload(
                    source,
                    "ok",
                    count=0,
                    checked_at=datetime.now(UTC).isoformat(),
                )
                rows_by_dataset[source.dataset] = []
                sources.append(source_status)
                if include_source_state_items:
                    source_item = _source_state_item(
                        source,
                        source_status["status"],
                        source_status.get("error"),
                        source_status.get("data_readiness"),
                        source_status.get("readiness_reason"),
                        source_status.get("readiness_blockers"),
                    )
                    if source_item:
                        items.append(source_item)
                continue
            rows, source_status = await _fetch_source(
                source, user, fetcher, limit_per_source
            )
            rows_by_dataset[source.dataset] = (
                rows if source_status["status"] == "ok" else []
            )
            sources.append(source_status)
            if include_source_state_items:
                source_item = _source_state_item(
                    source,
                    source_status["status"],
                    source_status.get("error"),
                    source_status.get("data_readiness"),
                    source_status.get("readiness_reason"),
                    source_status.get("readiness_blockers"),
                )
                if source_item:
                    items.append(source_item)
            if source_status["status"] != "ok":
                continue
            for row in rows:
                item = _normalize_row(source, row, thresholds)
                if item:
                    items.append(item)

    items = await _overlay_item_state(items, user, persist=persist)
    return {
        "items": items,
        "sources": sources,
        "installations": installations,
        "modules": modules,
        "financial": _financial_metrics(sources, rows_by_dataset),
        "thresholds": threshold_rows,
    }


@_bind_to_core
def _severity_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in SEVERITY_WEIGHT}
    for item in items:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return counts


@_bind_to_core
def _source_rollup_status(module_sources: list[dict[str, Any]]) -> str:
    if not module_sources:
        return "no_sources"
    priority = [
        "no_permission",
        "blocked",
        "invalid_schema",
        "unavailable",
        "missing",
        "stub",
        "partial",
        "empty",
        "ok",
    ]
    statuses = {
        "ok"
        if source.get("operationally_ready")
        else str(source.get("data_readiness") or source.get("status") or "no_sources")
        for source in module_sources
    }
    if statuses == {"ok"}:
        return "ok"
    for status in priority:
        if status in statuses:
            return status
    return "attention"


@_bind_to_core
def _domain_payload(
    domain: str,
    modules: list[ControlRoomModule],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    domain_items = [item for item in items if item["domain"] == domain]
    domain_modules = []
    for module in modules:
        if module.domain != domain and not any(
            source.domain == domain for source in module.sources
        ):
            continue
        module_has_sources = any(
            source.get("module_id") == module.visible_id
            and source.get("domain") == domain
            for source in sources
        )
        module_has_items = any(
            item.get("module_id", item.get("cartridge")) == module.visible_id
            for item in domain_items
        )
        if module_has_sources or module_has_items or _show_known_non_ready_sources():
            domain_modules.append(module)
    module_payload = []
    for module in domain_modules:
        module_sources = [
            source
            for source in sources
            if source.get("module_id") == module.visible_id
            and source.get("domain") == domain
        ]
        module_items = [
            item
            for item in domain_items
            if item.get("module_id", item.get("cartridge")) == module.visible_id
        ]
        source_count = sum(int(source.get("count") or 0) for source in module_sources)
        source_status = _source_rollup_status(module_sources)
        data_readiness = _module_data_readiness(module_sources)
        module_payload.append(
            {
                "id": module.visible_id,
                "connector_id": module.cartridge,
                "label": module.label,
                "domain": domain,
                "accent": module.accent,
                "description": module.description,
                "item_count": len(module_items),
                "critical_count": sum(
                    1 for item in module_items if item["severity"] == "critical"
                ),
                "source_status": source_status,
                "data_readiness": data_readiness,
                "operationally_ready": data_readiness == "ready",
                "kpis": [
                    {
                        "label": "Registros fuente",
                        "value": source_count,
                        "tone": "neutral",
                        "bad": data_readiness != "ready"
                        and source_status != "no_sources",
                        "sql": " UNION ALL ".join(
                            f"SELECT COUNT(*) AS registros, '{source['dataset']}' AS dataset FROM {source['dataset']}"
                            for source in module_sources
                        )
                        or "-- sin fuente materializada",
                    },
                    {
                        "label": "Items abiertos",
                        "value": sum(
                            1
                            for item in module_items
                            if item["status"] not in TERMINAL_ITEM_STATUSES
                        ),
                        "tone": "attention",
                        "bad": any(
                            item["status"] not in TERMINAL_ITEM_STATUSES
                            for item in module_items
                        ),
                        "sql": f"SELECT * FROM control_room_items WHERE cartridge_id = '{module.cartridge}' AND domain = '{domain}' AND status NOT IN ('approved','dismissed','resolved')",
                    },
                ],
            }
        )
    return {
        "id": domain.lower().replace(" ", "_"),
        "label": domain,
        "accent": DOMAIN_ACCENTS.get(domain, "#64748b"),
        "item_count": len(domain_items),
        "critical_count": sum(
            1 for item in domain_items if item["severity"] == "critical"
        ),
        "cartridge_count": len({module.visible_id for module in domain_modules}),
        "modules": module_payload,
    }


@_bind_to_core
async def dashboard(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
    persist: bool = True,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC)
    payload = await _collect_items(
        user,
        fetcher=fetcher,
        limit_per_source=limit_per_source,
        include_source_state_items=True,
        persist=persist,
    )
    items = payload["items"]
    sources = payload["sources"]
    modules = payload["modules"]
    installations = payload["installations"]
    financial = payload["financial"]
    thresholds = payload.get("thresholds") or []
    active_cartridges = {
        str(row.get("cartridge_id") or "").strip()
        for row in installations
        if str(row.get("cartridge_id") or "").strip()
        and str(row.get("installation_status") or "ready").strip().lower()
        in ACTIVE_INSTALLATION_STATUSES
    }
    lesson_rows = await _load_lesson_rows(user, limit=200)
    lesson_summary = _lesson_insights(lesson_rows)
    items = _attach_lessons_to_items(items, lesson_rows)
    if persist:
        await _cleanup_obsolete_source_state_items(user, sources, items)
    if persist:
        known_ids = {str(item.get("id")) for item in items}
        for item in await _persisted_intelligence_items(user):
            item_cartridge = str(item.get("cartridge") or "").strip()
            if item_cartridge != "platform" and item_cartridge not in active_cartridges:
                continue
            if str(item.get("id")) not in known_ids:
                items.append(item)
                known_ids.add(str(item.get("id")))
    alerts_payload = _alert_payload(items)
    alerts = alerts_payload["alerts"]
    alert_summary = alerts_payload["summary"]

    by_severity = _severity_counts(items)
    by_cartridge: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for item in items:
        by_cartridge[item["cartridge"]] = by_cartridge.get(item["cartridge"], 0) + 1
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1

    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    open_decisions = 0
    try:
        async def _count_decisions(conn: Any, _tenant_id: str | None, _workspace_id: str) -> int:
            return int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM decisions WHERE workspace_id = $1 AND status = 'open'",
                    workspace_id,
                )
                or 0
            )

        open_decisions = await _run_with_db_scope(pool, user or {}, _count_decisions)
    except Exception:
        open_decisions = 0

    installation_by_cartridge = {
        str(row.get("cartridge_id")): row
        for row in installations
        if str(row.get("cartridge_id") or "").strip()
    }
    visible_module_ids = {
        str(source.get("module_id") or "").strip()
        for source in sources
        if str(source.get("module_id") or "").strip()
    } | {
        str(item.get("module_id") or item.get("cartridge") or "").strip()
        for item in items
        if str(item.get("module_id") or item.get("cartridge") or "").strip()
    }
    modules_for_payload = (
        modules
        if _show_known_non_ready_sources()
        else [module for module in modules if module.visible_id in visible_module_ids]
    )
    cartridges = []
    for module in modules_for_payload:
        row = installation_by_cartridge.get(module.cartridge, {})
        cartridge_id = module.visible_id
        module_items = [
            item
            for item in items
            if item.get("module_id", item.get("cartridge")) == module.visible_id
        ]
        module_sources = [
            source for source in sources if source.get("module_id") == module.visible_id
        ]
        source_status = _source_rollup_status(module_sources)
        data_readiness = _module_data_readiness(module_sources)
        installation_status = str(row.get("installation_status") or "ready")
        cartridges.append(
            {
                "id": cartridge_id,
                "connector_id": module.cartridge,
                "connector_label": row.get("label") or module.cartridge,
                "label": module.label,
                "domain": module.domain,
                "accent": module.accent,
                "description": module.description,
                "status": installation_status,
                "current_step": row.get("current_step"),
                "active": installation_status in ACTIVE_INSTALLATION_STATUSES,
                "operational": bool(module.operational),
                "item_count": len(module_items),
                "critical_count": sum(
                    1 for item in module_items if item["severity"] == "critical"
                ),
                "source_status": source_status,
                "data_readiness": data_readiness,
                "operationally_ready": data_readiness == "ready",
                "datasets": module_sources,
            }
        )

    domain_labels = list(DOMAIN_ORDER)
    for module in modules_for_payload:
        if module.domain not in domain_labels:
            domain_labels.append(module.domain)
        for source in module.sources:
            if source.domain not in domain_labels:
                domain_labels.append(source.domain)
    domains = [
        _domain_payload(domain, modules_for_payload, items, sources)
        for domain in domain_labels
    ]
    if not _show_known_non_ready_sources():
        domains = [
            domain
            for domain in domains
            if domain.get("modules") or int(domain.get("item_count") or 0) > 0
        ]
    data_readiness = _readiness_counts(sources)
    data_ready_modules = [
        row for row in cartridges if row["active"] and row.get("operationally_ready")
    ]
    partial_modules = [
        row
        for row in cartridges
        if row["active"] and row.get("data_readiness") == "partial"
    ]
    stub_modules = [
        row
        for row in cartridges
        if row["active"] and row.get("data_readiness") == "stub"
    ]

    return {
        "meta": {
            "generated_at": generated_at.isoformat(),
            "refresh_interval_seconds": CONTROL_ROOM_REFRESH_INTERVAL_SECONDS,
            "live_mode": "polling",
            "source_count": len(sources),
            "item_count": len(items),
            # Runtime confidence: the UI surfaces the real version/env and
            # distinguishes supervised execution from external ERP write-back.
            "version": app_version(),
            "app_env": os.environ.get("APP_ENV", "production").strip().lower(),
            "execution_mode": "supervised_execution",
            "supervised_execution_enabled": True,
            "external_writeback_enabled": _external_writeback_enabled(),
            "write_back_enabled": _external_writeback_enabled(),
        },
        "workspace": {
            "tenant_id": (user or {}).get("active_tenant_id")
            or (user or {}).get("tenant_id"),
            "workspace_id": workspace_id,
        },
        "period": generated_at.strftime("%B %Y"),
        "omega_steps": OMEGA_STEPS,
        "summary": {
            "total_items": len(items),
            "total_anomalies": sum(1 for item in items if item["kind"] == "anomaly"),
            "control_items": sum(1 for item in items if item["kind"] != "anomaly"),
            "by_severity": by_severity,
            "by_cartridge": by_cartridge,
            "by_domain": by_domain,
            "critical": by_severity.get("critical", 0),
            "attention": by_severity.get("high", 0) + by_severity.get("medium", 0),
            "open_decisions": open_decisions,
            "active_connectors": len(
                {
                    row["connector_id"]
                    for row in cartridges
                    if row["active"] and not row["operational"]
                }
            ),
            "active_modules": len(
                [row for row in cartridges if row["active"] and not row["operational"]]
            ),
            "active_cartridges": len(
                [row for row in cartridges if row["active"] and not row["operational"]]
            ),
            "operational_cartridges": len(
                [row for row in cartridges if row["active"] and row["operational"]]
            ),
            "source_states": {
                status: sum(1 for source in sources if source["status"] == status)
                for status in [
                    "ok",
                    "empty",
                    "missing",
                    "unavailable",
                    "invalid_schema",
                    "blocked",
                    "no_permission",
                ]
            },
            "data_readiness": data_readiness,
            "data_ready_sources": data_readiness.get("ready", 0),
            "data_ready_modules": len(data_ready_modules),
            "partial_modules": len(partial_modules),
            "stub_modules": len(stub_modules),
            "cycle_counts": _cycle_counts(items),
            "financial": financial,
            "thresholds": {
                "active": sum(1 for row in thresholds if row.get("enabled", True)),
                "total": len(thresholds),
                "by_cartridge": {
                    cartridge_id: sum(
                        1
                        for row in thresholds
                        if row.get("cartridge_id") == cartridge_id
                    )
                    for cartridge_id in sorted(
                        {
                            str(row.get("cartridge_id") or "").strip()
                            for row in thresholds
                            if str(row.get("cartridge_id") or "").strip()
                        }
                    )
                },
                "items_with_thresholds": sum(
                    1 for item in items if item.get("thresholds_applied")
                ),
            },
            "lessons": lesson_summary,
            "alerts": alert_summary,
        },
        "domains": domains,
        "cartridges": cartridges,
        "sources": sources,
        "alerts": alerts,
        "items": items,
    }


@_bind_to_core
async def list_anomalies(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
) -> dict[str, Any]:
    payload = await _collect_items(
        user,
        fetcher=fetcher,
        limit_per_source=limit_per_source,
        include_source_state_items=False,
        persist=False,
        use_catalog=True,
    )
    anomalies = [item for item in payload["items"] if item["kind"] == "anomaly"]
    return {"anomalies": anomalies, "sources": payload["sources"]}


@_bind_to_core
async def summary(
    user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows
) -> dict[str, Any]:
    collected = await _collect_items(
        user,
        fetcher=fetcher,
        include_source_state_items=False,
        persist=False,
        use_catalog=True,
    )
    items = [item for item in collected["items"] if item["kind"] == "anomaly"]
    by_severity = _severity_counts(items)
    by_cartridge: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for item in items:
        by_cartridge[item["cartridge"]] = by_cartridge.get(item["cartridge"], 0) + 1
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1
    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    open_decisions = 0
    if workspace_id:
        async def _count_decisions(conn: Any, _tenant_id: str | None, _workspace_id: str) -> int:
            return int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM decisions WHERE workspace_id = $1 AND status = 'open'",
                    workspace_id,
                )
                or 0
            )

        open_decisions = await _run_with_db_scope(pool, user or {}, _count_decisions)
    lesson_rows = await _load_lesson_rows(user, limit=100)
    items = _attach_lessons_to_items(items, lesson_rows)
    alert_summary = _alert_payload(items)["summary"]
    return {
        "total_anomalies": len(items),
        "by_severity": by_severity,
        "by_cartridge": by_cartridge,
        "by_domain": by_domain,
        "open_decisions": open_decisions,
        "sources": collected["sources"],
        "financial": collected["financial"],
        "thresholds": {
            "active": sum(
                1 for row in collected.get("thresholds", []) if row.get("enabled", True)
            ),
            "total": len(collected.get("thresholds", [])),
        },
        "lessons": _lesson_insights(lesson_rows),
        "alerts": alert_summary,
    }


_ITEM_STATUSES = (
    "open",
    "in_review",
    "decision_created",
    "approved",
    "dismissed",
    "resolved",
)
_ITEM_SEVERITIES = ("critical", "high", "medium", "low")


@_bind_to_core
async def ops_summary(user: dict | None) -> dict[str, Any]:
    """Lightweight operational summary for the active workspace.

    Reads ONLY the persisted control-room tables with cheap COUNT/GROUP BY
    queries — it never runs the heavy dataset-fetch path that ``dashboard``
    does, so it is safe to poll. Workspace-scoped, no secrets. Useful to
    answer "does this workspace have data, alert pressure, lessons and
    action executions?" without rendering the whole cockpit.

    Note: the live alert queue and source states are computed from datasets
    in ``dashboard`` — here ``items_by_severity`` (open items) is the cheap,
    persisted proxy for alert pressure.
    """
    import os as _os

    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()

    async def _load_counts(conn: Any, _tenant_id: str | None, _workspace_id: str) -> dict[str, Any]:
        status_rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM control_room_items "
            "WHERE workspace_id = $1 GROUP BY status",
            workspace_id,
        )
        severity_rows = await conn.fetch(
            "SELECT severity, COUNT(*) AS n FROM control_room_items "
            "WHERE workspace_id = $1 AND status = 'open' GROUP BY severity",
            workspace_id,
        )
        exec_rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM control_room_action_executions "
            "WHERE workspace_id = $1 GROUP BY status",
            workspace_id,
        )
        lessons_total = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM control_room_lessons WHERE workspace_id = $1",
                workspace_id,
            )
            or 0
        )
        thresholds_total = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM control_room_thresholds WHERE workspace_id = $1 AND enabled = TRUE",
                workspace_id,
            )
            or 0
        )
        last_item_at = await conn.fetchval(
            "SELECT MAX(last_seen_at) FROM control_room_items WHERE workspace_id = $1",
            workspace_id,
        )
        return {
            "status_rows": status_rows,
            "severity_rows": severity_rows,
            "exec_rows": exec_rows,
            "lessons_total": lessons_total,
            "thresholds_total": thresholds_total,
            "last_item_at": last_item_at,
        }

    counts = await _run_with_db_scope(pool, user or {}, _load_counts)
    status_rows = counts["status_rows"]
    items_by_status = {s: 0 for s in _ITEM_STATUSES}
    for row in status_rows:
        items_by_status[str(row["status"])] = int(row["n"])
    total_items = sum(items_by_status.values())

    severity_rows = counts["severity_rows"]
    open_by_severity = {s: 0 for s in _ITEM_SEVERITIES}
    for row in severity_rows:
        open_by_severity[str(row["severity"])] = int(row["n"])

    exec_rows = counts["exec_rows"]
    executions_by_status = {str(row["status"]): int(row["n"]) for row in exec_rows}

    lessons_total = counts["lessons_total"]
    thresholds_total = counts["thresholds_total"]
    last_item_at = counts["last_item_at"]

    app_env = _os.environ.get("APP_ENV", "production").strip().lower()
    writeback_enabled = _external_writeback_enabled()
    return {
        "version": app_version(),
        "app_env": app_env,
        "active_workspace": workspace_id,
        "tenant": tenant_id,
        "items": {"total": total_items, "by_status": items_by_status},
        "open_items_by_severity": open_by_severity,
        "action_executions": executions_by_status,
        "lessons": lessons_total,
        "thresholds_active": thresholds_total,
        "last_item_seen_at": last_item_at.isoformat() if last_item_at else None,
        "execution_mode": "supervised_execution",
        "supervised_execution_enabled": True,
        "external_writeback_enabled": writeback_enabled,
        "write_back_enabled": writeback_enabled,
        "writeback_blocked_by_default": not writeback_enabled,
        "external_writeback_blocked_by_default": not writeback_enabled,
        "has_demo_seed": _os.environ.get("CONTROL_ROOM_DEMO_SEED", "").strip().lower()
        in {"1", "true", "yes", "on"},
    }


@_bind_to_core
async def get_item(
    item_id: str, user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows
) -> dict[str, Any]:
    payload = await dashboard(user, fetcher=fetcher, persist=True)
    for item in payload["items"]:
        if item["id"] == item_id:
            return item
    raise HTTPException(404, "control room item not found")


@_bind_to_core
async def get_anomaly(
    anomaly_id: str, user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows
) -> dict[str, Any]:
    item = await get_item(anomaly_id, user, fetcher=fetcher)
    if item["kind"] != "anomaly":
        raise HTTPException(404, "anomaly not found")
    return item


@_bind_to_core
async def _record_action_execution(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    mode: str,
    status: str,
    payload: dict[str, Any],
    result: dict[str, Any],
    error: str | None = None,
    critical: bool = False,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO control_room_action_executions (
                tenant_id, workspace_id, item_id, template_id, mode, status,
                payload, result, error, actor_id, actor_email, completed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11, NOW())
            RETURNING *
            """,
            tenant_id,
            workspace_id,
            item["id"],
            template.get("template_id"),
            mode,
            status,
            json.dumps(payload),
            json.dumps(result),
            error,
            user.get("id"),
            user.get("email"),
        )
        return _row_to_public(row)
    except Exception:
        if critical:
            raise
        return {
            "id": None,
            "workspace_id": workspace_id,
            "item_id": item["id"],
            "template_id": template.get("template_id"),
            "mode": mode,
            "status": status,
            "payload": payload,
            "result": result,
            "error": error,
            "actor_email": user.get("email"),
            "created_at": datetime.now(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
        }


@_bind_to_core
async def list_alerts(
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    payload = await dashboard(user, fetcher=fetcher, persist=True)
    return {
        "alerts": payload.get("alerts") or [],
        "summary": payload.get("summary", {}).get("alerts")
        or _alert_payload(payload.get("items") or [])["summary"],
        "generated_at": payload.get("meta", {}).get("generated_at"),
    }


__all__ = (
    "_ITEM_STATUSES",
    "_ITEM_SEVERITIES",
    "_is_production_env",
    "_internal_headers",
    "_mcp_payload",
    "query_dataset_rows",
    "_details",
    "_encode_id",
    "_severity",
    "_num",
    "_copy_for",
    "_workspace_scope",
    "_workspace_id",
    "_actor_id",
    "_can_read_workspace_wide",
    "_module_by_cartridge",
    "_module_by_visible_id",
    "_all_sources",
    "_allowed_from_user",
    "_attach_thresholds",
    "_installed_cartridges",
    "_fetch_source",
    "_base_item",
    "_normalize_standard_anomaly",
    "_normalize_replicon_allocation",
    "_normalize_replicon_timesheet",
    "_normalize_replicon_pnl",
    "_normalize_replicon_skill_gap",
    "_normalize_s4_revenue",
    "_normalize_s4_backlog",
    "_normalize_s4_supplier_spend",
    "_normalize_row",
    "_money_sum",
    "_ratio",
    "_financial_metrics",
    "_cycle_counts",
    "_lessons_for_item",
    "_collect_items",
    "_severity_counts",
    "_source_rollup_status",
    "_domain_payload",
    "dashboard",
    "list_anomalies",
    "summary",
    "ops_summary",
    "get_item",
    "get_anomaly",
    "_record_action_execution",
    "list_alerts",
)
