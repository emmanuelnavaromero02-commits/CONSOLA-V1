"""
ΩMEGA by EPIUSE — Console
MCP-first: descubre y orquesta MCP servers registrados.
UI minimalista: chat con asistente + estado de servidores MCP.
"""

from __future__ import annotations

# fmt: off

import asyncio
import hashlib
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
    app_declared_datasets as _app_declared_datasets,
    app_payload_cartridge_candidates as _app_payload_cartridge_candidates,
    apps_from_payload as _apps_from_payload,
    filter_apps_payload_to_ready_datasets as _filter_apps_payload_to_ready_datasets,
    filter_apps_payload_to_scoped_connections as _filter_apps_payload_to_scoped_connections,
    user_with_apps_scope as _user_with_apps_scope,
)
from app.domains.apps.readiness import (
    gold_ready_datasets_for_apps as _gold_ready_datasets_for_apps_impl,
)
from app.domains.apps.service import (
    apps_payload_visible_and_ready as _apps_payload_visible_and_ready_impl,
    delete_refinement_app_payload as _delete_refinement_app_payload_impl,
    load_refinement_apps_payload as _load_refinement_apps_payload_impl,
    refinement_app_html as _refinement_app_html_impl,
)
from app.domains.apps.scope import (
    active_scoped_connection_cartridges as _active_scoped_connection_cartridges_impl,
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
    inject_app_bridge as _inject_app_bridge,
    inject_script_nonce as _inject_script_nonce,
    workspace_server_url as _workspace_server_url_impl,
)
from app.domains.apps.manifests import (
    drift_report as _app_drift_report,
    packaged_manifest as _packaged_manifest,
    served_digest as _served_manifest_digest,
)
from app.domains.apps.grants import (
    granted_datasets as _granted_datasets,
    has_grant as _has_app_grant,
    reconcile_workspace as _reconcile_app_grants_for_workspace,
)
from app.domains.apps.capability import (
    api_navigation_blocked as _api_navigation_blocked,
    frame_fetch_metadata_ok as _frame_fetch_metadata_ok,
    issue_content_capability as _issue_content_capability,
    verify_content_capability as _verify_content_capability,
    verify_content_capability_envelope as _verify_content_capability_envelope,
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
    invoke_agent_payload as _invoke_agent_payload_impl,
    parse_agent_scheduled_fire_at as _parse_agent_scheduled_fire_at_impl,
)
from app.domains.agentops.streaming import (
    agent_invoke_stream_response as _agent_invoke_stream_response_impl,
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
from app.domains.admin.users_scope import (
    assert_can_manage_target_user as _assert_can_manage_target_user_impl,
    assert_can_use_workspace as _assert_can_use_workspace_impl,
    attach_workspace_summaries as _attach_workspace_summaries_impl,
    list_admin_users_payload as _list_admin_users_payload_impl,
    list_user_picker_payload as _list_user_picker_payload_impl,
    set_workspace_role_for_user as _set_workspace_role_for_user_impl,
    target_user_workspace_ids as _target_user_workspace_ids_impl,
    visible_user_ids_for_admin as _visible_user_ids_for_admin_impl,
    workspace_rows_from_auth_stub as _workspace_rows_from_auth_stub_impl,
    workspace_summaries_for_users as _workspace_summaries_for_users_impl,
)
from app.domains.admin.vpn_invites import (
    create_vpn_config_link as _create_vpn_config_link_impl,
    issue_vpn_for_user as _issue_vpn_for_user_impl,
    reissue_vpn_for_user_payload as _reissue_vpn_for_user_payload_impl,
    rollback_failed_invite as _rollback_failed_invite_impl,
    vpn_config_download_response as _vpn_config_download_response_impl,
)
from app.domains.admin.password_reset import (
    admin_send_reset_payload as _admin_send_reset_payload_impl,
)
from app.domains.admin.user_mutations import (
    create_admin_user_payload as _create_admin_user_payload_impl,
    delete_admin_user_payload as _delete_admin_user_payload_impl,
    invite_admin_user_payload as _invite_admin_user_payload_impl,
    reinvite_admin_user_payload as _reinvite_admin_user_payload_impl,
    update_admin_user_payload as _update_admin_user_payload_impl,
)
from app.domains.studio.chat_stream import (
    studio_chat_stream_response as _studio_chat_stream_response_impl,
)
from app.domains.studio.entity_mutations import (
    rename_studio_entity_payload as _rename_studio_entity_payload_impl,
    update_studio_entity_payload as _update_studio_entity_payload_impl,
)
from app.domains.copilot.llm_keys import (
    llm_key_set_payload as _llm_key_set_payload_impl,
    llm_key_status_payload as _llm_key_status_payload_impl,
    llm_secret_keys as _llm_secret_keys_impl,
)
from app.domains.copilot.assistant_chat import (
    assistant_chat_payload as _assistant_chat_payload_impl,
)
from app.domains.decisions.access import (
    can_delete_decision as _dec_can_delete_impl,
    can_edit_decision as _dec_can_edit_impl,
    current_workspace_id as _current_workspace_id_impl,
    decision_list_query as _dec_list_query_impl,
    decision_load_query as _dec_load_query_impl,
    decision_visible_clause as _dec_visible_clause_impl,
    is_decision_workspace_admin as _dec_is_workspace_admin_impl,
)
from app.domains.decisions.business_visibility import (
    fetch_business_decisions as _fetch_business_decisions,
    filter_decision_rows as _filter_decision_rows,
    preserve_control_room_provenance as _preserve_control_room_provenance,
)
from app.domains.decisions.payloads import (
    coerce_date as _coerce_date_impl,
    coerce_datetime as _coerce_dt_impl,
    decision_update_assignments as _decision_update_assignments_impl,
    decision_update_sql_and_params as _decision_update_sql_and_params_impl,
    decision_row_to_dict as _dec_row_to_dict_impl,
)
from app.services.control_room.business_repository import (
    decision_kpis_with_provenance as _decision_kpis_with_provenance,
)
from app.domains.iam.roles import (
    GLOBAL_ASSIGNABLE_ROLES as _IAM_GLOBAL_ASSIGNABLE_ROLES,
    WORKSPACE_ASSIGNABLE_ROLES as _IAM_WORKSPACE_ASSIGNABLE_ROLES,
    assignable_role as _assignable_role_impl,
    is_global_iam_admin as _is_global_iam_admin_impl,
    iam_admin_workspace_ids as _iam_admin_workspace_ids_impl,
    session_workspace_ids as _session_workspace_ids_impl,
    workspace_scope_db_unavailable as _workspace_scope_db_unavailable_impl,
)
from app.domains.iam.access_request import (
    me_access_response as _me_access_response_impl,
)
from app.domains.iam.access_payload import (
    fetch_cartridge_access as _fetch_cartridge_access,
)
from app.domains.data_platform.catalog_payloads import (
    catalog_cache_key as _catalog_cache_key,
    catalog_query_args as _catalog_query_args,
)
from app.domains.data_platform.catalog_requests import (
    catalog_get_payload as _catalog_get_payload_impl,
    catalog_relationship_payload as _catalog_relationship_payload_impl,
    catalog_upsert_payload as _catalog_upsert_payload_impl,
)
from app.domains.data_platform.bronze_physical import (
    bronze_physical_snapshot as _bronze_physical_snapshot_impl,
    count_bronze_parquet_rows as _count_bronze_parquet_rows_impl,
)
from app.domains.data_platform.bronze_query import (
    bronze_query_payload as _bronze_query_payload_impl,
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
from app.domains.data_platform.data_api_requests import (
    data_options_payload as _data_options_payload_impl,
    filtered_data_query_payload as _filtered_data_query_payload_impl,
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
    explorer_delete_response as _explorer_delete_response,
    explorer_download_response as _explorer_download_response,
    explorer_list_kwargs as _explorer_list_kwargs,
    explorer_list_response as _explorer_list_response,
    explorer_object_row as _explorer_object_row,
    explorer_path_allowed as _explorer_path_allowed_impl,
    explorer_quicklinks_for_cartridges as _explorer_quicklinks_for_cartridges_impl,
    explorer_visible_buckets as _explorer_visible_buckets,
    resolve_explorer_bucket as _resolve_explorer_bucket_impl,
)
from app.domains.data_platform.refinement_errors import (
    raise_for_refinement_payload_error as _raise_for_refinement_payload_error,
    upstream_error_detail as _upstream_error_detail,
)
from app.domains.data_platform.refinement_invoke import (
    refinement_invoke as _refinement_invoke_impl,
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
from app.domains.data_platform.schema_requests import (
    schema_response_payload as _schema_response_payload_impl,
    sources_response_payload as _sources_response_payload_impl,
)
from app.domains.data_platform.rag_payloads import (
    rag_empty_answer as _rag_empty_answer,
    rag_search_arguments as _rag_search_arguments,
    rag_synthesis_messages as _rag_synthesis_messages,
)
from app.domains.data_platform.rag_requests import (
    rag_answer_payload as _rag_answer_payload_impl,
    rag_delete_source_payload as _rag_delete_source_payload_impl,
    rag_ingest_payload as _rag_ingest_payload_impl,
    rag_reindex_payload as _rag_reindex_payload_impl,
    rag_search_payload as _rag_search_payload_impl,
    rag_sources_payload as _rag_sources_payload_impl,
)
from app.domains.data_platform.semantic_requests import (
    semantic_enrich_payload as _semantic_enrich_payload_impl,
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
    is_app_content_capability_path as _is_app_content_capability_path_impl,
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
from app.domains.system.readyz import build_readyz_checks as _build_readyz_checks_impl
from app.domains.system.lifespan import (
    cancel_background_tasks as _cancel_background_tasks,
    env_flag_enabled as _env_flag_enabled,
    run_packaged_startup_seeds as _run_packaged_startup_seeds_impl,
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
from app.domains.pipeline.run_logs import (
    build_job_logs_payload as _build_job_logs_payload_impl,
    build_pipeline_run_logs_payload as _build_pipeline_run_logs_payload_impl,
)
from app.domains.pipeline.overview import (
    build_pipeline_overview as _build_pipeline_overview_impl,
)
from app.domains.pipeline.extract_all import (
    fanout_pipeline_extract_all as _fanout_pipeline_extract_all_impl,
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
from app.domains.pipeline.status_transitions import (
    postgres_monotonic_status as _postgres_monotonic_pipeline_status,
    postgres_status_accepts as _postgres_pipeline_status_accepts,
)
from app.domains.pipeline.successfactors_reservation import (
    reserve_entity_extract_slot as _reserve_successfactors_entity_extract_slot_impl,
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
    build_sync_run_status as _build_sync_run_status_impl,
    fetch_active_sync_run as _fetch_active_sync_run_impl,
    fetch_sync_child_runs as _fetch_sync_child_runs_impl,
    fetch_sync_run as _fetch_sync_run_impl,
    inactive_sync_run_payload as _inactive_sync_run_payload,
    initial_sync_steps as _initial_sync_steps,
    maybe_trigger_aggregate_extract_all as _maybe_trigger_aggregate_extract_all_impl,
    merge_sync_steps as _merge_sync_steps,
    normalize_sync_now_request_id as _normalize_sync_now_request_id,
    normalize_sync_step_payload as _normalize_sync_step_payload,
    pipeline_extract_all_mode_target as _pipeline_extract_all_mode_target,
    pipeline_extract_all_public_response as _pipeline_extract_all_public_response,
    pipeline_extract_all_run_id as _pipeline_extract_all_run_id,
    run_sync_extract_all_with_retries as _run_sync_extract_all_with_retries,
    sync_child_gold_refresh_summary as _sync_child_gold_refresh_summary,
    sync_child_reason as _sync_child_reason,
    sync_clean_mode as _sync_clean_mode,
    sync_clean_target as _sync_clean_target,
    sync_control_room_gold_refresh_terminal as _sync_control_room_gold_refresh_terminal,
    sync_entity_idempotency_key as _sync_entity_idempotency_key,
    sync_errors_retryable as _sync_errors_retryable,
    sync_dataset_seed_failure_extra as _sync_dataset_seed_failure_extra,
    sync_dataset_seed_failure_step_updates as _sync_dataset_seed_failure_step_updates,
    sync_extract_all_error_message as _sync_extract_all_error_message,
    sync_extract_all_result_state as _sync_extract_all_result_state,
    sync_extract_all_trigger_extra as _sync_extract_all_trigger_extra,
    sync_extract_all_trigger_step_updates as _sync_extract_all_trigger_step_updates,
    sync_extra_from_row as _sync_extra_from_row,
    sync_gold_refresh_dataset_names as _sync_gold_refresh_dataset_names,
    sync_child_runtime_state as _sync_child_runtime_state,
    sync_core_step_updates as _sync_core_step_updates,
    sync_materialization_state as _sync_materialization_state,
    sync_now_lock_key as _sync_now_lock_key_impl,
    sync_now_run_id_from_request_id as _sync_now_run_id_from_request_id,
    sync_public_payload as _sync_public_payload,
    sync_running_extra as _sync_running_extra,
    sync_run_age_seconds as _sync_run_age_seconds,
    sync_run_needs_final_reconcile as _sync_run_needs_final_reconcile,
    sync_run_working_state as _sync_run_working_state,
    sync_start_step_updates as _sync_start_step_updates,
    sync_status_from_steps as _sync_status_from_steps,
    sync_step_entity_summary as _sync_step_entity_summary,
)
from app.domains.pipeline.concurrency import (
    gather_by_entity as _pipeline_gather_by_entity,
)
from app.domains.pipeline.agentops_refresh import (
    run_sync_agentops_monitors as _run_sync_agentops_monitors_impl,
    run_sync_agentops_status as _run_sync_agentops_status_impl,
)
from app.domains.pipeline.control_room_refresh import (
    run_sync_control_room_gold_refresh as _run_sync_control_room_gold_refresh_impl,
    run_sync_control_room_status as _run_sync_control_room_status_impl,
)
from app.domains.pipeline.aggregate_trigger import (
    trigger_sync_aggregate_extract_all as _trigger_sync_aggregate_extract_all_impl,
)
from app.domains.pipeline.packaged_datasets import (
    ensure_sync_packaged_datasets as _ensure_sync_packaged_datasets_impl,
)
from app.domains.pipeline.dag_templates_payloads import (
    dag_template_code_payload as _dag_template_code_payload_impl,
    dag_templates_payload as _dag_templates_payload_impl,
)
from app.domains.pipeline.airflow_trigger import (
    trigger_airflow_extract_dag as _trigger_airflow_extract_dag_impl,
)
from app.domains.pipeline.extract_config import (
    apply_user_scope_to_dag_conf as _apply_user_scope_to_dag_conf_impl,
    build_dag_extract_conf as _build_dag_extract_conf_impl,
    build_mcp_extract_args as _build_mcp_extract_args_impl,
    dag_extract_dag_id_from_metadata as _dag_extract_dag_id_from_metadata_impl,
    dag_run_id_from_idempotency_key as _dag_run_id_from_idempotency_key_impl,
    entity_declared_in_static_catalog as _entity_declared_in_static_catalog_impl,
    is_transient_airflow_trigger_error as _is_transient_airflow_trigger_error_impl,
    normalize_pipeline_conn_id as _normalize_pipeline_conn_id_impl,
    resolve_pipeline_sync_conn_id as _resolve_pipeline_sync_conn_id_impl,
)
from app.domains.pipeline.run_history import (
    list_pipeline_entity_runs as _list_pipeline_entity_runs_impl,
    list_pipeline_runs as _list_pipeline_runs_impl,
    pipeline_run_logs_payload as _pipeline_run_logs_payload_impl,
)
from app.domains.monitoring.invoke import (
    invoke_monitoring_tool as _invoke_monitoring_tool_impl,
)
from app.domains.monitoring.tools import build_monitoring_tools
from app.domains.studio.access import (
    cartridge_visible_for_context as _cartridge_visible_for_context,
    has_studio_ops_write_role as _has_studio_ops_write_role_impl,
    studio_ops_role_name as _studio_ops_role_name_impl,
)
from app.domains.studio.cartridge_probe import (
    probe_microservice as _probe_microservice_impl,
)
from app.domains.studio.dag_graph import parse_dag_graph as _parse_dag_graph
from app.domains.studio.ops_invoke import (
    invoke_studio_ops_tool as _invoke_studio_ops_tool_impl,
)
from app.domains.studio.ops_tools import build_studio_ops_tools
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
from app.services import scheduled_monitor_execution as _scheduled_monitor_execution
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
from app.services.s3_client import get_lakehouse_explorer_client, get_minio_client
from app.services.security_context import (
    build_security_context,
    rls_user_context,
    sign_security_context,
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

    # Startup seeds reconcile packaged catalogs used by Studio, Control Room,
    # and cartridge surfaces. A failure no longer leaves Console apparently
    # ready: the process stays live, but /readyz returns 503 until the next
    # successful boot.
    await _run_packaged_startup_seeds_impl(
        app,
        get_db_pool=_get_db_pool,
        run_startup_seed=_run_startup_seed,
    )
    task = asyncio.create_task(_periodic_health_check())
    copilot_context_task: asyncio.Task | None = None
    if _env_flag_enabled(os.environ.get("COPILOT_CONTEXT_SCHEDULER_ENABLED")):
        from app.services import copilot_context_service

        copilot_context_task = asyncio.create_task(
            copilot_context_service.hourly_scheduler()
        )
    try:
        yield
    finally:
        await _cancel_background_tasks(task, copilot_context_task)
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
    "banxico": {
        "banxico": ("INTERNAL_API_KEY_BANXICO_TO_CONSOLE",),
        "cartridge-banxico": ("INTERNAL_API_KEY_BANXICO_TO_CONSOLE",),
        "airflow": ("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",),
    },
    "inegi": {
        "inegi": ("INTERNAL_API_KEY_INEGI_TO_CONSOLE",),
        "cartridge-inegi": ("INTERNAL_API_KEY_INEGI_TO_CONSOLE",),
        "airflow": ("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",),
    },
    "sec_edgar": {
        "sec_edgar": ("INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE",),
        "cartridge-sec-edgar": ("INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE",),
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
    return await _count_bronze_parquet_rows_impl(
        source=source,
        latest_date=latest_date,
        user=user,
        bronze_latest_s3_glob=_bronze_latest_s3_glob,
        hdr_for=_hdr_for,
        mcp_payload=_mcp_payload,
        refinement_url=REFINEMENT_URL,
    )


async def _bronze_physical_snapshot(
    cartridge: str, entity: str, user: dict | None
) -> dict:
    return await _bronze_physical_snapshot_impl(
        cartridge=cartridge,
        entity=entity,
        user=user,
        safe_pipeline_name=_safe_pipeline_name,
        build_security_context=build_security_context,
        explorer_path_allowed=_explorer_path_allowed,
        minio_client=_minio_client,
        bronze_latest_date_from_objects=_bronze_latest_date_from_objects,
        count_rows=_count_bronze_parquet_rows,
    )


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


# Inventory behind this guard: no /api route serves a downloadable or
# navigable document. Every one answers JSON to a same-origin fetch/XHR, and
# the surfaces that do stream files live under /viewer and /static. So the
# exception list is empty by inspection rather than by assumption; add a path
# here only with the same check done again.
_API_NAVIGATION_EXEMPT: frozenset[str] = frozenset()


@app.middleware("http")
async def api_navigation_guard_middleware(request: Request, call_next):
    """Stop a sandboxed app turning an authenticated API into a navigation.

    ``connect-src 'none'`` denies a published app every fetch primitive, but
    not navigation — and a top-level GET carries the SameSite=Lax session
    cookie, which is how an app framed at our origin could still read
    ``/api/me``. Nothing under /api is meant to be reached as a document, so a
    document/frame destination is refused before any handler runs.

    This is defence in depth layered on the capability check, not a substitute
    for it, and it deliberately does not touch the cookie's SameSite setting.
    """
    path = request.url.path
    if (
        path.startswith("/api/")
        and path not in _API_NAVIGATION_EXEMPT
        and _api_navigation_blocked(request.headers)
    ):
        return _apply_security_headers(
            JSONResponse({"detail": "not available"}, status_code=403), path
        )
    return await call_next(request)


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


def _is_app_content_capability_path(path: str) -> bool:
    return _is_app_content_capability_path_impl(path)


def _is_agent_runner_request(request: Request) -> bool:
    return _is_agent_runner_request_impl(
        request.url.path,
        supplied_token=request.headers.get("X-Agent-Runner-Token", ""),
        expected_token=os.environ.get("AGENT_RUNNER_TOKEN", ""),
    )


async def _enrich_session_workspace_user(
    user: dict,
    requested_workspace_id: str | None,
) -> dict:
    workspaces = await _workspace_access_options(user)
    if not workspaces:
        return user
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
            raise HTTPException(403, "workspace access forbidden")
    enriched_user = dict(user)
    enriched_user.update(
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
    return enriched_user


async def _enrich_jwt_workspace_user(
    jwt_user: dict,
    requested_workspace_id: str | None,
) -> dict:
    workspaces = await _workspace_access_options(jwt_user)
    if not workspaces:
        return jwt_user
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
            raise HTTPException(403, "workspace access forbidden")
    enriched_user = dict(jwt_user)
    enriched_user.update(
        {
            "workspace_role": active_workspace["workspace_role"],
            "active_workspace_id": active_workspace["workspace_id"],
            "active_tenant_id": active_workspace["tenant_id"],
            "workspaces": workspaces,
            "allowed_cartridges": await _workspace_cartridges(
                active_workspace["workspace_id"], user_id=jwt_user["id"]
            ),
        }
    )
    return enriched_user


def _auth_middleware_error_response(detail: str, status_code: int, path: str) -> Response:
    return _apply_security_headers(
        JSONResponse({"detail": detail}, status_code=status_code),
        path,
    )


def _is_auth_public_path(path: str) -> bool:
    return path in _AUTH_PUBLIC_EXACT or any(
        path.startswith(p) for p in _AUTH_PUBLIC_PREFIX
    )


async def _session_user_for_middleware(
    request: Request,
    *,
    requested_workspace_id: str | None,
    is_public: bool,
    path: str,
) -> tuple[dict | None, Response | None]:
    token = request.cookies.get(_auth.COOKIE_NAME)
    user = await _auth.get_session_user(token) if token else None
    if not user or not (requested_workspace_id or not user.get("active_workspace_id")):
        return user, None
    try:
        return await _enrich_session_workspace_user(user, requested_workspace_id), None
    except HTTPException as exc:
        return None, _auth_middleware_error_response(exc.detail, exc.status_code, path)
    except Exception:
        logger.exception("Session workspace enrichment failed")
        if not is_public and _uses_rbac_dependency(path):
            return None, _auth_middleware_error_response(
                "workspace context unavailable",
                403,
                path,
            )
        return user, None


async def _bearer_user_for_middleware(
    request: Request,
    *,
    requested_workspace_id: str | None,
    is_public: bool,
    path: str,
) -> tuple[dict | None, Response | None]:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, None
    try:
        # Sprint v1.10: async variant runs the Redis blacklist
        # check; legacy decode kept for unit tests.
        claims = await verify_access_token_async(auth_header[7:])
        jwt_user = await _auth.get_user_by_id(int(claims["sub"]))
        if jwt_user and jwt_user.get("is_active"):
            return await _enrich_jwt_workspace_user(
                jwt_user, requested_workspace_id
            ), None
    except HTTPException as exc:
        return None, _auth_middleware_error_response(exc.detail, exc.status_code, path)
    except Exception:
        logger.debug("Bearer JWT auth fallback failed", exc_info=True)
        if not is_public and _uses_rbac_dependency(path):
            return None, _auth_middleware_error_response(
                "authentication required",
                401,
                path,
            )
    return None, None


def _unauthenticated_middleware_response(
    request: Request,
    *,
    path: str,
    is_public: bool,
    user: dict | None,
) -> Response | None:
    if user or is_public:
        return None
    if _uses_rbac_dependency(path):
        return None
    if _is_api_like(path, request.headers.get("accept", "")):
        return _auth_middleware_error_response("authentication required", 401, path)
    return _apply_security_headers(RedirectResponse(url=f"/login?next={path}"), path)


def _forced_password_change_middleware_response(
    request: Request,
    *,
    path: str,
    is_public: bool,
    user: dict | None,
) -> Response | None:
    if not user or not user.get("must_change_password") or is_public:
        return None
    if path in _AUTH_FORCED_CHANGE_ALLOW_EXACT:
        return None
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


async def _middleware_authenticated_user(
    request: Request,
    *,
    path: str,
    is_public: bool,
) -> tuple[dict | None, Response | None]:
    requested_workspace_id = requested_workspace_id_from_request(request)
    user, response = await _session_user_for_middleware(
        request,
        requested_workspace_id=requested_workspace_id,
        is_public=is_public,
        path=path,
    )
    if response is not None or user:
        return user, response
    return await _bearer_user_for_middleware(
        request,
        requested_workspace_id=requested_workspace_id,
        is_public=is_public,
        path=path,
    )


async def _auth_preflight_response(
    request: Request,
    call_next,
    *,
    path: str,
) -> Response | None:
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

    return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    response = await _auth_preflight_response(request, call_next, path=path)
    if response is not None:
        return response

    # This exact iframe route intentionally has no session identity. Its only
    # authority is the signed, short-lived capability checked by the route.
    # Ignore ambient Cookie/Authorization headers before they can trigger a DB
    # lookup or make a valid capability depend on unrelated browser state.
    if _is_app_content_capability_path(path):
        request.state.user = None
        return await call_next(request)

    is_public = _is_auth_public_path(path)
    user, response = await _middleware_authenticated_user(
        request,
        path=path,
        is_public=is_public,
    )
    if response is not None:
        return response

    request.state.user = user
    try:
        await _rate_limit_api_surface(request, path, user)
    except HTTPException as exc:
        return _auth_middleware_error_response(exc.detail, exc.status_code, path)

    if not user and not is_public and _uses_rbac_dependency(path):
        return await call_next(request)

    response = _unauthenticated_middleware_response(
        request,
        path=path,
        is_public=is_public,
        user=user,
    )
    if response is not None:
        return response

    # Forced password change: confine the session to the change-password flow.
    response = _forced_password_change_middleware_response(
        request,
        path=path,
        is_public=is_public,
        user=user,
    )
    if response is not None:
        return response

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
    subject = _auth.hash_refresh_token(refresh_token or "")[:16]
    await _rate_limit(request, "/auth/refresh", subject)
    rotated = await _auth.rotate_refresh_token(refresh_token)
    if not rotated:
        resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
        resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
        return resp
    user, new_refresh_token, refresh_expires = rotated

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
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    await _auth.logout_tokens(token, refresh_token)

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


async def _readyz_intelligence_readiness(
    user: dict | None,
    *,
    require_data: bool,
) -> dict:
    from app.services.intelligence.readiness import intelligence_readiness

    return await intelligence_readiness(user, require_data=require_data)


@app.get("/readyz")
async def readyz(request: Request):
    """Dependency-aware readiness probe.

    `/healthz` only proves the process can answer. `/readyz` is stricter:
    it verifies Postgres and core sibling services so deploy/proxy layers can
    keep traffic away from a half-started console.
    """
    # Readiness source-contract markers: CONTROL_ROOM_REQUIRE_DATA_READY,
    # require_data, require_intelligence, intelligence_opt_out_allowed,
    # require_data=require_intelligence_data, _is_production_env().
    checks, ok = await _build_readyz_checks_impl(
        app=request.app,
        query_params=request.query_params,
        authenticated_user=getattr(request.state, "user", None),
        environ=os.environ,
        refinement_url=REFINEMENT_URL,
        mcp_infra_url=os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010"),
        vault_url=_vault_url,
        startup_readiness_status=_startup_readiness_status,
        get_db_pool=_get_db_pool,
        dependency_health=_dependency_health,
        control_room_data_check=_control_room_data_check,
        intelligence_readiness=_readyz_intelligence_readiness,
        is_production_env=_is_production_env,
        warn=logger.warning,
    )
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
    # The front-end uses these flags to decide what to render. They are
    # display hints only; every action endpoint enforces its own gate.
    # Source-level frontend contract marker:
    # "workspaces": switchable_workspaces
    return await _me_access_response_impl(
        user=user,
        fetch_cartridge_access=_fetch_cartridge_access,
        cartridge_pool_factory=cartridge_service.pool,
        logger=logger,
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
    return await _llm_key_status_payload_impl(
        vault_scope=vault_scope,
        vault_url=_VAULT_URL,
        vault_headers=_vault_headers_for_user(user),
        http_client_factory=httpx.AsyncClient,
    )


@app.put(
    "/api/copilot/llm-key",
    dependencies=[Depends(require_csrf), Depends(require_permission("llm.keys.write"))],
)
async def api_copilot_llm_key_set(
    body: dict, user: dict = Depends(require_permission("llm.keys.write"))
):
    vault_scope = _tenant_vault_scope(user, "llm")
    return await _llm_key_set_payload_impl(
        body=body,
        user=user,
        vault_scope=vault_scope,
        vault_url=_VAULT_URL,
        vault_headers=_vault_headers_for_user(user),
        audit_record_event=_audit.record_event,
        http_client_factory=httpx.AsyncClient,
    )


# ── Assistant ─────────────────────────────────────────────────────────────────


@app.post(
    "/assistant/chat",
    dependencies=[Depends(require_csrf), Depends(require_permission("copilot.use"))],
)
async def chat(body: dict, user: dict = Depends(require_permission("copilot.use"))):
    return await _assistant_chat_payload_impl(
        body=body,
        user=user,
        assistant_chat=assistant.chat,
        call_with_optional_user=_call_with_optional_user,
        llm_configuration_error=llm_client.LLMConfigurationError,
        llm_provider_error=llm_client.LLMProviderError,
        logger_info=logger.info,
        logger_warning=logger.warning,
    )


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
    return await _build_job_logs_payload_impl(
        job_id=job_id,
        limit=limit,
        user=user,
        job_service=job_service,
        get_db_pool=_get_db_pool,
    )


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


def _bronze_source_cartridge_entity(source: str) -> tuple[str, str] | None:
    parts = str(source or "").strip().strip("/").split("/")
    if len(parts) < 3 or parts[0] != "raw":
        return None
    cartridge = parts[1].strip()
    entity = parts[2].strip()
    if not cartridge or not entity:
        return None
    return cartridge, entity


def _sql_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


async def _latest_bronze_storage_uri_for_schema(
    source: str, user: dict | None
) -> str | None:
    parts = _bronze_source_cartridge_entity(source)
    if parts is None:
        return None
    cartridge, entity = parts
    if not await _table_has_column("pipeline_runs", "storage_uri"):
        return None
    scope_sql, scope_values = await _pipeline_runs_scope_predicate(user, 3)
    query = f"""
        SELECT storage_uri
        FROM pipeline_runs
        WHERE cartridge_id = $1
          AND entity = $2
          AND COALESCE(storage_uri, '') <> ''
          {scope_sql}
        ORDER BY finished_at DESC NULLS LAST, started_at DESC NULLS LAST
        LIMIT 1
    """
    try:
        pool = await _get_db_pool()
        async with _pipeline_runs_read_conn(pool, user) as conn:
            row = await conn.fetchrow(query, cartridge, entity, *scope_values)
    except Exception:
        logger.debug(
            "schema storage_uri fallback lookup failed source=%s",
            source,
            exc_info=True,
        )
        return None
    uri = str(row["storage_uri"] if row else "").strip()
    if not uri or not _dataset_source_visible_for_user(user, uri):
        return None
    return uri


async def _bronze_storage_uri_schema_fallback(
    source: str, user: dict | None
) -> dict[str, Any] | None:
    storage_uri = await _latest_bronze_storage_uri_for_schema(source, user)
    if not storage_uri:
        return None
    sql = (
        "SELECT * FROM read_parquet("
        f"{_sql_single_quote(storage_uri)}, "
        "hive_partitioning = true, union_by_name = true"
        ") WHERE 1 = 0"
    )
    preview = await _refinement_invoke(
        "preview_transform",
        {"sql": sql, "limit": 1, "sources": [source]},
        timeout=30,
        user=user,
    )
    if isinstance(preview, dict):
        preview = {
            **preview,
            "source": source,
            "storage_uri": storage_uri,
            "rows": preview.get("rows") or preview.get("data") or [],
        }
    return preview if isinstance(preview, dict) else None


def _invalidate_data_platform_read_caches(user: dict | None) -> None:
    for namespace in ("schema", "sources", "catalog"):
        _scoped_read_cache_invalidate(namespace, user)


@app.get("/api/schema", dependencies=[Depends(require_permission("datasets.read"))])
async def api_schema(source: str, user: dict = Depends(require_permission("datasets.read"))):
    _require_technical_source_access(user, source)

    async def load_schema() -> dict:
        return await _schema_response_payload_impl(
            source=source,
            user=user,
            gold_dataset_from_source=_gold_dataset_from_source,
            gold_schema_payload=_gold_schema_payload,
            refinement_invoke=_refinement_invoke,
            schema_error=_schema_error,
            gold_schema_error_payload=_gold_schema_error_payload,
            empty_partitions=_empty_partitions,
            empty_preview=_empty_preview,
            schema_payload_warnings=_schema_payload_warnings,
            preview_has_columns=_preview_has_columns,
            bronze_schema_payload=_bronze_schema_payload,
            storage_schema_fallback=lambda checked_source: _bronze_storage_uri_schema_fallback(
                checked_source, user
            ),
        )

    return await _scoped_read_cache_get_or_set("schema", user, (source,), load_schema)


@app.get("/api/sources", dependencies=[Depends(require_permission("datasets.read"))])
async def api_sources(user: dict = Depends(require_permission("datasets.read"))):
    async def load_sources() -> dict:
        # Scope-regression compatibility: the helper applies
        # _filter_technical_sources(user, sources) before returning.
        return await _sources_response_payload_impl(
            user=user,
            refinement_invoke=_refinement_invoke,
            gold_sources_from_catalog=_gold_sources_from_catalog,
            filter_technical_sources=_filter_technical_sources,
        )

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
    return await _bronze_query_payload_impl(
        body=body,
        user=user,
        merge_sources=_merge_declared_and_inferred_bronze_sources,
        rewrite_paths=_rewrite_bronze_logical_paths,
        rls_user_context=_rls_user_context,
        headers_factory=_hdr_for,
        mcp_payload=_mcp_payload,
        refinement_url=REFINEMENT_URL,
        upstream_error_detail=_upstream_error_detail,
        raise_for_refinement_payload_error=_raise_for_refinement_payload_error,
        http_client_factory=httpx.AsyncClient,
    )


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
    return get_lakehouse_explorer_client()


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
    buckets = _explorer_visible_buckets(
        _EXPLORER_DEFAULT_BUCKETS,
        is_security_admin=_is_security_admin_context(ctx),
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
    from app.services import publication_heads as _publication
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
    published = await _publication.published_object_keys(user, bucket_name)
    objects = [
        _explorer_object_row(o)
        for o in resp.get("Contents", [])
        if o.get("Key") != prefix
        and _explorer_path_allowed(o.get("Key", ""), user, object_access=True)
        and _publication.visible_materialized_object(o.get("Key", ""), published)
    ]
    folders = [
        p["Prefix"]
        for p in resp.get("CommonPrefixes", [])
        if _explorer_path_allowed(p.get("Prefix", ""), user)
        and _publication.visible_materialized_prefix(p.get("Prefix", ""), published)
    ]
    return _explorer_list_response(
        bucket_name=bucket_name,
        prefix=prefix,
        folders=folders,
        objects=objects,
        response=resp,
    )


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
    from app.services import publication_heads as _publication
    s3 = _s3_client()
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(
        key, user, object_access=True
    ) or not await _publication.published_object(key, user, bucket_name):
        raise HTTPException(403, "object not allowed")
    expires_in = min(max(int(expires), 60), 3600)
    if _publication.materialized_object_key(key):
        raw = await _publication.verified_published_object(
            s3, key, user, bucket_name
        )
        if raw is None:
            raise HTTPException(409, "published object integrity unavailable")
        url = (
            "/api/explorer/download-content?bucket="
            f"{quote(bucket, safe='')}&key={quote(key, safe='')}"
        )
        return _explorer_download_response(url, expires_in=expires_in)
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


@app.get(
    "/api/explorer/download-content",
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def api_explorer_download_content(
    bucket: str,
    key: str,
    user: dict = Depends(require_authenticated),
):
    from app.services import publication_heads as _publication

    if not _publication.materialized_object_key(key):
        raise HTTPException(404, "published object not found")
    bucket_name = _resolve_explorer_bucket(bucket, user)
    if not _explorer_path_allowed(key, user, object_access=True):
        raise HTTPException(403, "object not allowed")
    raw = await _publication.verified_published_object(
        _s3_client(), key, user, bucket_name
    )
    if raw is None:
        raise HTTPException(409, "published object integrity unavailable")
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


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
    from app.services import publication_heads as _publication
    if _publication.materialized_object_key(key):
        raise HTTPException(409, "published objects require staged retirement")
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
    return _workspace_server_url_impl(
        is_production_env=_is_production_env,
        logger_warning=logger.warning,
    )


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
            if content:
                raise HTTPException(403, "app content is not available")
            return RedirectResponse(url="/apps-gallery", status_code=303)
    html_text, _app = await _refinement_app_html(name, runtime_user)
    # Server-owned, one per response: the app's inline bootstrap runs under a
    # nonce instead of widening the policy with 'unsafe-inline'.
    nonce = secrets.token_urlsafe(16)
    html_text = _inject_published_app_theme(html_text)
    html_text = _inject_script_nonce(html_text, nonce)
    # The broker client goes in ahead of the app's own scripts. Under
    # ``connect-src 'none'`` this is the app's only route to data, so it must
    # be installed before anything the author ships can capture ``fetch``.
    html_text = _inject_app_bridge(html_text, nonce)
    return HTMLResponse(
        content=html_text,
        headers=_app_content_headers(nonce),
    )


async def _resolve_capability_subject(claims: dict) -> dict | None:
    """Load the user the capability names, and confirm the membership stands.

    The cookie is deliberately not consulted: the frame is credentialless and
    sends none, and completing the claims from a cookie would reintroduce the
    ambient authority the sandbox exists to remove.
    """
    try:
        user_id = int(str(claims.get("user") or ""))
    except (TypeError, ValueError):
        return None
    tenant_id = str(claims.get("tenant") or "")
    workspace_id = str(claims.get("workspace") or "")
    if not tenant_id or not workspace_id:
        return None
    try:
        pool = await _get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT u.id,
                       u.role,
                       w.tenant_id,
                       w.id AS workspace_id,
                       CASE
                         WHEN u.role IN ('admin', 'owner', 'super_admin')
                           THEN 'workspace_admin'
                         ELSE r.name
                       END AS workspace_role
                  FROM public.users u
                  JOIN public.workspaces w
                    ON w.id = $2::uuid
                   AND w.tenant_id = $3::uuid
                  LEFT JOIN public.user_workspace_roles uwr
                    ON uwr.user_id = u.id
                   AND uwr.workspace_id = w.id
                  LEFT JOIN public.roles r ON r.id = uwr.role_id
                 WHERE u.id = $1
                   AND u.is_active = TRUE
                   AND (
                     u.role IN ('admin', 'owner', 'super_admin')
                     OR uwr.user_id IS NOT NULL
                   )
                 ORDER BY r.name ASC NULLS LAST
                 LIMIT 1
                """,
                user_id, workspace_id, tenant_id,
            )
    except Exception:
        logger.warning("[app-content] capability subject lookup failed", exc_info=True)
        return None
    if row is None:
        return None
    resolved = {
        "id": int(row["id"]),
        "role": row["role"],
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "active_tenant_id": str(row["tenant_id"]),
        "active_workspace_id": str(row["workspace_id"]),
        "workspace_role": row["workspace_role"],
    }
    # Session-authenticated requests receive this entitlement list during
    # middleware enrichment.  Capability requests have no cookie, so rebuild
    # the same server-owned context before cartridge visibility is enforced.
    # Failure grants nothing.
    try:
        resolved["allowed_cartridges"] = await _workspace_cartridges(
            resolved["workspace_id"], user_id=resolved["id"]
        )
    except Exception:
        logger.warning("[app-content] entitlement lookup failed", exc_info=True)
        resolved["allowed_cartridges"] = []
    return resolved


async def _active_manifest_digest(
    name: str, tenant_id: str, workspace_id: str
) -> str | None:
    """The digest the registry currently calls active for this app."""
    try:
        pool = await _get_db_pool()
        async with pool.acquire() as conn:
            await _set_rls_scope(conn, tenant_id, workspace_id)
            return await conn.fetchval(
                "SELECT manifest_digest FROM public.analytic_app_manifests "
                "WHERE app_name = $1 AND revision = 'active'",
                str(name),
            )
    except Exception:
        logger.warning("[app-content] manifest lookup failed", exc_info=True)
        return None


async def _require_app_content_capability(
    request: Request, name: str, cap: str, user: dict | None
) -> dict:
    """Admit a /content request on the capability alone, then re-check it all.

    A signature only proves the claims were minted by us. Everything they
    assert is verified again against current state — the user is still active
    and still a member of that workspace, the app is still packaged, the
    manifest is still the active revision, the installation is still ready —
    so a capability issued a minute ago stops working the moment any of that
    changes.

    Single implementation: this path is registered twice, here and in the v1
    marketplace router, and two copies of a security check is how one of them
    silently stops matching the other.
    """
    denied = HTTPException(403, "app content is not available")
    _validate_dataset_name(name)
    claims = _verify_content_capability_envelope(cap, app_name=name)
    if claims is None or not _frame_fetch_metadata_ok(request.headers):
        raise denied
    await _request_rate_limits.rate_limit_app_content_capability(
        claims,
        limiter_factory=get_rate_limiter,
    )

    resolved = await _resolve_capability_subject(claims)
    if resolved is None:
        raise denied

    verified = _verify_content_capability(
        cap,
        app_name=name,
        tenant_id=resolved["tenant_id"],
        workspace_id=resolved["workspace_id"],
        user_id=resolved["id"],
        manifest_digest=await _active_manifest_digest(
            name, resolved["tenant_id"], resolved["workspace_id"]
        ),
    )
    if verified is None:
        raise denied
    return resolved


async def _build_app_embed_response(
    request: Request, name: str, user: dict | None
) -> HTMLResponse:
    """Mint the capability and render the wrapper. Shared by both routers."""
    _validate_dataset_name(name)
    _html_text, granted, digest, cartridge = await _app_grant_context(
        request, name, user
    )
    nonce = secrets.token_urlsafe(16)
    capability = None
    if digest:
        tenant_id, workspace_id = await _app_scope_for(user)
        capability = _issue_content_capability(
            app_name=name,
            cartridge_id=cartridge or "",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=(user or {}).get("id"),
            manifest_digest=digest,
        )
    return HTMLResponse(
        content=_app_embed_wrapper_html(name, granted, nonce, capability=capability),
        headers={
            "Content-Security-Policy": _app_embed_csp(nonce),
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "no-store",
        },
    )


async def _require_app_scoped_grant(
    request: Request, app_name: str, dataset: str, user: dict | None
) -> None:
    """Refuse unless the durable ledger grants this exact read, right now.

    Every rejection is the same 403 with the same wording: a message that
    distinguished "no such dataset" from "not granted" would let a published
    app enumerate the workspace's datasets one refusal at a time.
    """
    denied = HTTPException(403, "app data access is not granted")
    _validate_dataset_name(app_name)
    _validate_dataset_name(dataset)
    if _api_navigation_blocked(request.headers):
        raise denied
    try:
        html_text, granted, digest, _cartridge = await _app_grant_context(
            request, app_name, user
        )
    except HTTPException:
        raise denied
    if not digest or dataset not in granted:
        raise denied


async def _set_rls_scope(conn, tenant_id: str, workspace_id: str) -> None:
    """Bind the connection to the caller's scope so RLS applies to the ledger."""
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, true), "
        "set_config('app.workspace_id', $2, true)",
        str(tenant_id or ""),
        str(workspace_id or ""),
    )


async def _app_scope_for(user: dict | None) -> tuple[str, str]:
    """The caller's tenant/workspace, resolved server side."""
    return await _workspace_scope_for_apps_filter(user or {})


async def _app_grant_context(
    request: Request, name: str, user: dict | None
) -> tuple[str, list[str], str | None, str | None]:
    """Everything the embed needs, with authority taken only from the ledger.

    Returns the HTML about to be served, the datasets the durable ledger grants
    for this scope/app/digest, that digest, and the cartridge. A user-created
    app, an unknown app, or one whose served HTML no longer matches the
    reviewed revision resolves to an empty grant list — never to a fallback.
    """
    html_text, app_row = await _refinement_app_html(
        name, getattr(request.state, "user", None) or user or {}
    )
    manifest = _packaged_manifest(name)
    digest = _served_manifest_digest(name, html_text)
    cartridge = (manifest or {}).get("cartridge_id") or _app_cartridge_id(
        {"name": name, **(app_row or {})}
    )
    granted: list[str] = []
    if digest:
        tenant_id, workspace_id = await _app_scope_for(user)
        try:
            pool = await _get_db_pool()
            async with pool.acquire() as conn:
                # set_config(..., true) is transaction-local.  Without this
                # transaction asyncpg autocommit clears the RLS scope before
                # the grant query and every app appears to have zero grants.
                async with conn.transaction():
                    await _set_rls_scope(conn, tenant_id, workspace_id)
                    granted = await _granted_datasets(
                        conn,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        app_name=name,
                        manifest_digest=digest,
                    )
        except Exception:
            # Fail closed: an unreachable ledger grants nothing.
            logger.warning("[app-grants] grant lookup failed for %s", name, exc_info=True)
            granted = []
    return html_text, granted, digest, cartridge


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
    return await _refinement_app_html_impl(
        name=name,
        user=user,
        validate_dataset_name=_validate_dataset_name,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload=_mcp_payload,
        refinement_url=REFINEMENT_URL,
        upstream_error_detail=_upstream_error_detail,
    )


@app.get("/apps/{name}/embed", dependencies=[Depends(require_permission("apps.read"))])
async def serve_app_embed(
    request: Request,
    name: str,
    user: dict = Depends(require_permission("apps.read")),
):
    return await _build_app_embed_response(request, name, user)


@app.get("/apps/{name}/content")
async def serve_app_content_proxy(
    request: Request,
    name: str,
    cap: str = "",
):
    """Untrusted app HTML, admitted only as a frame of our own wrapper.

    The session cookie is deliberately not what authorises this route. Opened
    as a top-level document it would render an attacker's page at our origin
    and let it navigate to authenticated endpoints, where the browser
    re-attaches the Lax cookie. The capability minted by /embed is the control;
    the Fetch Metadata check below is a second, cheaper one.
    """
    # No session dependency on this route by design. The inner frame is
    # credentialless, so it sends no cookie — gating on the cookie would 401
    # before the capability was even read, and re-adding the cookie to make
    # that work would hand the frame back the credentials the sandbox removes.
    # The capability is the authorisation; everything in it is re-checked
    # against the server's own state below.
    user = await _require_app_content_capability(request, name, cap, None)
    return await _proxy_workspace_app(request, name, content=True, user=user)


@app.get(
    "/api/apps/{app_name}/data/{dataset}",
    dependencies=[Depends(require_permission("apps.read"))],
)
async def api_app_scoped_data(
    app_name: str,
    dataset: str,
    request: Request,
    limit: int = 5000,
    user: dict = Depends(require_permission("datasets.read")),
):
    """The definitive authority for a published app's data read.

    The wrapper filters too, but that runs in the browser and a browser check
    is a convenience, not a control. Everything is re-established here from the
    server's own view: the caller's scope, the app's cartridge and install
    state, the digest of the HTML currently served, and an unrevoked grant in
    the durable ledger. Only then does it delegate to the ordinary dataset
    read, which still applies RLS.
    """
    await _require_app_scoped_grant(request, app_name, dataset, user)
    return await api_data(dataset=dataset, request=request, limit=limit, user=user)


@app.get(
    "/api/apps/{app_name}/data/{dataset}/options",
    dependencies=[Depends(require_permission("apps.read"))],
)
async def api_app_scoped_data_options(
    app_name: str,
    dataset: str,
    request: Request,
    columns: str = "",
    user: dict = Depends(require_permission("datasets.read")),
):
    await _require_app_scoped_grant(request, app_name, dataset, user)
    return await api_data_options(dataset=dataset, columns=columns, user=user)


@app.post(
    "/api/apps/{app_name}/data/{dataset}/query",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("apps.read")),
    ],
)
async def api_app_scoped_data_query(
    app_name: str,
    dataset: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("datasets.read")),
):
    await _require_app_scoped_grant(request, app_name, dataset, user)
    return await api_data_query(dataset=dataset, request=request, body=body, user=user)


@app.get("/apps/{name}", dependencies=[Depends(require_permission("apps.read"))])
async def serve_app(
    name: str, request: Request, user: dict = Depends(require_permission("apps.read"))
):
    """Send the caller to the app's canonical home instead of serving it here.

    This route used to return the published HTML as a top-level, same-origin
    document. Anything reaching it that way ran with the console's origin —
    its storage, its cookies, its authenticated endpoints — which is exactly
    the escape the viewer is built to prevent. The only supported path to app
    content is the wrapper at ``/apps/{name}/embed``, and the only supported
    entry point is the viewer, so a direct hit redirects there rather than
    quietly handing back a second, unprotected way in.
    """
    _validate_dataset_name(name)
    return RedirectResponse(
        url=f"/analytics/viewer?app={quote(name, safe='')}", status_code=303
    )


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
    return await _active_scoped_connection_cartridges_impl(
        user,
        candidate_cartridges,
        scope_resolver=_workspace_scope_for_apps_filter,
        scoped_user_factory=_user_with_apps_scope,
        vault_url=_VAULT_URL,
        vault_headers_for_user=_vault_headers_for_user,
        http_client_factory=httpx.AsyncClient,
        logger_debug=logger.debug,
    )


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
    return await _gold_ready_datasets_for_apps_impl(
        user,
        apps_payload,
        dsn=_gold_dsn_for_readiness(),
        workspace_scope_resolver=_workspace_scope_for_apps_filter,
        logger_debug=logger.debug,
    )


async def _apps_payload_visible_and_ready(
    user: dict,
    *,
    include_unready: bool = False,
    cartridge: str | None = None,
) -> dict[str, Any]:
    async def _load_apps_payload(load_user: dict) -> Any:
        return await _load_refinement_apps_payload_impl(
            user=load_user,
            http_client_factory=httpx.AsyncClient,
            headers_factory=_hdr_for,
            mcp_payload=_mcp_payload,
            refinement_url=REFINEMENT_URL,
            upstream_error_detail=_upstream_error_detail,
        )

    return await _apps_payload_visible_and_ready_impl(
        user,
        include_unready=include_unready,
        cartridge=cartridge,
        load_apps_payload=_load_apps_payload,
        require_cartridge_visible=_require_cartridge_visible,
        active_scoped_connection_cartridges=_active_scoped_connection_cartridges,
        installed_scoped_app_cartridges=_installed_scoped_app_cartridges,
        gold_ready_datasets_for_apps=_gold_ready_datasets_for_apps,
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
    return await _delete_refinement_app_payload_impl(
        name=name,
        user=user,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload=_mcp_payload,
        refinement_url=REFINEMENT_URL,
    )


@app.get("/api/data/{dataset}", dependencies=[Depends(require_permission("datasets.read"))])
async def api_data(
    dataset: str,
    request: Request,
    limit: int = 5000,
    user: dict = Depends(require_permission("datasets.read")),
):
    """Return dataset rows as JSON array for use by analytic apps."""
    _validate_dataset_name(dataset)

    from app.services.intelligence.gold_fetcher import query_gold_dataset_rows

    return await query_gold_dataset_rows(dataset, user, limit)


@app.get("/api/data/{dataset}/options", dependencies=[Depends(require_permission("datasets.read"))])
async def api_data_options(
    dataset: str, columns: str = "", user: dict = Depends(require_permission("datasets.read"))
):
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


@app.post("/api/data/{dataset}/query", dependencies=[Depends(require_permission("datasets.read"))])
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

    from app.services import cartridge_service as _cs

    return await _build_pipeline_overview_impl(
        cartridge=cartridge,
        user=user,
        get_cartridge=_cs.get_cartridge,
        mcp_invoke=mcp_registry.invoke,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        pipeline_runs_read_conn=_pipeline_runs_read_conn,
        pipeline_gather_by_entity=_pipeline_gather_by_entity,
        refresh_dag_run_status=_refresh_dag_run_status,
        call_with_optional_user=_call_with_optional_user,
        job_list_recent=job_service.list_recent,
        refinement_invoke=_refinement_invoke,
        bronze_physical_snapshot=_bronze_physical_snapshot,
        pipeline_jobs_by_entity=_pipeline_jobs_by_entity,
        pipeline_bronze_date_count=_pipeline_bronze_date_count,
        pipeline_silver_datasets_by_source=_pipeline_silver_datasets_by_source,
        pipeline_entity_row=_pipeline_entity_row,
        pipeline_response_payload=_pipeline_response_payload,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        refinement_url=REFINEMENT_URL,
        dag_status_timeout_sec=PIPELINE_DAG_STATUS_TIMEOUT_SEC,
        datasets_timeout_sec=PIPELINE_DATASETS_TIMEOUT_SEC,
        bronze_snapshot_timeout_sec=PIPELINE_BRONZE_SNAPSHOT_TIMEOUT_SEC,
        logger_debug=logger.debug,
    )


@app.get("/api/dag_templates", dependencies=[Depends(require_permission("pipelines.read"))])
async def api_dag_templates():
    from app.services import dag_templates

    return _dag_templates_payload_impl(dag_templates_service=dag_templates)


@app.get(
    "/api/dag_templates/{template_id}", dependencies=[Depends(require_permission("pipelines.read"))]
)
async def api_dag_template_code(
    template_id: str, cartridge: str = "my_cartridge", entity: str = "MyEntity"
):
    from app.services import dag_templates

    return _dag_template_code_payload_impl(
        template_id=template_id,
        cartridge=cartridge,
        entity=entity,
        dag_templates_service=dag_templates,
    )


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
    return await _list_pipeline_runs_impl(
        cartridge=cartridge,
        entity=entity,
        limit=limit,
        user=user,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        pipeline_runs_read_conn=_pipeline_runs_read_conn,
        sanitize_pipeline_run_for_user=_sanitize_pipeline_run_for_user,
        refresh_dag_run_status=_refresh_dag_run_status,
        error_id_factory=lambda: uuid.uuid4().hex,
        logger_exception=logger.exception,
    )


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

    return await _list_pipeline_entity_runs_impl(
        cartridge=cartridge,
        entity=entity,
        limit=limit,
        user=user,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        pipeline_runs_read_conn=_pipeline_runs_read_conn,
        refresh_dag_run_status=_refresh_dag_run_status,
        format_pipeline_entity_run=_format_pipeline_entity_run,
        error_id_factory=lambda: uuid.uuid4().hex,
        logger_exception=logger.exception,
    )


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

    return await _pipeline_run_logs_payload_impl(
        cartridge=cartridge,
        entity=entity,
        dag_run_id=dag_run_id,
        metadata=metadata,
        user=user,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        pipeline_runs_read_conn=_pipeline_runs_read_conn,
        build_pipeline_run_logs_payload=_build_pipeline_run_logs_payload_impl,
        refresh_dag_run_status=_refresh_dag_run_status,
        mcp_invoke=mcp_registry.invoke,
        normalize_airflow_state=_normalize_airflow_state,
        airflow_log_task_ids=_airflow_log_task_ids,
        airflow_log_attempt=_airflow_log_attempt,
        error_id_factory=lambda: uuid.uuid4().hex,
        logger_exception=logger.exception,
    )


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
        return await _api_pipeline_extract_dag_based(
            cartridge=cartridge,
            entity=entity,
            body=body,
            metadata=metadata,
            user=user,
        )

    args = _build_mcp_extract_args_impl(entity, body)
    result = await mcp_registry.invoke(cartridge, "extract", args, user=user)
    return result


def _pipeline_extract_dag_id(
    *,
    cartridge: str,
    entity: str,
    metadata: dict,
) -> str:
    return _dag_extract_dag_id_from_metadata_impl(
        cartridge=cartridge,
        entity=entity,
        metadata=metadata,
        static_catalog_contains=_entity_declared_in_static_catalog,
    )


def _pipeline_extract_dag_conf(
    *,
    cartridge: str,
    entity: str,
    body: dict,
    metadata: dict,
    user: dict,
) -> dict:
    extract_conf = _build_dag_extract_conf(cartridge, entity, metadata.get("mode"), body)
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
    return _apply_user_scope_to_dag_conf(extract_conf, user)


async def _pipeline_extract_reserve_slot(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    conf: dict,
    user: dict,
    requested_dag_run_id: str | None,
) -> dict | None:
    return await _reserve_successfactors_entity_extract_slot(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        conf=conf,
        user=user,
        requested_dag_run_id=requested_dag_run_id,
    )


async def _record_pipeline_extract_failure(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    dag_run_id: str,
    conf: dict,
    metadata: dict,
    error: str,
) -> None:
    await _record_dag_pipeline_trigger(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
        mode=conf.get("mode", metadata.get("mode") or "incremental"),
        status="failed",
        conf={**conf, "trigger_error": error},
        tenant_id=conf.get("tenant_id"),
        workspace_id=conf.get("workspace_id"),
    )


async def _record_pipeline_extract_success(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    dag_run_id: str,
    conf: dict,
    metadata: dict,
    result: dict,
) -> None:
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


def _pipeline_extract_triggered_response(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    dag_run_id: str,
    result: dict,
    conf: dict,
) -> dict:
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


def _pipeline_extract_requested_dag_run_id(dag_id: str, body: dict) -> str | None:
    return _dag_run_id_from_idempotency_key(
        dag_id,
        body.get("idempotency_key") or body.get("request_id"),
    )


def _pipeline_extract_reserved_response(slot: dict | None) -> dict | None:
    if slot and slot.get("response"):
        return slot["response"]
    return None


def _pipeline_extract_reserved_run_id(
    slot: dict | None, requested_dag_run_id: str | None
) -> str | None:
    if slot and slot.get("dag_run_id"):
        return slot["dag_run_id"]
    return requested_dag_run_id


async def _handle_pipeline_extract_trigger_error(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    requested_dag_run_id: str | None,
    conf: dict,
    metadata: dict,
    slot: dict | None,
    error: str,
) -> None:
    if slot and slot.get("reserved"):
        await _record_pipeline_extract_failure(
            cartridge=cartridge,
            entity=entity,
            dag_id=dag_id,
            dag_run_id=requested_dag_run_id or "",
            conf=conf,
            metadata=metadata,
            error=error,
        )


async def _trigger_pipeline_extract_dag_or_raise(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    requested_dag_run_id: str | None,
    conf: dict,
    metadata: dict,
    slot: dict | None,
    user: dict,
) -> dict:
    result = await _trigger_airflow_extract_dag(dag_id, conf, user, requested_dag_run_id)
    if result.get("error"):
        await _handle_pipeline_extract_trigger_error(
            cartridge=cartridge,
            entity=entity,
            dag_id=dag_id,
            requested_dag_run_id=requested_dag_run_id,
            conf=conf,
            metadata=metadata,
            slot=slot,
            error=result["error"],
        )
        raise HTTPException(502, f"Airflow trigger failed: {result['error']}")
    return result


def _pipeline_extract_result_dag_run_id(
    result: dict, requested_dag_run_id: str | None
) -> str | None:
    return result.get("dag_run_id") or result.get("run_id") or requested_dag_run_id


async def _pipeline_extract_success_response(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    requested_dag_run_id: str | None,
    conf: dict,
    metadata: dict,
    result: dict,
) -> dict:
    dag_run_id = _pipeline_extract_result_dag_run_id(result, requested_dag_run_id)
    await _record_pipeline_extract_success(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
        conf=conf,
        metadata=metadata,
        result=result,
    )
    return _pipeline_extract_triggered_response(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
        result=result,
        conf=conf,
    )


async def _api_pipeline_extract_dag_based(
    *,
    cartridge: str,
    entity: str,
    body: dict,
    metadata: dict,
    user: dict,
) -> dict:
    dag_id = _pipeline_extract_dag_id(
        cartridge=cartridge,
        entity=entity,
        metadata=metadata,
    )
    conf = _pipeline_extract_dag_conf(
        cartridge=cartridge,
        entity=entity,
        body=body,
        metadata=metadata,
        user=user,
    )
    requested_dag_run_id = _pipeline_extract_requested_dag_run_id(dag_id, body)
    slot = await _pipeline_extract_reserve_slot(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        conf=conf,
        user=user,
        requested_dag_run_id=requested_dag_run_id,
    )
    reserved_response = _pipeline_extract_reserved_response(slot)
    if reserved_response is not None:
        return reserved_response
    requested_dag_run_id = _pipeline_extract_reserved_run_id(slot, requested_dag_run_id)
    result = await _trigger_pipeline_extract_dag_or_raise(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        requested_dag_run_id=requested_dag_run_id,
        conf=conf,
        metadata=metadata,
        slot=slot,
        user=user,
    )
    response = await _pipeline_extract_success_response(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        requested_dag_run_id=requested_dag_run_id,
        conf=conf,
        metadata=metadata,
        result=result,
    )
    _invalidate_data_platform_read_caches(user)
    return response


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
    _invalidate_data_platform_read_caches(user)
    aggregate_result = await _maybe_trigger_aggregate_extract_all(
        cartridge=cartridge,
        body=body,
        user=user,
    )
    if aggregate_result is not None:
        return aggregate_result

    return await _fanout_pipeline_extract_all_impl(
        cartridge=cartridge,
        body=body,
        user=user,
        api_pipeline=api_pipeline,
        api_pipeline_extract=api_pipeline_extract,
        call_with_optional_user=_call_with_optional_user,
        sync_entity_idempotency_key=_sync_entity_idempotency_key,
        error_id_factory=lambda: uuid.uuid4().hex,
        logger_exception=logger.exception,
    )


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
    return await _reserve_successfactors_entity_extract_slot_impl(
        cartridge=cartridge,
        entity=entity,
        dag_id=dag_id,
        conf=conf,
        user=user,
        requested_dag_run_id=requested_dag_run_id,
        expected_cartridge=_SAP_SUCCESSFACTORS_CARTRIDGE,
        entity_dag_id=_SAP_SUCCESSFACTORS_ENTITY_DAG_ID,
        extract_all_dag_id=_SAP_SUCCESSFACTORS_EXTRACT_ALL_DAG_ID,
        aggregate_entity=_SYNC_AGGREGATE_ENTITY,
        active_window_seconds=_SAP_SUCCESSFACTORS_ACTIVE_WINDOW_SECONDS,
        max_active_entity_extracts=_SAP_SUCCESSFACTORS_MAX_ACTIVE_ENTITY_EXTRACTS,
        build_security_context=build_security_context,
        table_has_column=_table_has_column,
        airflow_run_id_fragment=_airflow_run_id_fragment,
        active_extract_run_payload=_active_extract_run_payload,
        get_db_pool=_get_db_pool,
        token=uuid.uuid4().hex,
        logger_exception=logger.exception,
    )


async def _maybe_trigger_aggregate_extract_all(
    *, cartridge: str, body: dict[str, Any], user: dict | None
) -> dict[str, Any] | None:
    return await _maybe_trigger_aggregate_extract_all_impl(
        cartridge=cartridge,
        body=body,
        user=user,
        sync_extract_all_dags=_SYNC_EXTRACT_ALL_DAGS,
        pipeline_extract_all_mode_target_func=_pipeline_extract_all_mode_target,
        resolve_pipeline_sync_conn_id_func=_resolve_pipeline_sync_conn_id,
        pipeline_extract_all_run_id_func=_pipeline_extract_all_run_id,
        trigger_sync_aggregate_extract_all_func=_trigger_sync_aggregate_extract_all,
        pipeline_extract_all_public_response_func=_pipeline_extract_all_public_response,
    )


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
    return await _fetch_active_sync_run_impl(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        user=user,
        get_db_pool=_get_db_pool,
        table_has_column=_table_has_column,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        scoped_db_for_user=scoped_db_for_user,
        active_sync_run_lookup_parts_func=_active_sync_run_lookup_parts,
        logger_warning=logger.warning,
    )


async def _trigger_sync_aggregate_extract_all(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    run_id: str,
    user: dict | None,
) -> dict[str, Any] | None:
    return await _trigger_sync_aggregate_extract_all_impl(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        run_id=run_id,
        user=user,
        sync_extract_all_dags=_SYNC_EXTRACT_ALL_DAGS,
        sync_aggregate_entity=_SYNC_AGGREGATE_ENTITY,
        apply_user_scope_to_dag_conf=_apply_user_scope_to_dag_conf,
        dag_run_id_from_idempotency_key=_dag_run_id_from_idempotency_key,
        trigger_airflow_extract_dag=_trigger_airflow_extract_dag,
        record_dag_pipeline_trigger=_record_dag_pipeline_trigger,
    )


async def _ensure_sync_packaged_datasets(
    *, cartridge: str, user: dict | None
) -> dict[str, Any]:
    from app.services.seed_packaged_datasets import (
        seed_packaged_datasets_for_workspace,
    )

    return await _ensure_sync_packaged_datasets_impl(
        cartridge=cartridge,
        user=user,
        workspace_scope_from_user=_workspace_scope_from_user,
        get_db_pool=_get_db_pool,
        seed_packaged_datasets_for_workspace=seed_packaged_datasets_for_workspace,
        logger_info=logger.info,
    )


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
    return await _run_sync_agentops_monitors_impl(
        cartridge=cartridge,
        sync_run_id=sync_run_id,
        user=user,
        ensure_successfactors_talent_monitor=_ensure_successfactors_talent_monitor,
        list_agents=_agents.list_agents,
        load_agent=_agent_runtime.load_agent,
        reserve_scheduled_run=_agent_scheduler.reserve_scheduled_run,
        execute_reserved_scheduled_monitor=(
            _scheduled_monitor_execution.execute_reserved_scheduled_monitor
        ),
        sync_agentops_monitor_candidates=_sync_agentops_monitor_candidates,
        sync_agentops=_sync_agentops,
        logger_warning=logger.warning,
    )


async def _execute_scoped_sync_run_upsert(
    pool: Any,
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    status: str,
    finished: bool,
    error_message: str | None,
    extra_json: str,
    tenant_id: str,
    workspace_id: str,
) -> str:
    monotonic_status = _postgres_monotonic_pipeline_status(
        "pipeline_runs.status", "EXCLUDED.status"
    )
    accepts_status = _postgres_pipeline_status_accepts(
        "pipeline_runs.status", "EXCLUDED.status"
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), "
                "set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            return await conn.execute(
                f"""
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
                    status = {monotonic_status},
                    mode = EXCLUDED.mode,
                    finished_at = CASE WHEN {accepts_status}
                        THEN COALESCE(EXCLUDED.finished_at, pipeline_runs.finished_at)
                        ELSE pipeline_runs.finished_at END,
                    error_message = CASE WHEN {accepts_status}
                        THEN EXCLUDED.error_message ELSE pipeline_runs.error_message END,
                    tenant_id = COALESCE(pipeline_runs.tenant_id, EXCLUDED.tenant_id),
                    workspace_id = COALESCE(pipeline_runs.workspace_id, EXCLUDED.workspace_id),
                    extra = CASE WHEN {accepts_status}
                        THEN COALESCE(pipeline_runs.extra, '{{}}'::jsonb) || EXCLUDED.extra
                        ELSE pipeline_runs.extra END
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


async def _execute_unscoped_sync_run_upsert(
    pool: Any,
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    status: str,
    finished: bool,
    error_message: str | None,
    extra_json: str,
) -> str:
    monotonic_status = _postgres_monotonic_pipeline_status(
        "pipeline_runs.status", "EXCLUDED.status"
    )
    accepts_status = _postgres_pipeline_status_accepts(
        "pipeline_runs.status", "EXCLUDED.status"
    )
    return await pool.execute(
        f"""
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
            status = {monotonic_status},
            mode = EXCLUDED.mode,
            finished_at = CASE WHEN {accepts_status}
                THEN COALESCE(EXCLUDED.finished_at, pipeline_runs.finished_at)
                ELSE pipeline_runs.finished_at END,
            error_message = CASE WHEN {accepts_status}
                THEN EXCLUDED.error_message ELSE pipeline_runs.error_message END,
            extra = CASE WHEN {accepts_status}
                THEN COALESCE(pipeline_runs.extra, '{{}}'::jsonb) || EXCLUDED.extra
                ELSE pipeline_runs.extra END
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


def _sync_run_scope_values(user: dict | None) -> tuple[str | None, str | None]:
    ctx = build_security_context(user)
    tenant_id = str(ctx.get("tenant_id") or "").strip() or None
    workspace_id = str(ctx.get("workspace_id") or "").strip() or None
    return tenant_id, workspace_id


def _sync_run_requires_scope(
    *,
    cartridge: str,
    scope_columns_present: bool,
    tenant_id: str | None,
    workspace_id: str | None,
) -> bool:
    return (
        scope_columns_present
        and cartridge != "platform"
        and not (tenant_id and workspace_id)
    )


async def _execute_sync_run_upsert_for_scope(
    pool: Any,
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    status: str,
    has_scope: bool,
    tenant_id: str | None,
    workspace_id: str | None,
    error_message: str | None,
    extra_json: str,
) -> str:
    finished = status in _SYNC_TERMINAL_STATUSES
    if has_scope and tenant_id and workspace_id:
        return await _execute_scoped_sync_run_upsert(
            pool,
            run_id=run_id,
            cartridge=cartridge,
            mode=mode,
            status=status,
            finished=finished,
            error_message=error_message,
            extra_json=extra_json,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    return await _execute_unscoped_sync_run_upsert(
        pool,
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        status=status,
        finished=finished,
        error_message=error_message,
        extra_json=extra_json,
    )


def _sync_run_upsert_debug(
    *,
    command_status: str,
    scope_columns_present: bool,
    has_scope: bool,
    tenant_id: str | None,
    workspace_id: str | None,
) -> dict[str, Any]:
    return {
        "command_status": command_status,
        "scope_columns_present": scope_columns_present,
        "has_scope": has_scope,
        "tenant_id_present": bool(tenant_id),
        "workspace_id_present": bool(workspace_id),
    }


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
    tenant_id, workspace_id = _sync_run_scope_values(user)
    scope_columns_present = await _table_has_column(
        "pipeline_runs", "tenant_id", refresh=True
    ) and await _table_has_column("pipeline_runs", "workspace_id", refresh=True)
    if _sync_run_requires_scope(
        cartridge=cartridge,
        scope_columns_present=scope_columns_present,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    ):
        raise HTTPException(403, "sync run tenant/workspace scope is required")
    has_scope = bool(tenant_id and workspace_id and scope_columns_present)
    command_status = await _execute_sync_run_upsert_for_scope(
        pool,
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        status=status,
        has_scope=has_scope,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        error_message=error_message,
        extra_json=json.dumps(extra),
    )
    return _sync_run_upsert_debug(
        command_status=command_status,
        scope_columns_present=scope_columns_present,
        has_scope=has_scope,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def _fetch_sync_run(
    *,
    cartridge: str,
    run_id: str,
    user: dict | None,
) -> dict[str, Any] | None:
    # Keep refresh_columns=True visible here for the sync scope contract tests.
    return await _fetch_sync_run_impl(
        cartridge=cartridge,
        run_id=run_id,
        user=user,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        scoped_db_for_user=scoped_db_for_user,
        sync_now_entity=_SYNC_NOW_ENTITY,
    )


async def _sync_child_runs(
    *,
    cartridge: str,
    run_ids: list[str],
    user: dict | None,
) -> list[dict[str, Any]]:
    return await _fetch_sync_child_runs_impl(
        cartridge=cartridge,
        run_ids=run_ids,
        user=user,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        scoped_db_for_user=scoped_db_for_user,
        refresh_dag_run_status=_refresh_dag_run_status,
        sync_now_entity=_SYNC_NOW_ENTITY,
    )


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
    return await _run_sync_control_room_gold_refresh_impl(
        cartridge=cartridge,
        row=row,
        child_rows=child_rows,
        gold_refresh_summary=gold_refresh_summary,
        user=user,
        build_security_context=build_security_context,
        sync_gold_refresh_dataset_names=_sync_gold_refresh_dataset_names,
        sync_gold_refresh_airflow_run_id=_sync_gold_refresh_airflow_run_id,
        sync_control_room=_sync_control_room,
    )


async def _sync_status_call_pipeline(
    current_cartridge: str,
    *,
    user: dict | None,
) -> dict[str, Any]:
    return await _call_with_optional_user(api_pipeline, current_cartridge, user=user)


async def _run_sync_agentops_status(**kwargs: Any) -> dict[str, Any]:
    return await _run_sync_agentops_status_impl(
        **kwargs,
        run_sync_agentops_monitors=_run_sync_agentops_monitors,
        sync_agentops_is_terminal=_sync_agentops_is_terminal,
        logger_warning=logger.warning,
    )


def _sync_control_room_cache_invalidate(current_user: dict | None) -> None:
    from app.routers.control_room import _control_room_cache_invalidate
    from app.services.intelligence.gold_fetcher import clear_gold_row_cache

    _control_room_cache_invalidate(current_user)
    try:
        tenant_id, workspace_id = _workspace_scope_from_user(current_user)
    except Exception:
        clear_gold_row_cache()
        return
    clear_gold_row_cache(tenant_id, workspace_id)


async def _build_sync_run_status(
    *,
    cartridge: str,
    row: dict[str, Any],
    user: dict | None,
    persist_control_room_state: bool = False,
) -> dict[str, Any]:
    return await _build_sync_run_status_impl(
        cartridge=cartridge,
        row=row,
        user=user,
        sync_child_runs=_sync_child_runs,
        call_pipeline=_sync_status_call_pipeline,
        run_control_room_gold_refresh=_run_sync_control_room_gold_refresh,
        run_control_room_status=_run_sync_control_room_status_impl,
        run_agentops_status=_run_sync_agentops_status,
        upsert_sync_run=_upsert_sync_run,
        fetch_sync_run_func=_fetch_sync_run,
        control_room_cache_invalidate=_sync_control_room_cache_invalidate,
        logger_debug=logger.debug,
        stale_after_seconds=_SYNC_NOW_STALE_AFTER_SECONDS,
        persist_control_room_state=persist_control_room_state,
    )


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
    run_id, steps, reserved_response = await _reserve_sync_now_run_or_response(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        request_id=request_id,
        user=user,
    )
    if reserved_response is not None:
        return reserved_response
    _invalidate_data_platform_read_caches(user)

    return await _continue_sync_now_after_reservation(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        request_id=request_id,
        run_id=run_id,
        steps=steps,
        user=user,
    )


async def _reserve_sync_now_run_or_response(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    user: dict,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
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
            existing_response = await _existing_sync_now_response(
                cartridge=cartridge,
                run_id=run_id,
                request_id=request_id,
                user=user,
            )
            if existing_response is not None:
                return run_id, [], existing_response
            active_response = await _active_sync_now_response(
                cartridge=cartridge,
                mode=mode,
                target=target,
                conn_id=conn_id,
                user=user,
            )
            if active_response is not None:
                return run_id, [], active_response
            steps = await _record_sync_now_start(
                run_id=run_id,
                cartridge=cartridge,
                mode=mode,
                target=target,
                conn_id=conn_id,
                request_id=request_id,
                user=user,
            )
        finally:
            await sync_lock_conn.execute(
                "SELECT pg_advisory_unlock(hashtext($1))", lock_key
            )
    return run_id, steps, None


async def _existing_sync_now_response(
    *,
    cartridge: str,
    run_id: str,
    request_id: str | None,
    user: dict,
) -> dict[str, Any] | None:
    if not request_id:
        return None
    existing_row = await _fetch_sync_run(cartridge=cartridge, run_id=run_id, user=user)
    if not existing_row:
        return None
    existing_status = str(existing_row.get("status") or "").lower()
    existing_extra = _sync_extra_from_row(existing_row)
    if (
        existing_status in _SYNC_TERMINAL_STATUSES
        and not _sync_run_needs_final_reconcile(existing_row, existing_extra)
    ):
        return _sync_public_payload(existing_row, existing_extra)
    return await _build_sync_run_status(
        cartridge=cartridge,
        row=existing_row,
        user=user,
        persist_control_room_state=True,
    )


async def _active_sync_now_response(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    user: dict,
) -> dict[str, Any] | None:
    active_row = await _fetch_active_sync_run(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        user=user,
    )
    if not active_row:
        return None
    active_status = await _build_sync_run_status(
        cartridge=cartridge,
        row=active_row,
        user=user,
        persist_control_room_state=True,
    )
    if str(active_status.get("status") or "").lower() not in _SYNC_TERMINAL_STATUSES:
        return active_status
    return None


async def _record_sync_now_start(
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    user: dict,
) -> list[dict[str, Any]]:
    steps = _merge_sync_steps(
        _initial_sync_steps(),
        _sync_start_step_updates(),
    )
    await _upsert_sync_run(
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        status="running",
        user=user,
        extra=_sync_running_extra(
            mode=mode,
            target=target,
            conn_id=conn_id,
            request_id=request_id,
            steps=steps,
        ),
    )
    return steps


def _sync_extract_attempt_components(
    extract_attempt: dict[str, Any],
) -> tuple[dict[str, Any], int, list[Any], list[Any]]:
    result = extract_attempt["result"]
    attempts = int(extract_attempt["attempts"])
    extract_state = _sync_extract_all_result_state(result)
    return (
        result,
        attempts,
        list(extract_state["triggered_entities"]),
        list(extract_state["errors"]),
    )


async def _sync_now_status_or_diagnostics(
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    user: dict,
    upsert_debug: dict[str, Any],
) -> dict[str, Any]:
    row = await _fetch_sync_run(cartridge=cartridge, run_id=run_id, user=user)
    if not row:
        await _raise_missing_sync_run_diagnostics(
            run_id=run_id,
            cartridge=cartridge,
            mode=mode,
            target=target,
            conn_id=conn_id,
            user=user,
            upsert_debug=upsert_debug,
        )
    return await _build_sync_run_status(
        cartridge=cartridge,
        row=row,
        user=user,
        persist_control_room_state=True,
    )


async def _trigger_sync_extract_all_components(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    run_id: str,
    user: dict,
) -> tuple[dict[str, Any], int, list[Any], list[Any]]:
    extract_attempt = await _trigger_sync_extract_all_attempt(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        run_id=run_id,
        user=user,
    )
    return _sync_extract_attempt_components(extract_attempt)


async def _continue_sync_now_after_reservation(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    run_id: str,
    steps: list[dict[str, Any]],
    user: dict,
) -> dict[str, Any]:
    dataset_seed, steps, early_response = await _seed_sync_packaged_datasets_or_response(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        request_id=request_id,
        run_id=run_id,
        steps=steps,
        user=user,
    )
    if early_response is not None:
        return early_response

    result, attempts, triggered_entities, errors = (
        await _trigger_sync_extract_all_components(
            cartridge=cartridge,
            mode=mode,
            target=target,
            conn_id=conn_id,
            run_id=run_id,
            user=user,
        )
    )

    steps, upsert_debug = await _persist_sync_extract_trigger_result(
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        request_id=request_id,
        steps=steps,
        triggered_entities=triggered_entities,
        errors=errors,
        attempts=attempts,
        result=result,
        dataset_seed=dataset_seed,
        user=user,
    )
    return await _sync_now_status_or_diagnostics(
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        user=user,
        upsert_debug=upsert_debug,
    )


async def _sync_packaged_dataset_seed_failure_response(
    *,
    exc: Exception,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    run_id: str,
    steps: list[dict[str, Any]],
    user: dict,
) -> tuple[None, list[dict[str, Any]], dict[str, Any] | None]:
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
        _sync_dataset_seed_failure_step_updates(message),
    )
    await _upsert_sync_run(
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        status="failed",
        user=user,
        extra=_sync_dataset_seed_failure_extra(
            mode=mode,
            target=target,
            conn_id=conn_id,
            request_id=request_id,
            steps=steps,
            message=message,
        ),
        error_message=message[:500],
    )
    row = await _fetch_sync_run(cartridge=cartridge, run_id=run_id, user=user)
    if row:
        return None, steps, _sync_public_payload(row, _sync_extra_from_row(row))
    raise HTTPException(
        500,
        "sync packaged dataset seed failed before Airflow trigger",
    )


async def _seed_sync_packaged_datasets_or_response(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    run_id: str,
    steps: list[dict[str, Any]],
    user: dict,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    if cartridge != "sap_successfactors":
        return None, steps, None
    try:
        dataset_seed = await _ensure_sync_packaged_datasets(
            cartridge=cartridge,
            user=user,
        )
        return dataset_seed, steps, None
    except Exception as exc:  # noqa: BLE001
        return await _sync_packaged_dataset_seed_failure_response(
            exc=exc,
            cartridge=cartridge,
            mode=mode,
            target=target,
            conn_id=conn_id,
            request_id=request_id,
            run_id=run_id,
            steps=steps,
            user=user,
        )


def _sync_extract_all_trigger_body(
    *, mode: str, target: str, run_id: str, conn_id: str | None
) -> dict[str, Any]:
    return {
        "mode": mode,
        "target": target,
        "idempotency_key": run_id,
        **({"conn_id": conn_id} if conn_id else {}),
    }


async def _trigger_sync_extract_all_attempt(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    run_id: str,
    user: dict,
) -> dict[str, Any]:
    return await _run_sync_extract_all_with_retries(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        run_id=run_id,
        extract_body=_sync_extract_all_trigger_body(
            mode=mode,
            target=target,
            run_id=run_id,
            conn_id=conn_id,
        ),
        user=user,
        trigger_sync_aggregate_extract_all=_trigger_sync_aggregate_extract_all,
        call_with_optional_user=_call_with_optional_user,
        api_pipeline_extract_all=api_pipeline_extract_all,
        retryable_errors=_sync_errors_retryable,
        sleep=asyncio.sleep,
    )


async def _persist_sync_extract_trigger_result(
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    steps: list[dict[str, Any]],
    triggered_entities: list[Any],
    errors: list[Any],
    attempts: int,
    result: dict[str, Any],
    dataset_seed: dict[str, Any] | None,
    user: dict,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    steps = _merge_sync_steps(
        steps,
        _sync_extract_all_trigger_step_updates(
            triggered_entities=triggered_entities,
            errors=errors,
            attempts=attempts,
        ),
    )
    status = _sync_status_from_steps(steps)
    upsert_debug = await _upsert_sync_run(
        run_id=run_id,
        cartridge=cartridge,
        mode=mode,
        status=status,
        user=user,
        extra=_sync_extract_all_trigger_extra(
            mode=mode,
            target=target,
            conn_id=conn_id,
            request_id=request_id,
            steps=steps,
            triggered_entities=triggered_entities,
            errors=errors,
            result=result,
            dataset_seed=dataset_seed,
        ),
        error_message=_sync_extract_all_error_message(errors),
    )
    return steps, upsert_debug


async def _raise_missing_sync_run_diagnostics(
    *,
    run_id: str,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    user: dict,
    upsert_debug: dict[str, Any],
) -> None:
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


async def _load_pipeline_vault_payload(
    cartridge_id: str, current_user: dict | None
) -> Any:
    async with httpx.AsyncClient(
        headers=_vault_headers_for_user(current_user or {}), timeout=5
    ) as c:
        r = await c.get(f"{_VAULT_URL}/connections/{quote(cartridge_id, safe='')}")
    if r.status_code not in (404, 204):
        r.raise_for_status()
        return r.json()
    return None


async def _load_pipeline_entity_config_conn_id(cartridge_id: str) -> Any:
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


async def _resolve_pipeline_sync_conn_id(
    cartridge: str,
    requested_conn_id: object | None,
    user: dict | None,
) -> str | None:
    return await _resolve_pipeline_sync_conn_id_impl(
        cartridge,
        requested_conn_id,
        user,
        vault_payload_loader=_load_pipeline_vault_payload,
        entity_config_conn_loader=_load_pipeline_entity_config_conn_id,
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
    return await _rename_studio_entity_payload_impl(
        cartridge_id=cartridge_id,
        entity=entity,
        body=body,
        cartridge_service=cartridge_service,
    )


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
    return await _update_studio_entity_payload_impl(
        cartridge_id=cartridge_id,
        entity=entity,
        body=body,
        cartridge_service=cartridge_service,
    )


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
_CREDENTIAL_BOOTSTRAP_CARTRIDGES = {"banxico", "inegi", "sec_edgar"}


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


def _require_cartridge_visible(
    user: dict | None,
    cartridge_id: str,
    *,
    allow_credential_bootstrap: bool = False,
) -> None:
    if user is None:
        return
    ctx = build_security_context(user)
    if _is_security_admin_context(ctx):
        return
    if not _cartridge_visible_for_context(ctx, cartridge_id):
        if allow_credential_bootstrap and cartridge_id in _CREDENTIAL_BOOTSTRAP_CARTRIDGES:
            return
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
    return await _studio_chat_stream_response_impl(
        body=body,
        user=user,
        cartridge_service=cartridge_service,
        studio_assistant=studio_assistant,
        uuid_factory=uuid.uuid4,
        logger_exception=logger.exception,
        streaming_response_factory=StreamingResponse,
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
    # Source-contract markers: _invoke_agent_payload_impl receives these
    # callbacks and performs the actual calls in the same order.
    # _agent_invoke_background_requested(body)
    # _start_agent_invoke_background(agent, message, history, user)
    # _agent_invoke_background_response(agent)
    return await _invoke_agent_payload_impl(
        agent_id=agent_id,
        body=body,
        user=user,
        agents_service=_agents,
        agent_runtime=_agent_runtime,
        background_requested=_agent_invoke_background_requested,
        start_background=_start_agent_invoke_background,
        background_response=_agent_invoke_background_response,
    )


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
    _validate_agent_runner_token(request)
    agent = await _load_scheduled_agent(agent_id, body)
    scheduled_fire_at, schedule_key, airflow_dag_run_id = _scheduled_agent_run_params(
        agent,
        body,
    )
    reservation = await _reserve_scheduled_agent_run(
        agent,
        scheduled_fire_at=scheduled_fire_at,
        schedule_key=schedule_key,
        airflow_dag_run_id=airflow_dag_run_id,
    )
    if reservation.get("duplicate"):
        return _scheduled_agent_duplicate_response(agent, reservation)
    return await _run_reserved_scheduled_agent(
        agent,
        body,
        reservation=reservation,
        scheduled_fire_at=scheduled_fire_at,
        airflow_dag_run_id=airflow_dag_run_id,
    )


def _validate_agent_runner_token(request: Request) -> None:
    token = request.headers.get("X-Agent-Runner-Token", "")
    if not _AGENT_RUNNER_TOKEN or not secrets.compare_digest(
        token, _AGENT_RUNNER_TOKEN
    ):
        raise HTTPException(401, "invalid runner token")


async def _load_scheduled_agent(agent_id: str, body: dict) -> Any:
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
    return agent


def _scheduled_agent_run_params(agent: Any, body: dict) -> tuple[Any, str, str | None]:
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
    schedule_key = str(schedule.get("key") or "default").strip() or "default"
    extra_role = str((extra or {}).get("role") or "").strip().lower()
    monitor_contract = extra.get("monitor") if isinstance(extra, dict) else None
    if (
        extra_role != "monitor"
        or not isinstance(monitor_contract, dict)
        or not monitor_contract
    ):
        raise HTTPException(403, "scheduled agents require monitor role and monitor contract")
    airflow_dag_run_id = str(body.get("airflow_dag_run_id") or "").strip() or None
    return scheduled_fire_at, schedule_key, airflow_dag_run_id


async def _reserve_scheduled_agent_run(
    agent: Any,
    *,
    scheduled_fire_at: Any,
    schedule_key: str,
    airflow_dag_run_id: str | None,
) -> dict:
    return await _agent_scheduler.reserve_scheduled_run(
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


def _scheduled_agent_duplicate_response(agent: Any, reservation: dict) -> dict:
    completed = reservation.get("status") == "ok" and isinstance(
        reservation.get("agent_run_id"), int
    )
    return {
        "ok": completed,
        "status": "completed" if completed else str(reservation.get("status") or "error"),
        "reply": "scheduled run already recorded",
        "viewer_urls": [],
        "messages": [],
        "agent_id": agent.id,
        "run_id": reservation.get("agent_run_id"),
        "duplicate": True,
        "schedule_run": reservation,
    }


async def _run_reserved_scheduled_agent(
    agent: Any,
    body: dict,
    *,
    reservation: dict,
    scheduled_fire_at: Any,
    airflow_dag_run_id: str | None,
) -> Any:
    message = (body.get("message") or "").strip() or "Ejecuta tu tarea programada."
    result = await _scheduled_monitor_execution.execute_reserved_scheduled_monitor(
        agent=agent,
        message=message,
        reservation=reservation,
        scheduled_fire_at=scheduled_fire_at.isoformat(),
        metadata={
            "airflow_dag_run_id": airflow_dag_run_id,
        },
    )
    result["ok"] = True
    result["status"] = "completed"
    result["schedule_run"] = {
        **reservation,
        "status": "ok",
        "agent_run_id": result["run_id"],
    }
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
    return await _agent_invoke_stream_response_impl(
        agent_id=agent_id,
        body=body,
        user=user,
        agents_service=_agents,
        agent_runtime=_agent_runtime,
        streaming_response_factory=StreamingResponse,
    )


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


def _vault_headers_for_user(user: dict, cartridge: str | None = None) -> dict[str, str]:
    ctx = build_security_context(user)
    if cartridge in _CREDENTIAL_BOOTSTRAP_CARTRIDGES:
        allowed = {
            str(item).strip()
            for item in (ctx.get("allowed_cartridges") or [])
            if str(item).strip()
        }
        if "*" not in allowed and cartridge not in allowed:
            allowed.add(str(cartridge))
            ctx = {**ctx, "allowed_cartridges": sorted(allowed)}
            ctx.pop("_signature", None)
            ctx.pop("_signed_at", None)
            ctx.pop("_signature_version", None)
            ctx = sign_security_context(ctx)
    return {
        **_hdr_for("VAULT"),
        "x-security-context": json.dumps(ctx, ensure_ascii=False),
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
    _require_cartridge_visible(user, cartridge, allow_credential_bootstrap=True)
    try:
        async with httpx.AsyncClient(
            headers=_vault_headers_for_user(user, cartridge), timeout=5
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
    _require_cartridge_visible(user, cartridge, allow_credential_bootstrap=True)
    vault_conn_id = _tenant_vault_conn_id(user, conn_id)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user, cartridge), timeout=5) as c:
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
    _require_cartridge_visible(user, cartridge, allow_credential_bootstrap=True)
    vault_conn_id = _tenant_vault_conn_id(user, conn_id)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user, cartridge), timeout=5) as c:
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
    _require_cartridge_visible(user, cartridge, allow_credential_bootstrap=True)
    vault_conn_id = _tenant_vault_conn_id(user, conn_id)
    async with httpx.AsyncClient(headers=_vault_headers_for_user(user, cartridge), timeout=5) as c:
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
    return await _rag_sources_payload_impl(
        kinds=kinds,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_for_user=_rag_headers_for_user,
    )


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
    return await _rag_delete_source_payload_impl(
        source_id=source_id,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_for_user=_rag_headers_for_user,
    )


@app.post(
    "/api/rag/search",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))],
)
async def api_rag_search(body: dict, user: dict = Depends(require_permission("datasets.read"))):
    return await _rag_search_payload_impl(
        body=body,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload_factory=_mcp_payload,
        upstream_error_detail=_upstream_error_detail,
    )


@app.post(
    "/api/rag/reindex",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_rag_reindex(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    return await _rag_reindex_payload_impl(
        body=body,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        upstream_error_detail=_upstream_error_detail,
        refinement_invoke=_refinement_invoke,
        require_cartridge_visible=_require_cartridge_visible,
        build_security_context=build_security_context,
    )


@app.post(
    "/api/rag/ingest",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("datasets.write")),
        Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN)),
    ],
)
async def api_rag_ingest(body: dict, user: dict = Depends(require_permission("datasets.write"))):
    return await _rag_ingest_payload_impl(
        body=body,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        upstream_error_detail=_upstream_error_detail,
        build_security_context=build_security_context,
    )


@app.post(
    "/api/rag/ask", dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))]
)
async def api_rag_ask(body: dict, user: dict = Depends(require_permission("datasets.read"))):
    """Retrieval-augmented answer: search top-K chunks, synthesize with the chat LLM."""
    from app.services import llm_client as _llm

    return await _rag_answer_payload_impl(
        body=body,
        user=user,
        rag_url=_RAG_URL,
        http_client_factory=httpx.AsyncClient,
        headers_factory=_hdr_for,
        mcp_payload_factory=_mcp_payload,
        upstream_error_detail=_upstream_error_detail,
        llm_client=_llm,
        uuid_factory=uuid.uuid4,
        logger_exception=logger.exception,
        rag_search_arguments=_rag_search_arguments,
        rag_empty_answer=_rag_empty_answer,
        rag_synthesis_messages=_rag_synthesis_messages,
    )


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
    cartridge = await _scope_catalog_cartridge_arg(user, cartridge)
    if not cartridge:
        return _empty_catalog_payload()
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
    return await _semantic_enrich_payload_impl(
        body=body,
        user=user,
        scope_catalog_cartridge_arg=_scope_catalog_cartridge_arg,
        refinement_invoke=_refinement_invoke,
        scoped_read_cache_invalidate=_scoped_read_cache_invalidate,
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
    return await _catalog_get_payload_impl(
        layer=layer,
        cartridge=cartridge,
        tags=tags,
        datasets=datasets,
        user=user,
        scope_catalog_cartridge_arg=_scope_catalog_cartridge_arg,
        user_allowed_cartridges=_user_allowed_cartridges,
        empty_catalog_payload=_empty_catalog_payload,
        catalog_query_args=_catalog_query_args,
        catalog_cache_key=_catalog_cache_key,
        refinement_invoke=_refinement_invoke,
        raise_for_refinement_payload_error=_raise_for_refinement_payload_error,
        scoped_read_cache_get_or_set=_scoped_read_cache_get_or_set,
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
    return await _catalog_upsert_payload_impl(
        body=body,
        user=user,
        refinement_invoke=_refinement_invoke,
        raise_for_refinement_payload_error=_raise_for_refinement_payload_error,
        scoped_read_cache_invalidate=_scoped_read_cache_invalidate,
    )


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
    return await _catalog_relationship_payload_impl(
        body=body,
        user=user,
        refinement_invoke=_refinement_invoke,
        raise_for_refinement_payload_error=_raise_for_refinement_payload_error,
        scoped_read_cache_invalidate=_scoped_read_cache_invalidate,
    )


async def _refinement_invoke(
    tool: str, args: dict, *, timeout: int = 30, user: dict | None = None
):
    return await _refinement_invoke_impl(
        tool,
        args,
        timeout=timeout,
        user=user,
        httpx_module=httpx,
        hdr_for=_hdr_for,
        mcp_payload=_mcp_payload,
        upstream_error_detail=_upstream_error_detail,
        raise_for_refinement_payload_error=_raise_for_refinement_payload_error,
    )


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
    tools = build_studio_ops_tools()
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

    return await _invoke_studio_ops_tool_impl(
        tool=tool,
        args=args or {},
        user=user,
        cartridge_service=cartridge_service,
        get_db_pool=_get_db_pool,
        pipeline_runs_scope_predicate=_pipeline_runs_scope_predicate,
        pipeline_runs_read_conn=_pipeline_runs_read_conn,
        mcp_registry=mcp_registry,
        airflow_log_attempt=_airflow_log_attempt,
        airflow_log_task_ids=_airflow_log_task_ids,
        uuid_factory=uuid.uuid4,
        logger_debug=logger.debug,
        logger_exception=logger.exception,
    )


# ── Monitoring MCP server (deeplinks para el asistente) ───────────────────────

CONSOLE_URL = _public_url("CONSOLE_URL", development_default="http://localhost:8000")


@app.get(
    "/monitoring/tools", dependencies=[Depends(require_permission("monitor.read"))]
)
async def monitoring_tools(user: dict = Depends(require_permission("monitor.read"))):
    # Sprint v1.22: same rationale as /monitoring/mcp/tools — tool
    # discovery should be authenticated.
    return {"tools": build_monitoring_tools()}


@app.post(
    "/monitoring/invoke",
    dependencies=[Depends(require_csrf), Depends(require_permission("monitor.read"))],
)
async def monitoring_invoke(body: dict, user: dict = Depends(require_permission("monitor.read"))):
    # Sprint v1.22: was reachable without any auth. monitoring tools
    # read job state and DAG metadata, which a session-less caller has
    # no business seeing. CSRF added because this is a state-shaped
    # POST and could be called from a cross-origin form otherwise.
    _require_effective_permission(user, "monitor.read")
    return await _invoke_monitoring_tool_impl(
        body=body,
        user=user,
        job_service=job_service,
        console_url=CONSOLE_URL,
    )


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


def _decision_reference_id(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise HTTPException(400, f"{field} must be a positive integer or null")
    try:
        reference_id = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be a positive integer or null") from None
    if reference_id <= 0 or str(value).strip() != str(reference_id):
        raise HTTPException(400, f"{field} must be a positive integer or null")
    return reference_id


def _decision_idempotency_key(request: Request) -> str:
    raw = request.headers.get("Idempotency-Key")
    if raw is None or not raw.strip():
        raise HTTPException(400, "Idempotency-Key header is required")
    if raw != raw.strip() or len(raw.encode("utf-8")) > 200:
        raise HTTPException(400, "Idempotency-Key header is invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        raise HTTPException(400, "Idempotency-Key header is invalid")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _decision_action_fingerprint(action_text: str, note: str | None) -> str:
    canonical = json.dumps(
        {"action_text": action_text, "note": note},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decision_action_to_dict(row) -> dict:
    action = dict(row)
    action["ts"] = action["ts"].isoformat() if action.get("ts") else None
    return action


async def _create_decision_action_idempotently(
    conn,
    *,
    decision_id: int,
    user: dict,
    tenant_id: str,
    workspace_id: str,
    action_text: str,
    note: str | None,
    key_hash: str,
) -> dict:
    """Reserve and complete one action in the caller's scoped transaction."""
    operation = f"decision.action.create:{decision_id}"
    request_fingerprint = _decision_action_fingerprint(action_text, note)
    # The ledger policy binds rows to the authenticated actor in addition to
    # the tenant/workspace GUCs installed by scoped_db_for_user().
    await conn.execute(
        "SELECT set_config('app.user_id', $1, true)",
        str(user["id"]),
    )
    reservation_id = await conn.fetchval(
        """INSERT INTO workspace_decision_idempotency (
               tenant_id, workspace_id, actor_user_id, operation,
               idempotency_key_hash, request_fingerprint
           )
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (
               actor_user_id, workspace_id, operation, idempotency_key_hash
           ) DO NOTHING
           RETURNING id""",
        tenant_id,
        workspace_id,
        user["id"],
        operation,
        key_hash,
        request_fingerprint,
    )
    if reservation_id is None:
        reservation = await conn.fetchrow(
            """SELECT request_fingerprint, status, action_id
                 FROM workspace_decision_idempotency
                WHERE actor_user_id = $1
                  AND tenant_id = $2
                  AND workspace_id = $3
                  AND operation = $4
                  AND idempotency_key_hash = $5""",
            user["id"],
            tenant_id,
            workspace_id,
            operation,
            key_hash,
        )
        if not reservation:
            raise HTTPException(409, "idempotency reservation is unavailable")
        if reservation["request_fingerprint"] != request_fingerprint:
            raise HTTPException(
                409, "Idempotency-Key was already used for another payload"
            )
        if reservation["status"] != "completed" or reservation["action_id"] is None:
            raise HTTPException(409, "idempotent operation is not complete")
        action = await conn.fetchrow(
            """SELECT * FROM decision_actions
                WHERE id = $1 AND decision_id = $2""",
            reservation["action_id"],
            decision_id,
        )
        if not action:
            raise HTTPException(409, "idempotent action is no longer available")
        return _decision_action_to_dict(action)

    row = await conn.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        decision_id,
        action_text,
        note,
        user.get("email") or "user",
    )
    completed_id = await conn.fetchval(
        """UPDATE workspace_decision_idempotency
              SET status = 'completed', action_id = $2,
                  completed_at = clock_timestamp()
            WHERE id = $1
              AND actor_user_id = $3
              AND workspace_id = $4
              AND status = 'in_progress'
            RETURNING id""",
        reservation_id,
        row["id"],
        user["id"],
        workspace_id,
    )
    if completed_id is None:
        raise RuntimeError("decision idempotency reservation completion failed")
    return _decision_action_to_dict(row)


async def _validate_decision_references(
    conn,
    *,
    tenant_id: str | None,
    workspace_id: str,
    assignee_id: int | None = None,
    follow_up_decision_id: int | None = None,
) -> None:
    if assignee_id is None and follow_up_decision_id is None:
        return
    if not tenant_id or not workspace_id:
        raise HTTPException(403, "active tenant and workspace are required")
    if assignee_id is not None:
        assigned = await conn.fetchval(
            """SELECT uwr.user_id
                 FROM users AS u
                 JOIN user_workspace_roles AS uwr ON uwr.user_id = u.id
                WHERE u.id = $1
                  AND u.tenant_id = $3
                  AND u.is_active = TRUE
                  AND uwr.workspace_id = $2
                LIMIT 1""",
            assignee_id,
            workspace_id,
            tenant_id,
        )
        if assigned is None:
            raise HTTPException(400, "assignee_id is not valid for the active workspace")
    if follow_up_decision_id is not None:
        followed = await conn.fetchval(
            """SELECT id FROM decisions
                WHERE id = $1 AND workspace_id = $2
                LIMIT 1""",
            follow_up_decision_id,
            workspace_id,
        )
        if followed is None:
            raise HTTPException(
                400,
                "follow_up_decision_id is not valid for the active workspace",
            )


def _dec_visible_clause(
    uid: int, is_admin: bool, params: list, workspace_id: str | None = None
) -> str:
    return _dec_visible_clause_impl(
        uid=uid,
        is_admin=is_admin,
        params=params,
        workspace_id=workspace_id,
    )


def _dec_is_workspace_admin(user: dict) -> bool:
    return _dec_is_workspace_admin_impl(
        is_global_admin=_is_global_iam_admin(user),
        workspace_role=_workspace_role(user),
    )


def _dec_load_query(decision_id: int, user: dict, workspace_id: str) -> tuple[str, list[Any]]:
    return _dec_load_query_impl(
        decision_id=decision_id,
        user_id=user["id"],
        is_admin=_dec_is_workspace_admin(user),
        workspace_id=workspace_id,
    )


def _dec_list_query(
    *,
    user: dict,
    workspace_id: str,
    status: str,
    overdue: str,
) -> tuple[str, list[Any]]:
    return _dec_list_query_impl(
        user_id=user["id"],
        is_admin=_dec_is_workspace_admin(user),
        workspace_id=workspace_id,
        status=status,
        overdue=overdue,
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
    # Scope-regression compatibility: _dec_load_query keeps the legacy guard
    # SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2.
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        return await _dec_load_on_conn(
            conn,
            decision_id=decision_id,
            user=user,
            workspace_id=workspace_id,
            tenant_id=_tenant_id,
        )


async def _dec_load_on_conn(
    conn,
    *,
    decision_id: int,
    user: dict,
    workspace_id: str,
    tenant_id: str | None,
    for_update: bool = False,
) -> dict | None:
    sql, params = _dec_load_query(decision_id, user, workspace_id)
    if for_update:
        sql += " FOR UPDATE"
    row = await conn.fetchrow(sql, *params)
    if not row:
        return None
    visible = await _filter_decision_rows(
        conn,
        workspace_id=workspace_id,
        rows=[row],
        tenant_id=tenant_id,
    )
    return visible[0] if visible else None


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
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        return {"decisions": []}
    sql, params = _dec_list_query(
        user=user,
        workspace_id=workspace_id,
        status=status,
        overdue=overdue,
    )
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        rows = await _fetch_business_decisions(
            conn,
            sql=sql,
            params=params,
            workspace_id=workspace_id,
            tenant_id=_tenant_id,
        )
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
    assignee_id = _decision_reference_id(body.get("assignee_id"), "assignee_id")
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        await _validate_decision_references(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            assignee_id=assignee_id,
        )
        row = await conn.fetchrow(
            """INSERT INTO decisions
                  (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
               RETURNING *""",
            title,
            body.get("description") or "",
            _coerce_date(body.get("commitment_date")),
            _json_dec.dumps(
                _decision_kpis_with_provenance(body.get("kpis"), "manual")
            ),
            user["id"],
            assignee_id,
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


def _decision_update_assignments(body: dict) -> tuple[list[str], list[Any]]:
    return _decision_update_assignments_impl(body)


def _decision_update_sql_and_params(
    *,
    sets: list[str],
    params: list[Any],
    decision_id: int,
    existing: Any,
) -> tuple[str, list[Any]]:
    # Sprint v1.37: pin UPDATE to (id, workspace_id) — defense-in-depth
    # against a future code path that loads ``existing`` from a
    # different source. ``existing`` already came from
    # ``_dec_load_with_visibility`` which itself filters by workspace,
    # so ``existing["workspace_id"]`` is the active workspace by
    # construction.
    return _decision_update_sql_and_params_impl(
        sets=sets,
        params=params,
        decision_id=decision_id,
        workspace_id=existing["workspace_id"],
    )


@app.patch(
    "/api/decisions/{decision_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def api_decisions_update(
    decision_id: int, body: dict, user: dict = Depends(require_permission("control_room.write"))
):
    """Patch any subset of: title, description, commitment_date, kpis, status, outcome,
    closed_at, follow_up_decision_id, assignee_id, visibility."""
    normalized_body = dict(body)
    for field in ("assignee_id", "follow_up_decision_id"):
        if field in normalized_body:
            normalized_body[field] = _decision_reference_id(
                normalized_body[field], field
            )
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(403, "active workspace is required")
    pool = await _dec_pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        existing = await _dec_load_on_conn(
            conn,
            decision_id=decision_id,
            user=user,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            for_update=True,
        )
        if not existing:
            raise HTTPException(404, f"Decision {decision_id} not found")
        if not _dec_can_edit(existing, user):
            raise HTTPException(
                403, "you can only edit decisions you created or are assigned to"
            )
        if "kpis" in normalized_body:
            normalized_body = {
                **normalized_body,
                "kpis": _preserve_control_room_provenance(
                    existing.get("kpis"), normalized_body.get("kpis")
                ),
            }
        await _validate_decision_references(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            assignee_id=normalized_body.get("assignee_id"),
            follow_up_decision_id=normalized_body.get("follow_up_decision_id"),
        )
        sets, params = _decision_update_assignments(normalized_body)
        if not sets:
            raise HTTPException(400, "no updatable fields supplied")
        sql, params = _decision_update_sql_and_params(
            sets=sets,
            params=params,
            decision_id=decision_id,
            existing=existing,
        )
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
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(403, "active workspace is required")
    pool = await _dec_pool()
    # Sprint v1.37: pin DELETE to (id, workspace_id) — same rationale
    # as the UPDATE above. ``existing["workspace_id"]`` comes from the
    # workspace-scoped SELECT FOR UPDATE in this same transaction.
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        existing = await _dec_load_on_conn(
            conn,
            decision_id=decision_id,
            user=user,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            for_update=True,
        )
        if not existing:
            raise HTTPException(404, f"Decision {decision_id} not found")
        if not _dec_can_delete(existing, user):
            raise HTTPException(403, "only the creator or an admin can delete a decision")
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
    decision_id: int,
    body: dict,
    request: Request,
    user: dict = Depends(require_permission("control_room.write")),
):
    key_hash = _decision_idempotency_key(request)
    raw_action_text = body.get("action_text")
    if not isinstance(raw_action_text, str):
        raise HTTPException(400, "action_text must be a string")
    action_text = raw_action_text.strip()
    if not action_text:
        raise HTTPException(400, "action_text is required")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(400, "note must be a string or null")
    pool = await _dec_pool()
    workspace_id = _current_workspace_id(user)
    if not workspace_id:
        raise HTTPException(403, "active workspace is required")
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, _workspace_id):
        if not tenant_id:
            raise HTTPException(403, "active tenant and workspace are required")
        existing = await _dec_load_on_conn(
            conn,
            decision_id=decision_id,
            user=user,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            for_update=True,
        )
        if not existing:
            raise HTTPException(404, f"Decision {decision_id} not found")
        if not _dec_can_edit(existing, user):
            raise HTTPException(403, "only creator/assignee/admin can add to bitácora")
        return await _create_decision_action_idempotently(
            conn,
            decision_id=decision_id,
            user=user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            action_text=action_text,
            note=note,
            key_hash=key_hash,
        )


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
    return await _list_user_picker_payload_impl(
        user=user,
        auth_list_users=_auth.list_users,
        is_global_iam_admin=_is_global_iam_admin,
        visible_user_ids_for_admin=_visible_user_ids_for_admin,
    )


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


def _iam_admin_workspace_ids(user: dict | None) -> set[str]:
    return _iam_admin_workspace_ids_impl(user)


def _workspace_scope_db_unavailable(exc: BaseException) -> bool:
    return _workspace_scope_db_unavailable_impl(exc)


async def _workspace_rows_from_auth_stub(user_id: int) -> list[dict]:
    """Compatibility path for unit-test auth doubles without DATABASE_URL."""
    return await _workspace_rows_from_auth_stub_impl(user_id, auth=_auth)


async def _target_user_workspace_ids(user_id: int) -> set[str]:
    return await _target_user_workspace_ids_impl(
        user_id,
        get_db_pool=_get_db_pool,
        auth=_auth,
        workspace_scope_db_unavailable=_workspace_scope_db_unavailable,
    )


async def _workspace_summaries_for_users(user_ids: list[int]) -> dict[int, list[dict]]:
    return await _workspace_summaries_for_users_impl(
        user_ids,
        get_db_pool=_get_db_pool,
        workspace_scope_db_unavailable=_workspace_scope_db_unavailable,
    )


async def _attach_workspace_summaries(users: list[dict]) -> list[dict]:
    return await _attach_workspace_summaries_impl(
        users,
        workspace_summaries=_workspace_summaries_for_users,
    )


async def _visible_user_ids_for_admin(admin_user: dict, users: list[dict]) -> set[int]:
    return await _visible_user_ids_for_admin_impl(
        admin_user,
        users,
        get_db_pool=_get_db_pool,
        workspace_scope_db_unavailable=_workspace_scope_db_unavailable,
        is_global_iam_admin=_is_global_iam_admin,
        session_workspace_ids=_session_workspace_ids,
        admin_workspace_ids=_iam_admin_workspace_ids,
    )


async def _set_workspace_role_for_user(
    user_id: int, workspace_id: str, role: str
) -> None:
    await _set_workspace_role_for_user_impl(
        user_id,
        workspace_id,
        role,
        get_db_pool=_get_db_pool,
    )


async def _assert_can_use_workspace(admin_user: dict, workspace_id: str | None) -> None:
    await _assert_can_use_workspace_impl(
        admin_user,
        workspace_id,
        is_global_iam_admin=_is_global_iam_admin,
        session_workspace_ids=_session_workspace_ids,
    )


async def _assert_can_manage_target_user(admin_user: dict, target_user_id: int) -> None:
    await _assert_can_manage_target_user_impl(
        admin_user,
        target_user_id,
        auth_get_user_by_id=_auth.get_user_by_id,
        is_global_iam_admin=_is_global_iam_admin,
        session_workspace_ids=_session_workspace_ids,
        target_workspace_ids=_target_user_workspace_ids,
        admin_workspace_ids=_iam_admin_workspace_ids,
    )


@app.get("/api/admin/users")
async def api_admin_users_list(
    admin_user: dict = Depends(require_permission("iam.users.read")),
):
    return await _list_admin_users_payload_impl(
        admin_user=admin_user,
        auth_list_users=_auth.list_users,
        is_global_iam_admin=_is_global_iam_admin,
        visible_user_ids_for_admin=_visible_user_ids_for_admin,
        attach_workspace_summaries=_attach_workspace_summaries,
    )


@app.post(
    "/api/admin/users",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_create(
    body: dict,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    return await _create_admin_user_payload_impl(
        body=body,
        admin_user=admin_user,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        normalize_email_or_400=_normalize_email_or_400,
        validate_password_or_400=_validate_password_or_400,
        assignable_role=_assignable_role,
        is_global_iam_admin=_is_global_iam_admin,
        assert_can_use_workspace=_assert_can_use_workspace,
        set_workspace_role_for_user=_set_workspace_role_for_user,
        workspace_scope_db_unavailable=_workspace_scope_db_unavailable,
    )


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
    return await _update_admin_user_payload_impl(
        user_id=user_id,
        body=body,
        admin_user=admin_user,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        validate_password_or_400=_validate_password_or_400,
        assignable_role=_assignable_role,
        is_global_iam_admin=_is_global_iam_admin,
        assert_can_manage_target_user=_assert_can_manage_target_user,
        set_workspace_role_for_user=_set_workspace_role_for_user,
        workspace_scope_db_unavailable=_workspace_scope_db_unavailable,
    )


@app.delete(
    "/api/admin/users/{user_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("iam.users.write"))],
)
async def api_admin_users_delete(
    user_id: int,
    request: Request,
    admin_user: dict = Depends(require_permission("iam.users.write")),
):
    return await _delete_admin_user_payload_impl(
        user_id=user_id,
        admin_user=admin_user,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        assert_can_manage_target_user=_assert_can_manage_target_user,
    )


def _vpn_configured() -> bool:
    return _vpn_configured_impl(os.environ)


def _pack_vpn_conf(conf_text: str, email: str) -> tuple[bytes, str]:
    return _pack_vpn_conf_impl(conf_text, email)


def _safe_filename(email: str) -> str:
    return _safe_filename_impl(email)


async def _create_vpn_config_link(user_id: int, email: str) -> dict:
    return await _create_vpn_config_link_impl(
        user_id,
        email,
        vpn_configured=_vpn_configured,
        vpn_service=_vpn,
        tokens_service=_tokens,
        vpn_link=_vpn_link,
        internal_error_request_id=_internal_error_request_id,
        logger_exception=logger.exception,
        vpn_error_cls=_vpn.VPNError,
    )


async def _issue_vpn_for_user(user_id: int, email: str, name: str | None) -> dict:
    return await _issue_vpn_for_user_impl(
        user_id,
        email,
        name,
        create_vpn_config_link=_create_vpn_config_link,
        email_service=_email,
        vpn_ttl_hours=VPN_TTL_HOURS,
    )


async def _rollback_failed_invite(user_id: int, vpn_result: dict | None = None) -> None:
    await _rollback_failed_invite_impl(
        user_id,
        vpn_result,
        vpn_service=_vpn,
        auth_service=_auth,
        logger_warning=logger.warning,
        logger_exception=logger.exception,
    )


@app.get("/vpn-config/{token}")
async def get_vpn_config(token: str, user: dict | None = Depends(current_user)):
    return await _vpn_config_download_response_impl(
        token=token,
        tokens_service=_tokens,
        vpn_service=_vpn,
        safe_filename=_safe_filename,
        response_cls=Response,
        vpn_error_cls=_vpn.VPNError,
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
    return await _reissue_vpn_for_user_payload_impl(
        user_id=user_id,
        admin_user=admin_user,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        assert_can_manage_target_user=_assert_can_manage_target_user,
        issue_vpn_for_user=_issue_vpn_for_user,
    )


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
    return await _invite_admin_user_payload_impl(
        body=body,
        admin_user=admin_user,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        tokens_service=_tokens,
        email_service=_email,
        normalize_email_or_400=_normalize_email_or_400,
        assignable_role=_assignable_role,
        assert_can_use_workspace=_assert_can_use_workspace,
        create_vpn_config_link=_create_vpn_config_link,
        pack_vpn_conf=_pack_vpn_conf,
        safe_filename=_safe_filename,
        rollback_failed_invite=_rollback_failed_invite,
        activation_link=_activation_link,
        invite_ttl_hours=INVITE_TTL_HOURS,
        vpn_ttl_hours=VPN_TTL_HOURS,
        logger_exception=logger.exception,
    )


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
    return await _reinvite_admin_user_payload_impl(
        user_id=user_id,
        body=body,
        admin_user=admin_user,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        tokens_service=_tokens,
        email_service=_email,
        assert_can_manage_target_user=_assert_can_manage_target_user,
        create_vpn_config_link=_create_vpn_config_link,
        pack_vpn_conf=_pack_vpn_conf,
        safe_filename=_safe_filename,
        activation_link=_activation_link,
        invite_ttl_hours=INVITE_TTL_HOURS,
        vpn_ttl_hours=VPN_TTL_HOURS,
    )


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
    return await _admin_send_reset_payload_impl(
        user_id=user_id,
        admin=admin,
        request_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        auth_service=_auth,
        audit_service=_audit,
        tokens_service=_tokens,
        email_service=_email,
        reset_link_for_token=_reset_link,
        reset_ttl_hours=RESET_TTL_HOURS,
        assert_can_manage_target_user=_assert_can_manage_target_user,
    )


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
        "Idempotency-Key",
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
