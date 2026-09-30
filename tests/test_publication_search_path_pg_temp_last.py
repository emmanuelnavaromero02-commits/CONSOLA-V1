from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "infra/init_gold"
MIGRATION = GOLD / "46_publication_search_path_pg_temp_last.sql"
_DEFINITION = re.compile(
    r"CREATE OR REPLACE FUNCTION omega_publication\.(\w+)\((.*?)\bAS \$\w*\$",
    re.DOTALL,
)
_ALTER = re.compile(
    r"ALTER FUNCTION omega_publication\.(\w+)\([^)]*\)\n"
    r"  SET search_path = ([a-z_, ]+);"
)


def _pinned_publication_functions() -> dict[str, str]:
    pinned: dict[str, str] = {}
    for path in sorted(GOLD.rglob("*.sql")):
        if path == MIGRATION:
            continue
        for name, header in _DEFINITION.findall(path.read_text(encoding="utf-8")):
            match = re.search(
                r"SET search_path\s*=\s*([a-z_]+(?:\s*,\s*[a-z_]+)*)", header
            )
            if match:
                pinned[name] = re.sub(r"\s*,\s*", ", ", match.group(1).strip())
    return pinned


def _altered() -> dict[str, str]:
    return dict(_ALTER.findall(MIGRATION.read_text(encoding="utf-8")))


def test_every_pinned_publication_function_searches_pg_temp_last() -> None:
    pinned = _pinned_publication_functions()
    altered = _altered()

    assert len(pinned) == 17
    assert set(altered) == set(pinned)
    for name, path in altered.items():
        assert path.endswith(", pg_temp"), name
        base = pinned[name].removesuffix(", pg_temp")
        assert path == f"{base}, pg_temp", name
        assert path.startswith("pg_catalog"), name


def test_migration_only_alters_search_paths_and_records_itself() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    statements = [part.strip() for part in text.split(";") if part.strip()]

    alters = [item for item in statements if item.startswith("ALTER FUNCTION")]
    assert len(alters) == 17
    assert all("SET search_path" in item for item in alters)
    assert "GRANT" not in text and "REVOKE" not in text and "OWNER" not in text
    assert "BEGIN;" not in text and "COMMIT;" not in text
    assert "'gold/46_publication_search_path_pg_temp_last.sql'" in text
    assert "ON CONFLICT (filename) DO NOTHING" in text
