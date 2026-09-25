"""SQL access to SAP Business One company databases.

Business One keeps one database (HANA: one schema) per company. This module
opens that connection and validates everything that ends up inside SQL text:
table and column names come from the catalogue, schema names from the
company map, and every value travels as a bound parameter.

Two dialects share the same SQL:

* ``hana`` — production, through the SAP HANA client (``hdbcli``);
* ``postgres`` — the Business One-shaped test bed in
  ``tests/fixtures/sap_b1``, through ``psycopg2``.

The database host is normally a private address reached over the client's
VPN, so the HTTP egress guard used by the OData cartridges does not apply
here: this is a database session, not an HTTP request, and the destination
is fixed by configuration.

Environment variables (or the Console Vault connection ``sap_b1/default``):
    SAP_B1_DIALECT, SAP_B1_HOST, SAP_B1_PORT, SAP_B1_USER, SAP_B1_PASSWORD,
    SAP_B1_DATABASE, SAP_B1_COMPANIES ("alias=SCHEMA,alias=SCHEMA"),
    SAP_B1_ENCRYPT, SAP_B1_SSL_VALIDATE_CERTIFICATE.

Client-specific values (hosts, schema names, users) stay outside the
repository. Error messages never carry the password.
"""
from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Sequence

logger = logging.getLogger(__name__)

CARTRIDGE_ID = "sap_b1"
DIALECTS = ("hana", "postgres")
DEFAULT_PORTS = {"hana": 30015, "postgres": 5432}

# Same rule as refinement.app.duckdb_engine.SAFE_IDENTIFIER_RE: table and
# column names are identifiers, never quoted client input.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Business One company schemas may carry a hyphen (SBO_DEMO-MX); they are
# always double-quoted.
_SCHEMA_RE = re.compile(r"^[A-Za-z0-9_$][A-Za-z0-9_$\-]{0,127}$")
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MAX_ERROR_TEXT = 500


class B1SourceError(RuntimeError):
    """The database could not be reached or a query failed."""


class B1ConfigurationError(B1SourceError):
    """The cartridge lacks the settings needed to open a connection."""


class CircuitBreakerOpen(B1SourceError):
    pass


class CartridgeCircuitBreaker:
    """Trips on repeated connectivity failures only, and recovers on its own.

    Authentication failures and SQL errors never count: the process serves
    every tenant, so a wrong password typed three times in one workspace
    must not block extractions for the others. After ``cooldown_seconds``
    the breaker lets one probe through (half-open); a success closes it.
    """

    failures = 0
    threshold = 3
    cooldown_seconds = 60
    state = "HEALTHY"
    opened_at: float | None = None

    @classmethod
    def before_request(cls) -> None:
        if cls.failures < cls.threshold:
            return
        if cls.opened_at is not None and time.monotonic() - cls.opened_at >= cls.cooldown_seconds:
            cls.state = "HALF_OPEN"
            return
        cls.state = "UNHEALTHY"
        raise CircuitBreakerOpen("sap_b1 circuit breaker is UNHEALTHY")

    @classmethod
    def record_success(cls) -> None:
        cls.failures = 0
        cls.state = "HEALTHY"
        cls.opened_at = None

    @classmethod
    def record_failure(cls) -> None:
        cls.failures += 1
        if cls.failures >= cls.threshold:
            cls.state = "UNHEALTHY"
            cls.opened_at = time.monotonic()

    @classmethod
    def snapshot(cls) -> dict[str, Any]:
        return {"state": cls.state, "failures": cls.failures, "threshold": cls.threshold}

    @classmethod
    def reset(cls) -> None:
        cls.failures = 0
        cls.state = "HEALTHY"
        cls.opened_at = None


def quote_ident(name: str) -> str:
    """Double-quote a table or column name after validating it."""
    if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
        raise ValueError(f"Invalid identifier: {name!r}")
    return f'"{name}"'


def quote_schema(name: str) -> str:
    """Double-quote a company schema name after validating it."""
    if not isinstance(name, str) or not _SCHEMA_RE.fullmatch(name):
        raise ValueError("Invalid company schema name")
    return f'"{name}"'


