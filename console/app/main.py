"""
ΩMEGA by EPIUSE — Console
MCP-first: descubre y orquesta MCP servers registrados.
UI minimalista: chat con asistente + estado de servidores MCP.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import secrets
import time
import uuid
from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Sprint v1.18: structured JSON logs to stdout, with secret redaction
# applied to every record. Imported and called here (rather than at the
# bottom of imports) so the logger configured below is the JSON one
# from the very first record.
from app.logging_config import setup_logging  # noqa: E402

setup_logging(service_name="console")

import httpx
from app.domains.apps.payloads import (
    app_cartridge_id as _app_cartridge_id,
    app_datasets_from_payload as _app_datasets_from_payload,
    app_declared_datasets as _app_declared_datasets,
    app_payload_cartridge_candidates as _app_payload_cartridge_candidates,
    apps_from_payload as _apps_from_payload,
    filter_apps_payload_to_ready_datasets as _filter_apps_payload_to_ready_datasets,
    filter_apps_payload_to_scoped_connections as _filter_apps_payload_to_scoped_connections,
    user_with_apps_scope as _user_with_apps_scope,
)
from app.domains.apps.scope import (
    installed_scoped_app_cartridges as _installed_scoped_app_cartridges_impl,
    normalize_candidate_cartridges as _normalize_candidate_cartridges,
    resolve_scoped_config_cartridge as _resolve_scoped_config_cartridge_impl,
    resolve_scoped_operation_cartridge as _resolve_scoped_operation_cartridge_impl,
    scope_catalog_cartridge_arg as _scope_catalog_cartridge_arg_impl,
    workspace_scope_for_apps_filter as _workspace_scope_for_apps_filter_impl,
)
from app.domains.apps.embed import (
    app_content_headers as _app_content_headers,
    app_embed_csp as _app_embed_csp,
    app_embed_wrapper_html as _app_embed_wrapper_html,
    datasets_from_app_html as _datasets_from_app_html,
)
from app.domains.agentops.successfactors_talent_monitor import (
    SUCCESSFACTORS_TALENT_MONITOR_SLUG as _SUCCESSFACTORS_TALENT_MONITOR_SLUG,
    coerce_successfactors_talent_monitor_payload as _agentops_coerce_successfactors_talent_monitor_payload,
    ensure_successfactors_talent_monitor as _agentops_ensure_successfactors_talent_monitor,
    has_operational_monitor_contract as _agentops_has_operational_monitor_contract,
    is_successfactors_talent_monitor_row as _agentops_is_successfactors_talent_monitor_row,
    merge_agent_tools as _agentops_merge_agent_tools,
    successfactors_talent_monitor_contract as _agentops_successfactors_talent_monitor_contract,
    successfactors_talent_monitor_needs_runtime_repair as _agentops_successfactors_talent_monitor_needs_runtime_repair,
    sync_agentops_is_terminal as _agentops_sync_agentops_is_terminal,
    sync_agentops_monitor_candidates as _agentops_sync_agentops_monitor_candidates,
)
from app.domains.agentops.invocation import (
    agent_invoke_background_requested as _agent_invoke_background_requested_impl,
    agent_invoke_background_response as _agent_invoke_background_response_impl,
    agent_schedule_due as _agent_schedule_due_impl,
    parse_agent_scheduled_fire_at as _parse_agent_scheduled_fire_at_impl,
)
from app.domains.accounts.lifecycle import (
    normalize_email_or_400 as _normalize_email_or_400_impl,
    pack_vpn_conf as _pack_vpn_conf_impl,
    password_min_length as _password_min_length_impl,
    safe_filename as _safe_filename_impl,
    token_link as _token_link,
    validate_password_or_400 as _validate_password_or_400_impl,
    vpn_configured as _vpn_configured_impl,
    vpn_token_link as _vpn_token_link,
)
from app.domains.copilot.llm_keys import llm_secret_keys as _llm_secret_keys_impl
from app.domains.decisions.access import (
    can_delete_decision as _dec_can_delete_impl,
    can_edit_decision as _dec_can_edit_impl,
    current_workspace_id as _current_workspace_id_impl,
    is_decision_workspace_admin as _dec_is_workspace_admin_impl,
)
from app.domains.decisions.payloads import (
    coerce_date as _coerce_date_impl,
    coerce_datetime as _coerce_dt_impl,
    decision_row_to_dict as _dec_row_to_dict_impl,
)
from app.domains.iam.roles import (
    GLOBAL_ASSIGNABLE_ROLES as _IAM_GLOBAL_ASSIGNABLE_ROLES,
    WORKSPACE_ASSIGNABLE_ROLES as _IAM_WORKSPACE_ASSIGNABLE_ROLES,
    assignable_role as _assignable_role_impl,
    is_global_iam_admin as _is_global_iam_admin_impl,
    session_workspace_ids as _session_workspace_ids_impl,
    workspace_scope_db_unavailable as _workspace_scope_db_unavailable_impl,
)
from app.domains.iam.access_payload import (
    fetch_cartridge_access as _fetch_cartridge_access,
    me_access_payload as _me_access_payload,
)
from app.domains.data_platform.catalog_payloads import (
    catalog_cache_key as _catalog_cache_key,
    catalog_query_args as _catalog_query_args,
)
from app.domains.data_platform.data_api_payloads import (
    DataApiQueryValidationError as _DataApiQueryValidationError,
    data_api_columns_param as _data_api_columns_param,
    data_api_filtered_query as _data_api_filtered_query,
    data_api_invalid_column as _data_api_invalid_column,
    data_api_options_response as _data_api_options_response,
    data_api_options_sql as _data_api_options_sql,
    data_api_query_limit as _data_api_query_limit,
    data_api_valid_dataset_name as _data_api_valid_dataset_name,
)
from app.domains.data_platform.gold_catalog import (
    GoldCatalogRuntime as _GoldCatalogRuntime,
    empty_catalog_payload as _empty_catalog_payload,
    gold_dataset_from_source as _gold_dataset_from_source,
)
from app.domains.data_platform.lineage_payloads import (
    lineage_graph_payload as _lineage_graph_payload,
)
from app.domains.data_platform.explorer_access import (
    explorer_path_allowed as _explorer_path_allowed_impl,
    explorer_quicklinks_for_cartridges as _explorer_quicklinks_for_cartridges_impl,
    resolve_explorer_bucket as _resolve_explorer_bucket_impl,
)
from app.domains.data_platform.refinement_errors import (
    raise_for_refinement_payload_error as _raise_for_refinement_payload_error,
    upstream_error_detail as _upstream_error_detail,
)
from app.domains.data_platform.schema_payloads import (
    bronze_schema_payload as _bronze_schema_payload,
    dataset_detail_columns as _dataset_detail_columns,
    empty_partitions as _empty_partitions,
    empty_preview as _empty_preview,
    gold_schema_error_payload as _gold_schema_error_payload,
    normalize_dataset_detail as _normalize_dataset_detail,
    preview_has_columns as _preview_has_columns,
    schema_error as _schema_error,
    schema_message as _schema_message,
    schema_payload_warnings as _schema_payload_warnings,
    schema_status as _schema_status,
)
from app.domains.data_platform.rag_payloads import (
    rag_empty_answer as _rag_empty_answer,
    rag_search_arguments as _rag_search_arguments,
    rag_synthesis_messages as _rag_synthesis_messages,
)
from app.domains.data_platform.scoped_reads import (
    BRONZE_LOGICAL_READ_PARQUET_CALL_RE as _BRONZE_LOGICAL_READ_PARQUET_CALL_RE,
    BRONZE_LOGICAL_READ_PARQUET_PATH_RE as _BRONZE_LOGICAL_READ_PARQUET_PATH_RE,
    BRONZE_READ_PARQUET_SOURCE_RE as _BRONZE_READ_PARQUET_SOURCE_RE,
    SAFE_BRONZE_SOURCE_SEGMENT_RE as _SAFE_BRONZE_SOURCE_SEGMENT_RE,
    SCOPED_READ_CACHE as _SCOPED_READ_CACHE,
    SCOPED_READ_CACHE_LOCKS as _SCOPED_READ_CACHE_LOCKS,
    bronze_latest_date_from_objects as _bronze_latest_date_from_objects,
    bronze_latest_s3_glob as _bronze_latest_s3_glob,
    bronze_bucket_name as _bronze_bucket_name,
    bronze_source_from_reader_path as _bronze_source_from_reader_path,
    infer_bronze_sources_from_sql as _infer_bronze_sources_from_sql,
    merge_declared_and_inferred_bronze_sources as _merge_declared_and_inferred_bronze_sources,
    rewrite_bronze_logical_paths as _rewrite_bronze_logical_paths,
    safe_pipeline_name as _safe_pipeline_name,
    scoped_bronze_s3_path as _scoped_bronze_s3_path,
    scoped_cache_identity as _scoped_cache_identity,
    scoped_read_cache_get as _scoped_read_cache_get,
    scoped_read_cache_get_or_set as _scoped_read_cache_get_or_set,
    scoped_read_cache_invalidate as _scoped_read_cache_invalidate,
    scoped_read_cache_key as _scoped_read_cache_key,
    scoped_read_cache_set as _scoped_read_cache_set,
    scoped_read_cache_ttl as _scoped_read_cache_ttl,
    workspace_scope_from_user as _workspace_scope_from_user,
)
from app.domains.data_platform.semantic_enrichment import (
    semantic_enrichment_candidates as _semantic_enrichment_candidates,
    semantic_enrichment_empty_response as _semantic_enrichment_empty_response,
    semantic_enrichment_limit as _semantic_enrichment_limit,
    semantic_enrichment_success_response as _semantic_enrichment_success_response,
)
from app.domains.data_platform.semantic_payloads import (
    semantic_manifest_response as _semantic_manifest_response,
)
from app.domains.data_platform.source_visibility import (
    OPERATIONAL_CARTRIDGES as _OPERATIONAL_CARTRIDGES,
    allowed_cartridges_for_user as _allowed_cartridges_for_user,
    context_visible_cartridges as _context_visible_cartridges,
    dataset_source_visible_for_user as _dataset_source_visible_for_user,
    filter_technical_sources as _filter_technical_sources,
    is_security_admin_context as _is_security_admin_context,
    is_workspace_scoped_user as _is_workspace_scoped_user,
    require_technical_cartridge_access as _require_technical_cartridge_access,
    require_technical_source_access as _require_technical_source_access,
    require_workspace_scope_for_technical_view as _require_workspace_scope_for_technical_view,
    sanitize_dataset_metadata_for_user as _sanitize_dataset_metadata_for_user_impl,
    sanitize_datasets_payload_for_user as _sanitize_datasets_payload_for_user_impl,
    user_allowed_cartridges as _user_allowed_cartridges,
)
from app.domains.data_platform.table_metadata import (
    table_has_column as _table_has_column_impl,
)
from app.domains.security.request_classification import (
    is_agent_runner_request as _is_agent_runner_request_impl,
    is_api_like as _is_api_like_impl,
    is_direct_static_html_request as _is_direct_static_html_request_impl,
    uses_rbac_dependency as _uses_rbac_dependency_impl,
)
from app.domains.security.internal_auth import (
    internal_cartridge_headers as _internal_cartridge_headers_impl,
    internal_outbound_headers as _internal_outbound_headers_impl,
    internal_outbound_key as _internal_outbound_key_impl,
    internal_service_user as _internal_service_user_impl,
    is_internal_service_actor as _is_internal_service_actor_impl,
    require_effective_permission as _require_effective_permission_impl,
    user_payload as _user_payload_impl,
)
from app.domains.security.cors import (
    allowed_origins as _allowed_origins_impl,
)
from app.domains.system.runtime import (
    healthz_payload as _healthz_payload,
    runtime_config_payload as _runtime_config_payload,
    system_info_payload as _system_info_payload,
)
from app.domains.pipeline.run_state import (
    airflow_log_attempt as _airflow_log_attempt,
    airflow_log_task_ids as _airflow_log_task_ids,
    airflow_task_id as _airflow_task_id,
    duration_seconds as _duration_seconds,
    normalize_airflow_state as _normalize_airflow_state,
    pipeline_bronze_date_count as _pipeline_bronze_date_count,
    parse_iso_datetime as _parse_iso_datetime,
    pipeline_jobs_by_entity as _pipeline_jobs_by_entity,
    pipeline_entity_row as _pipeline_entity_row,
    pipeline_entity_run_payload as _pipeline_entity_run_payload,
    pipeline_response_payload as _pipeline_response_payload,
    pipeline_run_extra as _pipeline_run_extra,
    pipeline_silver_datasets_by_source as _pipeline_silver_datasets_by_source,
    sanitize_pipeline_run_for_user as _sanitize_pipeline_run_for_user_impl,
)
from app.domains.pipeline.scope import (
    pipeline_runs_read_conn as _pipeline_runs_read_conn_impl,
    pipeline_runs_scope_predicate as _pipeline_runs_scope_predicate_impl,
)
from app.domains.pipeline.job_payloads import (
    is_pipeline_job_payload as _is_pipeline_job_payload,
    refresh_pipeline_job_payload as _refresh_pipeline_job_payload_impl,
    refresh_pipeline_job_payloads as _refresh_pipeline_job_payloads_impl,
)
from app.domains.pipeline.recording import (
    refresh_dag_run_status as _refresh_dag_run_status_impl,
    record_dag_pipeline_trigger as _record_dag_pipeline_trigger_impl,
)
from app.domains.pipeline.successfactors_reservation import (
    active_entity_limit_payload as _sf_active_entity_limit_payload,
    active_entity_run as _sf_active_entity_run,
    active_entity_run_count as _sf_active_entity_run_count,
    active_scope_args as _sf_active_scope_args,
    extract_all_conflict as _sf_extract_all_conflict,
    reservation_applies as _sf_reservation_applies,
    reservation_extra as _sf_reservation_extra,
    reservation_run_id as _sf_reservation_run_id,
)
from app.domains.pipeline.sync_state import (
    SAP_SUCCESSFACTORS_CARTRIDGE as _SAP_SUCCESSFACTORS_CARTRIDGE,
    SAP_SUCCESSFACTORS_ENTITY_DAG_ID as _SAP_SUCCESSFACTORS_ENTITY_DAG_ID,
    SAP_SUCCESSFACTORS_EXTRACT_ALL_DAG_ID as _SAP_SUCCESSFACTORS_EXTRACT_ALL_DAG_ID,
    SYNC_AGENTOPS_TOOLS as _SYNC_AGENTOPS_TOOLS,
    SYNC_AGGREGATE_ENTITY as _SYNC_AGGREGATE_ENTITY,
    SYNC_CHILD_BLOCKED_STATUSES as _SYNC_CHILD_BLOCKED_STATUSES,
    SYNC_CHILD_TERMINAL_STATUSES as _SYNC_CHILD_TERMINAL_STATUSES,
    SYNC_EXTRACT_ALL_DAGS as _SYNC_EXTRACT_ALL_DAGS,
    SYNC_NOW_DAG_ID as _SYNC_NOW_DAG_ID,
    SYNC_NOW_ENTITY as _SYNC_NOW_ENTITY,
    SYNC_TERMINAL_STATUSES as _SYNC_TERMINAL_STATUSES,
    active_sync_run_lookup_parts as _active_sync_run_lookup_parts,
    active_extract_run_payload as _active_extract_run_payload,
    airflow_run_id_fragment as _airflow_run_id_fragment,
    inactive_sync_run_payload as _inactive_sync_run_payload,
    initial_sync_steps as _initial_sync_steps,
    merge_sync_steps as _merge_sync_steps,
    normalize_sync_now_request_id as _normalize_sync_now_request_id,
    normalize_sync_step_payload as _normalize_sync_step_payload,
    pipeline_extract_all_mode_target as _pipeline_extract_all_mode_target,
    pipeline_extract_all_public_response as _pipeline_extract_all_public_response,
    pipeline_extract_all_run_id as _pipeline_extract_all_run_id,
    sync_child_gold_refresh_summary as _sync_child_gold_refresh_summary,
    sync_child_reason as _sync_child_reason,
    sync_clean_mode as _sync_clean_mode,
    sync_clean_target as _sync_clean_target,
    sync_control_room_gold_refresh_terminal as _sync_control_room_gold_refresh_terminal,
    sync_entity_idempotency_key as _sync_entity_idempotency_key,
    sync_errors_retryable as _sync_errors_retryable,
    sync_extra_from_row as _sync_extra_from_row,
    sync_gold_refresh_dataset_names as _sync_gold_refresh_dataset_names,
    sync_now_lock_key as _sync_now_lock_key_impl,
    sync_now_run_id_from_request_id as _sync_now_run_id_from_request_id,
    sync_public_payload as _sync_public_payload,
    sync_run_age_seconds as _sync_run_age_seconds,
    sync_run_needs_final_reconcile as _sync_run_needs_final_reconcile,
    sync_status_from_steps as _sync_status_from_steps,
    sync_step_entity_summary as _sync_step_entity_summary,
)
from app.domains.pipeline.concurrency import (
    gather_by_entity as _pipeline_gather_by_entity,
)
from app.domains.pipeline.airflow_trigger import (
    trigger_airflow_extract_dag as _trigger_airflow_extract_dag_impl,
)
from app.domains.pipeline.extract_config import (
    apply_user_scope_to_dag_conf as _apply_user_scope_to_dag_conf_impl,
    build_dag_extract_conf as _build_dag_extract_conf_impl,
    connection_id_from_vault_payload as _connection_id_from_vault_payload_impl,
    dag_run_id_from_idempotency_key as _dag_run_id_from_idempotency_key_impl,
    entity_declared_in_static_catalog as _entity_declared_in_static_catalog_impl,
    is_transient_airflow_trigger_error as _is_transient_airflow_trigger_error_impl,
    normalize_pipeline_conn_id as _normalize_pipeline_conn_id_impl,
    resolve_pipeline_sync_conn_id as _resolve_pipeline_sync_conn_id_impl,
)
from app.domains.studio.access import (
    cartridge_visible_for_context as _cartridge_visible_for_context,
    has_studio_ops_write_role as _has_studio_ops_write_role_impl,
    studio_ops_role_name as _studio_ops_role_name_impl,
)
from app.domains.studio.cartridge_probe import (
    probe_microservice as _probe_microservice_impl,
)
from app.domains.studio.dag_graph import parse_dag_graph as _parse_dag_graph
from app.domains.vault.scope import (
    require_vault_scope_visible as _require_vault_scope_visible_impl,
    tenant_vault_conn_id as _tenant_vault_conn_id_impl,
    tenant_vault_display_conn as _tenant_vault_display_conn_impl,
    tenant_vault_prefix as _tenant_vault_prefix_impl,
    tenant_vault_scope as _tenant_vault_scope_impl,
)
from app.domains.vault.reveal import (
    cartridge_vault_reveal_user as _cartridge_vault_reveal_user_impl,
    is_cartridge_vault_reveal_request as _is_cartridge_vault_reveal_request_impl,
)
from app.domains.viewer.navigation import viewer_redirect_url as _viewer_redirect_url
from app.services.db_scope import scoped_db_for_user
from app.services.service_urls import (
    app_env as _app_env,
    env_float as _env_float,
    env_int as _env_int,
    is_private_public_url as _is_private_public_url,
    is_production_env as _is_production_env,
    public_url as _public_url,
    running_in_container as _running_in_container,
    service_url as _service_url,
    vault_url as _vault_url,
)
from app.services import request_rate_limits as _request_rate_limits
from app.services import security_headers as _security_headers
from app.services import sync_agentops as _sync_agentops
from app.services import sync_control_room as _sync_control_room
from app.services import sync_progress as _sync_progress
from app.services.db_pool import (
    close_main_pool as _close_main_pool,
    db_dsn as _db_dsn,
    get_db_pool as _get_db_pool,
)
from app.services.mcp_payloads import mcp_payload as _mcp_payload
from app.services.rate_limiter import get_rate_limiter
from app.services.readyz_dependencies import (
    READYZ_DEPENDENCY_CACHE as _READYZ_DEPENDENCY_CACHE,
    dependency_health as _readyz_dependency_health,
)
from app.services.readyz_data import control_room_data_check as _readyz_control_room_data_check
from app.services.runtime_calls import (
    call_with_optional_user as _call_with_optional_user,
    runtime_user as _runtime_user,
)
from app.services.startup_readiness import (
    record_startup_failure as _record_startup_failure,
    reset_startup_readiness_state as _reset_startup_readiness_state,
    run_startup_seed as _run_startup_seed,
    startup_readiness_status as _startup_readiness_status,
)
from app.services.status_pages import (
    functional_status_page as _functional_status_page,
    internal_error_request_id as _internal_error_request_id,
)
from fastapi import (
    Body,
    FastAPI,
    HTTPException,
    UploadFile,
    File,
    Request,
    Depends,
    Header,
    Query,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SECURITY_HEADERS = _security_headers.SECURITY_HEADERS
VIEWER_SECURITY_HEADERS = _security_headers.VIEWER_SECURITY_HEADERS
APP_EMBED_SECURITY_HEADERS = _security_headers.APP_EMBED_SECURITY_HEADERS
STRICT_AUTH_SECURITY_HEADERS = _security_headers.STRICT_AUTH_SECURITY_HEADERS
CONTROL_ROOM_SECURITY_HEADERS = _security_headers.CONTROL_ROOM_SECURITY_HEADERS
_STRICT_CSP_PATHS = _security_headers.STRICT_CSP_PATHS
APP_THEME_SHIM = _security_headers.APP_THEME_SHIM
APP_THEME_SCRIPT = _security_headers.APP_THEME_SCRIPT
_is_viewer_path = _security_headers.is_viewer_path
_is_app_embed_path = _security_headers.is_app_embed_path
_is_control_room_path = _security_headers.is_control_room_path
_apply_security_headers = _security_headers.apply_security_headers
_inject_published_app_theme = _security_headers.inject_published_app_theme


PIPELINE_DAG_STATUS_TIMEOUT_SEC = _env_float("PIPELINE_DAG_STATUS_TIMEOUT_SEC", 1.5)
PIPELINE_BRONZE_SNAPSHOT_TIMEOUT_SEC = _env_float(
    "PIPELINE_BRONZE_SNAPSHOT_TIMEOUT_SEC", 2.5
)
PIPELINE_DATASETS_TIMEOUT_SEC = _env_float("PIPELINE_DATASETS_TIMEOUT_SEC", 4.0)


from app.services import (
    mcp_registry,
    assistant,
    studio_assistant,
    token_store,
    job_service,
    tool_manifest,
    llm_client,
)
from app.services import cartridge_service
from app.services import agent_service as _agents
from app.services import agent_runtime as _agent_runtime
from app.services import agent_scheduler as _agent_scheduler
from app.services import auth as _auth
from app.services import tokens as _tokens
from app.services import email_service as _email
from app.services import vpn_service as _vpn
from app.services.jwt_auth import (
    JWTAuthError,
    create_access_token,
    decode_access_token,
    verify_access_token_async,
)
from app.services.csrf import (
    CSRF_COOKIE_NAME,
    require_csrf,
    set_csrf_cookie,
    clear_csrf_cookie,
)
from app.security import get_internal_api_key
from app.dependencies import (
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_WORKSPACE_ADMIN,
    _workspace_access_options,
    _workspace_cartridges,
    _workspace_memberships,
    get_current_global_user,
    get_current_user as get_current_user_dependency,
    requested_workspace_id_from_request,
    require_any_role,
    require_authenticated,
    require_global_any_role,
    require_role,
)
from app.services.auth import verify_internal_api_key
from app.services import audit_service as _audit
from app.services.permissions import (
    ROLE_DEFINITIONS,
    get_effective_permissions,
    has_permission,
    require_permission,
    workspace_role as _workspace_role,
)
from app.services.s3_client import get_boto3_s3_client, get_minio_client
from app.services.security_context import (
    build_security_context,
    rls_user_context,
    verify_signed_security_context,
)
from app.middleware.request_id import request_id_var


async def _periodic_health_check():
    """Wait for cartridges to boot, then re-check every 60 s."""
    await asyncio.sleep(12)  # grace period for sibling containers
    while True:
        try:
            await mcp_registry.health_check_all()
        except Exception:
            logger.debug("Periodic health check failed", exc_info=True)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _reset_startup_readiness_state(app)
    get_rate_limiter()
    await mcp_registry.startup()

    async def _seed_dag_sources() -> None:
        from app.services.seed_dag_sources import seed_missing_dag_sources

        pool = await _get_db_pool()
        await seed_missing_dag_sources(pool)

    async def _seed_packaged_datasets() -> None:
        from app.services.seed_packaged_datasets import seed_packaged_datasets

        pool = await _get_db_pool()
        await seed_packaged_datasets(pool)

    async def _seed_packaged_hints() -> None:
        from app.services.seed_packaged_hints import seed_packaged_hints

        pool = await _get_db_pool()
        await seed_packaged_hints(pool)

    async def _seed_packaged_apps() -> None:
        from app.services.seed_packaged_apps import seed_packaged_apps

        pool = await _get_db_pool()
        await seed_packaged_apps(pool)

    # Startup seeds reconcile packaged catalogs used by Studio, Control Room,
    # and cartridge surfaces. A failure no longer leaves Console apparently
    # ready: the process stays live, but /readyz returns 503 until the next
    # successful boot.
    await _run_startup_seed(app, "seed_missing_dag_sources", _seed_dag_sources)
    await _run_startup_seed(app, "seed_packaged_datasets", _seed_packaged_datasets)
    await _run_startup_seed(app, "seed_packaged_hints", _seed_packaged_hints)
    await _run_startup_seed(app, "seed_packaged_apps", _seed_packaged_apps)
    task = asyncio.create_task(_periodic_health_check())
    copilot_context_task: asyncio.Task | None = None
    if os.environ.get("COPILOT_CONTEXT_SCHEDULER_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }:
        from app.services import copilot_context_service

        copilot_context_task = asyncio.create_task(
            copilot_context_service.hourly_scheduler()
        )
    try:
        yield
    finally:
        task.cancel()
        if copilot_context_task is not None:
            copilot_context_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if copilot_context_task is not None:
            try:
                await copilot_context_task
            except asyncio.CancelledError:
                pass
        await _auth.close_pool()
        await _tokens.close_pool()
        await job_service.close_pool()
        await token_store.close_pool()
        await mcp_registry.close_pool()
        await cartridge_service.close_pool()
        await _agent_runtime.close_pool()
        await _close_main_pool()
        await _close_dec_pool()


INTERNAL_API_KEY = get_internal_api_key()
# INTERNAL_API_KEY is cached at service startup. Rotating it requires restarting
# Console and peer services so all in-process values are refreshed together.


def _key_for(server: str) -> str:
    """Sprint v1.12: pick the per-pair INTERNAL_API_KEY_CONSOLE_TO_<SERVER>
    secret if present, falling back to the shared legacy INTERNAL_API_KEY so
    that a half-migrated stack keeps working. ``server`` must be one of
    ``REFINEMENT`` / ``VAULT`` / ``MCP_INFRA``."""
    return _internal_outbound_key_impl(
        server,
        internal_api_key=INTERNAL_API_KEY,
        is_production=_is_production_env(),
    )


def _hdr_for(server: str) -> dict[str, str]:
    """Headers for an outbound internal call from console to ``server``."""
    return _internal_outbound_headers_impl(
        server,
        internal_api_key=INTERNAL_API_KEY,
        is_production=_is_production_env(),
        request_id=request_id_var.get(),
    )


def _is_internal_request(request: Request) -> bool:
    supplied = (
        request.headers.get("x-api-key")
        or request.headers.get("x-internal-api-key")
        or ""
    )
    service = (request.headers.get("x-internal-service") or "").strip().lower()
    if service != "console" or not supplied:
        return False
    console_to_console = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CONSOLE", "")
    if console_to_console and secrets.compare_digest(
        str(supplied), str(console_to_console)
    ):
        return True
    if _is_production_env():
        return False
    return secrets.compare_digest(str(supplied), str(INTERNAL_API_KEY))


_CARTRIDGE_VAULT_REVEAL_KEYS: dict[str, dict[str, tuple[str, ...]]] = {
    "replicon": {
        "replicon": ("INTERNAL_API_KEY_REPLICON_TO_CONSOLE",),
        "cartridge-replicon": ("INTERNAL_API_KEY_REPLICON_TO_CONSOLE",),
        "airflow": ("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",),
    },
    "hubspot": {
        "hubspot": ("INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",),
        "cartridge-hubspot": ("INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",),
        "airflow": ("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",),
    },
    "salesforce": {
        "salesforce": ("INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",),
        "cartridge-salesforce": ("INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",),
    },
    "sap_hcm": {
        "cartridge-sap_hcm": ("INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",),
    },
    "sap_s4hana": {
        "cartridge-sap_s4hana": ("INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",),
    },
    "sap_successfactors": {
        "cartridge-sap_successfactors": (
            "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
        ),
        "airflow": ("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",),
    },
}


def _is_cartridge_vault_reveal_request(request: Request) -> bool:
    return _is_cartridge_vault_reveal_request_impl(
        request,
        reveal_keys=_CARTRIDGE_VAULT_REVEAL_KEYS,
        environ=os.environ,
        is_production_env=_is_production_env,
        internal_api_key=INTERNAL_API_KEY,
    )


def _cartridge_vault_reveal_user(request: Request) -> dict | None:
    return _cartridge_vault_reveal_user_impl(
        request,
        reveal_keys=_CARTRIDGE_VAULT_REVEAL_KEYS,
        environ=os.environ,
        is_production_env=_is_production_env,
        internal_api_key=INTERNAL_API_KEY,
        internal_service_user=_internal_service_user,
        verify_signed_security_context=verify_signed_security_context,
    )


def _internal_service_user() -> dict:
    return _internal_service_user_impl(role_admin=ROLE_ADMIN)


def _user_payload(user: dict | None) -> dict | None:
    return _user_payload_impl(user, get_effective_permissions=get_effective_permissions)


async def _internal_or_authenticated(request: Request) -> dict:
    state_user = getattr(request.state, "user", None)
    if state_user:
        return state_user
    if _is_internal_request(request):
        return _internal_service_user()
    return await require_authenticated(request)


def _is_internal_service_actor(user: dict | None) -> bool:
    return _is_internal_service_actor_impl(user)


def _require_effective_permission(user: dict | None, permission: str) -> None:
    return _require_effective_permission_impl(
        user,
        permission,
        has_permission=has_permission,
    )


app = FastAPI(title="ΩMEGA by EPIUSE Console", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    path = request.url.path
    if _is_api_like(path, request.headers.get("accept", "")):
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )
    if exc.status_code in {403, 404, 503}:
        return _functional_status_page(exc.status_code, exc.detail)
    return JSONResponse(
        {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    request_id = _internal_error_request_id(request)
    logger.exception(
        "unhandled console exception request_id=%s",
        request_id,
        extra={"request_id": request_id, "exception_type": type(exc).__name__},
    )
    return JSONResponse(
        {"error": "Internal Error", "request_id": request_id},
        status_code=500,
    )


def _allowed_origins() -> list[str]:
    # Static console-next is served same-origin by FastAPI on :8000.
    # Keep workspace :8001 in the local default because it remains a
    # legitimate browser client for credentialed workspace flows.
    # Production MUST override via the env var (see prod fail-closed guard).
    default_local_origins = "http://localhost:8000,http://localhost:8001"
    return _allowed_origins_impl(
        os.environ,
        warn=logger.warning,
        default_local_origins=default_local_origins,
    )


# v1.44.3.2.2 R-Mac-3 (CORS ordering hotfix): the CORSMiddleware
# registration USED to live here at module-load time, which made it
# the FIRST middleware on user_middleware and therefore the
# INNERMOST in Starlette's reversed stack. Symptom Codex curl'd on
# the Mac:
#   $ curl -i -H "Origin: http://localhost:8001" http://localhost:8000/auth/login
#   → access-control-allow-credentials: true   ✓
#   → access-control-allow-origin:    MISSING  ✗
#
# Root cause: with CORS innermost, the OUTER auth_middleware /
# security_headers_middleware can short-circuit responses (401 /
# redirect / preflight 405) before reaching CORS, so CORS never gets
# to add Allow-Origin. Even on public paths, OPTIONS preflights
# pass through auth first.
#
# The fix moves the registration to the END of the module (after
# RequestIDMiddleware), so CORS ends up OUTERMOST in the final
# ASGI stack. See the matching block near the bottom of this file.
# This call site stays as a no-op so existing line-number references
# in comments / commit messages don't drift; the real registration
# is the only effective one.

# Sprint v1.41.1 / v1.42.1 — Request correlation IDs. The actual
# ``app.add_middleware(RequestIDMiddleware)`` call lives at the bottom
# of this module, AFTER the two ``@app.middleware("http")`` decorators
# (security headers + auth). Starlette builds its middleware stack by
# iterating ``user_middleware`` in reverse, so the LAST registered
# middleware ends up outermost. The auditor's 401-vs-X-Request-ID
# finding was caused by registering it here (which left it inside the
# auth wrapper, so 401s never reached its send-wrapper).
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _validate_dataset_name(dataset: str) -> None:
    if not _data_api_valid_dataset_name(dataset):
        raise HTTPException(400, "Invalid dataset name")


def _rls_user_context(user: dict | None) -> dict:
    return rls_user_context(user)


def _minio_client():
    return get_minio_client()


async def _count_bronze_parquet_rows(
    source: str, latest_date: str, user: dict | None
) -> int | None:
    parquet_glob = _bronze_latest_s3_glob(source, latest_date, user)
    sql = (
        "SELECT COUNT(*) AS record_count "
        f"FROM read_parquet('{parquet_glob}', hive_partitioning=true, union_by_name=true)"
    )
    async with httpx.AsyncClient(
        headers=_hdr_for("REFINEMENT"),
        timeout=60,
    ) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "preview_transform", {"sql": sql, "limit": 1, "sources": [source]}, user
            ),
        )
    if r.status_code != 200:
        return None
    data = r.json()
    rows = data.get("data") or []
    if not rows:
        return None
    row = rows[0]
    value = row.get("record_count")
    if value is None and row:
        value = next(iter(row.values()))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _bronze_physical_snapshot(
    cartridge: str, entity: str, user: dict | None
) -> dict:
    if not _safe_pipeline_name(cartridge) or not _safe_pipeline_name(entity):
        return {}

    source = f"raw/{cartridge}/{entity}"
    ctx = build_security_context(user)
    tenant = str(ctx.get("tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or "").strip()
    list_prefix = f"{source}/"
    if tenant and workspace:
        list_prefix = f"{source}/tenant_id={tenant}/workspace_id={workspace}/"
    if not _explorer_path_allowed(list_prefix, user):
        return {}
    bucket = os.environ.get("MINIO_BUCKET", "lakehouse")
    try:

        def _list_object_names() -> list[str]:
            client = _minio_client()
            return [
                obj.object_name
                for obj in client.list_objects(
                    bucket, prefix=list_prefix, recursive=True
                )
            ]

        object_names = await asyncio.to_thread(_list_object_names)
        latest_date = _bronze_latest_date_from_objects(cartridge, entity, object_names)
        if not latest_date:
            return {}
        return {
            "latest_date": latest_date,
            "record_count": await _count_bronze_parquet_rows(source, latest_date, user),
        }
    except Exception:
        return {}


async def _table_has_column(table: str, column: str, *, refresh: bool = False) -> bool:
    return await _table_has_column_impl(
        table,
        column,
        pool_getter=_get_db_pool,
        logger_debug=logger.debug,
        refresh=refresh,
    )


async def _pipeline_runs_scope_predicate(
    user: dict | None, start_index: int = 1, *, refresh_columns: bool = False
) -> tuple[str, list]:
    return await _pipeline_runs_scope_predicate_impl(
        user,
        start_index,
        refresh_columns=refresh_columns,
        build_security_context=build_security_context,
        table_has_column=_table_has_column,
    )


@asynccontextmanager
async def _pipeline_runs_read_conn(pool: Any, user: dict | None):
    async with _pipeline_runs_read_conn_impl(
        pool,
        user,
        build_security_context=build_security_context,
        scoped_db_for_user=scoped_db_for_user,
    ) as conn:
        yield conn


async def _record_dag_pipeline_trigger(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    dag_run_id: str,
    mode: str,
    status: str,
    conf: dict,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    await _record_dag_pipeline_trigger_impl(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
        mode=mode,
        status=status,
        conf=conf,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        get_db_pool=_get_db_pool,
        table_has_column=_table_has_column,
        normalize_airflow_state=_normalize_airflow_state,
    )


async def _refresh_dag_run_status(row: dict, user: dict | None = None) -> dict:
    return await _refresh_dag_run_status_impl(
        row,
        user,
        mcp_invoke=mcp_registry.invoke,
        get_db_pool=_get_db_pool,
        build_security_context=build_security_context,
        normalize_airflow_state=_normalize_airflow_state,
        parse_iso_datetime=_parse_iso_datetime,
        duration_seconds=_duration_seconds,
        logger_debug=logger.debug,
    )


RATE_LIMIT_WINDOW_SECONDS = _request_rate_limits.RATE_LIMIT_WINDOW_SECONDS
RATE_LIMITS = _request_rate_limits.RATE_LIMITS
_TRUSTED_PROXY_IPS = _request_rate_limits.trusted_proxy_ips()


def _client_ip(request: Request) -> str:
    return _request_rate_limits.client_ip(
        request,
        trusted_proxies=_TRUSTED_PROXY_IPS,
    )


async def _rate_limit(request: Request, action: str, subject: str = "") -> None:
    # v1.44.3.3 (Task A): E2E suites running through the Next.js
    # same-origin proxy all surface to FastAPI as a single
    # source-IP (the docker container's IP), so concurrent test
    # logins share one per-IP bucket and exhaust the 8/300s
    # window long before a real user could. The
    # ``RATE_LIMIT_ENABLED`` env var lets the test harness bypass
    # the limiter completely; in production it stays unset (or
    # explicitly ``true``) and the brute-force protection is
    # untouched.
    #
    # We also bypass when APP_ENV is ``test`` for the same reason
    # — the Python suite calls these endpoints repeatedly during
    # the auth contract tests.
    await _request_rate_limits.rate_limit(
        request,
        action,
        subject,
        limiter_factory=get_rate_limiter,
        trusted_proxies=_TRUSTED_PROXY_IPS,
    )


async def _rate_limit_api_surface(
    request: Request, path: str, user: dict | None
) -> None:
    await _request_rate_limits.rate_limit_api_surface(
        request,
        path,
        user,
        limiter_factory=get_rate_limiter,
        trusted_proxies=_TRUSTED_PROXY_IPS,
    )


def _rate_limit_disabled() -> bool:
    """Return True when rate limiting should bypass.

    The bypass fires in two scenarios — both are EXPLICITLY
    test-harness affordances, never production behaviour:

      1. ``RATE_LIMIT_ENABLED=false`` (any case) — an explicit
         opt-out for E2E suites that hammer /auth/login. Default
         unset → enabled.
      2. ``APP_ENV`` ∈ {``test``, ``testing``} — automatic for
         pytest harnesses that don't bother setting
         RATE_LIMIT_ENABLED.

    Production deployments default ``APP_ENV`` to ``production``
    (see console/app/security.py) and leave RATE_LIMIT_ENABLED
    unset, so the limiter stays on.
    """
    return _request_rate_limits.rate_limit_disabled()


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)


# ── Auth middleware ────────────────────────────────────────────────────────────

_AUTH_PUBLIC_EXACT = {
    # ── Login / session ──
    "/login",
    "/auth/login",
    "/api/auth/login",
    "/auth/logout",
    "/auth/me",
    "/auth/me-jwt",
    "/auth/me-current",
    "/auth/refresh",
    # ── Account activation ──
    "/activate",
    "/auth/activate",
    "/auth/activate/info",
    # ── Password recovery ──
    "/forgot-password",
    "/auth/forgot-password",
    "/reset-password",
    "/auth/reset-password",
    "/auth/reset/info",
    # ── Static / browser ──
    "/favicon.ico",
    # ── Health / monitoring ──
    # Sprint v1.23.1 hotfix: /healthz must be reachable WITHOUT auth so
    # the v1.21 compose probe + the v1.23 smoke script can hit it from
    # inside the container / from `make smoke` on the host. Without
    # this entry, auth_middleware redirects /healthz to /login (307)
    # and the probe never sees a 200 — leaving the service stuck on
    # `(unhealthy)` even when it's fine. Workspace and vault already
    # handle /healthz via their own public-path sets; console was the
    # outlier.
    "/healthz",
    "/readyz",
}
_AUTH_PUBLIC_PREFIX = ("/static/", "/vpn-config/")
_AUTH_API_LIKE_PREFIX = (
    "/api/",
    "/mcp/",
    "/internal/",
    "/datasets",
    "/jobs",
    "/tokens",
    "/studio/",
    "/studio_ops/",
    "/monitoring/",
    "/auth/",
)
_AUTH_INTERNAL_SERVICE_PREFIX = ("/monitoring/mcp/", "/studio_ops/mcp/")

# Routes a user is allowed to hit while in must_change_password=true state.
_AUTH_FORCED_CHANGE_ALLOW_EXACT = {
    "/me",
    "/api/me",
    "/api/me/access",
    "/api/me/profile",
    "/api/me/change-password",
    "/auth/logout",
    "/auth/me",
}

_RBAC_DEPENDENCY_PREFIXES = (
    "/jobs",
    "/tokens/summary",
    "/assistant/chat",
    "/datasets",
    "/api/data",
    "/api/jobs",
    "/api/me",
    "/api/users",
    "/api/decisions",
    "/api/datasets",
    "/api/intelligence",
    "/api/v1/intelligence",
    "/api/apps",
    "/api/control-room",
    "/api/admin/users",
    "/api/pipeline",
    "/api/pipeline_runs",
    "/api/dag_templates",
    "/api/schema",
    "/api/sources",
    "/api/vault",
    "/api/rag",
    "/api/catalog",
    "/api/semantic",
    "/api/studio",  # v1.44.3.3 Task B (stubs)
    "/studio/cartridges",
    "/studio/import",
    "/studio/chat",
)


def _is_api_like(path: str, accept: str) -> bool:
    return _is_api_like_impl(path, accept, _AUTH_API_LIKE_PREFIX)


def _is_direct_static_html_request(path: str) -> bool:
    return _is_direct_static_html_request_impl(path)


def _uses_rbac_dependency(path: str) -> bool:
    return _uses_rbac_dependency_impl(path, _RBAC_DEPENDENCY_PREFIXES)


def _is_agent_runner_request(request: Request) -> bool:
    return _is_agent_runner_request_impl(
        request.url.path,
        supplied_token=request.headers.get("X-Agent-Runner-Token", ""),
        expected_token=os.environ.get("AGENT_RUNNER_TOKEN", ""),
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    if _is_direct_static_html_request(path):
        return _apply_security_headers(
            JSONResponse({"detail": "not found"}, status_code=404), path
        )

    # Internal routes (server-to-server) bypass session auth.
    # Their own router-level dependency (verify_internal_api_key) handles auth via header.
    if path.startswith("/internal/"):
        return await call_next(request)

    if path.startswith(_AUTH_INTERNAL_SERVICE_PREFIX) and _is_internal_request(request):
        request.state.user = _internal_service_user()
        return await call_next(request)

    try:
        cartridge_vault_user = _cartridge_vault_reveal_user(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if cartridge_vault_user:
        request.state.user = cartridge_vault_user
        return await call_next(request)

    # Airflow scheduled agent runs authenticate with X-Agent-Runner-Token;
    # the route re-checks the same token before executing the agent.
    if _is_agent_runner_request(request):
        return await call_next(request)

    is_public = path in _AUTH_PUBLIC_EXACT or any(
        path.startswith(p) for p in _AUTH_PUBLIC_PREFIX
    )

    requested_workspace_id = requested_workspace_id_from_request(request)
    token = request.cookies.get(_auth.COOKIE_NAME)
    user = await _auth.get_session_user(token) if token else None
    if user and (requested_workspace_id or not user.get("active_workspace_id")):
        try:
            workspaces = await _workspace_access_options(user)
            if workspaces:
                active_workspace = workspaces[0]
                if requested_workspace_id:
                    active_workspace = next(
                        (
                            w
                            for w in workspaces
                            if w["workspace_id"] == requested_workspace_id
                        ),
                        None,
                    )
                    if not active_workspace:
                        return _apply_security_headers(
                            JSONResponse(
                                {"detail": "workspace access forbidden"},
                                status_code=403,
                            ),
                            path,
                        )
                user = dict(user)
                user.update(
                    {
                        "workspace_role": active_workspace["workspace_role"],
                        "active_workspace_id": active_workspace["workspace_id"],
                        "active_tenant_id": active_workspace["tenant_id"],
                        "workspaces": workspaces,
                        "allowed_cartridges": await _workspace_cartridges(
                            active_workspace["workspace_id"], user_id=user["id"]
                        ),
                    }
                )
        except Exception:
            logger.exception("Session workspace enrichment failed")
            if not is_public and _uses_rbac_dependency(path):
                return _apply_security_headers(
                    JSONResponse(
                        {"detail": "workspace context unavailable"}, status_code=403
                    ),
                    path,
                )

    # Fall back to JWT bearer so require_permission() routes get request.state.user set.
    if not user:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                # Sprint v1.10: async variant runs the Redis blacklist
                # check; legacy decode kept for unit tests.
                claims = await verify_access_token_async(auth_header[7:])
                jwt_user = await _auth.get_user_by_id(int(claims["sub"]))
                if jwt_user and jwt_user.get("is_active"):
                    workspaces = await _workspace_access_options(jwt_user)
                    if workspaces:
                        active_workspace = workspaces[0]
                        if requested_workspace_id:
                            active_workspace = next(
                                (
                                    w
                                    for w in workspaces
                                    if w["workspace_id"] == requested_workspace_id
                                ),
                                None,
                            )
                            if not active_workspace:
                                return _apply_security_headers(
                                    JSONResponse(
                                        {"detail": "workspace access forbidden"},
                                        status_code=403,
                                    ),
                                    path,
                                )
                        jwt_user = dict(jwt_user)
                        jwt_user.update(
                            {
                                "workspace_role": active_workspace["workspace_role"],
                                "active_workspace_id": active_workspace["workspace_id"],
                                "active_tenant_id": active_workspace["tenant_id"],
                                "workspaces": workspaces,
                                "allowed_cartridges": await _workspace_cartridges(
                                    active_workspace["workspace_id"],
                                    user_id=jwt_user["id"],
                                ),
                            }
                        )
                    user = jwt_user
            except Exception:
                logger.debug("Bearer JWT auth fallback failed", exc_info=True)
                if not is_public and _uses_rbac_dependency(path):
                    return _apply_security_headers(
                        JSONResponse(
                            {"detail": "authentication required"}, status_code=401
                        ),
                        path,
                    )

    request.state.user = user
    try:
        await _rate_limit_api_surface(request, path, user)
    except HTTPException as exc:
        return _apply_security_headers(
            JSONResponse({"detail": exc.detail}, status_code=exc.status_code), path
        )

    if not user and not is_public and _uses_rbac_dependency(path):
        return await call_next(request)

    if not user and not is_public:
        if _is_api_like(path, request.headers.get("accept", "")):
            return _apply_security_headers(
                JSONResponse({"detail": "authentication required"}, status_code=401),
                path,
            )
        return _apply_security_headers(
            RedirectResponse(url=f"/login?next={path}"), path
        )

    # Forced password change: confine the session to the change-password flow.
    if user and user.get("must_change_password") and not is_public:
        if path not in _AUTH_FORCED_CHANGE_ALLOW_EXACT:
            if _is_api_like(path, request.headers.get("accept", "")):
                return _apply_security_headers(
                    JSONResponse(
                        {
                            "detail": "password change required",
                            "must_change_password": True,
                        },
                        status_code=403,
                    ),
                    path,
                )
            return _apply_security_headers(RedirectResponse(url="/me"), path)

    return await call_next(request)


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "authentication required")
    return u


def require_admin(request: Request) -> dict:
    u = require_user(request)
    if u.get("role") not in {"admin", "owner", "super_admin"}:
        raise HTTPException(403, "admin role required")
    return u


def _access_token_for_user(user: dict) -> str:
    return create_access_token(
        {
            "sub": str(user["id"]),
            "email": user["email"],
            "role": user["role"],
        }
    )


def _set_refresh_cookie(resp: JSONResponse, token: str, expires) -> None:
    resp.set_cookie(
        _auth.REFRESH_COOKIE_NAME,
        token,
        httponly=True,
        secure=_auth.cookie_secure(),
        samesite="lax",
        expires=expires.replace(microsecond=0),
        path="/",
    )


# ── Auth routes ────────────────────────────────────────────────────────────────


@app.get("/login")
async def login_page(request: Request):
    # Seed the CSRF cookie so the page's POST /auth/login fetch can echo
    # it back without an extra round-trip. The cookie is re-issued on
    # every GET /login (cheap, and avoids a stale-token edge case when
    # the user keeps the tab open across logout/login).
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "login/index.html")


async def _login_response(request: Request, body: dict):
    email = (body.get("email") or "").strip()
    pw = body.get("password") or ""
    await _rate_limit(request, "/auth/login", email)
    if not email or not pw:
        raise HTTPException(400, "email and password are required")
    ip = request.client.host if request.client else None
    user = await _auth.authenticate(email, pw, ip=ip)
    if not user:
        raise HTTPException(401, "invalid credentials")
    token, expires = await _auth.create_session(user["id"], ip=ip)
    access_token = _access_token_for_user(user)
    refresh_token, refresh_expires = await _auth.create_refresh_token(user["id"])
    resp = JSONResponse(
        {"user": user, "access_token": access_token, "token_type": "bearer"}
    )
    resp.set_cookie(
        _auth.COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_auth.cookie_secure(),
        expires=expires.replace(microsecond=0),
        path="/",
    )
    _set_refresh_cookie(resp, refresh_token, refresh_expires)
    # Keep the CSRF token stable across the login transition. API clients
    # and the Next.js proxy seed the token on GET /login, submit it to
    # /auth/login, then immediately use the same in-memory token for the
    # first authenticated mutation while the cookie jar is catching up.
    # The double-submit check remains active because the header must still
    # match the csrf_token cookie.
    set_csrf_cookie(resp, request.cookies.get(CSRF_COOKIE_NAME))
    return resp


@app.post("/auth/login", dependencies=[Depends(require_csrf)])
async def auth_login(request: Request, body: dict):
    return await _login_response(request, body)


@app.post("/api/auth/login", dependencies=[Depends(require_csrf)])
async def api_auth_login(request: Request, body: dict):
    # Legacy compatibility alias for clients that still post to
    # /api/auth/login. Delegate through the real handler so CSRF,
    # rate-limit, and session behavior stay identical to /auth/login.
    return await auth_login(request, body)


@app.post("/auth/refresh", dependencies=[Depends(require_csrf)])
async def auth_refresh(request: Request):
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    # Hash a prefix of the token into the subject so per-token buckets isolate
    # spamming attempts without writing the secret material to Redis keys.
    subject = (refresh_token or "")[:16]
    await _rate_limit(request, "/auth/refresh", subject)
    rotate_refresh_token = getattr(_auth, "rotate_refresh_token", None)
    if callable(rotate_refresh_token):
        rotated = await rotate_refresh_token(refresh_token)
        if not rotated:
            resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
            resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
            return resp
        user, new_refresh_token, refresh_expires = rotated
    else:
        # Compatibility for unit-test doubles that predate atomic rotation.
        user = await _auth.get_refresh_token_user(refresh_token)
        if not user:
            resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
            resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
            return resp
        await _auth.revoke_refresh_token(refresh_token)
        new_refresh_token, refresh_expires = await _auth.create_refresh_token(
            user["id"]
        )

    if not user:
        resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
        resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
        return resp

    access_token = _access_token_for_user(user)
    resp = JSONResponse({"access_token": access_token, "token_type": "bearer"})
    _set_refresh_cookie(resp, new_refresh_token, refresh_expires)
    return resp


@app.post("/auth/logout", dependencies=[Depends(require_csrf)])
async def auth_logout(request: Request):
    token = request.cookies.get(_auth.COOKIE_NAME)
    if token:
        await _auth.destroy_session(token)
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    if refresh_token:
        await _auth.revoke_refresh_token(refresh_token)

    # Sprint v1.10 — blacklist the bearer access token's jti so a stolen
    # JWT can't keep authenticating up to its exp. Silent if the caller
    # is cookie-only (most of our UI) or the token is already invalid.
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(bearer)
            jti = claims.get("jti")
            exp = claims.get("exp")
            if jti and exp is not None:
                from app.services.jwt_blacklist import get_blacklist

                await get_blacklist().revoke(jti, int(exp))
        except Exception:
            # JWT already expired / malformed / signature mismatch —
            # nothing to revoke, nothing to do.
            pass

    resp = JSONResponse({"logged_out": True})
    resp.delete_cookie(_auth.COOKIE_NAME, path="/")
    resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
    clear_csrf_cookie(resp)
    return resp


@app.get("/auth/me")
async def auth_me(request: Request):
    return {"user": _user_payload(current_user(request))}


@app.get("/auth/me-jwt")
async def auth_me_jwt(authorization: str | None = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    try:
        # Sprint v1.10: blacklist-aware verification.
        claims = await verify_access_token_async(token)
    except JWTAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "claims": {
            "sub": claims["sub"],
            "email": claims["email"],
            "role": claims["role"],
            "iat": claims["iat"],
            "exp": claims["exp"],
            "jti": claims["jti"],
        }
    }


@app.get("/auth/me-current")
async def auth_me_current(user: dict = Depends(get_current_user_dependency)):
    return {"user": _user_payload(user)}


# ── Activation ────────────────────────────────────────────────────────────────

APP_BASE_URL = _public_url(
    "APP_BASE_URL",
    fallback_env="CONSOLE_URL",
    development_default="http://localhost:8000",
)
INVITE_TTL_HOURS = int(os.environ.get("INVITE_TOKEN_TTL_HOURS", "72"))
RESET_TTL_HOURS = int(os.environ.get("RESET_TOKEN_TTL_HOURS", "1"))
VPN_TTL_HOURS = int(os.environ.get("VPN_TOKEN_TTL_HOURS", "72"))


def _normalize_email_or_400(
    value: object | None, *, required_message: str = "email is required"
) -> str:
    return _normalize_email_or_400_impl(
        value,
        email_re=EMAIL_RE,
        required_message=required_message,
    )


def _password_min_length() -> int:
    return _password_min_length_impl(_auth)


def _validate_password_or_400(
    password: object | None, *, field: str = "password"
) -> str:
    return _validate_password_or_400_impl(
        password,
        field=field,
        min_length=_password_min_length(),
    )


def _activation_link(token: str) -> str:
    return _token_link(APP_BASE_URL, "activate", token)


def _reset_link(token: str) -> str:
    return _token_link(APP_BASE_URL, "reset-password", token)


def _vpn_link(token: str) -> str:
    return _vpn_token_link(APP_BASE_URL, token)


def _set_session_cookie(resp: JSONResponse, token: str, expires) -> None:
    resp.set_cookie(
        _auth.COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_auth.cookie_secure(),
        expires=expires.replace(microsecond=0),
        path="/",
    )


@app.get("/activate")
async def viewer_activate():
    response = FileResponse(STATIC / "activate.html")
    set_csrf_cookie(response)
    return response


@app.post("/auth/activate", dependencies=[Depends(require_csrf)])
async def auth_activate(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw = body.get("new_password") or ""
    await _rate_limit(request, "/auth/activate", token[:16])
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    _validate_password_or_400(pw, field="new_password")
    info = await _tokens.consume_lookup(token, "invite")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.activate_user(info["user_id"], pw)
    if not user:
        raise HTTPException(400, "el password debe tener al menos 12 caracteres")
    ip = request.client.host if request.client else None
    sess_token, expires = await _auth.create_session(user["id"], ip=ip)
    resp = JSONResponse({"activated": True, "user": user})
    _set_session_cookie(resp, sess_token, expires)
    return resp


@app.get("/auth/activate/info")
async def auth_activate_info(token: str = ""):
    """Public probe so the activate page can show the user's email/name."""
    info = await _tokens.lookup(token, "invite")
    if not info:
        return {"valid": False}
    return {"valid": True, "email": info["email"], "name": info["name"]}


