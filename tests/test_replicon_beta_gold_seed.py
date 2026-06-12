import inspect
import json
from pathlib import Path

import yaml

from scripts import seed_replicon_beta_gold as seed


REPO = Path(__file__).resolve().parents[1]


def _replicon_app_datasets() -> set[str]:
    out: set[str] = set()
    for path in (REPO / "cartridges" / "replicon" / "apps").glob("*.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        for item in meta.get("datasets_used", []):
            out.add(str(item))
    return out


def test_seed_covers_every_dataset_declared_by_replicon_apps() -> None:
    assert seed.REQUIRED_REPLICON_GOLD_DATASETS == _replicon_app_datasets()


def test_every_seeded_gold_table_is_workspace_scoped_and_non_empty() -> None:
    for dataset, payload in seed.DATASETS.items():
        columns = seed.gold_table_columns(dataset)

        assert columns[:2] == ["tenant_id", "workspace_id"]
        assert payload["rows"], f"{dataset} seed rows are empty"


def test_replicon_seed_marker_is_honest_and_external_write_back_off() -> None:
    marker = seed.REPLICON_VAULT_MARKER

    assert marker["auth_method"] == "seeded_gold"
    assert marker["status"] == "data_seed_only"
    assert marker["write_back_enabled"] is False
    assert marker["external_write_back_enabled"] is False
    assert "token" not in marker
    assert "password" not in marker


def test_seed_updates_catalog_and_lineage_but_not_external_run_history() -> None:
    source = inspect.getsource(seed)

    assert "silver_lineage" in source
    assert "data_catalog" in source
    assert "datasets" in source
    assert "pipeline_runs" not in source


def test_makefile_exposes_replicon_beta_seed_target() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert "seed-replicon-beta-gold:" in makefile
    assert "scripts/seed_replicon_beta_gold.py" in makefile


def test_replicon_intelligence_contract_uses_seeded_gold_datasets() -> None:
    contract = yaml.safe_load(
        (REPO / "console/app/config/intelligence_contracts/replicon.yaml").read_text(encoding="utf-8")
    )
    cartridge_contract = yaml.safe_load(
        (REPO / "cartridges/replicon/app/config/intelligence.yaml").read_text(encoding="utf-8")
    )
    datasets = {str(metric["dataset"]) for metric in contract["metrics"]}

    assert cartridge_contract == contract
    assert datasets <= seed.REQUIRED_REPLICON_GOLD_DATASETS
    assert "consultor_timesheet_semanal" not in datasets


def test_replicon_seed_lineage_storage_uri_is_workspace_scoped() -> None:
    source = inspect.getsource(seed._refresh_lineage)

    assert "tenant_id={tenant_id}" in source
    assert "workspace_id={workspace_id}" in source
