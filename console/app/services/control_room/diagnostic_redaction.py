from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


_SECRET_KEYS = frozenset(
    {
        "access_key",
        "access_key_id",
        "account_key",
        "aws_access_key_id",
        "aws_secret_access_key",
        "access_token",
        "api_key",
        "authorization",
        "authorization_header",
        "authorization_headers",
        "client_secret",
        "connection_string",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "database_url",
        "dsn",
        "password",
        "private_key",
        "private_key_data",
        "refresh_token",
        "sas_token",
        "secret",
        "secret_access_key",
        "secret_key",
        "service_account_key",
        "session_token",
        "subscription_key",
        "token",
    }
)
_PII_KEYS = frozenset(
    {
        "address",
        "curp",
        "email",
        "owner_user_id",
        "phone",
        "phone_number",
        "rfc",
        "ssn",
        "tax_id",
        "user_id",
    }
)
_SENSITIVE_VALUE = re.compile(
    r"(?ix)"
    r"(\b(?:token|password|secret|api(?:[_ -])?key|authorization|cookie|"
    r"client[_-]?secret|access[_-]?key|(?:oauth[_-]?)?access[_-]?token|"
    r"refresh[_-]?token|session[_-]?token|connection[_-]?string|"
    r"database[_-]?url|dsn|credential|(?:client[_-]?)?private[_-]?key)"
    r"\b[\"']?\s*(?::|=|\bis\b)\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_URI_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]*\d){8,14}(?!\w)")
_CURP = re.compile(r"(?i)\b[A-Z][AEIOU][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b")
_RFC = re.compile(r"(?i)\b[A-Z&]{3,4}\d{6}[A-Z0-9]{3}\b")
_ADDRESS = re.compile(
    r"(?i)\b(address|direccion|domicilio)\s*(?::|=|\bis\b)\s*[^,;\n]+"
)
_SECRET_KEY_SUFFIXES = (
    "_access_key",
    "_access_key_id",
    "_account_key",
    "_api_key",
    "_authorization",
    "_authorization_header",
    "_client_secret",
    "_connection_string",
    "_cookie",
    "_cookies",
    "_credential",
    "_credentials",
    "_database_url",
    "_dsn",
    "_password",
    "_private_key",
    "_private_key_data",
    "_sas_token",
    "_secret",
    "_secret_access_key",
    "_secret_key",
    "_service_account_key",
    "_subscription_key",
    "_token",
)
_PII_KEY_SUFFIXES = (
    "_address",
    "_curp",
    "_email",
    "_phone",
    "_phone_number",
    "_rfc",
    "_ssn",
    "_tax_id",
)


_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")


def _key(value: object) -> str:
    text = _ACRONYM_BOUNDARY.sub(r"\1_\2", str(value).strip())
    text = _CAMEL_BOUNDARY.sub(r"\1_\2", text)
    return _KEY_SEPARATOR.sub("_", text).strip("_").lower()


def _sensitive_field(field: str) -> bool:
    return (
        field in _SECRET_KEYS
        or field in _PII_KEYS
        or field.endswith(_SECRET_KEY_SUFFIXES)
        or field.endswith(_PII_KEY_SUFFIXES)
    )


def redact_diagnostic_value(value: object, *, field: str = "") -> object:
    normalized = _key(field)
    if _sensitive_field(normalized):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(key): redact_diagnostic_value(nested, field=str(key))
            for key, nested in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [redact_diagnostic_value(nested) for nested in value]
    if not isinstance(value, str):
        return value
    clean = _BEARER.sub("Bearer [REDACTED]", value)
    clean = _SENSITIVE_VALUE.sub(r"\1[REDACTED]", clean)
    clean = _URI_CREDENTIALS.sub(r"\1[REDACTED]@", clean)
    clean = _EMAIL.sub("[REDACTED]", clean)
    clean = _PHONE.sub("[REDACTED]", clean)
    clean = _CURP.sub("[REDACTED]", clean)
    clean = _RFC.sub("[REDACTED]", clean)
    return _ADDRESS.sub(r"\1=[REDACTED]", clean)


__all__ = ("redact_diagnostic_value",)
