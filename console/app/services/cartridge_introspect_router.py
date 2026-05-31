"""
Cartridge Introspect Router — Level 1: universal source discovery.
==================================================================
Detects the *pattern* of an arbitrary data source and extracts its entities
+ typed fields into the canonical ``schema_introspect.Field`` shape, so the
Autopilot can build a cartridge from ANY source — not just OData.

Supported source kinds (deterministic, offline-parseable):
  - odata        : EDMX $metadata          -> schema_introspect.parse_odata_metadata
  - openapi      : OpenAPI/Swagger spec     -> schema_introspect.parse_openapi_fields
  - graphql      : introspection __schema   -> parse_graphql_introspection
  - sql          : information_schema rows   -> parse_sql_information_schema
  - file_csv     : CSV header (+ sample)     -> parse_csv_header
  - rest_sample  : JSON sample payload       -> parse_json_sample
  - soap         : WSDL/XSD elements         -> parse_wsdl_elements (best-effort)

Plus ``detect_source_pattern`` which infers extraction strategy:
paginated? incremental (by date)? async export? webhook?

Design: pure, deterministic, no network. The live HTTP fetch lives in the
Studio router; this module only PARSES what it's handed, which keeps it
unit-testable and keeps secrets out of the parsing layer.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

import defusedxml.ElementTree as ET

from app.services import schema_introspect
from app.services.schema_introspect import Field


def _safe_field(spec: dict[str, Any]) -> Field | None:
    """Normalize a field, returning None instead of raising on a bad name.

    Real introspection routinely yields non-identifier names ('Order ID',
    'created-at', '2024sales'). The module's contract is 'never raise' — so a
    field that can't be normalized is skipped, not fatal.
    """
    try:
        return schema_introspect.normalize_field(spec)
    except (ValueError, TypeError):
        return None


# DoS caps for adversarially-large introspected schemas (audit #15).
_MAX_ENTITIES = 500
_MAX_FIELDS_PER_ENTITY = 1000


def _append_field(fields: list[Field], spec: dict[str, Any]) -> None:
    """Normalize+append a field, silently skipping non-identifier names."""
    f = _safe_field(spec)
    if f is not None:
        fields.append(f)

# ── Source-kind detection ─────────────────────────────────────────────────────

_INCREMENTAL_HINTS = (
    "modified", "updated", "lastchange", "last_change", "changed", "fecha",
    "modifiedsince", "updatedat", "lastmodified", "timestamp", "date",
)
_PAGINATION_HINTS = ("next", "cursor", "offset", "page", "skiptoken", "@odata.nextlink")


def detect_source_kind(descriptor: dict[str, Any]) -> str:
    """Classify a source from a descriptor (hint + payload). Never raises."""
    if not isinstance(descriptor, dict):
        return "rest_sample"
    kind = str(descriptor.get("kind") or "").strip().lower().replace("-", "_")
    if kind in {"odata", "openapi", "graphql", "sql", "file_csv", "rest_sample", "soap"}:
        return kind

    url = str(descriptor.get("url") or descriptor.get("base_url") or "").lower()
    path = str(descriptor.get("file_path") or "").lower()

    if descriptor.get("edmx") or url.endswith("$metadata") or "edmx" in str(descriptor.get("metadata") or "").lower():
        return "odata"
    spec = descriptor.get("spec") or descriptor.get("openapi")
    if isinstance(spec, dict) and (spec.get("openapi") or spec.get("swagger")):
        return "openapi"
    if descriptor.get("graphql") or "graphql" in url or _is_graphql_introspection(descriptor.get("sample")):
        return "graphql"
    if "wsdl" in url or descriptor.get("wsdl"):
        return "soap"
    if descriptor.get("information_schema") or descriptor.get("columns") or kind == "sql":
        return "sql"
    if path.endswith((".csv", ".tsv")) or descriptor.get("csv"):
        return "file_csv"
    if descriptor.get("sample") is not None:
        return "rest_sample"
    return "rest_sample"


def _is_graphql_introspection(sample: Any) -> bool:
    return (
        isinstance(sample, dict)
        and isinstance(sample.get("data"), dict)
        and isinstance(sample["data"].get("__schema"), dict)
    )


def detect_source_pattern(descriptor: dict[str, Any], fields: list[Field] | None = None) -> dict[str, Any]:
    """Infer the extraction strategy: paginated / incremental / async / webhook."""
    if not isinstance(descriptor, dict):
        descriptor = {}
    sample = descriptor.get("sample")
    text_blob = json.dumps(sample).lower() if isinstance(sample, (dict, list)) else ""
    paginated = any(h in text_blob for h in _PAGINATION_HINTS) or bool(descriptor.get("paginated"))

    incremental_field = None
    for f in (fields or []):
        if not isinstance(f, dict):
            continue
        nm = str(f.get("name", "")).lower()
        if f.get("type") in {"date", "timestamp", "datetime"} and any(h in nm for h in _INCREMENTAL_HINTS):
            incremental_field = f["name"]
            break

    is_async = bool(descriptor.get("async_export")) or "export" in str(descriptor.get("kind") or "").lower()
    is_webhook = bool(descriptor.get("webhook"))

    return {
        "paginated": paginated,
        "incremental": bool(incremental_field),
        "incremental_field": incremental_field,
        "async_export": is_async,
        "webhook": is_webhook,
        "extraction_mode": (
            "async_export" if is_async
            else "webhook" if is_webhook
            else "incremental" if incremental_field
            else "full"
        ),
    }


# ── Per-kind parsers (each returns {entity_name: [Field]}) ────────────────────


def parse_csv_header(text: str) -> dict[str, list[Field]]:
    """CSV header -> one entity 'records' with inferred-from-sample types."""
    if not isinstance(text, str) or not text.strip():
        return {}
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {}
    header = rows[0]
    sample = rows[1] if len(rows) > 1 else []
    fields: list[Field] = []
    for idx, col in enumerate(header):
        col = (col or "").strip()
        if not col:
            continue
        val = sample[idx] if idx < len(sample) else ""
        _append_field(fields, {
            "name": col,
            "type": _infer_scalar_type(val),
            "primary_key": _looks_like_pk(col, "records"),
        })
    return {"records": fields} if fields else {}


def parse_sql_information_schema(rows: list[dict[str, Any]]) -> dict[str, list[Field]]:
    """information_schema.columns rows -> {table: [Field]}.

    Each row: {table_name, column_name, data_type, is_nullable, is_primary_key?}.
    """
    out: dict[str, list[Field]] = {}
    for r in (rows if isinstance(rows, list) else []):
        table = str(r.get("table_name") or r.get("table") or "").strip()
        col = str(r.get("column_name") or r.get("column") or "").strip()
        if not table or not col:
            continue
        _append_field(out.setdefault(table, []), {
            "name": col,
            "type": _sql_type(str(r.get("data_type") or "")),
            "nullable": str(r.get("is_nullable") or "YES").upper() != "NO",
            "primary_key": _bool(r.get("is_primary_key")),
        })
    out = {t: f for t, f in out.items() if f}
    return out


def parse_json_sample(sample: Any, entity_name: str = "records") -> dict[str, list[Field]]:
    """A sample REST JSON payload -> inferred fields. Handles {data:[...]}, [...], {...}."""
    obj = _first_record(sample)
    if not isinstance(obj, dict):
        return {}
    fields: list[Field] = []
    for k, v in obj.items():
        _append_field(fields, {
            "name": str(k),
            "type": _infer_scalar_type(v),
            "primary_key": _looks_like_pk(str(k), entity_name),
        })
    return {entity_name: fields} if fields else {}


def parse_graphql_introspection(sample: Any) -> dict[str, list[Field]]:
    """GraphQL introspection result -> object types as entities."""
    if not _is_graphql_introspection(sample):
        return {}
    types = sample["data"]["__schema"].get("types") or []
    out: dict[str, list[Field]] = {}
    for t in types:
        if not isinstance(t, dict) or t.get("kind") != "OBJECT":
            continue
        name = str(t.get("name") or "")
        if not name or name.startswith("__") or name in {"Query", "Mutation", "Subscription"}:
            continue
        fields: list[Field] = []
        for fld in (t.get("fields") or []):
            fname = str(fld.get("name") or "")
            if not fname:
                continue
            _append_field(fields, {
                "name": fname,
                "type": _graphql_type(fld.get("type")),
                "primary_key": fname.lower() in {"id", f"{name.lower()}_id"},
            })
        if fields:
            out[name] = fields
            if len(out) >= _MAX_ENTITIES:
                break
    return out


def parse_wsdl_elements(wsdl_xml: str) -> dict[str, list[Field]]:
    """Best-effort: extract xsd:complexType elements as entities. XXE-safe."""
    if not wsdl_xml or not wsdl_xml.strip():
        return {}
    try:
        root = ET.fromstring(wsdl_xml.encode("utf-8"))
    except Exception:
        return {}
    # Some XML backends (e.g. lxml, depending on what else is loaded in-process)
    # return a 'parsererror' element for malformed input instead of raising —
    # treat that as a parse failure, not a real schema.
    if root is None or _strip_ns(getattr(root, "tag", "") or "").lower() == "parsererror":
        return {}
    out: dict[str, list[Field]] = {}
    for ct in _iter_local(root, "complexType"):
        name = ct.get("name")
        if not name:
            continue
        fields: list[Field] = []
        for el in _iter_local(ct, "element"):
            en = el.get("name")
            if not en:
                continue
            _append_field(fields, {
                "name": en,
                "type": _xsd_type(el.get("type") or "string"),
                "nullable": el.get("minOccurs", "1") == "0",
            })
        if fields:
            out[name] = fields
    return out


def extract_entities(descriptor: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Universal entry: classify the source, parse it, return entities + kind.

    Returns ([{"name", "fields":[Field]}], source_kind). Empty list if nothing
    could be parsed (caller falls back to static connector.yaml).
    """
    if not isinstance(descriptor, dict):
        return [], "rest_sample"
    kind = detect_source_kind(descriptor)
    by_entity: dict[str, list[Field]] = {}
    if kind == "odata":
        by_entity = schema_introspect.parse_odata_metadata(
            str(descriptor.get("edmx") or descriptor.get("metadata") or "")
        )
    elif kind == "openapi":
        spec = descriptor.get("spec") or descriptor.get("openapi") or {}
        by_entity = schema_introspect.parse_openapi_fields(spec)
    elif kind == "graphql":
        by_entity = parse_graphql_introspection(descriptor.get("sample"))
    elif kind == "sql":
        by_entity = parse_sql_information_schema(
            descriptor.get("columns") or descriptor.get("information_schema") or []
        )
    elif kind == "file_csv":
        by_entity = parse_csv_header(str(descriptor.get("csv") or descriptor.get("text") or ""))
    elif kind == "soap":
        by_entity = parse_wsdl_elements(str(descriptor.get("wsdl") or ""))
    else:  # rest_sample
        by_entity = parse_json_sample(descriptor.get("sample"), descriptor.get("entity_name") or "records")

    entities = [{"name": name, "fields": fields} for name, fields in by_entity.items() if fields]
    # DoS cap (audit #15): a maliciously huge schema (100k entities/fields) would
    # fan out into multi-MB SQL + several in-memory copies. Bound both.
    entities = entities[:_MAX_ENTITIES]
    for e in entities:
        if len(e["fields"]) > _MAX_FIELDS_PER_ENTITY:
            e["fields"] = e["fields"][:_MAX_FIELDS_PER_ENTITY]
    return entities, kind