# ── Forgot / reset password ──────────────────────────────────────────────────


@app.get("/forgot-password")
async def viewer_forgot():
    # Seed CSRF cookie so the form's POST /auth/forgot-password fetch can
    # echo it back without a prior visit to /login.
    response = FileResponse(STATIC / "forgot_password.html")
    set_csrf_cookie(response)
    return response


@app.post("/auth/forgot-password", dependencies=[Depends(require_csrf)])
async def auth_forgot(request: Request, body: dict):
    """Generic OK regardless of whether the email exists (avoids enum oracle)."""
    email = (body.get("email") or "").strip().lower()
    await _rate_limit(request, "/auth/forgot-password", email)
    if email:
        u = await _auth.get_user_by_email(email)
        if u and u.get("is_active"):
            tok, _ = await _tokens.create(u["id"], "reset")
            subject, html = _email.render_password_reset(
                u.get("name"), _reset_link(tok), RESET_TTL_HOURS
            )
            await _email.send_email(u["email"], subject, html)
    return {"sent": True}


@app.get("/reset-password")
async def viewer_reset():
    response = FileResponse(STATIC / "reset_password.html")
    set_csrf_cookie(response)
    return response


@app.get("/auth/reset/info")
async def auth_reset_info(token: str = ""):
    info = await _tokens.lookup(token, "reset")
    if not info:
        return {"valid": False}
    return {"valid": True, "email": info["email"]}


@app.post("/auth/reset-password", dependencies=[Depends(require_csrf)])
async def auth_reset(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw = body.get("new_password") or ""
    await _rate_limit(request, "/auth/reset-password", token[:16])
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    _validate_password_or_400(pw, field="new_password")
    info = await _tokens.consume_lookup(token, "reset")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.reset_password_to(info["user_id"], pw)
    if not user:
        raise HTTPException(
            400, f"el password debe tener al menos {_password_min_length()} caracteres"
        )
    ip = request.client.host if request.client else None
    sess_token, expires = await _auth.create_session(user["id"], ip=ip)
    resp = JSONResponse({"reset": True, "user": user})
    _set_session_cookie(resp, sess_token, expires)
    # Rotate CSRF after the password reset so any leaked pre-reset token
    # cannot replay.
    set_csrf_cookie(resp)
    return resp


def _console_version() -> str:
    """Deprecated shim — use app.version.app_version(). Kept so existing
    call sites stay valid; delegates to the single source of truth."""
    from app.version import app_version

    return app_version()


@app.get("/healthz")
async def healthz():
    """Sprint v1.21 (F2): liveness probe for the compose healthcheck.
    No auth, no DB call — answers as long as the FastAPI event loop is
    running. Used by infra/docker-compose.yml so dependent services
    wait on service_healthy instead of service_started, avoiding the
    boot race where console answers before its lifespan has wired the
    DB pool.

    Carries ``version`` + ``app_env`` (no secrets) so an operator can
    confirm WHAT is deployed without authenticating — useful for the
    beta/demo runbook's health checks."""
    return _healthz_payload(version=_console_version(), app_env=_app_env())


async def _dependency_health(name: str, url: str, server: str | None = None) -> dict:
    return await _readyz_dependency_health(
        name,
        url,
        server,
        header_factory=_hdr_for,
        http_client_factory=httpx.AsyncClient,
        monotonic=time.monotonic,
        log=logger,
    )


async def _control_room_data_check(*, require_data: bool = False) -> dict:
    return await _readyz_control_room_data_check(
        require_data=require_data,
        db_pool_factory=_get_db_pool,
        log=logger,
    )


@app.get("/readyz")
async def readyz(request: Request):
    """Dependency-aware readiness probe.

    `/healthz` only proves the process can answer. `/readyz` is stricter:
    it verifies Postgres and core sibling services so deploy/proxy layers can
    keep traffic away from a half-started console.
    """
    checks: dict[str, dict] = {}
    checks["startup"] = _startup_readiness_status(request.app)
    if checks["startup"].get("status") != "up":
        body = {"ok": False, "service": "console"}
        if getattr(request.state, "user", None):
            body["checks"] = checks
        return JSONResponse(
            body,
            status_code=503,
        )

    try:
        pool = await _get_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = {"status": "up"}
    except Exception as exc:
        logger.warning("readiness probe failed for postgres", exc_info=True)
        checks["postgres"] = {"status": "down", "error": type(exc).__name__}

    deps = {
        "refinement": (f"{REFINEMENT_URL.rstrip('/')}/healthz", "REFINEMENT"),
        "mcp-infra": (
            f"{os.environ.get('MCP_INFRA_URL', 'http://mcp-infra:8010').rstrip('/')}/healthz",
            "MCP_INFRA",
        ),
        "vault": (f"{_vault_url()}/healthz", "VAULT"),
    }
    for name, (url, server) in deps.items():
        checks[name] = await _dependency_health(name, url, server)

    require_data = os.environ.get(
        "CONTROL_ROOM_REQUIRE_DATA_READY", ""
    ).strip().lower() in {"1", "true", "yes", "on"} or str(
        request.query_params.get("require_data") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    require_intelligence_param = (
        str(request.query_params.get("require_intelligence") or "").strip().lower()
    )
    intelligence_opt_out_allowed = (
        not _is_production_env()
        and require_intelligence_param in {"0", "false", "no", "off"}
    )
    require_intelligence_data = (
        os.environ.get("CONTROL_ROOM_REQUIRE_INTELLIGENCE_READY", "").strip().lower()
        in {"1", "true", "yes", "on"}
        or require_intelligence_param in {"1", "true", "yes", "on"}
        or (require_data and _is_production_env() and not intelligence_opt_out_allowed)
    )
    if require_data:
        checks["control_room_data"] = await _control_room_data_check(require_data=require_data)
    else:
        checks["control_room_data"] = {
            "status": "up",
            "required": False,
            "reason": "data_check_not_required",
        }

    if require_intelligence_data:
        try:
            from app.services.intelligence.readiness import intelligence_readiness

            checks["intelligence_data"] = await intelligence_readiness(
                getattr(request.state, "user", None),
                require_data=require_intelligence_data,
            )
        except Exception as exc:
            logger.warning("readiness probe failed for intelligence_data", exc_info=True)
            checks["intelligence_data"] = {
                "status": "degraded",
                "required": require_intelligence_data,
                "error": type(exc).__name__,
            }
    else:
        checks["intelligence_data"] = {
            "status": "up",
            "required": False,
            "reason": "data_check_not_required",
        }

    dependency_ok = all(
        check.get("status") == "up"
        for name, check in checks.items()
        if name not in {"control_room_data", "intelligence_data"}
    )
    data_ok = (
        checks["control_room_data"].get("status") == "up"
        and (
            checks["intelligence_data"].get("status") == "up"
            or not require_intelligence_data
        )
    ) or not require_data
    ok = dependency_ok and data_ok
    body = {"ok": ok, "service": "console"}
    if getattr(request.state, "user", None):
        body["checks"] = checks
    return JSONResponse(
        body,
        status_code=200 if ok else 503,
    )


@app.get("/api/config")
async def api_config(request: Request):
    """Runtime config (URLs only, no secrets)."""
    return _runtime_config_payload(os.environ, public_url=_public_url)


@app.get("/api/system/info")
async def system_info(user: dict = Depends(require_authenticated)):
    # v1.43.2 (Frontend R1 hardening): expose ``dev_mode`` so the UI
    # can hide CTAs that gate on dev-only mcp-infra tools (Studio
    # Deploy DAG, etc). Pre-v1.43.2 the console rendered those
    # buttons unconditionally; clicking them in production now surfaces
    # a PermissionError from airflow_create_dag — which is correct but
    # confusing. The button is hidden by checking this flag.
    # Source-level contract marker for tests and frontend feature gates:
    # "app_env" "dev_mode" {"development", "dev", "local", "test"}
    return _system_info_payload(os.environ, version=_console_version())


@app.get("/me")
async def viewer_me(request: Request):
    require_user(request)
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "me/index.html")


@app.get("/api/me")
async def api_me(user: dict = Depends(require_authenticated)):
    return _user_payload(user)


@app.get("/api/me/access")
async def api_me_access(user: dict = Depends(require_authenticated)):
    """Phase-0 SaaS-controls: a single endpoint that returns the user's
    *effective* security profile so the front-end can render "Mis accesos"
    without the user (or operator) inferring permissions from role names.

    The shape is intentionally narrow: the front-end uses these fields to
    decide what to render and what to disable. The backend is still the
    source of truth — every action endpoint enforces its own permission.
    """
    from app.services import permissions as _perms

    effective = sorted(_perms.get_effective_permissions(user))
    role_canonical = _perms.canonical_role(user.get("role"))
    workspace_role_resolved = _perms.workspace_role(user) or None

    cartridges_allowed, cartridges_denied = await _fetch_cartridge_access(
        user,
        pool_factory=cartridge_service.pool,
        logger=logger,
    )

    # The front-end uses these flags to decide what to render. They are
    # display hints only; every action endpoint enforces its own gate.
    # Source-level frontend contract marker:
    # "workspaces": switchable_workspaces
    return _me_access_payload(
        user,
        effective_permissions=effective,
        role_canonical=role_canonical,
        workspace_role_resolved=workspace_role_resolved,
        cartridges_allowed=cartridges_allowed,
        cartridges_denied=cartridges_denied,
    )


@app.post("/api/me/change-password", dependencies=[Depends(require_csrf)])
async def api_me_change_password(
    body: dict, user: dict = Depends(require_authenticated)
):
    current = body.get("current_password") or ""
    new = body.get("new_password") or ""
    if not current or not new:
        raise HTTPException(400, "current_password and new_password are required")
    ok, err = await _auth.change_own_password(user["id"], current, new)
    if not ok:
        raise HTTPException(400, err or "password change failed")
    resp = JSONResponse({"changed": True})
    # Rotate CSRF after a successful self-service password change.
    set_csrf_cookie(resp)
    return resp


# ── Jobs ──────────────────────────────────────────────────────────────────────


@app.get("/jobs", dependencies=[Depends(require_permission("monitor.read"))])
async def list_jobs(limit: int = 20, user: dict = Depends(require_permission("monitor.read"))):
    jobs = await _call_with_optional_user(job_service.list_recent, limit, user=user)
    return {"jobs": await _refresh_pipeline_job_payloads(jobs, user)}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_permission("monitor.read"))])
async def get_job(job_id: str, user: dict = Depends(require_permission("monitor.read"))):
    job = await job_service.get_scoped(job_id, user=user)
    return await _refresh_pipeline_job_payload(job, user)


# ── Token usage ───────────────────────────────────────────────────────────────


@app.get("/tokens/summary")
async def tokens_summary(user: dict = Depends(require_permission("copilot.use"))):
    return await token_store.summary(user_context=user)


def _llm_secret_keys(data: dict) -> set[str]:
    return _llm_secret_keys_impl(data)


@app.get(
    "/api/copilot/llm-key", dependencies=[Depends(require_permission("llm.keys.read"))]
)
async def api_copilot_llm_key_status(
    user: dict = Depends(require_permission("llm.keys.read")),
):
    vault_scope = _tenant_vault_scope(user, "llm")
    try:
        async with httpx.AsyncClient(
            headers=_vault_headers_for_user(user), timeout=5
        ) as c:
            r = await c.get(f"{_VAULT_URL}/secrets/{quote(vault_scope, safe='')}")
        if r.status_code in {404, 204}:
            return {"provider": "anthropic", "configured": False, "scope": "llm"}
        r.raise_for_status()
        data = r.json() if r.content else {}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Vault request failed") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Vault request failed") from exc
    return {
        "provider": "anthropic",
        "configured": "anthropic_api_key"
        in _llm_secret_keys(data if isinstance(data, dict) else {}),
        "scope": "llm",
    }


