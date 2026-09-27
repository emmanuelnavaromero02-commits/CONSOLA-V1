from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal


ColumnKind = Literal["text", "number", "temporal", "boolean", "other"]
CompileMode = Literal["execute", "definition"]
SourceKind = Literal["bronze", "dataset"]

OPS = frozenset(
    {
        "eq",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "contains",
        "not_contains",
        "starts_with",
        "is_empty",
        "is_not_empty",
        "in",
    }
)
MAX_FILTERS = 20
MAX_IN = 100
MAX_VALUE_LEN = 500
MAX_SORT = 3
MAX_COLUMNS = 100
MAX_IDENTIFIER_LEN = 255
MAX_SCHEMA_COLUMNS = 2000
BRONZE_LIMIT_CAP = 2000
DATASET_LIMIT_CAP = 10000
DEFAULT_LIMIT = 50
LATEST_DATE_COLUMN = "load_date"

_ALL_KINDS: frozenset[str] = frozenset({"text", "number", "temporal", "boolean", "other"})
_OP_KINDS: dict[str, frozenset[str]] = {
    "eq": frozenset({"text", "number", "temporal", "boolean"}),
    "neq": frozenset({"text", "number", "temporal", "boolean"}),
    "in": frozenset({"text", "number", "temporal", "boolean"}),
    "gt": frozenset({"number", "temporal"}),
    "gte": frozenset({"number", "temporal"}),
    "lt": frozenset({"number", "temporal"}),
    "lte": frozenset({"number", "temporal"}),
    "between": frozenset({"number", "temporal"}),
    "contains": frozenset({"text"}),
    "not_contains": frozenset({"text"}),
    "starts_with": frozenset({"text"}),
    "is_empty": _ALL_KINDS,
    "is_not_empty": _ALL_KINDS,
}
_NO_VALUE_OPS = frozenset({"is_empty", "is_not_empty"})
_COMPARISON_SQL = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_SORTABLE_KINDS = frozenset({"text", "number", "temporal", "boolean"})
_OP_LABELS = {
    "eq": "es igual a",
    "neq": "es distinto de",
    "gt": "es mayor que",
    "gte": "es mayor o igual que",
    "lt": "es menor que",
    "lte": "es menor o igual que",
    "between": "está entre",
    "contains": "contiene",
    "not_contains": "no contiene",
    "starts_with": "empieza con",
    "is_empty": "está vacío",
    "is_not_empty": "no está vacío",
    "in": "es uno de",
}
_KIND_LABELS = {
    "text": "texto",
    "number": "número",
    "temporal": "fecha",
    "boolean": "sí/no",
    "other": "estructura",
}

_SOURCE_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_NUMBER_RE = re.compile(r"^-?\d{1,38}(?:\.\d{1,12})?(?:[eE][-+]?\d{1,2})?$")
_MAX_NUMBER_DIGITS = 38
_MAX_NUMBER_SCALE = 12
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$"
)
# Mirrors of refinement's SQL guards (refinement/app/main.py, duckdb_engine.py); kept equal by tests.
ENGINE_SQL_FORBIDDEN_RE = re.compile(
    r"\b(attach|call|copy|create|delete|drop|export|import|insert|install|load|pragma|set|truncate|update|alter)\b",
    re.IGNORECASE,
)
ENGINE_SQL_COMMENT_RE = re.compile(r"(--|/\*)")
ENGINE_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
ENGINE_DANGEROUS_PATTERNS = (
    re.compile(r"\bread_(?:csv|text|json|blob|parquet_objects)\s*\(", re.IGNORECASE),
    re.compile(r"\bATTACH\b", re.IGNORECASE),
    re.compile(r"\bDETACH\b", re.IGNORECASE),
    re.compile(r"\bINSTALL\b", re.IGNORECASE),
    re.compile(r"\bLOAD\b", re.IGNORECASE),
    re.compile(r"\bPRAGMA\b", re.IGNORECASE),
    re.compile(r"\bCOPY\s+(?:.*\s+)?FROM\b", re.IGNORECASE | re.DOTALL),
    re.compile(
        r"\bSET\s+(?:GLOBAL|SESSION|memory_limit|threads|extension_directory)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bCREATE\s+(?:TABLE|VIEW|FUNCTION|MACRO|SECRET)\b", re.IGNORECASE),
    re.compile(
        r"\bDROP\s+(?:TABLE|VIEW|FUNCTION|MACRO|SECRET|SCHEMA|DATABASE)\b",
        re.IGNORECASE,
    ),
)
ENGINE_DANGEROUS_PATH_RE = re.compile(r"(?i)(file://|['\"]/(?:etc|proc|var)/)")
_STORAGE_SCHEME_RE = re.compile(r"(?i)\b(?:s3|gs|file)://")
_TRUE_WORDS = frozenset({"true", "1", "si", "sí", "verdadero", "yes"})
_FALSE_WORDS = frozenset({"false", "0", "no", "falso"})
_NUMBER_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "INT",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "INT1",
        "INT2",
        "INT4",
        "INT8",
        "SHORT",
        "LONG",
        "SIGNED",
        "FLOAT",
        "FLOAT4",
        "FLOAT8",
        "REAL",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
        "NUMBER",
    }
)
_TEXT_TYPES = frozenset(
    {"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR", "NVARCHAR", "UUID", "ENUM", "JSON"}
)
_TEMPORAL_TYPES = frozenset(
    {
        "DATE",
        "DATETIME",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "TIMESTAMP_S",
        "TIMESTAMP_MS",
        "TIMESTAMP_NS",
        "TIMESTAMP_US",
    }
)
_BOOLEAN_TYPES = frozenset({"BOOLEAN", "BOOL", "LOGICAL"})
_STORAGE_URI_RE = re.compile(r"(?i)\b(?:s3|gs|file|https?)://[^\s'\"),]+")
_SCOPE_MARKER_RE = re.compile(r"(?i)\b(tenant_id|workspace_id)(=|%3D)[^/%\s'\"),]+")


