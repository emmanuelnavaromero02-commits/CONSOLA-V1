from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation, localcontext
from typing import Any

try:
    from app.duckdb_engine import _sql_quote, validate_safe_identifier
    from app.publication_snapshot import PublicationInputOutdated
except ModuleNotFoundError:
    from refinement.app.duckdb_engine import _sql_quote, validate_safe_identifier
    from refinement.app.publication_snapshot import PublicationInputOutdated


logger = logging.getLogger(__name__)

CARTRIDGE = "sap_successfactors"
EXPOSURE_DATASET = "sap_successfactors_talent_attrition_exposure"
RECURRING_SOURCE = "silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest"
NON_RECURRING_SOURCE = (
    "silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest"
)
RISK_SOURCE = "gold/sap_successfactors/sap_successfactors_talent_retention_risk"
EXPOSURE_SOURCES = (RECURRING_SOURCE, NON_RECURRING_SOURCE, RISK_SOURCE)
KEY_ENV = "FIELD_ENCRYPTION_KEY"
MIN_GROUP_SIZE = 5
DOMINANCE_SHARE = Decimal("0.5")
SIGNIFICANT_DIGITS = 2
NON_RECURRING_WINDOW_DAYS = 365
PRIVACY_RULE = "aggregate_min5_dominance50_unitmax"
CONTRACT_VERSION = "talent_attrition_exposure.v4"
FREQUENCY_FACTORS: dict[str, int] = {
    "ANN": 1,
    "ANNUAL": 1,
    "MON": 12,
    "MONTHLY": 12,
    "BWK": 26,
    "BIWEEKLY": 26,
    "SMO": 24,
    "SEMIMONTHLY": 24,
    "WKL": 52,
    "WEEKLY": 52,
    "QTR": 4,
    "QUARTERLY": 4,
}
STATUS_PUBLISHED = "published"
STATUS_NO_GROUPS = "no_publishable_groups"
STATUS_MISSING_KEY = "missing_key"
STATUS_INVALID_KEY = "invalid_key"
OUTPUT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("risk_band", "VARCHAR"),
    ("currency", "VARCHAR"),
    ("headcount", "BIGINT"),
    ("annualized_comp_total", "DECIMAL(38, 2)"),
    ("annualized_comp_avg", "DECIMAL(38, 2)"),
    ("excluded_undecryptable", "BIGINT"),
    ("excluded_unknown_frequency", "BIGINT"),
    ("excluded_invalid_amount", "BIGINT"),
    ("excluded_non_recurring_365d", "BIGINT"),
)
CONSTANT_COLUMNS = ("privacy_rule", "contract_version", "generated_at")
_INPUT_STATE_SOURCE = f"materializer/{CARTRIDGE}/{EXPOSURE_DATASET}"
_KEY_ID_LABEL = b"omega/sap_successfactors/talent_attrition_exposure/key-id/v1"
_AMOUNT_LIMIT = Decimal(10) ** 16
_CENT = Decimal("0.01")
_FETCH_BATCH = 2000
_MAX_TEXT = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DECIMAL_LITERAL = re.compile(r"^-?[0-9]+\.[0-9]{2}$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_RECURRING_COLUMNS = frozenset(
    {"user_id", "pay_component", "paycomp_value", "frequency", "currency", "start_date", "end_date"}
)
_RISK_COLUMNS = frozenset({"user_id", "risk_band"})
_NON_RECURRING_COLUMNS = frozenset({"user_id", "currency", "paycomp_value", "pay_date"})
_RULES_DIGEST = hashlib.sha256(
    json.dumps(
        {
            "frequency_factors": FREQUENCY_FACTORS,
            "min_group_size": MIN_GROUP_SIZE,
            "dominance_share": str(DOMINANCE_SHARE),
            "significant_digits": SIGNIFICANT_DIGITS,
            "rounding_unit": "max(two_significant_figures, largest_amount_power_of_ten)",
            "grouping": ["risk_band", "currency"],
            "non_recurring_window_days": NON_RECURRING_WINDOW_DAYS,
            "duplicate_ciphertexts": "excluded",
            "privacy_rule": PRIVACY_RULE,
            "contract_version": CONTRACT_VERSION,
            "columns": OUTPUT_COLUMNS,
        },
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


class ExposureInputError(PublicationInputOutdated):
    pass


class ExposureAggregationError(ValueError):
    pass


@dataclass
class ExposureOutcome:
    status: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    counters: Counter = field(default_factory=Counter)


def is_exposure_dataset(dataset: dict[str, Any]) -> bool:
    return (
        str(dataset.get("cartridge") or "") == CARTRIDGE
        and str(dataset.get("name") or "") == EXPOSURE_DATASET
    )


def _read_key() -> str:
    return (os.environ.get(KEY_ENV) or "").strip()


def _key_state() -> str:
    key = _read_key()
    if not key:
        return "absent"
    if _fernet(key) is None:
        return "invalid"
    digest = hmac.new(key.encode("utf-8"), _KEY_ID_LABEL, hashlib.sha256).hexdigest()
    return "key:" + digest[:16]


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def exposure_input_state(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    if not is_exposure_dataset(dataset):
        return []
    return [
        {
            "source": _INPUT_STATE_SOURCE,
            "decryption_key": _key_state(),
            "rules": _RULES_DIGEST,
            "as_of": _utc_today().isoformat(),
        }
    ]


def _as_of(engine: Any) -> date:
    bound = ""
    items = getattr(engine, "_input_items", None)
    if callable(items):
        bound = str((items().get(_INPUT_STATE_SOURCE) or {}).get("as_of") or "")
    return date.fromisoformat(bound) if bound else _utc_today()


def _date_literal(value: date) -> str:
    text = value.isoformat()
    if not _ISO_DATE.fullmatch(text):
        raise ValueError("invalid evaluation date")
    return f"DATE '{text}'"


def _degraded(status: str) -> bool:
    return status != STATUS_PUBLISHED


def with_exposure_status(dataset: dict[str, Any], result: Any) -> Any:
    if not is_exposure_dataset(dataset) or not isinstance(result, dict):
        return result
    if isinstance(result.get("degraded"), bool) and result.get("status"):
        return result
    state = _key_state()
    if state == "absent":
        status = STATUS_MISSING_KEY
    elif state == "invalid":
        status = STATUS_INVALID_KEY
    else:
        status = STATUS_PUBLISHED if int(result.get("row_count") or 0) > 0 else STATUS_NO_GROUPS
    return {**result, "status": status, "degraded": _degraded(status)}


def _fernet(key: str) -> Any | None:
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode("utf-8"))
    except Exception:
        return None


def _clean_text(value: Any) -> str:
    return _CONTROL_CHARS.sub("", str(value or "")).strip()[:_MAX_TEXT]


def _reader(engine: Any, uri: str, user_context: dict | None) -> str:
    reader = f"read_parquet({engine._parquet_reader_argument(uri)}, union_by_name=true)"
    engine._validate_scoped_storage_sql(reader, user_context)
    return reader


def _source_uri(engine: Any, source: str, user_context: dict | None) -> str | None:
    layer, cartridge, name = source.split("/")
    return engine._latest_materialized_uri(layer, cartridge, name, user_context)


def _published_uri(engine: Any, source: str, user_context: dict | None) -> str:
    uri = _source_uri(engine, source, user_context)
    if not uri:
        raise ExposureInputError(source)
    return uri


def _require_columns(
    con: Any, reader: str, required: frozenset[str], source: str
) -> set[str]:
    probe = f"DESCRIBE SELECT * FROM {reader} LIMIT 0"  # nosec B608 - server-resolved quoted reader
    rows = con.execute(probe).fetchall()
    present = {str(row[0]).lower() for row in rows}
    if not required <= present:
        raise ExposureInputError(source)
    return present


def _risk_cte(risk_reader: str, risk_columns: set[str]) -> str:
    valid = (
        "COALESCE(invalid_score_input, TRUE) IS FALSE"
        if "invalid_score_input" in risk_columns
        else "FALSE"
    )
    score = (
        "TRY_CAST(retention_risk_score AS DOUBLE)"
        if "retention_risk_score" in risk_columns
        else "CAST(NULL AS DOUBLE)"
    )
    return f"""
risk AS (
    SELECT user_id, risk_band
    FROM (
        SELECT
            CAST(user_id AS VARCHAR) AS user_id,
            CAST(risk_band AS VARCHAR) AS risk_band,
            ROW_NUMBER() OVER (
                PARTITION BY CAST(user_id AS VARCHAR)
                ORDER BY {score} DESC NULLS LAST,
                         CAST(risk_band AS VARCHAR)
            ) AS _rn
        FROM {risk_reader}
        WHERE user_id IS NOT NULL
          AND {valid}
          AND CAST(risk_band AS VARCHAR) IN ('high', 'medium', 'low')
    )
    WHERE _rn = 1
)"""


def recurring_read_sql(
    risk_reader: str,
    recurring_reader: str,
    risk_columns: set[str],
    as_of: date,
    non_recurring_reader: str | None = None,
) -> str:
    today = _date_literal(as_of)
    census = [f"SELECT CAST(paycomp_value AS VARCHAR) AS token FROM {recurring_reader}"]
    if non_recurring_reader:
        census.append(
            f"SELECT CAST(paycomp_value AS VARCHAR) AS token FROM {non_recurring_reader}"
        )
    tokens = "\n        UNION ALL\n        ".join(census)
    return f"""
WITH {_risk_cte(risk_reader, risk_columns)},
recurring AS (
    SELECT
        CAST(user_id AS VARCHAR) AS user_id,
        CAST(pay_component AS VARCHAR) AS pay_component,
        CAST(paycomp_value AS VARCHAR) AS token,
        CAST(frequency AS VARCHAR) AS frequency,
        NULLIF(UPPER(TRIM(CAST(currency AS VARCHAR))), '') AS currency,
        TRY_CAST(start_date AS DATE) AS start_date,
        TRY_CAST(end_date AS DATE) AS end_date
    FROM {recurring_reader}
    WHERE user_id IS NOT NULL
),
token_census AS (
    SELECT token, COUNT(*) AS uses
    FROM (
        {tokens}
    ) AS tokens
    WHERE NULLIF(token, '') IS NOT NULL
    GROUP BY token
),
effective AS (
    SELECT
        recurring.*,
        ROW_NUMBER() OVER (
            PARTITION BY user_id, pay_component
            ORDER BY start_date DESC
        ) AS _rn
    FROM recurring
    WHERE start_date IS NOT NULL
      AND start_date <= {today}
      AND (end_date IS NULL OR end_date >= {today})
)
SELECT
    risk.risk_band,
    effective.currency,
    effective.user_id,
    effective.frequency,
    effective.token,
    COALESCE(token_census.uses, 0) AS token_uses
FROM effective
JOIN risk ON risk.user_id = effective.user_id
LEFT JOIN token_census ON token_census.token = effective.token
WHERE effective._rn = 1
ORDER BY 1, 2, 3, 4
"""


def non_recurring_count_sql(
    risk_reader: str, non_recurring_reader: str, risk_columns: set[str], as_of: date
) -> str:
    today = _date_literal(as_of)
    return f"""
WITH {_risk_cte(risk_reader, risk_columns)}
SELECT
    risk.risk_band,
    NULLIF(UPPER(TRIM(CAST(payments.currency AS VARCHAR))), '') AS currency,
    COUNT(*) AS records
FROM {non_recurring_reader} AS payments
JOIN risk ON risk.user_id = CAST(payments.user_id AS VARCHAR)
WHERE TRY_CAST(payments.pay_date AS DATE) > {today} - {int(NON_RECURRING_WINDOW_DAYS)}
  AND TRY_CAST(payments.pay_date AS DATE) <= {today}
GROUP BY 1, 2
"""


def _parse_amount(plain: bytes) -> Decimal | None:
    try:
        text = plain.decode("utf-8").strip()
        if not text:
            return None
        value = Decimal(text)
    except (UnicodeDecodeError, InvalidOperation, ValueError):
        return None
    if not value.is_finite() or abs(value) >= _AMOUNT_LIMIT:
        return None
    return value


def _significant_unit(value: Decimal) -> Decimal:
    return Decimal(1).scaleb(value.adjusted() - SIGNIFICANT_DIGITS + 1)


def _power_of_ten_ceiling(value: Decimal) -> Decimal:
    power = Decimal(1).scaleb(value.adjusted())
    return power if value == power else power.scaleb(1)


def round_significant(value: Decimal) -> Decimal:
    if not value:
        return Decimal(0).quantize(_CENT)
    rounded = value.quantize(_significant_unit(value), rounding=ROUND_HALF_UP)
    return rounded.quantize(_CENT, rounding=ROUND_HALF_UP)


def rounding_unit(total: Decimal, largest: Decimal) -> Decimal:
    return max(_significant_unit(total), _power_of_ten_ceiling(largest))


def round_to_unit(value: Decimal, unit: Decimal) -> Decimal:
    multiple = (value / unit).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return (multiple * unit).quantize(_CENT, rounding=ROUND_HALF_UP)


def aggregate_exposure(
    records: Any,
    non_recurring_counts: dict[tuple[str, str], int],
    fernet: Any,
) -> ExposureOutcome:
    counters: Counter = Counter()
    excluded: dict[tuple[str, str], Counter] = {}
    totals: dict[tuple[str, str], dict[str, Decimal]] = {}
    rows: list[dict[str, Any]] = []
    plain = value = people = positive = total = largest = published = None
    try:
        with localcontext(Context(prec=60)):
            for band, currency, user_id, frequency, token, uses in records:
                counters["records"] += 1
                if not currency:
                    counters["missing_currency"] += 1
                    continue
                group = (_clean_text(band), _clean_text(currency))
                group_excluded = excluded.setdefault(group, Counter())
                factor = FREQUENCY_FACTORS.get(str(frequency or "").strip().upper())
                if factor is None:
                    group_excluded["unknown_frequency"] += 1
                    counters["unknown_frequency"] += 1
                    continue
                if not token:
                    group_excluded["invalid_amount"] += 1
                    counters["invalid_amount"] += 1
                    continue
                if uses != 1:
                    counters["duplicate_ciphertext"] += 1
                    continue
                try:
                    plain = fernet.decrypt(str(token).encode("ascii"))
                except Exception:
                    group_excluded["undecryptable"] += 1
                    counters["undecryptable"] += 1
                    continue
                value = _parse_amount(plain)
                plain = None
                if value is None:
                    group_excluded["invalid_amount"] += 1
                    counters["invalid_amount"] += 1
                    continue
                people = totals.setdefault(group, {})
                people[str(user_id)] = people.get(str(user_id), Decimal(0)) + value * factor
                value = None
            for group in sorted(set(totals) | set(excluded)):
                people = totals.get(group, {})
                positive = [amount for amount in people.values() if amount > 0]
                counters["non_positive_total"] += len(people) - len(positive)
                if len(positive) < MIN_GROUP_SIZE:
                    if people:
                        counters["suppressed_small_groups"] += 1
                    continue
                total = sum(positive, Decimal(0))
                largest = max(positive)
                if largest > total * DOMINANCE_SHARE:
                    counters["suppressed_dominance_groups"] += 1
                    continue
                published = round_to_unit(total, rounding_unit(total, largest))
                if not published:
                    counters["suppressed_below_unit_groups"] += 1
                    continue
                group_excluded = excluded.get(group, Counter())
                band, currency = group
                rows.append(
                    {
                        "risk_band": band,
                        "currency": currency,
                        "headcount": len(positive),
                        "annualized_comp_total": published,
                        "annualized_comp_avg": round_significant(published / len(positive)),
                        "excluded_undecryptable": group_excluded["undecryptable"],
                        "excluded_unknown_frequency": group_excluded["unknown_frequency"],
                        "excluded_invalid_amount": group_excluded["invalid_amount"],
                        "excluded_non_recurring_365d": int(non_recurring_counts.get(group, 0)),
                    }
                )
    finally:
        for group_people in totals.values():
            group_people.clear()
        totals.clear()
        plain = value = people = positive = total = largest = published = None
    counters["non_recurring_excluded_365d"] = sum(
        int(v) for v in non_recurring_counts.values()
    )
    counters["published_groups"] = len(rows)
    return ExposureOutcome(
        status=STATUS_PUBLISHED if rows else STATUS_NO_GROUPS,
        rows=rows,
        counters=counters,
    )


def _literal(name: str, value: Any) -> str:
    if name in {"risk_band", "currency"}:
        return _sql_quote(_clean_text(value))
    if name in {"annualized_comp_total", "annualized_comp_avg"}:
        text = format(Decimal(value), "f")
        if not _DECIMAL_LITERAL.fullmatch(text):
            raise ExposureAggregationError("aggregate amount is not a 2-decimal value")
        return text
    return str(int(value))


def publication_sql(rows: list[dict[str, Any]]) -> str:
    tail = (
        f"{_sql_quote(PRIVACY_RULE)} AS privacy_rule, "
        f"{_sql_quote(CONTRACT_VERSION)} AS contract_version, "
        "CURRENT_TIMESTAMP AS generated_at"
    )
    if not rows:
        nulls = ", ".join(f"CAST(NULL AS {typ}) AS {name}" for name, typ in OUTPUT_COLUMNS)
        return f"SELECT {nulls}, {tail} WHERE FALSE"
    casts = ", ".join(f"CAST({name} AS {typ}) AS {name}" for name, typ in OUTPUT_COLUMNS)
    names = ", ".join(name for name, _ in OUTPUT_COLUMNS)
    values = ", ".join(
        "(" + ", ".join(_literal(name, row[name]) for name, _ in OUTPUT_COLUMNS) + ")"
        for row in rows
    )
    return f"SELECT {casts}, {tail} FROM (VALUES {values}) AS exposure({names})"


def _optional_reader(
    engine: Any, con: Any, user_context: dict | None
) -> str | None:
    uri = _source_uri(engine, NON_RECURRING_SOURCE, user_context)
    if not uri:
        return None
    reader = _reader(engine, uri, user_context)
    _require_columns(con, reader, _NON_RECURRING_COLUMNS, NON_RECURRING_SOURCE)
    return reader


def _non_recurring_counts(
    con: Any,
    risk_reader: str,
    reader: str | None,
    risk_columns: set[str],
    as_of: date,
) -> dict[tuple[str, str], int]:
    if not reader:
        return {}
    sql = non_recurring_count_sql(risk_reader, reader, risk_columns, as_of)
    counts: dict[tuple[str, str], int] = {}
    for band, currency, records in con.execute(sql).fetchall():
        if currency:
            key = (_clean_text(band), _clean_text(currency))
            counts[key] = counts.get(key, 0) + int(records)
    return counts


def _stream(cursor: Any):
    while True:
        batch = cursor.fetchmany(_FETCH_BATCH)
        if not batch:
            return
        yield from batch
        del batch


def compute_exposure(
    engine: Any, con: Any, user_context: dict | None
) -> ExposureOutcome:
    key = _read_key()
    if not key:
        return ExposureOutcome(status=STATUS_MISSING_KEY)
    fernet = _fernet(key)
    key = None
    if fernet is None:
        return ExposureOutcome(status=STATUS_INVALID_KEY)
    as_of = _as_of(engine)
    risk_reader = _reader(
        engine, _published_uri(engine, RISK_SOURCE, user_context), user_context
    )
    recurring_reader = _reader(
        engine, _published_uri(engine, RECURRING_SOURCE, user_context), user_context
    )
    risk_columns = _require_columns(con, risk_reader, _RISK_COLUMNS, RISK_SOURCE)
    _require_columns(con, recurring_reader, _RECURRING_COLUMNS, RECURRING_SOURCE)
    non_recurring_reader = _optional_reader(engine, con, user_context)
    non_recurring = _non_recurring_counts(
        con, risk_reader, non_recurring_reader, risk_columns, as_of
    )
    cursor = con.execute(
        recurring_read_sql(
            risk_reader, recurring_reader, risk_columns, as_of, non_recurring_reader
        )
    )
    outcome: ExposureOutcome | None = None
    try:
        outcome = aggregate_exposure(_stream(cursor), non_recurring, fernet)
    except Exception:
        outcome = None
    finally:
        fernet = cursor = None
    if outcome is None:
        raise ExposureAggregationError("compensation exposure aggregation failed")
    return outcome


def _log_outcome(outcome: ExposureOutcome) -> None:
    counters = outcome.counters
    logger.info(
        "successfactors compensation exposure: status=%s groups=%d "
        "suppressed_small_groups=%d suppressed_dominance_groups=%d "
        "suppressed_below_unit_groups=%d records=%d "
        "undecryptable=%d duplicate_ciphertext=%d unknown_frequency=%d invalid_amount=%d "
        "missing_currency=%d non_positive_total=%d non_recurring_excluded_365d=%d",
        outcome.status,
        int(counters["published_groups"]),
        int(counters["suppressed_small_groups"]),
        int(counters["suppressed_dominance_groups"]),
        int(counters["suppressed_below_unit_groups"]),
        int(counters["records"]),
        int(counters["undecryptable"]),
        int(counters["duplicate_ciphertext"]),
        int(counters["unknown_frequency"]),
        int(counters["invalid_amount"]),
        int(counters["missing_currency"]),
        int(counters["non_positive_total"]),
        int(counters["non_recurring_excluded_365d"]),
    )


def materialize_exposure_dataset(
    engine: Any, ds: dict, user_context: dict | None = None
) -> dict:
    name = str(ds.get("name") or "")
    validate_safe_identifier(name, "dataset")
    if not is_exposure_dataset(ds):
        raise ValueError(f"Unsupported SuccessFactors aggregate dataset: {name}")
    if str(ds.get("layer") or "") != "gold":
        raise ValueError("Invalid dataset layer")
    tenant, workspace = engine._scope_values(user_context)
    if not (tenant and workspace):
        raise ValueError("Gold materialization requires tenant_id and workspace_id")
    table = f"gold_{name}"
    validate_safe_identifier(table, "table")
    with engine._duckdb_lock:
        con = engine._conn()
        outcome = compute_exposure(engine, con, user_context)
        sql = publication_sql(outcome.rows)
        outcome.rows.clear()
        effective = engine._ensure_scope_columns(con, sql, user_context)
        engine._pg_gold_attach(con, user_context)
        engine._ensure_scoped_gold_table(con, table, effective)
        row_count = engine._replace_scoped_gold_rows(
            con, table, effective, tenant, workspace
        )
        engine._apply_gold_rls(table)
        snapshot_uri = engine._copy_scoped_gold_table_snapshot(
            con,
            table,
            engine._snapshot_path("gold", CARTRIDGE, name, user_context),
            tenant,
            workspace,
            user_context,
        )
        storage_uri = snapshot_uri or f"postgres_gold:{table}"
        schema_fields = [
            {"name": str(row[0]), "type": str(row[1])}
            for row in con.execute(
                f"DESCRIBE SELECT * FROM ({effective}) _exposure LIMIT 0"  # nosec B608 - built from literals
            ).fetchall()
        ]
    _log_outcome(outcome)
    engine._write_lineage(
        silver_name=name,
        cartridge_id=CARTRIDGE,
        source_entity=RECURRING_SOURCE,
        source_load_date=ds.get("source_load_date"),
        source_batch_id=ds.get("source_batch_id"),
        sql_def=str(ds.get("sql_def") or ds.get("sql") or ""),
        column_mapping=ds.get("column_mapping", {}),
        layer="gold",
        row_count=int(row_count or 0),
        storage_uri=storage_uri,
        user_context=user_context,
    )
    engine._prune_snapshots("gold", CARTRIDGE, name, user_context)
    engine._update_catalog(
        name,
        "gold",
        CARTRIDGE,
        schema_fields,
        ds.get("column_mapping", {}),
        ds.get("description", ""),
        user_context,
    )
    return {
        "name": name,
        "layer": "gold",
        "row_count": int(row_count or 0),
        "storage_uri": storage_uri,
        "status": outcome.status,
        "degraded": _degraded(outcome.status),
    }
