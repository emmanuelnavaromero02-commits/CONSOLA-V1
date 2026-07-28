from __future__ import annotations

import ast
from pathlib import Path

from app.services.control_room import diagnostics_public_copy


SERVICES = Path(__file__).parents[1] / "app" / "services" / "control_room"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    return imports


def test_operational_wrapper_has_no_low_level_text_classifier_imports() -> None:
    path = SERVICES / "operational_diagnostics.py"
    imports = _imports(path)
    source = path.read_text(encoding="utf-8")

    assert "app.services.public_text_sensitivity" not in imports
    assert "contains_public_technical_copy" not in source
    assert "contains_public_technical_data" not in source
    assert "_public_text" not in source
    assert "_text" not in source


def test_terminal_factory_is_the_only_diagnostics_classifier_owner() -> None:
    factory = SERVICES / "diagnostics_public_factory.py"
    imports = _imports(factory)
    wrapper = (SERVICES / "operational_diagnostics.py").read_text(encoding="utf-8")

    assert "app.services.public_text_sensitivity" in imports
    assert "build_diagnostics_response" in wrapper


def test_product_registry_contains_only_real_terminal_literals() -> None:
    assert set(diagnostics_public_copy._PRODUCT_COPY_REGISTRY.values()) == {
        "Technical diagnostic",
        "Source query failed",
        "Diagnostic error reported",
        "Installation error reported",
    }
    product_text = "\n".join(diagnostics_public_copy._PRODUCT_COPY_REGISTRY.values())
    fixture_text = (
        Path(__file__).with_name("control_room_diagnostics_private_copy_fixture.py")
    ).read_text(encoding="utf-8")

    assert "TABLE Rock" not in product_text
    assert "TABLE Rock" in fixture_text


def test_no_transportable_approval_type_or_text_trust_api_exists() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            SERVICES / "diagnostics_public_copy.py",
            SERVICES / "diagnostics_public_factory.py",
            SERVICES / "operational_diagnostics.py",
        )
    )

    assert "ApprovedPublicText" not in sources
    assert "trusted=" not in sources
    assert ".format(" not in sources


def test_product_registry_literals_are_static_and_have_no_slots() -> None:
    path = SERVICES / "diagnostics_public_copy.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_PRODUCT_COPY_REGISTRY"
    ]

    assert len(assignments) == 1
    registry_call = assignments[0].value
    assert isinstance(registry_call, ast.Call)
    registry_literal = registry_call.args[0]
    assert isinstance(registry_literal, ast.Dict)
    assert all(
        isinstance(value, ast.Constant) and type(value.value) is str
        for value in registry_literal.values
    )
    assert not any(isinstance(node, ast.JoinedStr) for node in ast.walk(tree))
    assert all(
        "{" not in value and "}" not in value
        for value in (diagnostics_public_copy._PRODUCT_COPY_REGISTRY.values())
    )


def test_touched_diagnostics_modules_remain_bounded() -> None:
    for name in (
        "diagnostics_public_copy.py",
        "diagnostics_public_factory.py",
        "operational_diagnostics.py",
    ):
        assert len((SERVICES / name).read_text(encoding="utf-8").splitlines()) <= 300
