"""Shared plumbing for cap-free domain aggregates (Finance / Operations / Risk).

Mission 1 replicates the SuccessFactors Talent pattern
(``successfactors_talent_population``) for other business domains. Every
aggregate module built on top of this helper keeps the same contract:

* Gold reads open a dedicated asyncpg connection to ``GOLD_DATABASE_URL``
  (``gold_fetcher._gold_dsn``) inside a ``repeatable_read`` read-only
  transaction whose first statement sets ``app.tenant_id`` /
  ``app.workspace_id``, so native RLS and the explicit predicates agree.
* The physical relation is resolved ONLY through
  ``omega_publication.dataset_publication_heads``
  (``resolve_published_gold_relation``). Nothing here builds ``gold_<dataset>``
  names by string concatenation.
* The relation must exist (``to_regclass``) and expose every REQUIRED column
  before any aggregate SQL runs. OPTIONAL columns that are missing degrade the
  metric (``status='degraded'``) instead of raising.
* Identifiers (relation, columns) come from module-level allowlists only; every
  runtime value travels as a ``$n`` parameter.
* Only COUNT / SUM / AVG / GROUP BY results travel back; top-N breakdowns are
  bounded by ``MAX_GROUP_ROWS``. No function returns dataset rows.
* Failures degrade to a status instead of raising: missing head / relation /
  scope -> ``unavailable``; Postgres or network errors -> ``unavailable``.
"""

from __future__ import annotations

import calendar
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import asyncpg
from fastapi import HTTPException

from app.services.gold_publication_relation import (
    PublishedGoldRelation,
    published_relation_columns,
    resolve_published_gold_relation,
)
from app.services.intelligence.gold_fetcher import _gold_dsn
from app.services.intelligence.utils import workspace_scope

STATUS_READY = "ready"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"
STATUSES = frozenset({STATUS_READY, STATUS_DEGRADED, STATUS_UNAVAILABLE})

# Upper bound for any GROUP BY / top-N result set. Totals never depend on it:
# they always come from a separate un-limited aggregate query.
MAX_GROUP_ROWS = 50
DEFAULT_TOP_N = 5
GOLD_COMMAND_TIMEOUT_SECONDS = 10

# Same GUC statement as successfactors_talent_population / db_scope.SET_SCOPE_SQL.
SET_SCOPE_SQL = (
    "SELECT set_config('app.tenant_id', $1, true),"
    " set_config('app.workspace_id', $2, true)"
)
# Every Gold aggregate binds $1 = workspace_id and $2 = tenant_id.
GOLD_SCOPE_PREDICATE = "workspace_id::text = $1 AND tenant_id::text = $2"
SCOPE_COLUMNS = frozenset({"tenant_id", "workspace_id"})

_HEAD_PUBLISHED_AT_SQL = """
    SELECT h.published_at
      FROM omega_publication.dataset_publication_heads h
     WHERE h.tenant_id = $1 AND h.workspace_id = $2
       AND h.dataset = $3 AND h.layer = 'gold'
"""

_HTTP_REASONS = {
    400: "invalid_scope",
    403: "no_permission",
    404: "missing",
    503: "unavailable",
}


# ── Result contract ──────────────────────────────────────────────────────────


@dataclass
class AggregateResult:
    """Base contract shared by every domain aggregate result.

    ``supported`` is always True for a function that exists: metrics without a
    Gold relation are not shipped as stubs (see docs/data_gaps.md).
    ``proxy_note`` explains, for proxy metrics, what the number really measures
    and what it does NOT measure so the LLM never over-promises.
    """

    status: str = STATUS_UNAVAILABLE
    supported: bool = True
    proxy_note: str | None = None
    error: str | None = None
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return jsonable(asdict(self))