# ── Type inference helpers ────────────────────────────────────────────────────


def _infer_scalar_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (dict, list)):
        return "json"
    s = str(value or "").strip()
    if re.fullmatch(r"-?\d+", s):
        return "int"
    if re.fullmatch(r"-?\d+\.\d+", s):
        return "float"
    if s.lower() in {"true", "false"}:
        return "bool"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}.*)?", s):
        return "timestamp" if ("T" in s or ":" in s) else "date"
    return "string"


def _sql_type(data_type: str) -> str:
    d = data_type.lower()
    if any(t in d for t in ("int", "serial", "bigint", "smallint")):
        return "int"
    if any(t in d for t in ("numeric", "decimal", "real", "double", "float", "money")):
        return "float"
    if "bool" in d:
        return "bool"
    if "timestamp" in d or "datetime" in d:
        return "timestamp"
    if "date" in d:
        return "date"
    if "json" in d:
        return "json"
    return "string"


def _graphql_type(type_ref: Any) -> str:
    """Unwrap NON_NULL/LIST and map scalar GraphQL types."""
    seen = 0
    t = type_ref
    while isinstance(t, dict) and t.get("ofType") and seen < 6:
        t = t["ofType"]
        seen += 1
    name = str((t or {}).get("name") or "").lower() if isinstance(t, dict) else ""
    return {
        "int": "int", "float": "float", "boolean": "bool",
        "id": "string", "string": "string", "datetime": "timestamp", "date": "date",
    }.get(name, "string")


