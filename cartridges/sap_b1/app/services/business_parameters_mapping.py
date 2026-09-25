from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.b1_source import B1ConfigurationError

ENTITY = "BusinessParameters"
COLUMNS = ("Kind", "Company", "Period", "ParamKey", "ValueText", "ValueNum")
KINDS = ("control", "account", "threshold", "setting")
CONTROL_KEYS = ("revenue_net", "cogs", "gross_profit")
ACCOUNT_KEYS = ("revenue", "cogs")
ANY = "*"
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9.\-]{1,15}\*?$")
_LIMIT = Decimal("1e13")
_SIX = Decimal("0.000001")


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
    if not _KEY_RE.fullmatch(key):
        raise B1ConfigurationError(f"invalid parameter key {key!r}")
    number = _number(value)
    if kind == "control":
        if company == ANY or period == ANY or key not in CONTROL_KEYS or number is None:
            raise B1ConfigurationError(
                f"control totals need a company, a YYYY-MM period, one of {CONTROL_KEYS} and a number"
            )
    elif kind == "account":
        codes = [code.strip() for code in value.split(",")]
        if period != ANY or key not in ACCOUNT_KEYS or not all(_ACCOUNT_RE.fullmatch(code) for code in codes):
            raise B1ConfigurationError(
                f"account lists need period *, one of {ACCOUNT_KEYS} and account codes (a trailing * is a prefix)"
            )
        value, number = ",".join(codes), None
    elif kind == "threshold" and number is None:
        raise B1ConfigurationError(f"threshold {key!r} needs a number")
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