class ExplorerQueryError(ValueError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ExplorerColumn:
    name: str
    type: str
    kind: ColumnKind


@dataclass(frozen=True)
class ExplorerFilter:
    column: str
    op: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class ExplorerSort:
    column: str
    direction: Literal["asc", "desc"]


@dataclass(frozen=True)
class ExplorerSpec:
    columns: tuple[str, ...]
    filters: tuple[ExplorerFilter, ...]
    sort: tuple[ExplorerSort, ...]
    limit: int | None
    latest_only: bool


@dataclass(frozen=True)
class ExplorerSource:
    kind: SourceKind
    cartridge: str = ""
    entity: str = ""
    name: str = ""

    @property
    def logical(self) -> str:
        if self.kind == "bronze":
            return f"raw/{self.cartridge}/{self.entity}"
        return f"gold/{self.name}"


@dataclass(frozen=True)
class ExplorerQuery:
    sql: str
    params: tuple[str, ...]
    sql_display: str
    limit: int | None


def explorer_column_kind(raw_type: Any) -> ColumnKind:
    text = str(raw_type or "").strip().upper()
    if not text:
        return "other"
    if "[" in text or text.startswith(("STRUCT", "MAP", "UNION", "LIST")):
        return "other"
    base = re.split(r"[\s(]", text, maxsplit=1)[0]
    if base in _NUMBER_TYPES:
        return "number"
    if base in _TEXT_TYPES:
        return "text"
    if base in _TEMPORAL_TYPES:
        return "temporal"
    if base in _BOOLEAN_TYPES:
        return "boolean"
    return "other"


def _identifier_problem(name: str) -> str | None:
    if not name or len(name) > MAX_IDENTIFIER_LEN:
        return "longitud inválida"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return "caracteres de control"
    if "{" in name or "}" in name:
        return "llaves"
    return None


def normalize_explorer_schema(fields: Any) -> tuple[ExplorerColumn, ...]:
    columns: list[ExplorerColumn] = []
    seen: set[str] = set()
    for item in fields if isinstance(fields, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name in seen or _identifier_problem(name):
            continue
        raw_type = str(item.get("type") or "")
        seen.add(name)
        columns.append(ExplorerColumn(name=name, type=raw_type, kind=explorer_column_kind(raw_type)))
        if len(columns) >= MAX_SCHEMA_COLUMNS:
            break
    return tuple(columns)


def quote_identifier(name: str) -> str:
    problem = _identifier_problem(name)
    if problem:
        raise ExplorerQueryError(f"Nombre de columna no permitido ({problem}).")
    return '"' + name.replace('"', '""') + '"'


def parse_explorer_source(raw: Any) -> ExplorerSource:
    if not isinstance(raw, Mapping):
        raise ExplorerQueryError("Indica la fuente de datos a explorar.")
    kind = raw.get("kind")
    if kind == "bronze":
        cartridge = raw.get("cartridge")
        entity = raw.get("entity")
        if not isinstance(cartridge, str) or not _SOURCE_SEGMENT_RE.fullmatch(cartridge):
            raise ExplorerQueryError("La fuente de datos no es válida.")
        if not isinstance(entity, str) or not _SOURCE_SEGMENT_RE.fullmatch(entity):
            raise ExplorerQueryError("La entidad de la fuente no es válida.")
        return ExplorerSource(kind="bronze", cartridge=cartridge, entity=entity)
    if kind == "dataset":
        name = raw.get("name")
        if not isinstance(name, str) or not _DATASET_NAME_RE.fullmatch(name):
            raise ExplorerQueryError("El nombre del dataset no es válido.")
        return ExplorerSource(kind="dataset", name=name)
    raise ExplorerQueryError("Tipo de fuente no soportado: usa bronze o dataset.")


def bronze_execute_relation(source: ExplorerSource) -> str:
    return f"read_parquet('{source.logical}')"


def bronze_definition_relation(source: ExplorerSource) -> str:
    return (
        f"read_parquet('s3://{{bucket}}/{source.logical}/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    )


def dataset_relation(source: ExplorerSource) -> str:
    return f"pggold.gold_{source.name}"


def _scalar(value: Any, *, where: str) -> Any:
    if value is None:
        raise ExplorerQueryError(f"Falta el valor en {where}.")
    if isinstance(value, bool) or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExplorerQueryError(f"Valor numérico inválido en {where}.")
        return value
    raise ExplorerQueryError(f"Solo se aceptan textos, números o sí/no en {where}.")


def _string_list(raw: Any, *, label: str, maximum: int) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ExplorerQueryError(f"{label} debe ser una lista.")
    if len(raw) > maximum:
        raise ExplorerQueryError(f"{label}: máximo {maximum} elementos.")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ExplorerQueryError(f"{label} contiene un nombre inválido.")
        if item not in out:
            out.append(item)
    return tuple(out)


def parse_explorer_spec(body: Mapping[str, Any], *, limit_cap: int) -> ExplorerSpec:
    if not isinstance(body, Mapping):
        raise ExplorerQueryError("El cuerpo de la consulta debe ser un objeto.")
    columns = _string_list(body.get("columns"), label="Columnas", maximum=MAX_COLUMNS)

    raw_filters = body.get("filters") or []
    if not isinstance(raw_filters, list):
        raise ExplorerQueryError("Los filtros deben ser una lista.")
    if len(raw_filters) > MAX_FILTERS:
        raise ExplorerQueryError(f"Máximo {MAX_FILTERS} filtros por consulta.")
    filters: list[ExplorerFilter] = []
    for index, item in enumerate(raw_filters, start=1):
        where = f"el filtro {index}"
        if not isinstance(item, Mapping):
            raise ExplorerQueryError(f"Formato inválido en {where}.")
        column = item.get("column")
        op = item.get("op")
        if not isinstance(column, str) or not column:
            raise ExplorerQueryError(f"Elige una columna en {where}.")
        if not isinstance(op, str) or op not in OPS:
            raise ExplorerQueryError(f"Condición no soportada en {where}.")
        if op in _NO_VALUE_OPS:
            values: tuple[Any, ...] = ()
        elif op in {"in", "between"}:
            raw_values = item.get("values")
            if not isinstance(raw_values, list):
                raise ExplorerQueryError(f"Faltan los valores en {where}.")
            if op == "between" and len(raw_values) != 2:
                raise ExplorerQueryError(f"'está entre' necesita exactamente dos valores ({where}).")
            if op == "in" and not 1 <= len(raw_values) <= MAX_IN:
                raise ExplorerQueryError(f"'es uno de' acepta de 1 a {MAX_IN} valores ({where}).")
            values = tuple(_scalar(value, where=where) for value in raw_values)
        else:
            values = (_scalar(item.get("value"), where=where),)
        filters.append(ExplorerFilter(column=column, op=op, values=values))

    raw_sort = body.get("sort") or []
    if not isinstance(raw_sort, list):
        raise ExplorerQueryError("El orden debe ser una lista.")
    if len(raw_sort) > MAX_SORT:
        raise ExplorerQueryError(f"Máximo {MAX_SORT} criterios de orden.")
    sort: list[ExplorerSort] = []
    for item in raw_sort:
        if not isinstance(item, Mapping):
            raise ExplorerQueryError("Formato de orden inválido.")
        column = item.get("column")
        direction = item.get("direction", "asc")
        if not isinstance(column, str) or not column:
            raise ExplorerQueryError("Elige la columna para ordenar.")
        if direction not in ("asc", "desc"):
            raise ExplorerQueryError("La dirección de orden debe ser asc o desc.")
        if any(existing.column == column for existing in sort):
            raise ExplorerQueryError(f"La columna '{column}' aparece dos veces en el orden.")
        sort.append(ExplorerSort(column=column, direction=direction))

    raw_limit = body.get("limit", DEFAULT_LIMIT)
    if raw_limit is None:
        raw_limit = DEFAULT_LIMIT
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise ExplorerQueryError("El número de filas debe ser un entero.")
    if raw_limit < 1:
        raise ExplorerQueryError("El número de filas debe ser al menos 1.")
    latest_only = body.get("latest_only", False)
    if not isinstance(latest_only, bool):
        raise ExplorerQueryError("'Solo la carga más reciente' debe ser verdadero o falso.")
    return ExplorerSpec(
        columns=columns,
        filters=tuple(filters),
        sort=tuple(sort),
        limit=min(raw_limit, limit_cap),
        latest_only=latest_only,
    )


def _text_value(value: Any, *, column: str) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    if len(text) > MAX_VALUE_LEN:
        raise ExplorerQueryError(
            f"El valor para '{column}' es demasiado largo (máximo {MAX_VALUE_LEN} caracteres)."
        )
    if "\x00" in text:
        raise ExplorerQueryError(f"El valor para '{column}' contiene caracteres no permitidos.")
    return text


def _number_value(value: Any, *, column: str) -> str:
    if isinstance(value, bool):
        raise ExplorerQueryError(f"'{column}' es numérica: el valor debe ser un número.")
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not _NUMBER_RE.fullmatch(text):
        raise ExplorerQueryError(f"'{column}' es numérica: '{str(value)[:40]}' no es un número.")
    try:
        with localcontext() as context:
            context.prec = 120
            number = Decimal(text)
            if number == number.to_integral_value():
                canonical = format(number.quantize(Decimal(1)), "f")
            else:
                canonical = format(number.normalize(), "f")
    except InvalidOperation:
        raise ExplorerQueryError(f"'{column}' es numérica: '{str(value)[:40]}' no es un número.") from None
    canonical = "0" if canonical == "-0" else canonical
    whole, _, fraction = canonical.lstrip("-").partition(".")
    whole_limit = _MAX_NUMBER_DIGITS - (_MAX_NUMBER_SCALE if fraction else 0)
    if len(fraction) > _MAX_NUMBER_SCALE or len(whole) > whole_limit:
        raise ExplorerQueryError(
            f"'{column}': el número '{str(value)[:40]}' excede la precisión admitida "
            f"({_MAX_NUMBER_DIGITS} dígitos, {_MAX_NUMBER_SCALE} decimales)."
        )
    return canonical


def _temporal_value(value: Any, *, column: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    try:
        if _DATE_RE.fullmatch(text):
            return date.fromisoformat(text).isoformat()
        if _DATETIME_RE.fullmatch(text):
            return datetime.fromisoformat(text.replace("T", " ")).isoformat(sep=" ")
    except ValueError:
        pass
    raise ExplorerQueryError(
        f"'{column}' es una fecha: usa el formato AAAA-MM-DD (valor recibido: '{str(value)[:40]}')."
    )


def _boolean_value(value: Any, *, column: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return "true"
    if text in _FALSE_WORDS:
        return "false"
    raise ExplorerQueryError(f"'{column}' es sí/no: '{str(value)[:40]}' no es un valor válido.")


def _canonical_value(value: Any, kind: ColumnKind, *, column: str) -> str:
    if kind == "number":
        return _number_value(value, column=column)
    if kind == "temporal":
        return _temporal_value(value, column=column)
    if kind == "boolean":
        return _boolean_value(value, column=column)
    return _text_value(value, column=column)


def _definition_text_literal(value: str, *, column: str) -> str:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ExplorerQueryError(
            f"El valor para '{column}' contiene saltos de línea o caracteres de control; "
            "no se puede guardar en la definición."
        )
    if "{" in value or "}" in value:
        raise ExplorerQueryError(
            f"El valor para '{column}' contiene llaves ({{ }}), reservadas por el motor."
        )
    if _STORAGE_SCHEME_RE.search(value):
        raise ExplorerQueryError(
            f"El valor para '{column}' contiene una ruta de almacenamiento, que el motor bloquea "
            "en definiciones guardadas."
        )
    return "'" + value.replace("'", "''") + "'"


def _display_text_literal(value: str) -> str:
    cleaned = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in value)
    return "'" + cleaned.replace("'", "''") + "'"


class _Renderer:
    def __init__(self, mode: CompileMode | Literal["display"]) -> None:
        self.mode = mode
        self.params: list[str] = []

    def literal(self, value: str, kind: ColumnKind, *, column: str) -> str:
        if self.mode == "execute":
            self.params.append(value)
            if kind == "number":
                return "TRY_CAST(? AS HUGEINT)" if "." not in value else "TRY_CAST(? AS DECIMAL(38,12))"
            if kind == "temporal":
                return "TRY_CAST(? AS TIMESTAMP)"
            if kind == "boolean":
                return "TRY_CAST(? AS BOOLEAN)"
            return "?"
        if kind == "number":
            return value
        if kind == "temporal":
            return f"TIMESTAMP '{value}'"
        if kind == "boolean":
            return "TRUE" if value == "true" else "FALSE"
        if self.mode == "definition":
            return _definition_text_literal(value, column=column)
        return _display_text_literal(value)


def _folded(expr: str) -> str:
    return f"strip_accents(lower({expr}))"


def _filter_sql(
    item: ExplorerFilter,
    column: ExplorerColumn,
    values: tuple[str, ...],
    renderer: _Renderer,
) -> str:
    quoted = quote_identifier(column.name)
    kind = column.kind
    target = f"CAST({quoted} AS VARCHAR)" if kind == "text" else quoted
    op = item.op
    if op == "is_empty":
        return f"({quoted} IS NULL OR trim(CAST({quoted} AS VARCHAR)) = '')"
    if op == "is_not_empty":
        return f"({quoted} IS NOT NULL AND trim(CAST({quoted} AS VARCHAR)) <> '')"
    lits = [renderer.literal(value, kind, column=column.name) for value in values]
    if op == "eq":
        return f"{target} = {lits[0]}"
    if op == "neq":
        return f"{target} IS DISTINCT FROM {lits[0]}"
    if op in _COMPARISON_SQL:
        return f"{quoted} {_COMPARISON_SQL[op]} {lits[0]}"
    if op == "between":
        return f"{quoted} BETWEEN {lits[0]} AND {lits[1]}"
    if op == "in":
        return f"{target} IN ({', '.join(lits)})"
    folded_column = _folded(f"CAST({quoted} AS VARCHAR)")
    folded_value = _folded(lits[0])
    if op == "contains":
        return f"contains({folded_column}, {folded_value})"
    if op == "not_contains":
        return f"({quoted} IS NULL OR NOT contains({folded_column}, {folded_value}))"
    if op == "starts_with":
        return f"starts_with({folded_column}, {folded_value})"
    raise ExplorerQueryError("Condición no soportada.")


def _between_order(values: tuple[str, ...], kind: ColumnKind, *, column: str) -> None:
    low, high = values
    if kind == "number":
        ordered = Decimal(low) <= Decimal(high)
    else:
        ordered = datetime.fromisoformat(low) <= datetime.fromisoformat(high)
    if not ordered:
        raise ExplorerQueryError(
            f"En '{column}' el primer valor de 'está entre' debe ser menor o igual que el segundo."
        )


def _resolved_filters(
    spec: ExplorerSpec, by_name: Mapping[str, ExplorerColumn]
) -> list[tuple[ExplorerFilter, ExplorerColumn, tuple[str, ...]]]:
    resolved = []
    for item in spec.filters:
        column = by_name.get(item.column)
        if column is None:
            raise ExplorerQueryError(f"La columna '{item.column[:80]}' no existe en esta fuente.")
        if column.kind not in _OP_KINDS[item.op]:
            raise ExplorerQueryError(
                f"La condición '{_OP_LABELS[item.op]}' no aplica a '{column.name}' "
                f"(tipo {_KIND_LABELS[column.kind]})."
            )
        values = tuple(
            _canonical_value(value, column.kind, column=column.name) for value in item.values
        )
        if item.op == "between":
            _between_order(values, column.kind, column=column.name)
        if item.op == "in":
            values = tuple(dict.fromkeys(values))
        resolved.append((item, column, values))
    return resolved


def _require_engine_policy(sql: str, *, mode: CompileMode) -> None:
    masked = ENGINE_SINGLE_QUOTED_RE.sub("''", sql)
    found = None
    for pattern in (ENGINE_SQL_FORBIDDEN_RE, ENGINE_SQL_COMMENT_RE):
        found = found or pattern.search(masked)
    if found is None and ";" in masked:
        found = re.search(";", masked)
    for pattern in (*ENGINE_DANGEROUS_PATTERNS, ENGINE_DANGEROUS_PATH_RE):
        found = found or pattern.search(sql)
    if found is None:
        return
    fragment = " ".join(found.group(0).split())[:40]
    where = "columnas o valores" if mode == "definition" else "nombres de columna"
    raise ExplorerQueryError(
        f"La consulta contiene «{fragment}» en {where}, texto que el motor de datos bloquea "
        "por seguridad. Quita esa columna o valor, o usa la consulta SQL técnica."
    )


def compile_explorer_query(
    spec: ExplorerSpec,
    schema: Sequence[ExplorerColumn],
    *,
    relation_sql: str,
    limit_cap: int,
    mode: CompileMode,
    allow_latest_only: bool = True,
) -> ExplorerQuery:
    if mode not in ("execute", "definition"):
        raise ExplorerQueryError("Modo de compilación inválido.", status_code=500)
    if not schema:
        raise ExplorerQueryError("La fuente no tiene columnas legibles.", status_code=422)
    by_name = {column.name: column for column in schema}

    for name in spec.columns:
        if name not in by_name:
            raise ExplorerQueryError(f"La columna '{name[:80]}' no existe en esta fuente.")
    projection = ", ".join(quote_identifier(name) for name in spec.columns) or "*"

    filters = _resolved_filters(spec, by_name)

    order_parts = []
    for item in spec.sort:
        column = by_name.get(item.column)
        if column is None:
            raise ExplorerQueryError(f"La columna '{item.column[:80]}' no existe en esta fuente.")
        if column.kind not in _SORTABLE_KINDS:
            raise ExplorerQueryError(f"No se puede ordenar por '{column.name}' (estructura).")
        order_parts.append(
            f"{quote_identifier(column.name)} {item.direction.upper()} NULLS LAST"
        )

    latest_clause = ""
    if spec.latest_only:
        if not allow_latest_only:
            raise ExplorerQueryError(
                "'Solo la carga más reciente' aplica únicamente a fuentes bronze."
            )
        if LATEST_DATE_COLUMN not in by_name:
            raise ExplorerQueryError(
                "Esta fuente no tiene la columna load_date; no se puede limitar a la carga más reciente."
            )
        latest_clause = f"{quote_identifier(LATEST_DATE_COLUMN)} = '{{latest_date}}'"

    limit = None
    if mode == "execute":
        limit = min(spec.limit or DEFAULT_LIMIT, limit_cap)

    def render(renderer: _Renderer) -> str:
        conditions = [latest_clause] if latest_clause else []
        conditions.extend(
            _filter_sql(item, column, values, renderer) for item, column, values in filters
        )
        sql = f"SELECT {projection} FROM {relation_sql}"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if order_parts:
            sql += " ORDER BY " + ", ".join(order_parts)
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return sql

    renderer = _Renderer(mode)
    sql = render(renderer)
    _require_engine_policy(sql, mode=mode)
    display = sql if mode == "definition" else render(_Renderer("display"))
    return ExplorerQuery(sql=sql, params=tuple(renderer.params), sql_display=display, limit=limit)


def sanitize_explorer_detail(detail: Any, *, fallback: str) -> str:
    text = str(detail or "").strip()
    if not text:
        return fallback
    text = re.split(r"\bLINE \d+:", text, maxsplit=1)[0]
    text = _STORAGE_URI_RE.sub("[almacenamiento]", text)
    text = _SCOPE_MARKER_RE.sub(lambda match: f"{match.group(1)}=…", text)
    text = " ".join(text.split())
    return text[:300] or fallback
