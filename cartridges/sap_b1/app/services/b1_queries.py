"""Query plans for Business One tables: what to read and how to read it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Sequence

from app.core.b1_source import quote_ident, quote_schema

WATERMARK_UPDATE_TS = "b1_update_ts"
WATERMARK_INTEGER = "integer"
WATERMARK_KINDS = (WATERMARK_UPDATE_TS, WATERMARK_INTEGER)
UPDATE_TS_MAX = 235959
COMPANY_COLUMN = "_company"
SOURCE_UPDATED_COLUMN = "_source_updated_at"
MODES = ("full", "incremental", "historical")
ARROW_KINDS = ("int64", "decimal(19,6)", "timestamp", "string")
METADATA_COLUMNS = ("_extracted_at", "_run_id", "_source_entity", "_load_type", "_watermark_value")
DEFAULT_PAGE_SIZE = 2000
MAX_PAGE_SIZE = 100_000

_WM_DATE_ALIAS = "_wm_date"
_WM_TS_ALIAS = "_wm_ts"
_INT_WIDTH = 15
_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T")[:19]).date()
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (ValueError, ArithmeticError):
        return None


def combine_update_stamp(update_date: Any, update_ts: Any) -> datetime | None:
    """``UpdateDate`` + ``UpdateTS`` (HHMMSS) → one naive datetime, source clock."""
    day = _as_date(update_date)
    if day is None:
        return None
    hhmmss = _as_int(update_ts) or 0
    if not 0 <= hhmmss <= UPDATE_TS_MAX:
        return None
    hours, rest = divmod(hhmmss, 10_000)
    minutes, seconds = divmod(rest, 100)
    if minutes > 59 or seconds > 59:
        return None
    return datetime(day.year, day.month, day.day, hours, minutes, seconds)


@dataclass(frozen=True)
class Watermark:
    """A persisted position in a table, canonical text sorts monotonically."""

    kind: str
    at: datetime | None = None
    number: int | None = None

    @classmethod
    def parse(cls, kind: str | None, text: Any) -> "Watermark | None":
        """Return None when the stored text cannot be trusted."""
        if kind not in WATERMARK_KINDS:
            return None
        raw = str(text if text is not None else "").strip()
        if not raw:
            return None
        if kind == WATERMARK_INTEGER:
            number = _as_int(raw)
            return None if number is None or number < 0 else cls(kind=kind, number=number)
        try:
            at = datetime.fromisoformat(raw.replace(" ", "T")[:19])
        except ValueError:
            return None
        return cls(kind=kind, at=at.replace(tzinfo=None))

    @classmethod
    def from_stamp(cls, at: datetime) -> "Watermark":
        return cls(kind=WATERMARK_UPDATE_TS, at=at.replace(tzinfo=None, microsecond=0))

    @classmethod
    def from_number(cls, number: int) -> "Watermark":
        return cls(kind=WATERMARK_INTEGER, number=int(number))

    def text(self) -> str:
        if self.kind == WATERMARK_INTEGER:
            return f"{int(self.number or 0):0{_INT_WIDTH}d}"
        assert self.at is not None
        return self.at.strftime(_STAMP_FORMAT)

    def with_backoff(self, minutes: int) -> "Watermark":
        """Re-read the last ``minutes`` so a row committed while the previous run was reading is not lost."""
        if self.kind != WATERMARK_UPDATE_TS or not minutes:
            return self
        assert self.at is not None
        return Watermark(kind=self.kind, at=self.at - timedelta(minutes=int(minutes)))

    @property
    def day(self) -> date:
        assert self.at is not None
        return self.at.date()

    @property
    def hhmmss(self) -> int:
        assert self.at is not None
        return self.at.hour * 10_000 + self.at.minute * 100 + self.at.second

    def __lt__(self, other: "Watermark") -> bool:
        return self.text() < other.text()


def watermark_key(entity: str, company_alias: str) -> str:
    """Watermarks are tracked per entity and company: ``OINV@mx_mfg``."""
    return f"{entity}@{company_alias}"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            import json

            try:
                value = json.loads(text)
            except ValueError:
                value = [text]
        else:
            value = [part.strip() for part in text.split(",")]
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(frozen=True)
class EntityPlan:
    entity: str
    table: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    watermark_kind: str | None
    watermark_field: str | None
    watermark_ts_field: str | None
    parent: str | None
    parent_key: str | None
    join_key: str | None
    date_field: str | None
    page_size: int
    column_types: tuple[tuple[str, str], ...] = ()

    @property
    def incremental_capable(self) -> bool:
        return self.watermark_kind is not None

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (*self.columns, COMPANY_COLUMN, SOURCE_UPDATED_COLUMN)


def plan_from_config(config: dict[str, Any]) -> EntityPlan:
    """Turn one ``entities.yaml`` / ``entity_config`` row into a validated plan."""
    entity = str(config.get("entity") or "").strip()
    if not entity:
        raise ValueError("entity is required")
    table = str(config.get("table") or entity).strip()
    quote_ident(table)

    columns = tuple(_as_list(config.get("select_fields")))
    if not columns:
        raise ValueError(f"{entity}: select_fields must list the columns to read (never SELECT *)")
    for column in columns:
        quote_ident(column)
    if len(set(columns)) != len(columns):
        raise ValueError(f"{entity}: select_fields repeats a column")

    primary_key = tuple(_as_list(config.get("primary_key")))
    for column in primary_key:
        quote_ident(column)
        if column not in columns:
            raise ValueError(f"{entity}: primary key column {column} is not in select_fields")

    watermark_field = str(config.get("watermark_field") or "").strip() or None
    watermark_ts_field = str(config.get("watermark_ts_field") or "").strip() or None
    kind_text = str(config.get("watermark_format") or "").strip().lower() or None
    watermark_kind: str | None = None
    if watermark_field:
        quote_ident(watermark_field)
        if kind_text is None:
            kind_text = WATERMARK_UPDATE_TS if watermark_field == "UpdateDate" else WATERMARK_INTEGER
        if kind_text not in WATERMARK_KINDS:
            raise ValueError(f"{entity}: watermark_format must be one of {WATERMARK_KINDS}")
        watermark_kind = kind_text
        if watermark_kind == WATERMARK_UPDATE_TS:
            watermark_ts_field = watermark_ts_field or "UpdateTS"
            quote_ident(watermark_ts_field)
        else:
            watermark_ts_field = None

    parent = str(config.get("parent") or "").strip() or None
    parent_key = str(config.get("parent_key") or "").strip() or None
    join_key = str(config.get("join_key") or parent_key or "").strip() or None
    if parent:
        quote_ident(parent)
        if not parent_key:
            raise ValueError(f"{entity}: parent requires parent_key")
        quote_ident(parent_key)
        quote_ident(join_key or "")
        if join_key not in columns:
            raise ValueError(f"{entity}: join_key {join_key} is not in select_fields")
    else:
        parent_key = None
        join_key = None
        if watermark_kind and watermark_field not in columns:
            raise ValueError(f"{entity}: watermark_field {watermark_field} is not in select_fields")
        if watermark_kind == WATERMARK_UPDATE_TS and watermark_ts_field not in columns:
            raise ValueError(f"{entity}: watermark_ts_field {watermark_ts_field} is not in select_fields")

    date_field = str(config.get("date_field") or "").strip() or None
    if date_field:
        quote_ident(date_field)
        if not parent and date_field not in columns:
            raise ValueError(f"{entity}: date_field {date_field} is not in select_fields")

    page_size_raw = config.get("page_size")
    if page_size_raw is None or page_size_raw == "":
        page_size_raw = DEFAULT_PAGE_SIZE
    try:
        page_size = int(page_size_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{entity}: page_size must be an integer") from exc
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"{entity}: page_size must be 1..{MAX_PAGE_SIZE}")

    raw_types = config.get("column_types")
    if isinstance(raw_types, str) and raw_types.strip():
        import json

        try:
            raw_types = json.loads(raw_types)
        except ValueError as exc:
            raise ValueError(f"{entity}: column_types must be a mapping") from exc
    column_types: list[tuple[str, str]] = []
    if raw_types:
        if not isinstance(raw_types, dict):
            raise ValueError(f"{entity}: column_types must be a mapping")
        for column, kind in raw_types.items():
            if column not in columns:
                raise ValueError(f"{entity}: column_types names {column}, which is not in select_fields")
            if kind not in ARROW_KINDS:
                raise ValueError(f"{entity}: column type {kind!r} for {column} must be one of {ARROW_KINDS}")
        column_types = [(column, str(raw_types[column])) for column in columns if column in raw_types]

    return EntityPlan(
        entity=entity,
        table=table,
        columns=columns,
        primary_key=primary_key,
        watermark_kind=watermark_kind,
        watermark_field=watermark_field,
        watermark_ts_field=watermark_ts_field,
        parent=parent,
        parent_key=parent_key,
        join_key=join_key,
        date_field=date_field,
        page_size=page_size,
        column_types=tuple(column_types),
    )


def arrow_schema(plan: EntityPlan):
    """The parquet schema every file of this entity is written with."""
    if not plan.column_types:
        return None
    import pyarrow as pa

    kinds = dict(plan.column_types)
    mapping = {
        "int64": pa.int64(),
        "decimal(19,6)": pa.decimal128(19, 6),
        "timestamp": pa.timestamp("us"),
        "string": pa.string(),
    }
    fields = [pa.field(column, mapping[kinds.get(column, "string")]) for column in plan.columns]
    fields.extend(pa.field(column, pa.string()) for column in (COMPANY_COLUMN, SOURCE_UPDATED_COLUMN, *METADATA_COLUMNS))
    return pa.schema(fields)


def _at_midnight(day: date) -> datetime:
    """Bind a day as a datetime: B1 keeps its date columns as TIMESTAMP at midnight, and a datetime parameter compares."""
    return datetime.combine(day, time.min)


def _keyset_clause(alias: str, key: Sequence[str], after: Sequence[Any]) -> tuple[str, list[Any]]:
    """``pk > last`` for a composite key without row-value syntax (HANA lacks it)."""
    if len(key) != len(after):
        raise ValueError("keyset cursor does not match the primary key")
    branches: list[str] = []
    params: list[Any] = []
    for index, column in enumerate(key):
        equal = " AND ".join(f"{alias}.{quote_ident(key[j])} = ?" for j in range(index))
        greater = f"{alias}.{quote_ident(column)} > ?"
        branches.append(f"({equal} AND {greater})" if equal else greater)
        params.extend(after[j] for j in range(index))
        params.append(after[index])
    if len(branches) == 1:
        return branches[0], params
    return "(" + " OR ".join(branches) + ")", params


def _date_bounds(from_date: str | None, to_date: str | None) -> tuple[date | None, date | None]:
    start = _as_date(from_date) if from_date else None
    end = _as_date(to_date) if to_date else None
    if from_date and start is None:
        raise ValueError("from_date must be an ISO date")
    if to_date and end is None:
        raise ValueError("to_date must be an ISO date")
    if start and end and end < start:
        raise ValueError("to_date is before from_date")
    return start, end


def select_sql(
    plan: EntityPlan,
    schema: str,
    *,
    mode: str,
    watermark: Watermark | None = None,
    after_key: Sequence[Any] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> tuple[str, list[Any]]:
    """Render one page of ``plan`` in company ``schema`` as (sql, params)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    schema_sql = quote_schema(schema)
    t, h = "t", "h"
    use_join = bool(plan.parent)
    stamp_source = h if use_join else t

    select_cols = [f"{t}.{quote_ident(column)}" for column in plan.columns]
    if plan.watermark_kind == WATERMARK_UPDATE_TS:
        select_cols.append(f"{stamp_source}.{quote_ident(plan.watermark_field or '')} AS {quote_ident(_WM_DATE_ALIAS)}")
        select_cols.append(f"{stamp_source}.{quote_ident(plan.watermark_ts_field or '')} AS {quote_ident(_WM_TS_ALIAS)}")

    sql = f"SELECT {', '.join(select_cols)} FROM {schema_sql}.{quote_ident(plan.table)} {t}"
    if use_join:
        sql += (
            f" JOIN {schema_sql}.{quote_ident(plan.parent or '')} {h}"
            f" ON {h}.{quote_ident(plan.parent_key or '')} = {t}.{quote_ident(plan.join_key or '')}"
        )

    where: list[str] = []
    params: list[Any] = []

    if mode == "incremental" and watermark is not None:
        if watermark.kind != plan.watermark_kind:
            raise ValueError(f"{plan.entity}: watermark kind {watermark.kind} does not match the plan")
        if watermark.kind == WATERMARK_UPDATE_TS:
            d = f"{stamp_source}.{quote_ident(plan.watermark_field or '')}"
            ts = f"{stamp_source}.{quote_ident(plan.watermark_ts_field or '')}"
            where.append(f"({d} > ? OR ({d} = ? AND {ts} >= ?))")
            params.extend([_at_midnight(watermark.day), _at_midnight(watermark.day), watermark.hhmmss])
        else:
            where.append(f"{stamp_source}.{quote_ident(plan.watermark_field or '')} > ?")
            params.append(watermark.number)

    if mode == "historical":
        if not plan.date_field:
            raise ValueError(f"{plan.entity}: historical mode needs a date_field")
        start, end = _date_bounds(from_date, to_date)
        column = f"{stamp_source}.{quote_ident(plan.date_field)}"
        if start is not None:
            where.append(f"{column} >= ?")
            params.append(_at_midnight(start))
        if end is not None:
            where.append(f"{column} < ?")
            params.append(_at_midnight(end + timedelta(days=1)))

    if after_key is not None and plan.primary_key:
        clause, clause_params = _keyset_clause(t, plan.primary_key, after_key)
        where.append(clause)
        params.extend(clause_params)

    if where:
        sql += " WHERE " + " AND ".join(where)
    if plan.primary_key:
        sql += " ORDER BY " + ", ".join(f"{t}.{quote_ident(column)}" for column in plan.primary_key)
        sql += f" LIMIT {int(plan.page_size)}"
    return sql, params


