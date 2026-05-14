"""Sprint v1.25 — TODOs in dag_templates.py are resolved (audit F9).

Three contract tests:

  test_no_internal_todos_remaining
      The platform-internal `TODO:` marker is gone from the file. The
      original 9 occurrences were user-edit scaffolding for the
      operator who copies the generated DAG; they've been renamed
      to EDIT_HERE (and a couple were dropped where a sensible
      default was already present). A future PR that adds a real
      platform TODO fails this test — that's the point: platform
      tech debt should not hide under the same string as
      user-edit prompts.

  test_template_renders_valid_python_dag
      The 4 templates (rest_full / rest_incremental / sql_extract /
      replicon_analytics) each produce parseable Python via
      compile(...). Catches a future template edit that introduces
      a syntax error.

  test_template_handles_unknown_id_and_invalid_input
      get_code returns None for an unknown template_id (silent
      fallback by design); raises on invalid identifier inputs
      (cartridge / entity must match the safe-identifier regex).
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_PY = REPO_ROOT / "console" / "app" / "services" / "dag_templates.py"


def _load_module():
    """Import console.app.services.dag_templates.

    The module imports only stdlib (`re`) — no FastAPI / asyncpg / DB
    handles — so we don't need to manipulate sys.modules or sys.path
    here. The conftest at console/ already exposes `app.*` on the
    import path. Doing a destructive `sys.modules.pop('app.*')` here
    (early iteration) regressed 21 peer tests in this same directory
    that depend on cached `app.dependencies` / `app.services.auth`
    stubs from their own fixtures.
    """
    import importlib
    return importlib.import_module("app.services.dag_templates")


# ── TODO removal contract ───────────────────────────────────────────


def test_no_internal_todos_remaining():
    """The platform-internal `TODO:` marker MUST be gone. Operator-facing
    edit prompts use `EDIT_HERE:` so a future `grep TODO console/` doesn't
    false-positive on user-edit scaffolding.

    A genuinely-deferred platform task may live here only with an
    explicit `# DEFERRED v1.NN:` prefix and a tracking sprint id —
    if such a marker is ever added we expect a code review trail."""
    src = TEMPLATES_PY.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(src.splitlines(), 1):
        # Exact-word "TODO" only; tolerate "TODO" appearing inside a
        # comment string IF the line ALSO contains the explicit
        # "DEFERRED v1." opt-out marker.
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
    """Sanity: the 9 user-edit prompts that survived the v1.25 cleanup
    use the EDIT_HERE label. Counts vary if a future PR adds or removes
    prompts, but going below 1 likely means the template lost its
    customization hooks entirely."""
    src = TEMPLATES_PY.read_text(encoding="utf-8")
    n = src.count("EDIT_HERE:")
    assert n >= 1, (
        f"expected at least one EDIT_HERE: marker in dag_templates.py, "
        f"got {n}. If the templates no longer need user-edit prompts, "
        f"verify the operator workflow still makes sense."
    )


# ── Template rendering produces valid Python ────────────────────────


# The 4 templates the file ships. Pinned here so a future PR that
# drops one (or adds one without test coverage) is visible.
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
    """Every template, when rendered with realistic inputs, must
    compile() cleanly as Python. Catches a future edit that breaks the
    template-string interpolation (unbalanced braces, etc.)."""
    mod = _load_module()
    code = mod.get_code(template_id, cartridge="replicon", entity="TimeEntry")
    assert code, f"get_code({template_id!r}) returned falsy"
    # compile() returns a code object on success, raises SyntaxError
    # on failure. We don't execute the result — Airflow imports would
    # fail in this env — only verify it parses.
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
    """A rendered template MUST contain @dag and @task — otherwise
    Airflow won't pick it up. Catches a future edit that nukes the
    decorator scaffolding."""
    mod = _load_module()
    code = mod.get_code(template_id, cartridge="replicon", entity="TimeEntry")
    assert "@dag(" in code, f"{template_id}: no @dag decorator in output"
    assert "@task(" in code, f"{template_id}: no @task decorator in output"


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_template_has_no_leftover_placeholder_braces(template_id):
    """After substitution the rendered code must not contain unfilled
    `{cartridge}` or `{entity}` literals — those would mean a template
    used the wrong escaping (single `{` instead of `{{`) and the
    Airflow worker would treat them as f-string parameters at the
    wrong time."""
    mod = _load_module()
    code = mod.get_code(template_id, cartridge="replicon", entity="TimeEntry")
    # Note: Python f-string literals in the rendered DAG legitimately
    # use `{var}`, but `{cartridge}` / `{entity}` should have been
    # replaced by str.replace() before we see the code.
    assert "{cartridge}" not in code, (
        f"{template_id}: literal `{{cartridge}}` survived template substitution"
    )
    assert "{entity}" not in code, (
        f"{template_id}: literal `{{entity}}` survived template substitution"
    )


# ── Error / unknown-id handling ─────────────────────────────────────


def test_unknown_template_id_returns_none():
    """The dispatch is intentionally silent on unknown IDs — the
    caller (Studio UI) gets None and shows its own error."""
    mod = _load_module()
    assert mod.get_code("does_not_exist", "replicon", "TimeEntry") is None


def test_invalid_cartridge_identifier_raises():
    """Cartridge / entity must match the safe-identifier regex. A
    `;DROP TABLE` slipped in via the studio API gets stopped here."""
    mod = _load_module()
    with pytest.raises(ValueError):
        mod.get_code("rest_full", cartridge="bad name;drop", entity="X")


def test_invalid_entity_identifier_raises():
    mod = _load_module()
    with pytest.raises(ValueError):
        mod.get_code("rest_full", cartridge="replicon", entity="bad/entity")
