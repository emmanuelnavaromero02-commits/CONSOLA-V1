from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

import psycopg2
from psycopg2.extras import execute_values

from . import schema as b1
from .generator import Dataset


def load(dsn: str, dataset: Dataset, *, drop_existing: bool = False) -> Dict[str, Dict[str, int]]:
    loaded: Dict[str, Dict[str, int]] = {}
    with psycopg2.connect(dsn) as conn:
        for company in dataset.companies:
            rows = dataset.tables[company.alias]
            with conn.cursor() as cur:
                if drop_existing:
                    cur.execute(f"DROP SCHEMA IF EXISTS {b1.quote(company.schema)} CASCADE")
                cur.execute(b1.render_ddl(company.schema))
                counts: Dict[str, int] = {}
                for table in b1.TABLES:
                    values = rows.get(table) or []
                    if values:
                        columns = ", ".join(b1.quote(c) for c in b1.columns(table))
                        execute_values(
                            cur,
                            f"INSERT INTO {b1.quote(company.schema)}.{b1.quote(table)} ({columns}) VALUES %s",
                            values,
                            page_size=1000,
                        )
                    counts[table] = len(values)
            conn.commit()
            loaded[company.alias] = counts
    return loaded


def query(dsn: str, sql: str, params: Sequence[Any] | None = None) -> List[tuple]:
    with psycopg2.connect(dsn) as conn:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def scalar(dsn: str, sql: str, params: Sequence[Any] | None = None) -> Any:
    rows = query(dsn, sql, params)
    if len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError(f"expected one value, got {len(rows)} rows")
    return rows[0][0]


def table_ref(schema: str, table: str) -> str:
    return f"{b1.quote(schema)}.{b1.quote(table)}"


def month_key(rows: Iterable[tuple]) -> Dict[str, Any]:
    return {f"{int(y):04d}-{int(m):02d}": v for y, m, v in rows}
