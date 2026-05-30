"""Schema introspection helpers for Studio.

The functions in this module are deliberately pure: they do not perform I/O,
do not log credentials, and never raise for malformed customer specs. Callers
receive an empty mapping when a document cannot be parsed and can attach their
own reason at the API boundary.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, NotRequired, TypedDict
import re
import xml.etree.ElementTree as ET


class Field(TypedDict):
    name: str
    type: str
    nullable: bool
    primary_key: bool
    source_type: str
    source: NotRequired[str]
    source_name: NotRequired[str]


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_REF_PREFIX = "#/components/schemas/"


def _safe_entity_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "")).strip("_")
    if not name:
        return ""
    if name[0].isdigit():
        name = f"_{name}"
    return name[:128] if _IDENT_RE.fullmatch(name[:128]) else ""


def _field_name(value: str) -> str:
    name = str(value or "")
    return name if _IDENT_RE.fullmatch(name) else ""


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _json_type(schema: dict[str, Any]) -> tuple[str, str]:
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        raw = next((str(t) for t in raw_type if t != "null"), "string")
    else:
        raw = str(raw_type or "")
    fmt = str(schema.get("format") or "").lower()
    source_type = raw
    if fmt:
        source_type = f"{raw}:{fmt}" if raw else fmt
    if fmt in {"date"}:
        return "date", source_type
    if fmt in {"date-time", "datetime", "timestamp"}:
        return "timestamp", source_type
    if raw in {"integer", "int"}:
        return "int", source_type or raw
    if raw in {"number"}:
        return "float", source_type or raw
    if raw in {"boolean", "bool"}:
        return "bool", source_type or raw
    if raw in {"object", "array"}:
        return "json", source_type or raw
    return "string", source_type or raw or "string"


def _edm_type(source_type: str) -> str:
    typ = str(source_type or "").lower()
    typ = typ.removeprefix("edm.")
    if typ in {"byte", "sbyte", "int16", "int32", "int64"}:
        return "int"
    if typ in {"decimal", "double", "single", "float"}:
        return "float"
    if typ in {"boolean", "bool"}:
        return "bool"
    if typ == "date":
        return "date"
    if typ in {"datetime", "datetimeoffset", "time", "timeofday"}:
        return "timestamp"
    if typ in {"binary", "stream"}:
        return "json"
    return "string"


def _source_type(source_type: str) -> str:
    typ = str(source_type or "").lower().strip()
    if typ.startswith("edm."):
        return _edm_type(typ)
    if typ in {"string", "str", "varchar", "character varying", "char", "character", "text", "uuid"}:
        return "string"
    if typ in {"int", "integer", "smallint", "bigint", "serial", "bigserial"} or typ.startswith("int"):
        return "int"
    if typ in {"number", "numeric", "decimal", "double", "double precision", "real", "float"}:
        return "float"
    if typ in {"bool", "boolean"}:
        return "bool"
    if typ == "date":
        return "date"
    if typ in {"time", "timestamp", "timestamp without time zone", "timestamp with time zone", "datetime", "timestamptz"}:
        return "timestamp"
    if typ in {"json", "jsonb", "array", "object", "struct"} or typ.endswith("[]"):
        return "json"
    return _json_type({"type": typ})[0]


def _nullable_from_json(schema: dict[str, Any], required: set[str], name: str) -> bool:
    raw_type = schema.get("type")
    has_null = isinstance(raw_type, list) and "null" in raw_type
    if schema.get("nullable") is True or has_null:
        return True
    if name not in required:
        return True
    return False


def _resolve_ref(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        target = components.get(ref[len(_REF_PREFIX):])
        if isinstance(target, dict):
            return target
    return schema


def _schema_properties(schema: dict[str, Any], components: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    schema = _resolve_ref(schema, components)
    if not isinstance(schema, dict):
        return {}, set()
    properties: dict[str, Any] = {}
    required_set: set[str] = set()
    if "allOf" in schema and isinstance(schema["allOf"], list):
        for part in schema["allOf"]:
            if isinstance(part, dict):
                part_props, part_required = _schema_properties(part, components)
                properties.update(part_props)
                required_set.update(part_required)
    own_properties = schema.get("properties")
    required = schema.get("required")
    if isinstance(own_properties, dict):
        properties.update(own_properties)
    if isinstance(required, list):
        required_set.update(str(item) for item in required if isinstance(item, str))
    return (
        properties,
        required_set,
    )


def _fields_from_json_schema(schema: dict[str, Any], components: dict[str, Any]) -> list[Field]:
    properties, required = _schema_properties(schema, components)
    fields: list[Field] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        field_name = _field_name(str(name))
        if not field_name:
            continue
        prop = _resolve_ref(prop, components)
        canonical, source_type = _json_type(prop)
        fields.append({
            "name": field_name,
            "type": canonical,
            "nullable": _nullable_from_json(prop, required, str(name)),
            "primary_key": bool(
                _bool_value(prop.get("x-primary-key"))
                or _bool_value(prop.get("primary_key"))
                or str(name).lower() in {"id", "uuid"}
            ),
            "source_type": source_type,
            "source_name": str(name),
        })
    return fields


def _response_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return None
    for status in ("200", "201", "default"):
        response = responses.get(status)
        if not isinstance(response, dict):
            continue
        content = response.get("content")
        if not isinstance(content, dict):
            continue
        for media in ("application/json", "application/vnd.api+json", "*/*"):
            body = content.get(media)
            if isinstance(body, dict) and isinstance(body.get("schema"), dict):
                return body["schema"]
        for body in content.values():
            if isinstance(body, dict) and isinstance(body.get("schema"), dict):
                return body["schema"]
    return None


def _entity_name_from_path(path: str) -> str:
    segments = [
        s for s in str(path).strip("/").split("/")
        if s and not (s.startswith("{") and s.endswith("}"))
    ]
    return _safe_entity_name(segments[-1]) if segments else ""


def _schema_from_array_or_ref(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    schema = _resolve_ref(schema, components)
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        return _resolve_ref(schema["items"], components)
    if isinstance(schema.get("properties"), dict):
        for key in ("data", "items", "results", "value"):
            nested = schema["properties"].get(key)
            if isinstance(nested, dict):
                nested = _resolve_ref(nested, components)
                if nested.get("type") == "array" and isinstance(nested.get("items"), dict):
                    return _resolve_ref(nested["items"], components)
    return schema


def parse_openapi_fields(spec: dict) -> dict[str, list[Field]]:
    """Return entity fields from OpenAPI components.schemas and path responses."""
    try:
        if not isinstance(spec, dict):
            return {}
        components = ((spec.get("components") or {}).get("schemas") or {})
        components = components if isinstance(components, dict) else {}
        out: dict[str, list[Field]] = {}
        for name, schema in components.items():
            if not isinstance(schema, dict):
                continue
            entity = _safe_entity_name(str(name))
            if not entity:
                continue
            fields = _fields_from_json_schema(deepcopy(schema), components)
            if fields:
                out[entity] = fields

        paths = spec.get("paths")
        if isinstance(paths, dict):
            for path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue
                entity = _entity_name_from_path(str(path))
                if not entity or entity in out:
                    continue
                for method in ("get", "post", "put", "patch"):
                    op = path_item.get(method)
                    if not isinstance(op, dict):
                        continue
                    schema = _response_schema(op)
                    if not schema:
                        continue
                    fields = _fields_from_json_schema(_schema_from_array_or_ref(schema, components), components)
                    if fields:
                        out[entity] = fields
                        break
        return out
    except Exception:
        return {}


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _local_children(element: ET.Element, local_name: str) -> list[ET.Element]:
    return [child for child in list(element) if _strip_namespace(child.tag) == local_name]


def _entity_type_name(value: str) -> str:
    return str(value or "").rsplit(".", 1)[-1]


def parse_odata_entity_sets(edmx_xml: str) -> dict[str, str]:
    """Return OData EntitySet name -> EntityType name mappings."""
    try:
        if not isinstance(edmx_xml, str) or not edmx_xml.strip():
            return {}
        root = ET.fromstring(edmx_xml.encode("utf-8"))
        out: dict[str, str] = {}
        for entity_set in root.iter():
            if _strip_namespace(entity_set.tag) != "EntitySet":
                continue
            set_name = _safe_entity_name(entity_set.attrib.get("Name", ""))
            type_name = _entity_type_name(entity_set.attrib.get("EntityType", ""))
            if set_name and type_name:
                out[set_name] = type_name
        return out
    except Exception:
        return {}


def parse_odata_metadata(edmx_xml: str) -> dict[str, list[Field]]:
    """Parse OData V2/V4 EDMX EntityType/Property metadata."""
    try:
        if not isinstance(edmx_xml, str) or not edmx_xml.strip():
            return {}
        root = ET.fromstring(edmx_xml.encode("utf-8"))
        type_fields: dict[str, list[Field]] = {}
        for entity_type in root.iter():
            if _strip_namespace(entity_type.tag) != "EntityType":
                continue
            entity_name = _safe_entity_name(entity_type.attrib.get("Name", ""))
            if not entity_name:
                continue
            keys: set[str] = set()
            for key in _local_children(entity_type, "Key"):
                for ref in _local_children(key, "PropertyRef"):
                    ref_name = ref.attrib.get("Name")
                    if ref_name:
                        keys.add(ref_name)
            fields: list[Field] = []
            for prop in _local_children(entity_type, "Property"):
                source_name = prop.attrib.get("Name", "")
                field_name = _field_name(source_name)
                if not field_name:
                    continue
                source_type = prop.attrib.get("Type", "Edm.String")
                nullable = str(prop.attrib.get("Nullable", "true")).lower() != "false"
                fields.append({
                    "name": field_name,
                    "type": _edm_type(source_type),
                    "nullable": False if field_name in keys else nullable,
                    "primary_key": field_name in keys,
                    "source_type": source_type,
                    "source_name": source_name,
                })
            if fields:
                type_fields[entity_name] = fields

        out = dict(type_fields)
        for set_name, type_name in parse_odata_entity_sets(edmx_xml).items():
            if set_name and type_name in type_fields:
                out.setdefault(set_name, [dict(field) for field in type_fields[type_name]])
        return out
    except Exception:
        return {}


def normalize_field(field: dict[str, Any]) -> Field:
    """Normalize a Studio field dict while preserving source type metadata."""
    name = _field_name(str(field.get("name") or field.get("id") or ""))
    if not name:
        raise ValueError("field.name is required")
    typ = str(field.get("type") or field.get("data_type") or field.get("source_type") or "string")
    source_type = str(field.get("source_type") or typ)
    canonical = typ.lower()
    if canonical not in {"string", "int", "float", "bool", "date", "timestamp", "json"}:
        canonical = _source_type(source_type or typ)
    return {
        "name": name,
        "type": canonical,
        "nullable": _bool_value(field.get("nullable"), default=True),
        "primary_key": _bool_value(field.get("primary_key")) or _bool_value(field.get("is_primary_key")),
        "source_type": source_type,
        "source_name": str(field.get("source_name") or field.get("name") or name),
    }
