from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DAGS = {
    cartridge: REPO / "cartridges" / cartridge / "dags" / f"{cartridge}_extract.py"
    for cartridge in ("hubspot", "salesforce", "sap_b1", "sap_hcm", "sap_s4hana", "replicon")
}


def _validator(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "_safe_name")
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_SAFE_NAME" for t in node.targets))
    ]
    namespace: dict = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_safe_name"]


@pytest.mark.parametrize("cartridge", sorted(DAGS))
def test_conf_names_are_validated_before_reaching_a_url(cartridge):
    source = DAGS[cartridge].read_text(encoding="utf-8")
    assert not re.search(r'entity = (?:str\()?conf\.get\("entity"', source)
    safe_name = _validator(DAGS[cartridge])
    for good in ("OINV", "EmpJob", "contacts", "IntercompanyPartners", "user_v2"):
        assert safe_name(good, "entity") == good
    for bad in ("", "../run_full_load_all", "a/b", "x?mode=full", "x#y", "é", "a b", "x" * 200):
        with pytest.raises(ValueError):
            safe_name(bad, "entity")
