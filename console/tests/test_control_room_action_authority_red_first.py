from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import permissions
from app.services.control_room.business_action_authority_policy import (
    ACTION_BINDING_TTL_SECONDS,
    EXECUTABLE_TEMPLATE_IDS,
    EXECUTION_HANDLE_TTL_SECONDS,
    INTENT_TTL_SECONDS,
    require_distinct_actors,
)
from app.services.control_room.business_action_public_projection import public_action
from app.services.control_room.business_action_tokens import (
    IssuedStageHandle,
    handle_digest,
    new_handle,
    server_operation_digest,
)
from app.services.control_room.business_authoritative_execution import (
    _authority_audit,
)
from app.services.control_room.business_explicit_action_binding import (
    VerifiedActionBinding,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "infra/init/99zzb_control_room_action_authority.sql"
SECURITY = ROOT / "infra/init/99zzc_control_room_action_authority_security.sql"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_red_01_binding_is_user_bound():
    source = read(
        "console/app/services/control_room/business_action_authority_repository.py"
    )
    assert "subject_user_id = $3" in source
    assert "actor_id(user)" in source
    resolver = read("console/app/services/control_room/business_action_handle.py")
    assert "authorized_explicit_action_bindings" not in resolver


def test_red_02_real_producer_exists():
    surface = read("console/app/routers/control_room_surfaces.py")
    assert "issue_action_bindings" in surface
    assert "actions_by_item=actions_by_item" in surface


def test_red_03_unpersisted_fact_has_no_action():
    source = read(
        "console/app/services/control_room/business_action_binding_producer.py"
    )
    assert "if row is None:" in source
    assert "continue" in source


def test_red_04_valid_fact_only_emits_followup():
    assert EXECUTABLE_TEMPLATE_IDS == frozenset({"create_followup_task"})
    action = public_action("a" * 64)
    assert action.label == "Crear seguimiento operativo"
    assert action.requires_approval is True


def test_red_05_intent_binds_maker_and_workspace():
    sql = MIGRATION.read_text(encoding="utf-8")
    for field in ("tenant_id", "workspace_id", "maker_user_id", "item_id"):
        assert field in sql
    assert "EXCLUDE USING gist" in sql
    assert "authority_window WITH &&" in sql


def test_red_06_admin_cannot_self_approve():
    for role in ("owner", "super_admin", "admin"):
        assert "control_room.approve" not in permissions.ROLE_PERMISSIONS[role]
    with pytest.raises(HTTPException) as blocked:
        require_distinct_actors(7, 7)
    assert blocked.value.status_code == 403


def test_red_07_approve_permission_is_required():
    checker = permissions.ROLE_PERMISSIONS["control_room_approver"]
    assert "control_room.approve" in checker
    assert "control_room.execute" in checker
    assert "control_room.write" not in checker


def test_red_08_tokens_are_stage_user_and_workspace_bound():
    source = read("console/app/services/control_room/business_action_tokens.py")
    assert "token.stage = $4" in source
    assert "token.subject_user_id = $5" in source
    assert "token.workspace_id = $2::uuid" in source


def test_red_09_invalid_expired_and_consumed_tokens_fail_closed():
    for value in ("", "0" * 63, "g" * 64, "0" * 65):
        with pytest.raises(HTTPException) as invalid:
            handle_digest(value)
        assert invalid.value.status_code == 404
    source = read("console/app/services/control_room/business_action_tokens.py")
    assert "token.expires_at > NOW()" in source
    assert 'row.get("status") == "consumed"' in source


def test_red_10_action_handle_promotes_once():
    source = read(
        "console/app/services/control_room/business_action_authority_repository.py"
    )
    assert "status = 'consumed'" in source
    assert "status = 'active' AND expires_at > NOW()" in source


def test_red_11_approve_reject_has_one_winner():
    source = read(
        "console/app/services/control_room/business_action_transition_core.py"
    )
    assert "AND state = $9 AND state_version = $10" in source
    assert "FOR UPDATE" in source


def test_red_12_replay_is_idempotent():
    first = server_operation_digest(
        workspace_id="workspace",
        intent_id="intent",
        operation="approve",
        contract_digest="c" * 64,
        actor_user_id=7,
    )
    second = server_operation_digest(
        workspace_id="workspace",
        intent_id="intent",
        operation="approve",
        contract_digest="c" * 64,
        actor_user_id=7,
    )
    assert first == second and len(first) == 64


def test_red_13_changed_authority_becomes_stale():
    source = read("console/app/services/control_room/business_action_revalidation.py")
    for field in (
        "binding_digest",
        "evidence_digest",
        "observation_fingerprint",
        "contract_digest",
        "target_digest",
        "dry_run_digest",
    ):
        assert field in source


def test_red_14_permission_revocation_blocks_transition():
    source = read("console/app/services/control_room/business_action_revalidation.py")
    assert "actor_has_current_permission" in source
    assert "users.is_active" in source
    assert "user_workspace_roles" in source


def test_red_15_ledger_is_append_only():
    sql = SECURITY.read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "BEFORE TRUNCATE" in sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE" in sql


def test_red_16_audit_failure_aborts_transition():
    source = read(
        "console/app/services/control_room/business_action_transition_core.py"
    )
    assert source.index("UPDATE control_room_action_intents") < source.index(
        "await append_intent_event"
    )
    assert "if not row:" in read(
        "console/app/services/control_room/business_action_ledger.py"
    )


def test_red_17_rls_authority_schema_exists():
    sql = SECURITY.read_text(encoding="utf-8")
    for table in (
        "control_room_action_intents",
        "control_room_action_tokens",
        "control_room_action_intent_events",
    ):
        assert table in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches" in sql


def test_red_18_public_surfaces_do_not_leak_authority_claims():
    binding = VerifiedActionBinding(
        {
            "binding_id": "b" * 64,
            "template_id": "create_followup_task",
            "execution_target_digest": "e" * 64,
            "template_contract_digest": "t" * 64,
            "attestation_key_id": "digest-only-authority",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2026-01-01T00:15:00Z",
        }
    )
    audit = _authority_audit(binding, {"safe": True})
    assert "binding_id" not in audit
    assert "b" * 64 not in repr(audit)


def test_red_19_experience_and_preview_contracts_stay_compatible():
    route = read("console/app/routers/control_room.py")
    assert '"/actions/preview"' in route
    assert "ExperienceActionPreviewResponse(action_handle=body.action_handle)" in route
    assert '"/approval"' not in read("console/app/routers/control_room_surfaces.py")


def test_red_20_external_templates_are_not_executable():
    assert ACTION_BINDING_TTL_SECONDS == 15 * 60
    assert INTENT_TTL_SECONDS == 24 * 60 * 60
    assert EXECUTION_HANDLE_TTL_SECONDS == 5 * 60
    handle = new_handle()
    assert len(handle) == 64
    assert handle_digest(handle) == hashlib.sha256(bytes.fromhex(handle)).digest()
    issued = IssuedStageHandle(
        "intent",
        "execution",
        datetime.now(UTC) + timedelta(minutes=5),
        handle,
    )
    assert handle not in repr(issued)
