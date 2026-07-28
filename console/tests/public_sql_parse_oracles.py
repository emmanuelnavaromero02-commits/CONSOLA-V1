"""Real parse-only SQL oracles; this module is never imported by Console."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO


DUCKDB_VERSION = "1.2.2"
POSTGRESQL_VERSION = "15.18"
_PROTOCOL_V3 = 196608
_SYNTAX_ERROR = "42601"
_MULTIPLE_COMMANDS = "cannot insert multiple commands into a prepared statement"


class ParseResult(str, Enum):
    ACCEPTED = "accepted"
    ACCEPTED_MULTIPLE = "accepted_multiple"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PostgreSQLOracleConfig:
    host: str
    port: int
    user: str
    database: str
    timeout_seconds: float = 5.0


class OracleProtocolError(RuntimeError):
    """The test oracle did not provide the pinned parse-only contract."""


def duckdb_parse_only(value: str) -> ParseResult:
    """Parse with DuckDB extract_statements without preparing or executing."""

    import duckdb

    if duckdb.__version__ != DUCKDB_VERSION:
        raise OracleProtocolError(
            f"expected DuckDB {DUCKDB_VERSION}, got {duckdb.__version__}"
        )
    connection = duckdb.connect(":memory:")
    try:
        try:
            statements = connection.extract_statements(value)
        except duckdb.ParserException:
            return ParseResult.REJECTED
    finally:
        connection.close()
    if not statements:
        return ParseResult.REJECTED
    if any(statement.type.name == "INVALID" for statement in statements):
        return ParseResult.REJECTED
    return (
        ParseResult.ACCEPTED_MULTIPLE if len(statements) > 1 else ParseResult.ACCEPTED
    )


def _packet(payload: bytes) -> bytes:
    return struct.pack("!I", len(payload) + 4) + payload


def _message(kind: bytes, payload: bytes = b"") -> bytes:
    return kind + _packet(payload)


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    value = stream.read(length)
    if value is None or len(value) != length:
        raise OracleProtocolError("PostgreSQL oracle closed the protocol early")
    return value


def _read_message(stream: BinaryIO) -> tuple[bytes, bytes]:
    kind = _read_exact(stream, 1)
    length = struct.unpack("!I", _read_exact(stream, 4))[0]
    if length < 4:
        raise OracleProtocolError("invalid PostgreSQL protocol message length")
    return kind, _read_exact(stream, length - 4)


def _error_fields(payload: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for field in payload.rstrip(b"\0").split(b"\0"):
        if field:
            fields[field[:1].decode("ascii")] = field[1:].decode(
                "utf-8", errors="replace"
            )
    return fields


def _startup(
    connection: socket.socket,
    stream: BinaryIO,
    config: PostgreSQLOracleConfig,
) -> None:
    parameters = (
        b"user\0"
        + config.user.encode("utf-8")
        + b"\0database\0"
        + config.database.encode("utf-8")
        + b"\0application_name\0public_sql_parse_oracle\0\0"
    )
    connection.sendall(_packet(struct.pack("!I", _PROTOCOL_V3) + parameters))
    authenticated = False
    server_version: str | None = None
    while True:
        kind, payload = _read_message(stream)
        if kind == b"R":
            auth_method = struct.unpack("!I", payload[:4])[0]
            if auth_method != 0:
                raise OracleProtocolError(
                    "PostgreSQL oracle must use test-only trust authentication"
                )
            authenticated = True
        elif kind == b"S":
            key, value = payload.rstrip(b"\0").split(b"\0", 1)
            if key == b"server_version":
                server_version = value.decode("ascii")
        elif kind == b"E":
            raise OracleProtocolError(str(_error_fields(payload)))
        elif kind == b"Z":
            break
    parsed_version = server_version.split(" ", 1)[0] if server_version else None
    if not authenticated or parsed_version != POSTGRESQL_VERSION:
        raise OracleProtocolError(
            f"expected PostgreSQL {POSTGRESQL_VERSION}, got {server_version!r}"
        )


def postgresql_parse_only(
    value: str,
    config: PostgreSQLOracleConfig,
) -> ParseResult:
    """Send only PostgreSQL Parse and Sync protocol messages; never Bind/Execute."""

    if "\0" in value:
        return ParseResult.REJECTED
    with socket.create_connection(
        (config.host, config.port), timeout=config.timeout_seconds
    ) as connection:
        stream = connection.makefile("rb")
        _startup(connection, stream, config)
        parse_payload = b"\0" + value.encode("utf-8") + b"\0" + struct.pack("!H", 0)
        connection.sendall(_message(b"P", parse_payload) + _message(b"S"))
        result: ParseResult | None = None
        while True:
            kind, payload = _read_message(stream)
            if kind == b"1":
                result = ParseResult.ACCEPTED
            elif kind == b"E":
                fields = _error_fields(payload)
                if fields.get("C") != _SYNTAX_ERROR:
                    result = ParseResult.ACCEPTED
                elif fields.get("M") == _MULTIPLE_COMMANDS:
                    result = ParseResult.ACCEPTED_MULTIPLE
                else:
                    result = ParseResult.REJECTED
            elif kind in {b"2", b"C", b"D"}:
                raise OracleProtocolError(
                    "Bind/Execute response observed in parse-only oracle"
                )
            elif kind == b"Z":
                break
        connection.sendall(_message(b"X"))
    if result is None:
        raise OracleProtocolError("PostgreSQL parser returned no result")
    return result


__all__ = (
    "DUCKDB_VERSION",
    "OracleProtocolError",
    "POSTGRESQL_VERSION",
    "ParseResult",
    "PostgreSQLOracleConfig",
    "duckdb_parse_only",
    "postgresql_parse_only",
)