def jsonable(value: Any) -> Any:
    """Make aggregate payloads JSON-serialisable (Decimal, date, datetime)."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bounded_int(value: Any, default: int) -> int:
    """Sanitize an untrusted numeric input: bool, NaN, inf, text and None all
    fall back to ``default`` instead of raising (int(inf) raises OverflowError)."""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def clamp_top_n(
    value: Any, *, default: int = DEFAULT_TOP_N, upper: int = MAX_GROUP_ROWS
) -> int:
    """Bound every top-N request to [1, MAX_GROUP_ROWS]."""
    return max(1, min(_as_bounded_int(value, default), upper))


def clamp_months(value: Any, *, default: int = 1, upper: int = 24) -> int:
    return max(1, min(_as_bounded_int(value, default), upper))


# ── Calendar helpers (windows are computed in Python and bound as $n) ────────


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_of_date(value: date | datetime | None) -> date:
    if value is None:
        return utc_now().date()
    if isinstance(value, datetime):
        return value.date()
    return value


def as_of_datetime(value: datetime | None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def month_start(value: date) -> date:
    return value.replace(day=1)


def add_months(value: date, months: int) -> date:
    """Shift a first-of-month date by ``months`` (negative allowed)."""
    index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def business_days_in_month(value: date) -> int:
    """Monday..Friday count for the month containing ``value``."""
    days = calendar.monthrange(value.year, value.month)[1]
    return sum(
        1
        for day in range(1, days + 1)
        if date(value.year, value.month, day).weekday() < 5
    )


# ── Gold scope (dedicated connection, repeatable_read, GUCs) ────────────────


@dataclass
class GoldScope:
    conn: Any
    tenant_id: str
    workspace_id: str

    @property
    def scope_args(self) -> tuple[str, str]:
        """Positional args matching GOLD_SCOPE_PREDICATE ($1 workspace, $2 tenant)."""
        return (self.workspace_id, self.tenant_id)


@asynccontextmanager
async def open_gold_scope(user: dict | None) -> AsyncIterator[GoldScope]:
    """Mirror of ``successfactors_talent_population._scoped_connection`` plus
    the repeatable_read/readonly transaction and the GUC statement."""
    dsn = _gold_dsn()
    if not dsn:
        raise HTTPException(503, "gold database unavailable")
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id or not str(tenant_id).strip() or not str(workspace_id).strip():
        raise HTTPException(
            403, "gold dataset requires complete tenant/workspace scope"
        )
    conn = await asyncpg.connect(dsn, command_timeout=GOLD_COMMAND_TIMEOUT_SECONDS)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            yield GoldScope(conn=conn, tenant_id=tenant_id, workspace_id=workspace_id)
    finally:
        await conn.close()


# ── Relation resolution + column contract ───────────────────────────────────


@dataclass(frozen=True)
class ResolvedRelation:
    dataset: str
    relation: PublishedGoldRelation
    columns: frozenset[str]
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]
    published_at: datetime | None

    @property
    def sql(self) -> str:
        return self.relation.sql

    def has(self, column: str) -> bool:
        return column in self.columns

    def expr(self, column: str, expression: str, fallback: str) -> str:
        """Return ``expression`` when ``column`` exists, else ``fallback``.

        Both strings are literals chosen by the calling module (allowlisted
        identifiers), never derived from user input.
        """
        return expression if self.has(column) else fallback


async def resolve_relation(
    scope: GoldScope,
    dataset: str,
    *,
    required: frozenset[str] | set[str],
    optional: frozenset[str] | set[str] = frozenset(),
) -> ResolvedRelation:
    """Resolve the published Gold relation for ``dataset`` and validate columns.

    Raises HTTPException(404) when there is no published head or the relation
    does not exist (``to_regclass``); callers convert that into
    ``status='unavailable'`` through ``run_gold_aggregate``.
    """
    relation = await resolve_published_gold_relation(
        scope.conn, scope.tenant_id, scope.workspace_id, dataset
    )
    exists = bool(await scope.conn.fetchval("SELECT to_regclass($1)", relation.sql))
    if not exists:
        raise HTTPException(404, f"dataset unavailable: {dataset}")
    columns = frozenset(await published_relation_columns(scope.conn, relation))
    required_all = frozenset(required) | SCOPE_COLUMNS
    published_at = await scope.conn.fetchval(
        _HEAD_PUBLISHED_AT_SQL, scope.tenant_id, scope.workspace_id, dataset
    )
    return ResolvedRelation(
        dataset=dataset,
        relation=relation,
        columns=columns,
        missing_required=tuple(sorted(required_all - columns)),
        missing_optional=tuple(sorted(frozenset(optional) - columns)),
        published_at=published_at if isinstance(published_at, datetime) else None,
    )


async def resolve_optional_relation(
    scope: GoldScope,
    dataset: str,
    *,
    required: frozenset[str] | set[str],
    optional: frozenset[str] | set[str] = frozenset(),
) -> ResolvedRelation | None:
    """Like ``resolve_relation`` but a missing dataset yields ``None``
    (the metric degrades instead of becoming unavailable)."""
    try:
        return await resolve_relation(
            scope, dataset, required=required, optional=optional
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def invalid_schema_error(resolved: ResolvedRelation) -> str:
    return f"invalid_schema: {resolved.dataset} misses required columns " + ", ".join(
        resolved.missing_required
    )


def gold_evidence(
    resolved: ResolvedRelation, *, filters: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "gold_relation",
        "dataset": resolved.dataset,
        "relation": resolved.sql,
        "run_id": resolved.relation.run_id,
        "generation": resolved.relation.generation,
        "published_at": resolved.published_at,
        "missing_optional_columns": list(resolved.missing_optional),
        "filters": jsonable(filters),
    }


def status_for(*resolved: ResolvedRelation | None) -> str:
    """ready when every relation exposes all optional columns, else degraded."""
    for item in resolved:
        if item is None or item.missing_optional:
            return STATUS_DEGRADED
    return STATUS_READY


def missing_optional_columns(*resolved: ResolvedRelation | None) -> list[str]:
    out: list[str] = []
    for item in resolved:
        if item is None:
            continue
        out.extend(f"{item.dataset}.{column}" for column in item.missing_optional)
    return out


# ── Runner (fail-closed to a status, never an exception) ────────────────────


ResultT = TypeVar("ResultT", bound=AggregateResult)


def http_reason(exc: HTTPException) -> str:
    return _HTTP_REASONS.get(int(exc.status_code), "unavailable")


async def run_gold_aggregate(
    user: dict | None,
    compute: Callable[[GoldScope], Awaitable[ResultT]],
    unavailable: Callable[[str], ResultT],
) -> ResultT:
    """Open the Gold scope, run ``compute`` and map every failure to a result."""
    try:
        async with open_gold_scope(user) as scope:
            return await compute(scope)
    except HTTPException as exc:
        return unavailable(f"{http_reason(exc)}: {exc.detail}")
    except (asyncpg.PostgresError, OSError) as exc:
        return unavailable(f"unavailable: {exc}")