def _xsd_type(xsd: str) -> str:
    x = xsd.split(":")[-1].lower()
    return {
        "int": "int", "integer": "int", "long": "int", "short": "int",
        "decimal": "float", "double": "float", "float": "float",
        "boolean": "bool", "date": "date", "datetime": "timestamp", "datetimestamp": "timestamp",
    }.get(x, "string")


def _first_record(sample: Any) -> Any:
    if isinstance(sample, list):
        return sample[0] if sample else None
    if isinstance(sample, dict):
        for key in ("data", "results", "value", "items", "records"):
            v = sample.get(key)
            if isinstance(v, list) and v:
                return v[0]
            if isinstance(v, dict):
                return v
        return sample
    return None


def _looks_like_pk(field_name: str, entity_name: str) -> bool:
    """Heuristic primary-key detection: 'id', '{entity}_id', or the singular
    '{entity-without-trailing-s}_id' (e.g. 'deals' -> 'deal_id')."""
    fn = field_name.lower()
    en = entity_name.lower()
    singular = en[:-1] if en.endswith("s") else en
    return fn in {"id", f"{en}_id", f"{singular}_id"}


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _iter_local(element: Any, local_name: str) -> list[Any]:
    return [e for e in element.iter() if _strip_ns(e.tag) == local_name]
