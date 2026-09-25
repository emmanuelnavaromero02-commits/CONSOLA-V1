from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_PY = REPO_ROOT / "console" / "app" / "services" / "dag_templates.py"


def _load_module():
    import importlib
    return importlib.import_module("app.services.dag_templates")


def test_no_internal_todos_remaining():
    src = TEMPLATES_PY.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(src.splitlines(), 1):
        if "TODO" not in line:
            continue
        if "DEFERRED v1." in line:
            continue
        bad.append(f"line {lineno}: {line.strip()[:120]}")
    assert not bad, (
        "Platform-internal TODO markers remain in dag_templates.py "
        "(use EDIT_HERE: for user-edit prompts; use a real comment "
        "+ DEFERRED v1.NN: for genuinely-deferred platform tasks):\n  "
        + "\n  ".join(bad)
    )


def test_user_edit_markers_use_edit_here_label():
    src = TEMPLATES_PY.read_text(encoding="utf-8")
    n = src.count("EDIT_HERE:")
    assert n >= 1, (
        f"expected at least one EDIT_HERE: marker in dag_templates.py, "
        f"got {n}. If the templates no longer need user-edit prompts, "
        f"verify the operator workflow still makes sense."
    )


EXPECTED_TEMPLATE_IDS = (
    "rest_full",
    "rest_incremental",
    "sql_extract",
    "replicon_analytics",
)


def test_templates_module_exposes_expected_template_set():
    mod = _load_module()
    ids = {t["id"] for t in mod.get_all()}
    assert set(EXPECTED_TEMPLATE_IDS).issubset(ids), (
        f"missing template IDs: {set(EXPECTED_TEMPLATE_IDS) - ids}"
    )


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_template_renders_to_compilable_python(template_id):
    mod = _load_module()
    code = mod.get_code(template_id, cartridge="replicon", entity="TimeEntry")
    assert code, f"get_code({template_id!r}) returned falsy"
    try:
        compile(code, f"<{template_id}>", "exec")
    except SyntaxError as e:
        pytest.fail(
            f"template {template_id} produced un-parseable Python at "
            f"line {e.lineno}: {e.msg}\nGenerated code snippet:\n"
            + "\n".join(f"  {i+1:3d}: {ln}" for i, ln in
                        enumerate(code.splitlines()) if abs((i+1) - (e.lineno or 0)) <= 3)
        )


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_template_has_dag_decorator_and_at_least_one_task(template_id):
    mod = _load_module()
    code = mod.get_code(template_id, cartridge="replicon", entity="TimeEntry")
    assert "@dag(" in code, f"{template_id}: no @dag decorator in output"
    assert "@task(" in code, f"{template_id}: no @task decorator in output"


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_template_has_no_leftover_placeholder_braces(template_id):
    mod = _load_module()
    code = mod.get_code(template_id, cartridge="replicon", entity="TimeEntry")
    assert "{cartridge}" not in code, (
        f"{template_id}: literal `{{cartridge}}` survived template substitution"
    )
    assert "{entity}" not in code, (
        f"{template_id}: literal `{{entity}}` survived template substitution"
    )


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_template_storage_is_provider_aware_and_never_creates_cloud_bucket(template_id):
    mod = _load_module()
    code = mod.get_code(template_id, cartridge="replicon", entity="TimeEntry")

    assert 'provider == "gcs"' in code
    assert 'os.environ.get("GCS_ACCESS_KEY_ID")' in code
    assert 'os.environ.get("GCS_SECRET_ACCESS_KEY")' in code
    assert 'secure, region = True, "auto"' in code
    assert 'storage["provider"] == "minio" and not client.bucket_exists(bucket)' in code
    assert 'storage["provider"] == "s3" and not storage["access_key"]' in code


def test_template_endpoint_parser_preserves_local_minio_host_and_port():
    import ast

    mod = _load_module()
    code = mod.get_code("rest_full", cartridge="replicon", entity="TimeEntry")
    tree = ast.parse(code)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_storage_endpoint_host"
    )
    helper_source = ast.get_source_segment(code, helper)

    assert helper_source is not None
    assert 'urlsplit(raw if "://" in raw else "//" + raw)' in helper_source
    assert "return parsed.netloc or parsed.path" in helper_source


def test_unknown_template_id_returns_none():
    mod = _load_module()
    assert mod.get_code("does_not_exist", "replicon", "TimeEntry") is None


def test_invalid_cartridge_identifier_raises():
    mod = _load_module()
    with pytest.raises(ValueError):
        mod.get_code("rest_full", cartridge="bad name;drop", entity="X")


def test_invalid_entity_identifier_raises():
    mod = _load_module()
    with pytest.raises(ValueError):
        mod.get_code("rest_full", cartridge="replicon", entity="bad/entity")
