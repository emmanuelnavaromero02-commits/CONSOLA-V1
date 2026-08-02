from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONITOR_SOURCE = ROOT / "console/app/domains/agentops/successfactors_talent_monitor.py"


def test_agent_schedule_runs_migration_enforces_one_run_per_fire_time():
    sql = (ROOT / "infra/init/99u_agent_schedule_runs.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS agent_schedule_runs" in sql
    assert "scheduled_fire_at   TIMESTAMPTZ NOT NULL" in sql
    assert "agent_schedule_runs_fire_uidx" in sql
    assert "ON agent_schedule_runs (agent_id, schedule_key, scheduled_fire_at)" in sql
    assert (
        "agent_run_id        BIGINT REFERENCES agent_runs(id) ON DELETE SET NULL" in sql
    )
    assert "ALTER TABLE agent_schedule_runs FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert (
        "GRANT USAGE, SELECT ON SEQUENCE agent_schedule_runs_id_seq TO omega_console"
        in sql
    )


def test_airflow_agent_runner_uses_server_owned_scoped_discovery():
    source = (ROOT / "airflow/dags/agent_runner.py").read_text(encoding="utf-8")
    runtime = (ROOT / "console/app/services/scheduled_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "/api/operations/internal/agent-runner/due" in source
    assert '"window_start": logical_date.isoformat()' in source
    assert '"window_end": window_end.isoformat()' in source
    assert "FROM agents WHERE is_active" not in source
    assert "FROM workspaces w" in runtime
    assert "JOIN tenants t" in runtime
    assert "async with scoped_db(pool, tenant_id, workspace_id)" in runtime
    assert "tenant_id = $1::uuid" in runtime
    assert "workspace_id = $2::uuid" in runtime
    assert '"tenant_id": agent.get("tenant_id")' in source
    assert '"workspace_id": agent.get("workspace_id")' in source
    assert '"scheduled_fire_at": agent.get("scheduled_fire_at")' in source
    assert '"airflow_dag_run_id": context["run_id"]' in source


def test_agent_runtime_scheduled_monitor_is_deterministic_and_auditable():
    source = (ROOT / "console/app/services/agent_runtime.py").read_text(
        encoding="utf-8"
    )
    section = source.split("async def run_scheduled_monitor", 1)[1]
    assert '"mcp-infra__wisdom_bits__run"' in section
    assert '"mcp-infra__control_room__raise_analysis_alert"' in section
    assert '"mcp-infra__simulation__monte_carlo_run"' in section
    assert '"mcp-infra__calibration__bayesian_state"' in section
    assert '"mcp-infra__decision__orchestrate"' in section
    assert "_monitor_engine_specs(contract)" in section
    assert "monte_carlo requires explicit input_variables" in section
    assert "monte_carlo requires explicit seed" in section
    assert "bayesian_calibration requires explicit calibration_group" in section
    assert "missing_calibration_state" in section
    assert "_monitor_should_alert(contract, payload_with_engines)" in section
    assert "engine_error_count" in source
    assert "upstream_monte_carlo_simulation_id" in section
    assert '"source_type": source_type' in section
    assert '"deterministic_monitor": True' in section
    assert 'dataset == "sap_successfactors_talent_simulation_inputs"' in source
    assert 'ready_statuses = {"ready"}' in source
    assert "async def _get_gold_pool()" in source
    assert 'os.environ.get("GOLD_DATABASE_URL")' in source
    assert "async def _monitor_resolve_decision_engine_inputs" in source
    assert '"engine_inputs": decision_engine_inputs' in section
    load_agent_section = source.split("async def load_agent(", 1)[1].split(
        "async def load_agent_by_slug", 1
    )[0]
    gold_lookup_section = source.split("async def _monitor_latest_gold_row", 1)[
        1
    ].split("async def _monitor_resolve_dataset_inputs", 1)[0]
    assert "pool = await _get_pool()" in load_agent_section
    assert "pool = await _get_gold_pool()" in gold_lookup_section
    assert "scheduled monitor requires extra.monitor contract" in section
    assert "recommendation_only" in section
    assert 'conversation_id=f"agent_run:' not in source


def test_agent_runtime_keeps_wisdombit_as_decision_source_after_simulation():
    source = (ROOT / "console/app/services/agent_runtime.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    decision_if = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "decision_orchestrator" in (ast.get_source_segment(source, node.test) or "")
    )
    section = ast.get_source_segment(source, decision_if) or ""

    assert 'source_type = "monte_carlo_simulation"' not in section
    assert 'source_type = "wisdom_bit"' in section
    assert "source_id = wisdom_bit_id" in section
    assert "upstream_monte_carlo_simulation_id" in section


def test_agent_runtime_audit_normalizes_structured_tool_errors():
    source = (ROOT / "console/app/services/agent_runtime.py").read_text(
        encoding="utf-8"
    )
    audit_section = source.split("async def _audit_agent_tool", 1)[1].split(
        "def _make_invoke", 1
    )[0]
    deny_section = source.split("async def deny", 1)[1].split(
        "if full_name not in allowed_full", 1
    )[0]

    assert "def _safe_error_text(error: Any) -> str:" in source
    assert "error: Any | None = None" in audit_section
    assert "error_text = _safe_error_text(error)" in audit_section
    assert 'metadata["error"] = error_text[:500]' in audit_section
    assert "error[:500]" not in audit_section
    assert "message_text = _safe_error_text(message) or status" in deny_section
    assert '"message": message_text' in deny_section


def test_successfactors_runtime_monitor_contract_keeps_bayes_engine():
    section = MONITOR_SOURCE.read_text(encoding="utf-8")

    assert '"name": "monte_carlo"' in section
    assert '"name": "bayesian_calibration"' in section
    assert '"calibration_group": "sap_successfactors:talent_readiness"' in section
    assert '"name": "decision_orchestrator"' in section
    assert '"bayesian_calibration": {' in section


def test_sync_agentops_candidates_require_workspace_scope():
    section = MONITOR_SOURCE.read_text(encoding="utf-8")

    assert 'str(agent.get("tenant_id") or "").strip()' in section
    assert 'str(agent.get("workspace_id") or "").strip()' in section


def test_scheduled_agents_fail_closed_without_monitor_contract():
    source = ast.unparse(
        ast.parse((ROOT / "console/app/main.py").read_text(encoding="utf-8"))
    ).replace("'", '"')
    section = source.split("async def api_agents_invoke_scheduled", 1)[1]
    assert (
        'scheduled_user_context = scheduled_scope if scheduled_scope.get("workspace_id") else None'
        in section
    )
    assert "load_agent(agent_id, user_context=scheduled_user_context)" in section
    assert "_repair_loaded_successfactors_talent_monitor_if_needed" in section
    assert "scheduled agents require monitor role and monitor contract" in section
    assert "execute_reserved_scheduled_monitor" in section
    assert (
        "result = await _agent_runtime.run(agent, message, history=[], user=None)"
        not in section
    )

    v1_source = ast.unparse(
        ast.parse(
            (ROOT / "console/app/routers/v1/agents.py").read_text(encoding="utf-8")
        )
    ).replace("'", '"')
    v1_section = v1_source.split("async def api_agents_invoke_scheduled", 1)[1]
    assert (
        'scheduled_user_context = scheduled_scope if scheduled_scope.get("workspace_id") else None'
        in v1_section
    )
    assert "load_agent(agent_id, user_context=scheduled_user_context)" in v1_section
    assert "_repair_loaded_successfactors_talent_monitor_if_needed" in v1_section
    assert "scheduled agents require monitor role and monitor contract" in v1_section
    assert "execute_reserved_scheduled_monitor" in v1_section
    assert (
        "result = await _agent_runtime.run(agent, message, history=[], user=None)"
        not in v1_section
    )


def test_successfactors_monitor_runtime_repair_before_agent_reads():
    source = (ROOT / "console/app/main.py").read_text(encoding="utf-8")
    monitor_source = MONITOR_SOURCE.read_text(encoding="utf-8")
    assert "def _successfactors_talent_monitor_needs_runtime_repair" in source
    assert '"sap_successfactors_talent_monitor"' in monitor_source
    assert (
        'role != "monitor" or not has_operational_monitor_contract(agent)'
        in monitor_source
    )
    list_section = source.split("async def api_agents_list", 1)[1].split(
        "async def api_agents_tool_catalog",
        1,
    )[0]
    get_section = source.split("async def api_agents_get", 1)[1].split(
        "@app.post(",
        1,
    )[0]
    assert "_repair_successfactors_talent_monitor_list_if_needed" in list_section
    assert "if not include_inactive" not in list_section
    assert "_repair_successfactors_talent_monitor_row_if_needed" in get_section
    create_section = source.split("async def api_agents_create", 1)[1].split(
        "@app.patch(",
        1,
    )[0]
    update_section = source.split("async def api_agents_update", 1)[1].split(
        "@app.delete(",
        1,
    )[0]
    assert "_coerce_successfactors_talent_monitor_payload(body)" in create_section
    assert "_coerce_successfactors_talent_monitor_payload(body)" in update_section
    coerce_section = monitor_source.split(
        "def coerce_successfactors_talent_monitor_payload",
        1,
    )[1]
    assert "slug != SUCCESSFACTORS_TALENT_MONITOR_SLUG" in coerce_section
    assert 'patched["extra"] = merged_extra' in coerce_section
    assert 'patched["role"] = "monitor"' in coerce_section
    assert 'patched["allowed_tools"] = merge_agent_tools' in coerce_section

    v1_source = (ROOT / "console/app/routers/v1/agents.py").read_text(encoding="utf-8")
    v1_list_section = v1_source.split("async def api_agents_list", 1)[1].split(
        "# /api/agents/_tool-catalog",
        1,
    )[0]
    v1_get_section = v1_source.split("async def api_agents_get", 1)[1].split(
        "# /api/agents",
        1,
    )[0]
    assert "_repair_successfactors_talent_monitor_list_if_needed" in v1_list_section
    assert "if not include_inactive" not in v1_list_section
    assert "_repair_successfactors_talent_monitor_row_if_needed" in v1_get_section
    assert "_coerce_successfactors_talent_monitor_payload(body)" in v1_source
