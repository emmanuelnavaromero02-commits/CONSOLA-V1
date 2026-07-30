from __future__ import annotations

import pytest

from refinement.app.materialization_evidence import persist_materialization_evidence


class _Cursor:
    def __init__(self, *, fail_on: str | None = None):
        self.fail_on = fail_on
        self.calls: list[tuple[str, tuple | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params=None):
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("secret database detail")


class _Connection:
    def __init__(self, *, fail_on: str | None = None):
        self.cursor_value = _Cursor(fail_on=fail_on)
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def _scope():
    return {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
    }


def _lineage():
    return {
        "silver_name": "pnl_mensual",
        "cartridge_id": "replicon",
        "source_entity": "raw/replicon/OperationalTruthProbe",
        "source_load_date": "2026-07-30",
        "source_batch_id": "batch-1",
        "sql_def": "SELECT project_id FROM source",
        "column_mapping": {"project_id": "project"},
        "layer": "gold",
        "row_count": 3,
        "storage_uri": "s3://lakehouse/gold/probe.parquet",
    }


def _catalog():
    return {
        "name": "pnl_mensual",
        "layer": "gold",
        "cartridge": "replicon",
        "schema_fields": [{"name": "project_id", "type": "VARCHAR"}],
        "column_mapping": {"project_id": "project"},
        "description": "Operational truth probe",
    }


def test_lineage_and_catalog_commit_in_one_scoped_transaction():
    connection = _Connection()

    persist_materialization_evidence(
        lambda: connection,
        scope=_scope(),
        lineage=_lineage(),
        catalog=_catalog(),
    )

    statements = "\n".join(sql for sql, _params in connection.cursor_value.calls)
    assert "set_config('app.tenant_id'" in statements
    assert "INSERT INTO silver_lineage" in statements
    assert "INSERT INTO data_catalog" in statements
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed == 1


def test_catalog_failure_rolls_back_lineage_and_sanitizes_error():
    connection = _Connection(fail_on="INSERT INTO data_catalog")

    with pytest.raises(RuntimeError) as raised:
        persist_materialization_evidence(
            lambda: connection,
            scope=_scope(),
            lineage=_lineage(),
            catalog=_catalog(),
        )

    assert str(raised.value) == "materialization evidence unavailable"
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed == 1
