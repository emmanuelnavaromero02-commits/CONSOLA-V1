from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REFINEMENT_MAIN = REPO_ROOT / "refinement" / "app" / "main.py"
INIT_DIR = REPO_ROOT / "infra" / "init"


REQUIRED_TABLES = ("analytic_apps", "data_catalog", "data_relationships")


def _string_literals_in(module: ast.Module):
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


def test_no_create_table_in_refinement_main(refinement_executable_strings):
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
    offenders = []
    for s in refinement_executable_strings:
        if re.search(r"\bCREATE\s+INDEX\b", s, re.IGNORECASE):
            offenders.append(s[:120])
    assert not offenders, (
        "refinement/app/main.py contains a CREATE INDEX string in "
        "executable code:\n  " + "\n  ".join(offenders)
    )


def test_no_alter_table_on_operational_db_in_refinement_main(refinement_executable_strings):
    offenders = []
    for s in refinement_executable_strings:
        if not re.search(r"\bALTER\s+TABLE\b", s, re.IGNORECASE):
            continue
        if any(t in s for t in REQUIRED_TABLES):
            offenders.append(s[:120])
    assert not offenders, (
        "refinement/app/main.py runs ALTER TABLE on an operational "
        "table — omega_refinement isn't the owner:\n  "
        + "\n  ".join(offenders)
    )


def _all_init_sql() -> str:
    return "\n\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(INIT_DIR.glob("*.sql"))
    )


@pytest.mark.parametrize("table", REQUIRED_TABLES)
def test_required_table_is_provisioned_by_an_init_script(table):
    sql = _all_init_sql()
    pattern = rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table)}\b"
    assert re.search(pattern, sql, re.IGNORECASE), (
        f"No init script creates the `{table}` table. refinement reads "
        f"from this table — without an init-time CREATE, the service "
        f"won't be able to bootstrap on a fresh DB."
    )


def test_required_tables_appear_in_refinement_role_grants():
    sql = (INIT_DIR / "25_service_roles.sql").read_text(encoding="utf-8")
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


def test_lifespan_does_not_call_runtime_ddl_helpers():
    src = REFINEMENT_MAIN.read_text(encoding="utf-8")
    m = re.search(
        r"async def lifespan\(app: FastAPI\):(.*?)(?=^\w)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert m, "lifespan function not found in refinement/app/main.py"
    body = m.group(1)
    forbidden = ("CREATE TABLE", "CREATE INDEX", "ALTER TABLE")
    body_executable = "\n".join(
        ln for ln in body.splitlines()
        if not ln.lstrip().startswith("#")
    )
    body_executable = re.sub(r'""".*?"""', "", body_executable, flags=re.DOTALL)
    hits = [t for t in forbidden if t in body_executable]
    assert not hits, (
        f"refinement lifespan still contains DDL keywords ({hits}). "
        f"Move provisioning to infra/init/*.sql."
    )