@app.put(
    "/api/copilot/llm-key",
    dependencies=[Depends(require_csrf), Depends(require_permission("llm.keys.write"))],
)
async def api_copilot_llm_key_set(
    body: dict, user: dict = Depends(require_permission("llm.keys.write"))
):
    value = str(body.get("value") or "").strip()
    if not value:
        raise HTTPException(400, "value is required")
    vault_scope = _tenant_vault_scope(user, "llm")
    try:
        async with httpx.AsyncClient(
            headers=_vault_headers_for_user(user), timeout=5
        ) as c:
            r = await c.put(
                f"{_VAULT_URL}/secrets/{quote(vault_scope, safe='')}/anthropic_api_key",
                json={"value": value},
            )
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Vault request failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Vault request failed") from exc
    await _audit.record_event(
        user.get("id"),
        user.get("email"),
        "copilot.llm_key.upsert",
        "vault_secret",
        "llm/anthropic_api_key",
        status="success",
        metadata={
            "provider": "anthropic",
            "scope": "llm",
            "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
            "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        },
        critical=True,
    )
    return {"provider": "anthropic", "configured": True, "scope": "llm"}


# ── Assistant ─────────────────────────────────────────────────────────────────


@app.post(
    "/assistant/chat",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.use"))],
)
async def chat(body: dict, user: dict = Depends(require_permission("copilot.use"))):
    try:
        return await _call_with_optional_user(
            assistant.chat,
            body.get("message", ""),
            body.get("history", []),
            user=user,
        )
    except llm_client.LLMConfigurationError as exc:
        message = (
            "⚠️ El copiloto necesita una clave Anthropic para este workspace. "
            "Ábrela en Tokens y guarda la clave API del tenant antes de usar el chat."
        )
        logger.info(
            "assistant chat blocked by LLM configuration",
            extra={"user_id": user.get("id"), "reason": str(exc)},
        )
        return {
            "reply": message,
            "viewer_urls": [],
            "messages": [{"role": "assistant", "content": message}],
        }
    except llm_client.LLMProviderError as exc:
        logger.warning(
            "assistant chat provider error",
            extra={"user_id": user.get("id"), "reason": str(exc)},
        )
        raise HTTPException(
            status_code=502, detail=f"El proveedor LLM respondió con error. {exc}"
        ) from exc


# ── Datasets proxy → refinement ───────────────────────────────────────────────


def _sanitize_dataset_metadata_for_user(user: dict | None, dataset: dict) -> dict:
    # Source-hardening contract markers kept in this router for audit tests:
    # is_physical_reference = "://" in value or "tenant_id=" in value or "workspace_id=" in value
    # or f"tenant_id={tenant_id}" not in candidate
    # or f"workspace_id={workspace_id}" not in candidate
    return _sanitize_dataset_metadata_for_user_impl(user, dataset)


def _sanitize_datasets_payload_for_user(user: dict | None, payload: Any) -> Any:
    return _sanitize_datasets_payload_for_user_impl(user, payload)


@app.get("/datasets", dependencies=[Depends(require_permission("datasets.read"))])
async def list_datasets(user: dict = Depends(require_permission("datasets.read"))):
    payload = await _refinement_invoke("list_datasets", {}, user=user)
    return _sanitize_datasets_payload_for_user(user, payload)


@app.get("/datasets/{name}/schema", dependencies=[Depends(require_permission("datasets.read"))])
async def dataset_schema(name: str, user: dict = Depends(require_permission("datasets.read"))):
    return await _refinement_invoke("get_schema", {"name": name}, user=user)


@app.get("/api/datasets", dependencies=[Depends(require_permission("datasets.read"))])
async def api_list_datasets_alias(user: dict = Depends(require_permission("datasets.read"))):
    return await list_datasets(user)


@app.get("/api/datasets/{name}/schema", dependencies=[Depends(require_permission("datasets.read"))])
async def api_dataset_schema_alias(
    name: str, user: dict = Depends(require_permission("datasets.read"))
):
    return await dataset_schema(name, user)


@app.get("/datasets/{name}/data", dependencies=[Depends(require_permission("datasets.read"))])
async def dataset_data(
    name: str,
    request: Request,
    limit: int = 100,
    user: dict = Depends(require_permission("datasets.read")),
):
    # Forward user context so refinement can apply RLS. Without it the GOLD
    # tables fall through to the empty-tenant filter (or the revenue_manager
    # 'N/D' fallback) and any authenticated user could read cross-tenant rows.
    user = user if isinstance(user, dict) else getattr(request.state, "user", None)
    user = _runtime_user(user) or {}
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "query_dataset",
                {"name": name, "limit": limit, "user_context": _rls_user_context(user)},
                user,
            ),
        )
        if r.status_code >= 400:
            raise HTTPException(
                r.status_code, _upstream_error_detail(r, "Refinement query failed")
            )
        payload = r.json()
        _raise_for_refinement_payload_error(payload, "Refinement query failed")
        return payload


@app.post(
    "/datasets/{name}/refresh",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))],
)
async def refresh_dataset(
    name: str, user: dict = Depends(require_permission("datasets.write"))
):
    return await _refinement_invoke(
        "materialize", {"name": name}, timeout=120, user=user
    )


# ── Viewer data APIs ──────────────────────────────────────────────────────────


async def _refresh_pipeline_job_payload(job: dict, user: dict | None) -> dict:
    return await _refresh_pipeline_job_payload_impl(
        job,
        user,
        refresh_dag_run_status=_refresh_dag_run_status,
    )


async def _refresh_pipeline_job_payloads(
    jobs: list[dict], user: dict | None
) -> list[dict]:
    return await _refresh_pipeline_job_payloads_impl(
        jobs,
        user,
        refresh_dag_run_status=_refresh_dag_run_status,
    )


@app.get("/api/jobs", dependencies=[Depends(require_permission("monitor.read"))])
async def api_jobs(limit: int = 50, user: dict = Depends(require_permission("monitor.read"))):
    jobs = await _call_with_optional_user(job_service.list_recent, limit, user=user)
    return {"jobs": await _refresh_pipeline_job_payloads(jobs, user)}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_permission("monitor.read"))])
async def api_job(job_id: str, user: dict = Depends(require_permission("monitor.read"))):
    job = await job_service.get_scoped(job_id, user=user)
    return await _refresh_pipeline_job_payload(job, user)


@app.get("/api/jobs/{job_id}/logs", dependencies=[Depends(require_permission("monitor.read"))])
async def api_job_logs(
    job_id: str, limit: int = 200, user: dict = Depends(require_permission("monitor.read"))
):
    import json as _json

    scoped = await job_service.get_scoped(job_id, user=user)
    if scoped.get("error"):
        raise HTTPException(404, "job not found")
    job_args = scoped.get("args") if isinstance(scoped.get("args"), dict) else {}
    job_result = scoped.get("result") if isinstance(scoped.get("result"), dict) else {}
    cartridge = str(
        job_args.get("cartridge_id")
        or job_args.get("cartridge")
        or job_result.get("cartridge_id")
        or job_result.get("cartridge")
        or ""
    ).strip()
    if not cartridge:
        raise HTTPException(
            422, "job cartridge is unavailable; cannot resolve scoped logs"
        )
    pool = await _get_db_pool()
    rows = await pool.fetch(
        "SELECT entity, level, message, detail, ts FROM run_logs "
        "WHERE run_id=$1 AND cartridge=$2 ORDER BY ts ASC LIMIT $3",
        job_id,
        cartridge,
        limit,
    )
    result = []
    for row in rows:
        detail = row["detail"]
        if isinstance(detail, str):
            try:
                detail = _json.loads(detail)
            except (json.JSONDecodeError, ValueError):
                pass
        result.append(
            {
                "ts": row["ts"].isoformat(),
                "entity": row["entity"],
                "level": row["level"],
                "message": row["message"],
                "detail": detail,
            }
        )
    return {"logs": result}


@app.get("/api/tools/manifest", dependencies=[Depends(require_permission("agents.read"))])
async def api_tools_manifest(user: dict = Depends(require_permission("agents.read"))):
    """Sprint v1.41.0 (tornillo copilot): unified tool catalog with risk_level
    + requires_approval, sourced from every registered MCP server. The copilot
    router (v1.42+) consumes this to decide auto-execution vs approval prompts."""
    from app.services.tool_manifest import build_manifest

    return await build_manifest()


# Sprint v1.41.0 — cartridge management endpoints live in
# console/app/routers/cartridges.py (registered with include_router below).


_gold_catalog_runtime: _GoldCatalogRuntime | None = None


def _get_gold_catalog_runtime() -> _GoldCatalogRuntime:
    global _gold_catalog_runtime
    if _gold_catalog_runtime is None:
        _gold_catalog_runtime = _GoldCatalogRuntime(
            get_db_pool=_get_db_pool,
            workspace_scope_for_apps_filter=_workspace_scope_for_apps_filter,
            allowed_cartridges_for_user=_allowed_cartridges_for_user,
        )
    return _gold_catalog_runtime


async def _gold_catalog_scope_for_dataset(
    dataset: str, user: dict | None
) -> tuple[str, str]:
    return await _get_gold_catalog_runtime().catalog_scope_for_dataset(dataset, user)


async def _gold_sources_from_catalog(user: dict | None) -> list[str]:
    return await _get_gold_catalog_runtime().sources_from_catalog(user)


async def _gold_semantic_entities_from_catalog(
    cartridge: str, user: dict | None
) -> list[dict[str, Any]]:
    return await _get_gold_catalog_runtime().semantic_entities_from_catalog(
        cartridge, user
    )


async def _gold_schema_payload(source: str, user: dict | None) -> dict[str, Any]:
    return await _get_gold_catalog_runtime().schema_payload(source, user)


@app.get("/api/schema", dependencies=[Depends(require_permission("datasets.read"))])
async def api_schema(source: str, user: dict = Depends(require_permission("datasets.read"))):
    _require_technical_source_access(user, source)

    async def load_schema() -> dict:
        if _gold_dataset_from_source(source):
            try:
                return await _gold_schema_payload(source, user)
            except Exception as exc:
                error = _schema_error("gold", exc)
                return _gold_schema_error_payload(source, error)

        errors: list[dict] = []
        try:
            partitions = await _refinement_invoke(
                "get_source_partitions",
                {"source": source},
                timeout=30,
                user=user,
            )
        except Exception as exc:
            error = _schema_error("partitions", exc)
            errors.append(error)
            partitions = _empty_partitions(source, "error", error["message"])

        try:
            preview = await _refinement_invoke(
                "preview_source",
                {"source": source, "limit": 5},
                timeout=30,
                user=user,
            )
        except Exception as exc:
            error = _schema_error("preview", exc)
            errors.append(error)
            preview = _empty_preview(source, "error", error["message"])

        errors.extend(_schema_payload_warnings("partitions", partitions))
        errors.extend(_schema_payload_warnings("preview", preview))
        if not _preview_has_columns(preview):
            errors.append(
                {
                    "stage": "preview",
                    "status_code": 200,
                    "reason": "empty_schema",
                    "message": "sin columnas inferidas",
                    "detail": "La fuente no devolvió columnas inferidas desde el parquet.",
                }
            )

        return _bronze_schema_payload(source, partitions, preview, errors)

    return await _scoped_read_cache_get_or_set("schema", user, (source,), load_schema)


@app.get("/api/sources", dependencies=[Depends(require_permission("datasets.read"))])
async def api_sources(user: dict = Depends(require_permission("datasets.read"))):
    async def load_sources() -> dict:
        try:
            data = await _refinement_invoke("list_sources", {}, timeout=60, user=user)
        except HTTPException:
            data = {}
        # Normalize: result may be {"result": [...]} or {"sources": [...]}
        sources = data.get("result") or data.get("sources") or []
        gold_sources = await _gold_sources_from_catalog(user)
        if isinstance(sources, list):
            sources = _filter_technical_sources(user, sources)
            return {
                "sources": sorted(
                    set([str(s) for s in sources if str(s).strip()] + gold_sources)
                )
            }
        return {"sources": gold_sources}

    return await _scoped_read_cache_get_or_set("sources", user, ("all",), load_sources)


@app.post(
    "/api/datasets/save",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))],
)
async def api_dataset_save(
    body: dict, user: dict = Depends(require_permission("datasets.write"))
):
    sql = str(body.get("sql") or body.get("sql_def") or "")
    body = {
        **body,
        "sources": _merge_declared_and_inferred_bronze_sources(
            body.get("sources"),
            sql,
        ),
    }
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload("save_dataset", body, user),
        )
        r.raise_for_status()
    return r.json()


@app.get("/api/datasets/{name}/detail", dependencies=[Depends(require_permission("datasets.read"))])
async def api_dataset_detail(name: str, user: dict = Depends(require_permission("datasets.read"))):
    definition = await _refinement_invoke(
        "get_dataset_definition", {"name": name}, user=user
    )
    schema_payload = None
    schema_error = None
    try:
        schema_payload = await _refinement_invoke(
            "get_schema", {"name": name}, user=user
        )
    except HTTPException as exc:
        schema_error = str(exc.detail or "Dataset schema unavailable")
    return _normalize_dataset_detail(
        definition,
        schema_payload,
        schema_error,
        user,
        _sanitize_dataset_metadata_for_user,
    )


@app.post(
    "/api/bronze/query",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.write"))],
)
async def api_bronze_query(
    body: dict, user: dict = Depends(require_permission("datasets.write"))
):
    # Restricted to datasets.write because this endpoint accepts arbitrary SQL.
    # Read-only roles (viewer) must use the dataset-scoped endpoints below,
    # which build SQL server-side instead of trusting client input.
    sql = body.get("sql", "").strip()
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
        raise HTTPException(
            r.status_code, _upstream_error_detail(r, "Refinement query failed")
        )
    result = r.json()
    _raise_for_refinement_payload_error(result, "Refinement query failed")
    return result


@app.delete(
    "/api/datasets",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.delete")),
    ],
)
async def api_delete_dataset(
    name: str, user: dict = Depends(require_permission("datasets.delete"))
):
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload("delete_dataset", {"name": name}, user),
        )
    if r.status_code == 404:
        raise HTTPException(404, f"Dataset '{name}' not found")
    return r.json()


@app.get("/api/datasets/{name}/lineage", dependencies=[Depends(require_permission("datasets.read"))])
async def api_dataset_lineage(name: str, user: dict = Depends(require_permission("datasets.read"))):
    try:
        async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
            r = await c.post(
                f"{REFINEMENT_URL}/mcp/invoke",
                json=_mcp_payload("get_lineage", {"name": name, "limit": 20}, user),
            )
    except httpx.TimeoutException:
        return {
            "name": name,
            "lineage": [],
            "degraded": True,
            "error": "lineage_timeout",
        }
    if r.status_code >= 500:
        return {
            "name": name,
            "lineage": [],
            "degraded": True,
            "error": "lineage_unavailable",
        }
    return r.json()


# ── Object explorer (S3/MinIO listing + presigned downloads) ─────────────────

_EXPLORER_DEFAULT_BUCKETS = [
    {
        "id": "lakehouse",
        "label": "Lakehouse",
        "name": os.environ.get("MINIO_BUCKET", "lakehouse"),
    },
    {
        "id": "ses_inbox",
        "label": "SES Inbox",
        "name": os.environ.get("SES_INBOX_BUCKET", "modecissions-mail-inbound-36243c"),
    },
]

_EXPLORER_ADMIN_QUICKLINKS = [
    {"label": "SES inbox (incoming)", "bucket": "ses_inbox", "prefix": "inbound/"},
    {
        "label": "SES inbox (processed)",
        "bucket": "ses_inbox",
        "prefix": "inbound-processed/",
    },
]


def _s3_client():
    return get_boto3_s3_client()


def _resolve_explorer_bucket(bucket: str, user: dict | None = None) -> str:
    ctx = build_security_context(user)
    return _resolve_explorer_bucket_impl(
        bucket,
        _EXPLORER_DEFAULT_BUCKETS,
        is_security_admin=_is_security_admin_context(ctx),
        lakehouse_bucket=os.environ.get("MINIO_BUCKET", "lakehouse"),
    )


def _explorer_quicklinks_for_cartridges(
    active_cartridges: set[str],
) -> list[dict[str, str]]:
    return _explorer_quicklinks_for_cartridges_impl(active_cartridges)


def _explorer_path_allowed(
    path: str, user: dict | None, *, object_access: bool = False
) -> bool:
    ctx = build_security_context(user)
    return _explorer_path_allowed_impl(
        path,
        ctx,
        is_security_admin=_is_security_admin_context(ctx),
        object_access=object_access,
    )


@app.get(
    "/api/explorer/buckets",
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def api_explorer_buckets(user: dict = Depends(require_authenticated)):
    ctx = build_security_context(user)
    buckets = (
        _EXPLORER_DEFAULT_BUCKETS
        if _is_security_admin_context(ctx)
        else [
            item for item in _EXPLORER_DEFAULT_BUCKETS if item.get("id") == "lakehouse"
        ]
    )
    active_cartridges = await _active_scoped_connection_cartridges(
        user, _OPERATIONAL_CARTRIDGES
    )
    quicklink_candidates = [
        *_explorer_quicklinks_for_cartridges(active_cartridges),
        *_EXPLORER_ADMIN_QUICKLINKS,
    ]
    quicklinks = [
        item
        for item in quicklink_candidates
        if _explorer_path_allowed(item.get("prefix", ""), user)
        and (_is_security_admin_context(ctx) or item.get("bucket") == "lakehouse")
    ]
    return {"buckets": buckets, "quicklinks": quicklinks}


@app.get(
    "/api/explorer/list", dependencies=[Depends(require_permission("pipelines.read"))]
)
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
        raise HTTPException(502, f"object storage list failed: {exc}") from exc
    objects = [
        {
            "key": o["Key"],
            "size": o["Size"],
            "last_modified": o["LastModified"].isoformat(),
        }
        for o in resp.get("Contents", [])
        if o.get("Key") != prefix
        and _explorer_path_allowed(o.get("Key", ""), user, object_access=True)
    ]
    folders = [
        p["Prefix"]
        for p in resp.get("CommonPrefixes", [])
        if _explorer_path_allowed(p.get("Prefix", ""), user)
    ]
    return {
        "bucket": bucket_name,
        "prefix": prefix,
        "folders": folders,
        "objects": objects,
        "next_token": resp.get("NextContinuationToken"),
        "is_truncated": bool(resp.get("IsTruncated", False)),
    }


@app.get(
    "/api/explorer/download",
    dependencies=[Depends(require_permission("pipelines.read"))],
)
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
        raise HTTPException(502, f"object storage download failed: {exc}") from exc
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


@app.delete(
    "/api/explorer/object",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("pipelines.write")),
    ],
)
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
        raise HTTPException(502, f"object storage delete failed: {exc}") from exc
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


@app.get("/api/lineage", dependencies=[Depends(require_permission("datasets.read"))])
async def api_lineage(
    cartridge: str | None = None,
    user: dict = Depends(require_permission("datasets.read")),
):
    """Global lineage graph across raw sources and silver/gold datasets."""
    if cartridge:
        _require_technical_cartridge_access(user, cartridge)
    payload = await _refinement_invoke("list_datasets", {}, timeout=15, user=user)
    datasets = _sanitize_datasets_payload_for_user(user, payload or {}).get("datasets") or []
    if cartridge:
        datasets = [d for d in datasets if d.get("cartridge") == cartridge]
    allowed = _user_allowed_cartridges(user)
    if allowed is not None:
        datasets = [d for d in datasets if str(d.get("cartridge") or "") in allowed]

    def source_visible(source: str) -> bool:
        if not _dataset_source_visible_for_user(user, source):
            return False
        return True

    return _lineage_graph_payload(datasets, source_visible=source_visible)


# ── Analytic Apps ─────────────────────────────────────────────────────────────


def _workspace_server_url() -> str:
    raw = os.environ.get("WORKSPACE_INTERNAL_URL") or os.environ.get(
        "WORKSPACE_BACKEND_URL"
    )
    if raw:
        return raw.rstrip("/")
    public = os.environ.get("WORKSPACE_PUBLIC_URL") or os.environ.get("WORKSPACE_URL")
    if (
        Path("/.dockerenv").exists()
        and public
        and re.match(r"^https?://(localhost|127\.0\.0\.1)(:|/|$)", public)
    ):
        return "http://workspace:8001"
    if public:
        return public.rstrip("/")
    if _is_production_env():
        logger.warning("WORKSPACE_INTERNAL_URL is not configured in production")
        return ""
    return "http://localhost:8001"


async def _proxy_workspace_app(
    request: Request, name: str, *, content: bool = False, user: dict | None = None
) -> Response:
    """Serve published analytic app HTML from the same catalog used by /api/apps."""
    runtime_user = user or getattr(request.state, "user", None) or {}
    cartridge = _app_cartridge_id({"name": name})
    if cartridge:
        _require_cartridge_visible(runtime_user, cartridge)
        active = await _active_scoped_connection_cartridges(runtime_user, {cartridge})
        if cartridge not in active:
            return RedirectResponse(url="/apps-gallery", status_code=303)
    html_text, _app = await _refinement_app_html(name, runtime_user)
    return HTMLResponse(
        content=html_text,
        headers=_app_content_headers(),
    )


async def _workspace_app_content_for_embed(
    request: Request,
    name: str,
    user: dict | None,
) -> tuple[str, list[str]]:
    html_text, app = await _refinement_app_html(
        name, getattr(request.state, "user", None) or user or {}
    )
    datasets = set(_datasets_from_app_html(html_text))
    datasets.update(_app_declared_datasets(app))
    try:
        apps_payload = await _apps_payload_visible_and_ready(
            user or {}, include_unready=True
        )
        for app in _apps_from_payload(apps_payload):
            if str(app.get("name") or "") == name:
                datasets.update(_app_declared_datasets(app))
                break
    except Exception:
        logger.debug("Failed to enrich embed app datasets for %s", name, exc_info=True)
    return html_text, sorted(datasets)


async def _refinement_app_html(name: str, user: dict | None) -> tuple[str, dict]:
    _validate_dataset_name(name)
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload("get_app_html", {"name": name}, user or {}),
        )
    if r.status_code >= 400:
        raise HTTPException(
            r.status_code, _upstream_error_detail(r, "App content unavailable")
        )
    payload = r.json()
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    if not isinstance(result, dict) or result.get("error"):
        raise HTTPException(
            404,
            str((result or {}).get("error") or f"App '{name}' not found"),
        )
    html_text = str(result.get("html") or "")
    if not html_text.strip():
        raise HTTPException(404, f"App '{name}' has no HTML content")
    return html_text, result


@app.get("/apps/{name}/embed", dependencies=[Depends(require_permission("apps.read"))])
async def serve_app_embed(
    request: Request,
    name: str,
    user: dict = Depends(require_permission("apps.read")),
):
    _validate_dataset_name(name)
    _html_text, datasets_used = await _workspace_app_content_for_embed(
        request, name, user
    )
    nonce = secrets.token_urlsafe(16)
    return HTMLResponse(
        content=_app_embed_wrapper_html(name, datasets_used, nonce),
        headers={
            "Content-Security-Policy": _app_embed_csp(nonce),
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@app.get(
    "/apps/{name}/content", dependencies=[Depends(require_permission("apps.read"))]
)
async def serve_app_content_proxy(
    request: Request,
    name: str,
    user: dict = Depends(require_permission("apps.read")),
):
    return await _proxy_workspace_app(request, name, content=True, user=user)


@app.get("/apps/{name}", dependencies=[Depends(require_permission("apps.read"))])
async def serve_app(
    name: str, request: Request, user: dict = Depends(require_permission("apps.read"))
):
    return await _proxy_workspace_app(request, name, user=user)


async def _workspace_scope_for_apps_filter(user: dict | None) -> tuple[str, str]:
    return await _workspace_scope_for_apps_filter_impl(
        user,
        context_factory=build_security_context,
        workspace_memberships=_workspace_memberships,
        get_db_pool=_get_db_pool,
        role_admin=ROLE_ADMIN,
        logger=logger,
    )


async def _active_scoped_connection_cartridges(
    user: dict | None,
    candidate_cartridges: set[str] | None = None,
) -> set[str]:
    candidates = _normalize_candidate_cartridges(candidate_cartridges)
    if not candidates:
        return set()
    tenant_id, workspace_id = await _workspace_scope_for_apps_filter(user)
    scoped_user = _user_with_apps_scope(user, tenant_id, workspace_id)
    active: set[str] = set()
    for cartridge in sorted(candidates):
        try:
            async with httpx.AsyncClient(
                headers=_vault_headers_for_user(scoped_user or {}), timeout=5
            ) as c:
                response = await c.get(
                    f"{_VAULT_URL}/connections/{quote(cartridge, safe='')}"
                )
        except Exception:
            logger.debug(
                "Failed to load scoped Vault connections for %s",
                cartridge,
                exc_info=True,
            )
            continue
        if response.status_code in {404, 204} or response.status_code >= 400:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        connections = payload.get("connections") if isinstance(payload, dict) else []
        if isinstance(connections, list) and any(
            isinstance(conn, dict) for conn in connections
        ):
            active.add(cartridge)
    return active


async def _installed_scoped_app_cartridges(
    user: dict | None,
    candidate_cartridges: set[str] | None = None,
) -> set[str]:
    return await _installed_scoped_app_cartridges_impl(
        user,
        candidate_cartridges,
        scope_resolver=_workspace_scope_for_apps_filter,
        get_db_pool=_get_db_pool,
        scoped_db_for_user=scoped_db_for_user,
        scoped_user_factory=_user_with_apps_scope,
        context_visible_cartridges=_context_visible_cartridges,
    )


async def _resolve_scoped_operation_cartridge(
    user: dict | None,
    cartridge: str | None,
    *,
    fallback: str = "sap_successfactors",
    candidates: set[str] | None = None,
) -> tuple[str, set[str]]:
    candidate_set = candidates or _OPERATIONAL_CARTRIDGES
    active = await _active_scoped_connection_cartridges(user, candidate_set)
    allowed = _user_allowed_cartridges(user)
    return _resolve_scoped_operation_cartridge_impl(
        user,
        cartridge,
        active=active,
        allowed=allowed,
        fallback=fallback,
        candidates=candidate_set,
        require_workspace_scope=_require_workspace_scope_for_technical_view,
        require_cartridge_visible=_require_cartridge_visible,
    )


def _resolve_scoped_config_cartridge(
    user: dict | None,
    cartridge: str | None,
    *,
    fallback: str = "sap_successfactors",
    candidates: set[str] | None = None,
) -> str:
    """Resolve read-only cartridge configuration from workspace entitlements.

    Config-only surfaces must not require an active Vault connection; a single
    connected cartridge cannot hide other installed cartridges in the workspace.
    """
    candidate_set = candidates or _OPERATIONAL_CARTRIDGES
    visible = _context_visible_cartridges(user)
    return _resolve_scoped_config_cartridge_impl(
        user,
        cartridge,
        visible=visible,
        is_workspace_scoped=_is_workspace_scoped_user(user),
        fallback=fallback,
        candidates=candidate_set,
        require_cartridge_visible=_require_cartridge_visible,
    )


async def _scope_catalog_cartridge_arg(user: dict | None, cartridge: str | None) -> str:
    # Scope-hardening contract marker retained for source-based tests:
    # no cartridge installed for this workspace
    active = await _active_scoped_connection_cartridges(user, _OPERATIONAL_CARTRIDGES)
    allowed = _user_allowed_cartridges(user)
    return _scope_catalog_cartridge_arg_impl(
        user,
        cartridge,
        active=active,
        allowed=allowed,
        candidates=_OPERATIONAL_CARTRIDGES,
        require_workspace_scope=_require_workspace_scope_for_technical_view,
        require_cartridge_visible=_require_cartridge_visible,
    )


def _gold_dsn_for_readiness() -> str:
    return (
        os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    ).replace("postgresql+psycopg2://", "postgresql://")


async def _gold_ready_datasets_for_apps(
    user: dict | None, apps_payload: Any
) -> tuple[set[str] | None, str]:
    requested = _app_datasets_from_payload(apps_payload)
    if not requested:
        return None, "no_dataset_metadata"
    dsn = _gold_dsn_for_readiness()
    if not dsn:
        return None, "gold_dsn_missing"
    tenant_id, workspace_id = await _workspace_scope_for_apps_filter(user)
    if not workspace_id:
        return set(), "workspace_scope_missing"
    try:
        import asyncpg as _asyncpg

        conn = await _asyncpg.connect(dsn, command_timeout=5)
    except Exception:
        logger.debug("Failed to connect to Gold for app readiness", exc_info=True)
        return None, "gold_unreachable"
    ready: set[str] = set()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id or "",
                workspace_id,
            )
            for dataset in sorted(requested):
                table = f"gold_{dataset}"
                exists = bool(
                    await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
                )
                if not exists:
                    continue
                columns = {
                    str(row["column_name"])
                    for row in await conn.fetch(
                        """
                        SELECT column_name
                          FROM information_schema.columns
                         WHERE table_schema = 'public'
                           AND table_name = $1
                        """,
                        table,
                    )
                }
                if "workspace_id" not in columns:
                    continue
                if "tenant_id" in columns and tenant_id:
                    has_row = await conn.fetchval(
                        f'SELECT 1 FROM public."{table}" WHERE workspace_id::text = $1 AND tenant_id::text = $2 LIMIT 1',
                        workspace_id,
                        tenant_id,
                    )
                else:
                    has_row = await conn.fetchval(
                        f'SELECT 1 FROM public."{table}" WHERE workspace_id::text = $1 LIMIT 1',
                        workspace_id,
                    )
                if has_row:
                    ready.add(dataset)
    except Exception:
        logger.debug("Failed to verify Gold readiness for apps", exc_info=True)
        return None, "gold_check_failed"
    finally:
        await conn.close()
    return ready, "checked"


async def _apps_payload_visible_and_ready(
    user: dict,
    *,
    include_unready: bool = False,
    cartridge: str | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke", json=_mcp_payload("list_apps", {}, user)
        )
    if r.status_code >= 400:
        raise HTTPException(
            r.status_code, _upstream_error_detail(r, "Apps service unavailable")
        )
    payload = r.json()
    requested_cartridge = str(cartridge or "").strip()
    if requested_cartridge:
        _require_cartridge_visible(user, requested_cartridge)
    payload_candidates = _app_payload_cartridge_candidates(payload)
    candidates = {requested_cartridge} if requested_cartridge else payload_candidates
    active_cartridges = await _active_scoped_connection_cartridges(user, candidates)
    scope_mode = "active_connections"
    if include_unready and not active_cartridges:
        active_cartridges = await _installed_scoped_app_cartridges(user, candidates)
        if active_cartridges:
            scope_mode = "installed_cartridges"
    scoped_payload = _filter_apps_payload_to_scoped_connections(
        payload, active_cartridges, scope_mode=scope_mode
    )
    ready_datasets, readiness_mode = await _gold_ready_datasets_for_apps(
        user, scoped_payload
    )
    return _filter_apps_payload_to_ready_datasets(
        scoped_payload,
        ready_datasets,
        mode=readiness_mode,
        include_unready=include_unready,
    )


@app.get("/api/apps", dependencies=[Depends(require_permission("apps.read"))])
async def api_apps(
    user: dict = Depends(require_permission("apps.read")),
    include_unready: bool = False,
    cartridge: str | None = None,
):
    """List published analytic apps visible to the active scoped connections."""
    return await _apps_payload_visible_and_ready(
        user, include_unready=include_unready, cartridge=cartridge
    )


@app.delete(
    "/api/apps/{name}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("apps.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_apps_delete(name: str, user: dict = Depends(require_permission("apps.write"))):
    """Delete a published analytic app by name."""
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload("delete_app", {"name": name}, user),
        )
    payload = r.json()
    result = payload.get("result", payload)
    if not result.get("deleted"):
        raise HTTPException(404, result.get("error") or f"App '{name}' not found")
    return result


@app.get("/api/data/{dataset}", dependencies=[Depends(require_permission("datasets.read"))])
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


@app.get("/api/data/{dataset}/options", dependencies=[Depends(require_permission("datasets.read"))])
async def api_data_options(
    dataset: str, columns: str = "", user: dict = Depends(require_permission("datasets.read"))
):
    """Return distinct values per column for building filter selectors."""
    _validate_dataset_name(dataset)
    cols = _data_api_columns_param(columns)
    if not cols:
        raise HTTPException(
            400, "columns param required, e.g. ?columns=revenue_manager,cliente"
        )

    invalid_col = _data_api_invalid_column(cols)
    if invalid_col:
        raise HTTPException(400, f"Invalid column name: {invalid_col}")

    # SQL produced by _data_api_options_sql targets pggold.gold_<dataset> via Refinement.
    union_sql = _data_api_options_sql(dataset, cols)

    try:
        async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=30) as c:
            r = await c.post(
                f"{REFINEMENT_URL}/mcp/invoke",
                json=_mcp_payload(
                    "preview_transform",
                    {
                        "sql": union_sql,
                        "limit": 5000,
                        "user_context": _rls_user_context(user),
                    },
                    user,
                ),
            )
    except httpx.TransportError as exc:
        raise HTTPException(500, "Options backend unavailable") from exc
    if r.status_code >= 400:
        raise HTTPException(
            r.status_code, _upstream_error_detail(r, "Options backend failed")
        )
    try:
        result = r.json()
    except ValueError as exc:
        raise HTTPException(500, "Options backend returned invalid JSON") from exc
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(500, "Options backend failed")
    if not isinstance(result, dict):
        raise HTTPException(500, "Options backend returned invalid payload")
    return _data_api_options_response(cols, result.get("data", []))


