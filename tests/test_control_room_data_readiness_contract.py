from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONTROL_ROOM = REPO / "console/app/services/control_room_service.py"
CONTROL_ROOM_CORE = REPO / "console/app/services/control_room/core.py"
CONTROL_ROOM_UI = REPO / "console-next/src/app/(shell)/control-room/page.tsx"


PARTIAL_OR_STUB_DATASETS = {
    "manager_hierarchy",
    "sap_hcm_org_hierarchy",
    "workforce_cost_monthly",
    "cost_center_expense",
    "inventory_movement_summary",
    "overdue_billing",
    "sap_successfactors_compensation_distribution",
    "sap_successfactors_empemploymenttermination_latest",
    "sap_successfactors_recruitment_funnel",
    "sap_successfactors_recruitment_pipeline",
    "sap_successfactors_turnover_by_period",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_control_room_readiness_registry_tracks_known_partial_and_stub_datasets():
    src = _read(CONTROL_ROOM)
    core = _read(CONTROL_ROOM_CORE)
    for dataset in PARTIAL_OR_STUB_DATASETS:
        assert dataset in src
        assert dataset in core
    assert '"data_readiness": "partial"' in src
    assert '"data_readiness": "stub"' in src
    assert '"data_readiness": "partial"' in core
    assert '"data_readiness": "stub"' in core


def test_control_room_source_contract_columns_match_known_dataset_shapes():
    src = _read(CONTROL_ROOM)
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


def test_dashboard_exposes_data_readiness_in_backend_and_ui():
    backend = _read(CONTROL_ROOM)
    ui = _read(CONTROL_ROOM_UI)
    for needle in (
        "operationally_ready",
        "data_ready_modules",
        "partial_modules",
        "stub_modules",
        "readiness_reason",
        "readiness_blockers",
    ):
        assert needle in backend
        assert needle in ui
    assert "modulos operativos" not in ui


def test_readyz_supports_strict_control_room_data_mode():
    main_src = _read(REPO / "console/app/main.py")
    router_src = _read(REPO / "console/app/routers/v1/system.py")
    makefile = _read(REPO / "Makefile")
    for src in (main_src, router_src):
        assert "control_room_data" in src
        assert "CONTROL_ROOM_REQUIRE_DATA_READY" in src
        assert "require_data" in src
        assert re.search(r"status_code=200 if ok else 503", src)
    assert "readyz?require_data=1" in makefile
