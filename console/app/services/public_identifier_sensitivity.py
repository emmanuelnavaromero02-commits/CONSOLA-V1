from __future__ import annotations

import ast
import re


_MACHINE_TECHNICAL_TERM = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"(?:bindings?|provenance|receipts?)(?:[_.-][a-z0-9]+)+|"
    r"[a-z0-9]+(?:[_.-][a-z0-9]+)*[_.-](?:bindings?|provenance|receipts?)"
    r")(?![a-z0-9])"
)
_MACHINE_STATE_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?i:(?:data|readiness|source)[_.-](?:state|status))|"
    r"(?:data|readiness|source)(?:State|Status)"
    r")(?![A-Za-z0-9])"
)
_DATASET_REFERENCE = re.compile(
    r"(?i)(?:\bdatasets?\s*[:=]|"
    r"(?:^|[^a-z0-9])datasets?[_./-][a-z0-9]|"
    r"(?:^|[^a-z0-9])datasets?(?:internal|private|raw|technical)"
    r"(?:$|[^a-z0-9])|"
    r"\bdatasets?\s+materializad[oa]s?\s+de\b)"
)
_PACKAGED_DATASET_ID = re.compile(
    r"(?i)(?<![a-z0-9])"
    r"(?:bronze|gold|materialized|raw|silver|staging)"
    r"[_/][a-z0-9]+(?:[_/][a-z0-9]+)*(?![a-z0-9])"
)
_TIER_DATASET_TOKEN = re.compile(
    r"(?i)(?<![a-z0-9])(?:bronze|gold|materialized|raw|silver|staging)"
    r"[.-][a-z0-9]+(?:[.-][a-z0-9]+)*(?![a-z0-9])"
)
_TITLECASE_BUSINESS_TIER = re.compile(
    r"(?:Bronze|Gold|Materialized|Raw|Silver|Staging)"
    r"[.-][A-Z][a-z0-9]+(?:[.-][A-Z][a-z0-9]+)*"
)
_BUSINESS_NAME_AFTER_TIER = re.compile(r"\s+[A-ZÀ-ÖØ-Þ][^\s]*")
_TECHNICAL_TIER_FOLLOWUP = re.compile(
    r"\s+(?:data|dataset|schema|source|table|view)\b", re.IGNORECASE
)
_EXPLICIT_DATASET_CONTEXT = re.compile(r"(?i)\b(?:dataset|source)\s+$")
_QUOTED_TIER_DATASET = re.compile(
    r"(?i)(?:[\"`[](?:bronze|gold|materialized|raw|silver|staging)[\"`\]]"
    r"\s*\.\s*[\"`[]?[a-z0-9_]+[\"`\]]?|"
    r"(?:bronze|gold|materialized|raw|silver|staging)\s*\.\s*"
    r"[\"`[]+[a-z0-9_]+[\"`\]]+)"
)
_NON_BUSINESS_TIER_SEPARATOR = re.compile(
    r"(?i)(?<![a-z0-9])(?:bronze|gold|materialized|raw|silver|staging)"
    r"(?:\s+\.\s+|\.{2,}|:{1,2})[a-z0-9_]+"
)
_SUCCESSFACTORS_DATASET_ID = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"sap[_.-]successfactors(?:[_.-][a-z0-9]+(?:[_.-][a-z0-9]+)*)?|"
    r"successfactors[_.-][a-z0-9]+(?:[_.-][a-z0-9]+)*"
    r")(?![a-z0-9])"
)
_CAMEL_SUCCESSFACTORS_DATASET_ID = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"(?:[a-z0-9]+)?sapsuccessfactors[a-z0-9]*|"
    r"(?:[a-z0-9]+)?successfactors(?:anomal(?:y|ies)|company|dataset|department|"
    r"employee|employees|headcount|location|payroll|profile|snapshot|source|"
    r"talent|time|workforce|360)[a-z0-9]*"
    r")(?![a-z0-9])"
)
_CAMEL_TECHNICAL_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?i:bindings?|datasets?|provenance|receipts?|raw|sql)[A-Z][A-Za-z0-9]*|"
    r"[a-z][A-Za-z0-9]*?"
    r"(?:Bindings?|Datasets?|Provenance|Receipts?|RawSql|SQL|Sql)"
    r"(?:[A-Z][A-Za-z0-9]*)?"
    r")(?![A-Za-z0-9])"
)
_SNAKE_SQL_IDENTIFIER = re.compile(
    r"(?i)(?<![a-z0-9])(?:sql_[a-z0-9]+|[a-z0-9]+_sql)(?![a-z0-9])"
)
_TENANCY_IDENTIFIER = re.compile(
    r"(?i)(?<![a-z0-9])(?:tenant|workspace)[_. /:-]*"
    r"(?:guid|guids|id|ids|identifier|key|uuid|uuids)(?![a-z0-9])"
)
_CAMEL_TENANCY_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?:[a-z][A-Za-z0-9]*?)?"
    r"(?:[Tt]enant|[Ww]orkspace)"
    r"(?:GUID|GUIDs|Guid|Guids|Id|Ids|Identifier|Key|UUID|UUIDs|Uuid|Uuids)"
    r"(?:Raw|String|Value)?"
    r"(?![A-Za-z0-9])"
)
_MACHINE_IDENTIFIER = re.compile(
    r"(?i)(?<![a-z0-9])[a-z][a-z0-9]*(?:[_.-][a-z0-9]+)*[_.-]"
    r"(?:code|codes|guid|guids|id|ids|identifier|key|number|numbers|uuid|uuids)"
    r"(?![a-z0-9])"
)
_CAMEL_MACHINE_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])[a-z][A-Za-z0-9]*"
    r"(?:Code|Codes|GUID|GUIDs|Guid|Guids|ID|IDs|Id|Ids|Identifier|Key|Number|Numbers|"
    r"UUID|UUIDs|Uuid|Uuids)(?:External)?(?![A-Za-z0-9])"
)
_CAMEL_METADATA_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?:durationMs|httpStatus|latencyMs|retryAfter|rowCount|"
    r"schemaVersion|statusCode)(?![A-Za-z0-9])"
)
_MACHINE_SECRET_IDENTIFIER = re.compile(
    r"(?i)(?<![a-z0-9])(?:access[_-]token|api[_-]key|bearer[_-]token|"
    r"client[_-]secret|private[_-]key|refresh[_-]token)(?![a-z0-9])"
)
_CAMEL_SECRET_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?:accessToken|apiKey|bearerToken|clientSecret|privateKey|"
    r"refreshToken)(?![A-Za-z0-9])"
)
_CONTROL_ROOM_TECHNICAL_ID = re.compile(
    r"(?i)(?<![a-z0-9])(?:(?:control[_.-]room|sf)[_.-][a-z0-9]+"
    r"(?:[_.-][a-z0-9]+)+|active[_.-]headcount|headcount[_.-]by[_.-][a-z0-9]+"
    r"(?:[_.-][a-z0-9]+)*)(?![a-z0-9])"
)
_CAMEL_CONTROL_ROOM_ID = re.compile(
    r"(?<![A-Za-z0-9])(?:sf[A-Z][A-Za-z0-9]*|headcountBy[A-Z][A-Za-z0-9]*)"
    r"(?![A-Za-z0-9])"
)
_BUSINESS_TECHNICAL_CODE = re.compile(
    r"(?i)(?<![a-z0-9])(?:COMP|DEPT|EMP|LOC)[_-]?\d+(?![a-z0-9])"
)
_UUID_OR_ULID = re.compile(
    r"(?i)(?<![a-z0-9])(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}|[0-9A-HJKMNP-TV-Z]{26})(?![a-z0-9])"
)
_STRUCTURED_REPR = re.compile(
    r"(?s)^\s*(?:<[^<>]*\bobject\s+at\s+0x[0-9a-f]+>|"
    r"[A-Za-z][A-Za-z0-9_]*\([^()]*[A-Za-z_][A-Za-z0-9_]*\s*=.*\)|"
    r"\([^()]*,[^()]*\)|\{[^{}]*,[^{}]*\}|\[[^][]*,[^][]*\])\s*$",
    re.IGNORECASE,
)
_CONTAINER_SHAPED_REPR = re.compile(
    r"(?s)^(?:\{[^{}]*\}|\[[^\[\]]*\]|\((?:[^()]|\([^()]*\))*\))$"
)
_LABELED_TECHNICAL_VALUE = re.compile(
    r"(?i)\b(?:authority[_ -]?audit|bindings?|connection[_ -]?string|dataset|"
    r"payload|provenance|receipt|raw[_ -]?sql)\s*[:=]|"
    r"\b(?:tenant|workspace|simulation|orchestration|execution|action[_ -]?run|"
    r"run|connection|dataset|receipt|provenance|query|job|task|agent|"
    r"installation|source)[_ -]?id\s*[:=]|"
    r"\b(?:duration_ms|http_status|latency_ms|retry_after|row_count|"
    r"schema_version|status_code)\s*[:=]|"
    r"\b(?:technical[_ -]?)?id\s*[:=]"
)
_LABELED_VALUE = re.compile(
    r"(?<![A-Za-z0-9])(?P<key>[A-Za-z][A-Za-z0-9_. -]{0,79})\s*[:=]"
)
_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_TECHNICAL_STRUCTURE_KEYS = frozenset(
    {
        "binding",
        "bindings",
        "dataset",
        "datasets",
        "directory",
        "directories",
        "file",
        "files",
        "filepath",
        "path",
        "paths",
        "provenance",
        "receipt",
        "receipts",
        "sql",
        "state",
        "status",
    }
)
_TECHNICAL_STRUCTURE_SUFFIXES = (
    "_directory",
    "_directories",
    "_file",
    "_files",
    "_filepath",
    "_path",
    "_paths",
)
_TECHNICAL_STRUCTURE_PREFIXES = ("directory_", "file_path_", "filepath_", "path_")
_TECHNICAL_STRUCTURE_TOKENS = frozenset(
    {"binding", "bindings", "provenance", "receipt", "receipts", "state", "status"}
)


