"""Sprint v1.21.1 hotfix — refinement no longer runs DDL at startup.

The bug: v1.19 partitioned Postgres access by service; refinement
connects as `omega_refinement` which has SELECT/INSERT/UPDATE on
data_catalog / data_relationships / analytic_apps but NOT `CREATE
ON SCHEMA public`. The legacy `_ensure_semantic_catalog_tables()`
called `CREATE TABLE IF NOT EXISTS …` at startup; Postgres evaluates
the CREATE privilege BEFORE the IF NOT EXISTS branch, so the call
fails with `InsufficientPrivilege` and refinement crash-loops on boot.

The fix: drop the runtime DDL. Every table refinement reads/writes
is already provisioned by the init scripts:

  analytic_apps        infra/init/08_workspace_ownership.sql (+ 11 for datasets_used)
  data_catalog         infra/init/19_operational_stability_hotfix.sql
  data_relationships   infra/init/19_operational_stability_hotfix.sql

These tests pin the contract — refinement's main module must not
execute any CREATE/ALTER DDL (on the operational DB), and the three
tables it depends on must be in the init scripts.

Module-level AST walk only — no DB needed.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REFINEMENT_MAIN = REPO_ROOT / "refinement" / "app" / "main.py"
INIT_DIR = REPO_ROOT / "infra" / "init"


# Tables refinement reads from / writes to via omega_refinement.
# These MUST be provisioned by infra/init/*.sql — never at runtime.
REQUIRED_TABLES = ("analytic_apps", "data_catalog", "data_relationships")


def _string_literals_in(module: ast.Module):
    """Yield every string-literal value that appears in the module's
    executable code (i.e. inside function bodies / module top level —
    NOT inside docstrings, NOT inside .pyi type comments).

    A docstring is the first statement of a module/function/class whose
    expression is a Constant string. Everything else is fair game."""
    docstring_node_ids: set[int] = set()

    def _mark_docstring(scope: ast.AST):
        body = getattr(scope, "body", None)
        if not body:
            return
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstring_node_ids.add(id(first.value))

    _mark_docstring(module)
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _mark_docstring(node)

    for node in ast.walk(module):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_node_ids:
                continue
            yield node.value


@pytest.fixture(scope="module")
def refinement_executable_strings():
    tree = ast.parse(REFINEMENT_MAIN.read_text(encoding="utf-8"))
    return list(_string_literals_in(tree))


# ── Hotfix contract ─────────────────────────────────────────────────


def test_no_create_table_in_refinement_main(refinement_executable_strings):
    """The whole point of the hotfix: no executable string in
    refinement/app/main.py may contain `CREATE TABLE`. A future
    regression would re-introduce the boot crash."""
    offenders = []
    for s in refinement_executable_strings:
        if re.search(r"\bCREATE\s+TABLE\b", s, re.IGNORECASE):
            offenders.append(s[:120])
    assert not offenders, (
        "refinement/app/main.py contains a CREATE TABLE string in "
        "executable code — this will fail under omega_refinement which "
        "has no CREATE ON SCHEMA public:\n  "
        + "\n  ".join(offenders)
    )


def test_no_create_index_in_refinement_main(refinement_executable_strings):
    """Same story for CREATE INDEX — requires CREATE on the schema."""
    offenders = []
    for s in refinement_executable_strings:
        if re.search(r"\bCREATE\s+INDEX\b", s, re.IGNORECASE):
            offenders.append(s[:120])
    assert not offenders, (
        "refinement/app/main.py contains a CREATE INDEX string in "
        "executable code:\n  " + "\n  ".join(offenders)
    )


def test_no_alter_table_on_operational_db_in_refinement_main(refinement_executable_strings):
    """`ALTER TABLE … ADD COLUMN` requires table ownership; omega_refinement
    doesn't own any operational table. The GOLD-DB drop in delete-dataset
    is excluded because it runs against postgres_gold using the postgres
    superuser, NOT the operational DB.

    The test scopes by string content: anything that LOOKS like ALTER on
    a known omega_refinement table is forbidden."""
    offenders = []
    for s in refinement_executable_strings:
        if not re.search(r"\bALTER\s+TABLE\b", s, re.IGNORECASE):
            continue
        # Allow ALTER on tables that are clearly GOLD-side via aliases
        # like master_/gold_ or via the `pggold` schema prefix. The
        # operational tables this hotfix is about are analytic_apps,
        # data_catalog, data_relationships.
        if any(t in s for t in REQUIRED_TABLES):
            offenders.append(s[:120])
    assert not offenders, (
        "refinement/app/main.py runs ALTER TABLE on an operational "
        "table — omega_refinement isn't the owner:\n  "
        + "\n  ".join(offenders)
    )


# ── Init-script coverage ────────────────────────────────────────────


def _all_init_sql() -> str:
    """Read every .sql file under infra/init in lexical order."""
    return "\n\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(INIT_DIR.glob("*.sql"))
    )


@pytest.mark.parametrize("table", REQUIRED_TABLES)
def test_required_table_is_provisioned_by_an_init_script(table):
    """Each table refinement reads/writes via omega_refinement must be
    CREATE'd by SOME init script. If a future refactor accidentally
    removes the provisioning, this test fails BEFORE refinement crashes
    in production."""
    sql = _all_init_sql()
    pattern = rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table)}\b"
    assert re.search(pattern, sql, re.IGNORECASE), (
        f"No init script creates the `{table}` table. refinement reads "
        f"from this table — without an init-time CREATE, the service "
        f"won't be able to bootstrap on a fresh DB."
    )


def test_required_tables_appear_in_refinement_role_grants():
    """v1.20 audit-driven contract: omega_refinement must have explicit
    GRANTs on every operational table refinement touches. Asserts the
    three required tables are listed in the v1.19+v1.20 migration."""
    sql = (INIT_DIR / "25_service_roles.sql").read_text(encoding="utf-8")
    # Use the same role-section helper pattern as tests/test_postgres_roles.py
    starts = [
        (m.start(), m.group(1))
        for m in re.finditer(
            r"GRANT\s+CONNECT\s+ON\s+DATABASE\s+\w+\s+TO\s+(omega_[a-z_]+)",
            sql,
        )
    ]
    starts.sort()
    section = ""
    for i, (pos, role) in enumerate(starts):
        if role == "omega_refinement":
            end = starts[i + 1][0] if i + 1 < len(starts) else len(sql)
            section = sql[pos:end]
            break
    assert section, "omega_refinement section not found in migration 25"
    missing = [t for t in REQUIRED_TABLES if t not in section]
    assert not missing, (
        f"omega_refinement section is missing GRANTs on tables refinement "
        f"actually uses: {missing}. Add them in 25_service_roles.sql."
    )


# ── Lifespan contract ───────────────────────────────────────────────


def test_lifespan_does_not_call_runtime_ddl_helpers():
    """`_ensure_semantic_catalog_tables()` and `_ensure_apps_table()`
    are kept as no-ops for callers that still reference them, but the
    LIFESPAN must not depend on them creating anything. The lifespan
    should call them at most as no-op pings, or not at all.

    This test asserts that the lifespan body, as a string, contains no
    SQL DDL keyword. The helpers themselves are checked above."""
    src = REFINEMENT_MAIN.read_text(encoding="utf-8")
    # Extract the lifespan function body
    m = re.search(
        r"async def lifespan\(app: FastAPI\):(.*?)(?=^\w)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert m, "lifespan function not found in refinement/app/main.py"
    body = m.group(1)
    forbidden = ("CREATE TABLE", "CREATE INDEX", "ALTER TABLE")
    # Strip strings inside triple-quoted blocks (docstrings/comments in
    # the body) — we want to catch actual SQL string literals.
    # A simpler check: look at any of the forbidden tokens INSIDE a
    # SQL-looking string. We accept hits inside `#` line comments or
    # docstring fences because those are textual references, not SQL.
    body_executable = "\n".join(
        ln for ln in body.splitlines()
        if not ln.lstrip().startswith("#")
    )
    # Remove docstring blocks crudely (between """ markers).
    body_executable = re.sub(r'""".*?"""', "", body_executable, flags=re.DOTALL)
    hits = [t for t in forbidden if t in body_executable]
    assert not hits, (
        f"refinement lifespan still contains DDL keywords ({hits}). "
        f"Move provisioning to infra/init/*.sql."
    )