@dataclass(frozen=True)
class Company:
    alias: str
    schema: str


def parse_companies(spec: str) -> list[Company]:
    """Parse ``alias=SCHEMA,alias=SCHEMA`` into companies.

    The alias is what the lakehouse records in ``_company``; the schema is
    only ever used to address the source. Duplicated or malformed entries
    are configuration errors, not something to guess around.
    """
    companies: list[Company] = []
    seen: set[str] = set()
    for chunk in (spec or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        alias, sep, schema = chunk.partition("=")
        alias, schema = alias.strip(), schema.strip()
        if not sep or not alias or not schema:
            raise B1ConfigurationError("SAP_B1_COMPANIES entries must look like alias=SCHEMA")
        if not _ALIAS_RE.fullmatch(alias):
            raise B1ConfigurationError(f"invalid company alias: {alias!r}")
        try:
            quote_schema(schema)
        except ValueError as exc:
            raise B1ConfigurationError(f"invalid company schema for alias {alias!r}") from exc
        if alias in seen:
            raise B1ConfigurationError(f"duplicate company alias: {alias!r}")
        seen.add(alias)
        companies.append(Company(alias=alias, schema=schema))
    return companies


def _truthy(value: Any, default: bool) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


@dataclass
class B1Config:
    dialect: str
    host: str
    port: int
    user: str
    password: str
    database: str
    companies: list[Company]
    encrypt: bool
    ssl_validate_certificate: bool
    connect_timeout: int
    missing: list[str] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return not self.missing

    def status(self) -> dict[str, Any]:
        """Configuration report safe to return over HTTP: no host, user or password."""
        return {
            "cartridge": CARTRIDGE_ID,
            "configured": self.configured,
            "missing": list(self.missing),
            "dialect": self.dialect or None,
            "companies": [company.alias for company in self.companies],
        }


def resolve_config(security_context: str | None = None) -> B1Config:
    """Resolve the connection settings from env, then Vault, then settings."""
    # Imported here, not at module level: the platform settings and the
    # Console Vault client are what ties this module to the cartridge
    # container. Everything above (identifiers, companies, connections,
    # sanitised errors) is also used by the Windows push agent in
    # ``connect/windows-agent``, which runs with neither.
    from app.core.config import settings
    from app.core.vault_client import get_secret_for_worker

    ctx = (security_context or "").strip() or None

    def pick(env_name: str, default: Any = "") -> str:
        value = get_secret_for_worker(CARTRIDGE_ID, env_name, security_context=ctx)
        if value:
            return str(value)
        return "" if default is None else str(default)

    dialect = (pick("SAP_B1_DIALECT", settings.sap_b1_dialect) or "hana").strip().lower()
    host = pick("SAP_B1_HOST", settings.sap_b1_host).strip()
    port_text = pick("SAP_B1_PORT", settings.sap_b1_port).strip()
    user = pick("SAP_B1_USER", settings.sap_b1_user)
    password = pick("SAP_B1_PASSWORD", settings.sap_b1_password)
    database = pick("SAP_B1_DATABASE", settings.sap_b1_database).strip()
    companies_spec = pick("SAP_B1_COMPANIES", settings.sap_b1_companies)
    encrypt = _truthy(pick("SAP_B1_ENCRYPT", ""), settings.sap_b1_encrypt)
    ssl_validate = _truthy(
        pick("SAP_B1_SSL_VALIDATE_CERTIFICATE", ""), settings.sap_b1_ssl_validate_certificate
    )

    missing: list[str] = []
    if dialect not in DIALECTS:
        missing.append("SAP_B1_DIALECT")
    for name, value in (
        ("SAP_B1_HOST", host),
        ("SAP_B1_USER", user),
        ("SAP_B1_PASSWORD", password),
    ):
        if not value:
            missing.append(name)
    if dialect == "postgres" and not database:
        missing.append("SAP_B1_DATABASE")

    port = DEFAULT_PORTS.get(dialect, 0)
    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            port = 0
        if not 0 < port < 65536:
            missing.append("SAP_B1_PORT")

    companies: list[Company] = []
    if not companies_spec.strip():
        missing.append("SAP_B1_COMPANIES")
    else:
        try:
            companies = parse_companies(companies_spec)
        except B1ConfigurationError as exc:
            logger.warning("SAP_B1_COMPANIES rejected: %s", exc)
            missing.append("SAP_B1_COMPANIES")
        if not companies and "SAP_B1_COMPANIES" not in missing:
            missing.append("SAP_B1_COMPANIES")

    return B1Config(
        dialect=dialect,
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        companies=companies,
        encrypt=encrypt,
        ssl_validate_certificate=ssl_validate,
        connect_timeout=max(1, int(settings.sap_b1_connect_timeout_seconds or 15)),
        missing=missing,
    )


def _secrets_of(config: "B1Config") -> tuple[str, ...]:
    """Values a driver message may quote back and that must not travel:
    the password first, then host, user, database and the company schemas."""
    values = [config.password, config.host, config.user, config.database]
    values.extend(company.schema for company in config.companies)
    return tuple(dict.fromkeys(value for value in values if value and len(value) >= 3))


def _sanitize(text: str, secrets: Sequence[str]) -> str:
    out = str(text or "")
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            out = out.replace(secret, "***")
    return out[:_MAX_ERROR_TEXT]


class Connection:
    """A read-only DB-API session that renders ``?`` placeholders for its driver."""

    def __init__(self, raw: Any, placeholder: str, secrets: Sequence[str], dialect: str = "postgres") -> None:
        self._raw = raw
        self._placeholder = placeholder
        self._secrets = tuple(secrets)
        self.dialect = dialect

    def render(self, sql: str) -> str:
        if self._placeholder == "?":
            return sql
        return sql.replace("?", self._placeholder)

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]:
        """Run one statement; return the column names and every row."""
        try:
            cursor = self._raw.cursor()
            try:
                cursor.execute(self.render(sql), tuple(params))
                columns = [str(desc[0]) for desc in (cursor.description or [])]
                rows = [tuple(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
        except B1SourceError:
            raise
        except Exception as exc:
            raise B1SourceError(
                f"query failed ({type(exc).__name__}): {_sanitize(str(exc), self._secrets)}"
            ) from exc
        return columns, rows

    def source_now(self) -> datetime:
        """The database server's clock, the clock Business One stamps with.

        Read once per run so a watermark never advances past the moment the
        run started: a document edited behind the cursor while a long run
        was reading keeps a stamp older than the newest row seen, and the
        next cycle must still reach it.
        """
        sql = "SELECT CURRENT_TIMESTAMP FROM DUMMY" if self.dialect == "hana" else "SELECT LOCALTIMESTAMP(0)"
        _columns, rows = self.fetch_all(sql)
        value = rows[0][0] if rows and rows[0] else None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None, microsecond=0)
        parsed = datetime.fromisoformat(str(value).replace(" ", "T")[:19])
        return parsed.replace(tzinfo=None)

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:  # pragma: no cover - best effort
            pass


def _open_postgres(config: B1Config) -> Connection:
    import psycopg2

    raw = psycopg2.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        dbname=config.database,
        connect_timeout=config.connect_timeout,
        application_name="omega-sap_b1",
    )
    raw.set_session(readonly=True, autocommit=True)
    return Connection(raw, "%s", _secrets_of(config), dialect="postgres")


