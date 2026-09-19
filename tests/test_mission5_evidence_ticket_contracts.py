"""Mission 5: source-level contracts for the evidence-ticket boundary."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/init/99zzzzi_control_room_evidence_tickets.sql"
MCP_CONTROL_ROOM = ROOT / "mcp-infra/app/tools/control_room.py"
NEW_SQL_MODULES = (
    ROOT / "console/app/services/control_room/evidence_tickets.py",
    ROOT / "console/app/services/control_room/attested_monitor_alerts.py",
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_ticket_table_is_append_only_and_console_only():
    sql = _sql()

    assert "GRANT SELECT, INSERT ON control_room_evidence_tickets TO omega_console" in sql
    assert "REVOKE ALL ON control_room_evidence_tickets FROM omega_console" in sql
    assert "REVOKE ALL ON control_room_evidence_tickets FROM PUBLIC" in sql
    for role in ("omega_mcp_infra", "omega_refinement", "omega_vault", "omega_workspace"):
        assert f"'{role}'" in sql
    grants = re.findall(r"GRANT\s+([A-Z ,()_a-z]+?)\s+ON\s+control_room_evidence_tickets", sql)
    assert grants == ["SELECT, INSERT"]


def test_ticket_audit_trail_survives_and_one_ticket_per_lease():
    sql = _sql()

    assert "REFERENCES workspaces(tenant_id, id) ON DELETE RESTRICT" in sql
    assert "ON DELETE CASCADE" not in sql
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS control_room_evidence_tickets_lease_uidx\n"
        "    ON control_room_evidence_tickets (schedule_run_id, fencing_token);"
    ) in sql


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def test_public_alert_tool_takes_no_server_only_argument():
    tree = ast.parse(MCP_CONTROL_ROOM.read_text(encoding="utf-8"))
    public = _function(tree, "control_room__raise_alert")
    impl = _function(tree, "_raise_alert_impl")
    analysis = _function(tree, "control_room__raise_analysis_alert")

    public_args = [arg.arg for arg in (*public.args.args, *public.args.kwonlyargs)]
    assert [name for name in public_args if name.startswith("_")] == []
    assert "_server_metadata_patch" in [arg.arg for arg in impl.args.args]
    # The decorator (the registry entry) sits on the public function only.
    assert public.decorator_list and not impl.decorator_list
    called = {
        node.func.id
        for node in ast.walk(analysis)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_raise_alert_impl" in called
    assert "control_room__raise_alert" not in called


def test_mcp_infra_dispatch_refuses_underscore_arguments():
    source = (ROOT / "mcp-infra/app/main.py").read_text(encoding="utf-8")

    assert 'server_only = sorted(key for key in args if str(key).startswith("_"))' in source
    assert "server-only arg is not allowed" in source


def test_analysis_evidence_refuses_attestations_before_storing():
    source = MCP_CONTROL_ROOM.read_text(encoding="utf-8")
    body = source.split("def _analysis_evidence(", 1)[1].split("\ndef ", 1)[0]

    assert body.index("_carries_server_attestation(value)") < body.index('_as_safe_key(engine, "engine")')
    for label in ("metrics", "blockers", "distribution", "recommended_option"):
        assert f'("{label}", {label})' in body


def test_wisdom_bits_retries_without_authority_against_an_older_console():
    source = MCP_CONTROL_ROOM.read_text(encoding="utf-8")
    body = source.split("async def wisdom_bits__run(", 1)[1].split("\n@tool(", 1)[0]

    assert "exc.status_code != 422" in body
    assert 'console_body.pop("effect_authority")' in body
    assert body.count("_call_console_under_fence(") == 2


def test_ticket_table_is_workspace_scoped_under_forced_rls():
    sql = _sql()

    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "REFERENCES workspaces(tenant_id, id)" in sql
    assert "VALUES ('99zzzzi_control_room_evidence_tickets.sql', NOW())" in sql


def test_ticket_row_can_only_attest_its_own_item_from_a_scheduled_run():
    sql = _sql()

    assert "CHECK (item_id ~ '^agent_alert:[0-9a-f]{32}$')" in sql
    assert "locator_value = item_id" in sql
    assert "source_record_id = 'record-' || item_id" in sql
    assert "CHECK (security_context_source = 'agent_runner')" in sql
    assert "schedule_run_id BIGINT NOT NULL" in sql
    assert "fencing_token   BIGINT NOT NULL" in sql


def _mcp_subset(*names: str) -> dict[str, Any]:
    tree = ast.parse(MCP_CONTROL_ROOM.read_text(encoding="utf-8"))
    wanted = set(names)
    nodes = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in wanted for target in node.targets)
        )
    ]
    namespace: dict[str, Any] = {"Any": Any}
    code = compile(ast.Module(body=nodes, type_ignores=[]), "mcp_control_room_subset", "exec")
    exec(code, namespace)  # noqa: S102 # nosec B102 - functions lifted from repo source
    return namespace


@pytest.mark.parametrize(
    "ref",
    [
        {"type": "dataset_row", "server_attestation": "a" * 64},
        {"kind": "x", "nested": {"scope_binding": "s"}},
        {"refs": [{"Source_Row_Hash": "h"}]},
        {"business_binding": {}},
        {"attestation_key_id": "k"},
    ],
)
def test_mcp_infra_refuses_agent_supplied_attestations(ref):
    namespace = _mcp_subset(
        "_SERVER_ATTESTATION_KEYS",
        "_MAX_ATTESTATION_SCAN_DEPTH",
        "_carries_server_attestation",
    )

    assert namespace["_carries_server_attestation"](ref) is True


def test_mcp_infra_still_accepts_the_monitor_s_own_references():
    namespace = _mcp_subset(
        "_SERVER_ATTESTATION_KEYS",
        "_MAX_ATTESTATION_SCAN_DEPTH",
        "_carries_server_attestation",
    )
    carries = namespace["_carries_server_attestation"]

    assert carries({"kind": "wisdom_bit_monitor", "wisdom_bit_id": "WB-TALENTO", "agent_run_id": 1}) is False
    assert carries({"kind": "analysis_evidence", "engine": "wisdom_bit", "engine_run_id": "x"}) is False
    deep: dict[str, Any] = {}
    cursor = deep
    for _ in range(20):
        cursor["n"] = {}
        cursor = cursor["n"]
    assert carries(deep) is True


def test_evidence_refs_check_runs_before_anything_is_stored():
    source = MCP_CONTROL_ROOM.read_text(encoding="utf-8")
    body = source.split("def _evidence_refs(", 1)[1].split("\ndef ", 1)[0]

    check = body.index("_carries_server_attestation(item)")
    assert check < body.index("_redact_sensitive(item)")
    assert "evidence_refs cannot carry server attestation" in body


def test_wisdom_bits_forwards_authority_to_console_only_when_present():
    source = MCP_CONTROL_ROOM.read_text(encoding="utf-8")
    body = source.split("async def wisdom_bits__run(", 1)[1].split("\n@tool(", 1)[0]

    assert 'console_body["effect_authority"] = effect_authority' in body
    assert "if effect_authority is not None:" in body
    assert "_call_console_under_fence(" in body


@pytest.mark.parametrize("path", NEW_SQL_MODULES, ids=lambda path: path.name)
def test_new_sql_is_static_and_parameterised(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sql_words = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b")
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value for part in node.values if isinstance(part, ast.Constant)
            )
            assert not sql_words.search(text), f"f-string SQL in {path.name}"
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "format":
            target = node.func.value
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                assert not sql_words.search(target.value), f".format SQL in {path.name}"


def test_new_sql_always_filters_tenant_and_workspace():
    for path in NEW_SQL_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            name = node.targets[0].id if isinstance(node.targets[0], ast.Name) else ""
            if not name.endswith("_SQL") or not isinstance(node.value, ast.Constant):
                continue
            sql = str(node.value.value)
            if "assert_scheduled_effect_authority" in sql or sql.lstrip().startswith("SET LOCAL"):
                continue
            assert "tenant_id" in sql, f"{path.name}:{name}"
            assert "workspace_id" in sql, f"{path.name}:{name}"


def test_internal_read_route_writes_an_audit_line():
    source = (ROOT / "console/app/routers/control_room.py").read_text(encoding="utf-8")
    body = source.split("async def control_room_internal_read(", 1)[1].split("\n@router.", 1)[0]

    assert "control_room.internal_read" in body
    for field in ("internal_service", "security_context_source", "agent_id", "agent_run_id"):
        assert field in body
    assert body.index("control_room.internal_read") < body.index("_control_room_internal_view(")