def _normalized_key(value: str) -> str:
    key = _ACRONYM_BOUNDARY.sub(r"\1_\2", value)
    key = _CAMEL_BOUNDARY.sub(r"\1_\2", key)
    return _KEY_SEPARATOR.sub("_", key).strip("_").casefold()


def _contains_labeled_identifier(value: str) -> bool:
    return any(
        (key := _normalized_key(match.group("key"))) == "id"
        or key.endswith(("_id", "_ids"))
        for match in _LABELED_VALUE.finditer(value)
    )


def _contains_tier_dataset(value: str) -> bool:
    for match in _TIER_DATASET_TOKEN.finditer(value):
        token = match.group(0)
        business_name = bool(
            _TITLECASE_BUSINESS_TIER.fullmatch(token)
            and _BUSINESS_NAME_AFTER_TIER.match(value[match.end() :])
            and not _TECHNICAL_TIER_FOLLOWUP.match(value[match.end() :])
            and not _EXPLICIT_DATASET_CONTEXT.search(value[: match.start()])
        )
        if not business_name:
            return True
    return False


def _is_serialized_container(value: str) -> bool:
    stripped = value.strip()
    if not _CONTAINER_SHAPED_REPR.fullmatch(stripped):
        return False
    try:
        parsed = ast.literal_eval(stripped)
    except (MemoryError, RecursionError, SyntaxError, ValueError):
        return stripped[0] in "[{"
    return isinstance(parsed, (dict, list, set, tuple))


