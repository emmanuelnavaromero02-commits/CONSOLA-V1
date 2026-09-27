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
import time
from collections import OrderedDict
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
# Child rows read before DISTINCT: bounds the scan of a large fact table.
CONTAINMENT_ROW_CAP = 200_000
VERIFIED_OBJECTS_CACHE = 1024
_LOAD_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_LOAD_DATE_PATTERN = r"load_date=([0-9]{4}-[0-9]{2}-[0-9]{2})"


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
        # Published objects are immutable per (uri, version, checksum): one
        # full download and hash per object and process is enough.
        self._verified: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._verified_lock = threading.Lock()

    def _require_scoped(self, sql: str, ctx: dict[str, Any]) -> None:
        """Every storage literal must pass the engine's exact-scope validator.

        The worker only ever runs with a tenant and workspace scope, so an
        unscoped path is refused here rather than read.
        """
        validate = getattr(self.engine, "_validate_scoped_storage_sql", None)
        if not callable(validate):
            return
        try:
            validate(sql, ctx)
        except ValueError:
            raise ProbeError("storage_out_of_scope") from None

    @staticmethod
    def _check(deadline: float | None) -> None:
        if deadline is not None and deadline - time.monotonic() <= 0:
            raise ProbeError("probe_budget_exhausted")

    def _published_head(
        self, dataset: dict[str, Any], ctx: dict[str, Any], deadline: float | None
    ) -> dict[str, Any]:
        store = getattr(self.engine, "_publication_store", None)
        scope_of = getattr(self.engine, "_publication_scope", None)
        verify = getattr(self.engine, "_verify_prepared_object", None)
        if store is None or not callable(scope_of) or not callable(verify):
            return self.engine._published_dataset_head(dataset, ctx)
        head = store.head(scope_of(dataset, ctx))
        if not head:
            raise RuntimeError("dataset is not published")
        if head.get("status") == "legacy_unverified":
            return head
        key = (
            str(head.get("object_uri") or ""),
            str(head.get("object_version") or ""),
            str(head.get("object_checksum") or ""),
        )
        with self._verified_lock:
            known = key in self._verified
        if not known:
            self._check(deadline)
            verify(head)
            with self._verified_lock:
                self._verified[key] = None
                while len(self._verified) > VERIFIED_OBJECTS_CACHE:
                    self._verified.popitem(last=False)
        return head

    def _timeout(self, deadline: float | None) -> float:
        if deadline is None:
            return self.timeout_seconds
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("probe_budget_exhausted")
        return min(self.timeout_seconds, remaining)

    def _run(
        self,
        sql: str,
        *,
        gold_context: dict[str, Any] | None = None,
        deadline: float | None = None,
    ) -> list[tuple]:
        timeout = self._timeout(deadline)
        with self.engine._duckdb_lock:
            con = self.engine._conn()
            if gold_context is not None:
                self.engine._pg_gold_attach(con, gold_context)
            timer = threading.Timer(timeout, con.interrupt)
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
        self,
        dataset: dict[str, Any],
        ctx: dict[str, Any],
        *,
        deadline: float | None = None,
    ) -> PublishedRelation:
        self._check(deadline)
        try:
            head = self._published_head(dataset, ctx, deadline)
            sql = self.engine._published_sql(dataset, head)
        except ProbeError:
            raise
        except Exception:
            raise ProbeError("relation_unavailable") from None
        expand = getattr(self.engine, "_expand_partition_manifests", None)
        if callable(expand):
            sql = expand(sql, ctx)
        self._require_scoped(sql, ctx)
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
        deadline: float | None = None,
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
            row = self._run(sql, gold_context=gold_ctx, deadline=deadline)[0]
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
                row = self._run(sql, gold_context=gold_ctx, deadline=deadline)[0]
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
        row_cap: int = CONTAINMENT_ROW_CAP,
        deadline: float | None = None,
    ) -> ContainmentResult:
        cap = max(1, int(child_cap))
        rows = max(1, int(row_cap))
        child_q = quote_identifier(child_column)
        parent_q = quote_identifier(parent_column)
        # The child is sampled before DISTINCT so a large fact table is never
        # scanned in full; only the (smaller) parent key set is read whole.
        sql = (
            "WITH child_rows AS ("
            f"SELECT CAST({child_q} AS VARCHAR) AS v FROM ({child.sql}) _c "
            f"WHERE {child_q} IS NOT NULL LIMIT {rows + 1}"
            f"), child AS (SELECT DISTINCT v FROM child_rows LIMIT {cap + 1}"
            "), parent AS ("
            f"SELECT DISTINCT CAST({parent_q} AS VARCHAR) AS k FROM ({parent.sql}) _p "
            f"WHERE {parent_q} IS NOT NULL"
            ") SELECT (SELECT count(*) FROM child_rows), (SELECT count(*) FROM child), "
            "(SELECT count(*) FROM child c LEFT JOIN parent p ON c.v = p.k "
            "WHERE p.k IS NULL)"
        )
        gold_ctx = ctx if (child.gold or parent.gold) else None
        row = self._run(sql, gold_context=gold_ctx, deadline=deadline)[0]
        sampled_rows = int(row[0] or 0)
        child_distinct = int(row[1] or 0)
        orphans = int(row[2] or 0)
        return ContainmentResult(
            child_distinct=child_distinct,
            orphan_values=orphans,
            sampled=child_distinct > cap or sampled_rows > rows,
        )

    def _latest_partition_glob(
        self, source: str, ctx: dict[str, Any], deadline: float | None = None
    ) -> tuple[str, str]:
        """Latest load_date from object keys only: no Parquet footer is read."""
        path = str(self.engine._bronze_path(source, ctx))
        if path.endswith("/**/*.parquet") and "load_date=" not in path:
            base = path[: -len("/**/*.parquet")]
        elif "/load_date=*" in path:
            base = path.split("/load_date=*", 1)[0]
        else:
            raise ProbeError("bronze_partition_unavailable")
        if "'" in base:
            raise ProbeError("bronze_partition_unavailable")
        listing = f"{base}/load_date=*/batch_id=*/*.parquet"
        sql = (
            "SELECT max(regexp_extract(file, "
            f"{_quote_literal(_LOAD_DATE_PATTERN)}, 1)) "
            f"FROM glob({_quote_literal(listing)})"
        )
        self._require_scoped(sql, ctx)
        row = self._run(sql, deadline=deadline)[0]
        load_date = str(row[0] or "")
        if not _LOAD_DATE.fullmatch(load_date):
            raise ProbeError("bronze_partition_unavailable")
        return load_date, f"{base}/load_date={load_date}/**/*.parquet"

    def bronze_footer(
        self,
        source: str,
        ctx: dict[str, Any],
        *,
        allow_range: Callable[[str, str], bool] | None = None,
        deadline: float | None = None,
    ) -> BronzeFooter:
        load_date, narrowed = self._latest_partition_glob(source, ctx, deadline)
        listing_sql = (
            f"SELECT file FROM glob({_quote_literal(narrowed)}) ORDER BY file "
            f"LIMIT {MAX_BRONZE_FILES + 1}"
        )
        self._require_scoped(listing_sql, ctx)
        rows = self._run(listing_sql, deadline=deadline)
        files = [str(row[0]) for row in rows if row and row[0]]
        truncated = len(files) > MAX_BRONZE_FILES
        files = files[:MAX_BRONZE_FILES]
        if not files:
            raise ProbeError("bronze_partition_unavailable")
        file_list = "[" + ", ".join(_quote_literal(path) for path in files) + "]"
        self._require_scoped(f"SELECT * FROM read_parquet({file_list})", ctx)
        num_rows = int(
            self._run(
                f"SELECT sum(num_rows) FROM parquet_file_metadata({file_list})",
                deadline=deadline,
            )[0][0]
            or 0
        )
        schema = self._run(
            f"DESCRIBE SELECT * FROM read_parquet({file_list}, union_by_name=true, "
            "hive_partitioning=false) LIMIT 0",
            deadline=deadline,
        )
        stats = {
            str(row[0]): row
            for row in self._run(
                "SELECT path_in_schema, sum(stats_null_count), sum(num_values), "
                "min(stats_min_value), max(stats_max_value) "
                f"FROM parquet_metadata({file_list}) GROUP BY path_in_schema",
                deadline=deadline,
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
