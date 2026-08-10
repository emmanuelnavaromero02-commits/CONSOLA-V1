from __future__ import annotations

import ast
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_banxico_packaged_datasets_are_registered_for_pr2b():
    datasets = sorted((REPO / "cartridges/banxico/datasets").glob("*.sql"))
    names = [path.stem for path in datasets]

    assert names == [
        "banxico_market_context",
        "banxico_series_metadata_latest",
        "banxico_series_observations_normalized",
        "banxico_series_quality",
    ]
    for path in datasets:
        text = path.read_text(encoding="utf-8")
        assert "cartridge: banxico" in text
        assert "managed_by_banxico_materializer" in text


def test_dataset_refresh_chain_skips_intelligence_for_banxico():
    source = (REPO / "airflow/dags/dataset_refresh_chain.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    helper = functions["_skip_intelligence_for_cartridge"]
    helper_return = next(node for node in helper.body if isinstance(node, ast.Return))
    assert isinstance(helper_return.value, ast.Compare)
    assert isinstance(helper_return.value.ops[0], ast.In)
    skipped_set = helper_return.value.comparators[0]
    assert isinstance(skipped_set, ast.Set)
    assert {
        item.value for item in skipped_set.elts if isinstance(item, ast.Constant)
    } == {"banxico", "inegi", "sec_edgar"}

    trigger = functions["_trigger_gold_refresh_intelligence"]
    skip_guard = next(
        node
        for node in trigger.body
        if isinstance(node, ast.If)
        and "_skip_intelligence_for_cartridge" in ast.dump(node.test)
    )
    assert 'conf.get("skip_intelligence")' in ast.get_source_segment(
        source, skip_guard.test
    )
    assert any(isinstance(node, ast.Return) for node in skip_guard.body)