@app.post("/api/data/{dataset}/query", dependencies=[Depends(require_permission("datasets.read"))])
async def api_data_query_filtered(dataset: str, body: dict, request: Request):
    """
    Execute a filtered query against a gold dataset.
    Body: {"filters": {"revenue_manager": "X", "fiscal_year": 2025,
                        "cliente": "Y", "proyecto": "Z"},
           "limit": 1000, "columns": ["col1", "col2"]}
    fiscal_year uses March-February logic automatically.
    """
    _validate_dataset_name(dataset)

    filters = body.get("filters", {})
    limit = _data_api_query_limit(body.get("limit", 2000))
    columns = body.get("columns", ["*"])

    # Forward the authenticated user's context so refinement can apply RLS.
    _user = getattr(request.state, "user", None) or {}
    _user_context = _rls_user_context(_user)

    try:
        filtered_query = _data_api_filtered_query(
            dataset,
            filters=filters,
            limit=limit,
            columns=columns,
        )
    except _DataApiQueryValidationError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=60) as c:
        r = await c.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "preview_transform",
                {
                    "sql": filtered_query.sql,
                    "params": filtered_query.params,
                    "limit": filtered_query.limit,
                    "user_context": _user_context,
                },
                _user,
            ),
        )
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Query failed")
    result = r.json()
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result.get("data", [])


# ── Pipeline DAG ──────────────────────────────────────────────────────────────


@app.get(
    "/studio/cartridges/{cartridge_id}/connections",
    dependencies=[Depends(require_permission("vault.connections.read"))],
)
async def studio_cartridge_connections(
    cartridge_id: str, user: dict = Depends(require_permission("vault.connections.read"))
):
    """Proxy to Vault — returns masked connection config for the cartridge."""
    _require_cartridge_visible(user, cartridge_id)
    vault_url = _vault_url()
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        try:
            r = await c.get(f"{vault_url}/connections/{quote(cartridge_id, safe='')}")
            if r.status_code in (404, 204):
                return {"connections": []}
            if r.status_code >= 500:
                return {"connections": []}
            return r.json()
        except (httpx.HTTPError, ValueError):
            return {"connections": []}


@app.get("/api/pipeline", dependencies=[Depends(require_permission("pipelines.read"))])
async def api_pipeline(
    cartridge: str = "", user: dict = Depends(require_permission("pipelines.read"))
):
    """
    Ensambla el DAG completo: entidades × bronze status × silver datasets × gold deps.
    Fuentes: entity_config (entities), pipeline_runs + jobs (run history), refinement (datasets).
    """
    user = _runtime_user(user)
    cartridge, _active_cartridges = await _resolve_scoped_operation_cartridge(
        user,
        cartridge,
        fallback="sap_successfactors",
    )

    # 1. Entities
    from app.services import cartridge_service as _cs

    entity_list: list[dict] = []
    manifest = await _cs.get_cartridge(cartridge)
    if manifest:
        for e in manifest.get("entities") or []:
            entity_list.append(
                {
                    "entity": e.get("id") or e.get("entity") or "",
                    "mode": e.get("mode", "full"),
                    "watermark_field": e.get("watermark_field"),
                    "description": e.get("description", ""),
                }
            )
    if not entity_list:
        entities_raw = await mcp_registry.invoke(
            cartridge, "list_entities", {}, user=user
        )
        if isinstance(entities_raw, dict):
            entity_list = entities_raw.get("entities", entities_raw.get("result", []))
        elif isinstance(entities_raw, list):
            entity_list = entities_raw

    partial_reasons: dict[str, set[str]] = {}

    def _mark_partial(entity: str, reason: str) -> None:
        if entity:
            partial_reasons.setdefault(entity, set()).add(reason)

    # 2a. pipeline_runs — most recent run per entity (written by Airflow DAGs)
    dag_runs_by_entity: dict[str, dict] = {}
    try:
        _pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 2)
        async with _pipeline_runs_read_conn(_pool, user) as conn:
            rows_pg = await conn.fetch(
                f"""SELECT DISTINCT ON (entity)
                       run_id, dag_id, entity, airflow_dag_run_id,
                       status, mode,
                       started_at, finished_at,
                       record_count, bytes_written, storage_uri,
                       duration_seconds, watermark_updated_to, error_message, extra
                   FROM pipeline_runs
                   WHERE cartridge_id = $1
                     {scope_sql}
                   ORDER BY entity, started_at DESC""",
                cartridge,
                *scope_values,
            )
        raw_runs_by_entity = {row["entity"]: dict(row) for row in rows_pg}
        refreshed, pending, failed = await _pipeline_gather_by_entity(
            {
                entity: _refresh_dag_run_status(dict(row), user)
                for entity, row in raw_runs_by_entity.items()
            },
            PIPELINE_DAG_STATUS_TIMEOUT_SEC,
        )
        for entity, row in raw_runs_by_entity.items():
            dag_runs_by_entity[entity] = refreshed.get(entity) or row
        for entity in pending | failed:
            _mark_partial(entity, "airflow_status_refresh")
    except Exception:
        logger.debug("Could not load pipeline_runs for %s", cartridge, exc_info=True)

    # 2b. jobs table — internal queue (legacy / console-triggered runs)
    all_jobs = await _call_with_optional_user(job_service.list_recent, 100, user=user)
    jobs_by_entity = _pipeline_jobs_by_entity(all_jobs)

    # 3. Silver datasets from refinement
    try:
        all_datasets = (
            await _refinement_invoke(
                "list_datasets", {}, timeout=PIPELINE_DATASETS_TIMEOUT_SEC, user=user
            )
        ).get("datasets", [])
    except Exception:
        all_datasets = []
        # Some in-process tests replace ``httpx.AsyncClient`` with a minimal
        # get-only fake that predates the MCP invoke path. Keep that legacy
        # compatibility path working without changing production behavior.
        if not hasattr(httpx.AsyncClient, "post"):
            try:
                async with httpx.AsyncClient(
                    headers=_hdr_for("REFINEMENT"), timeout=15
                ) as c:
                    r = await c.get(f"{REFINEMENT_URL}/datasets")
                if getattr(r, "status_code", 500) == 200:
                    all_datasets = (r.json() or {}).get("datasets", [])
            except Exception:
                all_datasets = []

    silver_ds = [d for d in all_datasets if d.get("layer") == "silver"]
    gold_ds = [d for d in all_datasets if d.get("layer") == "gold"]
    silver_by_source = _pipeline_silver_datasets_by_source(silver_ds)

    snapshot_work: dict[str, Any] = {}
    for e in entity_list:
        entity = e.get("entity") or e.get("name") or ""
        if not entity:
            continue
        dag_run = dag_runs_by_entity.get(entity)
        last_job = jobs_by_entity.get(entity)
        bronze_date, bronze_count = _pipeline_bronze_date_count(dag_run, last_job)
        if not bronze_date or bronze_count is None:
            snapshot_work[entity] = _call_with_optional_user(
                _bronze_physical_snapshot,
                cartridge,
                entity,
                user=user,
            )

    (
        physical_bronze_by_entity,
        snapshot_pending,
        snapshot_failed,
    ) = await _pipeline_gather_by_entity(
        snapshot_work,
        PIPELINE_BRONZE_SNAPSHOT_TIMEOUT_SEC,
    )
    for entity in snapshot_pending | snapshot_failed:
        _mark_partial(entity, "bronze_snapshot")

    # 4. Assemble pipeline rows
    rows = []
    for e in entity_list:
        entity = e.get("entity") or e.get("name") or ""
        rows.append(
            _pipeline_entity_row(
                entity_config=e,
                cartridge=cartridge,
                dag_run=dag_runs_by_entity.get(entity),
                last_job=jobs_by_entity.get(entity),
                physical_bronze=physical_bronze_by_entity.get(entity),
                partial_reasons=partial_reasons.get(entity),
                silver_by_source=silver_by_source,
                gold_datasets=gold_ds,
            )
        )

    return _pipeline_response_payload(rows, partial_reasons)


@app.get("/api/dag_templates", dependencies=[Depends(require_permission("pipelines.read"))])
async def api_dag_templates():
    from app.services import dag_templates

    return {"templates": dag_templates.get_all()}


@app.get(
    "/api/dag_templates/{template_id}", dependencies=[Depends(require_permission("pipelines.read"))]
)
async def api_dag_template_code(
    template_id: str, cartridge: str = "my_cartridge", entity: str = "MyEntity"
):
    from app.services import dag_templates

    code = dag_templates.get_code(template_id, cartridge, entity)
    if code is None:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return {"id": template_id, "cartridge": cartridge, "entity": entity, "code": code}


@app.get("/api/pipeline_runs", dependencies=[Depends(require_permission("pipelines.read"))])
async def api_pipeline_runs(
    cartridge: str = "replicon",
    entity: str = None,
    limit: int = 50,
    user: dict = Depends(require_permission("pipelines.read")),
):
    """Recent DAG run history from pipeline_runs table."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    try:
        pool = await _get_db_pool()
        if entity:
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
            async with _pipeline_runs_read_conn(pool, user) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                    f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $3",
                    cartridge,
                    entity,
                    limit,
                    *scope_values,
                )
        else:
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 3)
            async with _pipeline_runs_read_conn(pool, user) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 "
                    f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $2",
                    cartridge,
                    limit,
                    *scope_values,
                )
        response_rows = [_sanitize_pipeline_run_for_user(dict(r), user) for r in rows]
        if entity:
            refreshed_rows = []
            for row in response_rows:
                refreshed_rows.append(
                    _sanitize_pipeline_run_for_user(
                        await _refresh_dag_run_status(row, user),
                        user,
                    )
                )
            response_rows = refreshed_rows
        return {"runs": response_rows}
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("pipeline runs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")


def _sanitize_pipeline_run_for_user(row: dict, user: dict | None) -> dict:
    return _sanitize_pipeline_run_for_user_impl(
        row,
        user,
        dataset_source_visible_for_user=_dataset_source_visible_for_user,
    )


def _format_pipeline_entity_run(row: dict, user: dict | None = None) -> dict:
    return _pipeline_entity_run_payload(_sanitize_pipeline_run_for_user(row, user))


@app.get(
    "/api/pipeline/{cartridge}/{entity}/runs",
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def api_pipeline_entity_runs(
    cartridge: str,
    entity: str,
    limit: int = 20,
    user: dict = Depends(require_permission("pipelines.read")),
):
    """Recent DAG-based pipeline runs for one cartridge entity."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(
            404, f"Entity '{entity}' not found for cartridge '{cartridge}'"
        )

    safe_limit = max(1, min(int(limit or 20), 100))

    try:
        pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
        async with _pipeline_runs_read_conn(pool, user) as conn:
            rows = await conn.fetch(
                f"""
                SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                       started_at, finished_at, duration_seconds,
                       record_count, bytes_written, storage_uri, watermark_updated_to,
                       error_message, extra
                  FROM pipeline_runs
                 WHERE cartridge_id=$1 AND entity=$2
                   {scope_sql}
                 ORDER BY started_at DESC NULLS LAST
                 LIMIT $3
                """,
                cartridge,
                entity,
                safe_limit,
                *scope_values,
            )
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("entity runs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    runs = []
    for row in rows:
        refreshed = await _refresh_dag_run_status(dict(row), user)
        runs.append(_format_pipeline_entity_run(refreshed, user))

    return {
        "cartridge": cartridge,
        "entity": entity,
        "runs": runs,
    }


@app.get(
    "/api/pipeline/{cartridge}/{entity}/runs/{dag_run_id}/logs",
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def api_pipeline_run_logs(
    cartridge: str,
    entity: str,
    dag_run_id: str,
    user: dict = Depends(require_permission("pipelines.read")),
):
    """Basic DAG run logs summary for one cartridge entity run."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if not metadata.get("entity"):
        raise HTTPException(
            404, f"Entity '{entity}' not found for cartridge '{cartridge}'"
        )

    try:
        pool = await _get_db_pool()
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 4)
        async with _pipeline_runs_read_conn(pool, user) as conn:
            row = await conn.fetchrow(
                f"""
                SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                       started_at, finished_at, duration_seconds, error_message
                  FROM pipeline_runs
                 WHERE cartridge_id=$1
                   AND entity=$2
                   AND (run_id=$3 OR airflow_dag_run_id=$3)
                   {scope_sql}
                 ORDER BY started_at DESC NULLS LAST
                 LIMIT 1
                """,
                cartridge,
                entity,
                dag_run_id,
                *scope_values,
            )
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("run logs query failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    if not row:
        raise HTTPException(
            404,
            {
                "error": f"Run '{dag_run_id}' not found for {cartridge}/{entity}",
                "attempted": {
                    "dag_id": metadata.get("dag_id"),
                    "dag_run_id": dag_run_id,
                    "task_ids": [],
                },
            },
        )

    run = await _refresh_dag_run_status(dict(row), user)
    dag_id = run.get("dag_id") or metadata.get("dag_id")
    resolved_dag_run_id = (
        run.get("airflow_dag_run_id") or run.get("run_id") or dag_run_id
    )
    response = {
        "cartridge": cartridge,
        "entity": entity,
        "dag_id": dag_id,
        "dag_run_id": resolved_dag_run_id,
        "status": _normalize_airflow_state(run.get("status")),
        "tasks": [],
        "logs": [],
        "error": run.get("error_message"),
        "available": False,
        "attempted": _airflow_log_attempt(dag_id, resolved_dag_run_id, []),
    }

    try:
        tasks_result = await mcp_registry.invoke(
            "infra",
            "airflow_list_task_instances",
            {
                "dag_id": dag_id,
                "dag_run_id": resolved_dag_run_id,
            },
            user=user,
        )
        if tasks_result.get("error"):
            fallback_task_ids = _airflow_log_task_ids(dag_id, [])
            response["attempted"] = _airflow_log_attempt(
                dag_id, resolved_dag_run_id, fallback_task_ids
            )
            response["error"] = (
                f"Could not list Airflow tasks for dag_id={dag_id} "
                f"dag_run_id={resolved_dag_run_id}: {tasks_result['error']}"
            )
            return response

        tasks = tasks_result.get("tasks") or []
        response["tasks"] = tasks
        task_ids = _airflow_log_task_ids(dag_id, tasks)
        response["attempted"] = _airflow_log_attempt(
            dag_id, resolved_dag_run_id, task_ids, tasks
        )
        if not task_ids:
            response["error"] = (
                f"No Airflow log task found for dag_id={dag_id} dag_run_id={resolved_dag_run_id}; "
                f"available_task_ids={response['attempted']['available_task_ids']}"
            )
            return response

        logs = []
        for task_id in task_ids:
            log_result = await mcp_registry.invoke(
                "infra",
                "airflow_get_task_logs",
                {
                    "dag_id": dag_id,
                    "dag_run_id": resolved_dag_run_id,
                    "task_id": task_id,
                },
                user=user,
            )
            if log_result.get("error"):
                logs.append(
                    {
                        "task_id": task_id,
                        "available": False,
                        "error": log_result["error"],
                    }
                )
            elif not log_result.get("logs"):
                logs.append(
                    {
                        "task_id": task_id,
                        "available": False,
                        "error": "No logs returned by Airflow",
                    }
                )
            else:
                logs.append(
                    {
                        "task_id": task_id,
                        "available": True,
                        "logs": log_result.get("logs", ""),
                    }
                )

        response["logs"] = logs
        response["available"] = any(item.get("available") for item in logs)
        if not response["available"]:
            response["error"] = (
                f"No Airflow logs found for dag_id={dag_id} "
                f"dag_run_id={resolved_dag_run_id} task_ids={task_ids}"
            )
        return response
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("run logs Airflow fetch failed error_id=%s", _eid)
        response["error"] = f"Internal server error. error_id={_eid}"
        return response


@app.post(
    "/api/pipeline/{cartridge}/{entity}/extract",
    dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)],
)
async def api_pipeline_extract(
    cartridge: str,
    entity: str,
    body: dict | None = None,
    user: dict = Depends(require_permission("pipelines.run")),
):
    """Trigger extraction for a single entity. Returns job_id for polling."""
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    body = body or {}
    metadata = await _pipeline_extract_metadata(cartridge, entity)
    if (metadata.get("pattern") or "").lower() == "dag-based":
        if not metadata.get("entity"):
            if _entity_declared_in_static_catalog(cartridge, entity):
                raise HTTPException(
                    400,
                    f"Entity '{entity}' is declared in entities.yaml for cartridge '{cartridge}' "
                    "but has no scoped entity_config entry; configure scope before extraction",
                )
            raise HTTPException(
                404, f"Entity '{entity}' not found for cartridge '{cartridge}'"
            )
        if not metadata.get("enabled"):
            raise HTTPException(400, f"Entity '{entity}' is disabled")
        dag_id = metadata.get("dag_id")
        if not dag_id:
            raise HTTPException(400, f"No dag_id configured for {cartridge}.{entity}")

        extract_conf = _build_dag_extract_conf(
            cartridge, entity, metadata.get("mode"), body
        )
        if not extract_conf.get("conn_id") and metadata.get("connection_id"):
            extract_conf["conn_id"] = _normalize_pipeline_conn_id(
                metadata.get("connection_id")
            )
        if cartridge == "sap_successfactors" and not extract_conf.get("conn_id"):
            raise HTTPException(
                400,
                f"SAP SuccessFactors entity '{entity}' requires entity_config.connection_id "
                "or request conn_id/connection_id before triggering Airflow",
            )
        conf = _apply_user_scope_to_dag_conf(extract_conf, user)
        requested_dag_run_id = _dag_run_id_from_idempotency_key(
            dag_id,
            body.get("idempotency_key") or body.get("request_id"),
        )
        slot = await _reserve_successfactors_entity_extract_slot(
            cartridge=cartridge,
            entity=entity,
            dag_id=dag_id,
            conf=conf,
            user=user,
            requested_dag_run_id=requested_dag_run_id,
        )
        if slot and slot.get("response"):
            return slot["response"]
        if slot and slot.get("dag_run_id"):
            requested_dag_run_id = slot["dag_run_id"]
        result = await _trigger_airflow_extract_dag(
            dag_id, conf, user, requested_dag_run_id
        )
        if result.get("error"):
            if slot and slot.get("reserved"):
                await _record_dag_pipeline_trigger(
                    cartridge=cartridge,
                    entity=entity,
                    dag_id=dag_id,
                    dag_run_id=requested_dag_run_id or "",
                    mode=conf.get("mode", metadata.get("mode") or "incremental"),
                    status="failed",
                    conf={**conf, "trigger_error": result["error"]},
                    tenant_id=conf.get("tenant_id"),
                    workspace_id=conf.get("workspace_id"),
                )
            raise HTTPException(502, f"Airflow trigger failed: {result['error']}")
        dag_run_id = (
            result.get("dag_run_id") or result.get("run_id") or requested_dag_run_id
        )
        await _record_dag_pipeline_trigger(
            cartridge=cartridge,
            entity=entity,
            dag_id=dag_id,
            dag_run_id=dag_run_id,
            mode=conf.get("mode", metadata.get("mode") or "incremental"),
            status=result.get("state") or "queued",
            conf=conf,
            tenant_id=conf.get("tenant_id"),
            workspace_id=conf.get("workspace_id"),
        )
        return {
            "triggered": True,
            "cartridge": cartridge,
            "entity": entity,
            "dag_id": dag_id,
            "job_id": dag_run_id,
            "run_id": dag_run_id,
            "dag_run_id": dag_run_id,
            "state": result.get("state"),
            "conf": conf,
        }

    mode = body.get("mode", "incremental")
    args = {
        "entity": entity,
        "mode": mode,
    }
    conn_id = _normalize_pipeline_conn_id(
        body.get("conn_id") or body.get("connection_id")
    )
    if conn_id:
        args["conn_id"] = conn_id
    result = await mcp_registry.invoke(cartridge, "extract", args, user=user)
    return result


@app.post(
    "/api/pipeline/{cartridge}/extract_all",
    dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)],
)
async def api_pipeline_extract_all(
    cartridge: str,
    body: dict | None = None,
    user: dict = Depends(require_permission("pipelines.run")),
):
    """Trigger extraction for every entity currently visible in the pipeline."""
    body = body or {}
    user = _runtime_user(user)
    _require_cartridge_visible(user, cartridge)
    aggregate_result = await _maybe_trigger_aggregate_extract_all(
        cartridge=cartridge,
        body=body,
        user=user,
    )
    if aggregate_result is not None:
        return aggregate_result

    pipeline = await _call_with_optional_user(api_pipeline, cartridge, user=user)
    rows = pipeline.get("pipeline") or []
    triggered: list[dict] = []
    errors: list[dict] = []

    for row in rows:
        entity = row.get("entity")
        if not entity:
            continue
        extract_body = dict(body)
        scoped_idempotency_key = _sync_entity_idempotency_key(
            body.get("idempotency_key") or body.get("request_id"),
            entity,
        )
        if scoped_idempotency_key:
            extract_body["idempotency_key"] = scoped_idempotency_key
        try:
            result = await _call_with_optional_user(
                api_pipeline_extract, cartridge, entity, extract_body, user=user
            )
            triggered.append(
                {
                    "entity": entity,
                    "job_id": result.get("job_id")
                    or result.get("dag_run_id")
                    or result.get("run_id"),
                    "dag_run_id": result.get("dag_run_id") or result.get("run_id"),
                    "dag_id": result.get("dag_id"),
                    "state": result.get("state") or result.get("status"),
                    "result": result,
                }
            )
        except HTTPException as exc:
            errors.append(
                {
                    "entity": entity,
                    "status_code": exc.status_code,
                    "error": str(exc.detail),
                }
            )
        except Exception:
            error_id = uuid.uuid4().hex
            logger.exception(
                "pipeline extract_all failed for %s.%s error_id=%s",
                cartridge,
                entity,
                error_id,
            )
            errors.append(
                {
                    "entity": entity,
                    "status_code": 500,
                    "error": f"Internal server error. error_id={error_id}",
                }
            )

    return {
        "cartridge": cartridge,
        "attempted": len(triggered) + len(errors),
        "triggered": triggered,
        "errors": errors,
        "blocked": [
            item for item in errors
            if item.get("status_code") in {400, 403, 404}
        ],
        "failed": [
            item for item in errors
            if item.get("status_code") not in {400, 403, 404}
        ],
        "partial": [],
        "skipped_explicit": [],
        "summary": {
            "triggered": len(triggered),
            "errors": len(errors),
            "blocked": sum(1 for item in errors if item.get("status_code") in {400, 403, 404}),
            "failed": sum(1 for item in errors if item.get("status_code") not in {400, 403, 404}),
        },
        "count": len(triggered),
        "error_count": len(errors),
    }


_SYNC_NOW_STALE_AFTER_SECONDS = _env_float("SYNC_NOW_STALE_AFTER_SECONDS", 90 * 60)
_SAP_SUCCESSFACTORS_ACTIVE_WINDOW_SECONDS = max(
    300,
    _env_int("SAP_SUCCESSFACTORS_ACTIVE_EXTRACT_WINDOW_SECONDS", 4 * 60 * 60),
)
_SAP_SUCCESSFACTORS_MAX_ACTIVE_ENTITY_EXTRACTS = max(
    1,
    _env_int("SAP_SUCCESSFACTORS_MAX_ACTIVE_ENTITY_EXTRACTS", 2),
)


async def _reserve_successfactors_entity_extract_slot(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    conf: dict[str, Any],
    user: dict | None,
    requested_dag_run_id: str | None = None,
) -> dict[str, Any] | None:
    if not _sf_reservation_applies(
        cartridge=cartridge,
        dag_id=dag_id,
        expected_cartridge=_SAP_SUCCESSFACTORS_CARTRIDGE,
        entity_dag_id=_SAP_SUCCESSFACTORS_ENTITY_DAG_ID,
    ):
        return None

    tenant_id = conf.get("tenant_id") or build_security_context(user).get("tenant_id")
    workspace_id = conf.get("workspace_id") or build_security_context(user).get(
        "workspace_id"
    )
    scope_columns_present = await _table_has_column(
        "pipeline_runs", "tenant_id", refresh=True
    ) and await _table_has_column("pipeline_runs", "workspace_id", refresh=True)
    if scope_columns_present and not (tenant_id and workspace_id):
        raise HTTPException(403, "pipeline run tenant/workspace scope is required")

    dag_run_id = _sf_reservation_run_id(
        dag_id=dag_id,
        requested_dag_run_id=requested_dag_run_id,
        entity_fragment=_airflow_run_id_fragment(entity),
        token=uuid.uuid4().hex,
    )
    extra = _sf_reservation_extra(conf)
    pool = await _get_db_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                if scope_columns_present:
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true), "
                        "set_config('app.workspace_id', $2, true)",
                        tenant_id,
                        workspace_id,
                    )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                    _SAP_SUCCESSFACTORS_CARTRIDGE,
                    f"{tenant_id or '*'}:{workspace_id or '*'}",
                )
                scope_sql, args = _sf_active_scope_args(
                    cartridge=_SAP_SUCCESSFACTORS_CARTRIDGE,
                    active_window_seconds=_SAP_SUCCESSFACTORS_ACTIVE_WINDOW_SECONDS,
                    scope_columns_present=scope_columns_present,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                rows = [
                    dict(row)
                    for row in await conn.fetch(
                        f"""
                        SELECT run_id, dag_id, entity, airflow_dag_run_id,
                               status, mode, started_at, extra
                          FROM pipeline_runs
                         WHERE cartridge_id=$1
                           AND status IN ('queued', 'running')
                           AND started_at > NOW() - ($2::integer * INTERVAL '1 second')
                           {scope_sql}
                         ORDER BY started_at DESC
                         LIMIT 50
                        """,
                        *args,
                    )
                ]

                extract_all_conflict = _sf_extract_all_conflict(
                    rows,
                    extract_all_dag_id=_SAP_SUCCESSFACTORS_EXTRACT_ALL_DAG_ID,
                    aggregate_entity=_SYNC_AGGREGATE_ENTITY,
                )
                if extract_all_conflict:
                    raise HTTPException(429, detail=extract_all_conflict)

                active_row = _sf_active_entity_run(
                    rows,
                    entity=entity,
                    entity_dag_id=_SAP_SUCCESSFACTORS_ENTITY_DAG_ID,
                )
                if active_row:
                    return {
                        "response": _active_extract_run_payload(
                            row=active_row,
                            cartridge=cartridge,
                            entity=entity,
                            dag_id=dag_id,
                            conf=conf,
                            reason="active_entity_run",
                        )
                    }

                active_entity_runs = _sf_active_entity_run_count(
                    rows,
                    entity_dag_id=_SAP_SUCCESSFACTORS_ENTITY_DAG_ID,
                )
                if active_entity_runs >= _SAP_SUCCESSFACTORS_MAX_ACTIVE_ENTITY_EXTRACTS:
                    raise HTTPException(
                        429,
                        detail=_sf_active_entity_limit_payload(
                            active=active_entity_runs,
                            limit=_SAP_SUCCESSFACTORS_MAX_ACTIVE_ENTITY_EXTRACTS,
                        ),
                    )

                if scope_columns_present:
                    await conn.execute(
                        """
                        INSERT INTO pipeline_runs (
                            run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                            mode, status, started_at, extra, tenant_id, workspace_id
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, 'queued', NOW(), $7::jsonb, $8::uuid, $9::uuid)
                        ON CONFLICT (run_id) DO UPDATE SET
                            airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                            mode = EXCLUDED.mode,
                            status = EXCLUDED.status,
                            tenant_id = COALESCE(pipeline_runs.tenant_id, EXCLUDED.tenant_id),
                            workspace_id = COALESCE(pipeline_runs.workspace_id, EXCLUDED.workspace_id),
                            extra = pipeline_runs.extra || EXCLUDED.extra
                        """,
                        dag_run_id,
                        dag_id,
                        cartridge,
                        entity,
                        dag_run_id,
                        conf.get("mode") or "incremental",
                        extra,
                        tenant_id,
                        workspace_id,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO pipeline_runs (
                            run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                            mode, status, started_at, extra
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, 'queued', NOW(), $7::jsonb)
                        ON CONFLICT (run_id) DO UPDATE SET
                            airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                            mode = EXCLUDED.mode,
                            status = EXCLUDED.status,
                            extra = pipeline_runs.extra || EXCLUDED.extra
                        """,
                        dag_run_id,
                        dag_id,
                        cartridge,
                        entity,
                        dag_run_id,
                        conf.get("mode") or "incremental",
                        extra,
                    )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("SuccessFactors extraction backpressure check failed")
        raise HTTPException(
            503,
            detail={
                "reason": "backpressure_unavailable",
                "message": "Could not reserve SAP SuccessFactors extraction slot.",
                "error": str(exc),
            },
        ) from exc

    return {"dag_run_id": dag_run_id, "reserved": True}


async def _maybe_trigger_aggregate_extract_all(
    *, cartridge: str, body: dict[str, Any], user: dict | None
) -> dict[str, Any] | None:
    if cartridge not in _SYNC_EXTRACT_ALL_DAGS:
        return None
    mode, target = _pipeline_extract_all_mode_target(body)
    conn_id = await _resolve_pipeline_sync_conn_id(
        cartridge, body.get("conn_id") or body.get("connection_id"), user
    )
    run_id = _pipeline_extract_all_run_id(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        body=body,
    )
    result = await _trigger_sync_aggregate_extract_all(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        run_id=run_id,
        user=user,
    )
    if result is None:
        return None
    return _pipeline_extract_all_public_response(result)


def _sync_now_lock_key(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    user: dict | None,
) -> str:
    ctx = build_security_context(user)
    return _sync_now_lock_key_impl(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        tenant_id=ctx.get("tenant_id"),
        workspace_id=ctx.get("workspace_id"),
    )


async def _fetch_active_sync_run(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    user: dict | None,
) -> dict[str, Any] | None:
    pool = await _get_db_pool()
    has_mode = await _table_has_column("pipeline_runs", "mode", refresh=True)
    has_extra = await _table_has_column("pipeline_runs", "extra", refresh=True)
    has_started_at = await _table_has_column(
        "pipeline_runs", "started_at", refresh=True
    )
    lookup = _active_sync_run_lookup_parts(
        cartridge=cartridge,
        mode=mode,
        target=target,
        has_mode=has_mode,
        has_extra=has_extra,
        has_started_at=has_started_at,
    )
    clauses = lookup["clauses"]
    args = lookup["args"]
    scope_sql, scope_values = await _pipeline_runs_scope_predicate(
        user, len(args) + 1, refresh_columns=True
    )
    order_sql = lookup["order_sql"]
    try:
        async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM pipeline_runs
                 WHERE {' AND '.join(clauses)}
                   {scope_sql}
                 ORDER BY {order_sql}
                 LIMIT 1
                """,
                *args,
                *scope_values,
            )
    except Exception:
        logger.warning(
            "active sync run lookup failed for cartridge=%s mode=%s target=%s conn_id=%s",
            cartridge,
            mode,
            target,
            conn_id,
            exc_info=True,
        )
        return None
    return dict(row) if row else None


async def _trigger_sync_aggregate_extract_all(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    run_id: str,
    user: dict | None,
) -> dict[str, Any] | None:
    dag_id = _SYNC_EXTRACT_ALL_DAGS.get(cartridge)
    if not dag_id:
        return None

    conf = {
        "cartridge_id": cartridge,
        "mode": mode,
        "target": target,
        "idempotency_key": run_id,
    }
    if conn_id:
        conf["conn_id"] = conn_id
    conf = _apply_user_scope_to_dag_conf(conf, user)
    requested_dag_run_id = _dag_run_id_from_idempotency_key(dag_id, run_id)
    result = await _trigger_airflow_extract_dag(
        dag_id, conf, user, requested_dag_run_id
    )
    if result.get("error"):
        return {
            "cartridge": cartridge,
            "triggered": [],
            "errors": [
                {
                    "entity": _SYNC_AGGREGATE_ENTITY,
                    "status_code": 502,
                    "error": f"Airflow trigger failed: {result['error']}",
                }
            ],
            "count": 0,
            "error_count": 1,
            "trigger_strategy": "aggregate_dag",
        }

    dag_run_id = (
        result.get("dag_run_id") or result.get("run_id") or requested_dag_run_id
    )
    await _record_dag_pipeline_trigger(
        cartridge=cartridge,
        entity=_SYNC_AGGREGATE_ENTITY,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
        mode=conf.get("mode", mode),
        status=result.get("state") or "queued",
        conf=conf,
        tenant_id=conf.get("tenant_id"),
        workspace_id=conf.get("workspace_id"),
    )
    triggered = {
        "entity": _SYNC_AGGREGATE_ENTITY,
        "job_id": dag_run_id,
        "dag_run_id": dag_run_id,
        "dag_id": dag_id,
        "state": result.get("state"),
        "result": result,
    }
    return {
        "cartridge": cartridge,
        "triggered": [triggered],
        "errors": [],
        "count": 1,
        "error_count": 0,
        "trigger_strategy": "aggregate_dag",
    }


async def _ensure_sync_packaged_datasets(
    *, cartridge: str, user: dict | None
) -> dict[str, Any]:
    if cartridge != "sap_successfactors":
        return {"status": "skipped", "reason": "cartridge_not_packaged"}
    tenant_id, workspace_id = _workspace_scope_from_user(user)
    from app.services.seed_packaged_datasets import (
        seed_packaged_datasets_for_workspace,
    )

    pool = await _get_db_pool()
    result = await seed_packaged_datasets_for_workspace(
        pool,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        cartridge_id=cartridge,
    )
    logger.info(
        "sync packaged dataset seed cartridge=%s tenant=%s workspace=%s status=%s seeded_rows=%s",
        cartridge,
        tenant_id,
        workspace_id,
        result.get("status"),
        result.get("seeded_rows"),
    )
    return result


def _sync_agentops_is_terminal(payload: Any) -> bool:
    return _agentops_sync_agentops_is_terminal(payload)


def _sync_agentops_monitor_candidates(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _agentops_sync_agentops_monitor_candidates(agents)


def _successfactors_talent_monitor_contract() -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    return _agentops_successfactors_talent_monitor_contract()


def _is_successfactors_talent_monitor_row(agent: Any) -> bool:
    return _agentops_is_successfactors_talent_monitor_row(agent)


def _has_operational_monitor_contract(agent: Any) -> bool:
    return _agentops_has_operational_monitor_contract(agent)


def _successfactors_talent_monitor_needs_runtime_repair(agent: Any) -> bool:
    return _agentops_successfactors_talent_monitor_needs_runtime_repair(agent)


def _merge_agent_tools(primary: list[str], secondary: Any) -> list[str]:
    return _agentops_merge_agent_tools(primary, secondary)


def _coerce_successfactors_talent_monitor_payload(body: dict) -> dict:
    return _agentops_coerce_successfactors_talent_monitor_payload(body)


async def _ensure_successfactors_talent_monitor(user: dict | None) -> None:
    await _agentops_ensure_successfactors_talent_monitor(
        user,
        get_db_pool=_get_db_pool,
        build_security_context=build_security_context,
        logger=logger,
    )


async def _repair_successfactors_talent_monitor_row_if_needed(
    agent: dict | None,
    user: dict | None,
) -> dict | None:
    if not agent or not _successfactors_talent_monitor_needs_runtime_repair(agent):
        return agent
    await _ensure_successfactors_talent_monitor(user)
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id:
        return agent
    repaired = await _agents.get_agent(agent_id, user_context=user)
    return repaired or agent


async def _repair_successfactors_talent_monitor_list_if_needed(
    agents: list[dict],
    user: dict | None,
    *,
    cartridge_id: str | None = None,
    include_inactive: bool = False,
) -> list[dict]:
    if not any(_successfactors_talent_monitor_needs_runtime_repair(agent) for agent in agents):
        return agents
    await _ensure_successfactors_talent_monitor(user)
    refreshed = await _agents.list_agents(
        cartridge_id,
        include_inactive,
        user_context=user,
    )
    return refreshed or agents


async def _repair_loaded_successfactors_talent_monitor_if_needed(
    agent: Any,
    user_context: dict | None,
) -> Any:
    row = {
        "id": str(getattr(agent, "id", "") or ""),
        "cartridge_id": str(getattr(agent, "cartridge_id", "") or ""),
        "slug": str(getattr(agent, "slug", "") or ""),
        "extra": getattr(agent, "extra", None) or {},
    }
    if not _successfactors_talent_monitor_needs_runtime_repair(row):
        return agent
    if not row["id"]:
        return agent
    await _ensure_successfactors_talent_monitor(user_context)
    repaired = await _agent_runtime.load_agent(row["id"], user_context=user_context)
    return repaired or agent


async def _run_sync_agentops_monitors(
    *,
    cartridge: str,
    sync_run_id: str,
    user: dict | None,
) -> dict[str, Any]:
    from datetime import datetime as _dt, timezone as _tz

    checked_at_dt = _dt.now(_tz.utc)
    checked_at = checked_at_dt.isoformat()
    sync_fire_at = _dt(1970, 1, 1, tzinfo=_tz.utc)
    schedule_key = _sync_agentops.sync_agentops_schedule_key(sync_run_id)
    if cartridge == "sap_successfactors":
        await _ensure_successfactors_talent_monitor(user)
    agents = await _agents.list_agents(
        cartridge_id=cartridge,
        include_inactive=False,
        user_context=user,
    )
    candidates = _sync_agentops_monitor_candidates(agents)
    if not candidates:
        return _sync_agentops.no_monitor_candidates_payload(checked_at)

    results: list[dict[str, Any]] = []
    completed = 0
    failed = 0
    for agent_row in candidates[:12]:
        agent_id = str(agent_row.get("id") or "").strip()
        agent_slug = str(agent_row.get("slug") or agent_id)
        if not agent_id:
            failed += 1
            results.append(_sync_agentops.missing_agent_id_result(agent_slug))
            continue
        try:
            agent = await _agent_runtime.load_agent(agent_id, user_context=user)
            if not agent:
                raise RuntimeError("agent not found in scoped runtime")
            if not (str(agent.tenant_id or "").strip() and str(agent.workspace_id or "").strip()):
                raise RuntimeError("monitor agent requires tenant/workspace scope")
            reservation = await _agent_scheduler.reserve_scheduled_run(
                agent_id=str(agent.id),
                tenant_id=str(agent.tenant_id),
                workspace_id=str(agent.workspace_id),
                scheduled_fire_at=sync_fire_at,
                schedule_key=schedule_key,
                airflow_dag_run_id=sync_run_id,
                metadata={
                    "agent_slug": agent.slug,
                    "cartridge_id": agent.cartridge_id,
                    "sync_run_id": sync_run_id,
                    "checked_at": checked_at,
                    "source": "sync-now",
                },
            )
            if reservation.get("duplicate"):
                status = str(reservation.get("status") or "unknown")
                completed_delta, failed_delta = _sync_agentops.duplicate_monitor_counters(status)
                completed += completed_delta
                failed += failed_delta
                results.append(
                    _sync_agentops.duplicate_monitor_result(
                        agent_id=agent_id,
                        agent_slug=agent_slug,
                        reservation=reservation,
                    )
                )
                continue
            message = (
                "Ejecuta el monitor operativo posterior a sincronizacion para "
                f"{cartridge}. Usa datos reales recien extraidos; no simules."
            )
            try:
                result = await _agent_runtime.run_scheduled_monitor(
                    agent,
                    message,
                    scheduled_fire_at=checked_at,
                )
            except Exception as exc:
                await _agent_scheduler.finish_scheduled_run(
                    schedule_run_id=reservation.get("id"),
                    agent_run_id=None,
                    status="error",
                    tenant_id=str(agent.tenant_id),
                    workspace_id=str(agent.workspace_id),
                    error_message=f"{type(exc).__name__}: {exc}",
                    metadata={"sync_run_id": sync_run_id, "checked_at": checked_at},
                )
                raise
            await _agent_scheduler.finish_scheduled_run(
                schedule_run_id=reservation.get("id"),
                agent_run_id=result.get("run_id") if isinstance(result, dict) else None,
                status="ok",
                tenant_id=str(agent.tenant_id),
                workspace_id=str(agent.workspace_id),
                metadata={
                    "sync_run_id": sync_run_id,
                    "checked_at": checked_at,
                    "deterministic_monitor": bool(
                        isinstance(result, dict) and result.get("deterministic_monitor")
                    ),
                },
            )
            completed += 1
            results.append(
                _sync_agentops.scheduled_monitor_success_result(
                    agent_id=agent_id,
                    agent_slug=agent_slug,
                    result=result if isinstance(result, dict) else None,
                    reservation=reservation,
                )
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning(
                "sync AgentOps monitor failed cartridge=%s agent=%s: %s",
                cartridge,
                agent_slug,
                exc,
            )
            results.append(
                _sync_agentops.scheduled_monitor_failure_result(
                    agent_id=agent_id,
                    agent_slug=agent_slug,
                    exc=exc,
                )
            )

    total = len(candidates[:12])
    return _sync_agentops.sync_agentops_summary(
        checked_at=checked_at,
        sync_run_id=sync_run_id,
        total=total,
        completed=completed,
        failed=failed,
        results=results,
    )


async def _upsert_sync_run(
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    status: str,
    user: dict | None,
    extra: dict[str, Any],
    error_message: str | None = None,
) -> dict[str, Any]:
    pool = await _get_db_pool()
    ctx = build_security_context(user)
    tenant_id = str(ctx.get("tenant_id") or "").strip() or None
    workspace_id = str(ctx.get("workspace_id") or "").strip() or None
    scope_columns_present = await _table_has_column(
        "pipeline_runs", "tenant_id", refresh=True
    ) and await _table_has_column("pipeline_runs", "workspace_id", refresh=True)
    if (
        scope_columns_present
        and cartridge != "platform"
        and not (tenant_id and workspace_id)
    ):
        raise HTTPException(403, "sync run tenant/workspace scope is required")
    has_scope = bool(tenant_id and workspace_id and scope_columns_present)
    finished = status in _SYNC_TERMINAL_STATUSES
    extra_json = json.dumps(extra)
    command_status = ""

    if has_scope:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), "
                    "set_config('app.workspace_id', $2, true)",
                    tenant_id,
                    workspace_id,
                )
                command_status = await conn.execute(
                    """
                    INSERT INTO pipeline_runs (
                        run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                        mode, status, started_at, finished_at, error_message,
                        extra, tenant_id, workspace_id
                    )
                    VALUES (
                        $1, $2, $3, $4, $1, $5, $6, NOW(),
                        CASE WHEN $7 THEN NOW() ELSE NULL END,
                        $8, $9::jsonb, $10::uuid, $11::uuid
                    )
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        mode = EXCLUDED.mode,
                        finished_at = COALESCE(EXCLUDED.finished_at, pipeline_runs.finished_at),
                        error_message = EXCLUDED.error_message,
                        tenant_id = COALESCE(pipeline_runs.tenant_id, EXCLUDED.tenant_id),
                        workspace_id = COALESCE(pipeline_runs.workspace_id, EXCLUDED.workspace_id),
                        extra = COALESCE(pipeline_runs.extra, '{}'::jsonb) || EXCLUDED.extra
                    """,
                    run_id,
                    _SYNC_NOW_DAG_ID,
                    cartridge,
                    _SYNC_NOW_ENTITY,
                    mode,
                    status,
                    finished,
                    error_message,
                    extra_json,
                    tenant_id,
                    workspace_id,
                )
    else:
        command_status = await pool.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                mode, status, started_at, finished_at, error_message, extra
            )
            VALUES (
                $1, $2, $3, $4, $1, $5, $6, NOW(),
                CASE WHEN $7 THEN NOW() ELSE NULL END,
                $8, $9::jsonb
            )
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                mode = EXCLUDED.mode,
                finished_at = COALESCE(EXCLUDED.finished_at, pipeline_runs.finished_at),
                error_message = EXCLUDED.error_message,
                extra = COALESCE(pipeline_runs.extra, '{}'::jsonb) || EXCLUDED.extra
            """,
            run_id,
            _SYNC_NOW_DAG_ID,
            cartridge,
            _SYNC_NOW_ENTITY,
            mode,
            status,
            finished,
            error_message,
            extra_json,
        )
    return {
        "command_status": command_status,
        "scope_columns_present": scope_columns_present,
        "has_scope": has_scope,
        "tenant_id_present": bool(tenant_id),
        "workspace_id_present": bool(workspace_id),
    }


