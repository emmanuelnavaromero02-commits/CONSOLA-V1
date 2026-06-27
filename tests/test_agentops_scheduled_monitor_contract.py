from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_agent_schedule_runs_migration_enforces_one_run_per_fire_time():
    sql = (ROOT / "infra/init/99u_agent_schedule_runs.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS agent_schedule_runs" in sql
    assert "scheduled_fire_at   TIMESTAMPTZ NOT NULL" in sql
    assert "agent_schedule_runs_fire_uidx" in sql
    assert "ON agent_schedule_runs (agent_id, schedule_key, scheduled_fire_at)" in sql
    assert "agent_run_id        BIGINT REFERENCES agent_runs(id) ON DELETE SET NULL" in sql
    assert "ALTER TABLE agent_schedule_runs FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "GRANT USAGE, SELECT ON SEQUENCE agent_schedule_runs_id_seq TO omega_console" in sql


def test_airflow_agent_runner_sends_exact_schedule_fire_to_console():
    source = (ROOT / "airflow/dags/agent_runner.py").read_text(encoding="utf-8")
    assert "def _cron_fire_in_window(" in source
    assert '"scheduled_fire_at": fire_at.isoformat()' in source
    assert '"schedule_key":      str(sched.get("key") or "default")' in source
    assert '"scheduled_fire_at": agent.get("scheduled_fire_at")' in source
    assert '"airflow_dag_run_id": context["run_id"]' in source


def test_agent_runtime_scheduled_monitor_is_deterministic_and_auditable():
    source = (ROOT / "console/app/services/agent_runtime.py").read_text(encoding="utf-8")
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
    assert "scheduled monitor requires extra.monitor contract" in section
    assert "recommendation_only" in section
    assert 'conversation_id=f"agent_run:' not in source


def test_scheduled_agents_fail_closed_without_monitor_contract():
    source = (ROOT / "console/app/main.py").read_text(encoding="utf-8")
    section = source.split("async def api_agents_invoke_scheduled", 1)[1]
    assert "scheduled agents require monitor role and monitor contract" in section
    assert "run_scheduled_monitor" in section
    assert "result = await _agent_runtime.run(agent, message, history=[], user=None)" not in section

    v1_source = (ROOT / "console/app/routers/v1/agents.py").read_text(encoding="utf-8")
    v1_section = v1_source.split("async def api_agents_invoke_scheduled", 1)[1]
    assert "scheduled agents require monitor role and monitor contract" in v1_section
    assert "run_scheduled_monitor" in v1_section
    assert "result = await _agent_runtime.run(agent, message, history=[], user=None)" not in v1_section
