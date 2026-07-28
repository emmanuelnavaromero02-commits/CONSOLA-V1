from __future__ import annotations

import ast
from pathlib import Path


APP = Path(__file__).parents[1] / "app"
SERVICES = APP / "services" / "control_room"
SCHEMAS = APP / "schemas"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    return imports


def test_gold_projectors_delegate_without_classifying_text() -> None:
    for path in (
        SERVICES / "successfactors_gold_observations.py",
        SERVICES / "successfactors_gold_public_rows.py",
        SCHEMAS / "control_room_business_responses.py",
    ):
        imports = _imports(path)
        source = path.read_text(encoding="utf-8")
        assert "app.services.public_text_sensitivity" not in imports
        assert "public_business_label" not in source
        assert "contains_public_technical_copy" not in source


def test_gold_terminal_factory_is_the_single_text_policy_owner() -> None:
    path = SERVICES / "successfactors_gold_public_factory.py"
    imports = _imports(path)
    schema = (SCHEMAS / "control_room_business_responses.py").read_text(
        encoding="utf-8"
    )

    assert "app.services.public_text_sensitivity" in imports
    assert "return build_public_gold_response(value)" in schema
    assert "return build_public_gold_widget(value)" in schema


def test_gold_models_do_not_inherit_generic_public_projectors() -> None:
    path = SCHEMAS / "control_room_business_responses.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = {
        node.name: {ast.unparse(base) for base in node.bases}
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }

    assert classes["_StrictGoldModel"] == {"BaseModel"}
    assert classes["ControlRoomGoldMetricRow"] == {"_StrictGoldModel"}
    assert classes["ControlRoomGoldWidget"] == {"_StrictGoldModel"}
    assert classes["ControlRoomGoldKpisResponse"] == {"_StrictGoldModel"}
    widget = next(
        node
        for node in tree.body
        if getattr(node, "name", "") == "ControlRoomGoldWidget"
    )
    annotations = {
        node.target.id: ast.unparse(node.annotation)
        for node in widget.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert annotations["rows"] == "list[ControlRoomGoldMetricRow]"


def test_factory_constructs_models_after_terminal_projection() -> None:
    source = (SERVICES / "successfactors_gold_public_factory.py").read_text(
        encoding="utf-8"
    )

    assert (
        "ControlRoomGoldWidget.model_validate(project_public_gold_widget(raw))"
        in source
    )
    assert "ControlRoomGoldKpisResponse.model_validate(" in source
    assert "project_public_gold_response(raw)" in source


def test_touched_gold_modules_remain_bounded() -> None:
    for path in (
        SERVICES / "successfactors_gold_public_contract.py",
        SERVICES / "successfactors_gold_public_factory.py",
        SERVICES / "successfactors_gold_observations.py",
        SERVICES / "successfactors_gold_public_rows.py",
        SCHEMAS / "control_room_business_responses.py",
    ):
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 300