def _open_hana(config: B1Config) -> Connection:
    try:
        from hdbcli import dbapi
    except ImportError as exc:  # pragma: no cover - depends on the image
        raise B1SourceError("hdbcli is not installed; the hana dialect needs the SAP HANA client") from exc

    kwargs: dict[str, Any] = {
        "address": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "encrypt": config.encrypt,
        "sslValidateCertificate": config.ssl_validate_certificate,
        "connectTimeout": config.connect_timeout * 1000,
        "autocommit": True,
    }
    if config.database:
        # Tenant database when the port belongs to SYSTEMDB.
        kwargs["databaseName"] = config.database
    raw = dbapi.connect(**kwargs)
    return Connection(raw, "?", _secrets_of(config), dialect="hana")


def _looks_like_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("authentication", "password", "invalid username", "login failed", "code=10")
    )


def open_connection(config: B1Config) -> Connection:
    if not config.configured:
        raise B1ConfigurationError(f"sap_b1 not configured; missing env: {config.missing}")
    CartridgeCircuitBreaker.before_request()
    try:
        if config.dialect == "postgres":
            connection = _open_postgres(config)
        else:
            connection = _open_hana(config)
    except B1SourceError:
        raise
    except Exception as exc:
        message = _sanitize(str(exc), _secrets_of(config))
        if not _looks_like_auth_error(message):
            CartridgeCircuitBreaker.record_failure()
        raise B1SourceError(f"connection failed ({type(exc).__name__}): {message}") from exc
    CartridgeCircuitBreaker.record_success()
    return connection


