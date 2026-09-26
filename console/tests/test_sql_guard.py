from __future__ import annotations

import sys
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_successfactors_sql_guard():
    if str(REPO_ROOT) not in sys.path:
        sys.path.append(str(REPO_ROOT))
    module_path = REPO_ROOT / "omega_cartridge_kit" / "sql_guard.py"
    spec = importlib.util.spec_from_file_location("omega_cartridge_kit_sql_guard_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_successfactors_sql_guard_blocks_or_one_equals_one_before_duckdb():
    sql_guard = _load_successfactors_sql_guard()
    ok, reason = sql_guard.validate_kb_sql(
        (
            "SELECT * FROM read_parquet("
            "'s3://lakehouse/raw/sap_successfactors/User/*.parquet'"
            ") WHERE email = 'x' OR 1=1 LIMIT 10"
        ),
        (
            "s3://lakehouse/raw/sap_successfactors/",
            "s3://lakehouse/silver/sap_successfactors/",
            "s3://lakehouse/gold/sap_successfactors/",
        ),
    )

    assert ok is False
    assert reason == "SQL tautology predicates are not allowed"
