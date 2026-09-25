from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
CONTROL_ROOM = REPO / "console/app/services/control_room_service.py"
CONTROL_ROOM_CORE = REPO / "console/app/services/control_room/core.py"
CONTROL_ROOM_API = REPO / "console/app/services/control_room/api.py"
CONTROL_ROOM_STATE = REPO / "console/app/services/control_room/state.py"
CONTROL_ROOM_UI = REPO / "console-next/src/app/(shell)/control-room/page.tsx"
CONTROL_ROOM_EXPERIENCE_UI = (
    REPO
    / "console-next/src/components/control-room/experience/ControlRoomExperiencePage.tsx"
)
CONTROL_ROOM_EXPERIENCE_CLIENT = (
    REPO / "console-next/src/lib/control-room/experience-client.ts"
)
READINESS_MANIFEST = (
    REPO / "console/app/services/control_room/data_readiness_manifest.yaml"
)


def _readiness_entries() -> list[dict]:
    data = yaml.safe_load(READINESS_MANIFEST.read_text(encoding="utf-8")) or {}
    return data.get("datasets", [])


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_control_room_readiness_registry_tracks_known_partial_and_stub_datasets():
    src = _read(CONTROL_ROOM)
    core = _read(CONTROL_ROOM_CORE)
    entries = _readiness_entries()
    assert len(entries) >= 10
    assert {"partial", "stub"} <= {entry["readiness"] for entry in entries}
    for entry in entries:
        assert entry["reason"]
        assert entry["blockers"]
    assert "from app.services.control_room import core as _core" in src
    assert "readiness_manifest import dataset_readiness_registry" in core
    assert "CONTROL_ROOM_DATASET_READINESS" in core
    assert "dataset_readiness_registry()" in core


def test_control_room_source_contract_columns_match_known_dataset_shapes():
    src = _read(CONTROL_ROOM_CORE)
    for bad_field in (
        'entity_id_field="period"',
        'entity_id_field="org_unit"',
        'entity_id_field="material"',
    ):
        assert bad_field not in src
    for good_field in (
        'entity_id_field="org_id"',
        'entity_id_field="department_id"',
        'entity_id_field="termination_month"',
        'entity_id_field="posting_month"',
        'entity_label_field="department_name"',
    ):
        assert good_field in src


def test_dashboard_tracks_readiness_and_projects_a_sanitized_experience_ui():
    backend = "\n".join(
        _read(path)
        for path in (CONTROL_ROOM_CORE, CONTROL_ROOM_API, CONTROL_ROOM_STATE)
    )
    ui_root = _read(CONTROL_ROOM_UI)
    ui_experience = _read(CONTROL_ROOM_EXPERIENCE_UI)
    ui_client = _read(CONTROL_ROOM_EXPERIENCE_CLIENT)
    for needle in (
        "operationally_ready",
        "data_ready_modules",
        "partial_modules",
        "stub_modules",
        "readiness_reason",
        "readiness_blockers",
    ):
        assert needle in backend
    assert "ControlRoomExperiencePage" in ui_root
    assert "useControlRoomExperience" in ui_experience
    assert "ControlRoomExperienceV2" in ui_experience
    assert '"/api/control-room/experience/v2"' in ui_client
    for backend_only_field in (
        "operationally_ready",
        "partial_modules",
        "stub_modules",
        "readiness_reason",
        "readiness_blockers",
    ):
        assert backend_only_field not in ui_root
        assert backend_only_field not in ui_experience


def test_readyz_supports_strict_control_room_data_mode():
    main_src = _read(REPO / "console/app/main.py")
    router_src = _read(REPO / "console/app/routers/v1/system.py")
    readyz_data_src = _read(REPO / "console/app/services/readyz_data.py")
    makefile = _read(REPO / "Makefile")
    readyz_impl_src = _read(REPO / "console/app/domains/system/readyz.py")
    for src in (main_src, router_src):
        assert "control_room_data" in src
        assert "_build_readyz_checks_impl(" in src
        assert re.search(r"status_code=200 if ok else 503", src)
    assert "require_data" in main_src
    assert "_is_production_env()" in main_src
    assert "CONTROL_ROOM_REQUIRE_DATA_READY" in readyz_impl_src
    assert "require_data" in readyz_impl_src
    assert "require_intelligence" in readyz_impl_src
    assert "is_production_env()" in readyz_impl_src
    assert "intelligence_opt_out_allowed" in readyz_impl_src
    assert "require_data=require_intelligence_data" in readyz_impl_src
    assert "omega_publication.dataset_publication_heads" in readyz_data_src
    assert "omega_publication.materialization_runs" in readyz_data_src
    assert "r.status IN ('published','legacy_unverified')" in readyz_data_src
    assert "h.layer='gold'" in readyz_data_src
    assert "set_config('app.tenant_id'" in readyz_data_src
    assert "set_config('app.workspace_id'" in readyz_data_src
    assert 'isolation="repeatable_read", readonly=True' in readyz_data_src
    assert "lineage_gold_rows" in readyz_data_src
    intelligence = _read(REPO / "console/app/services/intelligence/readiness.py")
    assert "def _gold_counts" in intelligence
    assert "omega_publication.dataset_publication_heads" in intelligence
    assert "omega_publication.materialization_runs" in intelligence
    assert '"source": "publication_head"' in intelligence
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in intelligence
    assert "set_config('app.tenant_id'" in intelligence
    assert "set_config('app.workspace_id'" in intelligence
    assert "_PUBLISHED_RELATION_RE.fullmatch" in intelligence
    assert "readyz?require_data=1" in makefile