async def _fetch_sync_run(
    *,
    cartridge: str,
    run_id: str,
    user: dict | None,
) -> dict[str, Any] | None:
    pool = await _get_db_pool()
    scope_sql, scope_values = await _pipeline_runs_scope_predicate(
        user, 3, refresh_columns=True
    )
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        row = await conn.fetchrow(
            f"""
            SELECT *
              FROM pipeline_runs
             WHERE cartridge_id=$1
               AND run_id=$2
               AND entity='{_SYNC_NOW_ENTITY}'
               {scope_sql}
             LIMIT 1
            """,
            cartridge,
            run_id,
            *scope_values,
        )
    return dict(row) if row else None


async def _sync_child_runs(
    *,
    cartridge: str,
    run_ids: list[str],
    user: dict | None,
) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    pool = await _get_db_pool()
    scope_sql, scope_values = await _pipeline_runs_scope_predicate(
        user, 3, refresh_columns=True
    )
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM pipeline_runs
             WHERE (
                    run_id = ANY($1::text[])
                 OR airflow_dag_run_id = ANY($1::text[])
               )
               AND cartridge_id=$2
               AND entity <> '{_SYNC_NOW_ENTITY}'
               {scope_sql}
             ORDER BY started_at ASC
            """,
            run_ids,
            cartridge,
            *scope_values,
        )
    refreshed: list[dict[str, Any]] = []
    for row in rows:
        refreshed.append(await _refresh_dag_run_status(dict(row), user))
    return refreshed


def _sync_gold_refresh_airflow_run_id(
    row: dict[str, Any],
    child_rows: list[dict[str, Any]],
) -> str:
    return _sync_control_room.gold_refresh_airflow_run_id(
        row,
        child_rows,
        aggregate_entity=_SYNC_AGGREGATE_ENTITY,
    )


async def _run_sync_control_room_gold_refresh(
    *,
    cartridge: str,
    row: dict[str, Any],
    child_rows: list[dict[str, Any]],
    gold_refresh_summary: dict[str, Any],
    user: dict | None,
) -> dict[str, Any]:
    from datetime import datetime as _dt, timezone as _tz

    checked_at = _dt.now(_tz.utc).isoformat()
    datasets = _sync_gold_refresh_dataset_names(gold_refresh_summary)
    if not datasets:
        return _sync_control_room.gold_refresh_skipped_payload(checked_at)
    ctx = build_security_context(user)
    tenant_id = str(
        ctx.get("tenant_id") or ctx.get("active_tenant_id") or ""
    ).strip()
    workspace_id = str(
        ctx.get("workspace_id") or ctx.get("active_workspace_id") or ""
    ).strip()
    if not tenant_id or not workspace_id:
        return _sync_control_room.gold_refresh_missing_scope_payload(
            checked_at=checked_at,
            datasets=datasets,
        )
    airflow_dag_run_id = _sync_gold_refresh_airflow_run_id(row, child_rows)
    run_ref = _sync_control_room.gold_refresh_run_ref(
        workspace_id=workspace_id,
        cartridge=cartridge,
        airflow_dag_run_id=airflow_dag_run_id,
    )
    payload = _sync_control_room.gold_refresh_intelligence_payload(
        cartridge=cartridge,
        datasets=datasets,
        row=row,
        airflow_dag_run_id=airflow_dag_run_id,
        run_ref=run_ref,
        gold_refresh_summary=gold_refresh_summary,
    )
    try:
        from app.services import intelligence_engine

        result = await intelligence_engine.run_intelligence(user, payload, persist=True)
    except Exception as exc:  # noqa: BLE001
        return _sync_control_room.gold_refresh_error_payload(
            checked_at=checked_at,
            run_ref=run_ref,
            datasets=datasets,
            exc=exc,
        )
    return _sync_control_room.gold_refresh_result_payload(
        checked_at=checked_at,
        run_ref=run_ref,
        datasets=datasets,
        result=result if isinstance(result, dict) else None,
    )


async def _build_sync_run_status(
    *,
    cartridge: str,
    row: dict[str, Any],
    user: dict | None,
) -> dict[str, Any]:
    extra = _sync_extra_from_row(row)
    steps = (
        extra.get("steps")
        if isinstance(extra.get("steps"), list)
        else _initial_sync_steps()
    )
    triggered = (
        extra.get("triggered_entities")
        if isinstance(extra.get("triggered_entities"), list)
        else []
    )
    errors = extra.get("errors") if isinstance(extra.get("errors"), list) else []
    child_run_ids = [
        str(item.get("dag_run_id") or item.get("job_id") or "").strip()
        for item in triggered
        if isinstance(item, dict)
        and str(item.get("dag_run_id") or item.get("job_id") or "").strip()
    ]
    child_rows = await _sync_child_runs(
        cartridge=cartridge, run_ids=child_run_ids, user=user
    )
    gold_refresh_summary = _sync_child_gold_refresh_summary(child_rows)
    child_progress = _sync_progress.sync_child_progress_rows(
        child_rows,
        aggregate_entity=_SYNC_AGGREGATE_ENTITY,
        sync_now_entity=_SYNC_NOW_ENTITY,
    )
    aggregate_child_rows = child_progress["aggregate_child_rows"]
    aggregate_payload_ready = child_progress["aggregate_payload_ready"]
    entity_child_rows = child_progress["entity_child_rows"]
    aggregate_summary_pending = child_progress["aggregate_summary_pending"]
    progress_rows = child_progress["progress_rows"]
    child_counts = _sync_progress.sync_child_status_counts(
        progress_rows,
        blocked_statuses=_SYNC_CHILD_BLOCKED_STATUSES,
        terminal_statuses=_SYNC_CHILD_TERMINAL_STATUSES,
    )
    running_children = bool(child_counts["running"])
    failed_children = int(child_counts["failed"])
    success_children = int(child_counts["success"])
    partial_children = int(child_counts["partial"])
    blocked_children = int(child_counts["blocked"])
    terminal_children = int(child_counts["terminal"])
    entity_summary = _sync_step_entity_summary(progress_rows)
    stale_running = (
        str(row.get("status") or "").lower() not in _SYNC_TERMINAL_STATUSES
        and running_children
        and not success_children
        and (_sync_run_age_seconds(row) or 0) > _SYNC_NOW_STALE_AFTER_SECONDS
    )
    if stale_running:
        running_children = False
        failed_children = max(failed_children, 1)
        errors = [
            *errors,
            {
                "entity": _SYNC_NOW_ENTITY,
                "status_code": 504,
                "error": "Airflow sync run timed out before completing; start a new sync.",
            },
        ]

    try:
        pipeline_payload = await _call_with_optional_user(
            api_pipeline, cartridge, user=user
        )
        pipeline_rows = pipeline_payload.get("pipeline") or []
    except Exception as exc:
        pipeline_rows = []
        errors = [
            *errors,
            {"entity": "__pipeline__", "status_code": 503, "error": str(exc)},
        ]

    materialization = _sync_progress.sync_pipeline_materialization_summary(
        pipeline_rows,
        gold_refresh_summary,
    )
    bronze_ready = int(materialization["bronze_ready"])
    silver_ready = int(materialization["silver_ready"])
    gold_ready = int(materialization["gold_ready"])
    gold_total = int(materialization["gold_total"])
    gold_partial = bool(materialization["gold_partial"])
    control_room_gold_refresh = (
        dict(extra.get("control_room_gold_refresh"))
        if isinstance(extra.get("control_room_gold_refresh"), dict)
        else {}
    )
    if (
        cartridge == "sap_successfactors"
        and gold_ready
        and not running_children
        and not _sync_control_room_gold_refresh_terminal(control_room_gold_refresh)
    ):
        control_room_gold_refresh = await _run_sync_control_room_gold_refresh(
            cartridge=cartridge,
            row=row,
            child_rows=child_rows,
            gold_refresh_summary=gold_refresh_summary,
            user=user,
        )

    updates: dict[str, dict[str, Any]] = {
        "connection": _sync_progress.sync_connection_step_update(
            triggered=triggered,
            child_rows=child_rows,
            bronze_ready=bronze_ready,
        )
    }
    child_total = max(
        len(entity_child_rows),
        len(progress_rows),
        len(child_run_ids),
        len(triggered),
        1,
    )
    child_done = terminal_children
    bronze_update = _sync_progress.sync_bronze_step_update(
        running_children=running_children,
        aggregate_summary_pending=aggregate_summary_pending,
        progress_count=len(progress_rows),
        failed_children=failed_children,
        success_children=success_children,
        partial_children=partial_children,
        blocked_children=blocked_children,
        errors=errors,
        child_done=child_done,
        child_total=child_total,
        bronze_ready=bronze_ready,
        entity_summary=entity_summary,
    )
    if bronze_update:
        updates["bronze"] = bronze_update

    silver_gold_update = _sync_progress.sync_silver_gold_step_update(
        bronze_status=str(updates.get("bronze", {}).get("status") or ""),
        running_children=running_children,
        gold_total=gold_total,
        silver_ready=silver_ready,
        gold_ready=gold_ready,
        failed_children=failed_children,
        partial_children=partial_children,
        blocked_children=blocked_children,
        gold_partial=gold_partial,
        errors=errors,
    )
    if silver_gold_update:
        updates["silver_gold"] = silver_gold_update

    control_room_ready = False
    control_room_checked_at: str | None = None
    control_room_snapshot: dict[str, Any] = {}
    if cartridge == "sap_successfactors" and (bronze_ready or silver_ready or gold_ready):
        try:
            from app.services import control_room_service

            dashboard_payload = await control_room_service.dashboard(user, persist=True)
            gold_kpis = (
                await control_room_service.sap_successfactors_gold_kpis(user)
                if gold_ready
                else {}
            )
            talent_kpis = (
                await control_room_service.sap_successfactors_talent_kpis(user)
                if gold_ready
                else {}
            )
            from datetime import datetime as _dt, timezone as _tz

            control_room_checked_at = _dt.now(_tz.utc).isoformat()
            control_room_snapshot = _sync_progress.sync_control_room_snapshot(
                dashboard_payload,
                control_room_gold_refresh,
            )
            control_room_publish_failed = (
                str(control_room_gold_refresh.get("status") or "").lower() == "failed"
            )
            control_room_ready = bool(
                gold_ready and gold_kpis and talent_kpis and not control_room_publish_failed
            )
            updates["control_room"] = _sync_progress.sync_control_room_step_update(
                control_room_snapshot=control_room_snapshot,
                control_room_ready=control_room_ready,
                control_room_publish_failed=control_room_publish_failed,
                gold_ready=gold_ready,
            )
        except Exception as exc:
            from datetime import datetime as _dt, timezone as _tz

            control_room_checked_at = _dt.now(_tz.utc).isoformat()
            updates["control_room"] = _sync_progress.sync_control_room_error_step_update(
                exc
            )
    elif cartridge == "sap_successfactors":
        from datetime import datetime as _dt, timezone as _tz

        control_room_checked_at = _dt.now(_tz.utc).isoformat()
        updates["control_room"] = _sync_progress.sync_control_room_waiting_step_update(
            running_children=running_children
        )
    else:
        control_room_ready = bool(gold_ready)
        updates["control_room"] = _sync_progress.sync_control_room_generic_step_update(
            gold_ready=gold_ready
        )

    agentops_refresh = (
        dict(extra.get("agentops_refresh"))
        if isinstance(extra.get("agentops_refresh"), dict)
        else {}
    )
    if cartridge == "sap_successfactors":
        can_run_agentops = (
            not running_children
            and str(updates.get("control_room", {}).get("status") or "")
            in {"success", "partial"}
            and bool(bronze_ready or silver_ready or gold_ready)
        )
        if can_run_agentops and not _sync_agentops_is_terminal(agentops_refresh):
            try:
                agentops_refresh = await _run_sync_agentops_monitors(
                    cartridge=cartridge,
                    sync_run_id=str(row["run_id"]),
                    user=user,
                )
            except Exception as exc:  # noqa: BLE001
                from datetime import datetime as _dt, timezone as _tz

                logger.warning(
                    "sync AgentOps refresh failed cartridge=%s run_id=%s: %s",
                    cartridge,
                    row.get("run_id"),
                    exc,
                )
                agentops_refresh = {
                    "status": "failed",
                    "checked_at": _dt.now(_tz.utc).isoformat(),
                    "total": 1,
                    "completed": 0,
                    "failed": 1,
                    "results": [],
                    "reason": f"{type(exc).__name__}: {exc}"[:500],
                }
        updates["agents_intelligence"] = (
            _sync_progress.sync_agents_intelligence_step_update(
                applies=True,
                can_run_agentops=can_run_agentops,
                running_children=running_children,
                agentops_refresh=agentops_refresh,
            )
        )
    else:
        updates["agents_intelligence"] = (
            _sync_progress.sync_agents_intelligence_step_update(
                applies=False,
                can_run_agentops=False,
                running_children=running_children,
                agentops_refresh={},
            )
        )

    steps = _merge_sync_steps(steps, updates)
    status = _sync_status_from_steps(steps)
    updated_extra = _sync_progress.sync_updated_extra(
        steps=steps,
        triggered=triggered,
        errors=errors,
        control_room_ready=control_room_ready,
        control_room_checked_at=control_room_checked_at,
        control_room_snapshot=control_room_snapshot,
        agentops_refresh=agentops_refresh,
        gold_refresh_summary=gold_refresh_summary,
        control_room_gold_refresh=control_room_gold_refresh,
        aggregate_payload_ready=aggregate_payload_ready,
        aggregate_summary_pending=aggregate_summary_pending,
        child_run_count=len(child_rows),
        entity_child_run_count=len(entity_child_rows),
        entity_summary=entity_summary,
        previous_extra=extra,
        row_mode=str(row.get("mode") or "") or None,
    )
    await _upsert_sync_run(
        run_id=str(row["run_id"]),
        cartridge=cartridge,
        mode=str(row.get("mode") or updated_extra["mode"]),
        status=status,
        user=user,
        extra=updated_extra,
        error_message=_sync_progress.sync_run_error_message(errors),
    )
    if status in _SYNC_TERMINAL_STATUSES:
        try:
            from app.routers.control_room import _control_room_cache_invalidate

            _control_room_cache_invalidate(user)
        except Exception:
            logger.debug("Could not invalidate Control Room cache after sync", exc_info=True)
    refreshed = await _fetch_sync_run(
        cartridge=cartridge, run_id=str(row["run_id"]), user=user
    )
    return _sync_public_payload(refreshed or row, {**extra, **updated_extra})


@app.post(
    "/api/cartridges/{cartridge_id}/sync-now",
    dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)],
)
async def api_cartridge_sync_now(
    cartridge_id: str,
    body: dict | None = Body(default_factory=dict),
    user: dict = Depends(require_permission("pipelines.run")),
):
    body = body or {}
    user = _runtime_user(user)
    cartridge, _active = await _resolve_scoped_operation_cartridge(
        user, cartridge_id, fallback=cartridge_id
    )
    mode = _sync_clean_mode(body.get("mode"))
    target = _sync_clean_target(body.get("target"))
    conn_id = await _resolve_pipeline_sync_conn_id(
        cartridge, body.get("conn_id") or body.get("connection_id"), user
    )
    request_id = _normalize_sync_now_request_id(
        body.get("request_id") or body.get("idempotency_key")
    )
    lock_key = _sync_now_lock_key(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        user=user,
    )
    pool = await _get_db_pool()
    async with pool.acquire() as sync_lock_conn:
        await sync_lock_conn.execute("SELECT pg_advisory_lock(hashtext($1))", lock_key)
        try:
            run_id = _sync_now_run_id_from_request_id(
                cartridge=cartridge, request_id=request_id, lock_key=lock_key
            )
            if request_id:
                existing_row = await _fetch_sync_run(
                    cartridge=cartridge, run_id=run_id, user=user
                )
                if existing_row:
                    existing_status = str(existing_row.get("status") or "").lower()
                    existing_extra = _sync_extra_from_row(existing_row)
                    if (
                        existing_status in _SYNC_TERMINAL_STATUSES
                        and not _sync_run_needs_final_reconcile(
                            existing_row, existing_extra
                        )
                    ):
                        return _sync_public_payload(
                            existing_row, existing_extra
                        )
                    return await _build_sync_run_status(
                        cartridge=cartridge, row=existing_row, user=user
                    )

            active_row = await _fetch_active_sync_run(
                cartridge=cartridge,
                mode=mode,
                target=target,
                conn_id=conn_id,
                user=user,
            )
            if active_row:
                active_status = await _build_sync_run_status(
                    cartridge=cartridge, row=active_row, user=user
                )
                if (
                    str(active_status.get("status") or "").lower()
                    not in _SYNC_TERMINAL_STATUSES
                ):
                    return active_status

            steps = _merge_sync_steps(
                _initial_sync_steps(),
                {
                    "connection": {
                        "label": "Conexión",
                        "status": "running",
                        "detail": "Validando scope y conexión del cartucho.",
                    }
                },
            )
            await _upsert_sync_run(
                run_id=run_id,
                cartridge=cartridge,
                mode=mode,
                status="running",
                user=user,
                extra={
                    "mode": mode,
                    "target": target,
                    "conn_id": conn_id,
                    "request_id": request_id,
                    "steps": steps,
                    "triggered_entities": [],
                    "errors": [],
                    "control_room_ready": False,
                },
            )
        finally:
            await sync_lock_conn.execute(
                "SELECT pg_advisory_unlock(hashtext($1))", lock_key
            )

    dataset_seed: dict[str, Any] | None = None
    if cartridge == "sap_successfactors":
        try:
            dataset_seed = await _ensure_sync_packaged_datasets(
                cartridge=cartridge,
                user=user,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "sync packaged dataset seed failed cartridge=%s run_id=%s: %s",
                cartridge,
                run_id,
                message,
                exc_info=True,
            )
            steps = _merge_sync_steps(
                steps,
                {
                    "connection": {
                        "label": "Conexión",
                        "status": "success",
                        "detail": "Scope y conexión aceptados por el pipeline.",
                    },
                    "silver_gold": {
                        "label": "Silver/Gold",
                        "status": "failed",
                        "detail": "No se pudieron preparar los refinamientos del workspace.",
                        "error": message[:300],
                        "completed": 0,
                        "total": 1,
                    },
                    "control_room": {
                        "label": "Control Room",
                        "status": "failed",
                        "detail": "Sin Silver/Gold preparados no se puede refrescar Control Room.",
                        "completed": 0,
                        "total": 1,
                    },
                    "agents_intelligence": {
                        "label": "Agentes/IA",
                        "status": "failed",
                        "detail": "Sin materialización no se ejecutan monitores.",
                        "completed": 0,
                        "total": 1,
                    },
                },
            )
            await _upsert_sync_run(
                run_id=run_id,
                cartridge=cartridge,
                mode=mode,
                status="failed",
                user=user,
                extra={
                    "mode": mode,
                    "target": target,
                    "conn_id": conn_id,
                    "request_id": request_id,
                    "steps": steps,
                    "triggered_entities": [],
                    "errors": [
                        {
                            "entity": "__dataset_seed__",
                            "error": message,
                            "reason": "packaged_dataset_seed_failed",
                        }
                    ],
                    "control_room_ready": False,
                    "dataset_seed": {
                        "status": "failed",
                        "reason": "packaged_dataset_seed_failed",
                        "error": message,
                    },
                },
                error_message=message[:500],
            )
            row = await _fetch_sync_run(cartridge=cartridge, run_id=run_id, user=user)
            if row:
                return _sync_public_payload(row, _sync_extra_from_row(row))
            raise HTTPException(
                500,
                "sync packaged dataset seed failed before Airflow trigger",
            )

    extract_body = {
        "mode": mode,
        "target": target,
        "idempotency_key": run_id,
        **({"conn_id": conn_id} if conn_id else {}),
    }
    result: dict[str, Any] = {}
    attempts = 0
    for attempt in range(1, 4):
        attempts = attempt
        aggregate_result = await _trigger_sync_aggregate_extract_all(
            cartridge=cartridge,
            mode=mode,
            target=target,
            conn_id=conn_id,
            run_id=run_id,
            user=user,
        )
        result = (
            aggregate_result
            if aggregate_result is not None
            else await _call_with_optional_user(
                api_pipeline_extract_all, cartridge, extract_body, user=user
            )
        )
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        triggered = (
            result.get("triggered") if isinstance(result.get("triggered"), list) else []
        )
        if triggered or not _sync_errors_retryable(errors) or attempt == 3:
            break
        await asyncio.sleep(2 * attempt)

    triggered_entities = (
        result.get("triggered") if isinstance(result.get("triggered"), list) else []
    )
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    bronze_status = (
        "running" if triggered_entities else "failed" if errors else "partial"
    )
    steps = _merge_sync_steps(
        steps,
        {
            "connection": {
                "label": "Conexión",
                "status": "success" if triggered_entities or not errors else "partial",
                "detail": "El pipeline aceptó la sincronización."
                if triggered_entities
                else "El pipeline respondió sin entidades disparadas.",
                "attempts": attempts,
            },
            "bronze": {
                "label": "Bronze",
                "status": bronze_status,
                "detail": f"{len(triggered_entities)} entidades disparadas; {len(errors)} errores iniciales.",
                "attempts": attempts,
            },
            "silver_gold": {
                "label": "Silver/Gold",
                "status": "queued" if triggered_entities else "failed",
                "detail": "Airflow encadenará materialización downstream."
                if triggered_entities
                else "No hay extracción base para materializar.",
            },
            "control_room": {
                "label": "Control Room",
                "status": "queued" if triggered_entities else "failed",
                "detail": "Esperando Gold para refrescar señales."
                if triggered_entities
                else "No hay Gold nuevo disponible.",
                "completed": 0,
                "total": 1,
            },
            "agents_intelligence": {
                "label": "Agentes/IA",
                "status": "queued" if triggered_entities else "failed",
                "detail": "Se ejecutarán monitores reales al terminar Control Room."
                if triggered_entities
                else "No hay datos base para ejecutar monitores.",
                "completed": 0,
                "total": 1,
            },
        },
    )
    status = _sync_status_from_steps(steps)
    upsert_debug = await _upsert_sync_run(
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        status=status,
        user=user,
        extra={
            "mode": mode,
            "target": target,
            "conn_id": conn_id,
            "request_id": request_id,
            "steps": steps,
            "triggered_entities": triggered_entities,
            "errors": errors,
            "control_room_ready": False,
            "extract_all_result": result,
            "trigger_strategy": result.get("trigger_strategy") or "fanout",
            "dataset_seed": dataset_seed,
        },
        error_message="; ".join(
            str(item.get("error") or "")
            for item in errors[:3]
            if isinstance(item, dict)
        )
        or None,
    )
    row = await _fetch_sync_run(cartridge=cartridge, run_id=run_id, user=user)
    if not row:
        ctx = build_security_context(user)
        scope_sql, scope_values = await _pipeline_runs_scope_predicate(
            user, 3, refresh_columns=True
        )
        diagnostics = {
            "run_id": run_id,
            "cartridge": cartridge,
            "mode": mode,
            "target": target,
            "conn_id_present": bool(conn_id),
            "scope_predicate_present": bool(scope_sql),
            "scope_value_count": len(scope_values),
            "tenant_id_present": bool(ctx.get("tenant_id")),
            "workspace_id_present": bool(ctx.get("workspace_id")),
            **upsert_debug,
        }
        logger.error("sync_now_run_missing diagnostics=%s", diagnostics)
        raise HTTPException(500, "sync run was not recorded; scope diagnostics logged")
    return await _build_sync_run_status(cartridge=cartridge, row=row, user=user)


@app.get(
    "/api/cartridges/{cartridge_id}/sync-runs/active",
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def api_cartridge_active_sync_run(
    cartridge_id: str,
    mode: str = "incremental",
    target: str = "all",
    conn_id: str | None = None,
    user: dict = Depends(require_permission("pipelines.read")),
):
    user = _runtime_user(user)
    cartridge, _active = await _resolve_scoped_operation_cartridge(
        user, cartridge_id, fallback=cartridge_id
    )
    mode = _sync_clean_mode(mode)
    target = _sync_clean_target(target)
    resolved_conn_id = await _resolve_pipeline_sync_conn_id(cartridge, conn_id, user)
    row = await _fetch_active_sync_run(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=resolved_conn_id,
        user=user,
    )
    if not row:
        return _inactive_sync_run_payload(
            cartridge=cartridge,
            mode=mode,
            target=target,
            conn_id=resolved_conn_id,
        )
    try:
        return await _build_sync_run_status(cartridge=cartridge, row=row, user=user)
    except Exception:
        logger.warning(
            "active sync run status build failed for cartridge=%s run_id=%s; returning persisted payload",
            cartridge,
            row.get("run_id"),
            exc_info=True,
        )
        return _sync_public_payload(row, _sync_extra_from_row(row))


@app.get(
    "/api/cartridges/{cartridge_id}/sync-runs/{run_id}",
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def api_cartridge_sync_run(
    cartridge_id: str,
    run_id: str,
    user: dict = Depends(require_permission("pipelines.read")),
):
    user = _runtime_user(user)
    cartridge, _active = await _resolve_scoped_operation_cartridge(
        user, cartridge_id, fallback=cartridge_id
    )
    row = await _fetch_sync_run(cartridge=cartridge, run_id=run_id, user=user)
    if not row:
        raise HTTPException(404, "sync run not found")
    status = str(row.get("status") or "")
    extra = _sync_extra_from_row(row)
    if status in _SYNC_TERMINAL_STATUSES and not _sync_run_needs_final_reconcile(
        row, extra
    ):
        return _sync_public_payload(row, extra)
    return await _build_sync_run_status(cartridge=cartridge, row=row, user=user)


async def _pipeline_extract_metadata(cartridge: str, entity: str) -> dict:
    pool = await _get_db_pool()
    row = await pool.fetchrow(
        """
        SELECT
            c.id AS cartridge_id,
            c.pattern AS pattern,
            e.entity AS entity,
            e.dag_id AS dag_id,
            e.mode AS mode,
            e.enabled AS enabled,
            e.primary_key AS primary_key,
            e.connection_id AS connection_id
        FROM cartridges c
        LEFT JOIN entity_config e
          ON e.cartridge_id = c.id
         AND e.entity = $2
        WHERE c.id = $1
        """,
        cartridge,
        entity,
    )
    if not row:
        raise HTTPException(404, f"Cartridge '{cartridge}' not found")
    return dict(row)


def _entity_declared_in_static_catalog(cartridge: str, entity: str) -> bool:
    return _entity_declared_in_static_catalog_impl(
        cartridge,
        entity,
        repo_root=Path(__file__).resolve().parents[2],
    )


def _build_dag_extract_conf(
    cartridge: str, entity: str, configured_mode: str | None, body: dict
) -> dict:
    return _build_dag_extract_conf_impl(cartridge, entity, configured_mode, body)


def _normalize_pipeline_conn_id(conn_id: object | None) -> str | None:
    return _normalize_pipeline_conn_id_impl(conn_id)


def _connection_id_from_vault_payload(payload: Any) -> str | None:
    return _connection_id_from_vault_payload_impl(payload)


async def _resolve_pipeline_sync_conn_id(
    cartridge: str,
    requested_conn_id: object | None,
    user: dict | None,
) -> str | None:
    async def _load_vault_payload(
        cartridge_id: str, current_user: dict | None
    ) -> Any:
        async with httpx.AsyncClient(
            headers=_vault_headers_for_user(current_user or {}), timeout=5
        ) as c:
            r = await c.get(
                f"{_VAULT_URL}/connections/{quote(cartridge_id, safe='')}"
            )
        if r.status_code not in (404, 204):
            r.raise_for_status()
            return r.json()
        return None

    async def _load_entity_config_conn_id(cartridge_id: str) -> Any:
        pool = await _get_db_pool()
        row = await pool.fetchrow(
            """
            SELECT connection_id
              FROM entity_config
             WHERE cartridge_id = $1
               AND enabled IS TRUE
               AND NULLIF(BTRIM(COALESCE(connection_id, '')), '') IS NOT NULL
             GROUP BY connection_id
             ORDER BY COUNT(*) DESC, connection_id ASC
            LIMIT 1
            """,
            cartridge_id,
        )
        return row["connection_id"] if row else None

    return await _resolve_pipeline_sync_conn_id_impl(
        cartridge,
        requested_conn_id,
        user,
        vault_payload_loader=_load_vault_payload,
        entity_config_conn_loader=_load_entity_config_conn_id,
        logger_debug=logger.debug,
    )


def _apply_user_scope_to_dag_conf(conf: dict, user: dict | None) -> dict:
    return _apply_user_scope_to_dag_conf_impl(
        conf,
        user,
        security_context_builder=build_security_context,
    )


def _dag_run_id_from_idempotency_key(
    dag_id: str, idempotency_key: object | None
) -> str | None:
    return _dag_run_id_from_idempotency_key_impl(dag_id, idempotency_key)


async def _trigger_airflow_extract_dag(
    dag_id: str,
    conf: dict,
    user: dict | None,
    dag_run_id: str | None = None,
) -> dict:
    async def _airflow_trigger(args: dict, current_user: dict | None) -> dict:
        return await mcp_registry.invoke(
            "infra", "airflow_trigger_dag", args, user=current_user
        )

    return await _trigger_airflow_extract_dag_impl(
        dag_id,
        conf,
        user,
        dag_run_id,
        scoped_conf_builder=_apply_user_scope_to_dag_conf,
        airflow_trigger=_airflow_trigger,
        transient_error_checker=_is_transient_airflow_trigger_error,
        sleep=asyncio.sleep,
    )


def _is_transient_airflow_trigger_error(error: str) -> bool:
    return _is_transient_airflow_trigger_error_impl(error)


# ── Studio — Entity config ───────────────────────────────────────────────────


@app.post(
    "/studio/cartridges/{cartridge_id}/entities/{entity}/rename",
    dependencies=[Depends(require_csrf), Depends(require_permission("studio.write"))],
)
async def studio_rename_entity(
    cartridge_id: str,
    entity: str,
    body: dict,
    _global_admin: dict = Depends(
        require_global_any_role("owner", "super_admin", ROLE_ADMIN)
    ),
    user: dict = Depends(require_permission("studio.write")),
):
    _require_cartridge_visible(user, cartridge_id)
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")
    if new_name == entity:
        return {"renamed": False, "reason": "same name"}
    # Verify old entity exists
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    entities = [
        e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])
    ]
    if entity not in entities:
        raise HTTPException(
            404, f"Entity '{entity}' not found in cartridge '{cartridge_id}'"
        )
    if new_name in entities:
        raise HTTPException(409, f"Entity '{new_name}' already exists")
    await cartridge_service.rename_entity(cartridge_id, entity, new_name)
    return {"renamed": True, "old_name": entity, "new_name": new_name}


@app.patch(
    "/studio/cartridges/{cartridge_id}/entities/{entity}",
    dependencies=[Depends(require_csrf), Depends(require_permission("studio.write"))],
)
async def studio_update_entity(
    cartridge_id: str,
    entity: str,
    body: dict,
    _global_admin: dict = Depends(
        require_global_any_role("owner", "super_admin", ROLE_ADMIN)
    ),
    user: dict = Depends(require_permission("studio.write")),
):
    """Update entity_config fields."""
    _require_cartridge_visible(user, cartridge_id)
    allowed = {
        "display_name",
        "mode",
        "primary_key",
        "dag_id",
        "trigger_type",
        "cron_expression",
        "description",
        "enabled",
        "dag_params",
        "connection_id",
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    await cartridge_service.upsert_entity(cartridge_id, entity, **updates)
    return {"updated": True, "entity": entity, **updates}


# ── Studio — Cartridge management ────────────────────────────────────────────


@app.get("/studio/cartridges", dependencies=[Depends(require_permission("cartridges.read"))])
async def studio_list_cartridges(user: dict = Depends(require_permission("cartridges.read"))):
    cartridges = await cartridge_service.list_cartridges()
    ctx = build_security_context(user)
    allowed = {
        str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()
    }
    if "*" not in allowed:
        cartridges = [
            c
            for c in cartridges
            if str(c.get("id") or c.get("cartridge") or "").strip() in allowed
        ]
    return {"cartridges": cartridges}


# ── Microservice-backed cartridges (probed via internal HTTP) ───────────────
# Maps cartridge_id → internal base URL of the cartridge microservice. When a
# cartridge is in this map, /studio/cartridges/{id}/status probes the service's
# /health endpoint to decide whether to report it as operational, degraded
# (live but missing credentials) or offline (not responding).
_MICROSERVICE_CARTRIDGES = {
    "sap_successfactors": os.environ.get(
        "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203"
    ),
    "sap_hcm": os.environ.get("SAP_HCM_URL", "http://sap-hcm:8202"),
    "sap_s4hana": os.environ.get("SAP_S4HANA_URL", "http://sap-s4hana:8204"),
}


def _internal_headers() -> dict:
    return _internal_cartridge_headers_impl(
        cartridge_api_key=os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE"),
        internal_api_key=INTERNAL_API_KEY,
        is_production=_is_production_env(),
    )


async def _probe_microservice(base_url: str, cartridge_id: str) -> dict:
    """Probe a cartridge microservice and classify its status.

    Returns one of:
      - {"status": "operational",            ...} — /health and credentials OK
      - {"status": "degraded",     "reason": ...} — /health OK, credentials missing
      - {"status": "offline",      "reason": ...} — /health unreachable
    """
    return await _probe_microservice_impl(
        base_url,
        cartridge_id,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_internal_headers,
    )


@app.get(
    "/studio/cartridges/{cartridge_id}/status",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def studio_cartridge_status(
    cartridge_id: str, user: dict = Depends(require_permission("cartridges.read"))
):
    """Lightweight status probe for the cartridge.

    For Replicon (and any cartridge not backed by a dedicated microservice in
    this deployment) we just report ``operational`` if it is registered.
    For SAP cartridges we probe the corresponding FastAPI service.
    """
    _require_cartridge_visible(user, cartridge_id)
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")

    base_url = _MICROSERVICE_CARTRIDGES.get(cartridge_id)
    if not base_url:
        return {"cartridge_id": cartridge_id, "status": "operational"}

    probe = await _probe_microservice(base_url, cartridge_id)
    return {"cartridge_id": cartridge_id, **probe}


@app.post(
    "/studio/cartridges",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("cartridges.write")),
        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    ],
)
async def studio_create_cartridge(body: dict):
    cid = body.get("id", "").strip()
    name = body.get("name", "").strip()
    if not cid or not name:
        raise HTTPException(400, "id and name are required")
    existing = await cartridge_service.get_cartridge(cid)
    if existing:
        raise HTTPException(409, f"Cartridge '{cid}' already exists")
    try:
        manifest = await cartridge_service.create_cartridge(
            cid, name, body.get("description", "")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return manifest


@app.get(
    "/studio/cartridges/{cartridge_id}", dependencies=[Depends(require_permission("cartridges.read"))]
)
async def studio_get_cartridge(
    cartridge_id: str, user: dict = Depends(require_permission("cartridges.read"))
):
    _require_cartridge_visible(user, cartridge_id)
    manifest = await cartridge_service.get_cartridge(cartridge_id)
    if not manifest:
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    return manifest


@app.patch(
    "/studio/cartridges/{cartridge_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("cartridges.write")),
        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    ],
)
async def studio_update_cartridge(
    cartridge_id: str, body: dict, user: dict = Depends(require_permission("cartridges.write"))
):
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    try:
        return await cartridge_service.update_cartridge(cartridge_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post(
    "/studio/cartridges/{cartridge_id}/spec",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("cartridges.write")),
        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    ],
)
async def studio_upload_spec(
    cartridge_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("cartridges.write")),
):
    """Upload a spec file (OpenAPI YAML, WSDL, OData $metadata) for the cartridge."""
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    content = (await file.read()).decode("utf-8", errors="replace")
    try:
        key = cartridge_service.upload_spec(
            cartridge_id, file.filename or "spec.yaml", content
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"uploaded": key, "filename": file.filename, "size": len(content)}


def _require_cartridge_visible(user: dict | None, cartridge_id: str) -> None:
    if user is None:
        return
    ctx = build_security_context(user)
    if _is_security_admin_context(ctx):
        return
    if not _cartridge_visible_for_context(ctx, cartridge_id):
        raise HTTPException(403, "cartridge not allowed")


@app.get("/studio/cartridges/{cartridge_id}/export")
async def studio_export_cartridge(
    cartridge_id: str,
    user: dict = Depends(require_permission("cartridges.read")),
):
    """Download the cartridge as a ZIP archive."""
    _require_cartridge_visible(user, cartridge_id)
    if not await cartridge_service.get_cartridge(cartridge_id):
        raise HTTPException(404, f"Cartridge '{cartridge_id}' not found")
    try:
        zip_bytes = await cartridge_service.export_cartridge(cartridge_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{cartridge_id}.zip"'},
    )


@app.post(
    "/studio/import",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("cartridges.write")),
        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    ],
)
async def studio_import_cartridge(
    file: UploadFile = File(...), user: dict = Depends(require_permission("cartridges.write"))
):
    """Import a cartridge from a previously exported ZIP."""
    zip_bytes = await file.read()
    try:
        manifest = await cartridge_service.import_cartridge(zip_bytes, actor_user=user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return manifest


# ── Studio — AI assistant ─────────────────────────────────────────────────────


@app.post(
    "/studio/chat",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("studio.write")),
        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    ],
)
async def studio_chat(body: dict, user: dict = Depends(require_permission("studio.write"))):
    cartridge_id = body.get("cartridge_id")
    manifest = (
        await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    )
    return await studio_assistant.chat(
        message=body.get("message", ""),
        history=body.get("history", []),
        step=body.get("step", 1),
        manifest=manifest,
        actor_role=user.get("role"),
        actor_user=user,
    )


@app.post(
    "/studio/chat/stream",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("studio.write")),
        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
    ],
)
async def studio_chat_stream(body: dict, user: dict = Depends(require_permission("studio.write"))):
    """SSE-style streaming chat: emits tool_use / tool_result / text / done / error
    events as the assistant runs, so the UI can show a live reasoning trail."""
    cartridge_id = body.get("cartridge_id")
    manifest = (
        await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    )
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    step = body.get("step", 1)
    if not message:
        raise HTTPException(400, "message is required")

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            result = await studio_assistant.chat(
                message=message,
                history=history,
                step=step,
                manifest=manifest,
                on_event=on_event,
                actor_role=user.get("role"),
                actor_user=user,
            )
            await queue.put({"type": "done", **result})
        except Exception:
            _eid = uuid.uuid4().hex
            logger.exception("studio assistant chat failed error_id=%s", _eid)
            await queue.put(
                {"type": "error", "message": f"Internal server error. error_id={_eid}"}
            )

    asyncio.create_task(run())

    async def event_stream():
        yield "event: open\ndata: {}\n\n"
        while True:
            evt = await queue.get()
            etype = evt.get("type", "message")
            yield f"event: {etype}\ndata: {json.dumps(evt, default=str)}\n\n"
            if etype in ("done", "error"):
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/studio",
    dependencies=[Depends(require_permission("studio.read")), Depends(require_admin)],
)
async def studio_page():
    return FileResponse(STATIC / "studio.html")


def _viewer_redirect(
    request: Request, viewer_type: str, **params: str
) -> RedirectResponse:
    return RedirectResponse(
        url=_viewer_redirect_url(request.query_params, viewer_type, params),
        status_code=307,
    )


@app.get("/viewer/pipeline", dependencies=[Depends(require_permission("monitor.read"))])
async def viewer_pipeline(request: Request):
    return _viewer_redirect(request, "pipeline")


@app.get(
    "/viewer/vault",
    dependencies=[Depends(require_permission("vault.connections.read"))],
)
async def viewer_vault(request: Request):
    return _viewer_redirect(request, "vault")


@app.get("/explorer", dependencies=[Depends(require_permission("pipelines.read"))])
async def explorer_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "explorer/index.html")


@app.get("/viewer/lineage", dependencies=[Depends(require_permission("datasets.read"))])
async def viewer_lineage(request: Request):
    return _viewer_redirect(request, "lineage")


@app.get("/rag", dependencies=[Depends(require_admin)])
async def rag_page():
    # MEJORAS moved RAG operation into Studio step 7; keep /rag as a
    # compatibility entrypoint without serving the removed standalone page.
    return RedirectResponse(url="/studio")


# ── Agents — CRUD + invoke ────────────────────────────────────────────────────
# Agent definitions include prompts, tool allowlists and execution traces.
# Platform admins can manage global seed agents; tenant/workspace admins manage
# only agents scoped to their active workspace.


@app.get("/agents", dependencies=[Depends(require_permission("agents.read"))])
async def viewer_agents(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "agents/index.html")


@app.get("/api/agents", dependencies=[Depends(require_permission("agents.read"))])
async def api_agents_list(
    request: Request,
    cartridge_id: str | None = None,
    include_inactive: bool = False,
    user: dict = Depends(require_permission("agents.read")),
):
    agents = await _agents.list_agents(
        cartridge_id, include_inactive, user_context=user
    )
    agents = await _repair_successfactors_talent_monitor_list_if_needed(
        agents,
        user,
        cartridge_id=cartridge_id,
        include_inactive=include_inactive,
    )
    return {
        "agents": agents
    }


@app.get(
    "/api/agents/_tool-catalog",
    dependencies=[Depends(require_permission("agents.read"))],
)
async def api_agents_tool_catalog(request: Request):
    """Aggregate of tools exposed by every MCP server — used by the agent
    editor UI to populate the 'allowed_tools' multi-select."""
    out: dict[str, list] = {}
    async with httpx.AsyncClient(timeout=10) as c:
        for srv_id, base in _agent_runtime.SERVER_URLS.items():
            try:
                server_key = "MCP_INFRA" if srv_id == "mcp-infra" else srv_id.upper()
                r = await c.get(f"{base}/mcp/tools", headers=_hdr_for(server_key))
                r.raise_for_status()
                out[srv_id] = [
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        **tool_manifest.classify_tool(t["name"]),
                    }
                    for t in (r.json().get("tools") or [])
                ]
            except Exception:
                out[srv_id] = []
    return {"servers": out}


@app.get(
    "/api/agents/{agent_id}", dependencies=[Depends(require_permission("agents.read"))]
)
async def api_agents_get(
    request: Request,
    agent_id: str,
    user: dict = Depends(require_permission("agents.read")),
):
    a = await _agents.get_agent(agent_id, user_context=user)
    a = await _repair_successfactors_talent_monitor_row_if_needed(a, user)
    if not a:
        raise HTTPException(404, "agent not found")
    return a


@app.post(
    "/api/agents",
    dependencies=[Depends(require_csrf), Depends(require_permission("agents.write"))],
)
async def api_agents_create(
    request: Request,
    body: dict,
    user: dict = Depends(require_permission("agents.write")),
):
    body = _coerce_successfactors_talent_monitor_payload(body)
    try:
        return await _agents.create_agent(
            body, owner_user_id=user.get("id"), user_context=user
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.patch(
    "/api/agents/{agent_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("agents.write"))],
)
async def api_agents_update(
    request: Request,
    agent_id: str,
    body: dict,
    user: dict = Depends(require_permission("agents.write")),
):
    body = _coerce_successfactors_talent_monitor_payload(body)
    try:
        a = await _agents.update_agent(agent_id, body, user_context=user)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not a:
        raise HTTPException(404, "agent not found")
    return a


@app.delete(
    "/api/agents/{agent_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("agents.write"))],
)
async def api_agents_delete(
    request: Request,
    agent_id: str,
    user: dict = Depends(require_permission("agents.write")),
):
    try:
        ok = await _agents.delete_agent(agent_id, user_context=user)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    if not ok:
        raise HTTPException(404, "agent not found")
    return {"deleted": True}


def _agent_invoke_background_requested(body: dict) -> bool:
    return _agent_invoke_background_requested_impl(body)


def _agent_invoke_background_response(agent: Any) -> dict:
    return _agent_invoke_background_response_impl(agent)


def _start_agent_invoke_background(agent: Any, message: str, history: list, user: dict) -> None:
    agent_id = str(getattr(agent, "id", "") or "")
    history_context = list(history or [])
    user_context = dict(user or {})

    async def runner() -> None:
        try:
            await _agent_runtime.run(
                agent,
                message,
                history=history_context,
                user=user_context,
            )
        except Exception:  # noqa: BLE001
            logger.exception("agent background invoke failed agent_id=%s", agent_id)

    asyncio.create_task(runner())


@app.post(
    "/api/agents/{agent_id}/invoke",
    dependencies=[Depends(require_csrf), Depends(require_permission("agents.execute"))],
)
async def api_agents_invoke(
    request: Request,
    agent_id: str,
    body: dict,
    user: dict = Depends(require_permission("agents.execute")),
):
    visible = await _agents.get_agent(agent_id, user_context=user)
    if not visible:
        raise HTTPException(404, "agent not found")
    agent = await _agent_runtime.load_agent(agent_id, user_context=user)
    if not agent:
        raise HTTPException(404, "agent not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    history = body.get("history") or []
    if _agent_invoke_background_requested(body):
        _start_agent_invoke_background(agent, message, history, user)
        return _agent_invoke_background_response(agent)
    result = await _agent_runtime.run(agent, message, history=history, user=user)
    return result


_AGENT_RUNNER_TOKEN = os.environ.get("AGENT_RUNNER_TOKEN", "")
_AGENT_RUNNER_INTERVAL_MINUTES = int(
    os.environ.get("AGENT_RUNNER_INTERVAL_MINUTES", "5")
)
_AGENT_RUNNER_GRACE_MINUTES = int(os.environ.get("AGENT_RUNNER_GRACE_MINUTES", "1"))


def _parse_agent_scheduled_fire_at(value: Any) -> Any:
    return _parse_agent_scheduled_fire_at_impl(value)


def _agent_schedule_due(schedule: dict, scheduled_fire_at: Any | None = None) -> bool:
    return _agent_schedule_due_impl(
        schedule,
        scheduled_fire_at=scheduled_fire_at,
        interval_minutes=_AGENT_RUNNER_INTERVAL_MINUTES,
        grace_minutes=_AGENT_RUNNER_GRACE_MINUTES,
    )


@app.post(
    "/api/agents/{agent_id}/invoke/scheduled",
    dependencies=[Depends(verify_internal_api_key)],
)
async def api_agents_invoke_scheduled(request: Request, agent_id: str, body: dict):
    """Cron-driven invocation from the airflow `agent_runner` DAG. Uses a
    shared token so it can run without a user session. The agent_runs row
    is logged with user_id=NULL."""
    token = request.headers.get("X-Agent-Runner-Token", "")
    if not _AGENT_RUNNER_TOKEN or not secrets.compare_digest(
        token, _AGENT_RUNNER_TOKEN
    ):
        raise HTTPException(401, "invalid runner token")
    scheduled_scope = {
        "tenant_id": str(body.get("tenant_id") or "").strip() or None,
        "workspace_id": str(body.get("workspace_id") or "").strip() or None,
    }
    scheduled_user_context = scheduled_scope if scheduled_scope.get("workspace_id") else None
    agent = await _agent_runtime.load_agent(agent_id, user_context=scheduled_user_context)
    if not agent:
        raise HTTPException(404, "agent not found")
    agent = await _repair_loaded_successfactors_talent_monitor_if_needed(
        agent,
        scheduled_user_context,
    )
    if not (
        str(getattr(agent, "tenant_id", None) or "").strip()
        and str(getattr(agent, "workspace_id", None) or "").strip()
    ):
        raise HTTPException(403, "scheduled agent requires tenant/workspace scope")
    extra = getattr(agent, "extra", None) or {}
    schedule = extra.get("schedule") if isinstance(extra, dict) else {}
    if not isinstance(schedule, dict) or schedule.get("enabled") is False:
        raise HTTPException(403, "agent schedule is not enabled")
    if not (str(schedule.get("cron") or schedule.get("cron_expression") or "").strip()):
        raise HTTPException(403, "agent schedule cron is required")
    scheduled_fire_at = _parse_agent_scheduled_fire_at(body.get("scheduled_fire_at"))
    if not _agent_schedule_due(schedule, scheduled_fire_at=scheduled_fire_at):
        raise HTTPException(403, "agent schedule is not due")
    if scheduled_fire_at is None:
        from datetime import datetime as _dt, timezone as _tz

        scheduled_fire_at = _dt.now(_tz.utc).replace(second=0, microsecond=0)
    schedule_key = str(body.get("schedule_key") or schedule.get("key") or "default").strip() or "default"
    extra_role = str((extra or {}).get("role") or "").strip().lower()
    monitor_contract = extra.get("monitor") if isinstance(extra, dict) else None
    if extra_role != "monitor" or not isinstance(monitor_contract, dict) or not monitor_contract:
        raise HTTPException(403, "scheduled agents require monitor role and monitor contract")
    airflow_dag_run_id = str(body.get("airflow_dag_run_id") or "").strip() or None
    reservation = await _agent_scheduler.reserve_scheduled_run(
        agent_id=str(agent.id),
        tenant_id=str(getattr(agent, "tenant_id", "")),
        workspace_id=str(getattr(agent, "workspace_id", "")),
        scheduled_fire_at=scheduled_fire_at,
        schedule_key=schedule_key,
        airflow_dag_run_id=airflow_dag_run_id,
        metadata={
            "agent_slug": agent.slug,
            "cartridge_id": agent.cartridge_id,
            "airflow_dag_run_id": airflow_dag_run_id,
        },
    )
    if reservation.get("duplicate"):
        return {
            "reply": "scheduled run already recorded",
            "viewer_urls": [],
            "messages": [],
            "agent_id": agent.id,
            "run_id": reservation.get("agent_run_id"),
            "duplicate": True,
            "schedule_run": reservation,
        }
    message = (body.get("message") or "").strip() or "Ejecuta tu tarea programada."
    try:
        result = await _agent_runtime.run_scheduled_monitor(
            agent,
            message,
            scheduled_fire_at=scheduled_fire_at.isoformat(),
        )
    except Exception as exc:
        await _agent_scheduler.finish_scheduled_run(
            schedule_run_id=reservation.get("id"),
            agent_run_id=None,
            status="error",
            tenant_id=str(getattr(agent, "tenant_id", "")),
            workspace_id=str(getattr(agent, "workspace_id", "")),
            error_message=f"{type(exc).__name__}: {exc}",
            metadata={"airflow_dag_run_id": airflow_dag_run_id},
        )
        raise
    await _agent_scheduler.finish_scheduled_run(
        schedule_run_id=reservation.get("id"),
        agent_run_id=result.get("run_id") if isinstance(result, dict) else None,
        status="ok",
        tenant_id=str(getattr(agent, "tenant_id", "")),
        workspace_id=str(getattr(agent, "workspace_id", "")),
        metadata={
            "airflow_dag_run_id": airflow_dag_run_id,
            "deterministic_monitor": bool(
                isinstance(result, dict) and result.get("deterministic_monitor")
            ),
        },
    )
    if isinstance(result, dict):
        result["schedule_run"] = reservation
    return result


@app.post(
    "/api/agents/{agent_id}/invoke/stream",
    dependencies=[Depends(require_csrf), Depends(require_permission("agents.execute"))],
)
async def api_agents_invoke_stream(
    request: Request,
    agent_id: str,
    body: dict,
    user: dict = Depends(require_permission("agents.execute")),
):
    """Server-Sent Events stream of tool_use / tool_result / text events."""
    visible = await _agents.get_agent(agent_id, user_context=user)
    if not visible:
        raise HTTPException(404, "agent not found")
    agent = await _agent_runtime.load_agent(agent_id, user_context=user)
    if not agent:
        raise HTTPException(404, "agent not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    history = body.get("history") or []

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev: dict):
        await queue.put(ev)

    async def runner():
        try:
            result = await _agent_runtime.run(
                agent, message, history=history, user=user, on_event=on_event
            )
            await queue.put({"type": "done", "run_id": result.get("run_id")})
        except Exception as exc:  # noqa: BLE001
            await queue.put(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())

    async def gen():
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get(
    "/api/agents/{agent_id}/runs",
    dependencies=[Depends(require_permission("agents.read"))],
)
async def api_agents_runs(
    request: Request,
    agent_id: str,
    limit: int = 20,
    user: dict = Depends(require_permission("agents.read")),
):
    return {"runs": await _agents.list_runs(agent_id, limit=limit, user_context=user)}


@app.get(
    "/api/agent-runs/{run_id}",
    dependencies=[Depends(require_permission("agents.read"))],
)
async def api_agent_run_detail(
    request: Request,
    run_id: int,
    user: dict = Depends(require_permission("agents.read")),
):
    run = await _agents.get_run(run_id, user_context=user)
    if not run:
        raise HTTPException(404, "run not found")
    return run


# ── Vault proxy ───────────────────────────────────────────────────────────────

_VAULT_URL = _vault_url()
_RAG_URL = os.environ.get("RAG_URL", "http://mcp-infra:8010")  # migrado


def _tenant_vault_prefix(user: dict) -> str | None:
    return _tenant_vault_prefix_impl(user, is_global_admin=_is_global_iam_admin)


def _vault_headers_for_user(user: dict) -> dict[str, str]:
    return {
        **_hdr_for("VAULT"),
        "x-security-context": json.dumps(
            build_security_context(user), ensure_ascii=False
        ),
    }


def _tenant_vault_conn_id(user: dict, conn_id: str) -> str:
    return _tenant_vault_conn_id_impl(
        user,
        conn_id,
        is_global_admin=_is_global_iam_admin,
    )


def _tenant_vault_display_conn(user: dict, conn: dict) -> dict | None:
    # Tenant isolation contract markers retained for source-based hardening tests:
    # elif key.startswith("tenant_") and "__workspace_" in key:
    #     return None
    # display_key = key
    return _tenant_vault_display_conn_impl(
        user,
        conn,
        is_global_admin=_is_global_iam_admin,
    )


def _tenant_vault_scope(user: dict, scope: str) -> str:
    return _tenant_vault_scope_impl(
        user,
        scope,
        is_global_admin=_is_global_iam_admin,
    )


@app.get(
    "/api/vault/connections/{cartridge}",
    dependencies=[Depends(require_permission("vault.connections.read"))],
)
async def api_vault_list_connections(
    cartridge: str, user: dict = Depends(require_authenticated)
):
    _require_cartridge_visible(user, cartridge)
    try:
        async with httpx.AsyncClient(
            headers=_vault_headers_for_user(user), timeout=5
        ) as c:
            r = await c.get(f"{_VAULT_URL}/connections/{quote(cartridge, safe='')}")
        if r.status_code in (404, 204):
            return {"connections": []}
        if r.status_code >= 500:
            raise HTTPException(502, "Vault request failed")
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Vault request failed") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Vault request failed") from exc
    if not data:
        return {"connections": []}
    connections = data.get("connections") if isinstance(data, dict) else []
    if not isinstance(connections, list):
        return {"connections": []}
    visible: list[dict] = []
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        display = _tenant_vault_display_conn(user, conn)
        if display is not None:
            visible.append(display)
    return {"connections": visible}


@app.get(
    "/api/vault/connections/{cartridge}/{conn_id}/reveal",
    dependencies=[Depends(require_permission("vault.secrets.reveal"))],
)
async def api_vault_reveal_connection(
    cartridge: str, conn_id: str, user: dict = Depends(_internal_or_authenticated)
):
    """Returns full credentials including token (not masked)."""
    _require_cartridge_visible(user, cartridge)
    vault_conn_id = _tenant_vault_conn_id(user, conn_id)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        r = await c.get(
            f"{_VAULT_URL}/connections/{quote(cartridge, safe='')}/{quote(vault_conn_id, safe='')}"
        )
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        data = r.json()
    await _audit.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="vault.connection.reveal",
        resource_type="vault_connection",
        resource_id=f"{cartridge}/{conn_id}",
        status="success",
        metadata={
            "cartridge": cartridge,
            "connection_id": conn_id,
            "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
            "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        },
        critical=True,
    )
    if isinstance(data, dict) and vault_conn_id != conn_id:
        data["conn_id"] = conn_id
        if "id" in data:
            data["id"] = conn_id
    return data


@app.put(
    "/api/vault/connections/{cartridge}/{conn_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("vault.connections.write")),
    ],
)
async def api_vault_upsert_connection(
    cartridge: str,
    conn_id: str,
    body: dict,
    user: dict = Depends(require_authenticated),
):
    _require_cartridge_visible(user, cartridge)
    vault_conn_id = _tenant_vault_conn_id(user, conn_id)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        r = await c.put(
            f"{_VAULT_URL}/connections/{quote(cartridge, safe='')}/{quote(vault_conn_id, safe='')}",
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    await _audit.record_event(
        user.get("id"),
        user.get("email"),
        "vault.connection.upsert",
        "vault_connection",
        f"{cartridge}/{conn_id}",
        status="success",
        metadata={
            "cartridge": cartridge,
            "connection_id": conn_id,
            "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
            "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        },
    )
    if isinstance(data, dict) and vault_conn_id != conn_id:
        data["conn_id"] = conn_id
        if "id" in data:
            data["id"] = conn_id
    return data


@app.delete(
    "/api/vault/connections/{cartridge}/{conn_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("vault.connections.write")),
    ],
)
async def api_vault_delete_connection(
    cartridge: str, conn_id: str, user: dict = Depends(require_authenticated)
):
    _require_cartridge_visible(user, cartridge)
    vault_conn_id = _tenant_vault_conn_id(user, conn_id)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        r = await c.delete(
            f"{_VAULT_URL}/connections/{quote(cartridge, safe='')}/{quote(vault_conn_id, safe='')}"
        )
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and vault_conn_id != conn_id:
        data["conn_id"] = conn_id
        if "id" in data:
            data["id"] = conn_id
    await _audit.record_event(
        user.get("id"),
        user.get("email"),
        "vault.connection.deleted",
        "vault_connection",
        f"{cartridge}/{conn_id}",
        status="success",
        metadata={
            "cartridge": cartridge,
            "connection_id": conn_id,
            "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
            "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        },
    )
    return data


def _require_vault_scope_visible(user: dict, scope: str) -> None:
    return _require_vault_scope_visible_impl(
        user,
        scope,
        is_global_admin=_is_global_iam_admin,
    )


@app.get(
    "/api/vault/secrets/{scope}",
    dependencies=[Depends(require_permission("vault.secrets.read_masked"))],
)
async def api_vault_list_secrets(
    scope: str, user: dict = Depends(require_authenticated)
):
    _require_vault_scope_visible(user, scope)
    vault_scope = _tenant_vault_scope(user, scope)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/secrets/{quote(vault_scope, safe='')}")
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and vault_scope != scope:
        data["scope"] = scope
    return data


@app.get(
    "/api/vault/secrets/{scope}/{key}/reveal",
    dependencies=[Depends(require_permission("vault.secrets.reveal"))],
)
async def api_vault_reveal_secret(
    scope: str, key: str, user: dict = Depends(require_authenticated)
):
    _require_vault_scope_visible(user, scope)
    vault_scope = _tenant_vault_scope(user, scope)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        r = await c.get(
            f"{_VAULT_URL}/secrets/{quote(vault_scope, safe='')}/{quote(key, safe='')}"
        )
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()


@app.put(
    "/api/vault/secrets/{scope}/{key}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("vault.connections.write")),
    ],
)
async def api_vault_upsert_secret(
    scope: str, key: str, body: dict, user: dict = Depends(require_authenticated)
):
    _require_vault_scope_visible(user, scope)
    vault_scope = _tenant_vault_scope(user, scope)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        r = await c.put(
            f"{_VAULT_URL}/secrets/{quote(vault_scope, safe='')}/{quote(key, safe='')}",
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and vault_scope != scope:
        data["scope"] = scope
    await _audit.record_event(
        user.get("id"),
        user.get("email"),
        "vault.secret.upsert",
        "vault_secret",
        f"{scope}/{key}",
        status="success",
        metadata={
            "scope": scope,
            "key": key,
            "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
            "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        },
    )
    return data


@app.delete(
    "/api/vault/secrets/{scope}/{key}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("vault.connections.write")),
    ],
)
async def api_vault_delete_secret(
    scope: str, key: str, user: dict = Depends(require_authenticated)
):
    _require_vault_scope_visible(user, scope)
    vault_scope = _tenant_vault_scope(user, scope)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user), timeout=5) as c:
        r = await c.delete(
            f"{_VAULT_URL}/secrets/{quote(vault_scope, safe='')}/{quote(key, safe='')}"
        )
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and vault_scope != scope:
        data["scope"] = scope
    await _audit.record_event(
        user.get("id"),
        user.get("email"),
        "vault.secret.deleted",
        "vault_secret",
        f"{scope}/{key}",
        status="success",
        metadata={
            "scope": scope,
            "key": key,
            "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
            "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        },
    )
    return data


# ── RAG proxy ─────────────────────────────────────────────────────────────────


def _rag_headers_for_user(user: dict) -> dict[str, str]:
    return {
        **_hdr_for("MCP_INFRA"),
        "x-security-context": json.dumps(
            build_security_context(user), ensure_ascii=False
        ),
    }


@app.get("/api/rag/sources", dependencies=[Depends(require_permission("datasets.read"))])
async def api_rag_sources(kinds: str = "", user: dict = Depends(require_permission("datasets.read"))):
    async with httpx.AsyncClient(headers=_rag_headers_for_user(user), timeout=10) as c:
        params = {"kinds": kinds} if kinds else None
        r = await c.get(f"{_RAG_URL}/rag/sources", params=params)
        r.raise_for_status()
        return r.json()


@app.delete(
    "/api/rag/sources/{source_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_rag_delete_source(
    source_id: int, user: dict = Depends(require_permission("datasets.write"))
):
    async with httpx.AsyncClient(headers=_rag_headers_for_user(user), timeout=10) as c:
        r = await c.delete(f"{_RAG_URL}/rag/sources/{source_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Source not found")
        r.raise_for_status()
        return r.json()


@app.post(
    "/api/rag/search",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))],
)
async def api_rag_search(body: dict, user: dict = Depends(require_permission("datasets.read"))):
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=60) as c:
        r = await c.post(
            f"{_RAG_URL}/mcp/invoke",
            json=_mcp_payload(
                "search_rag",
                {
                    "query": body.get("query"),
                    "top_k": body.get("top_k", 5),
                    "source_ids": body.get("source_ids"),
                    "kinds": body.get("kinds"),
                },
                user,
            ),
        )
        if r.status_code >= 400:
            raise HTTPException(
                r.status_code, _upstream_error_detail(r, "RAG search failed")
            )
        return r.json().get("result") or r.json()


@app.post(
    "/api/rag/reindex",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_rag_reindex(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    kind = str((body or {}).get("kind") or "").strip().lower()
    requested_cartridge = str((body or {}).get("cartridge") or "").strip()
    if requested_cartridge:
        _require_cartridge_visible(user, requested_cartridge)
    if kind == "dataset" and requested_cartridge:
        dataset_name = str((body or {}).get("name") or "").strip()
        datasets_payload = await _refinement_invoke("list_datasets", {}, user=user)
        datasets = datasets_payload.get("datasets") if isinstance(datasets_payload, dict) else []
        match = next(
            (
                ds
                for ds in (datasets or [])
                if str(ds.get("name") or "") == dataset_name
                and str(ds.get("cartridge") or "") == requested_cartridge
            ),
            None,
        )
        if not match:
            raise HTTPException(
                404,
                f"dataset '{dataset_name}' not found for cartridge '{requested_cartridge}'",
            )
    body = {**body, "security_context": build_security_context(user)}
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/reindex", json=body)
        if r.status_code >= 400:
            raise HTTPException(
                r.status_code, _upstream_error_detail(r, "RAG reindex failed")
            )
        return r.json()


@app.post(
    "/api/rag/ingest",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_rag_ingest(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    body = {**body, "security_context": build_security_context(user)}
    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=300) as c:
        r = await c.post(f"{_RAG_URL}/rag/ingest", json=body)
        if r.status_code >= 400:
            raise HTTPException(
                r.status_code, _upstream_error_detail(r, "RAG ingest failed")
            )
        return r.json()


@app.post(
    "/api/rag/ask", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))]
)
async def api_rag_ask(body: dict, user: dict = Depends(require_permission("datasets.read"))):
    """Retrieval-augmented answer: search top-K chunks, synthesize with the chat LLM."""
    from app.services import llm_client as _llm

    search_args = _rag_search_arguments(body)
    query = search_args["query"]
    if not query:
        raise HTTPException(400, "Missing 'query'")

    async with httpx.AsyncClient(headers=_hdr_for("MCP_INFRA"), timeout=60) as c:
        r = await c.post(
            f"{_RAG_URL}/mcp/invoke",
            json=_mcp_payload("search_rag", search_args, user),
        )
        if r.status_code >= 400:
            raise HTTPException(
                r.status_code, _upstream_error_detail(r, "RAG search failed")
            )
        results = (r.json().get("result") or {}).get("results") or []

    if not results:
        return _rag_empty_answer()

    rag_messages = _rag_synthesis_messages(query, results)

    try:
        _llm._ensure_provider_configured("anthropic")
        resp = await _llm._anthropic_client().messages.create(
            model=_llm._resolve_chat_model(None),
            max_tokens=1024,
            system=rag_messages["system"],
            messages=[{"role": "user", "content": rag_messages["user"]}],
        )
        answer = (
            next(
                (b.text for b in resp.content if getattr(b, "type", "") == "text"), ""
            ).strip()
            or "(sin respuesta)"
        )
    except Exception:
        _eid = uuid.uuid4().hex
        logger.exception("LLM synthesis failed error_id=%s", _eid)
        raise HTTPException(500, f"Internal server error. error_id={_eid}")

    return {"answer": answer, "results": results}


@app.get("/api/semantic", dependencies=[Depends(require_permission("datasets.read"))])
async def api_semantic(
    cartridge: str = "", user: dict = Depends(require_permission("datasets.read"))
):
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


@app.post(
    "/api/semantic/enrich",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_semantic_enrich(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("datasets.write")),
):
    cartridge = await _scope_catalog_cartridge_arg(user, body.get("cartridge"))
    if not cartridge:
        raise HTTPException(404, "No active cartridge available for semantic enrichment")
    limit = _semantic_enrichment_limit(body)

    catalog = await _refinement_invoke(
        "get_data_catalog",
        {"cartridge": cartridge},
        timeout=45,
        user=user,
    )
    candidate_payload = _semantic_enrichment_candidates(
        catalog,
        cartridge=cartridge,
        limit=limit,
    )
    entries = candidate_payload["entries"]
    if not entries:
        return _semantic_enrichment_empty_response(
            cartridge=cartridge,
            candidate_payload=candidate_payload,
        )

    result = await _refinement_invoke(
        "upsert_catalog_entries",
        {"entries": entries},
        timeout=45,
        user=user,
    )
    _raise_for_refinement_payload_error(result, "Semantic enrichment failed")
    _scoped_read_cache_invalidate("catalog", user)
    return _semantic_enrichment_success_response(
        cartridge=cartridge,
        candidate_payload=candidate_payload,
        result=result,
    )


# ── Data Catalog API ──────────────────────────────────────────────────────────


@app.get("/api/catalog", dependencies=[Depends(require_permission("datasets.read"))])
async def api_catalog_get(
    layer: str = "",
    cartridge: str = "",
    tags: str = "",
    datasets: str = "",
    user: dict = Depends(require_permission("datasets.read")),
):
    cartridge = await _scope_catalog_cartridge_arg(user, cartridge)
    if not cartridge and _user_allowed_cartridges(user) is not None:
        return _empty_catalog_payload()
    args = _catalog_query_args(
        layer=layer,
        cartridge=cartridge,
        tags=tags,
        datasets=datasets,
    )
    cache_args = _catalog_cache_key(args)

    async def load_catalog() -> Any:
        result = await _refinement_invoke("get_data_catalog", args, user=user)
        _raise_for_refinement_payload_error(result, "Refinement catalog failed")
        return result

    return await _scoped_read_cache_get_or_set(
        "catalog", user, (cache_args,), load_catalog
    )


@app.post(
    "/api/catalog/entries",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_catalog_upsert(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    result = await _refinement_invoke("upsert_catalog_entries", body, user=user)
    _raise_for_refinement_payload_error(result, "Refinement catalog update failed")
    _scoped_read_cache_invalidate("catalog", user)
    return result


@app.post(
    "/api/catalog/relationships",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_catalog_relationship(
    body: dict, user: dict = Depends(require_permission("datasets.write"))
):
    result = await _refinement_invoke("register_relationship", body, user=user)
    _raise_for_refinement_payload_error(result, "Refinement relationship update failed")
    _scoped_read_cache_invalidate("catalog", user)
    return result


async def _refinement_invoke(
    tool: str, args: dict, *, timeout: int = 30, user: dict | None = None
):
    import httpx

    refinement_url = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
    try:
        async with httpx.AsyncClient(
            headers=_hdr_for("REFINEMENT"), timeout=timeout
        ) as client:
            r = await client.post(
                f"{refinement_url}/mcp/invoke",
                json=_mcp_payload(tool, args, user),
            )
            if r.status_code >= 400:
                raise HTTPException(
                    r.status_code,
                    _upstream_error_detail(r, "Refinement request failed"),
                )
            payload = r.json()
            _raise_for_refinement_payload_error(payload, "Refinement request failed")
            return payload
    except httpx.TimeoutException as exc:
        raise HTTPException(503, f"Refinement timed out while running {tool}") from exc
    except httpx.TransportError as exc:
        raise HTTPException(
            503, f"Refinement unavailable while running {tool}: {type(exc).__name__}"
        ) from exc


# ── Monitoring MCP server — MCP-compatible wrapper (used by registry) ─────────


@app.get("/monitoring/mcp/tools")
async def monitoring_mcp_tools(user: dict = Depends(_internal_or_authenticated)):
    """MCP-compatible tools endpoint so the registry can discover monitoring tools.

    Sprint v1.22: added auth. Tool descriptors include parameter
    schemas — an anonymous reader could enumerate the platform's MCP
    surface and target downstream attack research at it."""
    t = await monitoring_tools()
    return t  # already returns {"tools": [...]}


@app.post("/monitoring/mcp/invoke")
async def monitoring_mcp_invoke(
    body: dict, user: dict = Depends(_internal_or_authenticated)
):
    """MCP-compatible invoke endpoint so the assistant can call monitoring tools.

    Sprint v1.21 (F1): added require_authenticated. This route is the
    MCP entry point for the monitoring toolset (view_job, view_schema,
    etc.) and was previously reachable without a session — an
    unauthenticated caller could enumerate jobs and read schema metadata.
    The underlying monitoring_invoke() handler did not check the cookie
    on its own, so the dependency is the single chokepoint.
    """
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "monitor.read")
    return await monitoring_invoke(body, user=user)


# ── Studio-ops MCP server — cartridge & entity management tools ───────────────

STUDIO_OPS_WRITE_TOOLS = {"rename_entity", "delete_entity", "update_entity"}


def _role_name(user: dict) -> str:
    return _studio_ops_role_name_impl(user)


def _require_studio_ops_write_role(user: dict) -> None:
    if not _has_studio_ops_write_role_impl(
        user,
        write_roles={"owner", "super_admin", ROLE_ADMIN},
    ):
        raise HTTPException(403, "global admin role required")


@app.get("/studio_ops/mcp/tools")
async def studio_ops_tools(user: dict = Depends(_internal_or_authenticated)):
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "studio.read")
    tools = [
        {
            "name": "rename_entity",
            "description": (
                "Rename an entity within a cartridge. "
                "Updates entity_config, entity_watermarks, pipeline_runs and silver_lineage atomically. "
                "Bronze files in MinIO keep their original path (historical data). "
                "Use this when the user asks to rename an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {
                        "type": "string",
                        "description": "Cartridge ID, e.g. 'replicon'",
                    },
                    "old_name": {
                        "type": "string",
                        "description": "Current entity name",
                    },
                    "new_name": {"type": "string", "description": "New entity name"},
                },
                "required": ["cartridge_id", "old_name", "new_name"],
            },
        },
        {
            "name": "list_entities",
            "description": (
                "List all entities registered in a cartridge, including their mode, dag_id, "
                "trigger_type, and last pipeline run status."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                },
                "required": ["cartridge_id"],
            },
        },
        {
            "name": "get_entity_logs",
            "description": (
                "Fetch the Airflow task logs for the most recent run of a specific entity. "
                "Use this when a DAG run failed and the user wants to diagnose the error. "
                "Returns the error message from pipeline_runs plus the full Airflow task log."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity": {"type": "string"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "delete_entity",
            "description": (
                "Delete an entity from a cartridge. "
                "Removes it from entity_config and clears its watermarks. "
                "Pipeline run history is preserved for auditing. "
                "Use this when the user explicitly asks to delete or remove an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {
                        "type": "string",
                        "description": "Cartridge ID, e.g. 'replicon'",
                    },
                    "entity": {
                        "type": "string",
                        "description": "Entity name to delete",
                    },
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "update_entity",
            "description": (
                "Update one or more fields of an entity: display_name, mode (full|incremental), "
                "dag_id, trigger_type (manual|scheduled), cron_expression, description, enabled."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity": {"type": "string"},
                    "display_name": {"type": "string"},
                    "mode": {"type": "string", "enum": ["full", "incremental"]},
                    "dag_id": {"type": "string"},
                    "connection_id": {"type": "string"},
                    "trigger_type": {"type": "string", "enum": ["manual", "scheduled"]},
                    "cron_expression": {"type": "string"},
                    "description": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
    ]
    if _role_name(user) == ROLE_ANALYST:
        tools = [tool for tool in tools if tool["name"] not in STUDIO_OPS_WRITE_TOOLS]
    return {"tools": tools}


@app.post("/studio_ops/mcp/invoke", dependencies=[Depends(require_csrf)])
async def studio_ops_invoke(
    body: dict, user: dict = Depends(_internal_or_authenticated)
):
    tool = body.get("tool")
    args = body.get("args", {})
    if not _is_internal_service_actor(user):
        _require_effective_permission(user, "studio.read")
    cartridge_id_arg = str((args or {}).get("cartridge_id") or "").strip()
    if cartridge_id_arg:
        _require_cartridge_visible(user, cartridge_id_arg)

    if tool in STUDIO_OPS_WRITE_TOOLS:
        _require_studio_ops_write_role(user)

    if tool == "delete_entity":
        cartridge_id = args["cartridge_id"]
        entity = args["entity"]
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [
            e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])
        ]
        if entity not in entities:
            return {
                "error": f"Entity '{entity}' not found in cartridge '{cartridge_id}'"
            }
        await cartridge_service.delete_entity(cartridge_id, entity)
        return {
            "deleted": True,
            "cartridge_id": cartridge_id,
            "entity": entity,
            "note": "Pipeline run history preserved. Bronze files in MinIO not removed.",
        }

    if tool == "rename_entity":
        cartridge_id = args["cartridge_id"]
        old_name = args["old_name"]
        new_name = args["new_name"].strip()
        if not new_name or new_name == old_name:
            return {"renamed": False, "reason": "same name or empty"}
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        entities = [
            e.get("entity") or e.get("id") for e in (manifest.get("entities") or [])
        ]
        if old_name not in entities:
            return {"error": f"Entity '{old_name}' not found"}
        if new_name in entities:
            return {"error": f"Entity '{new_name}' already exists"}
        await cartridge_service.rename_entity(cartridge_id, old_name, new_name)
        return {
            "renamed": True,
            "old_name": old_name,
            "new_name": new_name,
            "note": "Bronze files in MinIO remain at the old path — new extractions will use the new name.",
        }

    if tool == "list_entities":
        cartridge_id = args["cartridge_id"]
        manifest = await cartridge_service.get_cartridge(cartridge_id)
        if not manifest:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        runs_map = {}
        try:
            _pool = await _get_db_pool()
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 2)
            async with _pipeline_runs_read_conn(_pool, user) as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT ON (entity) entity, status, started_at, finished_at, record_count, error_message "
                    f"FROM pipeline_runs WHERE cartridge_id=$1 {scope_sql} ORDER BY entity, started_at DESC",
                    cartridge_id,
                    *scope_values,
                )
            for r in rows:
                runs_map[r["entity"]] = {
                    "status": r["status"],
                    "started_at": str(r["started_at"])[:16]
                    if r["started_at"]
                    else None,
                    "finished_at": str(r["finished_at"])[:10]
                    if r["finished_at"]
                    else None,
                    "record_count": r["record_count"],
                    "error": r["error_message"],
                }
        except Exception:
            logger.debug(
                "Could not load pipeline_runs for list_entities %s",
                cartridge_id,
                exc_info=True,
            )
        entities = []
        for e in manifest.get("entities") or []:
            name = e.get("entity") or e.get("id") or ""
            entities.append(
                {
                    "entity": name,
                    "display_name": e.get("display_name"),
                    "mode": e.get("mode", "full"),
                    "dag_id": e.get("dag_id"),
                    "trigger_type": e.get("trigger_type", "manual"),
                    "last_run": runs_map.get(name),
                }
            )
        return {
            "cartridge_id": cartridge_id,
            "entities": entities,
            "count": len(entities),
        }

    if tool == "get_entity_logs":
        cartridge_id = args["cartridge_id"]
        entity = args["entity"]

        # 1. Last pipeline_run for this entity — includes airflow_dag_run_id stored by the DAG
        last_run = None
        try:
            _pool = await _get_db_pool()
            scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 3)
            async with _pipeline_runs_read_conn(_pool, user) as conn:
                row = await conn.fetchrow(
                    "SELECT dag_id, airflow_dag_run_id, status, mode, "
                    "       started_at, finished_at, record_count, error_message, extra "
                    "FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                    f"{scope_sql} ORDER BY started_at DESC LIMIT 1",
                    cartridge_id,
                    entity,
                    *scope_values,
                )
            if row:
                last_run = dict(row)
        except Exception:
            _eid = uuid.uuid4().hex
            logger.exception("get_entity_logs DB query failed error_id=%s", _eid)
            return {"error": f"Internal server error. error_id={_eid}"}

        if not last_run:
            return {"error": f"No pipeline runs found for {cartridge_id}/{entity}"}

        dag_id = last_run.get("dag_id") or f"{cartridge_id}_extract"

        # 2. Resolve Airflow dag_run_id — prefer the stored value, fall back to list+match
        airflow_run_id = last_run.get("airflow_dag_run_id")
        airflow_logs = None
        airflow_log_error = None
        airflow_attempt = _airflow_log_attempt(dag_id, airflow_run_id, [])
        try:
            if not airflow_run_id:
                # Fallback for older runs that predate the airflow_dag_run_id column:
                # match by conf.entity against recent Airflow runs
                runs_r = await mcp_registry.invoke(
                    "infra",
                    "airflow_list_dag_runs",
                    {"dag_id": dag_id, "limit": 20},
                    user=user,
                )
                af_runs = (runs_r or {}).get("runs", [])
                started_str = str(last_run.get("started_at", ""))[:10]
                for run in af_runs:
                    conf_entity = (run.get("conf") or {}).get("entity", "")
                    run_date = (run.get("start_date") or "")[:10]
                    if conf_entity == entity and run_date == started_str:
                        airflow_run_id = run["dag_run_id"]
                        break
                # Last resort: most recent run of that DAG
                if not airflow_run_id and af_runs:
                    airflow_run_id = af_runs[0]["dag_run_id"]

            if airflow_run_id:
                tasks_r = await mcp_registry.invoke(
                    "infra",
                    "airflow_list_task_instances",
                    {"dag_id": dag_id, "dag_run_id": airflow_run_id},
                    user=user,
                )
                if (tasks_r or {}).get("error"):
                    task_ids = _airflow_log_task_ids(dag_id, [])
                    airflow_attempt = _airflow_log_attempt(
                        dag_id, airflow_run_id, task_ids
                    )
                    airflow_log_error = (
                        f"Could not list Airflow tasks for dag_id={dag_id} "
                        f"dag_run_id={airflow_run_id}: {tasks_r['error']}"
                    )
                else:
                    tasks = (tasks_r or {}).get("tasks") or []
                    task_ids = _airflow_log_task_ids(dag_id, tasks)
                    airflow_attempt = _airflow_log_attempt(
                        dag_id, airflow_run_id, task_ids, tasks
                    )
                    if not task_ids:
                        airflow_log_error = (
                            f"No Airflow log task found for dag_id={dag_id} dag_run_id={airflow_run_id}; "
                            f"available_task_ids={airflow_attempt['available_task_ids']}"
                        )
                    for task_id in task_ids:
                        logs_r = await mcp_registry.invoke(
                            "infra",
                            "airflow_get_task_logs",
                            {
                                "dag_id": dag_id,
                                "dag_run_id": airflow_run_id,
                                "task_id": task_id,
                            },
                            user=user,
                        )
                        if (logs_r or {}).get("logs"):
                            airflow_logs = logs_r.get("logs", "")
                            break
                    if task_ids and not airflow_logs and not airflow_log_error:
                        airflow_log_error = (
                            f"No Airflow logs found for dag_id={dag_id} "
                            f"dag_run_id={airflow_run_id} task_ids={task_ids}"
                        )
            else:
                airflow_log_error = f"No Airflow dag_run_id found for dag_id={dag_id}"
        except Exception:
            logger.debug(
                "Airflow log fetch failed for %s/%s",
                cartridge_id,
                entity,
                exc_info=True,
            )
            airflow_log_error = f"Airflow log fetch failed for dag_id={dag_id} dag_run_id={airflow_run_id}"

        return {
            "entity": entity,
            "cartridge_id": cartridge_id,
            "dag_id": dag_id,
            "dag_run_id": airflow_run_id,
            "status": last_run["status"],
            "started_at": str(last_run.get("started_at", ""))[:19],
            "error_message": last_run.get("error_message"),
            "airflow_logs": airflow_logs or "",
            "airflow_log_error": airflow_log_error,
            "attempted": airflow_attempt,
        }

    if tool == "update_entity":
        cartridge_id = args.pop("cartridge_id")
        entity = args.pop("entity")
        if not args:
            return {"error": "No fields to update"}
        await cartridge_service.upsert_entity(cartridge_id, entity, **args)
        return {"updated": True, "entity": entity, **args}

    raise HTTPException(400, f"Unknown tool: {tool}")


# ── Monitoring MCP server (deeplinks para el asistente) ───────────────────────

CONSOLE_URL = _public_url("CONSOLE_URL", development_default="http://localhost:8000")


@app.get(
    "/monitoring/tools", dependencies=[Depends(require_permission("monitor.read"))]
)
async def monitoring_tools(user: dict = Depends(require_permission("monitor.read"))):
    # Sprint v1.22: same rationale as /monitoring/mcp/tools — tool
    # discovery should be authenticated.
    return {
        "tools": [
            {
                "name": "view_job",
                "description": (
                    "Genera un deeplink para visualizar el detalle de un job: "
                    "status, progreso, logs linea a linea por entidad. "
                    "Retorna una URL que el usuario puede abrir directamente."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "ID del job"},
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "view_jobs",
                "description": "Genera un deeplink para ver todos los jobs recientes con su estado.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "view_schema",
                "description": (
                    "Genera un deeplink para visualizar el schema de una fuente Bronze: "
                    "columnas, tipos, particiones disponibles y preview de filas."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Ruta de la fuente, e.g. 'raw/replicon/TimeEntry'",
                        },
                    },
                    "required": ["source"],
                },
            },
            {
                "name": "view_dataset",
                "description": (
                    "Genera un deeplink para visualizar un dataset Silver/Gold: "
                    "SQL, column mapping (terminos de negocio), lineage e historial y preview."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nombre del dataset"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "view_datasets",
                "description": "Genera un deeplink para ver todos los datasets Silver/Gold definidos.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "view_semantic",
                "description": (
                    "Genera un deeplink para visualizar el modelo semantico de un cartucho: "
                    "entidades, campos, modos de extraccion, watermarks y relaciones."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "cartridge": {"type": "string", "default": "replicon"},
                    },
                },
            },
            {
                "name": "view_pipeline",
                "description": (
                    "Genera un deeplink para el Pipeline Monitor DAG: vista completa del flujo "
                    "Entidad → Bronze → Silver → Gold con estado de frescura y botones de extracción. "
                    "Úsalo cuando el usuario pregunte por el estado del pipeline, quiera ver qué "
                    "está desactualizado, o quiera extraer/refrescar datos."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
    }


@app.post(
    "/monitoring/invoke",
    dependencies=[Depends(require_csrf), Depends(require_permission("monitor.read"))],
)
async def monitoring_invoke(body: dict, user: dict = Depends(require_permission("monitor.read"))):
    # Sprint v1.22: was reachable without any auth. monitoring tools
    # read job state and DAG metadata, which a session-less caller has
    # no business seeing. CSRF added because this is a state-shaped
    # POST and could be called from a cross-origin form otherwise.
    tool = body.get("tool")
    args = body.get("args") or {}
    if not isinstance(args, dict):
        raise HTTPException(400, "args must be an object")
    _require_effective_permission(user, "monitor.read")

    if tool == "view_job":
        job_id = args.get("job_id")
        if not job_id:
            raise HTTPException(400, "job_id is required")
        job = await job_service.get_scoped(job_id, user=user)
        entity = (job.get("args") or {}).get("entity", "")
        return {
            "url": f"{CONSOLE_URL}/viewer?type=job&id={quote(str(job_id), safe='')}",
            "label": f"Ver job {job_id}" + (f" — {entity}" if entity else ""),
            "status": job.get("status", "unknown"),
            "message": job.get("message", ""),
        }

    if tool == "view_jobs":
        return {
            "url": f"{CONSOLE_URL}/viewer?type=jobs",
            "label": "Ver todos los jobs",
        }

    if tool == "view_schema":
        source = args.get("source")
        if not source:
            raise HTTPException(400, "source is required")
        return {
            "url": f"{CONSOLE_URL}/viewer?type=schema&source={quote(str(source), safe='')}",
            "label": f"Ver schema de {source}",
        }

    if tool == "view_dataset":
        name = args.get("name")
        if not name:
            raise HTTPException(400, "name is required")
        return {
            "url": f"{CONSOLE_URL}/viewer?type=dataset&name={quote(str(name), safe='')}",
            "label": f"Ver dataset {name}",
        }

    if tool == "view_datasets":
        return {
            "url": f"{CONSOLE_URL}/viewer?type=datasets",
            "label": "Ver todos los datasets",
        }

    if tool == "view_semantic":
        cartridge = args.get("cartridge", "replicon")
        return {
            "url": f"{CONSOLE_URL}/viewer?type=semantic&cartridge={quote(str(cartridge), safe='')}",
            "label": f"Ver modelo semantico de {cartridge}",
        }

    if tool == "view_pipeline":
        return {
            "url": f"{CONSOLE_URL}/viewer?type=pipeline",
            "label": "Pipeline Monitor — Bronze → Silver → Gold",
        }

    raise HTTPException(400, f"Unknown tool: {tool}")


# ── DAG graph parser ──────────────────────────────────────────────────────────


@app.post(
    "/api/dags/parse",
    dependencies=[Depends(require_csrf), Depends(require_permission("studio.read"))],
)
async def api_dag_parse(body: dict, user: dict = Depends(require_permission("studio.read"))):
    # Sprint v1.22: parsing arbitrary Python source is non-trivial work
    # and an anonymous caller could DOS the parser. Auth + CSRF required.
    source = body.get("source", "")
    if not source:
        raise HTTPException(400, "source is required")
    return _parse_dag_graph(source)


# ── Decision Manager ─────────────────────────────────────────────────────────

import json as _json_dec
import asyncpg as _asyncpg_dec

_DEC_POOL: _asyncpg_dec.Pool | None = None


def _coerce_date(v):
    return _coerce_date_impl(v)


def _coerce_dt(v):
    return _coerce_dt_impl(v)


async def _dec_pool() -> _asyncpg_dec.Pool:
    global _DEC_POOL
    if _DEC_POOL is None:

        async def _init_conn(c):
            await c.set_type_codec(
                "jsonb",
                encoder=_json_dec.dumps,
                decoder=_json_dec.loads,
                schema="pg_catalog",
            )

        dsn = os.environ.get("DATABASE_URL", "").replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        _DEC_POOL = await _asyncpg_dec.create_pool(
            dsn,
            min_size=1,
            max_size=4,
            init=_init_conn,
            command_timeout=10,
        )
    return _DEC_POOL


async def _close_dec_pool() -> None:
    global _DEC_POOL
    if _DEC_POOL is not None:
        await _DEC_POOL.close()
        _DEC_POOL = None


def _dec_row_to_dict(row) -> dict:
    return _dec_row_to_dict_impl(row)


@app.get("/decisions", dependencies=[Depends(require_admin)])
async def viewer_decisions(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "decisions/index.html")


def _current_workspace_id(user: dict) -> str | None:
    """Return the active workspace UUID for ``user``, or ``None`` if the
    middleware never assigned one. Matches the workspace service's
    helper so console and workspace agree on which workspace owns a
    decision row.
    """
    return _current_workspace_id_impl(user)


def _dec_visible_clause(
    uid: int, is_admin: bool, params: list, workspace_id: str | None = None
) -> str:
    """Returns a SQL clause that filters decisions visible to this user.

    Sprint v1.37 (audit B7 P0): now also scopes to ``workspace_id``.
    Pre-v1.37 the clause filtered only by ``visibility`` /
    ``created_by_id`` / ``assignee_id``, so a user with membership in
    multiple workspaces saw every ``visibility='shared'`` decision in
    every workspace they had ever joined (even when their active
    session was scoped to a single one). Combined with the v1.32
    migration adding ``workspace_id`` to ``decisions``, the column
    exists; this is the code path catching up.

    Admins are still global by design, but only inside the active
    workspace — they don't get to read tenant A's decisions while their
    active session is on tenant B. If ``workspace_id`` is ``None``
    (user has no active workspace), the clause is ``FALSE`` so the
    query returns nothing rather than every row.
    """
    if not workspace_id:
        return "FALSE"
    params.append(workspace_id)
    ws_param = f"${len(params)}"
    workspace_clause = f"workspace_id = {ws_param}"
    if is_admin:
        return workspace_clause
    params.append(uid)
    p = f"${len(params)}"
    return f"({workspace_clause} AND (created_by_id = {p} OR assignee_id = {p}))"


def _dec_is_workspace_admin(user: dict) -> bool:
    return _dec_is_workspace_admin_impl(
        is_global_admin=_is_global_iam_admin(user),
        workspace_role=_workspace_role(user),
    )


async def _dec_load_with_visibility(decision_id: int, user: dict) -> dict | None:
    # Sprint v1.37: no active workspace -> no decisions are visible.
    # Short-circuit BEFORE opening the pool so an unauthorized caller
    # never touches the DB. Pre-v1.37 the lookup proceeded with only
    # the visibility/owner filters, so a logged-in user with zero
    # memberships could still see ``visibility='shared'`` rows from
    # any tenant.
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        return None
    is_admin = _dec_is_workspace_admin(user)
    params: list = [decision_id, workspace_id]
    sql = "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2"
    if not is_admin:
        params.append(user["id"])
        sql += f" AND (created_by_id = ${len(params)} OR assignee_id = ${len(params)})"
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        row = await conn.fetchrow(sql, *params)
    return dict(row) if row else None


def _dec_can_edit(row: dict, user: dict) -> bool:
    return _dec_can_edit_impl(
        row,
        user,
        is_workspace_admin=_dec_is_workspace_admin(user),
    )


def _dec_can_delete(row: dict, user: dict) -> bool:
    return _dec_can_delete_impl(
        row,
        user,
        is_workspace_admin=_dec_is_workspace_admin(user),
    )


@app.get("/api/decisions", dependencies=[Depends(require_permission("datasets.read"))])
async def api_decisions_list(
    status: str = "", overdue: str = "", user: dict = Depends(require_permission("datasets.read"))
):
    where, params = [], []
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        return {"decisions": []}
    where.append(
        _dec_visible_clause(
            user["id"], _dec_is_workspace_admin(user), params, workspace_id
        )
    )
    if status in ("open", "closed"):
        params.append(status)
        where.append(f"status = ${len(params)}")
    if overdue.lower() == "true":
        where.append(
            "status = 'open' AND commitment_date IS NOT NULL AND commitment_date < CURRENT_DATE"
        )
    sql = "SELECT * FROM decisions WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT 500"
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        rows = await conn.fetch(sql, *params)
    return {"decisions": [_dec_row_to_dict(r) for r in rows]}


@app.post(
    "/api/decisions",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def api_decisions_create(body: dict, user: dict = Depends(require_permission("control_room.write"))):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    # Sprint v1.37 (audit B7 P0): every decision belongs to the user's
    # active workspace. Without this, console.POST /api/decisions
    # silently created rows with workspace_id=NULL and the list
    # endpoint then leaked them as "shared" across tenants on the
    # legacy fallback in 33_decisions_workspace_id.sql.
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(400, "active workspace is required to create a decision")
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        row = await conn.fetchrow(
            """INSERT INTO decisions
                  (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
               RETURNING *""",
            title,
            body.get("description") or "",
            _coerce_date(body.get("commitment_date")),
            _json_dec.dumps(body.get("kpis") or []),
            user["id"],
            body.get("assignee_id"),
            body.get("visibility")
            if body.get("visibility") in ("private", "shared")
            else "private",
            workspace_id,
        )
    return _dec_row_to_dict(row)


@app.get("/api/decisions/{decision_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def api_decisions_get(
    decision_id: int, user: dict = Depends(require_permission("datasets.read"))
):
    row = await _dec_load_with_visibility(decision_id, user)
    if not row:
        raise HTTPException(404, f"Decision {decision_id} not found")
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        actions = await conn.fetch(
            "SELECT * FROM decision_actions WHERE decision_id = $1 ORDER BY ts DESC",
            decision_id,
        )
    out = _dec_row_to_dict(row)
    out["actions"] = [
        {**dict(a), "ts": a["ts"].isoformat() if a["ts"] else None} for a in actions
    ]
    return out


@app.patch(
    "/api/decisions/{decision_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def api_decisions_update(
    decision_id: int, body: dict, user: dict = Depends(require_permission("control_room.write"))
):
    """Patch any subset of: title, description, commitment_date, kpis, status, outcome,
    closed_at, follow_up_decision_id, assignee_id, visibility."""
    existing = await _dec_load_with_visibility(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_edit(existing, user):
        raise HTTPException(
            403, "you can only edit decisions you created or are assigned to"
        )

    allowed = {
        "title",
        "description",
        "commitment_date",
        "kpis",
        "status",
        "outcome",
        "closed_at",
        "follow_up_decision_id",
        "assignee_id",
        "visibility",
    }
    sets, params = [], []
    for k, v in body.items():
        if k not in allowed:
            continue
        if k == "kpis":
            params.append(_json_dec.dumps(v))
            sets.append(f"{k} = ${len(params)}::jsonb")
            continue
        if k == "commitment_date":
            v = _coerce_date(v)
        elif k == "closed_at":
            v = _coerce_dt(v)
        elif k == "visibility" and v not in ("private", "shared"):
            continue
        params.append(v)
        sets.append(f"{k} = ${len(params)}")
    if not sets:
        raise HTTPException(400, "no updatable fields supplied")
    if body.get("status") == "closed" and "closed_at" not in body:
        sets.append("closed_at = COALESCE(closed_at, NOW())")
    # Sprint v1.37: pin UPDATE to (id, workspace_id) — defense-in-depth
    # against a future code path that loads ``existing`` from a
    # different source. ``existing`` already came from
    # ``_dec_load_with_visibility`` which itself filters by workspace,
    # so ``existing["workspace_id"]`` is the active workspace by
    # construction.
    params.append(decision_id)
    decision_ref = f"${len(params)}"
    params.append(existing["workspace_id"])
    workspace_ref = f"${len(params)}"
    sql = (
        f"UPDATE decisions SET {', '.join(sets)} "
        f"WHERE id = {decision_ref} AND workspace_id = {workspace_ref} RETURNING *"
    )
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        row = await conn.fetchrow(sql, *params)
    if not row:
        # The visibility check passed but the row vanished between
        # SELECT and UPDATE (e.g. a concurrent delete, or the row was
        # moved to a different workspace). Treat as not-found rather
        # than 500.
        raise HTTPException(404, f"Decision {decision_id} not found")
    return _dec_row_to_dict(row)


@app.delete(
    "/api/decisions/{decision_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def api_decisions_delete(
    decision_id: int, user: dict = Depends(require_permission("control_room.write"))
):
    existing = await _dec_load_with_visibility(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_delete(existing, user):
        raise HTTPException(403, "only the creator or an admin can delete a decision")
    pool = await _dec_pool()
    # Sprint v1.37: pin DELETE to (id, workspace_id) — same rationale
    # as the UPDATE above. ``existing["workspace_id"]`` came from
    # ``_dec_load_with_visibility`` which is already workspace-scoped.
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        await conn.execute(
            "DELETE FROM decisions WHERE id = $1 AND workspace_id = $2",
            decision_id,
            existing["workspace_id"],
        )
    return {"deleted": True, "id": decision_id}


@app.post(
    "/api/decisions/{decision_id}/actions",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def api_decisions_add_action(
    decision_id: int, body: dict, user: dict = Depends(require_permission("control_room.write"))
):
    existing = await _dec_load_with_visibility(decision_id, user)
    if not existing:
        raise HTTPException(404, f"Decision {decision_id} not found")
    if not _dec_can_edit(existing, user):
        raise HTTPException(403, "only creator/assignee/admin can add to bitácora")
    action_text = (body.get("action_text") or "").strip()
    if not action_text:
        raise HTTPException(400, "action_text is required")
    pool = await _dec_pool()
    actor = user.get("email") or "user"
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        row = await conn.fetchrow(
            """INSERT INTO decision_actions (decision_id, action_text, note, actor)
               VALUES ($1, $2, $3, $4)
               RETURNING *""",
            decision_id,
            action_text,
            body.get("note"),
            actor,
        )
    return {**dict(row), "ts": row["ts"].isoformat() if row["ts"] else None}


# ── Users (assignee picker, all logged-in users) ────────────────────────────

_GLOBAL_ASSIGNABLE_ROLES = {
    *_IAM_GLOBAL_ASSIGNABLE_ROLES,
    ROLE_ADMIN,
}
_WORKSPACE_ASSIGNABLE_ROLES = set(_IAM_WORKSPACE_ASSIGNABLE_ROLES)


def _assignable_role(value: str | None, actor_user: dict | None = None) -> str:
    return _assignable_role_impl(
        value,
        actor_user,
        role_definitions=ROLE_DEFINITIONS,
        role_admin=ROLE_ADMIN,
        global_assignable_roles=_GLOBAL_ASSIGNABLE_ROLES,
        workspace_assignable_roles=_WORKSPACE_ASSIGNABLE_ROLES,
    )


@app.get("/api/users")
async def api_users_list(user: dict = Depends(require_permission("iam.users.read"))):
    users = await _auth.list_users(active_only=True)
    if _is_global_iam_admin(user):
        return {"users": users}
    visible_ids = await _visible_user_ids_for_admin(user, users)
    return {"users": [u for u in users if u.get("id") in visible_ids]}


# ── Admin user management ───────────────────────────────────────────────────


@app.get("/admin/users", dependencies=[Depends(require_permission("iam.users.read"))])
async def viewer_admin_users(
    request: Request, user: dict = Depends(require_permission("iam.users.read"))
):
    # Compatibility URL, but not a separate users app anymore:
    # /admin/users now enters the IAM ecosystem and opens the Users tab.
    return RedirectResponse(url="/operations/users", status_code=307)


def _is_global_iam_admin(user: dict | None) -> bool:
    return _is_global_iam_admin_impl(user, role_admin=ROLE_ADMIN)


def _session_workspace_ids(user: dict | None) -> set[str]:
    return _session_workspace_ids_impl(user)


def _workspace_scope_db_unavailable(exc: BaseException) -> bool:
    return _workspace_scope_db_unavailable_impl(exc)


async def _workspace_rows_from_auth_stub(user_id: int) -> list[dict]:
    """Compatibility path for unit-test auth doubles without DATABASE_URL."""
    pool_factory = getattr(_auth, "pool", None)
    if not callable(pool_factory):
        return []
    pool = await pool_factory()
    rows = await pool.fetch(
        "SELECT workspace_id::text AS workspace_id FROM user_workspace_roles WHERE user_id = $1",
        user_id,
    )
    return [dict(row) for row in rows]


async def _target_user_workspace_ids(user_id: int) -> set[str]:
    try:
        pool = await _get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not _workspace_scope_db_unavailable(exc):
            raise
        rows = await _workspace_rows_from_auth_stub(user_id)
        return {str(row["workspace_id"]) for row in rows if row.get("workspace_id")}
    rows = await pool.fetch(
        "SELECT workspace_id::text AS workspace_id FROM user_workspace_roles WHERE user_id = $1",
        user_id,
    )
    return {str(row["workspace_id"]) for row in rows if row["workspace_id"]}


async def _workspace_summaries_for_users(user_ids: list[int]) -> dict[int, list[dict]]:
    if not user_ids:
        return {}
    try:
        pool = await _get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not _workspace_scope_db_unavailable(exc):
            raise
        return {}
    rows = await pool.fetch(
        """
        SELECT uwr.user_id::int AS user_id,
               w.id::text AS workspace_id,
               w.name AS workspace_name,
               t.id::text AS tenant_id,
               t.name AS tenant_name,
               r.name AS workspace_role
          FROM user_workspace_roles uwr
          JOIN workspaces w ON w.id = uwr.workspace_id
          JOIN tenants t ON t.id = w.tenant_id
          JOIN roles r ON r.id = uwr.role_id
         WHERE uwr.user_id = ANY($1::int[])
         ORDER BY w.created_at ASC, w.name ASC, r.name ASC
        """,
        user_ids,
    )
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(int(row["user_id"]), []).append(
            {
                "workspace_id": row["workspace_id"],
                "workspace_name": row["workspace_name"],
                "tenant_id": row["tenant_id"],
                "tenant_name": row["tenant_name"],
                "workspace_role": row["workspace_role"],
            }
        )
    return result


async def _attach_workspace_summaries(users: list[dict]) -> list[dict]:
    ids = [int(u["id"]) for u in users if u.get("id") is not None]
    summaries = await _workspace_summaries_for_users(ids)
    enriched: list[dict] = []
    for user in users:
        item = dict(user)
        if user.get("id") is not None:
            item["workspaces"] = summaries.get(int(user["id"]), [])
        else:
            item["workspaces"] = []
        enriched.append(item)
    return enriched


async def _visible_user_ids_for_admin(admin_user: dict, users: list[dict]) -> set[int]:
    if _is_global_iam_admin(admin_user):
        return {int(u["id"]) for u in users if u.get("id") is not None}
    workspace_ids = sorted(_session_workspace_ids(admin_user))
    if not workspace_ids:
        return set()
    user_by_id = {int(u["id"]): u for u in users if u.get("id") is not None}
    try:
        pool = await _get_db_pool()
    except (RuntimeError, AttributeError) as exc:
        if not _workspace_scope_db_unavailable(exc):
            raise
        visible: set[int] = set()
        admin_id = admin_user.get("id")
        for candidate in users:
            candidate_id = candidate.get("id")
            if candidate_id != admin_id and _is_global_iam_admin(candidate):
                continue
            candidate_workspaces = _session_workspace_ids(candidate)
            if candidate_id == admin_id or candidate_workspaces.intersection(
                workspace_ids
            ):
                visible.add(int(candidate_id))
        return visible
    rows = await pool.fetch(
        """
        SELECT DISTINCT user_id
          FROM user_workspace_roles
         WHERE workspace_id = ANY($1::uuid[])
        """,
        workspace_ids,
    )
    visible: set[int] = set()
    for row in rows:
        user_id = int(row["user_id"])
        if user_id == admin_user.get("id"):
            visible.add(user_id)
            continue
        candidate = user_by_id.get(user_id)
        if candidate and _is_global_iam_admin(candidate):
            continue
        visible.add(user_id)
    return visible


async def _set_workspace_role_for_user(
    user_id: int, workspace_id: str, role: str
) -> None:
    pool = await _get_db_pool()
    role_id = await pool.fetchval("SELECT id FROM roles WHERE name = $1", role)
    if not role_id:
        raise HTTPException(400, "invalid workspace role")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM user_workspace_roles
                 WHERE user_id = $1
                   AND workspace_id = $2::uuid
                """,
                user_id,
                workspace_id,
            )
            await conn.execute(
                """
                INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
                VALUES ($1, $2::uuid, $3)
                """,
                user_id,
                workspace_id,
                role_id,
            )