def contains_public_identifier_copy(value: str) -> bool:

    stripped = value.strip()
    return bool(
        _MACHINE_TECHNICAL_TERM.search(value)
        or _MACHINE_STATE_IDENTIFIER.search(value)
        or _DATASET_REFERENCE.search(value)
        or _PACKAGED_DATASET_ID.search(value)
        or _contains_tier_dataset(value)
        or _QUOTED_TIER_DATASET.search(value)
        or _NON_BUSINESS_TIER_SEPARATOR.search(value)
        or _SUCCESSFACTORS_DATASET_ID.search(value)
        or _CAMEL_SUCCESSFACTORS_DATASET_ID.search(value)
        or _CAMEL_TECHNICAL_IDENTIFIER.search(value)
        or _SNAKE_SQL_IDENTIFIER.search(value)
        or _TENANCY_IDENTIFIER.search(value)
        or _CAMEL_TENANCY_IDENTIFIER.search(value)
        or _MACHINE_IDENTIFIER.search(value)
        or _CAMEL_MACHINE_IDENTIFIER.search(value)
        or _CAMEL_METADATA_IDENTIFIER.search(value)
        or _MACHINE_SECRET_IDENTIFIER.search(value)
        or _CAMEL_SECRET_IDENTIFIER.search(value)
        or _CONTROL_ROOM_TECHNICAL_ID.search(value)
        or _CAMEL_CONTROL_ROOM_ID.search(value)
        or _BUSINESS_TECHNICAL_CODE.search(value)
        or _UUID_OR_ULID.search(value)
        or _STRUCTURED_REPR.fullmatch(stripped)
        or _is_serialized_container(stripped)
        or _LABELED_TECHNICAL_VALUE.search(value)
        or _contains_labeled_identifier(value)
    )


def is_public_technical_structure_key(value: str) -> bool:

    key = _normalized_key(value)
    segments = key.split("_")
    return (
        key in _TECHNICAL_STRUCTURE_KEYS
        or (
            len(segments) > 1
            and any(segment in _TECHNICAL_STRUCTURE_TOKENS for segment in segments)
        )
        or key.endswith(_TECHNICAL_STRUCTURE_SUFFIXES)
        or key.startswith(_TECHNICAL_STRUCTURE_PREFIXES)
    )


__all__ = (
    "contains_public_identifier_copy",
    "is_public_technical_structure_key",
)
