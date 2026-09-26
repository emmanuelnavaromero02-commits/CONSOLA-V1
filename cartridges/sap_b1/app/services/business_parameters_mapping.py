from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.b1_source import B1ConfigurationError

ENTITY = "BusinessParameters"
COLUMNS = ("Kind", "Company", "Period", "ParamKey", "ValueText", "ValueNum")
KINDS = ("account", "threshold", "setting", "branch")
ACCOUNT_KEYS = ("revenue", "cogs")
ANY = "*"
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9.\-]{1,15}\*?$")
_WAREHOUSE_RE = re.compile(r"^[A-Za-z0-9._\-]{1,8}$")
_LIMIT = Decimal("1e13")
_SIX = Decimal("0.000001")


@dataclass(frozen=True)
class ParameterSpec:
    key: str
    kind: str
    unit: str
    default: str | None
    case: str
    label: str


PARAMETER_CATALOG: tuple[ParameterSpec, ...] = (
    ParameterSpec("margin_min_pct", "threshold", "%", None, "finanzas", "Margen bruto mínimo aceptable; debajo, el cliente es destructor de margen"),
    ParameterSpec("reconciliation_tolerance_pct", "setting", "%", "1", "finanzas", "Diferencia máxima contra Finanzas para dar una fila por cuadrada (estrictamente menor)"),
    ParameterSpec("dq_min_pct", "threshold", "%", None, "general", "Calidad de datos mínima por control"),
    ParameterSpec("sellout_growth_min_pct", "threshold", "%", None, "ventas", "Crecimiento mínimo del sell-out de la distribuidora"),
    ParameterSpec("sellout_sellin_min_pct", "threshold", "%", None, "ventas", "Ratio sell-out / sell-in mínimo"),
    ParameterSpec("channel_days_max", "threshold", "días", None, "ventas", "Días de inventario en el canal máximos"),
    ParameterSpec("distributor_margin_min_pct", "threshold", "%", None, "ventas", "Margen mínimo de la distribuidora"),
    ParameterSpec("expiry_exposed_max_pct", "threshold", "%", None, "ventas", "Porcentaje máximo del inventario expuesto a caducidad"),
    ParameterSpec("expiry_horizon_days", "setting", "días", "90", "ventas", "Horizonte de alerta de caducidad"),
    ParameterSpec("expiry_red_days", "setting", "días", "30", "ventas", "Lotes que caducan en estos días o menos: alerta roja"),
    ParameterSpec("expiry_yellow_days", "setting", "días", "60", "ventas", "Lotes que caducan en estos días o menos: alerta amarilla"),
    ParameterSpec("coverage_red_days", "setting", "días", "30", "compras", "Cobertura por debajo de estos días: rojo"),
    ParameterSpec("coverage_yellow_days", "setting", "días", "60", "compras", "Cobertura por debajo de estos días: amarillo"),
    ParameterSpec("planning_horizon_days", "setting", "días", "90", "compras", "Horizonte del plan de producción para calcular la necesidad"),
    ParameterSpec("critical_materials_top_n", "setting", "artículos", "30", "compras", "Materias primas críticas priorizadas por consumo"),
    ParameterSpec("cost_variance_max_pct", "threshold", "%", "5", "compras", "Desviación máxima del costo real contra el estándar"),
    ParameterSpec("lead_time_tolerance_days", "setting", "días", "0", "compras", "Días de gracia antes de contar una entrega como tardía"),
    ParameterSpec("default_lead_time_days", "setting", "días", "7", "compras", "Tiempo de entrega cuando el artículo no tiene uno"),
    ParameterSpec("safety_days", "setting", "días", "7", "compras", "Días de inventario de seguridad"),
    ParameterSpec("review_period_days", "setting", "días", "14", "compras", "Periodo de revisión para el punto de pedido"),
)
CATALOG_BY_KEY = {spec.key: spec for spec in PARAMETER_CATALOG}


def catalog_payload() -> list[dict[str, Any]]:
    return [
        {"key": s.key, "kind": s.kind, "unit": s.unit, "default": s.default, "case": s.case, "label": s.label}
        for s in PARAMETER_CATALOG
    ]


@dataclass(frozen=True)
class BusinessParameter:
    kind: str
    company: str
    period: str
    key: str
    value_text: str
    value_num: Decimal | None


