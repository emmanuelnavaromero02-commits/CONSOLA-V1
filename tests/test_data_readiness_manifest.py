from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
CONSOLE = REPO / "console"
MANIFEST = CONSOLE / "app/services/control_room/data_readiness_manifest.yaml"
CONTROL_ROOM = CONSOLE / "app/services/control_room_service.py"
CONTROL_ROOM_CORE = CONSOLE / "app/services/control_room/core.py"
SF_KBS = REPO / "cartridges/sap_successfactors/app/config/knowledge_bits.yaml"
PARTIAL_KB_YAMLS = {
    "sap_hcm": REPO / "cartridges/sap_hcm/app/config/knowledge_bits.yaml",
    "sap_s4hana": REPO / "cartridges/sap_s4hana/app/config/knowledge_bits.yaml",
    "sap_successfactors": SF_KBS,
}

DATASET_DIRS = {
    "sap_hcm": REPO / "cartridges/sap_hcm/datasets",
    "sap_s4hana": REPO / "cartridges/sap_s4hana/datasets",
    "sap_successfactors": REPO / "cartridges/sap_successfactors/datasets",
}
READINESS_MARKER_RE = re.compile(
    r"\bTODO\b|PENDIENTE\s*:|PENDIENTE\s*\(|WHERE\s+FALSE",
    re.IGNORECASE,
)


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}


def _dataset_entries() -> dict[tuple[str, str], dict]:
    return {(entry["cartridge"], entry["dataset"]): entry for entry in _manifest()["datasets"]}


def _kb_entries() -> dict[tuple[str, str], dict]:
    return {(entry["cartridge"], entry["id"]): entry for entry in _manifest()["knowledge_bits"]}


def test_manifest_loaders_accept_manifest():
    sys.path.insert(0, str(CONSOLE))
    from app.services.control_room.readiness_manifest import (  # noqa: PLC0415
        dataset_readiness_registry,
        knowledge_bit_readiness_registry,
    )

    datasets = dataset_readiness_registry()
    kbs = knowledge_bit_readiness_registry()
    assert datasets
    assert kbs
    assert all(entry["data_readiness"] in {"partial", "stub"} for entry in datasets.values())
    assert all(entry["reason"] and entry["blockers"] for entry in datasets.values())
    assert all(entry["reason"] and entry["blockers"] and entry["datasets"] for entry in kbs.values())


def test_manifest_entries_are_complete_and_evidence_exists():
    manifest = _manifest()
    assert manifest["version"] == 1
    assert len(manifest["datasets"]) >= 10
    seen: set[tuple[str, str]] = set()
    for entry in manifest["datasets"]:
        key = (entry["cartridge"], entry["dataset"])
        assert key not in seen
        seen.add(key)
        assert entry["readiness"] in {"partial", "stub"}
        assert entry["reason"]
        assert entry["blockers"]
        assert entry["evidence"]
        for evidence in entry["evidence"]:
            assert (REPO / evidence).exists(), f"{key}: missing evidence file {evidence}"


def test_control_room_uses_manifest_not_inline_readiness_table():
    core = CONTROL_ROOM_CORE.read_text(encoding="utf-8")
    facade = CONTROL_ROOM.read_text(encoding="utf-8")
    assert "from app.services.control_room.readiness_manifest import dataset_readiness_registry" in core
    assert "CONTROL_ROOM_DATASET_READINESS" in core
    assert "dataset_readiness_registry()" in core
    assert "from app.services.control_room import core as _core" in facade
    for source in (core, facade):
        assert '("sap_hcm", "manager_hierarchy")' not in source
        assert '("sap_successfactors", "sap_successfactors_recruitment_funnel")' not in source


def test_dataset_sql_with_functional_readiness_markers_is_manifested():
    entries = _dataset_entries()
    offenders: list[str] = []
    for cartridge, folder in DATASET_DIRS.items():
        for path in sorted(folder.glob("*.sql")):
            if not READINESS_MARKER_RE.search(path.read_text(encoding="utf-8")):
                continue
            key = (cartridge, path.stem)
            if key not in entries:
                offenders.append(f"{cartridge}/{path.name}")
                continue
            entry = entries[key]
            assert entry["readiness"] in {"partial", "stub"}
            assert entry["reason"] and entry["blockers"]
    assert not offenders, f"dataset readiness markers without manifest entries: {offenders}"


def test_partial_kbs_are_manifested():
    partial_kbs: set[tuple[str, str]] = set()
    for cartridge, path in PARTIAL_KB_YAMLS.items():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        partial_kbs.update(
            (cartridge, kb["id"])
            for kb in data.get("knowledge_bits", [])
            if "parcial" in str(kb.get("description", "")).lower()
            or "pendiente bronze" in str(kb.get("description", "")).lower()
            or "pendiente ampliacion bronze" in str(kb.get("description", "")).lower()
            or "pendiente ampliación bronze" in str(kb.get("description", "")).lower()
        )
    manifested = set(_kb_entries())
    assert partial_kbs
    assert partial_kbs <= manifested


def test_kb_readiness_references_manifested_datasets():
    dataset_entries = _dataset_entries()
    for (cartridge, kb_id), entry in _kb_entries().items():
        for dataset in entry["datasets"]:
            assert (cartridge, dataset) in dataset_entries, f"{kb_id}: {dataset} lacks dataset readiness"
