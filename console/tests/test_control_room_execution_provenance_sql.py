from __future__ import annotations

import inspect
import subprocess
import sys

import pytest

import app.services.control_room.business_execution_provenance_sql as sql
from app.services.control_room import business_execution_status
from app.services.control_room import business_external_projection
from app.services.control_room import execution


@pytest.mark.parametrize(
    ("expression", "fragment"),
    (
        (
            "COALESCE(metadata, '{}'::jsonb) || $3::jsonb",
            sql.EXECUTED_METADATA_ARG3_SQL,
        ),
        (
            "COALESCE(metadata, '{}'::jsonb) || $4::jsonb",
            sql.EXECUTED_METADATA_ARG4_SQL,
        ),
        (
            "COALESCE(metadata, '{}'::jsonb) "
            '|| \'{"execution_status":"executed"}\'::jsonb',
            sql.EXECUTED_METADATA_STATUS_SQL,
        ),
    ),
)
def test_executed_metadata_fragment_preserves_atomic_provenance(
    expression: str,
    fragment: str,
) -> None:
    assert expression in fragment
    assert fragment.count("jsonb_set(") == 1
    assert "'{decision_eligibility_provenance}'" in fragment
    assert '"stage":"executed"' in fragment
    assert '"reason":"explicit_execution"' in fragment
    assert fragment.rstrip().endswith("true\n)")


@pytest.mark.parametrize(
    "writer",
    (execution, business_execution_status, business_external_projection),
)
def test_all_execution_writers_use_shared_provenance_sql(writer: object) -> None:
    source = inspect.getsource(writer)

    assert "_exec_sql.EXECUTED_METADATA_" in source
    assert "'{decision_eligibility_provenance,stage}'" not in source
    assert "'{decision_eligibility_provenance,reason}'" not in source


@pytest.mark.parametrize(
    "module_name",
    (
        "app.services.control_room.business_execution_status",
        "app.services.control_room.business_external_projection",
    ),
)
def test_execution_writer_imports_are_clean_in_isolated_process(
    module_name: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
