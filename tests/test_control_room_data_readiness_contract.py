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
READINESS_MANIFEST = REPO / "console/app/services/control_room/data_readiness_manifest.yaml"


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


def test_dashboard_exposes_data_readiness_in_backend_and_ui():
    backend = "\n".join(_read(path) for path in (CONTROL_ROOM_CORE, CONTROL_ROOM_API, CONTROL_ROOM_STATE))
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
    assert "frentes con se\u00f1ales" in ui
    assert "listos para decidir" in ui
    for label in (
        "datos parciales",
        "fuera de alcance actual",
        "Requiere permisos OData",
        "Dataset no materializado",
        "Sin datos configurados",
        "No aplica",
    ):
        assert label in ui
    assert "Bloqueado por permisos." not in ui
    assert "Datos incompletos para una decisi\u00f3n autom\u00e1tica." not in ui
    assert "sin informaci\u00f3n suficiente" not in ui
    assert "modulos activos" not in ui


def test_readyz_supports_strict_control_room_data_mode():
    main_src = _read(REPO / "console/app/main.py")
    router_src = _read(REPO / "console/app/routers/v1/system.py")
    readyz_data_src = _read(REPO / "console/app/services/readyz_data.py")
    makefile = _read(REPO / "Makefile")
    for src in (main_src, router_src):
        assert "control_room_data" in src
        assert "CONTROL_ROOM_REQUIRE_DATA_READY" in src
        assert "require_data" in src
        assert "require_intelligence" in src
        assert "_is_production_env()" in src
        assert "intelligence_opt_out_allowed" in src
        assert "require_data=require_intelligence_data" in src
        assert re.search(r"status_code=200 if ok else 503", src)
    assert "silver_lineage" in readyz_data_src
    assert "lineage_gold_rows" in readyz_data_src
    intelligence = _read(REPO / "console/app/services/intelligence/readiness.py")
    assert "def _lineage_gold_counts" in intelligence
    assert '"source": "silver_lineage"' in intelligence
    assert "readyz?require_data=1" in makefile
