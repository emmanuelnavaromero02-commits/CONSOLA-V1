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
_SENSITIVE_VALUE = re.compile(
    r"(?ix)"
    r"(\b(?:tokens?|passwords?|secrets?|api(?:[_ -])?keys?|authorization|"
    r"cookies?|client[_-]?secrets?|access[_-]?keys?|"
    r"(?:oauth[_-]?)?access[_-]?tokens?|refresh[_-]?tokens?|"
    r"session[_-]?tokens?|connection[_-]?strings?|database[_-]?url|dsn|"
    r"credentials?|secret[_-]?(?:strings?|binar(?:y|ies)|values?)|"
    r"(?:client[_-]?)?private[_-]?keys?)"
    r"\b[\"']?\s*(?::|=|\bis\b)\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\n,;&}]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_URI_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s:]*:[^/@\s]+@")
_PRIVATE_KEY_BLOCK = re.compile(
    r"(?is)-----BEGIN[^\n-]*PRIVATE KEY-----.*?" r"-----END[^\n-]*PRIVATE KEY-----"
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]*\d){8,14}(?!\w)")
_CURP = re.compile(r"(?i)\b[A-Z][AEIOU][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b")
_RFC = re.compile(r"(?i)\b[A-Z&]{3,4}\d{6}[A-Z0-9]{3}\b")
_ADDRESS = re.compile(
    r"(?i)\b(address|direccion|domicilio)\s*(?::|=|\bis\b)\s*[^,;\n]+"
)
_KEY_VALUE = re.compile(
    r"(?ix)"
    r"(?P<key>(?<![A-Z0-9])[A-Z][A-Z0-9_. \[\]-]{0,79}?)"
    r"(?P<separator>\s*(?::|=|\bis\b)\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\n,;&}]+)"
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


_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_COMPACT_PLURALS = (
    ("binaries", "binary"),
    ("credentials", "credential"),
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


def _key(value: object) -> str:
    text = _ACRONYM_BOUNDARY.sub(r"\1_\2", str(value).strip())
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


def _sensitive_field(field: str) -> bool:
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


def _redact_key_value(match: re.Match[str]) -> str:
    if not _sensitive_field(_key(match.group("key"))):
        return match.group(0)
    return f"{match.group('key')}{match.group('separator')}[REDACTED]"


def redact_diagnostic_value(value: object, *, field: str = "") -> object:
    normalized = _key(field)
    if _sensitive_field(normalized):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                continue
            redacted[key] = redact_diagnostic_value(nested, field=key)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [redact_diagnostic_value(nested) for nested in value]
    if not isinstance(value, str):
        return value
    clean = _PRIVATE_KEY_BLOCK.sub("[REDACTED]", value)
    clean = _KEY_VALUE.sub(_redact_key_value, clean)
    clean = _BEARER.sub("Bearer [REDACTED]", clean)
    clean = _SENSITIVE_VALUE.sub(r"\1[REDACTED]", clean)
    clean = _URI_CREDENTIALS.sub(r"\1[REDACTED]@", clean)
    clean = _EMAIL.sub("[REDACTED]", clean)
    clean = _PHONE.sub("[REDACTED]", clean)
    clean = _CURP.sub("[REDACTED]", clean)
    clean = _RFC.sub("[REDACTED]", clean)
    return _ADDRESS.sub(r"\1=[REDACTED]", clean)


__all__ = ("redact_diagnostic_value",)
