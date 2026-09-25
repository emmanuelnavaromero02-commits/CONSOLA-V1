from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.b1_source import B1ConfigurationError

ENTITY = "FinanceManualRun"
COLUMNS = ("Indicator", "Company", "Period", "Dimension", "DimKey", "Unit", "ValueNum")
INDICATORS = ("margen_bruto", "margen_contribucion", "destructores", "concentracion_top20", "margen_vendedor")
DIMENSIONS = ("total", "cliente", "sku", "canal", "vendedor")
UNITS = ("monto", "pct")
GROUP = "grupo"
HEADER = ("indicador", "empresa", "mes", "dimension", "clave", "valor")
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_LIMIT = Decimal("1e13")
_SIX = Decimal("0.000001")
_DEFAULT_UNIT = {"concentracion_top20": "pct"}
_ALLOWED_DIMENSIONS = {
    "margen_bruto": set(DIMENSIONS),
    "margen_contribucion": set(DIMENSIONS),
    "margen_vendedor": {"vendedor"},
    "destructores": {"cliente"},
    "concentracion_top20": {"total", "cliente"},
}


@dataclass(frozen=True)
class FinanceRow:
    indicator: str
    company: str
    period: str
    dimension: str
    key: str
    unit: str
    value: Decimal


def _number(text: str) -> Decimal | None:
    cleaned = text.strip().replace(",", "").replace("_", "").replace("$", "").replace("%", "")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite() or abs(value) >= _LIMIT:
        return None
    return value.quantize(_SIX)


def _fail(line: int, message: str) -> B1ConfigurationError:
    return B1ConfigurationError(f"línea {line}: {message}")


def parse_finance_run(text: str) -> list[FinanceRow]:
    if len((text or "").encode("utf-8")) > MAX_BYTES:
        raise B1ConfigurationError("la corrida supera 5 MB")
    reader = csv.reader(io.StringIO((text or "").lstrip("﻿")))
    rows: list[FinanceRow] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    header: list[str] | None = None
    for line, record in enumerate(reader, start=1):
        cells = [cell.strip() for cell in record]
        if not any(cells) or cells[0].startswith("#"):
            continue
        if header is None:
            header = [cell.lower() for cell in cells]
            missing = [name for name in HEADER if name not in header]
            if missing:
                raise _fail(line, f"faltan columnas {missing}; se esperan {list(HEADER)} y opcionalmente 'unidad'")
            continue
        values = dict(zip(header, cells))
        indicator = values.get("indicador", "").lower()
        company = values.get("empresa", "").lower()
        period = values.get("mes", "")
        dimension = values.get("dimension", "").lower()
        key = values.get("clave", "")
        unit = (values.get("unidad") or _DEFAULT_UNIT.get(indicator, "monto")).lower()
        if indicator not in INDICATORS:
            raise _fail(line, f"indicador {indicator!r} no es uno de {list(INDICATORS)}")
        if company != GROUP and not _ALIAS_RE.fullmatch(company):
            raise _fail(line, f"empresa {company!r} no es un alias válido ni 'grupo'")
        if not _PERIOD_RE.fullmatch(period):
            raise _fail(line, f"mes {period!r} debe ser AAAA-MM")
        if dimension not in _ALLOWED_DIMENSIONS[indicator]:
            raise _fail(line, f"{indicator} admite las dimensiones {sorted(_ALLOWED_DIMENSIONS[indicator])}")
        if company == GROUP and dimension != "total":
            raise _fail(line, "el grupo solo se compara en la dimensión total")
        if dimension == "total":
            key = ""
        elif not key or len(key) > 100:
            raise _fail(line, "la clave es obligatoria (hasta 100 caracteres) salvo en la dimensión total")
        if unit not in UNITS:
            raise _fail(line, f"unidad {unit!r} debe ser monto o pct")
        value = _number(values.get("valor", ""))
        if value is None:
            raise _fail(line, "valor no es un número")
        identity = (indicator, company, period, dimension, key)
        if identity in seen:
            raise _fail(line, "fila repetida para el mismo indicador, empresa, mes, dimensión y clave")
        seen.add(identity)
        rows.append(FinanceRow(indicator, company, period, dimension, key, unit, value))
        if len(rows) > MAX_ROWS:
            raise B1ConfigurationError(f"la corrida supera {MAX_ROWS} filas")
    if header is None or not rows:
        raise B1ConfigurationError("la corrida no tiene filas")
    return rows


def finance_records(rows: list[FinanceRow]) -> list[dict[str, Any]]:
    return [
        {
            "Indicator": row.indicator,
            "Company": row.company,
            "Period": row.period,
            "Dimension": row.dimension,
            "DimKey": row.key,
            "Unit": row.unit,
            "ValueNum": row.value,
            "_company": row.company,
            "_source_updated_at": None,
        }
        for row in rows
    ]


def summary(rows: list[FinanceRow]) -> dict[str, Any]:
    periods: dict[str, set[str]] = {}
    for row in rows:
        periods.setdefault(row.company, set()).add(row.period)
    return {
        "rows": len(rows),
        "indicators": sorted({row.indicator for row in rows}),
        "companies": {company: sorted(values) for company, values in sorted(periods.items())},
    }


def arrow_schema():
    import pyarrow as pa

    from app.services.b1_queries import COMPANY_COLUMN, METADATA_COLUMNS, SOURCE_UPDATED_COLUMN

    fields = [pa.field(column, pa.string()) for column in COLUMNS[:-1]]
    fields.append(pa.field("ValueNum", pa.decimal128(19, 6)))
    fields.extend(pa.field(column, pa.string()) for column in (COMPANY_COLUMN, SOURCE_UPDATED_COLUMN, *METADATA_COLUMNS))
    return pa.schema(fields)