def rows_to_records(
    plan: EntityPlan,
    company_alias: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> list[dict[str, Any]]:
    """Name the row values, stamp the company, derive ``_source_updated_at``."""
    names = [str(name) for name in columns]
    if names[: len(plan.columns)] != list(plan.columns):
        raise ValueError(f"{plan.entity}: result columns do not match the plan")
    wm_date_index = names.index(_WM_DATE_ALIAS) if _WM_DATE_ALIAS in names else None
    wm_ts_index = names.index(_WM_TS_ALIAS) if _WM_TS_ALIAS in names else None
    width = len(plan.columns)
    records: list[dict[str, Any]] = []
    for row in rows:
        record = {name: row[index] for index, name in enumerate(plan.columns)}
        record[COMPANY_COLUMN] = company_alias
        stamp = None
        if wm_date_index is not None and wm_ts_index is not None and len(row) > max(wm_date_index, wm_ts_index):
            stamp = combine_update_stamp(row[wm_date_index], row[wm_ts_index])
        elif plan.watermark_kind == WATERMARK_UPDATE_TS and not plan.parent and width == len(row):
            stamp = combine_update_stamp(
                record.get(plan.watermark_field or ""), record.get(plan.watermark_ts_field or "")
            )
        record[SOURCE_UPDATED_COLUMN] = stamp.strftime(_STAMP_FORMAT) if stamp else None
        records.append(record)
    return records


def keyset_cursor(plan: EntityPlan, record: dict[str, Any]) -> tuple[Any, ...] | None:
    if not plan.primary_key:
        return None
    return tuple(record[column] for column in plan.primary_key)


def next_watermark(plan: EntityPlan, records: Sequence[dict[str, Any]]) -> Watermark | None:
    """The highest position seen in ``records`` for the plan's watermark kind."""
    if not records or plan.watermark_kind is None:
        return None
    if plan.watermark_kind == WATERMARK_INTEGER:
        numbers = [_as_int(record.get(plan.watermark_field or "")) for record in records]
        numbers = [number for number in numbers if number is not None]
        return Watermark.from_number(max(numbers)) if numbers else None
    stamps = [
        datetime.strptime(record[SOURCE_UPDATED_COLUMN], _STAMP_FORMAT)
        for record in records
        if record.get(SOURCE_UPDATED_COLUMN)
    ]
    return Watermark.from_stamp(max(stamps)) if stamps else None
