from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from app.core.b1_source import B1Config

APPLICATION_NAME = "omega-sap_b1"
MSSQL_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SCHEMA_RE = re.compile(r"^[A-Za-z0-9_$][A-Za-z0-9_$\-]{0,127}$")
_MSSQL_DATABASE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,127}$")
_MSSQL_SYSTEM_DATABASES = frozenset({"master", "model", "msdb", "tempdb", "resource", "distribution"})


class DriverUnavailable(RuntimeError):
    pass


def quote_ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
        raise ValueError(f"Invalid identifier: {name!r}")
    return f'"{name}"'


def _days(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("a day offset must be an integer")
    return value


class B1DialectStrategy(ABC):
    """Everything that differs between the SQL engines Business One runs on."""

    name: str = ""
    default_port: int = 0
    placeholder: str = "?"
    requires_database: bool = False
    now_sql: str = ""
    ping_sql: str = ""
    today_sql: str = ""
    auth_error_tokens: tuple[str, ...] = ("authentication failed", "login failed", "password")

    def quote_ident(self, name: str) -> str:
        return quote_ident(name)

    def validate_company_object(self, name: str) -> str:
        if not isinstance(name, str) or not _SCHEMA_RE.fullmatch(name):
            raise ValueError("Invalid company schema name")
        return name

    def company_prefix(self, company_object: str) -> str:
        return f'"{self.validate_company_object(company_object)}"'

    def table_ref(self, company_object: str, table: str) -> str:
        return f"{self.company_prefix(company_object)}.{self.quote_ident(table)}"

    def render(self, sql: str) -> str:
        return sql if self.placeholder == "?" else sql.replace("?", self.placeholder)

    def order_and_limit(self, order_by: Sequence[str], limit: int, offset: int = 0) -> str:
        if not order_by:
            raise ValueError("a page needs an ORDER BY")
        return f" ORDER BY {', '.join(order_by)}{self.limit_clause(limit, offset)}"

    @abstractmethod
    def limit_clause(self, limit: int, offset: int = 0) -> str: ...

    @abstractmethod
    def add_days(self, expression: str, days: int) -> str: ...

    @abstractmethod
    def days_between(self, start: str, end: str) -> str: ...

    def age_in_days(self, expression: str) -> str:
        return self.days_between(expression, self.today_sql)

    @abstractmethod
    def connect(self, config: "B1Config") -> Any: ...

    def is_auth_error(self, message: str) -> bool:
        lowered = str(message or "").lower()
        return any(token in lowered for token in self.auth_error_tokens)

    @staticmethod
    def same_column(expected: str, reported: Any) -> bool:
        # drivers report the stored name: exact on HANA (always quoted), collation-dependent on MSSQL
        return str(expected).casefold() == str(reported).casefold()


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be an integer")
    number = int(value)
    if number < 0:
        raise ValueError(f"{what} must not be negative")
    return number


class HanaDialect(B1DialectStrategy):
    name = "hana"
    default_port = 30015
    now_sql = "SELECT CURRENT_TIMESTAMP FROM DUMMY"
    ping_sql = "SELECT 1 FROM DUMMY"
    today_sql = "CURRENT_DATE"
    auth_error_tokens = (*B1DialectStrategy.auth_error_tokens, "invalid username", "code=10")

    def limit_clause(self, limit: int, offset: int = 0) -> str:
        clause = f" LIMIT {_int(limit, 'limit')}"
        return clause + (f" OFFSET {_int(offset, 'offset')}" if offset else "")

    def add_days(self, expression: str, days: int) -> str:
        return f"ADD_DAYS({expression}, {_days(days)})"

    def days_between(self, start: str, end: str) -> str:
        return f"DAYS_BETWEEN({start}, {end})"

    def connect(self, config: "B1Config") -> Any:
        try:
            from hdbcli import dbapi
        except ImportError as exc:  # pragma: no cover - depends on the image
            raise DriverUnavailable("hdbcli is not installed; the hana dialect needs the SAP HANA client") from exc
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
            kwargs["databaseName"] = config.database
        return dbapi.connect(**kwargs)


def _odbc_value(value: Any) -> str:
    return "{" + str(value).replace("}", "}}") + "}"


class MssqlDialect(B1DialectStrategy):
    name = "mssql"
    default_port = 1433
    now_sql = "SELECT SYSDATETIME()"
    ping_sql = "SELECT 1"
    today_sql = "CURRENT_TIMESTAMP"
    odbc_driver = MSSQL_ODBC_DRIVER
    auth_error_tokens = (*B1DialectStrategy.auth_error_tokens, "(18456)")

    def validate_company_object(self, name: str) -> str:
        # Business One keeps each company in its own database, always under dbo
        if (
            not isinstance(name, str)
            or not _MSSQL_DATABASE_RE.fullmatch(name)
            or name.lower() in _MSSQL_SYSTEM_DATABASES
        ):
            raise ValueError("Invalid company database name")
        return name

    def company_prefix(self, company_object: str) -> str:
        return f'"{self.validate_company_object(company_object)}"."dbo"'

    def limit_clause(self, limit: int, offset: int = 0) -> str:
        return f" OFFSET {_int(offset, 'offset')} ROWS FETCH NEXT {_int(limit, 'limit')} ROWS ONLY"

    def add_days(self, expression: str, days: int) -> str:
        return f"DATEADD(day, {_days(days)}, {expression})"

    def days_between(self, start: str, end: str) -> str:
        return f"DATEDIFF(day, {start}, {end})"

    def connection_string(self, config: "B1Config") -> str:
        parts = [
            f"DRIVER={_odbc_value(self.odbc_driver)}",
            f"SERVER={_odbc_value(f'tcp:{config.host},{int(config.port)}')}",
            f"UID={_odbc_value(config.user)}",
            f"PWD={_odbc_value(config.password)}",
            f"Encrypt={'yes' if config.encrypt else 'no'}",
            f"TrustServerCertificate={'no' if config.ssl_validate_certificate else 'yes'}",
            "ApplicationIntent=ReadOnly",
            f"APP={_odbc_value(APPLICATION_NAME)}",
        ]
        if config.database:
            parts.append(f"DATABASE={_odbc_value(self.validate_company_object(config.database))}")
        return ";".join(parts) + ";"

    def connect(self, config: "B1Config") -> Any:
        try:
            import pyodbc
        except ImportError as exc:
            raise DriverUnavailable("pyodbc is not installed; the mssql dialect needs pyodbc") from exc
        if self.odbc_driver not in pyodbc.drivers():
            raise DriverUnavailable(f"{self.odbc_driver} is not installed; the mssql dialect needs it")
        return pyodbc.connect(self.connection_string(config), autocommit=True, timeout=config.connect_timeout)


class PostgresDialect(B1DialectStrategy):
    """Test bed: a PostgreSQL schema per company with the Business One table shapes."""

    name = "postgres"
    default_port = 5432
    placeholder = "%s"
    requires_database = True
    now_sql = "SELECT LOCALTIMESTAMP(0)"
    ping_sql = "SELECT 1"
    today_sql = "CURRENT_DATE"

    def limit_clause(self, limit: int, offset: int = 0) -> str:
        clause = f" LIMIT {_int(limit, 'limit')}"
        return clause + (f" OFFSET {_int(offset, 'offset')}" if offset else "")

    def add_days(self, expression: str, days: int) -> str:
        return f"({expression} + {_days(days)} * INTERVAL '1 day')"

    def days_between(self, start: str, end: str) -> str:
        return f"(CAST({end} AS DATE) - CAST({start} AS DATE))"

    def connect(self, config: "B1Config") -> Any:
        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover - depends on the image
            raise DriverUnavailable("psycopg2 is not installed; the postgres test bed needs it") from exc
        raw = psycopg2.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
            connect_timeout=config.connect_timeout,
            application_name=APPLICATION_NAME,
        )
        raw.set_session(readonly=True, autocommit=True)
        return raw


_REGISTRY: dict[str, B1DialectStrategy] = {
    strategy.name: strategy for strategy in (HanaDialect(), MssqlDialect(), PostgresDialect())
}
DIALECTS: tuple[str, ...] = tuple(_REGISTRY)


def get_dialect(name: "str | B1DialectStrategy") -> B1DialectStrategy:
    if isinstance(name, B1DialectStrategy):
        return name
    key = str(name or "").strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ValueError(f"unknown SAP Business One dialect: {name!r}; expected one of {DIALECTS}") from None


__all__ = [
    "APPLICATION_NAME",
    "B1DialectStrategy",
    "DIALECTS",
    "DriverUnavailable",
    "HanaDialect",
    "MSSQL_ODBC_DRIVER",
    "MssqlDialect",
    "PostgresDialect",
    "get_dialect",
    "quote_ident",
]