async def _assert_can_use_workspace(admin_user: dict, workspace_id: str | None) -> None:
    if _is_global_iam_admin(admin_user):
        return
    memberships = _session_workspace_ids(admin_user)
    if not workspace_id or workspace_id not in memberships:
        raise HTTPException(403, "workspace access forbidden")


async def _assert_can_manage_target_user(admin_user: dict, target_user_id: int) -> None:
    if _is_global_iam_admin(admin_user):
        return
    memberships = _session_workspace_ids(admin_user)
    if not memberships:
        raise HTTPException(403, "workspace access forbidden")
    target = await _auth.get_user_by_id(target_user_id)
    if _is_global_iam_admin(target):
        raise HTTPException(403, "workspace admins cannot manage platform admins")
    target_workspaces = await _target_user_workspace_ids(target_user_id)
    if not target_workspaces or memberships.isdisjoint(target_workspaces):
        raise HTTPException(403, "workspace access forbidden")


@app.get("/api/admin/users")
async def api_admin_users_list(
    admin_user: dict = Depends(require_permission("iam.users.read")),
):
    users = await _auth.list_users(active_only=False)
    if _is_global_iam_admin(admin_user):
        return {"users": await _attach_workspace_summaries(users)}
    visible_ids = await _visible_user_ids_for_admin(admin_user, users)
    scoped_users = [u for u in users if u.get("id") in visible_ids]
    return {"users": await _attach_workspace_summaries(scoped_users)}


