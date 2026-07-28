from __future__ import annotations

import re

from app.services.control_room.business_copy_unicode import canonical_security_text
from app.services.control_room.diagnostic_escape_detection import (
    escaped_security_detection,
)
from app.services.control_room.diagnostic_field_escape_detection import (
    malformed_field_escape_detection,
)
from app.services.control_room.diagnostic_path_key_detection import (
    path_key_detection,
)

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
        "secret_binary",
        "secret_access_key",
        "secret_key",
        "secret_string",
        "secret_value",
        "service_account_key",
        "session_token",
        "subscription_key",
        "token",
    }
)
_SAFE_FIELDS = frozenset(
    {
        "cached_tokens",
        "input_tokens",
        "output_tokens",
        "token_count",
        "token_counts",
        "total_tokens",
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
    "_tokens",
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
_COMPACT_PLURALS = (
    ("binaries", "binary"),
    ("credentials", "credential"),
    ("passwords", "password"),
    ("secrets", "secret"),
    ("strings", "string"),
    ("tokens", "token"),
    ("values", "value"),
    ("keys", "key"),
    ("ids", "id"),
)
_SECRET_COMPACT = frozenset(
    {
        *(key.replace("_", "") for key in _SECRET_KEYS),
        "accountkeys",
        "apikeys",
        "awscredentials",
        "azurecredentials",
        "binary",
        "clientcredentials",
        "clientsecrets",
        "credentiallist",
        "credentialslist",
        "gcpcredentials",
        "key",
        "keyvaultsecret",
        "privatekeys",
        "serviceaccount",
        "serviceaccountcredentials",
        "secretbinary",
        "secretstring",
        "secretvalues",
        "string",
    }
)
_SECRET_COMPACT_SUFFIXES = frozenset(
    {
        "accesskey",
        "accesskeyid",
        "accesstoken",
        "accountkey",
        "apikey",
        "authorization",
        "authorizationheader",
        "clientsecret",
        "connectionstring",
        "credential",
        "credentialslist",
        "databaseurl",
        "password",
        "privatekey",
        "privatekeydata",
        "refreshtoken",
        "sastoken",
        "secret",
        "secretaccesskey",
        "secretbinary",
        "secretkey",
        "secretstring",
        "secretvalue",
        "serviceaccountkey",
        "sessiontoken",
        "subscriptionkey",
        "token",
    }
)
_PII_COMPACT = frozenset(key.replace("_", "") for key in _PII_KEYS)
_SECRET_SEGMENTS = frozenset(
    {"binary", "credential", "key", "password", "secret", "string", "token"}
)
_SEGMENT_SINGULARS = {plural: singular for plural, singular in _COMPACT_PLURALS}
_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")


def canonical_diagnostic_field(value: object) -> str:
    text = canonical_security_text(str(value).strip())
    text = _ACRONYM_BOUNDARY.sub(r"\1_\2", text)
    text = _CAMEL_BOUNDARY.sub(r"\1_\2", text)
    return _KEY_SEPARATOR.sub("_", text).strip("_").lower()


def _compact_forms(field: str) -> frozenset[str]:
    compact = field.replace("_", "")
    forms = {compact}
    parts = tuple(
        _SEGMENT_SINGULARS.get(part, part) for part in field.split("_") if part
    )
    if parts:
        forms.add("".join(parts))
    for plural, singular in _COMPACT_PLURALS:
        if compact.endswith(plural):
            forms.add(f"{compact[: -len(plural)]}{singular}")
    return frozenset(forms)


def _canonical_field_is_sensitive(field: str) -> bool:
    if field in _SAFE_FIELDS:
        return False
    compact_forms = _compact_forms(field)
    semantic_parts = {
        _SEGMENT_SINGULARS.get(part, part)
        for part in field.split("_")
        if part and not part.isdigit()
    }
    return bool(
        field in _SECRET_KEYS
        or field in _PII_KEYS
        or field.endswith(_SECRET_KEY_SUFFIXES)
        or field.endswith(_PII_KEY_SUFFIXES)
        or compact_forms & (_SECRET_COMPACT | _PII_COMPACT)
        or any(form.endswith(tuple(_SECRET_COMPACT_SUFFIXES)) for form in compact_forms)
        or semantic_parts & _SECRET_SEGMENTS
    )


def _canonical_path_terminal_is_sensitive(field: str) -> bool:
    """Apply strong field names without treating generic path words as secrets."""

    if field in _SAFE_FIELDS:
        return False
    compact_forms = _compact_forms(field)
    strong_compact = (_SECRET_COMPACT | _PII_COMPACT) - {
        "binary",
        "key",
        "string",
    }
    return bool(
        field in _SECRET_KEYS
        or field in _PII_KEYS
        or field.endswith(_SECRET_KEY_SUFFIXES)
        or field.endswith(_PII_KEY_SUFFIXES)
        or compact_forms & strong_compact
        or any(form.endswith(tuple(_SECRET_COMPACT_SUFFIXES)) for form in compact_forms)
    )


def sensitive_diagnostic_field(value: object) -> bool:
    raw = str(value).strip()
    path_detection = path_key_detection(raw)
    if path_detection.unsafe:
        return True
    if path_detection.path_like:
        return any(
            _canonical_path_terminal_is_sensitive(canonical_diagnostic_field(terminal))
            for terminal in path_detection.terminals
        )
    escape_detection = escaped_security_detection(raw)
    if escape_detection.unsafe:
        return True
    malformed_forms: tuple[str, ...] = ()
    if "\\" in raw and not escape_detection.forms:
        malformed_detection = malformed_field_escape_detection(raw)
        if malformed_detection.unsafe:
            return True
        malformed_forms = malformed_detection.forms
    return any(
        _canonical_field_is_sensitive(canonical_diagnostic_field(candidate))
        for candidate in (raw, *malformed_forms, *escape_detection.forms)
    )


__all__ = ("canonical_diagnostic_field", "sensitive_diagnostic_field")
