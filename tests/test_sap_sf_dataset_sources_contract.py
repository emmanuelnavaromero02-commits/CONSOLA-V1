from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO / "cartridges" / "sap_successfactors" / "datasets"
SEED_FILES = (
    REPO / "infra" / "init" / "82_sap_successfactors_datasets_seed.sql",
    REPO / "infra" / "init" / "99k_sap_successfactors_silver_schedule.sql",
)
REPAIR_MIGRATION = REPO / "infra" / "init" / "99zzx_sap_sf_dataset_sources_repair.sql"

_SEED_ROW_RE = re.compile(
    r"\(\$seed\$(?P<name>[a-z0-9_]+)\$seed\$, \$seed\$(?P<layer>[a-z]+)\$seed\$, "
    r"\$seed\$sap_successfactors\$seed\$, \$seed\$(?P<sources>\[.*?\])\$seed\$::jsonb, "
    r"\$seed\$(?P<sql>.*?)\$seed\$, \$seed\$",
    re.S,
)


def _literal_sources(sql_text: str) -> set[str]:
    found = set()
    for literal in re.findall(r"s3://\{bucket\}/((?:raw|silver|gold)/[^'\"\s\)]+)", sql_text):
        parts = literal.split("/")
        if len(parts) >= 3:
            found.add("/".join(parts[:3]))
    return found


def _declared_sources(text: str) -> set[str]:
    match = re.search(r"-- sources: (\[.*?\])", text)
    return set(json.loads(match.group(1))) if match else set()


def test_dataset_files_declare_every_storage_literal():
    offenders = {}
    for path in sorted(DATASETS_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        missing = _literal_sources(text) - _declared_sources(text)
        if missing:
            offenders[path.stem] = sorted(missing)
    assert not offenders, f"datasets with undeclared storage literals: {offenders}"


def test_seed_rows_declare_every_storage_literal():
    offenders = {}
    for seed_path in SEED_FILES:
        for match in _SEED_ROW_RE.finditer(seed_path.read_text(encoding="utf-8")):
            missing = _literal_sources(match.group("sql")) - set(json.loads(match.group("sources")))
            if missing:
                offenders[f"{seed_path.name}:{match.group('name')}"] = sorted(missing)
    known_repaired = {
        "82_sap_successfactors_datasets_seed.sql:sap_successfactors_employees_anomalies",
        "82_sap_successfactors_datasets_seed.sql:sap_successfactors_manager_hierarchy",
        "82_sap_successfactors_datasets_seed.sql:sap_successfactors_recruitment_funnel",
        "82_sap_successfactors_datasets_seed.sql:sap_successfactors_turnover_by_period",
    }
    unexpected = {k: v for k, v in offenders.items() if k not in known_repaired}
    assert not unexpected, f"NEW seed rows with undeclared literals (extend 99zzx-style repair): {unexpected}"
    assert set(offenders) <= known_repaired


def test_repair_migration_matches_canonical_files():
    text = REPAIR_MIGRATION.read_text(encoding="utf-8")
    for name in (
        "sap_successfactors_employees_anomalies",
        "sap_successfactors_manager_hierarchy",
        "sap_successfactors_recruitment_funnel",
        "sap_successfactors_turnover_by_period",
    ):
        file_sources = _declared_sources((DATASETS_DIR / f"{name}.sql").read_text(encoding="utf-8"))
        match = re.search(
            rf"SET sources = '(\[[^']*\])'::jsonb,\s+updated_at = NOW\(\)\s+WHERE cartridge = 'sap_successfactors'\s+AND name = '{name}'",
            text,
        )
        assert match, f"99zzx missing repair for {name}"
        assert set(json.loads(match.group(1))) == file_sources, f"99zzx sources for {name} diverge from the canonical file"