@app.post(
    "/api/admin/users",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_create(
    body: dict,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    email_raw = body.get("email")
    pw = body.get("password") or ""
    if not email_raw or not pw:
        raise HTTPException(400, "email and password are required")
    email = _normalize_email_or_400(email_raw)
    pw = _validate_password_or_400(pw)
    if await _auth.get_user_by_email(email):
        raise HTTPException(409, f"user with email {email} already exists")
    requested_role = _assignable_role(body.get("role"), admin_user)
    platform_role = requested_role if _is_global_iam_admin(admin_user) else "user"
    workspace_role = requested_role if not _is_global_iam_admin(admin_user) else None
    workspace_id = (
        body.get("workspace_id") or admin_user.get("active_workspace_id") or ""
    ).strip() or None
    if not workspace_id:
        raise HTTPException(400, "workspace_id is required")
    await _assert_can_use_workspace(admin_user, workspace_id)
    try:
        create_user_kwargs = {
            "email": email,
            "password": pw,
            "name": body.get("name"),
            "role": platform_role,
        }
        # Test doubles from older auth contracts may not expose workspace_id;
        # production auth.create_user does and assigns the membership in the
        # same transaction after the route has validated workspace scope.
        if "workspace_id" in inspect.signature(_auth.create_user).parameters:
            create_user_kwargs["workspace_id"] = workspace_id
        target_user = await _auth.create_user(**create_user_kwargs)
        if workspace_role and target_user.get("id"):
            try:
                await _set_workspace_role_for_user(
                    int(target_user["id"]), workspace_id, workspace_role
                )
            except (RuntimeError, AttributeError) as exc:
                if not _workspace_scope_db_unavailable(exc):
                    raise
    except RuntimeError:
        # create_user assigns workspace membership in the same transaction;
        # let the global 500 handler log + sanitize internal details.
        raise
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.created",
        "user",
        str(target_user["id"]),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "role": platform_role,
            "workspace_role": workspace_role,
            "workspace_id": workspace_id,
        },
    )
    return target_user


