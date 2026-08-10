from __future__ import annotations

import ast
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_inegi_packaged_datasets_are_registered_for_pr3():
    datasets = sorted((REPO / "cartridges/inegi/datasets").glob("*.sql"))
    names = [path.stem for path in datasets]

    assert names == [
        "inegi_indicator_metadata_latest",
        "inegi_indicator_observations_normalized",
        "inegi_indicator_quality",
        "inegi_market_context",
    ]
    for path in datasets:
        text = path.read_text(encoding="utf-8")
        assert "cartridge: inegi" in text
        assert "managed_by_inegi_materializer" in text


def test_dataset_refresh_chain_skips_intelligence_for_inegi():
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
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_skip_intelligence_for_cartridge"
            for child in ast.walk(node.test)
        )
    )
    assert any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "conf"
        and child.func.attr == "get"
        and child.args
        and isinstance(child.args[0], ast.Constant)
        and child.args[0].value == "skip_intelligence"
        for child in ast.walk(skip_guard.test)
    )
    assert len(skip_guard.body) == 1
    assert isinstance(skip_guard.body[0], ast.Return)
