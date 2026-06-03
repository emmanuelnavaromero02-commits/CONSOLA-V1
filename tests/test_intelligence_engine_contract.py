from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_intelligence_migration_is_workspace_scoped_and_self_registered():
    migration = read("infra/init/98_intelligence_engine.sql")
    for table in (
        "metric_baselines",
        "intelligence_signals",
        "evidence_packs",
        "evidence_items",
        "hypotheses",
        "decision_options",
        "prediction_outcomes",
    ):
        table_block = migration.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(");", 1)[0]
        assert "tenant_id" in table_block
        assert "workspace_id" in table_block
    assert "98_intelligence_engine.sql" in migration
    assert "control_room_items" not in migration


def test_priority_cartridges_have_valid_intelligence_contracts():
    for cartridge_id in ("hubspot", "replicon", "salesforce"):
        path = ROOT / "cartridges" / cartridge_id / "app/config/intelligence.yaml"
        assert path.exists(), f"missing intelligence contract for {cartridge_id}"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["cartridge"] == cartridge_id
        assert data["metrics"], f"no metrics in {path}"
        for metric in data["metrics"]:
            assert metric["id"]
            assert metric["dataset"]
            assert metric["entity"]["id_field"]
            assert metric["time_field"]
            assert metric["value_field"]
            assert metric["baseline"]["method"] == "moving_average"
            assert metric["baseline"]["minimum_history"] >= 2
            assert metric["action_templates"]


def test_intelligence_router_is_registered_and_mutations_are_guarded():
    main = read("console/app/main.py")
    router = read("console/app/routers/intelligence.py")
    assert "intelligence_router" in main
    assert "app.include_router(intelligence_router.router)" in main
    assert '@router.get("/signals"' in router
    assert '@router.post(' in router
    assert 'Depends(require_permission("datasets.read"))' in router
    assert 'Depends(require_permission("control_room.write"))' in router
    assert "Depends(require_csrf)" in router


def test_control_room_surfaces_persisted_intelligence_items_and_ui_pack():
    service = read("console/app/services/control_room/api.py")
    state = read("console/app/services/control_room/state.py")
    ui = read("console-next/src/app/(shell)/control-room/page.tsx")
    assert "item_kind = 'intelligence_signal'" in service
    assert "_persisted_intelligence_items" in service
    assert '"intelligence": metadata.get("intelligence")' in state
    assert '"intelligence_signal"' in ui
    assert "function IntelligencePanel" in ui