@app.patch(
    "/api/admin/users/{user_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_update(
    user_id: int,
    body: dict,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    # Don't let an admin demote / disable themselves accidentally
    if user_id == admin_user["id"] and (
        body.get("role") not in (None, admin_user.get("role"))
        or body.get("is_active") is False
    ):
        raise HTTPException(400, "you cannot demote or disable your own account")
    await _assert_can_manage_target_user(admin_user, user_id)
    before = await _auth.get_user_by_id(user_id)
    password = None
    if "password" in body:
        password = _validate_password_or_400(body.get("password"))
    password_changed = password is not None
    role_update = body.get("role")
    workspace_role = None
    platform_role_update = None
    if role_update:
        requested_role = _assignable_role(role_update, admin_user)
        if _is_global_iam_admin(admin_user):
            platform_role_update = requested_role
        else:
            workspace_role = requested_role
    target_user = await _auth.update_user(
        user_id,
        name=body.get("name"),
        role=platform_role_update,
        is_active=body.get("is_active"),
        password=password,
        escalation_notify=body.get("escalation_notify")
        if "escalation_notify" in body
        else None,
    )
    if not target_user:
        raise HTTPException(404, "user not found")
    if workspace_role:
        workspace_id = str(admin_user.get("active_workspace_id") or "")
        if not workspace_id:
            raise HTTPException(400, "active workspace is required")
        try:
            await _set_workspace_role_for_user(user_id, workspace_id, workspace_role)
        except (RuntimeError, AttributeError) as exc:
            if not _workspace_scope_db_unavailable(exc):
                raise
        target_user["workspace_role"] = workspace_role
    action = "user.updated"
    if before and before.get("role") != target_user.get("role"):
        action = "user.role_changed"
    elif before and before.get("is_active") and not target_user.get("is_active"):
        action = "user.disabled"
    elif before and not before.get("is_active") and target_user.get("is_active"):
        action = "user.enabled"
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        action,
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "role": target_user.get("role"),
            "is_active": target_user.get("is_active"),
            "password_changed": password_changed,
        },
    )
    if password_changed:
        # Password change is independently auditable: an admin overriding a
        # user's credential is privileged enough to warrant its own row, even
        # when bundled with other field updates in the same request.
        await _audit.record_event(
            admin_user.get("id"),
            admin_user.get("email"),
            "user.password_changed",
            "user",
            str(user_id),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            metadata={"target_email": target_user.get("email")},
        )
    return target_user


@app.delete(
    "/api/admin/users/{user_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_delete(
    user_id: int,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    if user_id == admin_user["id"]:
        raise HTTPException(400, "you cannot delete your own account")
    await _assert_can_manage_target_user(admin_user, user_id)
    try:
        ok = await _auth.delete_user(user_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "user not found")
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.deleted",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"deleted": True, "id": user_id}


def _vpn_configured() -> bool:
    return _vpn_configured_impl(os.environ)


def _pack_vpn_conf(conf_text: str, email: str) -> tuple[bytes, str]:
    return _pack_vpn_conf_impl(conf_text, email)


def _safe_filename(email: str) -> str:
    return _safe_filename_impl(email)


async def _create_vpn_config_link(user_id: int, email: str) -> dict:
    try:
        if not _vpn_configured():
            return {
                "issued": False,
                "error": "VPN_API_URL / VPN_API_PASSWORD no configurados",
            }
        wg_id = await _vpn.create_client(email)
        vpn_tok, _ = await _tokens.create(user_id, "vpn", wg_client_id=wg_id)
        try:
            conf_text = await _vpn.get_config(wg_id)
        except Exception:
            conf_text = None
        return {
            "issued": True,
            "link": _vpn_link(vpn_tok),
            "wg_client_id": wg_id,
            "conf_text": conf_text,
        }
    except _vpn.VPNError as exc:
        request_id = _internal_error_request_id()
        logger.exception(
            "vpn issuance failed request_id=%s",
            request_id,
            extra={"request_id": request_id, "exception_type": type(exc).__name__},
        )
        return {"issued": False, "error": "Internal Error", "request_id": request_id}
    except Exception as exc:
        request_id = _internal_error_request_id()
        logger.exception(
            "unexpected vpn issuance failure request_id=%s",
            request_id,
            extra={"request_id": request_id, "exception_type": type(exc).__name__},
        )
        return {"issued": False, "error": "Internal Error", "request_id": request_id}


async def _issue_vpn_for_user(user_id: int, email: str, name: str | None) -> dict:
    res = await _create_vpn_config_link(user_id, email)
    if not res.get("issued"):
        return res
    subject, html = _email.render_vpn_config(name, res["link"], VPN_TTL_HOURS)
    sent = await _email.send_email(email, subject, html)
    return {"issued": True, "email_sent": sent, "wg_client_id": res["wg_client_id"]}


async def _rollback_failed_invite(user_id: int, vpn_result: dict | None = None) -> None:
    vpn_client_id = (vpn_result or {}).get("wg_client_id")
    if vpn_client_id:
        try:
            await _vpn.delete_client(str(vpn_client_id))
        except Exception:
            logger.warning("invite rollback could not delete vpn client", exc_info=True)
    try:
        await _auth.delete_user(user_id)
    except Exception:
        logger.exception("invite rollback could not delete user_id=%s", user_id)


@app.get("/vpn-config/{token}")
async def get_vpn_config(token: str, user: dict | None = Depends(current_user)):
    info = await _tokens.consume_lookup(token, "vpn")
    if not info or not info.get("wg_client_id"):
        raise HTTPException(404, "Link invalido o ya utilizado")
    try:
        cfg = await _vpn.get_config(info["wg_client_id"])
    except _vpn.VPNError as exc:
        raise HTTPException(
            502, f"No se pudo obtener la configuracion VPN: {exc}"
        ) from exc
    safe = _safe_filename(info.get("email") or "user")
    return Response(
        content=cfg,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{safe}.conf"'},
    )


@app.post(
    "/api/admin/users/{user_id}/vpn-reissue",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_vpn_reissue(
    user_id: int,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    await _assert_can_manage_target_user(admin_user, user_id)
    res = await _issue_vpn_for_user(
        user_id, target_user["email"], target_user.get("name")
    )
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "vpn.reissued",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "issued": bool(res.get("issued")),
            "email_sent": res.get("email_sent"),
        },
    )
    return {"reissued": res.get("issued", False), **res}


@app.post(
    "/api/admin/users/invite",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_invite(
    body: dict,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    """Invite a new user by email. Creates an inactive user with no password,
    issues an invitation token, and emails the activation link."""
    email = _normalize_email_or_400(body.get("email"))
    existing = await _auth.get_user_by_email(email)
    if existing:
        raise HTTPException(409, f"user with email {email} already exists")
    role = _assignable_role(body.get("role"), admin_user)
    workspace_id = (
        body.get("workspace_id") or admin_user.get("active_workspace_id") or ""
    ).strip() or None
    if workspace_id:
        await _assert_can_use_workspace(admin_user, workspace_id)
    else:
        raise HTTPException(400, "workspace_id is required")
    target_user = await _auth.create_invited_user(
        email=email,
        name=body.get("name"),
        role=role,
        workspace_id=workspace_id,
    )
    tok, _ = await _tokens.create(target_user["id"], "invite")
    activation_link = _activation_link(tok)

    vpn_result: dict = {"issued": False}
    if body.get("with_vpn", True):
        vpn_result = await _create_vpn_config_link(target_user["id"], email)

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = _pack_vpn_conf(vpn_result["conf_text"], email)
        attachments.append(
            (f"{_safe_filename(email)}.zip", zip_bytes, "application/zip")
        )

    try:
        if vpn_result.get("issued"):
            subject, html = _email.render_invitation_with_vpn(
                target_user.get("name"),
                email,
                activation_link,
                vpn_result["link"],
                INVITE_TTL_HOURS,
                VPN_TTL_HOURS,
                vpn_password,
            )
            sent = await _email.send_email(
                email, subject, html, attachments=attachments
            )
            vpn_result["email_sent"] = sent
        else:
            subject, html = _email.render_invitation(
                target_user.get("name"), email, activation_link, INVITE_TTL_HOURS
            )
            sent = await _email.send_email(email, subject, html)
        if not sent:
            raise RuntimeError("invitation email send failed")
    except Exception as exc:
        await _rollback_failed_invite(int(target_user["id"]), vpn_result)
        logger.exception(
            "invitation email failed; rolled back user_id=%s", target_user["id"]
        )
        raise HTTPException(500, "Invitation email delivery failed") from exc
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.invited",
        "user",
        str(target_user["id"]),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "role": role,
            "workspace_id": workspace_id,
            "email_sent": sent,
            "vpn": vpn_result,
        },
    )
    return {"invited": True, "user": target_user, "email_sent": sent, "vpn": vpn_result}


@app.post(
    "/api/admin/users/{user_id}/reinvite",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_reinvite(
    user_id: int,
    request: Request,
    body: dict | None = None,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    """Re-issue an invitation email (only for users that have not activated yet)."""
    target_user = await _auth.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    await _assert_can_manage_target_user(admin_user, user_id)
    if target_user.get("is_active"):
        raise HTTPException(400, "user already active; use password reset instead")
    tok, _ = await _tokens.create(user_id, "invite")
    activation_link = _activation_link(tok)
    payload = body or {}
    vpn_result: dict = {"issued": False}
    if payload.get("with_vpn", True):
        vpn_result = await _create_vpn_config_link(user_id, target_user["email"])

    attachments: list[tuple[str, bytes, str]] = []
    vpn_password: str | None = None
    if vpn_result.get("issued") and vpn_result.get("conf_text"):
        zip_bytes, vpn_password = _pack_vpn_conf(
            vpn_result["conf_text"], target_user["email"]
        )
        attachments.append(
            (
                f"{_safe_filename(target_user['email'])}.zip",
                zip_bytes,
                "application/zip",
            )
        )

    if vpn_result.get("issued"):
        subject, html = _email.render_invitation_with_vpn(
            target_user.get("name"),
            target_user["email"],
            activation_link,
            vpn_result["link"],
            INVITE_TTL_HOURS,
            VPN_TTL_HOURS,
            vpn_password,
        )
        sent = await _email.send_email(
            target_user["email"], subject, html, attachments=attachments
        )
        vpn_result["email_sent"] = sent
    else:
        subject, html = _email.render_invitation(
            target_user.get("name"),
            target_user["email"],
            activation_link,
            INVITE_TTL_HOURS,
        )
        sent = await _email.send_email(target_user["email"], subject, html)
    await _audit.record_event(
        admin_user.get("id"),
        admin_user.get("email"),
        "user.reinvited",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"email_sent": sent, "vpn": vpn_result},
    )
    return {"reinvited": True, "email_sent": sent, "vpn": vpn_result}


@app.post(
    "/api/admin/users/{user_id}/send-reset",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_send_reset(
    user_id: int,
    request: Request,
    admin: dict = Depends(require_permission("iam.users.write")),
):
    """Email a password reset link and issue a one-time admin temporary password."""
    import secrets as _secrets

    target_user = await _auth.get_user_by_id(user_id)
    if not target_user or not target_user.get("is_active"):
        raise HTTPException(404, "user not found or inactive")
    await _assert_can_manage_target_user(admin, user_id)
    temporary_password = f"{_secrets.token_urlsafe(24)}Aa1!"
    pool = await _auth.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """
                UPDATE users
                   SET password_hash = $1,
                       must_change_password = TRUE,
                       is_active = TRUE
                 WHERE id = $2
                   AND is_active = TRUE
                 RETURNING id
                """,
                _auth.hash_password(temporary_password),
                user_id,
            )
            if not updated:
                raise HTTPException(404, "user not found or inactive")
            await conn.execute("DELETE FROM refresh_tokens WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM user_sessions WHERE user_id = $1", user_id)
    tok, _ = await _tokens.create(user_id, "reset")
    subject, html = _email.render_password_reset(
        target_user.get("name"), _reset_link(tok), RESET_TTL_HOURS
    )
    sent = await _email.send_email(target_user["email"], subject, html)
    await _audit.record_event(
        admin.get("id"),
        admin.get("email"),
        "password_reset.sent",
        "user",
        str(user_id),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "email_sent": sent,
            "temporary_password_issued": True,
            "password_delivery": "one_time_response",
            "sessions_revoked": True,
        },
    )
    return {
        "sent": sent,
        "temporary_password": temporary_password,
        "password_delivery": "one_time_response",
    }


from app.routers import cartridges as cartridges_router
from app.routers import actions as actions_router
from app.routers import copilot as copilot_router
from app.routers import (
    copilot_advanced as copilot_advanced_router,
)  # v1.45 advanced copilot
from app.routers import copilot_drafts as copilot_drafts_router  # v1.44.2 Tarea H
from app.routers import copilot_memory as copilot_memory_router  # v1.44.2 Tarea G
from app.routers import copilot_workflows as copilot_workflows_router  # v1.44.2 Tarea I
from app.routers import dashboard as dashboard_router  # v1.44.1 Tarea E
from app.routers import freshness as freshness_router
from app.routers import intelligence as intelligence_router
from app.routers import marketplace as marketplace_router
from app.routers import metrics as metrics_router
from app.routers import admin_tenants as admin_tenants_router
from app.routers import onboarding as onboarding_router  # v1.44.1 Tarea F
from app.routers import studio as studio_router  # v1.44.3.3 Task B
from app.routers import (
    control_room,
    mcp,
    mcp_public,
    operations,
    pages,
    security,
    settings,
    settings_internal,
)

app.include_router(pages.router)
app.include_router(mcp.router)
app.include_router(mcp_public.router)
app.include_router(settings.router)
app.include_router(settings_internal.router)
app.include_router(operations.router)
app.include_router(control_room.router)
app.include_router(actions_router.router)
app.include_router(security.router)
app.include_router(cartridges_router.router)
app.include_router(freshness_router.router)
app.include_router(intelligence_router.router)
app.include_router(intelligence_router.v1_router)
app.include_router(intelligence_router.internal_router)
app.include_router(marketplace_router.router)
app.include_router(metrics_router.router)
app.include_router(admin_tenants_router.router)
app.include_router(copilot_router.router)
app.include_router(dashboard_router.router)  # v1.44.1 Tarea E
app.include_router(onboarding_router.router)  # v1.44.1 Tarea F
app.include_router(copilot_memory_router.router)  # v1.44.2 Tarea G
app.include_router(copilot_drafts_router.router)  # v1.44.2 Tarea H
app.include_router(copilot_workflows_router.router)  # v1.44.2 Tarea I
app.include_router(
    copilot_workflows_router.plural_router
)  # v1.44.6 Task 1 executor aliases
app.include_router(
    copilot_advanced_router.router
)  # v1.45 advanced copilot (goals, lessons, watchdogs, briefing-v2, ask-with-context)
app.include_router(studio_router.router)  # v1.44.3.3 Task B (stub)


# v1.42.1 auditor finding: RequestIDMiddleware must be the OUTERMOST
# wrapper so the ``X-Request-ID`` header lands on responses generated
# by inner middlewares (auth 401, CSRF 403, etc.). Registering it
# here — after every ``@app.middleware("http")`` decorator above has
# run — guarantees it ends up near the front of ``user_middleware``.
# v1.44.3.2.2 R-Mac-3 update: CORS is now registered AFTER this so
# CORS ends up STRICTLY OUTERMOST, with RequestID one layer in.
# Both invariants hold:
#   - CORS sees every request (incl. OPTIONS preflight) before any
#     inner middleware short-circuits
#   - RequestID still wraps auth_middleware so X-Request-ID lands
#     on auth 401s / CSRF 403s
app.add_middleware(RequestIDMiddleware)

# v1.44.3.2.2 R-Mac-3 (CORS ordering hotfix): CORSMiddleware MUST
# be the OUTERMOST middleware in the ASGI stack so that:
#   - OPTIONS preflight requests are intercepted + answered by
#     CORS itself BEFORE auth_middleware can return 401/405,
#   - Allow-Origin lands on EVERY response including auth 401s and
#     security_headers redirects (which is what the browser needs
#     to surface a proper CORS error vs a generic "fetch failed").
#
# Starlette builds the stack by REVERSING user_middleware, so the
# LAST registered middleware ends up OUTERMOST. This is the LAST
# add_middleware call in the module, so CORS is now outermost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # v1.44.3.2.2 R-Mac: added X-CSRF-Token. The Next.js login flow
    # (lib/auth-flow.ts) sends the double-submit-cookie value as
    # this header on POST /auth/login; without it the browser
    # preflight rejects the actual request before it leaves the
    # tab.
    # R-Mac-3 follow-up: added X-Requested-With (axios + fetch
    # default), Accept (browser default), Cookie (some browsers
    # send it on credentialed requests).
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Internal-Api-Key",
        "x-api-key",
        "x-internal-service",
        "X-CSRF-Token",
        "X-Requested-With",
        "Accept",
        "Cookie",
    ],
    # R-Mac-3: surface Set-Cookie + X-CSRF-Token through the CORS
    # response so the browser's response.cookies / header reads
    # work from the Next.js side. expose_headers is for ACTUAL
    # responses (different from allow_headers, which is for the
    # preflight Access-Control-Allow-Headers reply).
    expose_headers=["Set-Cookie", "X-CSRF-Token", "X-Request-ID"],
    # Cache preflight for 1 h so the browser doesn't re-OPTIONS
    # every single XHR.
    max_age=3600,
)
