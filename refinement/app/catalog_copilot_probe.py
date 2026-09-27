"""Server-internal DuckDB probes for the Catalog Copilot.

Every query here is built by the server from the published relation of a
dataset (the same relation the read guard serves) or from the latest Bronze
partition footers. Results are counts: row counts, non-null counts, pattern
hit counts, exact key checks and containment ratios. No sampled value ever
leaves this module; the only literal values it reads are Parquet footer
min/max of columns the caller explicitly allows (non-sensitive dates).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from app.catalog_copilot_rules import (
        MAX_PATTERN_COLUMNS,
        PATTERN_SAMPLE_ROWS,
        PII_PATTERNS,
        UPPERCASE_PATTERNS,
    )
except ModuleNotFoundError:
    from refinement.app.catalog_copilot_rules import (
        MAX_PATTERN_COLUMNS,
        PATTERN_SAMPLE_ROWS,
        PII_PATTERNS,
        UPPERCASE_PATTERNS,
    )

QUERY_TIMEOUT_SECONDS = 10.0
EXACT_KEY_MAX_ROWS = 5_000_000
MAX_KEY_CANDIDATES = 8
MAX_BRONZE_FILES = 50
CONTAINMENT_CHILD_CAP = 20_000
_LOAD_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class ProbeError(RuntimeError):
    """A probe query could not complete; carries a stable error code."""

    catalog_probe_error = True

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def probe_error_code(exc: BaseException) -> str | None:
    """Stable code of a probe failure, also across duplicate module imports."""
    if getattr(exc, "catalog_probe_error", False):
        return str(getattr(exc, "code", "") or "probe_query_failed")
    return None


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class PublishedRelation:
    sql: str
    head: dict[str, Any]
    gold: bool


@dataclass
class ColumnProbeResult:
    sampled_rows: int = 0
    non_null: dict[str, int] = field(default_factory=dict)
    pattern_hits: dict[str, dict[str, int]] = field(default_factory=dict)
    exact_keys: dict[str, bool] = field(default_factory=dict)
    keys_checked: bool = False


@dataclass(frozen=True)
class ContainmentResult:
    child_distinct: int
    orphan_values: int
    sampled: bool

    @property
    def ratio(self) -> float | None:
        if self.child_distinct <= 0:
            return None
        return round((self.child_distinct - self.orphan_values) / self.child_distinct, 4)


@dataclass
class BronzeFooter:
    source: str
    load_date: str
    num_rows: int
    files: int
    truncated: bool
    fields: list[dict[str, Any]] = field(default_factory=list)


class CatalogCopilotProbe:
    def __init__(self, engine: Any, *, timeout_seconds: float = QUERY_TIMEOUT_SECONDS):
        self.engine = engine
        self.timeout_seconds = timeout_seconds

    def _run(
        self, sql: str, *, gold_context: dict[str, Any] | None = None
    ) -> list[tuple]:
        with self.engine._duckdb_lock:
            con = self.engine._conn()
            if gold_context is not None:
                self.engine._pg_gold_attach(con, gold_context)
            timer = threading.Timer(self.timeout_seconds, con.interrupt)
            timer.daemon = True
            timer.start()
            try:
                return con.execute(sql).fetchall()
            except Exception as exc:
                text = f"{type(exc).__name__} {exc}".lower()
                if "interrupt" in text:
                    raise ProbeError("probe_timeout") from None
                raise ProbeError("probe_query_failed") from None
            finally:
                timer.cancel()

    def published_relation(
        self, dataset: dict[str, Any], ctx: dict[str, Any]
    ) -> PublishedRelation:
        try:
            head = self.engine._published_dataset_head(dataset, ctx)
            sql = self.engine._published_sql(dataset, head)
        except Exception:
            raise ProbeError("relation_unavailable") from None
        expand = getattr(self.engine, "_expand_partition_manifests", None)
        if callable(expand):
            sql = expand(sql, ctx)
        gold = str(dataset.get("layer") or "silver").lower() == "gold"
        return PublishedRelation(sql=sql, head=head, gold=gold)

    def column_probe(
        self,
        relation: PublishedRelation,
        ctx: dict[str, Any],
        *,
        text_cols: list[str],
        key_cols: list[str],
        row_count: int | None,
        sample: int = PATTERN_SAMPLE_ROWS,
    ) -> ColumnProbeResult:
        result = ColumnProbeResult()
        text_cols = list(dict.fromkeys(text_cols))[:MAX_PATTERN_COLUMNS]
        key_cols = list(dict.fromkeys(key_cols))[:MAX_KEY_CANDIDATES]
        gold_ctx = ctx if relation.gold else None
        if text_cols:
            limit = max(1, min(int(sample), PATTERN_SAMPLE_ROWS))
            projections = ["count(*)"]
            plan: list[tuple[str, str | None]] = []
            for column in text_cols:
                quoted = quote_identifier(column)
                projections.append(f"count({quoted})")
                plan.append((column, None))
                for pattern_name, pattern in PII_PATTERNS.items():
                    value = f"trim(CAST({quoted} AS VARCHAR))"
                    if pattern_name in UPPERCASE_PATTERNS:
                        value = f"upper({value})"
                    projections.append(
                        "count(*) FILTER (WHERE regexp_full_match("
                        f"{value}, {_quote_literal(pattern)}))"
                    )
                    plan.append((column, pattern_name))
            columns = ", ".join(quote_identifier(column) for column in text_cols)
            sql = (
                f"SELECT {', '.join(projections)} FROM ("
                f"SELECT {columns} FROM ({relation.sql}) _published LIMIT {limit}"
                ") _sample"
            )
            row = self._run(sql, gold_context=gold_ctx)[0]
            result.sampled_rows = int(row[0] or 0)
            for (column, pattern_name), value in zip(plan, row[1:], strict=True):
                count = int(value or 0)
                if pattern_name is None:
                    result.non_null[column] = count
                elif count:
                    result.pattern_hits.setdefault(column, {})[pattern_name] = count
        if key_cols and isinstance(row_count, int) and 0 < row_count <= EXACT_KEY_MAX_ROWS:
            projections = ["count(*)"]
            for column in key_cols:
                quoted = quote_identifier(column)
                projections.append(f"count(DISTINCT {quoted})")
                projections.append(f"count({quoted})")
            sql = f"SELECT {', '.join(projections)} FROM ({relation.sql}) _published"
            try:
                row = self._run(sql, gold_context=gold_ctx)[0]
            except ProbeError:
                return result
            total = int(row[0] or 0)
            for index, column in enumerate(key_cols):
                distinct = int(row[1 + 2 * index] or 0)
                non_null = int(row[2 + 2 * index] or 0)
                result.exact_keys[column] = total > 0 and distinct == total == non_null
            result.keys_checked = True
        return result

    def containment(
        self,
        child: PublishedRelation,
        child_column: str,
        parent: PublishedRelation,
        parent_column: str,
        ctx: dict[str, Any],
        *,
        child_cap: int = CONTAINMENT_CHILD_CAP,
    ) -> ContainmentResult:
        cap = max(1, int(child_cap))
        child_q = quote_identifier(child_column)
        parent_q = quote_identifier(parent_column)
        sql = (
            "WITH child AS ("
            f"SELECT DISTINCT CAST({child_q} AS VARCHAR) AS v FROM ({child.sql}) _c "
            f"WHERE {child_q} IS NOT NULL LIMIT {cap + 1}"
            "), parent AS ("
            f"SELECT DISTINCT CAST({parent_q} AS VARCHAR) AS k FROM ({parent.sql}) _p "
            f"WHERE {parent_q} IS NOT NULL"
            ") SELECT (SELECT count(*) FROM child), "
            "(SELECT count(*) FROM child c LEFT JOIN parent p ON c.v = p.k "
            "WHERE p.k IS NULL)"
        )
        gold_ctx = ctx if (child.gold or parent.gold) else None
        row = self._run(sql, gold_context=gold_ctx)[0]
        child_distinct = int(row[0] or 0)
        orphans = int(row[1] or 0)
        return ContainmentResult(
            child_distinct=child_distinct,
            orphan_values=orphans,
            sampled=child_distinct > cap,
        )

    def _latest_partition_glob(self, source: str, ctx: dict[str, Any]) -> tuple[str, str]:
        load_date = self.engine._resolve_latest_date(source, ctx)
        if not load_date or not _LOAD_DATE.fullmatch(str(load_date)):
            raise ProbeError("bronze_partition_unavailable")
        path = str(self.engine._bronze_path(source, ctx))
        if path.endswith("/**/*.parquet") and "load_date=" not in path:
            narrowed = path[: -len("/**/*.parquet")] + f"/load_date={load_date}/**/*.parquet"
        elif "load_date=*" in path:
            narrowed = path.replace("load_date=*", f"load_date={load_date}", 1)
        else:
            raise ProbeError("bronze_partition_unavailable")
        if "'" in narrowed:
            raise ProbeError("bronze_partition_unavailable")
        return str(load_date), narrowed

    def bronze_footer(
        self,
        source: str,
        ctx: dict[str, Any],
        *,
        allow_range: Callable[[str, str], bool] | None = None,
    ) -> BronzeFooter:
        load_date, narrowed = self._latest_partition_glob(source, ctx)
        rows = self._run(
            f"SELECT file FROM glob({_quote_literal(narrowed)}) ORDER BY file "
            f"LIMIT {MAX_BRONZE_FILES + 1}"
        )
        files = [str(row[0]) for row in rows if row and row[0]]
        truncated = len(files) > MAX_BRONZE_FILES
        files = files[:MAX_BRONZE_FILES]
        if not files:
            raise ProbeError("bronze_partition_unavailable")
        file_list = "[" + ", ".join(_quote_literal(path) for path in files) + "]"
        num_rows = int(
            self._run(f"SELECT sum(num_rows) FROM parquet_file_metadata({file_list})")[0][0]
            or 0
        )
        schema = self._run(
            f"DESCRIBE SELECT * FROM read_parquet({file_list}, union_by_name=true, "
            "hive_partitioning=false) LIMIT 0"
        )
        stats = {
            str(row[0]): row
            for row in self._run(
                "SELECT path_in_schema, sum(stats_null_count), sum(num_values), "
                "min(stats_min_value), max(stats_max_value) "
                f"FROM parquet_metadata({file_list}) GROUP BY path_in_schema"
            )
        }
        fields: list[dict[str, Any]] = []
        for row in schema:
            name, data_type = str(row[0]), str(row[1])
            item: dict[str, Any] = {"name": name, "type": data_type}
            stat = stats.get(name)
            if stat is not None:
                nulls = stat[1]
                item["null_count"] = int(nulls) if nulls is not None else None
                item["num_values"] = int(stat[2] or 0)
                if allow_range is not None and allow_range(name, data_type):
                    item["range"] = (
                        None if stat[3] is None else str(stat[3])[:32],
                        None if stat[4] is None else str(stat[4])[:32],
                    )
            fields.append(item)
        return BronzeFooter(
            source=source,
            load_date=load_date,
            num_rows=num_rows,
            files=len(files),
            truncated=truncated,
            fields=fields,
        )


__all__ = [
    "BronzeFooter",
    "CatalogCopilotProbe",
    "ColumnProbeResult",
    "ContainmentResult",
    "EXACT_KEY_MAX_ROWS",
    "MAX_KEY_CANDIDATES",
    "ProbeError",
    "PublishedRelation",
    "probe_error_code",
    "quote_identifier",
]
