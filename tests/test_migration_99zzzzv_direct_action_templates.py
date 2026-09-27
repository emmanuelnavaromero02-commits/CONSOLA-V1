from __future__ import annotations

import re
from pathlib import Path

from app.services.control_room.business_action_authority_policy import (
    DIRECT_ACTION_TEMPLATE_IDS,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "infra/init"
MIGRATION = INIT / "99zzzzv_control_room_direct_action_templates.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_tracks_itself_idempotently_after_its_prerequisites():
    sql = _sql()
    names = sorted(path.name for path in INIT.glob("*.sql"))

    assert "INSERT INTO schema_migrations" in sql
    assert "'99zzzzv_control_room_direct_action_templates.sql'" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert names.index(MIGRATION.name) > names.index(
        "99zzb_control_room_action_authority.sql"
    )
    assert names.index(MIGRATION.name) > names.index(
        "91_control_room_v1_operational.sql"
    )
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", sql, re.M | re.I)


def test_binding_shape_is_replaced_not_duplicated_and_allows_exactly_five_templates():
    sql = _sql()
    assert (
        "DROP CONSTRAINT IF EXISTS control_room_action_tokens_shape_chk" in sql
    )
    assert sql.count("ADD CONSTRAINT control_room_action_tokens_shape_chk") == 1
    allowed = re.search(r"template_id IN \(([^)]*)\)", sql)
    assert allowed is not None
    templates = set(re.findall(r"'([a-z_]+)'", allowed.group(1)))
    assert templates == DIRECT_ACTION_TEMPLATE_IDS | {"create_followup_task"}
    assert (
        "template_id = 'create_followup_task'\n"
        "          OR (intent_id IS NULL AND binding_dry_run_action_run_id IS NULL)"
    ) in sql


def test_intent_ledger_stays_followup_only():
    sql = _sql()
    assert "control_room_action_intents" not in sql
    assert "control_room_action_intents_template_chk" not in sql


def test_seeded_templates_match_the_runtime_registry_exactly():
    sql = _sql()
    seeded = re.findall(
        r"\('([a-z_]+)', 'platform', NULL, '([^']+)', '([^']+)', '([a-z_]+)', "
        r"'low', 'direct', (TRUE|FALSE), '\{\"external_write\": false\}'::jsonb\)",
        sql,
    )
    assert {row[0] for row in seeded} == DIRECT_ACTION_TEMPLATE_IDS
    for template_id, label, description, action_kind, approval in seeded:
        template = ACTION_TEMPLATES[template_id]
        assert label == template["label"]
        assert description == template["description"]
        assert action_kind == template["action_kind"]
        assert approval == "FALSE"
        assert template["requires_approval"] is False


def test_reapplication_never_reenables_an_operator_disabled_template():
    sql = _sql()
    upsert = sql.split("ON CONFLICT (template_id) DO UPDATE", 1)[1].split(";", 1)[0]
    assert "enabled" not in upsert
    assert "label = EXCLUDED.label" in upsert
    assert "requires_approval = EXCLUDED.requires_approval" in upsert
