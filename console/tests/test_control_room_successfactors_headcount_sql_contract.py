from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb
import pytest

from app.services.seed_packaged_datasets import _parse_dataset


REPO = Path(__file__).resolve().parents[2]
DATASETS = REPO / "cartridges" / "sap_successfactors" / "datasets"
BOOTSTRAP = REPO / "infra" / "init" / "82_sap_successfactors_datasets_seed.sql"
DIMENSIONS = (
    ("company", "company_id", "company_name"),
    ("location", "location_id", "location_name"),
    ("department", "department_id", "department_name"),
)
BLANK_FILLERS = tuple(
    chr(codepoint)
    for codepoint in (
        0x115F,
        0x1160,
        0x2800,
        0x3164,
        0xA8F9,
        0xFFA0,
        0x10AF6,
        0x1144E,
        0x11945,
        0x11C44,
        0x11C45,
        0x11F48,
        0x13441,
        0x13442,
        0x16FE4,
    )
)
WHITESPACE_CHARACTERS = tuple(
    chr(codepoint)
    for codepoint in range(sys.maxunicode + 1)
    if chr(codepoint).isspace()
)


def _path(dimension: str) -> Path:
    return DATASETS / f"sap_successfactors_headcount_by_{dimension}.sql"


def _embedded_sql(bootstrap: str, dataset: str) -> str:
    match = re.search(
        rf"\$seed\${re.escape(dataset)}\$seed\$.*?\$seed\$\n(.*?)\n\$seed\$, \$seed\$",
        bootstrap,
        re.DOTALL,
    )
    assert match, f"missing bootstrap definition: {dataset}"
    return match.group(1)


def test_bootstrap_and_packaged_headcount_sql_are_byte_identical():
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    expected_source = '["gold/sap_successfactors/sap_successfactors_employee_360"]'

    for dimension, _id_key, _name_key in DIMENSIONS:
        path = _path(dimension)
        canonical = path.read_text(encoding="utf-8").rstrip("\n")
        assert _embedded_sql(bootstrap, path.stem) == canonical
        assert expected_source in canonical
        assert "^[\\pZ]+$" in canonical
        packaged = _parse_dataset(path)
        assert packaged["sql"].rstrip("\n") == canonical
        assert packaged["sources"] == [
            "gold/sap_successfactors/sap_successfactors_employee_360"
        ]
    assert bootstrap.count(f"$seed${expected_source}$seed$::jsonb") == 3
    assert "COALESCE(company_name, '(sin nombre)')" not in bootstrap
    assert "COALESCE(location_name, '(sin nombre)')" not in bootstrap
    assert "COALESCE(department_name, '(sin nombre)')" not in bootstrap
    assert "ON CONFLICT (name) DO NOTHING" not in bootstrap
    assert "WHERE EXCLUDED.name IN" in bootstrap
    refresh_clause = bootstrap.split("WHERE EXCLUDED.name IN", 1)[1].split(");", 1)[0]
    refreshed = set(re.findall(r"'([a-z_]+)'", refresh_clause))
    assert refreshed == {_path(dimension).stem for dimension, *_keys in DIMENSIONS}


@pytest.mark.parametrize("dimension,id_key,name_key", DIMENSIONS)
def test_headcount_sql_executes_in_duckdb_and_rejects_invisible_labels(
    tmp_path: Path, dimension: str, id_key: str, name_key: str
):
    connection = duckdb.connect()
    connection.execute(
        """
        CREATE TABLE employees (
            company_id VARCHAR,
            company_name VARCHAR,
            location_id VARCHAR,
            location_name VARCHAR,
            department_id VARCHAR,
            department_name VARCHAR,
            is_active BOOLEAN
        )
        """
    )
    forbidden = [
        "\u200b",
        "\u2060",
        "\ufeff",
        *(f"Nombre{chr(codepoint)}" for codepoint in range(0x202A, 0x202F)),
        *(f"Nombre{chr(codepoint)}" for codepoint in range(0x2066, 0x206A)),
        "Nombre\ufe0f",
        "Nombre\U000e0100",
        "\u034f",
        *BLANK_FILLERS,
        *(f"Nombre{filler}" for filler in BLANK_FILLERS),
        *WHITESPACE_CHARACTERS,
        "\u200b\u2060\ufeff",
        "",
        "   ",
        "(SIN NOMBRE)",
    ]

    def row(identifier: str | None, label: str, active: bool = True):
        return (identifier, label, identifier, label, identifier, label, active)

    connection.executemany(
        "INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            row(None, "Dirección México"),
            row(None, "Dirección México"),
            row("inactive", "Dirección México", False),
            *(row(f"invalid-{index}", label) for index, label in enumerate(forbidden)),
        ],
    )
    parquet = tmp_path / "employee_360.parquet"
    escaped_path = str(parquet).replace("'", "''")
    connection.execute(f"COPY employees TO '{escaped_path}' (FORMAT PARQUET)")
    sql = _path(dimension).read_text(encoding="utf-8")
    sql = re.sub(
        r"'s3://\{bucket\}/gold/sap_successfactors/"
        r"sap_successfactors_employee_360/\*\*/\*.parquet'",
        f"'{escaped_path}'",
        sql,
    )

    rows = connection.execute(sql).fetchall()

    assert len(rows) == 1
    assert rows[0][0] is None, f"{id_key} must not receive a fabricated fallback"
    assert rows[0][1] == "Dirección México", name_key
    assert rows[0][2] == 2
    connection.close()
