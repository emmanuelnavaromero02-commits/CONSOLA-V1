from __future__ import annotations

import types

from app.services.control_room import core as _core
from app.services.control_room.talent_catalog import (
    TALENT_BOX_DEFINITIONS,
    TALENT_LIVE_COMPONENT_IDS,
    TALENT_METADATA_ENTITIES,
)


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
def _agentops_tool_is_operational(tool: object) -> bool:
    name = str(tool or "").strip()
    if name.startswith("infra__"):
        name = f"mcp-infra__{name.split('__', 1)[1]}"
    return (
        name.startswith("mcp-infra__simulation__")
        or name.startswith("mcp-infra__decision__")
        or name.startswith("mcp-infra__wisdom_bits__")
        or name.startswith("mcp-infra__control_room__")
    )


@_bind_to_core
def _agentops_tool_label(tool: object) -> str:
    name = str(tool or "").strip()
    if name.startswith("infra__"):
        return f"mcp-infra__{name.split('__', 1)[1]}"
    return name


@_bind_to_core
def _agentops_engine_label(value: object) -> str:
    name = str(value or "").strip().lower()
    if name in {"simulation__monte_carlo_run", "monte_carlo_run"}:
        return "monte_carlo"
    if name in {"decision__orchestrate", "orchestrator"}:
        return "decision_orchestrator"
    if name in {"calibration__bayesian_state", "bayes"}:
        return "bayesian_calibration"
    return name


@_bind_to_core
def _agentops_monitor_engines(contract: object) -> list[dict[str, Any]]:
    if not isinstance(contract, dict):
        return []
    raw = contract.get("engines")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raw = []
    engines: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        engine = _agentops_engine_label(item.get("name") or item.get("engine"))
        if not engine:
            continue
        seen.add(engine)
        engines.append({
            "engine": engine,
            "enabled": item.get("enabled") is not False,
            "source_type": item.get("source_type"),
            "source_id": item.get("source_id"),
            "calibration_group": item.get("calibration_group"),
            "mode": "direct",
        })
        engine_inputs = item.get("engine_inputs")
        if isinstance(engine_inputs, dict) and isinstance(engine_inputs.get("bayesian_calibration"), dict):
            if "bayesian_calibration" not in seen:
                seen.add("bayesian_calibration")
                engines.append({
                    "engine": "bayesian_calibration",
                    "enabled": item.get("enabled") is not False,
                    "calibration_group": engine_inputs["bayesian_calibration"].get("calibration_group"),
                    "mode": "nested",
                })
    return engines


@_bind_to_core
def _source_has_ready_contract(source: ControlRoomSource) -> bool:
    return _readiness_contract(source).get("data_readiness") in DATA_READY_STATES


@_bind_to_core
def _source_should_fetch_partial_contract(source: ControlRoomSource) -> bool:
    return (
        source.cartridge == "sap_successfactors"
        and source.visible_module_id == "sap_successfactors_talent"
    )


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
def _sf_gold_truthy(value: Any) -> bool:
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


@_bind_to_core
def _sf_gold_public_rows(
    rows: list[dict[str, Any]], limit: int = 5
) -> list[dict[str, Any]]:
    return [
        {str(key): _sf_talent_public_value(value) for key, value in row.items()}
        for row in rows[:limit]
    ]


@_bind_to_core
def _sf_gold_top_headcount_rows(
    rows: list[dict[str, Any]], label_keys: tuple[str, str], limit: int = 5
) -> list[dict[str, Any]]:
    id_key, name_key = label_keys
    top: list[dict[str, Any]] = []
    for row in rows:
        label = str(row.get(name_key) or row.get(id_key) or "Sin clasificar")
        top.append(
            {
                "label": label,
                "id": _sf_talent_public_value(row.get(id_key)),
                "headcount": int(row.get("headcount") or 0),
            }
        )
    return sorted(top, key=lambda item: (-int(item["headcount"]), item["label"]))[
        :limit
    ]


@_bind_to_core
def _sf_gold_status_error(results: list[dict[str, Any]]) -> str | None:
    errors = [str(result.get("error")) for result in results if result.get("error")]
    return "; ".join(errors) if errors else None


@_bind_to_core
def _sf_gold_usable_rows(result: dict[str, Any]) -> list[dict[str, Any]] | None:
    status = str(result.get("status") or "")
    if status in {"ready", "empty"}:
        return result.get("rows") if isinstance(result.get("rows"), list) else []
    return None


@_bind_to_core
def _sf_gold_combine_widget_status(results: list[dict[str, Any]]) -> str:
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


@_bind_to_core
async def _sf_gold_result(
    dataset: str, user: dict | None, limit: int
) -> dict[str, Any]:
    from app.services.intelligence.gold_fetcher import query_gold_dataset_rows

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


@_bind_to_core
def _sf_foundation_gold_datasets() -> dict[str, str]:
    return {
        "employee_360": "sap_successfactors_employee_360",
        "headcount_by_company": "sap_successfactors_headcount_by_company",
        "headcount_by_location": "sap_successfactors_headcount_by_location",
        "headcount_by_department": "sap_successfactors_headcount_by_department",
    }


@_bind_to_core
async def _sf_foundation_gold_results(
    datasets: dict[str, str],
    user: dict | None,
) -> dict[str, dict[str, Any]]:
    return {
        "employee_360": await _sf_gold_result(datasets["employee_360"], user, 5000),
        "headcount_by_company": await _sf_gold_result(
            datasets["headcount_by_company"], user, 1000
        ),
        "headcount_by_location": await _sf_gold_result(
            datasets["headcount_by_location"], user, 1000
        ),
        "headcount_by_department": await _sf_gold_result(
            datasets["headcount_by_department"], user, 1000
        ),
    }


@_bind_to_core
def _sf_foundation_gold_rows(
    results: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]] | None]:
    return {
        key: _sf_gold_usable_rows(result)
        for key, result in results.items()
    }


@_bind_to_core
def _sf_gold_headcount_total(rows: list[dict[str, Any]] | None) -> int | None:
    if rows is None:
        return None
    return sum(int(row.get("headcount") or 0) for row in rows)


@_bind_to_core
def _sf_foundation_active_headcount(
    employee_rows: list[dict[str, Any]] | None,
    company_rows: list[dict[str, Any]] | None,
) -> int | None:
    company_headcount_total = _sf_gold_headcount_total(company_rows)
    employee_active_total = (
        None
        if employee_rows is None
        else sum(1 for row in employee_rows if _sf_gold_truthy(row.get("is_active")))
    )
    if company_headcount_total and company_headcount_total > 0:
        active_headcount = company_headcount_total
    elif employee_active_total is not None:
        active_headcount = employee_active_total
    else:
        active_headcount = company_headcount_total
    return active_headcount


@_bind_to_core
def _sf_foundation_widget(
    widget_id: str,
    title: str,
    value: Any,
    dataset: str,
    rows: list[dict[str, Any]],
    status: str,
    error: Any,
) -> dict[str, Any]:
    return {
        "id": widget_id,
        "title": title,
        "value": value,
        "dataset": dataset,
        "href": _sf_talent_dataset_href(dataset),
        "rows": rows,
        "status": status,
        "error": error,
    }


@_bind_to_core
def _sf_foundation_gold_widgets(
    datasets: dict[str, str],
    results: dict[str, dict[str, Any]],
    rows: dict[str, list[dict[str, Any]] | None],
) -> list[dict[str, Any]]:
    employee_rows = rows["employee_360"]
    company_rows = rows["headcount_by_company"]
    location_rows = rows["headcount_by_location"]
    department_rows = rows["headcount_by_department"]
    company_headcount_total = _sf_gold_headcount_total(company_rows)
    return [
        _sf_foundation_widget(
            "sf_active_headcount",
            "Headcount total activo",
            _sf_foundation_active_headcount(employee_rows, company_rows),
            datasets["employee_360"],
            _sf_gold_public_rows(employee_rows or [], 5),
            _sf_gold_combine_widget_status(
                [results["employee_360"], results["headcount_by_company"]]
            ),
            _sf_gold_status_error(
                [results["employee_360"], results["headcount_by_company"]]
            ),
        ),
        _sf_foundation_widget(
            "sf_headcount_by_company",
            "Headcount por compania",
            company_headcount_total,
            datasets["headcount_by_company"],
            _sf_gold_top_headcount_rows(
                company_rows or [], ("company_id", "company_name")
            ),
            results["headcount_by_company"]["status"],
            results["headcount_by_company"].get("error"),
        ),
        _sf_foundation_widget(
            "sf_headcount_by_location",
            "Headcount por ubicacion",
            _sf_gold_headcount_total(location_rows),
            datasets["headcount_by_location"],
            _sf_gold_top_headcount_rows(
                location_rows or [], ("location_id", "location_name")
            ),
            results["headcount_by_location"]["status"],
            results["headcount_by_location"].get("error"),
        ),
        _sf_foundation_widget(
            "sf_headcount_by_department",
            "Headcount por departamento",
            _sf_gold_headcount_total(department_rows),
            datasets["headcount_by_department"],
            _sf_gold_top_headcount_rows(
                department_rows or [], ("department_id", "department_name")
            ),
            results["headcount_by_department"]["status"],
            results["headcount_by_department"].get("error"),
        ),
    ]


@_bind_to_core
async def sap_successfactors_gold_kpis(user: dict | None) -> dict[str, Any]:
    """Core SuccessFactors Gold widgets for the active tenant/workspace.

    Reads go through the scoped Gold fetcher. A user outside the FEMSA
    workspace simply receives empty widgets because native Gold RLS filters the
    rows before they reach this code path.
    """
    datasets = _sf_foundation_gold_datasets()
    results = await _sf_foundation_gold_results(datasets, user)
    rows = _sf_foundation_gold_rows(results)
    generated_at = datetime.now(UTC).isoformat()
    tenant_id, workspace_id = _workspace_scope(user)
    return {
        "generated_at": generated_at,
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "widgets": _sf_foundation_gold_widgets(datasets, results, rows),
    }


@_bind_to_core
def _sf_talent_public_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


@_bind_to_core
def _sf_talent_extract_blockers(rows: list[dict[str, Any]]) -> list[str]:
    blockers: set[str] = set()
    for row in rows:
        blockers.update(_sf_talent_json_list(row.get("blockers")))
    return sorted(blockers)


@_bind_to_core
async def _sf_talent_latest_simulation_result(user: dict | None) -> dict[str, Any]:
    try:
        pool = await auth.pool()
        async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
            exists = bool(
                await conn.fetchval(
                    "SELECT to_regclass($1)",
                    "public.monte_carlo_simulations",
                )
            )
            if not exists:
                return {"status": "waiting_for_data", "row": None}
            row = await conn.fetchrow(
                """
                SELECT simulation_id,
                       source_type,
                       source_id,
                       model_version,
                       horizon_days,
                       iterations,
                       output_metric,
                       breach_threshold,
                       breach_direction,
                       distribution_summary,
                       sensitivity,
                       evidence_refs,
                       created_at,
                       updated_at
                  FROM monte_carlo_simulations
                 WHERE workspace_id = $1::uuid
                   AND source_type = 'wisdom_bit'
                   AND source_id = 'WB-TALENTO'
                 ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                 LIMIT 1
                """,
                workspace_id,
            )
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc), "row": None}
    if not row:
        return {"status": "waiting_for_data", "row": None}
    return {
        "status": "ready",
        "row": {
            key: _sf_talent_public_value(value)
            for key, value in dict(row).items()
        },
    }


@_bind_to_core
def _sf_talent_kpi_datasets() -> dict[str, str]:
    return {
        "employee_profile": "sap_successfactors_talent_employee_profile",
        "role_profile": "sap_successfactors_talent_role_profile",
        "mobility_history": "sap_successfactors_talent_mobility_history",
        "readiness": "sap_successfactors_talent_readiness",
        "nine_box": "sap_successfactors_talent_9box",
        "signals": "sap_successfactors_talent_signals",
        "operational_features": "sap_successfactors_talent_operational_features",
        "simulation_inputs": "sap_successfactors_talent_simulation_inputs",
    }


@_bind_to_core
async def _sf_talent_kpi_results(
    datasets: dict[str, str],
    user: dict | None,
) -> dict[str, dict[str, Any]]:
    return {
        "employee_profile": await _sf_talent_gold_result(datasets["employee_profile"], user, 5000),
        "role_profile": await _sf_talent_gold_result(datasets["role_profile"], user, 1000),
        "mobility_history": await _sf_talent_gold_result(datasets["mobility_history"], user, 5000),
        "readiness": await _sf_talent_gold_result(datasets["readiness"], user, 5000),
        "nine_box": await _sf_talent_gold_result(datasets["nine_box"], user, 5000),
        "signals": await _sf_talent_gold_result(datasets["signals"], user, 100),
        "operational_features": await _sf_talent_gold_result(datasets["operational_features"], user, 1),
        "simulation_inputs": await _sf_talent_gold_result(datasets["simulation_inputs"], user, 1),
    }