def _number(text: str) -> Decimal | None:
    try:
        value = Decimal(text.replace("_", ""))
    except InvalidOperation:
        return None
    if not value.is_finite() or abs(value) >= _LIMIT:
        return None
    return value.quantize(_SIX)


def _parse_entry(chunk: str) -> BusinessParameter:
    left, sep, value = chunk.partition("=")
    parts = [part.strip() for part in left.split(":")]
    value = value.strip()
    if not sep or len(parts) != 4 or not value:
        raise B1ConfigurationError("SAP_B1_BUSINESS_PARAMETERS entries must look like kind:company:period:key=value")
    kind, company, period, key = parts
    if kind not in KINDS:
        raise B1ConfigurationError(f"unknown parameter kind {kind!r}; use one of {KINDS}")
    if company != ANY and not _ALIAS_RE.fullmatch(company):
        raise B1ConfigurationError(f"invalid company alias in a business parameter: {company!r}")
    if period != ANY and not _PERIOD_RE.fullmatch(period):
        raise B1ConfigurationError(f"invalid period {period!r}; use YYYY-MM or *")
    if kind == "branch":
        if company == ANY or period != ANY or not _WAREHOUSE_RE.fullmatch(key) or len(value) > 100:
            raise B1ConfigurationError(
                "branches need a company, period *, the warehouse code as key and the branch name (up to 100 characters)"
            )
        return BusinessParameter(kind=kind, company=company, period=period, key=key, value_text=value, value_num=None)
    if not _KEY_RE.fullmatch(key):
        raise B1ConfigurationError(f"invalid parameter key {key!r}")
    number = _number(value)
    if kind == "account":
        codes = [code.strip() for code in value.split(",")]
        if period != ANY or key not in ACCOUNT_KEYS or not all(_ACCOUNT_RE.fullmatch(code) for code in codes):
            raise B1ConfigurationError(
                f"account lists need period *, one of {ACCOUNT_KEYS} and account codes (a trailing * is a prefix)"
            )
        value, number = ",".join(codes), None
    elif kind == "threshold" and number is None:
        raise B1ConfigurationError(f"threshold {key!r} needs a number")
    elif kind in ("threshold", "setting") and key in CATALOG_BY_KEY:
        expected = CATALOG_BY_KEY[key]
        if expected.kind != kind:
            raise B1ConfigurationError(f"{key!r} is a {expected.kind}, not a {kind}")
        if expected.unit != "texto" and number is None:
            raise B1ConfigurationError(f"{key!r} needs a number")
    return BusinessParameter(kind=kind, company=company, period=period, key=key, value_text=value, value_num=number)


def parse_business_parameters(spec: str) -> list[BusinessParameter]:
    parameters: list[BusinessParameter] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in re.split(r"[;\n]", spec or ""):
        chunk = raw.strip()
        if not chunk or chunk.startswith("#"):
            continue
        parameter = _parse_entry(chunk)
        identity = (parameter.kind, parameter.company, parameter.period, parameter.key)
        if identity in seen:
            raise B1ConfigurationError(f"duplicate business parameter {':'.join(identity)}")
        seen.add(identity)
        parameters.append(parameter)
    return parameters


def parameter_records(parameters: list[BusinessParameter]) -> list[dict[str, Any]]:
    loaded = BusinessParameter(kind="setting", company=ANY, period=ANY, key="loaded", value_text="1", value_num=Decimal("1.000000"))
    rows = [loaded, *(p for p in parameters if (p.kind, p.company, p.period, p.key) != ("setting", ANY, ANY, "loaded"))]
    return [
        {
            "Kind": p.kind,
            "Company": p.company,
            "Period": p.period,
            "ParamKey": p.key,
            "ValueText": p.value_text,
            "ValueNum": p.value_num,
            "_company": p.company,
            "_source_updated_at": None,
        }
        for p in sorted(rows, key=lambda item: (item.kind, item.company, item.period, item.key))
    ]


def arrow_schema():
    import pyarrow as pa

    from app.services.b1_queries import COMPANY_COLUMN, METADATA_COLUMNS, SOURCE_UPDATED_COLUMN

    fields = [pa.field(column, pa.string()) for column in COLUMNS[:-1]]
    fields.append(pa.field("ValueNum", pa.decimal128(19, 6)))
    fields.extend(pa.field(column, pa.string()) for column in (COMPANY_COLUMN, SOURCE_UPDATED_COLUMN, *METADATA_COLUMNS))
    return pa.schema(fields)
