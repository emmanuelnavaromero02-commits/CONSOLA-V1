from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("console", "workspace", "mcp-infra")

DIRECT_PGGOLD_PATTERNS = (
    (
        re.compile(r"\bATTACH\b[\s\S]{0,240}\bAS\s+pggold\b", re.IGNORECASE),
        "direct DuckDB ATTACH to pggold",
    ),
    (
        re.compile(r"\bDESCRIBE\s+(?:SELECT\s+\*\s+FROM\s+)?pggold\s*\.", re.IGNORECASE),
        "direct pggold schema introspection",
    ),
    (
        re.compile(r"\b(?:CREATE|DROP|DELETE|INSERT|UPDATE|TRUNCATE)\b[\s\S]{0,240}\bpggold\s*\.", re.IGNORECASE),
        "direct pggold mutation",
    ),
)


def find_direct_pggold_violations(files: dict[str, str]) -> list[str]:
    violations: list[str] = []
    for rel_path, text in files.items():
        normalized = rel_path.replace("\\", "/")
        if normalized.startswith(("refinement/", "infra/", "tests/")):
            continue
        for pattern, reason in DIRECT_PGGOLD_PATTERNS:
            if pattern.search(text):
                violations.append(f"{normalized}: {reason}")
        if normalized == "mcp-infra/app/tools/postgres.py" and "settings.pg_gold_" in text:
            violations.append(f"{normalized}: postgres MCP tools must not connect directly to Gold")
    return violations


def _python_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for dirname in SCAN_DIRS:
        for path in (REPO_ROOT / dirname).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            sources[rel] = path.read_text(encoding="utf-8")
    return sources


def test_no_direct_pggold_access_outside_refinement() -> None:
    assert find_direct_pggold_violations(_python_sources()) == []


def test_guard_detects_injected_direct_pggold_violation() -> None:
    violations = find_direct_pggold_violations(
        {
            "mcp-infra/app/bad.py": (
                "import duckdb\n"
                "con = duckdb.connect()\n"
                "con.execute(\"INSTALL postgres; LOAD postgres; "
                "ATTACH 'host=postgres_gold dbname=modecissions_gold' AS pggold (TYPE postgres);\")\n"
                "con.execute('DESCRIBE pggold.\"gold_sales\"')\n"
            )
        }
    )

    assert any("direct DuckDB ATTACH" in violation for violation in violations)
    assert any("schema introspection" in violation for violation in violations)


def test_console_and_workspace_gold_queries_route_through_refinement() -> None:
    forwarding_files = [
        REPO_ROOT / "console/app/main.py",
        REPO_ROOT / "console/app/routers/v1/data.py",
        REPO_ROOT / "workspace/app/main.py",
    ]
    for path in forwarding_files:
        text = path.read_text(encoding="utf-8")
        assert "pggold.gold_" in text
        assert "preview_transform" in text
        assert "REFINEMENT_URL" in text or "refinement_url" in text


def test_postgres_mcp_tools_disable_direct_gold_flag() -> None:
    text = (REPO_ROOT / "mcp-infra/app/tools/postgres.py").read_text(encoding="utf-8")

    assert "Gold database access must go through Refinement" in text
    assert "settings.pg_gold_" not in text
