from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CARTRIDGES = ("hubspot", "replicon", "salesforce", "sap_b1", "sap_hcm", "sap_s4hana", "sap_successfactors")


def test_no_cartridge_ships_a_private_guard():
    for cartridge in sorted(path.name for path in (REPO / "cartridges").iterdir() if path.is_dir()):
        assert not (REPO / "cartridges" / cartridge / "app" / "core" / "sql_guard.py").exists(), cartridge


def test_every_sql_cartridge_imports_the_shared_guard():
    for cartridge in CARTRIDGES:
        app = REPO / "cartridges" / cartridge / "app"
        imports = set()
        for source in app.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
        assert "omega_cartridge_kit.sql_guard" in imports, cartridge
        assert "app.core.sql_guard" not in imports, cartridge
