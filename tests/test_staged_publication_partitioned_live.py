from __future__ import annotations

import datetime as dt
import io
import uuid
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.parquet as pq

from tests.staged_publication_canaries import TENANT_A, WORKSPACE_A
from tests.staged_publication_live import LiveStack
from tests.test_staged_publication_integrity_live import _engine, _scope
from tests.test_staged_publication_live import staged_publication_live_stack

MONTHS = [f"2025-{month:02d}" for month in range(1, 7)]
ROWS_PER_MONTH = 25
UPSTREAM = """-- {name}  (silver)  cartridge: acceptance
-- sources: ["raw/acceptance/Movements"]
{header}
SELECT movement_id, strftime(posted_on, '%Y-%m') AS period, amount
FROM read_parquet('s3://{{bucket}}/raw/acceptance/Movements/**/*.parquet')
"""
DOWNSTREAM = """-- {name}  (silver)  cartridge: acceptance
SELECT period, COUNT(*) AS movements, SUM(amount) AS amount
FROM read_parquet('s3://{{bucket}}/silver/acceptance/{upstream}/**/*.parquet')
GROUP BY period
"""


def _put_raw(stack: LiveStack) -> int:
    rows = [
        (index * ROWS_PER_MONTH + row, dt.date(2025, index + 1, 1 + row), row * 2.5)
        for index in range(len(MONTHS))
        for row in range(ROWS_PER_MONTH)
    ]
    table = pa.table(
        {
            "movement_id": pa.array([row[0] for row in rows], pa.int64()),
            "posted_on": pa.array([row[1] for row in rows], pa.date32()),
            "amount": pa.array([row[2] for row in rows], pa.float64()),
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    stack.s3.put_object(
        Bucket="lakehouse",
        Key=(
            f"raw/acceptance/Movements/tenant_id={TENANT_A}/workspace_id={WORKSPACE_A}/"
            "load_date=2026-09-01/batch_id=partitioned/movements.parquet"
        ),
        Body=sink.getvalue(),
    )
    return len(rows)


def _published_uri(stack: LiveStack, dataset: str) -> str:
    return stack.sql(
        stack.reader_dsn,
        (TENANT_A, WORKSPACE_A),
        """SELECT r.object_uri FROM omega_publication.dataset_publication_heads h
             JOIN omega_publication.materialization_runs r
               ON r.materialization_run_id=h.materialization_run_id
            WHERE h.dataset=%s AND h.layer='silver'""",
        (dataset,),
    )[0][0]


def _rows(engine, dataset: dict) -> list[tuple]:
    result = engine.query_dataset(dataset, {}, 10_000, _scope())
    assert "error" not in result, result
    return sorted(tuple(sorted(row.items())) for row in result["data"])


def test_partitioned_silver_run_is_published_and_read_like_its_flat_twin(
    staged_publication_live_stack: LiveStack, monkeypatch
) -> None:
    stack = staged_publication_live_stack
    total = _put_raw(stack)
    engine = _engine(stack, monkeypatch)
    datasets = {}
    for name, header in (
        ("partitioned_movements", "-- partition_by: period"),
        ("flat_movements", "-- description: flat twin"),
    ):
        datasets[name] = {
            "name": name,
            "layer": "silver",
            "cartridge": "acceptance",
            "sources": ["raw/acceptance/Movements"],
            "sql_def": UPSTREAM.format(name=name, header=header),
        }
        assert engine.materialize(datasets[name], _scope())["row_count"] == total

    run, row_count, _lineage, catalog = stack.evidence("partitioned_movements")[0]
    key = urlsplit(_published_uri(stack, "partitioned_movements")).path.lstrip("/")
    set_prefix = key.rsplit("/", 1)[0] + "/"
    assert set_prefix.endswith(f"/_pending/{uuid.UUID(run).hex}/_partitioned/")
    assert row_count == total
    assert [field["name"] for field in catalog] == [
        "tenant_id", "workspace_id", "movement_id", "period", "amount",
    ]
    listed = stack.s3.list_objects_v2(Bucket="lakehouse", Prefix=set_prefix)["Contents"]
    periods = {
        item["Key"][len(set_prefix):].split("/", 1)[0]
        for item in listed
        if item["Key"] != key
    }
    assert periods == {f"period={month}" for month in MONTHS}
    assert "/_partitioned/" not in _published_uri(stack, "flat_movements")

    for upstream in list(datasets):
        downstream = {
            "name": f"{upstream}_monthly",
            "layer": "silver",
            "cartridge": "acceptance",
            "sources": [f"silver/acceptance/{upstream}"],
            "sql_def": DOWNSTREAM.format(name=f"{upstream}_monthly", upstream=upstream),
        }
        engine.materialize(downstream, _scope())
        datasets[downstream["name"]] = downstream

    assert _rows(engine, datasets["partitioned_movements"]) == _rows(
        engine, datasets["flat_movements"]
    )
    monthly = _rows(engine, datasets["partitioned_movements_monthly"])
    assert monthly == _rows(engine, datasets["flat_movements_monthly"])
    assert len(monthly) == len(MONTHS)
    if engine._con is not None:
        engine._con.close()