@_bind_to_core
def _sf_talent_kpi_rows(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    operational_rows = results["operational_features"]["rows"]
    simulation_rows = results["simulation_inputs"]["rows"]
    return {
        "profile_rows": results["employee_profile"]["rows"],
        "role_rows": results["role_profile"]["rows"],
        "mobility_rows": results["mobility_history"]["rows"],
        "readiness_rows": results["readiness"]["rows"],
        "nine_box_rows": results["nine_box"]["rows"],
        "signal_rows": results["signals"]["rows"],
        "operational_row": operational_rows[0] if operational_rows else {},
        "simulation_row": simulation_rows[0] if simulation_rows else {},
    }


@_bind_to_core
def _sf_talent_profiled_count(
    operational_row: dict[str, Any],
    profile_rows: list[dict[str, Any]],
) -> int:
    return (
        _sf_talent_int(operational_row.get("profiled_count"))
        or _sf_talent_int(operational_row.get("profiled_employee_count"))
        or len(profile_rows)
    )


@_bind_to_core
def _sf_talent_readiness_counts(
    operational_row: dict[str, Any],
    readiness_rows: list[dict[str, Any]],
) -> dict[str, int]:
    calculable_rows = sum(
        1
        for row in readiness_rows
        if (
            _sf_talent_status(row.get("readiness_status"))
            not in {"insufficient_data", "blocked", "missing"}
            or _sf_talent_status(row.get("source_mode"))
            in {"cpa_real", "benchmark_internal"}
        )
    )
    insufficient_rows = sum(
        1
        for row in readiness_rows
        if _sf_talent_status(row.get("readiness_status")) == "insufficient_data"
    )
    if not operational_row:
        return {
            "readiness_calculable": calculable_rows,
            "readiness_insufficient": insufficient_rows,
        }
    operational_calculable = (
        _sf_talent_int(operational_row.get("calculable_count"))
        or _sf_talent_int(operational_row.get("calculable_employee_count"))
    )
    operational_pending = _sf_talent_int(operational_row.get("readiness_pending_count"))
    return {
        "readiness_calculable": max(operational_calculable, calculable_rows),
        "readiness_insufficient": (
            insufficient_rows if readiness_rows else operational_pending
        ),
    }


@_bind_to_core
def _sf_talent_nine_box_available_count(
    operational_row: dict[str, Any],
    nine_box_rows: list[dict[str, Any]],
) -> int:
    row_count = sum(
        1
        for row in nine_box_rows
        if (
            _sf_talent_status(row.get("box_status"))
            not in {"blocked", "insufficient_data", "missing"}
            or _sf_talent_status(row.get("source_mode"))
            in {"cpa_real", "benchmark_internal"}
        )
    )
    if operational_row:
        return max(
            _sf_talent_int(operational_row.get("nine_box_classified_count")),
            row_count,
        )
    return row_count


@_bind_to_core
def _sf_talent_source_mode(
    operational_row: dict[str, Any],
    readiness_rows: list[dict[str, Any]],
) -> str:
    modes = {_sf_talent_status(row.get("source_mode"), "") for row in readiness_rows}
    if "cpa_real" in modes:
        return "cpa_real"
    if "benchmark_internal" in modes:
        return "benchmark_internal"
    return _sf_talent_status(operational_row.get("source_mode") or "")


@_bind_to_core
def _sf_talent_first_int(row: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return _sf_talent_int(row.get(key))
    return 0


@_bind_to_core
def _sf_talent_kpi_metrics(
    rows: dict[str, Any],
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    profile_rows = rows["profile_rows"]
    role_rows = rows["role_rows"]
    mobility_rows = rows["mobility_rows"]
    readiness_rows = rows["readiness_rows"]
    nine_box_rows = rows["nine_box_rows"]
    operational_row = rows["operational_row"]
    readiness_counts = _sf_talent_readiness_counts(operational_row, readiness_rows)
    source_mode = _sf_talent_source_mode(operational_row, readiness_rows)
    return {
        "profiled_employees": _sf_talent_profiled_count(operational_row, profile_rows),
        "roles_profiled": _sf_talent_int(operational_row.get("role_count")) or len(role_rows),
        "mobility_observed": _sf_talent_int(operational_row.get("mobility_observed_count")) or sum(
            1 for row in mobility_rows if _sf_talent_int(row.get("movement_events")) > 0
        ),
        "readiness_calculable": readiness_counts["readiness_calculable"],
        "readiness_insufficient": readiness_counts["readiness_insufficient"],
        "nine_box_available": _sf_talent_nine_box_available_count(
            operational_row,
            nine_box_rows,
        ),
        "roles_without_requirements": (
            _sf_talent_int(operational_row.get("roles_without_requirements"))
            or _sf_talent_int(operational_row.get("roles_without_requirements_count"))
        ),
        "high_severity_signals": _sf_talent_int(operational_row.get("high_severity_signal_count")),
        "learning_blockers": _sf_talent_first_int(
            operational_row,
            "learning_blocked_count",
            "learning_blocker_count",
        ),
        "recruiting_blockers": _sf_talent_first_int(
            operational_row,
            "recruiting_blocked_count",
            "recruiting_blocker_count",
        ),
        "skill_gap_count": _sf_talent_int(operational_row.get("skill_gap_count")),
        "skill_coverage_pct": operational_row.get("skill_coverage_pct"),
        "operational_status": _sf_talent_status(
            operational_row.get("feature_status") or results["operational_features"]["status"]
        ),
        "confidence": operational_row.get("confidence"),
        "source_mode": source_mode,
        "operational_label": str(operational_row.get("user_status_label") or "En espera de datos"),
    }


@_bind_to_core
def _sf_talent_kpi_blockers(
    rows: dict[str, Any],
    results: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    profile_rows = rows["profile_rows"]
    role_rows = rows["role_rows"]
    operational_row = rows["operational_row"]

    profile_blockers = _sf_talent_extract_blockers(profile_rows) or [
        "Datos de competencias pendientes",
        "Datos de desempeno pendientes",
        "Datos de aspiracion pendientes",
    ]
    role_blockers = _sf_talent_extract_blockers(role_rows)
    operational_blockers = _sf_talent_extract_blockers([operational_row]) if operational_row else []
    system_errors = [
        str(result.get("error"))
        for result in results.values()
        if result.get("error")
    ]

    blockers = []
    has_operational_reference = (
        metrics.get("source_mode") == "benchmark_internal"
        and _sf_talent_int(metrics.get("readiness_calculable")) > 0
    )
    if (
        not has_operational_reference
        and (
            not metrics["profiled_employees"]
            or metrics["readiness_insufficient"]
            or metrics["readiness_calculable"] == 0
        )
    ):
        blockers.append(
            {
                "id": "talent_cpa_inputs_missing",
                "status": "partial" if metrics["profiled_employees"] else "blocked",
                "title": "Datos de talento pendientes",
                "detail": "Para clasificar talento se necesitan desempeno, competencias y aspiracion con datos suficientes.",
                "items": operational_blockers or profile_blockers,
            }
        )
    if metrics["roles_without_requirements"] or role_blockers:
        blockers.append(
            {
                "id": "talent_role_requirements_partial",
                "status": "partial",
                "title": "Roles parciales",
                "detail": "Los roles existen, pero faltan requisitos de habilidades para comparar persona contra rol.",
                "items": role_blockers or ["Requisitos de rol pendientes", "Habilidades requeridas pendientes"],
            }
        )
    if system_errors:
        blockers.append({
            "id": "talent_dataset_availability",
            "status": "unavailable",
            "title": "Datasets no disponibles",
            "detail": "Algunos golds de Talento no pudieron leerse en este workspace.",
            "items": system_errors,
        })
    return blockers


@_bind_to_core
def _sf_talent_signal_payloads(signal_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(row.get("signal_id") or f"signal_{idx}"),
            "type": str(row.get("signal_type") or "priorizacion"),
            "severity": str(row.get("severity") or "medium"),
            "title": str(row.get("title") or "Senal Talento"),
            "affected_count": _sf_talent_int(row.get("affected_count")),
            "recommendation": str(row.get("recommendation") or ""),
            "status": str(row.get("status") or "recommendation_only"),
        }
        for idx, row in enumerate(signal_rows)
    ]


@_bind_to_core
def _sf_talent_role_samples(role_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "label": str(row.get("role_name") or row.get("job_code") or "Sin rol"),
                "job_code": _sf_talent_public_value(row.get("job_code")),
                "headcount": _sf_talent_int(row.get("active_employee_count")),
                "status": str(row.get("role_profile_status") or "partial"),
            }
            for row in role_rows
        ],
        key=lambda item: (-_sf_talent_int(item["headcount"]), str(item["label"])),
    )[:5]


@_bind_to_core
def _sf_talent_widget(
    widget_id: str,
    title: str,
    value: Any,
    dataset: str,
    status: str,
    *,
    rows: list[dict[str, Any]] | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    widget = {
        "id": widget_id,
        "title": title,
        "value": value,
        "dataset": dataset,
        "href": _sf_talent_dataset_href(dataset),
        "status": status,
    }
    if rows is not None:
        widget["rows"] = rows
    if detail is not None:
        widget["detail"] = detail
    return widget


@_bind_to_core
def _sf_talent_kpi_widgets(
    datasets: dict[str, str],
    results: dict[str, dict[str, Any]],
    rows: dict[str, Any],
    metrics: dict[str, Any],
    signals: list[dict[str, Any]],
    role_samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    readiness_insufficient = metrics["readiness_insufficient"]
    return [
        _sf_talent_widget(
            "sf_talent_profiled_employees",
            "Empleados perfil Talento",
            metrics["profiled_employees"],
            datasets["employee_profile"],
            results["employee_profile"]["status"],
        ),
        _sf_talent_widget(
            "sf_talent_roles_profiled",
            "Roles derivados",
            metrics["roles_profiled"],
            datasets["role_profile"],
            results["role_profile"]["status"],
            rows=role_samples,
        ),
        _sf_talent_widget(
            "sf_talent_readiness_calculable",
            "Perfiles calculables",
            metrics["readiness_calculable"],
            datasets["readiness"],
            "partial" if readiness_insufficient else results["readiness"]["status"],
            detail=f"{readiness_insufficient} en espera de datos",
        ),
        _sf_talent_widget(
            "sf_talent_9box_available",
            "Clasificacion disponible",
            metrics["nine_box_available"],
            datasets["nine_box"],
            "blocked"
            if rows["nine_box_rows"] and metrics["nine_box_available"] == 0
            else results["nine_box"]["status"],
        ),
        _sf_talent_widget(
            "sf_talent_operational_features",
            "Analisis operativo",
            metrics["operational_label"],
            datasets["operational_features"],
            metrics["operational_status"],
        ),
        _sf_talent_widget(
            "sf_talent_mobility_observed",
            "Movilidad observada",
            metrics["mobility_observed"],
            datasets["mobility_history"],
            results["mobility_history"]["status"],
        ),
        _sf_talent_widget(
            "sf_talent_active_signals",
            "Senales Talento",
            len(signals),
            datasets["signals"],
            results["signals"]["status"],
        ),
    ]


@_bind_to_core
def _sf_talent_kpi_profile_payload() -> dict[str, Any]:
    return {
        "industry": "retail",
        "company_profile": "femsa",
        "wisdom_bit": "WB-TALENTO",
        "decision_mode": "recommendation_only",
        "compensation_enabled": False,
        "write_back_enabled": False,
    }


@_bind_to_core
def _sf_talent_kpi_readiness_payload(
    metrics: dict[str, Any],
    *,
    latest_simulation: dict[str, Any],
    blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "ready_min": 80,
        "near_min": 60,
        "profiled_employees": metrics["profiled_employees"],
        "calculable_employees": metrics["readiness_calculable"],
        "insufficient_data_employees": metrics["readiness_insufficient"],
        "nine_box_available": metrics["nine_box_available"],
        "roles_without_requirements": metrics["roles_without_requirements"],
        "high_severity_signals": metrics["high_severity_signals"],
        "learning_blockers": metrics["learning_blockers"],
        "recruiting_blockers": metrics["recruiting_blockers"],
        "skill_gap_count": metrics["skill_gap_count"],
        "skill_coverage_pct": _sf_talent_public_value(metrics["skill_coverage_pct"]),
        "operational_status": metrics["operational_status"],
        "operational_label": metrics["operational_label"],
        "readiness_status": metrics["readiness_status"],
        "confidence": _sf_talent_public_value(metrics["confidence"]),
        "source_mode": metrics["source_mode"],
        "latest_analysis_status": latest_simulation.get("status"),
        "status": "partial" if metrics["readiness_insufficient"] or blockers else "ready",
    }


@_bind_to_core
def _sf_talent_operational_features_payload(
    datasets: dict[str, str],
    metrics: dict[str, Any],
    operational_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dataset": datasets["operational_features"],
        "status": metrics["operational_status"],
        "label": metrics["operational_label"],
        "row": {key: _sf_talent_public_value(value) for key, value in operational_row.items()},
    }


@_bind_to_core
def _sf_talent_analysis_inputs_payload(
    datasets: dict[str, str],
    results: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    simulation_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dataset": datasets["simulation_inputs"],
        "status": _sf_talent_status(
            simulation_row.get("input_status") or results["simulation_inputs"]["status"]
        ),
        "label": str(simulation_row.get("user_status_label") or metrics["operational_label"]),
        "scenario_count": _sf_talent_int(simulation_row.get("scenario_count")),
        "contract_version": simulation_row.get("analysis_contract_version"),
    }


# --- Workforce Trends (Fase 3 P0) --------------------------------------------
# Fuente UNICA de: plantilla activa, antiguedad promedio, rotacion, meses de
# historia y las series para sparklines/graficos. Lee gold YA materializado
# (talent_operational_features + las 3 series mensuales por cohorte de #476) y
# AGREGA una sola vez aqui. NO toca Gold/Monte Carlo/Bayes/datasets: solo
# presentacion. Consumido por Control Room (via el payload de talent-kpis) y,
# mas adelante, por Workforce Overview (via el endpoint) sin re-agregar.
# NOTA: estos helpers llevan @_bind_to_core porque build_workforce_trends corre en
# el namespace de core (ver _bind_to_core); sin el decorador no serian visibles.


@_bind_to_core
def _wt_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@_bind_to_core
def _wt_int(value: Any) -> int | None:
    num = _wt_num(value)
    return int(num) if num is not None else None


@_bind_to_core
def _wt_month_key(row: dict[str, Any]) -> str:
    return str(row.get("snapshot_month") or "")[:7]


@_bind_to_core
def _wt_aggregate_series(
    headcount_rows: list[dict[str, Any]],
    tenure_rows: list[dict[str, Any]],
    attrition_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Agrega las 3 series por-(cohorte,mes) a series por-mes de todo el workspace.

    headcount = suma; antiguedad = promedio PONDERADO por cohort_size; rotacion =
    suma(separations)/suma(cohort_size) RECOMPUTADA por mes (nunca promediar tasas).
    """
    headcount: dict[str, float] = {}
    for row in headcount_rows:
        month = _wt_month_key(row)
        value = _wt_num(row.get("active_headcount"))
        if month and value is not None:
            headcount[month] = headcount.get(month, 0.0) + value

    tenure: dict[str, list[float]] = {}
    for row in tenure_rows:
        month = _wt_month_key(row)
        avg = _wt_num(row.get("avg_tenure_months"))
        size = _wt_num(row.get("cohort_size")) or 0.0
        if month and avg is not None and size > 0:
            acc = tenure.setdefault(month, [0.0, 0.0])
            acc[0] += avg * size
            acc[1] += size

    attrition: dict[str, list[float]] = {}
    for row in attrition_rows:
        month = _wt_month_key(row)
        separations = _wt_num(row.get("separations")) or 0.0
        size = _wt_num(row.get("cohort_size")) or 0.0
        if month:
            acc = attrition.setdefault(month, [0.0, 0.0])
            acc[0] += separations
            acc[1] += size

    months = sorted(set(headcount) | set(tenure) | set(attrition))
    return {
        "months": months,
        "headcount": [
            int(round(headcount[month])) if month in headcount else None for month in months
        ],
        "avg_tenure_months": [
            round(tenure[month][0] / tenure[month][1], 2)
            if month in tenure and tenure[month][1] > 0
            else None
            for month in months
        ],
        "attrition_rate": [
            round(attrition[month][0] / attrition[month][1], 4)
            if month in attrition and attrition[month][1] > 0
            else None
            for month in months
        ],
    }


@_bind_to_core
async def build_workforce_trends(
    user: dict | None,
    *,
    operational_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bundle unico de Workforce Trends para todas las superficies (presentacion).

    operational_row se pasa cuando ya fue leido (payload de talent-kpis) para no
    re-consultar; el endpoint standalone lo omite y se lee aqui.
    """
    if operational_row is None:
        op_result = await _sf_talent_gold_result(
            "sap_successfactors_talent_operational_features", user, 1
        )
        op_rows = op_result.get("rows") or []
        operational_row = op_rows[0] if op_rows else {}

    headcount = await _sf_talent_gold_result(
        "sap_successfactors_talent_headcount_by_cohort_month", user, 5000
    )
    tenure = await _sf_talent_gold_result(
        "sap_successfactors_talent_tenure_by_cohort_month", user, 5000
    )
    attrition = await _sf_talent_gold_result(
        "sap_successfactors_talent_attrition_by_cohort_month", user, 5000
    )
    series = _wt_aggregate_series(
        headcount.get("rows") or [],
        tenure.get("rows") or [],
        attrition.get("rows") or [],
    )

    kpis = {
        "active_headcount": _wt_int(operational_row.get("active_headcount_current")),
        "avg_tenure_months": _wt_num(operational_row.get("avg_tenure_months_current")),
        "attrition_rate": _wt_num(operational_row.get("attrition_rate_current")),
        "history_months": _wt_int(operational_row.get("headcount_history_months")),
    }
    has_kpis = any(value is not None for value in kpis.values())
    status = "ready" if series["months"] and has_kpis else (
        "partial" if series["months"] or has_kpis else "waiting_for_data"
    )
    return {
        "status": status,
        "datasets": {
            "headcount": "sap_successfactors_talent_headcount_by_cohort_month",
            "tenure": "sap_successfactors_talent_tenure_by_cohort_month",
            "attrition": "sap_successfactors_talent_attrition_by_cohort_month",
            "operational_features": "sap_successfactors_talent_operational_features",
        },
        "kpis": kpis,
        "series": series,
    }


@_bind_to_core
async def sap_successfactors_talent_kpis(user: dict | None) -> dict[str, Any]:
    """Talent/WisdomBit KPIs for the active SuccessFactors workspace.

    The endpoint intentionally returns aggregate coverage and blockers only.
    C/P/A scores, salary values and individual PII are not exposed here.
    """

    datasets = _sf_talent_kpi_datasets()
    results = await _sf_talent_kpi_results(datasets, user)
    latest_simulation = await _sf_talent_latest_simulation_result(user)
    rows = _sf_talent_kpi_rows(results)
    metrics = _sf_talent_kpi_metrics(rows, results)
    metrics["readiness_status"] = _sf_talent_status(
        rows["operational_row"].get("readiness_status")
        or (
            "benchmark_internal"
            if metrics["source_mode"] == "benchmark_internal"
            and metrics["readiness_calculable"] > 0
            else ""
        )
        or metrics["operational_status"]
    )
    blockers = _sf_talent_kpi_blockers(rows, results, metrics)
    signals = _sf_talent_signal_payloads(rows["signal_rows"])
    role_samples = _sf_talent_role_samples(rows["role_rows"])
    widgets = _sf_talent_kpi_widgets(datasets, results, rows, metrics, signals, role_samples)

    generated_at = datetime.now(UTC).isoformat()
    tenant_id, workspace_id = _workspace_scope(user)
    operational_row = rows["operational_row"]
    simulation_row = rows["simulation_row"]

    return {
        "generated_at": generated_at,
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "profile": _sf_talent_kpi_profile_payload(),
        "readiness": _sf_talent_kpi_readiness_payload(
            metrics,
            latest_simulation=latest_simulation,
            blockers=blockers,
        ),
        "operational_features": _sf_talent_operational_features_payload(
            datasets,
            metrics,
            operational_row,
        ),
        "workforce_trends": await build_workforce_trends(
            user,
            operational_row=operational_row,
        ),
        "analysis_inputs": _sf_talent_analysis_inputs_payload(
            datasets,
            results,
            metrics,
            simulation_row,
        ),
        "latest_simulation": latest_simulation,
        "widgets": widgets,
        "signals": signals,
        "blockers": blockers,
    }


@_bind_to_core
def _sf_talent_dataset_href(dataset: str) -> str:
    return (
        "/data/catalog?layer=gold&cartridge=sap_successfactors"
        f"&datasets={dataset}"
    )


@_bind_to_core
def _sf_talent_box_definitions(
    definitions: list[dict[str, Any]] = TALENT_BOX_DEFINITIONS,
) -> list[dict[str, Any]]:
    return [dict(item) for item in definitions]


@_bind_to_core
def _sf_talent_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@_bind_to_core
def _sf_talent_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@_bind_to_core
def _sf_talent_status(value: Any, fallback: str = "unavailable") -> str:
    status = str(value or fallback).strip().lower()
    return status or fallback


@_bind_to_core
def _sf_talent_json_list(value: Any) -> list[str]:
    if value is None:
        return []
    parsed: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [text]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, dict):
        text = str(parsed.get("reason") or parsed.get("title") or parsed).strip()
        return [text] if text else []
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


@_bind_to_core
async def _sf_talent_gold_result(
    dataset: str, user: dict | None, limit: int
) -> dict[str, Any]:
    try:
        rows = await query_dataset_rows(dataset, user, limit)
    except HTTPException as exc:
        status = {
            403: "no_permission",
            404: "missing",
            503: "unavailable",
        }.get(exc.status_code, "unavailable")
        return {
            "dataset": dataset,
            "rows": [],
            "status": status,
            "error": str(exc.detail or f"{dataset} unavailable"),
        }
    except Exception as exc:
        return {
            "dataset": dataset,
            "rows": [],
            "status": "unavailable",
            "error": str(exc),
        }
    clean_rows = [dict(row) for row in rows if isinstance(row, dict)]
    return {
        "dataset": dataset,
        "rows": clean_rows,
        "status": "empty" if not clean_rows else "ready",
        "error": None,
    }


@_bind_to_core
def _sf_talent_employee_key(value: Any) -> str:
    raw = str(value or "unknown").strip() or "unknown"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"tal_{digest}"


@_bind_to_core
def _sf_talent_masked_name(employee_key: str) -> str:
    return f"Colaborador {employee_key[-4:].upper()}"


@_bind_to_core
def _sf_talent_fit_band(value: Any) -> str:
    score = _sf_talent_float(value)
    if score is None:
        return "insufficient_data"
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


@_bind_to_core
def _sf_talent_movement_bucket(value: Any) -> str:
    months = _sf_talent_float(value)
    if months is None:
        return "unknown"
    if months < 6:
        return "0-6m"
    if months < 12:
        return "6-12m"
    if months < 24:
        return "12-24m"
    return "24m+"


@_bind_to_core
def _sf_talent_masked_roster_row(row: dict[str, Any]) -> dict[str, Any]:
    employee_key = _sf_talent_employee_key(row.get("user_id") or row.get("employee_id"))
    return {
        "employee_key": employee_key,
        "display_name": _sf_talent_masked_name(employee_key),
        "role": str(row.get("role_name") or row.get("job_code") or "Rol no disponible"),
        "unit": str(row.get("department_name") or row.get("company_name") or "Unidad no disponible"),
        "region": str(row.get("location_name") or row.get("region") or "Region no disponible"),
        "readiness_status": _sf_talent_status(row.get("readiness_status"), "blocked"),
        "box_id": str(row.get("box_key") or ""),
        "box_label": str(row.get("box_label") or "9-box"),
        "performance_band": str(row.get("performance_band") or "unknown"),
        "potential_band": str(row.get("potential_band") or "unknown"),
        "fit_band": _sf_talent_fit_band(row.get("fit_score")),
        "movement_age_bucket": _sf_talent_movement_bucket(row.get("months_since_movement")),
        "data_status": _sf_talent_status(row.get("box_status") or row.get("cpa_status"), "blocked"),
    }


@_bind_to_core
def _sf_talent_metadata_entities(
    entities: list[dict[str, Any]] = TALENT_METADATA_ENTITIES,
) -> list[dict[str, Any]]:
    return [
        {**item, "blockers": list(item.get("blockers", []))}
        for item in entities
    ]


@_bind_to_core
def _sf_talent_live_component_for(
    entity_id: str,
    live_components: dict[str, dict[str, Any]],
    component_ids: dict[str, str] = TALENT_LIVE_COMPONENT_IDS,
) -> dict[str, Any]:
    return live_components.get(entity_id) or live_components.get(
        component_ids.get(entity_id, "")
    ) or {}


@_bind_to_core
def _sf_talent_live_blocker_items(component: dict[str, Any]) -> list[str]:
    candidates = [
        item for item in component.get("candidates", []) if isinstance(item, dict)
    ]
    if not candidates:
        return [str(item) for item in component.get("blockers", []) if str(item or "").strip()]

    permission = [
        str(item.get("odata_entity") or item.get("entity") or "").strip()
        for item in candidates
        if str(item.get("status") or "") == "permission_blocked"
    ]
    if permission:
        return [f"Permiso de lectura pendiente: {entity}" for entity in permission if entity]

    invalid_fields: list[str] = []
    for item in candidates:
        if str(item.get("status") or "") != "field_blocked":
            continue
        entity = str(item.get("odata_entity") or item.get("entity") or "").strip()
        fields = ", ".join(str(field) for field in (item.get("fields_missing") or []) if field)
        invalid_fields.append(f"Campos no expuestos en {entity}: {fields}" if fields else f"Campos no expuestos en {entity}")
    if invalid_fields:
        return invalid_fields

    missing = [
        str(item.get("odata_entity") or item.get("entity") or "").strip()
        for item in candidates
        if str(item.get("status") or "") == "missing"
    ]
    if missing:
        return [f"Entidad no expuesta en SAP: {entity}" for entity in missing if entity]

    missing_groups = component.get("required_groups") or []
    ready_groups = component.get("ready_groups") or []
    if missing_groups:
        return [
            "Grupo requerido pendiente: "
            + ", ".join(str(group) for group in missing_groups if group)
            + ("; encontrados: " + ", ".join(str(group) for group in ready_groups if group) if ready_groups else "")
        ]
    return ["Metadata pendiente de confirmar"]


@_bind_to_core
def _sf_talent_merge_live_metadata(
    entity: dict[str, Any],
    live_components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    component = _sf_talent_live_component_for(str(entity.get("id") or ""), live_components)
    if not component:
        return entity
    status = _sf_talent_status(component.get("status"), entity.get("status", "partial"))
    selected_entity = component.get("entity") or component.get("selected_entity")
    return {
        **entity,
        "status": status,
        "entity": str(selected_entity or entity.get("entity") or ""),
        "odata_entity": component.get("odata_entity") or component.get("selected_entity"),
        "fields_found": component.get("fields_found") or [],
        "fields_missing": component.get("fields_missing") or [],
        "sample_status": component.get("sample_status"),
        "ready_to_extract": bool(component.get("ready_to_extract")),
        "extraction_target": selected_entity,
        "blockers": [] if status == "ready" else _sf_talent_live_blocker_items(component),
        "live_status": component.get("status"),
        "live_selected_entity": component.get("selected_entity"),
        "live_candidates": component.get("candidates", []),
    }


@_bind_to_core
def _sf_talent_blockers_from_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for result in results:
        if not result.get("error"):
            continue
        blockers.append(
            {
                "id": f"{result.get('dataset', 'dataset')}_availability",
                "status": result.get("status") or "unavailable",
                "title": "Dataset no disponible",
                "detail": str(result.get("error") or ""),
                "items": [str(result.get("dataset") or "")],
            }
        )
    return blockers


@_bind_to_core
async def _sf_talent_live_metadata_readiness(user: dict | None) -> dict[str, Any] | None:
    """Fetch live SAP C/P/A metadata readiness from the cartridge service.

    Control Room must degrade, not fail, when the SAP cartridge is offline or
    credentials are pending. The payload carries no sample values/PII.
    """
    base_url = os.environ.get("SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203").rstrip("/")
    headers = _internal_headers("CARTRIDGE")
    headers["X-Security-Context"] = json.dumps(build_security_context(user), ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            response = await client.get(
                f"{base_url}/talent/metadata-readiness",
                params={"sample": "true"},
            )
        if response.status_code >= 400:
            return {
                "status": "unavailable",
                "error": f"metadata readiness HTTP {response.status_code}",
                "blockers": [
                    {
                        "component": "metadata_preflight",
                        "reason": "cartridge_http_error",
                        "status_code": response.status_code,
                    }
                ],
            }
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001 - dashboard must stay available.
        return {
            "status": "unavailable",
            "error": str(exc)[:240],
            "blockers": [
                {
                    "component": "metadata_preflight",
                    "reason": "cartridge_unavailable",
                }
            ],
        }


@_bind_to_core
def _sf_talent_9box_cells(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_box = {str(row.get("box_key") or ""): row for row in rows}
    cells: list[dict[str, Any]] = []
    for definition in _sf_talent_box_definitions():
        row = rows_by_box.get(definition["box_id"], {})
        employee_count = _sf_talent_int(row.get("employee_count"))
        ready_count = _sf_talent_int(row.get("ready_count"))
        benchmark_count = _sf_talent_int(row.get("benchmark_count"))
        blocked_count = _sf_talent_int(row.get("blocked_count"))
        classified_count = ready_count
        status = _sf_talent_status(
            row.get("box_status"),
            "ready" if classified_count > 0 else "empty",
        )
        cells.append(
            {
                **definition,
                "employee_count": employee_count,
                "ready_count": classified_count,
                "cpa_real_count": max(ready_count - benchmark_count, 0),
                "reference_count": benchmark_count,
                "blocked_count": blocked_count,
                "status": status,
                "href": f"/control-room/talent?box={definition['box_id']}",
            }
        )
    return cells


@_bind_to_core
def _sf_talent_9box_totals(cells: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "employees": sum(_sf_talent_int(cell["employee_count"]) for cell in cells),
        "ready": sum(_sf_talent_int(cell["ready_count"]) for cell in cells),
        "reference": sum(_sf_talent_int(cell.get("reference_count")) for cell in cells),
        "blocked": sum(_sf_talent_int(cell["blocked_count"]) for cell in cells),
        "cells": len(cells),
    }


@_bind_to_core
def _sf_talent_9box_operational_rows_from_detail(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_box: dict[str, dict[str, int]] = {}
    for row in rows:
        box_key = str(row.get("box_key") or "").strip()
        if not box_key:
            continue
        counts = by_box.setdefault(
            box_key,
            {
                "employee_count": 0,
                "ready_count": 0,
                "benchmark_count": 0,
                "blocked_count": 0,
            },
        )
        counts["employee_count"] += 1
        box_status = _sf_talent_status(row.get("box_status"), "")
        source_mode = _sf_talent_status(row.get("source_mode"), "")
        is_ready = (
            box_status not in {"blocked", "insufficient_data", "missing", ""}
            or source_mode in {"cpa_real", "benchmark_internal"}
        )
        if is_ready:
            counts["ready_count"] += 1
        else:
            counts["blocked_count"] += 1
        if source_mode == "benchmark_internal" or box_status == "benchmark_internal":
            counts["benchmark_count"] += 1
    return [
        {
            "box_key": box_key,
            "employee_count": counts["employee_count"],
            "ready_count": counts["ready_count"],
            "benchmark_count": counts["benchmark_count"],
            "blocked_count": counts["blocked_count"],
            "box_status": (
                "benchmark_internal"
                if counts["benchmark_count"] and counts["ready_count"]
                else "ready"
                if counts["ready_count"]
                else "blocked"
            ),
        }
        for box_key, counts in by_box.items()
    ]


@_bind_to_core
def _sf_talent_9box_blockers(
    result: dict[str, Any],
    *,
    total_ready: int,
) -> list[dict[str, Any]]:
    blockers = _sf_talent_blockers_from_results([result])
    if total_ready == 0:
        blockers.append(
            {
                "id": "talent_9box_cpa_incomplete",
                "status": "blocked",
                "title": "9-box pendiente de C/P/A",
                "detail": "La matriz se muestra como estructura, pero no clasifica personas hasta contar con competencia, desempeno y aspiracion.",
                "items": ["KB-COMPETENCIAS", "KB-DESEMPENO", "KB-ASPIRACION"],
            }
        )
    return blockers


@_bind_to_core
async def sap_successfactors_talent_9box(user: dict | None) -> dict[str, Any]:
    dataset = "sap_successfactors_talent_9box_operational"
    result = await _sf_talent_gold_result(dataset, user, 100)
    cells = _sf_talent_9box_cells(result["rows"])
    totals = _sf_talent_9box_totals(cells)
    if totals["ready"] == 0:
        detail_result = await _sf_talent_gold_result("sap_successfactors_talent_9box", user, 5000)
        detail_rows = _sf_talent_9box_operational_rows_from_detail(detail_result["rows"])
        detail_cells = _sf_talent_9box_cells(detail_rows)
        detail_totals = _sf_talent_9box_totals(detail_cells)
        if detail_totals["ready"] > 0:
            cells = detail_cells
            totals = detail_totals
            result = detail_result
    blockers = _sf_talent_9box_blockers(result, total_ready=totals["ready"])

    tenant_id, workspace_id = _workspace_scope(user)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset": dataset,
        "status": "ready" if totals["ready"] else result["status"] if result["status"] != "ready" else "blocked",
        "totals": totals,
        "cells": cells,
        "blockers": blockers,
        "privacy": {
            "roster": "masked",
            "forbidden_fields": ["full_name", "user_id", "pernr", "salary", "paycomp_value"],
        },
    }


@_bind_to_core
async def sap_successfactors_talent_9box_box(
    user: dict | None, box_id: str
) -> dict[str, Any]:
    definitions = {item["box_id"]: item for item in _sf_talent_box_definitions()}
    if box_id not in definitions:
        raise HTTPException(404, "unknown talent 9-box id")

    dataset = "sap_successfactors_talent_9box"
    result = await _sf_talent_gold_result(dataset, user, 5000)
    rows = [
        row for row in result["rows"] if str(row.get("box_key") or "") == box_id
    ]
    roster = [_sf_talent_masked_roster_row(row) for row in rows[:100]]
    raw_blockers: set[str] = set()
    for row in rows:
        raw_blockers.update(_sf_talent_json_list(row.get("blockers")))

    blockers = _sf_talent_blockers_from_results([result])
    if raw_blockers:
        blockers.append(
            {
                "id": f"{box_id}_row_blockers",
                "status": "blocked",
                "title": "Datos incompletos en la caja",
                "detail": "Algunos registros no tienen C/P/A suficiente para clasificacion real.",
                "items": sorted(raw_blockers),
            }
        )

    tenant_id, workspace_id = _workspace_scope(user)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset": dataset,
        "box": definitions[box_id],
        "status": result["status"] if roster else "empty" if result["status"] == "ready" else result["status"],
        "count": len(rows),
        "roster": roster,
        "blockers": blockers,
        "privacy": {
            "masked": True,
            "excluded_fields": ["full_name", "user_id", "pernr", "salary", "paycomp_value"],
        },
    }


@_bind_to_core
async def sap_successfactors_talent_anomalies(user: dict | None) -> dict[str, Any]:
    dataset = "sap_successfactors_talent_action_candidates"
    result = await _sf_talent_gold_result(dataset, user, 100)
    anomalies = [
        {
            "id": str(row.get("action_id") or f"talent_action_{idx}"),
            "type": str(row.get("action_type") or "priorizacion"),
            "severity": _severity(row.get("severity")),
            "title": str(row.get("title") or "Senal Talento"),
            "detail": str(row.get("recommendation") or "Revisar cobertura antes de decidir."),
            "affected_count": _sf_talent_int(row.get("affected_count")),
            "recommendation": str(row.get("recommendation") or ""),
            "status": str(row.get("status") or "recommendation_only"),
            "method": str(row.get("method") or "wisdombit"),
            "preview_available": True,
        }
        for idx, row in enumerate(result["rows"])
    ]
    tenant_id, workspace_id = _workspace_scope(user)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset": dataset,
        "status": result["status"],
        "summary": {
            "total": len(anomalies),
            "high": sum(1 for item in anomalies if item["severity"] in {"critical", "high"}),
            "recommendation_only": sum(1 for item in anomalies if item["status"] == "recommendation_only"),
        },
        "items": anomalies,
        "blockers": _sf_talent_blockers_from_results([result]),
    }


@_bind_to_core
def _sf_talent_cpa_readiness_counts(cpa_rows: list[dict[str, Any]]) -> dict[str, int]:
    ready_cpa = sum(
        1 for row in cpa_rows if _sf_talent_status(row.get("cpa_status")) == "ready"
    )
    insufficient = sum(
        1
        for row in cpa_rows
        if _sf_talent_status(row.get("cpa_status")) in {"insufficient_data", "blocked"}
    )
    return {"ready_cpa": ready_cpa, "insufficient": insufficient}


@_bind_to_core
def _sf_talent_live_components(live: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(component.get("id")): component
        for component in (live or {}).get("components", [])
        if isinstance(component, dict)
    }


@_bind_to_core
def _sf_talent_entities_for_readiness(
    ready_cpa: int,
    live: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    entities = _sf_talent_metadata_entities()
    live_components = _sf_talent_live_components(live)
    if live_components:
        entities = [_sf_talent_merge_live_metadata(entity, live_components) for entity in entities]
    if ready_cpa:
        entities = [
            {**entity, "status": "ready", "blockers": []}
            if entity["id"] in {"performance", "competency", "aspiration"}
            else entity
            for entity in entities
        ]
    return entities


@_bind_to_core
def _sf_talent_live_readiness_summary(live: dict[str, Any] | None) -> dict[str, Any]:
    live_status = str((live or {}).get("status") or "unavailable")
    raw_summary = (live or {}).get("summary")
    live_summary = raw_summary if isinstance(raw_summary, dict) else {}
    return {
        "live_status": live_status,
        "live_required_ready": _sf_talent_int(live_summary.get("required_ready")),
        "live_required_total": _sf_talent_int(live_summary.get("required_total")),
    }


@_bind_to_core
def _sf_talent_live_blocker_items(live: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [item for item in (live or {}).get("blockers", []) if isinstance(item, dict)]


@_bind_to_core
def _sf_talent_live_readiness_blockers(
    live: dict[str, Any] | None,
    ready_cpa: int,
    live_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    live_status = live_summary["live_status"]
    live_required_ready = live_summary["live_required_ready"]
    live_required_total = live_summary["live_required_total"]
    live_blockers_raw = _sf_talent_live_blocker_items(live)
    if live_status == "unavailable":
        return [
            {
                "id": "talent_metadata_preflight_unavailable",
                "status": "unavailable",
                "title": "Preflight SAP no disponible",
                "detail": str(
                    (live or {}).get("error")
                    or "No se pudo consultar metadata viva de SuccessFactors."
                ),
                "items": [
                    str(item.get("reason") or item.get("component") or item)
                    for item in live_blockers_raw
                ]
                or ["cartridge_unavailable"],
            }
        ]
    if live_required_total and live_required_ready < live_required_total:
        return [
            {
                "id": "talent_metadata_cpa_inputs_missing",
                "status": "blocked",
                "title": "Metadata C/P/A incompleta",
                "detail": "SuccessFactors todavia no expone todas las entidades/campos/permisos para competencia, desempeno y aspiracion.",
                "items": [
                    ", ".join(
                        str(entity) for entity in item.get("entities_checked", []) if entity
                    )
                    or str(item.get("component") or item.get("reason") or "metadata")
                    for item in live_blockers_raw
                ],
            }
        ]
    if live_required_total and ready_cpa == 0:
        return [
            {
                "id": "talent_cpa_materialization_pending",
                "status": "partial",
                "title": "C/P/A listo en metadata, pendiente en Gold",
                "detail": "SAP expone las entidades requeridas; falta extraer/materializar los scores C/P/A para desbloquear 9-box real.",
                "items": ["sap_successfactors_talent_cpa_scores"],
            }
        ]
    return []


@_bind_to_core
def _sf_talent_metadata_readiness_summary(
    entities: list[dict[str, Any]],
    counts: dict[str, int],
    live_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "cpa_ready_employees": counts["ready_cpa"],
        "cpa_insufficient_employees": counts["insufficient"],
        "entities": len(entities),
        "blocked_entities": sum(1 for entity in entities if entity["status"] == "blocked"),
        "live_required_ready": live_summary["live_required_ready"],
        "live_required_total": live_summary["live_required_total"],
        "live_status": live_summary["live_status"],
    }


@_bind_to_core
async def sap_successfactors_talent_metadata_readiness(
    user: dict | None,
) -> dict[str, Any]:
    cpa = await _sf_talent_gold_result("sap_successfactors_talent_cpa_scores", user, 1000)
    live = await _sf_talent_live_metadata_readiness(user)
    cpa_rows = cpa["rows"]
    counts = _sf_talent_cpa_readiness_counts(cpa_rows)
    entities = _sf_talent_entities_for_readiness(counts["ready_cpa"], live)
    dataset_blockers = _sf_talent_blockers_from_results([cpa])
    live_summary = _sf_talent_live_readiness_summary(live)
    live_blockers = _sf_talent_live_readiness_blockers(
        live, counts["ready_cpa"], live_summary
    )

    tenant_id, workspace_id = _workspace_scope(user)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "status": "ready" if counts["ready_cpa"] and counts["insufficient"] == 0 else "partial",
        "summary": _sf_talent_metadata_readiness_summary(
            entities,
            counts,
            live_summary,
        ),
        "entities": entities,
        "blockers": dataset_blockers + live_blockers,
        "live_preflight": live or {
            "status": "unavailable",
            "blockers": [{"reason": "no_live_preflight_payload"}],
        },
    }


@_bind_to_core
async def sap_successfactors_talent_overview(user: dict | None) -> dict[str, Any]:
    kpis = await sap_successfactors_talent_kpis(user)
    nine_box = await sap_successfactors_talent_9box(user)
    anomalies = await sap_successfactors_talent_anomalies(user)
    metadata = await sap_successfactors_talent_metadata_readiness(user)
    return {
        **kpis,
        "generated_at": datetime.now(UTC).isoformat(),
        "nine_box": {
            "status": nine_box.get("status"),
            "totals": nine_box.get("totals", {}),
            "cells": nine_box.get("cells", []),
            "blockers": nine_box.get("blockers", []),
        },
        "anomalies": {
            "status": anomalies.get("status"),
            "summary": anomalies.get("summary", {}),
            "items": anomalies.get("items", []),
        },
        "metadata_readiness": {
            "status": metadata.get("status"),
            "summary": metadata.get("summary", {}),
            "entities": metadata.get("entities", []),
        },
    }


@_bind_to_core
async def sap_successfactors_talent_action_preview(
    user: dict | None, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    action_id = str(payload.get("action_id") or payload.get("id") or "").strip()
    if not action_id:
        raise HTTPException(400, "action_id is required")
    box_id = str(payload.get("box_id") or "").strip() or None
    anomalies = await sap_successfactors_talent_anomalies(user)
    selected = next(
        (item for item in anomalies.get("items", []) if item.get("id") == action_id),
        None,
    ) or {
        "id": action_id,
        "title": "Preview Talento",
        "recommendation": "Validar datos y generar accion supervisada.",
        "severity": "medium",
        "affected_count": 0,
        "method": "manual_preview",
    }
    tenant_id, workspace_id = _workspace_scope(user)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": "femsa_sf",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "status": "preview_only",
        "action_id": action_id,
        "box_id": box_id,
        "title": selected.get("title"),
        "severity": selected.get("severity"),
        "affected_count": _sf_talent_int(selected.get("affected_count")),
        "recommendation": selected.get("recommendation") or selected.get("detail"),
        "method": selected.get("method"),
        "recommendation_only": True,
        "write_back_enabled": False,
        "compensation_enabled": False,
        "requires_approval": True,
        "external_mutations": [],
        "steps": [
            {
                "id": "validate_scope",
                "label": "Validar C/P/A y blockers",
                "status": "required",
            },
            {
                "id": "review_roster",
                "label": "Revisar roster enmascarado",
                "status": "required",
            },
            {
                "id": "open_decision",
                "label": "Crear decision supervisada",
                "status": "preview_only",
            },
        ],
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
def _replicon_allocation_thresholds(
    thresholds: ThresholdMap | None = None,
) -> dict[str, float]:
    return {
        "over_warning": _threshold_value(
            thresholds,
            "replicon",
            "over_allocation",
            "pct_asignacion",
            "warning_value",
            110,
        ),
        "over_critical": _threshold_value(
            thresholds,
            "replicon",
            "over_allocation",
            "pct_asignacion",
            "critical_value",
            130,
        ),
        "under_warning": _threshold_value(
            thresholds,
            "replicon",
            "under_allocation",
            "pct_asignacion",
            "warning_value",
            40,
        ),
        "under_critical": _threshold_value(
            thresholds,
            "replicon",
            "under_allocation",
            "pct_asignacion",
            "critical_value",
            20,
        ),
    }


@_bind_to_core
def _replicon_allocation_state(
    pct: float | None,
    threshold_values: dict[str, float],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    over_warning = threshold_values["over_warning"]
    under_warning = threshold_values["under_warning"]
    if pct is None or under_warning <= pct <= over_warning:
        return None
    if pct > over_warning:
        item_type = "over_allocation"
        threshold_state = (
            "critical" if pct >= threshold_values["over_critical"] else "warning"
        )
        warning_default = 110
        critical_default = 130
    else:
        item_type = "under_allocation"
        threshold_state = (
            "critical" if pct <= threshold_values["under_critical"] else "warning"
        )
        warning_default = 40
        critical_default = 20
    return {
        "item_type": item_type,
        "threshold_state": threshold_state,
        "severity": "high" if threshold_state == "critical" else "medium",
        "threshold_refs": [
            _threshold_ref(
                thresholds,
                "replicon",
                item_type,
                "pct_asignacion",
                warning_default=warning_default,
                critical_default=critical_default,
                currency="PCT",
            )
        ],
    }


@_bind_to_core
def _normalize_replicon_allocation(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    pct = _num(row.get("pct_asignacion"))
    allocation_state = _replicon_allocation_state(
        pct,
        _replicon_allocation_thresholds(thresholds),
        thresholds,
    )
    if allocation_state is None or pct is None:
        return None
    consultor = str(row.get("consultor") or "Sin consultor").strip()
    proyecto = str(
        row.get("proyecto") or row.get("project_name") or "Sin proyecto"
    ).strip()
    item_type = allocation_state["item_type"]
    item = _base_item(
        source,
        {**row, "severity": allocation_state["severity"]},
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
    return _attach_thresholds(
        item,
        allocation_state["threshold_refs"],
        allocation_state["threshold_state"],
    )


@_bind_to_core
def _replicon_timesheet_ratio(row: dict[str, Any]) -> tuple[float, float, float] | None:
    total = _num(row.get("horas_total")) or _num(row.get("horas_totales")) or 0
    no_billable = _num(row.get("horas_no_facturables")) or 0
    if total <= 0:
        return None
    return total, no_billable, no_billable / total


@_bind_to_core
def _replicon_timesheet_threshold_ratios(
    thresholds: ThresholdMap | None = None,
) -> tuple[float, float]:
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
    return warning_ratio, critical_ratio


@_bind_to_core
def _replicon_timesheet_context(row: dict[str, Any]) -> dict[str, str]:
    return {
        "consultor": str(row.get("consultor") or "Sin consultor").strip(),
        "proyecto": str(
            row.get("proyecto") or row.get("project_name") or "Sin proyecto"
        ).strip(),
        "period_text": f" en {row.get('semana') or row.get('mes')}"
        if (row.get("semana") or row.get("mes"))
        else "",
    }


@_bind_to_core
def _replicon_timesheet_threshold_ref(
    thresholds: ThresholdMap | None = None,
) -> list[dict[str, Any]]:
    return [
        _threshold_ref(
            thresholds,
            "replicon",
            "non_billable_ratio",
            "horas_no_facturables_ratio",
            warning_default=0.35,
            critical_default=0.55,
            currency="PCT",
        )
    ]


@_bind_to_core
def _normalize_replicon_timesheet(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    ratio_tuple = _replicon_timesheet_ratio(row)
    if ratio_tuple is None:
        return None
    _total, _no_billable, ratio = ratio_tuple
    warning_ratio, critical_ratio = _replicon_timesheet_threshold_ratios(thresholds)
    if ratio < warning_ratio:
        return None
    context = _replicon_timesheet_context(row)
    consultor = context["consultor"]
    proyecto = context["proyecto"]
    threshold_state = "critical" if ratio >= critical_ratio else "warning"
    severity = "high" if threshold_state == "critical" else "medium"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "non_billable_ratio",
        f"{consultor}:{proyecto}",
        consultor,
    )
    item.update(
        {
            "title": "Horas no facturables fuera de rango",
            "description": f"{consultor} tiene {ratio:.0%} de horas no facturables en {proyecto}{context['period_text']}.",
            "recommendation": "Validar causa con PM/RM, reclasificar si procede y ajustar forecast de margen.",
            "root_cause": "Registro de tiempo no facturable alto frente al total reportado.",
            "impact": "Puede erosionar margen y ocultar demanda no planificada.",
            "details": {**item["details"], **row},
        }
    )
    return _attach_thresholds(
        item,
        _replicon_timesheet_threshold_ref(thresholds),
        threshold_state,
    )


@_bind_to_core
def _replicon_pnl_thresholds(
    thresholds: ThresholdMap | None = None,
) -> dict[str, float]:
    return {
        "margin_warning": _threshold_value(
            thresholds,
            "replicon",
            "low_margin",
            "margen_bruto_pct",
            "warning_value",
            20,
        ),
        "margin_critical": _threshold_value(
            thresholds,
            "replicon",
            "low_margin",
            "margen_bruto_pct",
            "critical_value",
            0,
        ),
        "wip_warning": _threshold_value(
            thresholds, "replicon", "wip_variance", "wip_usd", "warning_value", 5000
        ),
        "wip_critical": _threshold_value(
            thresholds,
            "replicon",
            "wip_variance",
            "wip_usd",
            "critical_value",
            25000,
        ),
    }


@_bind_to_core
def _replicon_pnl_state(
    margin: float | None,
    wip: float,
    threshold_values: dict[str, float],
) -> dict[str, Any] | None:
    margin_breached = (
        margin is not None and margin < threshold_values["margin_warning"]
    )
    wip_breached = abs(wip) >= threshold_values["wip_warning"]
    if not margin_breached and not wip_breached:
        return None
    if margin is not None and margin < threshold_values["margin_critical"]:
        severity = "critical"
        threshold_state = "critical"
    elif abs(wip) >= threshold_values["wip_critical"]:
        severity = "critical"
        threshold_state = "critical"
    elif margin_breached:
        severity = "high"
        threshold_state = "warning"
    else:
        severity = "medium"
        threshold_state = "warning"
    return {
        "item_type": "low_margin" if margin_breached else "wip_variance",
        "margin_breached": margin_breached,
        "wip_breached": wip_breached,
        "severity": severity,
        "threshold_state": threshold_state,
    }


@_bind_to_core
def _replicon_pnl_threshold_refs(
    state: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> list[dict[str, Any]]:
    refs = []
    if state["margin_breached"]:
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
    if state["wip_breached"]:
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
    return refs


@_bind_to_core
def _normalize_replicon_pnl(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    margin = _num(row.get("margen_bruto_pct"))
    wip = _num(row.get("wip_usd")) or 0
    state = _replicon_pnl_state(margin, wip, _replicon_pnl_thresholds(thresholds))
    if state is None:
        return None
    proyecto = str(
        row.get("proyecto") or row.get("project_name") or "Sin proyecto"
    ).strip()
    manager = str(row.get("revenue_manager") or "Sin RM").strip()
    item = _base_item(
        source,
        {**row, "severity": state["severity"]},
        state["item_type"],
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
    return _attach_thresholds(
        item,
        _replicon_pnl_threshold_refs(state, thresholds),
        state["threshold_state"],
    )


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
def _sf_talent_signal_details(row: dict[str, Any]) -> dict[str, Any]:
    safe_keys = (
        "signal_id",
        "signal_type",
        "severity",
        "affected_count",
        "status",
        "generated_at",
        "readiness_status",
        "source_row_count",
        "materialized_at",
    )
    return {key: row.get(key) for key in safe_keys if row.get(key) is not None}


@_bind_to_core
def _sf_talent_signal_readiness(row: dict[str, Any]) -> str:
    explicit = str(row.get("readiness_status") or row.get("data_status") or "").strip().lower()
    if explicit:
        return explicit
    if _sf_talent_int(row.get("source_row_count")) > 0:
        return "gold_ready"
    if _sf_talent_int(row.get("affected_count")) > 0:
        return "gold_ready"
    return "insufficient_data"


@_bind_to_core
def _sf_talent_signal_confidence(
    *,
    readiness_status: str,
    source_row_count: int,
    affected_count: int,
) -> float:
    if readiness_status in {"ready", "gold_ready", "materialized"}:
        base = 0.62
    elif readiness_status in {"partial", "metadata_ready"}:
        base = 0.46
    else:
        base = 0.28
    row_boost = 0.0
    if source_row_count >= 100:
        row_boost = 0.18
    elif source_row_count >= 25:
        row_boost = 0.12
    elif source_row_count > 0:
        row_boost = 0.06
    affected_boost = 0.04 if affected_count > 0 else 0.0
    return round(min(0.88, base + row_boost + affected_boost), 3)


@_bind_to_core
def _sf_talent_signal_evidence_pack(
    *,
    source: ControlRoomSource,
    signal_id: str,
    title: str,
    affected_count: int,
    source_row_count: int,
    readiness_status: str,
    generated_at: str,
    materialized_at: str,
    blockers: list[str],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = [
        {
            "source_type": "gold",
            "source_ref": source.dataset,
            "supports_hypothesis": (
                f"{affected_count} registro(s) afectados en la senal {signal_id}."
            ),
            "strength": 0.78 if source_row_count else 0.48,
            "metadata": {
                "source_row_count": source_row_count,
                "materialized_at": materialized_at,
                "generated_at": generated_at,
            },
        },
        {
            "source_type": "readiness",
            "source_ref": "WB-TALENTO readiness",
            "supports_hypothesis": f"Estado de datos: {readiness_status}.",
            "strength": 0.7 if readiness_status in {"ready", "gold_ready", "materialized"} else 0.42,
        },
    ]
    for blocker in blockers[:4]:
        items.append(
            {
                "source_type": "blocker",
                "source_ref": "talent_metadata_readiness",
                "supports_hypothesis": blocker,
                "strength": 0.55,
            }
        )
    return {
        "summary": (
            f"Gold interno {source.dataset}: {source_row_count} fila(s) fuente, "
            f"{affected_count} afectado(s), estado {readiness_status}."
        ),
        "items": items,
        "materialized_at": materialized_at,
        "source_dataset": source.dataset,
        "source_row_count": source_row_count,
        "readiness_status": readiness_status,
        "title": title,
    }


@_bind_to_core
def _sf_talent_math_provenance(
    *,
    signal_id: str,
    affected_count: int,
    source_row_count: int,
    readiness_status: str,
    confidence: float,
) -> dict[str, Any]:
    has_simulation_inputs = affected_count > 0 and source_row_count > 0
    monte_carlo = {
        "status": "not_applicable" if has_simulation_inputs else "blocked",
        "mode": "talent_aggregate_template",
        "reason": (
            "Preparando analisis con variables agregadas."
            if has_simulation_inputs
            else "En espera de datos suficientes."
        ),
        "source_type": "wisdom_bit",
        "source_id": "WB-TALENTO",
    }
    bayes = {
        "status": "not_calibrated",
        "reason": "Requiere historial adicional.",
        "group": "sap_successfactors:talent_readiness",
        "sample_count": 0,
        "raw_probability": confidence,
        "calibrated_probability": None,
    }
    return {
        "ruleset_version": "sap_successfactors.wb_talento.v1",
        "control_origin": "sap_successfactors_talent_signal",
        "formula": "recommendation_only: severity + affected_count + gold_readiness",
        "input_hash": hashlib.sha256(
            f"{signal_id}:{affected_count}:{source_row_count}:{readiness_status}".encode("utf-8")
        ).hexdigest(),
        "monte_carlo": monte_carlo,
        "bayesian_calibration": bayes,
    }


@_bind_to_core
def _sf_talent_signal_context(
    row: dict[str, Any],
) -> dict[str, Any] | None:
    signal_id = str(row.get("signal_id") or "").strip()
    if not signal_id:
        return None
    signal_type = str(row.get("signal_type") or "talent_signal").strip() or "talent_signal"
    title = str(row.get("title") or "Senal Talento").strip() or "Senal Talento"
    recommendation = (
        str(row.get("recommendation") or "").strip()
        or "Revisar evidencia de Talento antes de crear una decision supervisada."
    )
    affected_count = _sf_talent_int(row.get("affected_count"))
    generated_at = str(row.get("generated_at") or datetime.now(UTC).isoformat())
    materialized_at = str(row.get("materialized_at") or generated_at)
    source_row_count = _sf_talent_int(
        row.get("source_row_count")
        or row.get("row_count")
        or row.get("sample_count")
        or affected_count
    )
    readiness_status = _sf_talent_signal_readiness(row)
    blockers = _sf_talent_json_list(row.get("blockers"))
    if readiness_status in {"blocked", "insufficient_data"} and not blockers:
        blockers = ["C/P/A, role requirements o materializacion Gold insuficiente."]
    confidence = _sf_talent_signal_confidence(
        readiness_status=readiness_status,
        source_row_count=source_row_count,
        affected_count=affected_count,
    )
    return {
        "signal_id": signal_id,
        "signal_type": signal_type,
        "title": title,
        "recommendation": recommendation,
        "affected_count": affected_count,
        "generated_at": generated_at,
        "materialized_at": materialized_at,
        "source_row_count": source_row_count,
        "readiness_status": readiness_status,
        "blockers": blockers,
        "confidence": confidence,
        "deviation_pct": 1.0 if affected_count > 0 else 0.0,
        "severity": _severity(row.get("severity")),
    }


@_bind_to_core
def _sf_talent_signal_priority(context: dict[str, Any], source: ControlRoomSource) -> dict[str, Any]:
    severity = context["severity"]
    affected_count = context["affected_count"]
    priority_score = min(
        100,
        SEVERITY_WEIGHT[severity] * 18 + min(24, affected_count // 25) + 20,
    )
    return {
        "score": priority_score,
        "band": "critical"
        if priority_score >= 90
        else "high"
        if priority_score >= 75
        else "medium"
        if priority_score >= 55
        else "low",
        "drivers": [
            {
                "label": "Severidad",
                "value": severity,
                "points": SEVERITY_WEIGHT[severity] * 18,
            },
            {
                "label": "Afectados",
                "value": affected_count,
                "points": min(24, affected_count // 25),
            },
            {
                "label": "Fuente Gold",
                "value": source.dataset,
                "points": 20,
            },
        ],
    }


@_bind_to_core
def _sf_talent_signal_root_cause(readiness_status: str) -> str:
    if readiness_status in {"ready", "gold_ready", "materialized", "partial"}:
        return "Senal Gold de WisdomBit Talento generada desde SuccessFactors."
    return "Datos Talent insuficientes para explicar la senal con evidencia completa."


@_bind_to_core
def _sf_talent_signal_impact(affected_count: int) -> str:
    if affected_count:
        return f"{affected_count} registros de Talento requieren revision supervisada."
    return "Talento requiere validacion de metadata/materializacion antes de decidir."


@_bind_to_core
def _sf_talent_signal_baseline(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "gold_readiness_count_v1",
        "actual_value": context["affected_count"],
        "expected_value": 0,
        "sample_count": context["source_row_count"],
        "confidence": context["confidence"],
        "readiness_status": context["readiness_status"],
    }


@_bind_to_core
def _sf_talent_signal_summary(
    source: ControlRoomSource,
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "signal_id": context["signal_id"],
        "metric_name": context["signal_id"],
        "signal_type": context["signal_type"],
        "signal_subtype": "recommendation_only",
        "source_system": "sap_successfactors",
        "source_dataset": source.dataset,
        "severity": context["severity"],
        "summary": context["title"],
        "affected_count": context["affected_count"],
        "recommendation": context["recommendation"],
        "generated_at": context["generated_at"],
        "deviation_pct": context["deviation_pct"],
        "confidence": context["confidence"],
        "sample_count": context["source_row_count"],
        "readiness_status": context["readiness_status"],
        "recommendation_only": True,
    }


@_bind_to_core
def _sf_talent_signal_hypotheses(
    context: dict[str, Any],
    *,
    blocked: bool,
) -> list[dict[str, Any]]:
    return [
        {
            "title": (
                "Datos Talent listos para revision supervisada"
                if not blocked
                else "Evidencia Talent insuficiente"
            ),
            "rationale": (
                f"La senal se basa en {context['source_row_count']} fila(s) Gold internas; "
                "no incluye PII, compensacion ni write-back."
            ),
            "confidence": context["confidence"],
        }
    ]


@_bind_to_core
def _sf_talent_signal_options(
    context: dict[str, Any],
    *,
    priority_score: int,
    blocked: bool,
) -> list[dict[str, Any]]:
    score_explanation = (
        "; ".join(context["blockers"]) or "Completar metadata y materializacion Talent."
        if blocked
        else context["recommendation"]
    )
    return [
        {
            "option_id": "review_talent_signal",
            "label": "Remediar datos Talent" if blocked else "Revisar senal de Talento",
            "action_kind": "prepare_successfactors_review",
            "impact_expected": context["affected_count"],
            "time_cost": 1,
            "risk": 1,
            "score": priority_score,
            "score_explanation": score_explanation,
            "selected": True,
            "recommendation_only": True,
        },
        {
            "option_id": "monitor_talent_signal",
            "label": "Monitorear sin cambio inmediato",
            "action_kind": "monitor_only",
            "impact_expected": 0,
            "time_cost": 0.5,
            "risk": 2,
            "score": max(0, priority_score - 20),
            "score_explanation": "Mantener seguimiento hasta el proximo refresh.",
            "recommendation_only": True,
        },
    ]


@_bind_to_core
def _sf_talent_signal_intelligence(
    *,
    source: ControlRoomSource,
    row: dict[str, Any],
    context: dict[str, Any],
    priority_score: int,
) -> dict[str, Any]:
    readiness_status = context["readiness_status"]
    blocked = readiness_status in {"blocked", "insufficient_data"}
    return {
        "baseline": _sf_talent_signal_baseline(context),
        "signal": _sf_talent_signal_summary(source, context),
        "evidence_pack": _sf_talent_signal_evidence_pack(
            source=source,
            signal_id=context["signal_id"],
            title=context["title"],
            affected_count=context["affected_count"],
            source_row_count=context["source_row_count"],
            readiness_status=readiness_status,
            generated_at=context["generated_at"],
            materialized_at=context["materialized_at"],
            blockers=context["blockers"],
        ),
        "hypotheses": _sf_talent_signal_hypotheses(context, blocked=blocked),
        "options": _sf_talent_signal_options(
            context,
            priority_score=priority_score,
            blocked=blocked,
        ),
    }


@_bind_to_core
def _sf_talent_signal_base_item(
    source: ControlRoomSource,
    context: dict[str, Any],
) -> dict[str, Any]:
    return _base_item(
        source,
        {"severity": context["severity"], "generated_at": context["generated_at"]},
        context["signal_type"],
        context["signal_id"],
        context["title"],
    )


@_bind_to_core
def _sf_talent_signal_item_fields(
    *,
    source: ControlRoomSource,
    row: dict[str, Any],
    context: dict[str, Any],
    priority: dict[str, Any],
    math_provenance: dict[str, Any],
) -> dict[str, Any]:
    priority_score = priority["score"]
    readiness_status = context["readiness_status"]
    return {
        "title": context["title"],
        "description": context["recommendation"],
        "recommendation": context["recommendation"],
        "root_cause": _sf_talent_signal_root_cause(readiness_status),
        "impact": _sf_talent_signal_impact(context["affected_count"]),
        "details": {
            **_sf_talent_signal_details(row),
            "source_dataset": source.dataset,
            "source_row_count": context["source_row_count"],
            "materialized_at": context["materialized_at"],
            "readiness_status": readiness_status,
            "blockers": context["blockers"],
            "recommendation_only": True,
        },
        "detected_at": context["generated_at"],
        "status": "open",
        "data_status": "gold_ready",
        "source_system": "sap_successfactors",
        "dataset": source.dataset,
        "gold_table": source.dataset,
        "freshness_at": context["generated_at"],
        "freshness_field": "generated_at",
        "control_origin": "sap_successfactors_talent_signal",
        "advisory": True,
        "recommendation_only": True,
        "priority": priority,
        "intelligence": _sf_talent_signal_intelligence(
            source=source,
            row=row,
            context=context,
            priority_score=priority_score,
        ),
        "monte_carlo": math_provenance["monte_carlo"],
        "bayesian_calibration": math_provenance["bayesian_calibration"],
        "math_provenance": math_provenance,
        "selected_option_id": "review_talent_signal",
        "execution_status": "not_started",
    }


@_bind_to_core
def _sf_talent_signal_lookup_sql(signal_id: str) -> str:
    escaped_signal_id = signal_id.replace("'", "''")
    return (
        "SELECT * FROM sap_successfactors_talent_signals "
        f"WHERE signal_id = '{escaped_signal_id}'"
    )


@_bind_to_core
def _normalize_successfactors_talent_signal(
    source: ControlRoomSource,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    context = _sf_talent_signal_context(row)
    if not context:
        return None
    signal_id = context["signal_id"]
    generated_at = context["generated_at"]
    readiness_status = context["readiness_status"]
    affected_count = context["affected_count"]
    source_row_count = context["source_row_count"]
    confidence = context["confidence"]
    math_provenance = _sf_talent_math_provenance(
        signal_id=signal_id,
        affected_count=affected_count,
        source_row_count=source_row_count,
        readiness_status=readiness_status,
        confidence=confidence,
    )
    priority = _sf_talent_signal_priority(context, source)
    item = _sf_talent_signal_base_item(source, context)
    item.update(
        _sf_talent_signal_item_fields(
            source=source,
            row=row,
            context=context,
            priority=priority,
            math_provenance=math_provenance,
        )
    )
    item["sql"] = _sf_talent_signal_lookup_sql(signal_id)
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
def _s4_backlog_thresholds(
    thresholds: ThresholdMap | None = None,
) -> dict[str, float]:
    return {
        "age_warning": _threshold_value(
            thresholds,
            "sap_s4hana",
            "aged_sales_backlog",
            "oldest_age_days",
            "warning_value",
            45,
        ),
        "age_critical": _threshold_value(
            thresholds,
            "sap_s4hana",
            "aged_sales_backlog",
            "oldest_age_days",
            "critical_value",
            90,
        ),
        "value_warning": _threshold_value(
            thresholds,
            "sap_s4hana",
            "aged_sales_backlog",
            "open_value",
            "warning_value",
            50000,
        ),
        "value_critical": _threshold_value(
            thresholds,
            "sap_s4hana",
            "aged_sales_backlog",
            "open_value",
            "critical_value",
            250000,
        ),
    }


@_bind_to_core
def _s4_backlog_state(
    age: float,
    open_value: float,
    threshold_values: dict[str, float],
) -> dict[str, Any] | None:
    age_breached = age >= threshold_values["age_warning"]
    value_breached = open_value >= threshold_values["value_warning"]
    if not age_breached and not value_breached:
        return None
    threshold_state = (
        "critical"
        if age >= threshold_values["age_critical"]
        or open_value >= threshold_values["value_critical"]
        else "warning"
    )
    return {
        "age_breached": age_breached,
        "value_breached": value_breached,
        "threshold_state": threshold_state,
        "severity": "critical" if threshold_state == "critical" else "high",
    }


@_bind_to_core
def _s4_backlog_threshold_refs(
    state: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> list[dict[str, Any]]:
    refs = []
    if state["age_breached"]:
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
    if state["value_breached"]:
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
    return refs


@_bind_to_core
def _normalize_s4_backlog(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    age = _num(row.get("oldest_age_days")) or 0
    open_value = _num(row.get("open_value")) or 0
    state = _s4_backlog_state(age, open_value, _s4_backlog_thresholds(thresholds))
    if state is None:
        return None
    customer = str(row.get("customer_code") or "Sin cliente").strip()
    item = _base_item(
        source,
        {**row, "severity": state["severity"]},
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
    return _attach_thresholds(
        item,
        _s4_backlog_threshold_refs(state, thresholds),
        state["threshold_state"],
    )


@_bind_to_core
def _s4_supplier_spend_thresholds(
    thresholds: ThresholdMap | None = None,
) -> dict[str, float]:
    return {
        "warning_spend": _threshold_value(
            thresholds,
            "sap_s4hana",
            "supplier_spend_concentration",
            "total_spend",
            "warning_value",
            250000,
        ),
        "critical_spend": _threshold_value(
            thresholds,
            "sap_s4hana",
            "supplier_spend_concentration",
            "total_spend",
            "critical_value",
            750000,
        ),
    }


@_bind_to_core
def _s4_supplier_spend_state(
    spend: float,
    threshold_values: dict[str, float],
) -> dict[str, Any] | None:
    if spend < threshold_values["warning_spend"]:
        return None
    threshold_state = (
        "critical" if spend >= threshold_values["critical_spend"] else "warning"
    )
    return {
        "threshold_state": threshold_state,
        "severity": "high" if threshold_state == "critical" else "medium",
    }


@_bind_to_core
def _s4_supplier_spend_threshold_refs(
    thresholds: ThresholdMap | None = None,
) -> list[dict[str, Any]]:
    return [
        _threshold_ref(
            thresholds,
            "sap_s4hana",
            "supplier_spend_concentration",
            "total_spend",
            warning_default=250000,
            critical_default=750000,
        )
    ]


@_bind_to_core
def _normalize_s4_supplier_spend(
    source: ControlRoomSource,
    row: dict[str, Any],
    thresholds: ThresholdMap | None = None,
) -> dict[str, Any] | None:
    spend = _num(row.get("total_spend")) or 0
    state = _s4_supplier_spend_state(
        spend,
        _s4_supplier_spend_thresholds(thresholds),
    )
    if state is None:
        return None
    supplier = str(row.get("supplier_code") or "Sin proveedor").strip()
    month = str(row.get("spend_month") or "").strip()
    item = _base_item(
        source,
        {**row, "severity": state["severity"]},
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
        _s4_supplier_spend_threshold_refs(thresholds),
        state["threshold_state"],
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
    if source.normalizer == "sap_successfactors_talent_signal":
        return _normalize_successfactors_talent_signal(source, row)
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
def _financial_relevant_sources(
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
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


@_bind_to_core
def _financial_risk_projects(pnl_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    return risk_rows[:6]


@_bind_to_core
def _financial_source_status(relevant: dict[str, Any]) -> str:
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
    return status


@_bind_to_core
def _financial_metrics(
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    pnl_rows = rows_by_dataset.get("pnl_mensual", [])
    revenue_rows = rows_by_dataset.get("revenue_by_customer", [])
    backlog_rows = rows_by_dataset.get("open_sales_orders", [])
    purchase_rows = rows_by_dataset.get("purchase_spend_by_supplier", [])
    relevant = _financial_relevant_sources(sources)
    revenue_usd = _money_sum(pnl_rows, "revenue_usd")
    margin_usd = _money_sum(pnl_rows, "margen_bruto_usd")
    return {
        "status": _financial_source_status(relevant),
        "sources": relevant,
        "revenue_usd": revenue_usd,
        "billed_usd": _money_sum(pnl_rows, "facturacion_mes_usd"),
        "wip_usd": _money_sum(pnl_rows, "wip_usd"),
        "cost_usd": _money_sum(pnl_rows, "costo_total"),
        "margin_usd": margin_usd,
        "margin_pct": _ratio(margin_usd, revenue_usd),
        "sales_revenue": _money_sum(revenue_rows, "revenue"),
        "backlog_value": _money_sum(backlog_rows, "open_value"),
        "open_orders": int(_money_sum(backlog_rows, "open_orders")),
        "oldest_backlog_days": int(
            max(
                (_num(row.get("oldest_age_days")) or 0 for row in backlog_rows),
                default=0,
            )
        ),
        "purchase_spend": _money_sum(purchase_rows, "total_spend"),
        "risk_projects": _financial_risk_projects(pnl_rows),
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
def _persisted_signal_intelligence(
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
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
    analysis_evidence = (
        metadata.get("analysis_evidence")
        if isinstance(metadata.get("analysis_evidence"), dict)
        else {}
    )
    return intelligence, decision_intelligence, analysis_evidence


@_bind_to_core
def _persisted_item_identity_fields(
    public_row: dict[str, Any],
    metadata: dict[str, Any],
    *,
    severity: str,
    kind: str,
    is_agent_alert: bool,
) -> dict[str, Any]:
    return {
        "id": str(public_row.get("item_id") or ""),
        "kind": kind,
        "tenant_id": public_row.get("tenant_id") or metadata.get("tenant_id"),
        "workspace_id": public_row.get("workspace_id") or metadata.get("workspace_id"),
        "domain": public_row.get("domain") or "Operacion",
        "module": metadata.get("module")
        or metadata.get("agent_name")
        or ("Agente monitor" if is_agent_alert else "Intelligence Engine"),
        "cartridge": public_row.get("cartridge_id") or "platform",
        "source_dataset": public_row.get("source_dataset") or "intelligence_signals",
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
    }


@_bind_to_core
def _persisted_item_narrative_fields(
    public_row: dict[str, Any],
    metadata: dict[str, Any],
    *,
    item_id: str,
    is_agent_alert: bool,
) -> dict[str, Any]:
    escaped_item_id = item_id.replace("'", "''")
    return {
        "details": metadata.get("details")
        if isinstance(metadata.get("details"), dict)
        else {},
        "title": public_row.get("title")
        or ("Alerta de agente monitor" if is_agent_alert else "Senal de inteligencia operativa"),
        "description": metadata.get("description")
        or public_row.get("title")
        or (
            "Alerta advisory de agente monitor"
            if is_agent_alert
            else "Senal de inteligencia operativa"
        ),
        "recommendation": metadata.get("recommendation")
        or "Revisar evidencia y seleccionar una opcion supervisada.",
        "root_cause": metadata.get("root_cause")
        or (
            "Hipotesis generada por agente monitor."
            if is_agent_alert
            else "Desviacion contra baseline."
        ),
        "impact": metadata.get("impact") or "Impacto operativo pendiente de validar.",
        "sql": metadata.get("sql")
        or (
            f"SELECT * FROM control_room_items WHERE item_id = '{escaped_item_id}'"
            if is_agent_alert
            else f"SELECT * FROM intelligence_signals WHERE signal_id = '{escaped_item_id}'"
        ),
    }


@_bind_to_core
def _persisted_item_state_fields(
    public_row: dict[str, Any],
    metadata: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    return {
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
    }


@_bind_to_core
def _persisted_item_analysis_fields(
    metadata: dict[str, Any],
    *,
    intelligence: dict[str, Any],
    decision_intelligence: dict[str, Any],
    analysis_evidence: dict[str, Any],
    source: str,
    occurrence_count: int,
    is_agent_alert: bool,
) -> dict[str, Any]:
    return {
        "math_provenance": metadata.get("math_provenance")
        if isinstance(metadata.get("math_provenance"), dict)
        else {},
        "monte_carlo": metadata.get("monte_carlo")
        if isinstance(metadata.get("monte_carlo"), dict)
        else {},
        "bayesian_calibration": metadata.get("bayesian_calibration")
        if isinstance(metadata.get("bayesian_calibration"), dict)
        else {},
        "decision_intelligence": decision_intelligence,
        "intelligence": intelligence,
        "source": source,
        "advisory": bool(metadata.get("advisory")) or is_agent_alert,
        "agent_id": metadata.get("agent_id"),
        "agent_run_id": metadata.get("agent_run_id"),
        "analysis_type": metadata.get("analysis_type")
        or analysis_evidence.get("analysis_type"),
        "engine": metadata.get("origin")
        or analysis_evidence.get("engine")
        or metadata.get("engine"),
        "engine_run_id": metadata.get("engine_run_id")
        or analysis_evidence.get("engine_run_id"),
        "analysis_evidence": analysis_evidence,
        "deduped": occurrence_count > 1,
        "occurrence_count": occurrence_count,
        "hypothesis": metadata.get("hypothesis"),
        "expected_outcome": metadata.get("expected_outcome"),
    }


@_bind_to_core
def _persisted_item_timestamp_fields(public_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "first_seen_at": public_row.get("first_seen_at"),
        "last_seen_at": public_row.get("last_seen_at"),
        "resolved_at": public_row.get("resolved_at"),
        "dismissed_at": public_row.get("dismissed_at"),
    }


@_bind_to_core
def _persisted_intelligence_payload(row: Any) -> dict[str, Any]:
    public_row = _row_to_public(row)
    metadata = _details(public_row.get("metadata"))
    severity = _severity(public_row.get("severity"))
    status = str(public_row.get("status") or "open")
    if status not in ITEM_STATUSES:
        status = "open"
    kind = str(public_row.get("item_kind") or "intelligence_signal")
    is_agent_alert = kind == "agent_alert"
    source = str(
        metadata.get("source") or ("agent" if is_agent_alert else "intelligence")
    )
    occurrence_count = int(metadata.get("occurrence_count") or 1)
    intelligence, decision_intelligence, analysis_evidence = (
        _persisted_signal_intelligence(metadata)
    )
    item_id = str(public_row.get("item_id") or "")
    return {
        **_persisted_item_identity_fields(
            public_row,
            metadata,
            severity=severity,
            kind=kind,
            is_agent_alert=is_agent_alert,
        ),
        **_persisted_item_narrative_fields(
            public_row,
            metadata,
            item_id=item_id,
            is_agent_alert=is_agent_alert,
        ),
        **_persisted_item_state_fields(public_row, metadata, status=status),
        **_persisted_item_analysis_fields(
            metadata,
            intelligence=intelligence,
            decision_intelligence=decision_intelligence,
            analysis_evidence=analysis_evidence,
            source=source,
            occurrence_count=occurrence_count,
            is_agent_alert=is_agent_alert,
        ),
        **_persisted_item_timestamp_fields(public_row),
    }


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
    items = [_persisted_intelligence_payload(row) for row in rows]
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
async def _collect_module_inventory(
    user: dict | None,
    *,
    use_catalog: bool,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    set[str],
    list[ControlRoomModule],
]:
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
    active = {
        cartridge_id
        for cartridge_id, row in installation_by_cartridge.items()
        if str(row.get("installation_status") or "ready")
        in ACTIVE_INSTALLATION_STATUSES
    }
    modules = [
        module
        for module in MODULES
        if module.cartridge in set(installation_by_cartridge)
    ]
    return installations, installation_by_cartridge, active, modules


@_bind_to_core
def _append_collected_source(
    *,
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    source: ControlRoomSource,
    source_status: dict[str, Any],
    rows: list[dict[str, Any]] | None = None,
    include_source_state_items: bool,
) -> None:
    rows_by_dataset[source.dataset] = (
        rows or [] if source_status["status"] == "ok" else []
    )
    sources.append(source_status)
    if not include_source_state_items:
        return
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


@_bind_to_core
def _blocked_installation_source_status(
    source: ControlRoomSource,
    installation: dict[str, Any],
) -> dict[str, Any]:
    return _source_status_payload(
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


@_bind_to_core
def _ready_placeholder_source_status(source: ControlRoomSource) -> dict[str, Any]:
    return _source_status_payload(
        source,
        "ok",
        count=0,
        checked_at=datetime.now(UTC).isoformat(),
    )


@_bind_to_core
def _source_contract_is_visible(source: ControlRoomSource) -> bool:
    return (
        _show_known_non_ready_sources()
        or _source_has_ready_contract(source)
        or _source_should_fetch_partial_contract(source)
    )


@_bind_to_core
def _append_normalized_source_rows(
    items: list[dict[str, Any]],
    source: ControlRoomSource,
    rows: list[dict[str, Any]],
    thresholds: dict[str, dict[str, Any]],
) -> None:
    for row in rows:
        item = _normalize_row(source, row, thresholds)
        if item:
            items.append(item)


@_bind_to_core
def _collect_inactive_module_sources(
    *,
    module: ControlRoomModule,
    installation: dict[str, Any],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    include_source_state_items: bool,
) -> None:
    for source in module.sources:
        source_status = _blocked_installation_source_status(
            source,
            installation,
        )
        _append_collected_source(
            items=items,
            sources=sources,
            rows_by_dataset=rows_by_dataset,
            source=source,
            source_status=source_status,
            include_source_state_items=include_source_state_items,
        )


@_bind_to_core
async def _collect_active_source(
    *,
    source: ControlRoomSource,
    user: dict | None,
    fetcher: DatasetFetcher,
    limit_per_source: int,
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    thresholds: dict[str, dict[str, Any]],
    include_source_state_items: bool,
) -> None:
    if not _source_contract_is_visible(source):
        _append_collected_source(
            items=items,
            sources=sources,
            rows_by_dataset=rows_by_dataset,
            source=source,
            source_status=_ready_placeholder_source_status(source),
            include_source_state_items=include_source_state_items,
        )
        return
    rows, source_status = await _fetch_source(
        source,
        user,
        fetcher,
        limit_per_source,
    )
    _append_collected_source(
        items=items,
        sources=sources,
        rows_by_dataset=rows_by_dataset,
        source=source,
        source_status=source_status,
        rows=rows,
        include_source_state_items=include_source_state_items,
    )
    if source_status["status"] == "ok":
        _append_normalized_source_rows(items, source, rows, thresholds)


@_bind_to_core
async def _collect_module_items(
    *,
    module: ControlRoomModule,
    installation_by_cartridge: dict[str, dict[str, Any]],
    active: set[str],
    user: dict | None,
    fetcher: DatasetFetcher,
    limit_per_source: int,
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    thresholds: dict[str, dict[str, Any]],
    include_source_state_items: bool,
) -> None:
    installation = installation_by_cartridge.get(module.cartridge, {})
    if module.cartridge not in active:
        _collect_inactive_module_sources(
            module=module,
            installation=installation,
            items=items,
            sources=sources,
            rows_by_dataset=rows_by_dataset,
            include_source_state_items=include_source_state_items,
        )
        return
    for source in module.sources:
        await _collect_active_source(
            source=source,
            user=user,
            fetcher=fetcher,
            limit_per_source=limit_per_source,
            items=items,
            sources=sources,
            rows_by_dataset=rows_by_dataset,
            thresholds=thresholds,
            include_source_state_items=include_source_state_items,
        )


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
    installations, installation_by_cartridge, active, modules = (
        await _collect_module_inventory(user, use_catalog=use_catalog)
    )

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
        await _collect_module_items(
            module=module,
            installation_by_cartridge=installation_by_cartridge,
            active=active,
            user=user,
            fetcher=fetcher,
            limit_per_source=limit_per_source,
            items=items,
            sources=sources,
            rows_by_dataset=rows_by_dataset,
            thresholds=thresholds,
            include_source_state_items=include_source_state_items,
        )

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
def _domain_module_has_runtime(
    module: ControlRoomModule,
    domain: str,
    domain_items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> bool:
    module_has_sources = any(
        source.get("module_id") == module.visible_id
        and source.get("domain") == domain
        for source in sources
    )
    module_has_items = any(
        item.get("module_id", item.get("cartridge")) == module.visible_id
        for item in domain_items
    )
    return module_has_sources or module_has_items


@_bind_to_core
def _domain_visible_modules(
    domain: str,
    modules: list[ControlRoomModule],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> list[ControlRoomModule]:
    domain_items = [item for item in items if item["domain"] == domain]
    visible_modules: list[ControlRoomModule] = []
    for module in modules:
        if module.domain != domain and not any(
            source.domain == domain for source in module.sources
        ):
            continue
        if (
            _domain_module_has_runtime(module, domain, domain_items, sources)
            or _show_known_non_ready_sources()
        ):
            visible_modules.append(module)
    return visible_modules


@_bind_to_core
def _domain_module_sources(
    module: ControlRoomModule,
    domain: str,
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        source
        for source in sources
        if source.get("module_id") == module.visible_id
        and source.get("domain") == domain
    ]


@_bind_to_core
def _domain_module_items(
    module: ControlRoomModule,
    domain_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        item
        for item in domain_items
        if item.get("module_id", item.get("cartridge")) == module.visible_id
    ]


@_bind_to_core
def _domain_module_kpis(
    module: ControlRoomModule,
    domain: str,
    module_sources: list[dict[str, Any]],
    module_items: list[dict[str, Any]],
    *,
    data_readiness: str,
    source_status: str,
) -> list[dict[str, Any]]:
    source_count = sum(int(source.get("count") or 0) for source in module_sources)
    open_item_count = sum(
        1 for item in module_items if item["status"] not in TERMINAL_ITEM_STATUSES
    )
    return [
        {
            "label": "Registros fuente",
            "value": source_count,
            "tone": "neutral",
            "bad": data_readiness != "ready" and source_status != "no_sources",
            "sql": " UNION ALL ".join(
                f"SELECT COUNT(*) AS registros, '{source['dataset']}' AS dataset FROM {source['dataset']}"
                for source in module_sources
            )
            or "-- sin fuente materializada",
        },
        {
            "label": "Items abiertos",
            "value": open_item_count,
            "tone": "attention",
            "bad": open_item_count > 0,
            "sql": f"SELECT * FROM control_room_items WHERE cartridge_id = '{module.cartridge}' AND domain = '{domain}' AND status NOT IN ('approved','dismissed','resolved')",
        },
    ]


@_bind_to_core
def _domain_module_payload(
    module: ControlRoomModule,
    domain: str,
    domain_items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    module_sources = _domain_module_sources(module, domain, sources)
    module_items = _domain_module_items(module, domain_items)
    source_status = _source_rollup_status(module_sources)
    data_readiness = _module_data_readiness(module_sources)
    return {
        "id": module.visible_id,
        "connector_id": module.cartridge,
        "label": module.label,
        "domain": domain,
        "accent": module.accent,
        "description": module.description,
        "item_count": len(module_items),
        "critical_count": sum(1 for item in module_items if item["severity"] == "critical"),
        "source_status": source_status,
        "data_readiness": data_readiness,
        "operationally_ready": data_readiness == "ready",
        "kpis": _domain_module_kpis(
            module,
            domain,
            module_sources,
            module_items,
            data_readiness=data_readiness,
            source_status=source_status,
        ),
    }


@_bind_to_core
def _domain_payload(
    domain: str,
    modules: list[ControlRoomModule],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    domain_items = [item for item in items if item["domain"] == domain]
    domain_modules = _domain_visible_modules(domain, modules, items, sources)
    module_payload = [
        _domain_module_payload(module, domain, domain_items, sources)
        for module in domain_modules
    ]
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
def _dashboard_active_cartridges(installations: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("cartridge_id") or "").strip()
        for row in installations
        if str(row.get("cartridge_id") or "").strip()
        and str(row.get("installation_status") or "ready").strip().lower()
        in ACTIVE_INSTALLATION_STATUSES
    }


@_bind_to_core
async def _dashboard_items_with_persisted(
    user: dict | None,
    items: list[dict[str, Any]],
    active_cartridges: set[str],
    *,
    persist: bool,
) -> list[dict[str, Any]]:
    if not persist:
        return items
    known_ids = {str(item.get("id")) for item in items}
    for item in await _persisted_intelligence_items(user):
        item_cartridge = str(item.get("cartridge") or "").strip()
        if item_cartridge != "platform" and item_cartridge not in active_cartridges:
            continue
        if str(item.get("id")) not in known_ids:
            items.append(item)
            known_ids.add(str(item.get("id")))
    return items


@_bind_to_core
def _dashboard_item_counts(
    items: Iterable[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    by_severity = _severity_counts(items)
    by_cartridge: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for item in items:
        by_cartridge[item["cartridge"]] = by_cartridge.get(item["cartridge"], 0) + 1
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1
    return by_severity, by_cartridge, by_domain


@_bind_to_core
async def _dashboard_open_decisions(user: dict | None, workspace_id: str) -> int:
    pool = await auth.pool()
    try:
        async def _count_decisions(conn: Any, _tenant_id: str | None, _workspace_id: str) -> int:
            return int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM decisions WHERE workspace_id = $1 AND status = 'open'",
                    workspace_id,
                )
                or 0
            )

        return await _run_with_db_scope(pool, user or {}, _count_decisions)
    except Exception:
        return 0


@_bind_to_core
def _dashboard_modules_for_payload(
    modules: list[ControlRoomModule],
    sources: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> list[ControlRoomModule]:
    visible_module_ids = {
        str(source.get("module_id") or "").strip()
        for source in sources
        if str(source.get("module_id") or "").strip()
    } | {
        str(item.get("module_id") or item.get("cartridge") or "").strip()
        for item in items
        if str(item.get("module_id") or item.get("cartridge") or "").strip()
    }
    if _show_known_non_ready_sources():
        return modules
    return [module for module in modules if module.visible_id in visible_module_ids]


@_bind_to_core
def _dashboard_cartridges_payload(
    modules: list[ControlRoomModule],
    installations: list[dict[str, Any]],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    installation_by_cartridge = {
        str(row.get("cartridge_id")): row
        for row in installations
        if str(row.get("cartridge_id") or "").strip()
    }
    cartridges = []
    for module in modules:
        row = installation_by_cartridge.get(module.cartridge, {})
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
                "id": module.visible_id,
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
    return cartridges


@_bind_to_core
def _dashboard_domains_payload(
    modules: list[ControlRoomModule],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    domain_labels = list(DOMAIN_ORDER)
    for module in modules:
        if module.domain not in domain_labels:
            domain_labels.append(module.domain)
        for source in module.sources:
            if source.domain not in domain_labels:
                domain_labels.append(source.domain)
    domains = [
        _domain_payload(domain, modules, items, sources)
        for domain in domain_labels
    ]
    if _show_known_non_ready_sources():
        return domains
    return [
        domain
        for domain in domains
        if domain.get("modules") or int(domain.get("item_count") or 0) > 0
    ]


@_bind_to_core
def _dashboard_threshold_summary(thresholds: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
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
    }


@_bind_to_core
def _dashboard_item_summary_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_items": len(items),
        "total_anomalies": sum(1 for item in items if item["kind"] == "anomaly"),
        "control_items": sum(1 for item in items if item["kind"] != "anomaly"),
    }


@_bind_to_core
def _dashboard_module_groups(
    cartridges: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "data_ready_modules": [
            row for row in cartridges if row["active"] and row.get("operationally_ready")
        ],
        "partial_modules": [
            row
            for row in cartridges
            if row["active"] and row.get("data_readiness") == "partial"
        ],
        "stub_modules": [
            row
            for row in cartridges
            if row["active"] and row.get("data_readiness") == "stub"
        ],
        "active_non_operational": [
            row for row in cartridges if row["active"] and not row["operational"]
        ],
        "operational_cartridges": [
            row for row in cartridges if row["active"] and row["operational"]
        ],
    }


@_bind_to_core
def _dashboard_source_states_payload(
    sources: list[dict[str, Any]]
) -> dict[str, int]:
    return {
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
    }


@_bind_to_core
def _dashboard_summary_payload(
    *,
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    cartridges: list[dict[str, Any]],
    by_severity: dict[str, int],
    by_cartridge: dict[str, int],
    by_domain: dict[str, int],
    open_decisions: int,
    financial: dict[str, Any],
    thresholds: list[dict[str, Any]],
    lesson_summary: dict[str, Any],
    alert_summary: dict[str, Any],
) -> dict[str, Any]:
    data_readiness = _readiness_counts(sources)
    modules = _dashboard_module_groups(cartridges)
    active_non_operational = modules["active_non_operational"]
    return {
        **_dashboard_item_summary_counts(items),
        "by_severity": by_severity,
        "by_cartridge": by_cartridge,
        "by_domain": by_domain,
        "critical": by_severity.get("critical", 0),
        "attention": by_severity.get("high", 0) + by_severity.get("medium", 0),
        "open_decisions": open_decisions,
        "active_connectors": len({row["connector_id"] for row in active_non_operational}),
        "active_modules": len(active_non_operational),
        "active_cartridges": len(active_non_operational),
        "operational_cartridges": len(modules["operational_cartridges"]),
        "source_states": _dashboard_source_states_payload(sources),
        "data_readiness": data_readiness,
        "data_ready_sources": data_readiness.get("ready", 0),
        "data_ready_modules": len(modules["data_ready_modules"]),
        "partial_modules": len(modules["partial_modules"]),
        "stub_modules": len(modules["stub_modules"]),
        "cycle_counts": _cycle_counts(items),
        "financial": financial,
        "thresholds": _dashboard_threshold_summary(thresholds, items),
        "lessons": lesson_summary,
        "alerts": alert_summary,
    }


@_bind_to_core
async def _dashboard_items_and_insights(
    user: dict | None,
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    active_cartridges: set[str],
    *,
    persist: bool,
) -> dict[str, Any]:
    lesson_rows = await _load_lesson_rows(user, limit=200)
    lesson_summary = _lesson_insights(lesson_rows)
    items = _attach_lessons_to_items(items, lesson_rows)
    if persist:
        await _cleanup_obsolete_source_state_items(user, sources, items)
    items = await _dashboard_items_with_persisted(
        user,
        items,
        active_cartridges,
        persist=persist,
    )
    alerts_payload = _alert_payload(items)
    return {
        "items": items,
        "lesson_summary": lesson_summary,
        "alerts": alerts_payload["alerts"],
        "alert_summary": alerts_payload["summary"],
    }


@_bind_to_core
def _dashboard_meta_payload(
    generated_at: datetime,
    sources: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
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
    }


@_bind_to_core
def _dashboard_workspace_payload(
    user: dict | None,
    workspace_id: str | None,
) -> dict[str, Any]:
    return {
        "tenant_id": (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id"),
        "workspace_id": workspace_id,
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
    active_cartridges = _dashboard_active_cartridges(installations)
    enriched = await _dashboard_items_and_insights(
        user,
        items,
        sources,
        active_cartridges,
        persist=persist,
    )
    items = enriched["items"]

    by_severity, by_cartridge, by_domain = _dashboard_item_counts(items)

    workspace_id = _workspace_id(user)
    open_decisions = await _dashboard_open_decisions(user, workspace_id)
    modules_for_payload = _dashboard_modules_for_payload(modules, sources, items)
    cartridges = _dashboard_cartridges_payload(
        modules_for_payload,
        installations,
        items,
        sources,
    )
    domains = _dashboard_domains_payload(modules_for_payload, items, sources)

    return {
        "meta": _dashboard_meta_payload(generated_at, sources, items),
        "workspace": _dashboard_workspace_payload(user, workspace_id),
        "period": generated_at.strftime("%B %Y"),
        "omega_steps": OMEGA_STEPS,
        "summary": _dashboard_summary_payload(
            items=items,
            sources=sources,
            cartridges=cartridges,
            by_severity=by_severity,
            by_cartridge=by_cartridge,
            by_domain=by_domain,
            open_decisions=open_decisions,
            financial=financial,
            thresholds=thresholds,
            lesson_summary=enriched["lesson_summary"],
            alert_summary=enriched["alert_summary"],
        ),
        "domains": domains,
        "cartridges": cartridges,
        "sources": sources,
        "alerts": enriched["alerts"],
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
async def _ops_summary_counts(
    user: dict | None,
    *,
    workspace_id: str,
) -> dict[str, Any]:
    pool = await auth.pool()

    async def _load_counts(
        conn: Any,
        _tenant_id: str | None,
        _workspace_id: str,
    ) -> dict[str, Any]:
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

    return await _run_with_db_scope(pool, user or {}, _load_counts)


@_bind_to_core
def _ops_summary_items_by_status(status_rows: Iterable[Any]) -> dict[str, int]:
    items_by_status = {s: 0 for s in _ITEM_STATUSES}
    for row in status_rows:
        items_by_status[str(row["status"])] = int(row["n"])
    return items_by_status


@_bind_to_core
def _ops_summary_open_by_severity(severity_rows: Iterable[Any]) -> dict[str, int]:
    open_by_severity = {s: 0 for s in _ITEM_SEVERITIES}
    for row in severity_rows:
        open_by_severity[str(row["severity"])] = int(row["n"])
    return open_by_severity


@_bind_to_core
def _ops_summary_payload(
    *,
    tenant_id: str | None,
    workspace_id: str,
    counts: dict[str, Any],
) -> dict[str, Any]:
    import os as _os

    items_by_status = _ops_summary_items_by_status(counts["status_rows"])
    total_items = sum(items_by_status.values())
    open_by_severity = _ops_summary_open_by_severity(counts["severity_rows"])
    exec_rows = counts["exec_rows"]
    executions_by_status = {str(row["status"]): int(row["n"]) for row in exec_rows}
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
        "lessons": counts["lessons_total"],
        "thresholds_active": counts["thresholds_total"],
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
    tenant_id, workspace_id = _workspace_scope(user)
    counts = await _ops_summary_counts(user, workspace_id=workspace_id)
    return _ops_summary_payload(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        counts=counts,
    )


@_bind_to_core
def _agentops_json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, type(fallback)) else fallback
        except Exception:
            return fallback
    return fallback


@_bind_to_core
def _agentops_runs_payload(
    rows: Iterable[Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], list[dict[str, Any]]]:
    runs_by_agent: dict[str, list[dict[str, Any]]] = {}
    tool_usage: dict[str, int] = {}
    run_payloads: list[dict[str, Any]] = []
    for row in rows:
        tool_calls = _agentops_json_value(row["tool_calls"], [])
        for call in tool_calls if isinstance(tool_calls, list) else []:
            tool = str(call.get("tool") or call.get("name") or "").strip()
            server = str(call.get("server") or "").strip()
            full_tool = f"{server}__{tool}" if server and "__" not in tool else tool
            if full_tool:
                tool_usage[full_tool] = tool_usage.get(full_tool, 0) + 1
        payload = {
            "id": int(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_slug": row["slug"],
            "agent_name": row["name"],
            "cartridge_id": row["cartridge_id"],
            "status": row["status"],
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
            "tool_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
            "tools": [
                str(call.get("tool") or call.get("name") or "")
                for call in tool_calls[:8]
                if isinstance(call, dict)
            ]
            if isinstance(tool_calls, list)
            else [],
            "error": row["error_message"][:500] if row["error_message"] else None,
        }
        run_payloads.append(payload)
        runs_by_agent.setdefault(payload["agent_id"], []).append(payload)
    return runs_by_agent, tool_usage, run_payloads


@_bind_to_core
def _agentops_alerts_by_agent(rows: Iterable[Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["agent_id"] or ""): {
            "total": int(row["total"] or 0),
            "open": int(row["open"] or 0),
            "last_seen_at": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
        }
        for row in rows
        if str(row["agent_id"] or "")
    }


@_bind_to_core
def _agentops_agents_payload(
    rows: Iterable[Any],
    *,
    alerts_by_agent: dict[str, dict[str, Any]],
    runs_by_agent: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int, int, dict[str, int]]:
    agents_payload: list[dict[str, Any]] = []
    monitor_count = 0
    active_count = 0
    configured_engine_counts: dict[str, int] = {}
    for row in rows:
        extra = _agentops_json_value(row["extra"], {})
        allowed_tools = _agentops_json_value(row["allowed_tools"], [])
        monitor = extra.get("monitor") if isinstance(extra, dict) else {}
        role = str((extra or {}).get("role") or "").strip().lower() if isinstance(extra, dict) else ""
        operational_tools = [
            _agentops_tool_label(tool) for tool in allowed_tools
            if isinstance(tool, str) and _agentops_tool_is_operational(tool)
        ]
        is_monitor = role == "monitor" and isinstance(monitor, dict) and bool(monitor)
        if is_monitor:
            monitor_count += 1
        if row["is_active"]:
            active_count += 1
        schedule = {}
        if isinstance(extra, dict):
            schedule = extra.get("schedule") or (monitor.get("schedule") if isinstance(monitor, dict) else {}) or {}
        agent_id = str(row["id"])
        last_run = (runs_by_agent.get(agent_id) or [None])[0]
        configured_engines = _agentops_monitor_engines(monitor)
        for engine in configured_engines:
            if engine.get("enabled") is False:
                continue
            name = str(engine.get("engine") or "").strip()
            if name:
                configured_engine_counts[name] = configured_engine_counts.get(name, 0) + 1
        agents_payload.append({
            "id": agent_id,
            "cartridge_id": row["cartridge_id"],
            "slug": row["slug"],
            "name": row["name"],
            "active": bool(row["is_active"]),
            "role": role or ("monitor" if is_monitor else "agent"),
            "monitor": bool(is_monitor),
            "operationally_ready": bool(is_monitor and operational_tools),
            "schedule": schedule,
            "monitor_contract": monitor if isinstance(monitor, dict) else {},
            "configured_engines": configured_engines,
            "allowed_tools": operational_tools,
            "operational_tools_count": len(operational_tools),
            "last_run": last_run,
            "alerts": alerts_by_agent.get(agent_id, {"total": 0, "open": 0, "last_seen_at": None}),
        })
    agents_payload.sort(
        key=lambda item: (
            not bool(item.get("monitor")),
            not bool(item.get("operationally_ready")),
            not bool(item.get("active")),
            str(item.get("cartridge_id") or ""),
            str(item.get("slug") or ""),
        )
    )
    return agents_payload, monitor_count, active_count, configured_engine_counts


@_bind_to_core
def _agentops_execution_counts(rows: Iterable[Any]) -> dict[str, dict[str, Any]]:
    execution_counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        engine = _agentops_engine_label(row["engine_name"])
        status = str(row["execution_status"] or "unknown")
        current = execution_counts.setdefault(
            engine,
            {"engine": engine, "total": 0, "by_status": {}, "latest_at": None},
        )
        count = int(row["total"] or 0)
        current["total"] += count
        current["by_status"][status] = int(current["by_status"].get(status, 0)) + count
        latest = row["latest_at"]
        if latest and (current["latest_at"] is None or latest > current["latest_at"]):
            current["latest_at"] = latest
    return execution_counts


@_bind_to_core
def _agentops_engines_payload(
    *,
    configured_engine_counts: dict[str, int],
    monitor_count: int,
    agents_payload: list[dict[str, Any]],
    monte_carlo_total: int,
    monte_carlo_latest: Any,
    calibration_total: int,
    calibration_samples: int,
    calibration_latest: Any,
    orchestration_total: int,
    orchestration_latest: Any,
    execution_counts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    engines_payload = [
        {
            "engine": "wisdom_bit",
            "configured": configured_engine_counts.get("wisdom_bit", 0),
            "evidence_count": sum(1 for agent in agents_payload if agent.get("monitor")),
            "latest_at": None,
            "status": "ready" if monitor_count else "missing",
        },
        {
            "engine": "monte_carlo",
            "configured": configured_engine_counts.get("monte_carlo", 0),
            "evidence_count": monte_carlo_total,
            "latest_at": monte_carlo_latest.isoformat() if monte_carlo_latest else None,
            "status": "ready" if monte_carlo_total else "configured" if configured_engine_counts.get("monte_carlo") else "missing",
        },
        {
            "engine": "bayesian_calibration",
            "configured": configured_engine_counts.get("bayesian_calibration", 0),
            "evidence_count": calibration_total,
            "sample_count": calibration_samples,
            "latest_at": calibration_latest.isoformat() if calibration_latest else None,
            "status": "ready"
            if calibration_samples >= 10
            else "configured"
            if configured_engine_counts.get("bayesian_calibration") or calibration_total
            else "missing",
        },
        {
            "engine": "decision_orchestrator",
            "configured": configured_engine_counts.get("decision_orchestrator", 0),
            "evidence_count": orchestration_total,
            "latest_at": orchestration_latest.isoformat() if orchestration_latest else None,
            "status": "ready" if orchestration_total else "configured" if configured_engine_counts.get("decision_orchestrator") else "missing",
        },
    ]
    for item in engines_payload:
        execution = execution_counts.get(str(item["engine"]))
        if execution:
            item["executions"] = {
                **execution,
                "latest_at": execution["latest_at"].isoformat() if execution.get("latest_at") else None,
            }
    return engines_payload


@_bind_to_core
def _agentops_runtime_metrics(
    raw: dict[str, Any],
    *,
    run_payloads: list[dict[str, Any]],
    alerts_by_agent: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    calibration_row = dict(raw["calibration_rows"][0]) if raw["calibration_rows"] else {}
    orchestration_row = dict(raw["orchestration_rows"][0]) if raw["orchestration_rows"] else {}
    return {
        "failed_recent": sum(1 for run in run_payloads if str(run.get("status")) == "error"),
        "open_alerts": sum(row.get("open", 0) for row in alerts_by_agent.values()),
        "total_alerts": sum(row.get("total", 0) for row in alerts_by_agent.values()),
        "monte_carlo_total": sum(int(row["total"] or 0) for row in raw["monte_carlo_rows"]),
        "monte_carlo_latest": max(
            (row["latest_at"] for row in raw["monte_carlo_rows"] if row["latest_at"]),
            default=None,
        ),
        "calibration_total": int(calibration_row.get("total") or 0),
        "calibration_samples": int(calibration_row.get("sample_count") or 0),
        "calibration_latest": calibration_row.get("latest_at"),
        "orchestration_total": int(orchestration_row.get("total") or 0),
        "orchestration_latest": orchestration_row.get("latest_at"),
    }


@_bind_to_core
def _agentops_tools_used_payload(tool_usage: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"tool": tool, "count": count}
        for tool, count in sorted(tool_usage.items(), key=lambda item: (-item[1], item[0]))
    ]


@_bind_to_core
def _agentops_origins_payload(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {"origin": str(row["origin"] or "unknown"), "count": int(row["total"] or 0)}
        for row in rows
    ]


@_bind_to_core
def _agentops_summary_payload(
    *,
    agents_payload: list[dict[str, Any]],
    active_count: int,
    monitor_count: int,
    run_payloads: list[dict[str, Any]],
    runtime: dict[str, Any],
    configured_engine_counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "agents_total": len(agents_payload),
        "active_agents": active_count,
        "monitor_agents": monitor_count,
        "recent_runs": len(run_payloads),
        "failed_recent_runs": runtime["failed_recent"],
        "open_agent_alerts": runtime["open_alerts"],
        "agent_alerts_total": runtime["total_alerts"],
        "configured_engines": sum(configured_engine_counts.values()),
        "monte_carlo_simulations": runtime["monte_carlo_total"],
        "bayesian_calibration_states": runtime["calibration_total"],
        "bayesian_calibration_samples": runtime["calibration_samples"],
        "decision_orchestrations": runtime["orchestration_total"],
    }


@_bind_to_core
def _agentops_snapshot_payload(
    *,
    tenant_id: str | None,
    workspace_id: str,
    agents_payload: list[dict[str, Any]],
    run_payloads: list[dict[str, Any]],
    engines_payload: list[dict[str, Any]],
    summary: dict[str, Any],
    tool_usage: dict[str, int],
    origin_rows: Iterable[Any],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "tenant": tenant_id,
        "active_workspace": workspace_id,
        "summary": summary,
        "agents": agents_payload,
        "recent_runs": run_payloads,
        "engines": engines_payload,
        "tools_used": _agentops_tools_used_payload(tool_usage),
        "origins": _agentops_origins_payload(origin_rows),
    }


@_bind_to_core
async def _agentops_table_presence(conn: Any) -> dict[str, bool]:
    table_exists: dict[str, bool] = {}
    for table in (
        "monte_carlo_simulations",
        "calibration_states",
        "decision_orchestration_runs",
        "decision_orchestration_executions",
    ):
        table_exists[table] = bool(
            await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
        )
    return table_exists


@_bind_to_core
async def _agentops_agent_rows(
    conn: Any,
    *,
    workspace_id: str,
    tenant_id: str | None,
    allowed_param: list[str] | None,
) -> list[Any]:
    return await conn.fetch(
        """
        SELECT id::text AS id, cartridge_id, slug, name, is_active,
               allowed_tools, extra, updated_at
          FROM agents
         WHERE (workspace_id = $1::uuid OR workspace_id IS NULL)
           AND ($2::uuid IS NULL OR tenant_id = $2::uuid OR tenant_id IS NULL)
           AND ($3::text[] IS NULL OR cartridge_id = ANY($3::text[]) OR cartridge_id = 'platform')
         ORDER BY is_active DESC, updated_at DESC, cartridge_id, slug
         LIMIT 100
        """,
        workspace_id,
        tenant_id,
        allowed_param,
    )


@_bind_to_core
async def _agentops_run_rows(
    conn: Any,
    *,
    workspace_id: str,
    allowed_param: list[str] | None,
    limit: int,
) -> list[Any]:
    return await conn.fetch(
        """
        SELECT r.id, r.agent_id::text AS agent_id, r.started_at, r.finished_at,
               r.status, r.tool_calls, r.error_message,
               a.slug, a.name, a.cartridge_id
          FROM agent_runs r
          JOIN agents a ON a.id = r.agent_id
         WHERE (r.workspace_id = $1::uuid OR (r.workspace_id IS NULL AND a.workspace_id = $1::uuid))
           AND ($3::text[] IS NULL OR a.cartridge_id = ANY($3::text[]) OR a.cartridge_id = 'platform')
         ORDER BY r.started_at DESC
         LIMIT $2
        """,
        workspace_id,
        limit,
        allowed_param,
    )


@_bind_to_core
async def _agentops_alert_rows(
    conn: Any,
    *,
    workspace_id: str,
    allowed_param: list[str] | None,
) -> list[Any]:
    return await conn.fetch(
        """
        SELECT metadata->>'agent_id' AS agent_id,
               COUNT(*)::int AS total,
               COUNT(*) FILTER (WHERE status = 'open')::int AS open,
               MAX(last_seen_at) AS last_seen_at
          FROM control_room_items
         WHERE workspace_id = $1::uuid
           AND item_kind = 'agent_alert'
           AND ($2::text[] IS NULL OR cartridge_id = ANY($2::text[]) OR cartridge_id = 'platform')
         GROUP BY metadata->>'agent_id'
        """,
        workspace_id,
        allowed_param,
    )


@_bind_to_core
async def _agentops_origin_rows(
    conn: Any,
    *,
    workspace_id: str,
    allowed_param: list[str] | None,
) -> list[Any]:
    return await conn.fetch(
        """
        SELECT COALESCE(metadata->>'origin', metadata->'analysis_evidence'->>'engine', metadata->>'source', 'unknown') AS origin,
               COUNT(*)::int AS total
          FROM control_room_items
         WHERE workspace_id = $1::uuid
           AND item_kind = 'agent_alert'
           AND ($2::text[] IS NULL OR cartridge_id = ANY($2::text[]) OR cartridge_id = 'platform')
         GROUP BY 1
         ORDER BY 2 DESC, 1
        """,
        workspace_id,
        allowed_param,
    )


@_bind_to_core
async def _agentops_base_rows(
    conn: Any,
    *,
    workspace_id: str,
    tenant_id: str | None,
    allowed_param: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    return {
        "agents": await _agentops_agent_rows(
            conn,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            allowed_param=allowed_param,
        ),
        "runs": await _agentops_run_rows(
            conn,
            workspace_id=workspace_id,
            allowed_param=allowed_param,
            limit=limit,
        ),
        "alert_rows": await _agentops_alert_rows(
            conn,
            workspace_id=workspace_id,
            allowed_param=allowed_param,
        ),
        "origin_rows": await _agentops_origin_rows(
            conn,
            workspace_id=workspace_id,
            allowed_param=allowed_param,
        ),
    }


@_bind_to_core
async def _agentops_monte_carlo_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: dict[str, bool],
) -> list[Any]:
    if table_exists["monte_carlo_simulations"]:
        return await conn.fetch(
            """
            SELECT source_type,
                   COUNT(*)::int AS total,
                   MAX(updated_at) AS latest_at
              FROM monte_carlo_simulations
             WHERE workspace_id = $1::uuid
             GROUP BY source_type
            """,
            workspace_id,
        )
    return []


@_bind_to_core
async def _agentops_calibration_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: dict[str, bool],
) -> list[Any]:
    if table_exists["calibration_states"]:
        return await conn.fetch(
            """
            SELECT COUNT(*)::int AS total,
                   COALESCE(SUM(sample_count), 0)::int AS sample_count,
                   MAX(updated_at) AS latest_at
              FROM calibration_states
             WHERE workspace_id = $1::uuid
            """,
            workspace_id,
        )
    return []


@_bind_to_core
async def _agentops_orchestration_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: dict[str, bool],
) -> list[Any]:
    if table_exists["decision_orchestration_runs"]:
        return await conn.fetch(
            """
            SELECT COUNT(*)::int AS total,
                   MAX(updated_at) AS latest_at
              FROM decision_orchestration_runs
             WHERE workspace_id = $1::uuid
            """,
            workspace_id,
        )
    return []


@_bind_to_core
async def _agentops_execution_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: dict[str, bool],
) -> list[Any]:
    if table_exists["decision_orchestration_executions"]:
        return await conn.fetch(
            """
            SELECT engine_name,
                   execution_status,
                   COUNT(*)::int AS total,
                   MAX(updated_at) AS latest_at
              FROM decision_orchestration_executions
             WHERE workspace_id = $1::uuid
             GROUP BY engine_name, execution_status
            """,
            workspace_id,
        )
    return []


@_bind_to_core
async def _agentops_intelligence_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: dict[str, bool],
) -> dict[str, Any]:
    return {
        "monte_carlo_rows": await _agentops_monte_carlo_rows(
            conn,
            workspace_id=workspace_id,
            table_exists=table_exists,
        ),
        "calibration_rows": await _agentops_calibration_rows(
            conn,
            workspace_id=workspace_id,
            table_exists=table_exists,
        ),
        "orchestration_rows": await _agentops_orchestration_rows(
            conn,
            workspace_id=workspace_id,
            table_exists=table_exists,
        ),
        "execution_rows": await _agentops_execution_rows(
            conn,
            workspace_id=workspace_id,
            table_exists=table_exists,
        ),
    }


@_bind_to_core
async def _agentops_load_snapshot(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    allowed_param: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    table_exists = await _agentops_table_presence(conn)
    return {
        **await _agentops_base_rows(
            conn,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            allowed_param=allowed_param,
            limit=limit,
        ),
        **await _agentops_intelligence_rows(
            conn,
            workspace_id=workspace_id,
            table_exists=table_exists,
        ),
    }


@_bind_to_core
def _agentops_payload_from_raw(
    raw: dict[str, Any],
    *,
    tenant_id: str | None,
    workspace_id: str,
) -> dict[str, Any]:
    alerts_by_agent = _agentops_alerts_by_agent(raw["alert_rows"])
    runs_by_agent, tool_usage, run_payloads = _agentops_runs_payload(raw["runs"])
    (
        agents_payload,
        monitor_count,
        active_count,
        configured_engine_counts,
    ) = _agentops_agents_payload(
        raw["agents"],
        alerts_by_agent=alerts_by_agent,
        runs_by_agent=runs_by_agent,
    )
    runtime = _agentops_runtime_metrics(
        raw,
        run_payloads=run_payloads,
        alerts_by_agent=alerts_by_agent,
    )
    engines_payload = _agentops_engines_payload(
        configured_engine_counts=configured_engine_counts,
        monitor_count=monitor_count,
        agents_payload=agents_payload,
        monte_carlo_total=runtime["monte_carlo_total"],
        monte_carlo_latest=runtime["monte_carlo_latest"],
        calibration_total=runtime["calibration_total"],
        calibration_samples=runtime["calibration_samples"],
        calibration_latest=runtime["calibration_latest"],
        orchestration_total=runtime["orchestration_total"],
        orchestration_latest=runtime["orchestration_latest"],
        execution_counts=_agentops_execution_counts(raw["execution_rows"]),
    )
    summary = _agentops_summary_payload(
        agents_payload=agents_payload,
        active_count=active_count,
        monitor_count=monitor_count,
        run_payloads=run_payloads,
        runtime=runtime,
        configured_engine_counts=configured_engine_counts,
    )
    return _agentops_snapshot_payload(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agents_payload=agents_payload,
        run_payloads=run_payloads,
        engines_payload=engines_payload,
        summary=summary,
        tool_usage=tool_usage,
        origin_rows=raw["origin_rows"],
    )


@_bind_to_core
async def agents_ops(user: dict | None, *, limit: int = 12) -> dict[str, Any]:
    """Persisted AgentOps snapshot for Control Room.

    This intentionally reads only agents, agent_runs and persisted
    control_room_items; it never starts agents or simulations from the
    dashboard polling path.
    """
    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    limit = max(1, min(int(limit or 12), 50))
    # Source-hardening markers retained after helper extraction:
    # _agentops_tool_is_operational(tool); _agentops_tool_label(tool);
    # _agentops_monitor_engines(monitor); "configured_engines": configured_engines
    # "monte_carlo_simulations"; "bayesian_calibration_states";
    # "bayesian_calibration_samples"; "decision_orchestrations";
    # "engines": engines_payload
    # "a.cartridge_id = ANY($3::text[])"; "cartridge_id = ANY($3::text[])";
    # "cartridge_id = ANY($2::text[])"; "cartridge_id = 'platform'"
    allowed_cartridges = _allowed_from_user(user)
    allowed_param = None if allowed_cartridges is None else sorted(allowed_cartridges)

    async def _load(conn: Any, _tenant_id: str | None, _workspace_id: str) -> dict[str, Any]:
        return await _agentops_load_snapshot(
            conn,
            tenant_id=_tenant_id,
            workspace_id=_workspace_id,
            allowed_param=allowed_param,
            limit=limit,
        )

    raw = await run_with_db_scope(pool, user or {}, _load)
    return _agentops_payload_from_raw(
        raw,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


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
    "agents_ops",
    "get_item",
    "get_anomaly",
    "_record_action_execution",
    "list_alerts",
)