class B1Client:
    """One configured Business One source: connections plus catalogue helpers."""

    CARTRIDGE_ID = CARTRIDGE_ID

    def __init__(self, security_context: str | None = None) -> None:
        self._security_context = (security_context or "").strip() or None
        self.config = resolve_config(self._security_context)

    @property
    def companies(self) -> list[Company]:
        return list(self.config.companies)

    def configuration_status(self) -> dict[str, Any]:
        return self.config.status()

    def require_configured(self) -> None:
        if not self.config.configured:
            raise B1ConfigurationError(
                f"sap_b1 not configured; missing env: {self.config.missing}"
            )

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        self.require_configured()
        connection = open_connection(self.config)
        try:
            yield connection
        finally:
            connection.close()

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]:
        with self.connection() as connection:
            return connection.fetch_all(sql, params)

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        """Open a session and read ``CINF.Version`` in every company schema.

        Reports per company whether the schema answered and which Business
        One version it carries. Never invents data: without configuration it
        returns ``degraded`` with the missing variable names.
        """
        status = self.configuration_status()
        if not status["configured"]:
            return {"status": "degraded", **status}

        companies: list[dict[str, Any]] = []
        try:
            with self.connection() as connection:
                for company in self.companies:
                    sql = (
                        f'SELECT "Version" FROM {quote_schema(company.schema)}."CINF"'
                    )
                    _columns, rows = connection.fetch_all(sql)
                    version = rows[0][0] if rows and rows[0] else None
                    companies.append(
                        {
                            "alias": company.alias,
                            "reachable": True,
                            "b1_version": int(version) if version is not None else None,
                        }
                    )
        except CircuitBreakerOpen as exc:
            return {
                "status": "error",
                "reachable": False,
                "message": str(exc),
                "circuit_breaker": CartridgeCircuitBreaker.snapshot(),
                **status,
            }
        except B1SourceError as exc:
            message = str(exc)
            return {
                "status": "auth_error" if _looks_like_auth_error(message) else "error",
                "reachable": False,
                "message": message,
                "companies": companies,
                **{k: v for k, v in status.items() if k != "companies"},
            }
        return {
            "status": "ok",
            "reachable": True,
            "cartridge": CARTRIDGE_ID,
            "configured": True,
            "dialect": status["dialect"],
            "companies": companies,
        }

    # ------------------------------------------------------------------
    # Catalogue helpers (mirror the other cartridges' client surface)
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict[str, Any]]:
        from app.services.catalog_service import get_all_entities

        return [
            {"id": e.get("entity"), "name": e.get("entity"), "description": e.get("description", "")}
            for e in get_all_entities()
            if e.get("entity")
        ]

    def get_table_schema(self, table_id: str) -> dict[str, Any]:
        from app.services.catalog_service import get_entity_config

        cfg = get_entity_config(table_id)
        if not cfg:
            return {"error": f"Entity '{table_id}' not in local catalog"}
        return {
            "entity": cfg.get("entity"),
            "fields": cfg.get("select_fields", []),
            "primary_key": cfg.get("primary_key"),
            "watermark_field": cfg.get("watermark_field"),
            "watermark_format": cfg.get("watermark_format"),
            "parent": cfg.get("parent"),
        }
