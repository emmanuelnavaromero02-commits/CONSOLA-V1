"""Deterministic rules of the Catalog Copilot.

Pure functions only: no I/O, no LLM. Everything here turns counts and
structural facts (column names, types, null rates, exact key checks,
containment ratios) into labels, confidences and executive Spanish text.
Evidence produced here is counts and rule codes, never sampled values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

RULES_VERSION = "catalog-copilot/1"

SEMANTIC_TYPES = (
    "identifier",
    "text",
    "date",
    "datetime",
    "time",
    "money",
    "number",
    "integer",
    "percent",
    "boolean",
    "complex",
)

HUMAN_TYPE_LABEL = {
    "identifier": "Identificador",
    "text": "Texto",
    "date": "Fecha",
    "datetime": "Fecha y hora",
    "time": "Hora",
    "money": "Monto Monetario",
    "number": "Número",
    "integer": "Número entero",
    "percent": "Porcentaje",
    "boolean": "Sí/No",
    "complex": "Lista o estructura",
}

CLASSIFICATIONS = ("pii", "financial", "confidential")
CLASSIFICATION_LABEL = {
    "pii": "Dato Personal Identificable",
    "financial": "Información Financiera",
    "confidential": "Confidencial",
}
SENSITIVE_STATS = frozenset({"pii", "financial"})
PROTECTION_LEVELS = frozenset({"masked", "encrypted", "shadowed"})

# Value patterns evaluated by DuckDB (RE2) on a bounded sample. Constants
# only: user input never reaches these expressions.
PII_PATTERNS: dict[str, str] = {
    "rfc": r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$",
    "curp": r"^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$",
    "email": r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$",
    "clabe": r"^\d{18}$",
    "card": r"^(4\d{12}(\d{3})?|5[1-5]\d{14}|3[47]\d{13})$",
    "phone_mx": r"^(\+?52)?\s?\d{10}$",
    "person_name": r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+( [A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+$",
}
PATTERN_CLASS = {
    "rfc": "pii",
    "curp": "pii",
    "email": "pii",
    "phone_mx": "pii",
    "person_name": "pii",
    "card": "financial",
    "clabe": "financial",
}
# Patterns that prove sensitivity on their own; the rest only support a
# matching name hint (ten or eighteen digits are also ordinary codes).
STANDALONE_PATTERNS = frozenset({"rfc", "curp", "email", "card"})
UPPERCASE_PATTERNS = frozenset({"rfc", "curp"})
PATTERN_MIN_SAMPLE = 20
PATTERN_MIN_RATIO = 0.8
MAX_PATTERN_COLUMNS = 60
PATTERN_SAMPLE_ROWS = 1000

_KEY_TOKENS = frozenset(
    {"id", "code", "codigo", "key", "externalcode", "pernr", "uuid", "guid"}
)
# Technical key names that carry no id/code token of their own.
_KEY_NAMES = frozenset(
    {"pernr", "bukrs", "kostl", "orgeh", "kunnr", "lifnr", "idempleado", "employeenumber"}
)
_STRONG_MONEY_TOKENS = frozenset(
    {
        "amount", "monto", "importe", "price", "precio", "cost", "costo",
        "revenue", "ingreso", "ingresos", "betrg", "dmbtr", "netpr", "mrr",
        "arr", "billing", "facturacion", "margin", "margen", "budget",
        "presupuesto", "salary", "salario", "sueldo", "paycomp",
    }
)
_WEAK_MONEY_TOKENS = frozenset({"total", "value", "valor", "pay", "comp"})
_COUNT_TOKENS = frozenset(
    {
        "count", "qty", "quantity", "headcount", "cantidad", "num", "numero",
        "employees", "empleados", "conteo", "days", "dias", "hours", "horas",
    }
)
_PERCENT_TOKENS = frozenset(
    {"pct", "percent", "percentage", "ratio", "rate", "porcentaje", "tasa"}
)
_SALARY_TOKENS = frozenset(
    {"salary", "salario", "sueldo", "compensation", "compensacion", "paycomp", "pay", "comp"}
)

_PII_TOKENS = {
    "email": "email",
    "correo": "email",
    "mail": "email",
    "phone": "phone",
    "telefono": "phone",
    "celular": "phone",
    "mobile": "phone",
    "rfc": "rfc",
    "curp": "curp",
    "nss": "nss",
    "ssn": "nss",
    "passport": "passport",
    "pasaporte": "passport",
    "dni": "national_id",
    "birth": "birth",
    "nacimiento": "birth",
    "dob": "birth",
    "gbdat": "birth",
    "address": "address",
    "direccion": "address",
    "domicilio": "address",
    "zip": "address",
    "postal": "address",
    "gender": "gender",
    "genero": "gender",
    "marital": "marital",
    "nationality": "nationality",
    "nacionalidad": "nationality",
    "ip": "ip",
}
_PERSON_NAME_EXACT = frozenset(
    {
        "nombre", "nombres", "nombrecompleto", "apellido", "apellidos",
        "apellidopaterno", "apellidomaterno", "firstname", "lastname",
        "fullname", "formalname", "middlename", "givenname", "familyname",
        "nombreempleado", "nombrecolaborador",
    }
)
_PERSON_NAME_QUALIFIERS = frozenset(
    {"first", "last", "full", "formal", "middle", "given", "family"}
)
_FINANCIAL_TOKENS = {
    "salary": "salary",
    "salario": "salary",
    "sueldo": "salary",
    "compensation": "compensation",
    "compensacion": "compensation",
    "paycomp": "compensation",
    "bonus": "bonus",
    "bono": "bonus",
    "clabe": "bank_account",
    "iban": "bank_account",
    "account": "account",
    "cuenta": "account",
    "bank": "bank_account",
    "banco": "bank_account",
    "card": "card",
    "tarjeta": "card",
}
_CONFIDENTIAL_TOKENS = frozenset(
    {"disciplinary", "disciplinaria", "disciplinario", "medical", "medico", "medica"}
)
_CONFIDENTIAL_PAIRS = (("performance", "rating"), ("termination", "reason"))

PII_KIND_LABEL = {
    "email": "Correo electrónico",
    "phone": "Teléfono",
    "rfc": "RFC",
    "curp": "CURP",
    "nss": "Número de seguridad social",
    "passport": "Pasaporte",
    "national_id": "Identificación oficial",
    "birth": "Fecha de nacimiento",
    "address": "Domicilio",
    "gender": "Género",
    "marital": "Estado civil",
    "nationality": "Nacionalidad",
    "ip": "Dirección IP",
    "person_name": "Nombre",
}

_KEY_SYNONYMS: dict[str, frozenset[str]] = {
    "person": frozenset(
        {
            "personidexternal", "personid", "userid", "employeeid", "empid",
            "employeenumber", "workerid", "pernr", "idempleado",
        }
    ),
    "legal_entity": frozenset(
        {
            "companycode", "company", "companyid", "legalentity",
            "legalentityid", "bukrs", "sociedad", "empresa",
        }
    ),
    "department": frozenset(
        {
            "deptid", "dept", "department", "departmentcode", "departmentid",
            "orgunit", "orgeh", "departamento",
        }
    ),
    "cost_center": frozenset({"costcenter", "costcenterid", "kostl", "centrocosto"}),
    "location": frozenset(
        {"location", "locationid", "locationcode", "site", "sede", "ubicacion"}
    ),
    "position": frozenset({"position", "positionid", "plans", "puesto"}),
    "job": frozenset({"jobcode", "jobid", "jobclassification"}),
    "customer": frozenset({"customer", "customerid", "kunnr", "cliente"}),
    "vendor": frozenset({"vendor", "vendorid", "lifnr", "proveedor"}),
    "project": frozenset({"project", "projectid", "projectcode"}),
}
_ROLE_SYNONYMS = {"managerid": "person", "supervisorid": "person"}
_SCOPE_COLUMNS = frozenset({"tenantid", "workspaceid"})

_TRANSLATION_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("date", "of", "birth"), "nacimiento"),
    (("first", "name"), "nombre"),
    (("last", "name"), "apellido"),
    (("full", "name"), "nombre completo"),
    (("formal", "name"), "nombre formal"),
    (("cost", "center"), "centro de costo"),
    (("legal", "entity"), "entidad legal"),
    (("business", "unit"), "unidad de negocio"),
    (("org", "unit"), "unidad organizativa"),
    (("pay", "grade"), "grado salarial"),
    (("marital", "status"), "estado civil"),
    (("termination", "reason"), "motivo de baja"),
    (("national", "id"), "identificación oficial"),
    (("zip", "code"), "código postal"),
    (("postal", "code"), "código postal"),
    (("performance", "rating"), "calificación de desempeño"),
    (("job", "title"), "título del puesto"),
    (("email", "address"), "correo electrónico"),
    (("phone", "number"), "teléfono"),
    (("hire", "date"), "contratación"),
    (("start", "date"), "inicio"),
    (("end", "date"), "fin"),
    (("last", "modified"), "última modificación"),
    (("last", "updated"), "última actualización"),
    (("created", "by"), "creado por"),
    (("modified", "by"), "modificado por"),
    (("person", "id", "external"), "persona"),
)
_TRANSLATIONS = {
    "hire": "contratación",
    "birth": "nacimiento",
    "department": "departamento",
    "dept": "departamento",
    "employee": "empleado",
    "employees": "empleados",
    "emp": "empleado",
    "worker": "colaborador",
    "person": "persona",
    "user": "usuario",
    "company": "compañía",
    "legal": "legal",
    "entity": "entidad",
    "cost": "costo",
    "center": "centro",
    "location": "ubicación",
    "position": "puesto",
    "job": "puesto",
    "manager": "jefe directo",
    "supervisor": "supervisor",
    "salary": "salario",
    "amount": "monto",
    "start": "inicio",
    "end": "fin",
    "date": "fecha",
    "name": "nombre",
    "email": "correo electrónico",
    "phone": "teléfono",
    "address": "dirección",
    "gender": "género",
    "status": "estado",
    "type": "tipo",
    "number": "número",
    "created": "creación",
    "updated": "actualización",
    "modified": "modificación",
    "last": "último",
    "termination": "baja",
    "reason": "motivo",
    "country": "país",
    "city": "ciudad",
    "state": "estado",
    "currency": "moneda",
    "value": "valor",
    "count": "conteo",
    "rate": "tasa",
    "customer": "cliente",
    "vendor": "proveedor",
    "project": "proyecto",
    "org": "organización",
    "unit": "unidad",
    "level": "nivel",
    "grade": "grado",
    "title": "título",
    "description": "descripción",
    "effective": "vigencia",
    "active": "activo",
    "flag": "indicador",
    "rating": "calificación",
    "performance": "desempeño",
    "bonus": "bono",
    "pay": "pago",
    "comp": "compensación",
    "compensation": "compensación",
    "hours": "horas",
    "hour": "hora",
    "week": "semana",
    "month": "mes",
    "year": "año",
    "day": "día",
    "quantity": "cantidad",
    "qty": "cantidad",
    "price": "precio",
    "revenue": "ingreso",
    "margin": "margen",
    "budget": "presupuesto",
    "invoice": "factura",
    "order": "pedido",
    "item": "artículo",
    "product": "producto",
    "region": "región",
    "area": "área",
    "division": "división",
    "business": "negocio",
    "nationality": "nacionalidad",
    "age": "edad",
    "tenure": "antigüedad",
    "seniority": "antigüedad",
    "external": "externo",
    "internal": "interno",
    "headcount": "plantilla",
    "event": "evento",
    "time": "tiempo",
    "entry": "registro",
    "total": "total",
    "family": "familia",
    "category": "categoría",
    "source": "origen",
    "target": "meta",
    "goal": "objetivo",
    "review": "evaluación",
    "record": "registro",
    "sales": "ventas",
    "balance": "saldo",
    "account": "cuenta",
    "bank": "banco",
    "tax": "impuesto",
    "document": "documento",
    "posting": "contabilización",
    "turnover": "rotación",
    "pernr": "número de personal",
    "departments": "departamentos",
    "companies": "compañías",
    "locations": "ubicaciones",
    "positions": "puestos",
    "jobs": "puestos",
    "projects": "proyectos",
    "customers": "clientes",
    "vendors": "proveedores",
    "users": "usuarios",
    "people": "personas",
    "persons": "personas",
    "months": "meses",
    "days": "días",
    "team": "equipo",
    "teams": "equipos",
    "plan": "plan",
    "notes": "notas",
    "note": "nota",
    "shift": "turno",
    "site": "sede",
    "label": "etiqueta",
    "badge": "gafete",
    "children": "hijos",
    "vacation": "vacaciones",
    "benefit": "beneficio",
    "score": "puntaje",
    "weight": "peso",
    "events": "eventos",
    "absence": "ausencia",
    "absences": "ausencias",
    "salaries": "salarios",
    "headcounts": "plantillas",
    "hierarchy": "jerarquía",
    "structure": "estructura",
    "by": "por",
    "per": "por",
    "and": "y",
    "with": "con",
    "history": "historial",
    "summary": "resumen",
    "detail": "detalle",
    "master": "maestro",
}
# "<x> name" reads "nombre de <x>" in Spanish.
_HEAD_NOUNS = {
    "name": "nombre",
    "number": "número",
    "level": "nivel",
    "type": "tipo",
    "status": "estado",
    "count": "conteo",
    "description": "descripción",
    "reason": "motivo",
}
# Tokens that restate the column's type and read badly after "Fecha de ..."
_TYPE_WORDS = {
    "date": frozenset({"date", "fecha", "dt", "day"}),
    "datetime": frozenset({"date", "fecha", "time", "timestamp", "datetime", "at", "ts"}),
    "time": frozenset({"time", "hora"}),
    "identifier": frozenset({"id", "code", "codigo", "key", "uuid", "guid"}),
    "money": frozenset({"amount", "monto", "importe"}),
    "percent": frozenset(
        {"pct", "percent", "percentage", "porcentaje", "rate", "ratio", "tasa"}
    ),
    "boolean": frozenset({"is", "has", "flag", "es"}),
}
_LABEL_STOPWORDS = frozenset({"of", "the", "on", "at"})
_CARTRIDGE_LABEL = {
    "sap_successfactors": "SAP SuccessFactors",
    "sap_b1": "SAP Business One",
    "sap_hcm": "SAP HCM",
    "sap_s4hana": "SAP S/4HANA",
    "replicon": "Replicon",
    "hubspot": "HubSpot",
    "salesforce": "Salesforce",
    "banxico": "Banxico",
    "inegi": "INEGI",
    "sec_edgar": "SEC EDGAR",
}
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_DISPLAY_NAME_SUFFIXES = ("latest", "curated", "current", "v1", "v2")
MAX_DESCRIPTION = 600
MAX_DISPLAY_NAME = 200


def _split_tokens(name: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name or ""))
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return [token for token in re.split(r"[^A-Za-z0-9]+", text.lower()) if token]


def normalize_name(name: str) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def _strip_accents(text: str) -> str:
    table = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return text.translate(table)


def humanize_identifier(value: str) -> str:
    """Port of console semantic_humanize_identifier, kept byte-compatible."""
    text = str(value or "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_\-/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def _raw(raw_type: str | None) -> str:
    return re.sub(r"\s+", " ", str(raw_type or "").strip().upper())


def _is_complex(raw: str) -> bool:
    return (
        raw.endswith("]")
        or raw.startswith(("LIST", "STRUCT", "MAP", "UNION", "ARRAY"))
        or raw in {"JSON", "JSONB"}
    )


def _is_integer(raw: str) -> bool:
    if re.fullmatch(r"(DECIMAL|NUMERIC)\s*\(\s*\d+\s*,\s*0\s*\)", raw):
        return True
    base = raw.split("(")[0].strip()
    return base in {
        "TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT",
        "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "INT1",
        "INT2", "INT4", "INT8", "SERIAL", "BIGSERIAL", "SMALLSERIAL",
    }


def _is_numeric(raw: str) -> bool:
    if _is_integer(raw):
        return True
    base = raw.split("(")[0].strip()
    return base in {
        "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL",
        "DECIMAL", "NUMERIC", "MONEY",
    }


def _is_texty(raw: str) -> bool:
    base = raw.split("(")[0].strip()
    return (
        base in {"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR", "UUID", "NAME"}
        or base.startswith(("CHARACTER", "VARCHAR"))
        or not base
    )


def _key_like(tokens: list[str], normalized: str) -> bool:
    return (
        normalized in _KEY_TOKENS
        or normalized in _KEY_NAMES
        or any(token in _KEY_TOKENS for token in tokens)
    )


def semantic_type(raw_type: str | None, column: str, *, is_key: bool = False) -> str:
    raw = _raw(raw_type)
    tokens = _split_tokens(column)
    normalized = normalize_name(column)
    token_set = set(tokens) | {normalized}
    if raw.startswith("BOOL"):
        return "boolean"
    if _is_complex(raw):
        return "complex"
    if raw == "DATE":
        return "date"
    if raw.startswith(("TIMESTAMP", "DATETIME")):
        return "datetime"
    if raw.startswith("TIME"):
        return "time"
    key_like = is_key or _key_like(tokens, normalized)
    if _is_numeric(raw):
        if key_like:
            return "identifier"
        counts = bool(token_set & _COUNT_TOKENS)
        strong_money = bool(token_set & _STRONG_MONEY_TOKENS) or "paycomp" in normalized
        if strong_money and not counts:
            return "money"
        if token_set & _PERCENT_TOKENS:
            return "percent"
        if token_set & _WEAK_MONEY_TOKENS and not counts and not _is_integer(raw):
            return "money"
        return "integer" if _is_integer(raw) else "number"
    if _is_texty(raw) and key_like:
        return "identifier"
    return "text"


def human_type_label(semantic: str) -> str:
    return HUMAN_TYPE_LABEL.get(semantic, HUMAN_TYPE_LABEL["text"])


@dataclass(frozen=True)
class NameHints:
    pii: tuple[str, ...] = ()
    financial: tuple[str, ...] = ()
    confidential: tuple[str, ...] = ()

    @property
    def any(self) -> bool:
        return bool(self.pii or self.financial or self.confidential)


def name_hints(column: str) -> NameHints:
    tokens = _split_tokens(column)
    normalized = normalize_name(column)
    token_set = set(tokens)
    pii: list[str] = []
    financial: list[str] = []
    confidential: list[str] = []
    for token in tokens:
        kind = _PII_TOKENS.get(token)
        if kind and kind not in pii:
            pii.append(kind)
        fin = _FINANCIAL_TOKENS.get(token)
        if fin and fin not in financial:
            financial.append(fin)
        if token in _CONFIDENTIAL_TOKENS and token not in confidential:
            confidential.append(token)
    if "paycomp" in normalized and "compensation" not in financial:
        financial.append("compensation")
    if "national" in token_set and "id" in token_set and "national_id" not in pii:
        pii.append("national_id")
    person_name = normalized in _PERSON_NAME_EXACT or (
        "name" in token_set and bool(token_set & _PERSON_NAME_QUALIFIERS)
    ) or bool(token_set & {"apellido", "apellidos"})
    if person_name and "person_name" not in pii:
        pii.append("person_name")
    for left, right in _CONFIDENTIAL_PAIRS:
        if left in token_set and right in token_set:
            confidential.append(f"{left}_{right}")
    return NameHints(tuple(pii), tuple(financial), tuple(confidential))


@dataclass(frozen=True)
class ColumnClassification:
    classifications: tuple[str, ...] = ()
    confidence: float | None = None
    basis: tuple[str, ...] = ()
    origin: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    pii_kind: str | None = None


def _ordered(classes: set[str]) -> tuple[str, ...]:
    if classes & SENSITIVE_STATS:
        classes = classes | {"confidential"}
    return tuple(name for name in CLASSIFICATIONS if name in classes)


def classify_column(
    column: str,
    semantic: str,
    *,
    declared_protection: str | None = None,
    pattern_hits: dict[str, int] | None = None,
    sampled_non_null: int = 0,
) -> ColumnClassification:
    """Sensitivity labels with a confidence and counts-only evidence."""
    hints = name_hints(column)
    hits = {
        name: int(count)
        for name, count in (pattern_hits or {}).items()
        if name in PII_PATTERNS and int(count or 0) > 0
    }
    sampled = max(0, int(sampled_non_null or 0))
    basis: list[str] = []
    classes: set[str] = set()
    confidence = 0.0
    pii_kind: str | None = hints.pii[0] if hints.pii else None

    name_classes: set[str] = set()
    if hints.pii:
        name_classes.add("pii")
        basis.extend(f"name:{kind}" for kind in hints.pii)
    if hints.financial:
        name_classes.add("financial")
        basis.extend(f"name:{kind}" for kind in hints.financial)
    if hints.confidential:
        name_classes.add("confidential")
        basis.extend(f"name:{kind}" for kind in hints.confidential)

    matched_patterns: list[str] = []
    if sampled >= PATTERN_MIN_SAMPLE:
        for name, count in sorted(hits.items()):
            if count / sampled < PATTERN_MIN_RATIO:
                continue
            pattern_class = PATTERN_CLASS[name]
            if name not in STANDALONE_PATTERNS and pattern_class not in name_classes:
                continue
            if semantic == "identifier" and name not in {"rfc", "curp", "email"}:
                continue
            matched_patterns.append(name)
            basis.append(f"pattern:{name}")
            classes.add(pattern_class)
            if pattern_class == "pii" and name != "person_name":
                pii_kind = pii_kind or name
    level = str(declared_protection or "").strip().lower()
    origin: str | None = None
    if level in PROTECTION_LEVELS:
        classes |= name_classes or {"pii"}
        basis.insert(0, f"declared:{level}")
        confidence = 1.0
        origin = "packaged"
    elif matched_patterns:
        classes |= name_classes
        corroborated = bool(
            {PATTERN_CLASS[name] for name in matched_patterns} & name_classes
        )
        if any(name in {"clabe", "phone_mx"} for name in matched_patterns):
            confidence = 0.9
        else:
            confidence = 0.97 if corroborated else 0.9
        origin = "copilot"
    elif name_classes:
        classes |= name_classes
        confidence = 0.7
        origin = "copilot"
    if semantic == "money" and set(_split_tokens(column)) & _SALARY_TOKENS:
        classes |= {"financial", "confidential"}
        basis.append("type:money")
        if confidence < 0.85:
            confidence = 0.85
            origin = origin or "copilot"
    if not classes:
        return ColumnClassification()
    evidence: dict[str, Any] = {"basis": list(dict.fromkeys(basis))}
    if hits:
        evidence["pattern_hits"] = dict(sorted(hits.items()))
    if sampled:
        evidence["sampled_non_null"] = sampled
        evidence["sample_method"] = "first_rows"
    return ColumnClassification(
        classifications=_ordered(classes),
        confidence=round(confidence, 3),
        basis=tuple(dict.fromkeys(basis)),
        origin=origin,
        evidence=evidence,
        pii_kind=pii_kind if "pii" in classes else None,
    )


def key_class(column: str) -> tuple[str, bool] | None:
    """Business key family of a column and whether it is a role (manager)."""
    normalized = normalize_name(column)
    if not normalized or normalized in _SCOPE_COLUMNS:
        return None
    if normalized in _ROLE_SYNONYMS:
        return _ROLE_SYNONYMS[normalized], True
    for family, names in _KEY_SYNONYMS.items():
        if normalized in names:
            return family, False
    return None


def is_scope_column(column: str) -> bool:
    return normalize_name(column) in _SCOPE_COLUMNS


def cardinality(from_unique: bool, to_unique: bool) -> str:
    if from_unique and to_unique:
        return "1:1"
    if to_unique:
        return "N:1"
    if from_unique:
        return "1:N"
    return "N:N"


def relationship_confidence(match: dict[str, Any]) -> float | None:
    """Confidence of a FK -> PK edge; None means it must not be persisted."""
    containment = match.get("containment")
    if containment is None:
        return None
    try:
        ratio = float(containment)
    except (TypeError, ValueError):
        return None
    if ratio < 0.9:
        return None
    score = 0.5
    if match.get("name_exact"):
        score += 0.2
    elif match.get("same_class"):
        score += 0.1
    if match.get("types_compatible"):
        score += 0.1
    score += 0.2 if ratio >= 0.98 else 0.1
    if match.get("target_key_exact"):
        score += 0.1
    score = round(min(score, 1.0), 3)
    return score if score >= 0.75 else None


def _translate_tokens(
    tokens: list[str], drop: frozenset[str] = frozenset()
) -> list[str]:
    if len(tokens) >= 2 and tokens[-1] in _HEAD_NOUNS and not any(
        tuple(tokens[-len(phrase) :]) == phrase
        for phrase, _spanish in _TRANSLATION_PHRASES
        if len(phrase) <= len(tokens)
    ):
        rest = _translate_tokens(tokens[:-1], drop)
        if rest:
            return [_HEAD_NOUNS[tokens[-1]], "de", *rest]
    out: list[str] = []
    index = 0
    while index < len(tokens):
        for phrase, spanish in _TRANSLATION_PHRASES:
            size = len(phrase)
            if tuple(tokens[index : index + size]) == phrase:
                out.append(spanish)
                index += size
                break
        else:
            token = tokens[index]
            if token not in drop and token not in _LABEL_STOPWORDS:
                out.append(_TRANSLATIONS.get(token, token))
            index += 1
    return out


def column_label(column: str, semantic: str | None = None) -> str:
    """Spanish label without the words that restate the type ("" if none left)."""
    tokens = humanize_identifier(column).split()
    drop = _TYPE_WORDS.get(semantic or "", frozenset())
    words = _translate_tokens(tokens, drop)
    return " ".join(word for word in words if word).strip()


def _of(label: str) -> str:
    return f" de {label}" if label else ""


def format_count(value: int) -> str:
    text = f"{int(value):,}"
    return text.replace(",", " ")


def format_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value).strip()
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
        if not match:
            return None
        try:
            parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return f"{parsed.day} {_MONTHS[parsed.month - 1]} {parsed.year}"


def _sentence_join(items: list[str]) -> str:
    items = [item for item in items if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]


def column_description(ctx: dict[str, Any]) -> str:
    """Executive Spanish description of one column from real facts only."""
    column = str(ctx.get("column") or "")
    semantic = str(ctx.get("semantic_type") or "text")
    classes = set(ctx.get("classifications") or ())
    label = column_label(column, semantic)
    parts: list[str] = []
    pii_kind = ctx.get("pii_kind")
    if "pii" in classes and semantic in {"text", "identifier"} and pii_kind:
        kind = PII_KIND_LABEL.get(str(pii_kind), "Dato")
        parts.append(
            f"{kind} de la persona: dato personal identificable; acceso restringido."
        )
    elif semantic == "identifier":
        if ctx.get("is_key"):
            parts.append(f"Identificador único{_of(label)}.")
        else:
            parts.append(f"Identificador{_of(label)}.")
        links_to = str(ctx.get("links_to") or "").strip()
        if links_to:
            parts.append(f"Enlaza con {links_to}.")
    elif semantic in {"date", "datetime"}:
        noun = "Fecha" if semantic == "date" else "Fecha y hora"
        parts.append(f"{noun}{_of(label)}.")
        if not (classes & SENSITIVE_STATS):
            low = format_date(ctx.get("min_value"))
            high = format_date(ctx.get("max_value"))
            if low and high:
                parts.append(f"Cubre del {low} al {high}.")
    elif semantic == "money":
        parts.append(f"Monto monetario{_of(label)}.")
        if "financial" in classes:
            parts.append("Información financiera confidencial.")
    elif semantic == "boolean":
        parts.append(f"Indicador Sí/No{_of(label)}.")
    elif semantic == "percent":
        parts.append(f"Porcentaje{_of(label)}.")
    elif semantic == "integer":
        parts.append(f"Número entero{_of(label)}.")
    elif semantic == "number":
        parts.append(f"Valor numérico{_of(label)}.")
    elif semantic == "time":
        parts.append(f"Hora{_of(label)}.")
    elif semantic == "complex":
        parts.append(f"Lista o estructura{_of(label)}.")
    else:
        distinct = ctx.get("distinct_count")
        rows = ctx.get("row_count")
        is_category = (
            isinstance(distinct, int)
            and 1 < distinct <= 50
            and (not isinstance(rows, int) or rows <= 0 or distinct <= rows * 0.5)
            and not classes
        )
        if is_category:
            parts.append(f"Categoría{_of(label)} ({distinct} valores distintos).")
        else:
            parts.append(f"Texto{_of(label)}.")
    text_so_far = " ".join(parts)
    if "pii" in classes and "identificable" not in text_so_far:
        parts.append("Dato personal identificable; acceso restringido.")
    elif "financial" in classes and "financiera" not in text_so_far:
        parts.append("Información financiera confidencial.")
    null_rate = ctx.get("null_rate")
    if isinstance(null_rate, (int, float)) and null_rate >= 0.2:
        parts.append(
            f"Incompleto: {round(float(null_rate) * 100)}% de los registros no tienen dato."
        )
    text = " ".join(parts)
    return text[:MAX_DESCRIPTION]


def cartridge_label(cartridge: str | None) -> str:
    value = str(cartridge or "").strip()
    if not value:
        return ""
    return _CARTRIDGE_LABEL.get(value, humanize_identifier(value).title())


def dataset_display_name(name: str, cartridge: str | None = None) -> str:
    text = str(name or "").strip()
    lowered = text.lower()
    for prefix in ("silver_", "gold_", "bronze_"):
        if lowered.startswith(prefix):
            text, lowered = text[len(prefix) :], lowered[len(prefix) :]
    cart = str(cartridge or "").strip().lower()
    if cart and lowered.startswith(cart + "_"):
        text = text[len(cart) + 1 :]
    tokens = humanize_identifier(text).split()
    while len(tokens) > 1 and tokens[-1] in _DISPLAY_NAME_SUFFIXES:
        tokens.pop()
    words = _translate_tokens(tokens) if tokens else [humanize_identifier(name)]
    label = " ".join(words).strip() or str(name)
    label = label[:1].upper() + label[1:]
    return label[:MAX_DISPLAY_NAME]


def source_display_name(source: str) -> str:
    parts = [part for part in str(source or "").split("/") if part]
    entity = parts[-1] if parts else str(source or "")
    return dataset_display_name(entity)


def dataset_description(facts: dict[str, Any]) -> str:
    """Executive dataset summary; unknown clauses are omitted, never guessed."""
    display = str(facts.get("display_name") or "").strip() or "Tabla"
    source = cartridge_label(facts.get("cartridge"))
    sentences: list[str] = []
    head = f"{display} de {source}" if source else display
    rows = facts.get("row_count")
    refreshed = format_date(facts.get("last_refresh"))
    clause = ""
    if isinstance(rows, int) and rows >= 0:
        clause = f"{format_count(rows)} registros"
        if refreshed:
            clause += f", actualizados el {refreshed}"
    elif refreshed:
        clause = f"actualizada el {refreshed}"
    sentences.append(f"{head}: {clause}." if clause else f"{head}.")
    counts = facts.get("type_counts") or {}
    pieces: list[str] = []
    for key, singular, plural in (
        ("identifier", "identificador", "identificadores"),
        ("date", "fecha", "fechas"),
        ("money", "monto monetario", "montos monetarios"),
    ):
        value = int(counts.get(key) or 0)
        if value > 0:
            pieces.append(f"{value} {singular if value == 1 else plural}")
    if pieces:
        sentences.append(f"Incluye {_sentence_join(pieces)}.")
    pii_kinds = [str(kind) for kind in facts.get("pii_kinds") or [] if kind]
    has_pii = bool(facts.get("pii_columns"))
    has_financial = bool(facts.get("financial_columns"))
    if has_pii or has_financial:
        sensitive: list[str] = []
        if has_pii:
            labels = [
                PII_KIND_LABEL.get(kind, kind).lower()
                if kind not in {"rfc", "curp"}
                else PII_KIND_LABEL[kind]
                for kind in dict.fromkeys(pii_kinds)
            ][:4]
            sensitive.append(
                "datos personales identificables"
                + (f" ({', '.join(labels)})" if labels else "")
            )
        if has_financial:
            sensitive.append("información financiera")
        joined = " e ".join(sensitive) if len(sensitive) == 2 else sensitive[0]
        sentences.append(f"Contiene {joined}; trátela como confidencial.")
    related = [str(item) for item in facts.get("related") or [] if item]
    related = list(dict.fromkeys(related))
    if related:
        shown = related[:3]
        rest = len(related) - len(shown)
        text = _sentence_join(shown)
        if rest > 0:
            text = f"{', '.join(shown)} y {rest} más"
        sentences.append(f"Se vincula con {text}.")
    description = " ".join(sentences)
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rsplit(" ", 1)[0] + "…"
    return description


def evidence_is_counts_only(evidence: dict[str, Any]) -> bool:
    """Mirror of the DB CHECK: no key that could carry sampled values."""
    forbidden = {"values", "examples", "sample", "samples", "min", "max"}
    if not isinstance(evidence, dict) or forbidden & set(evidence):
        return False
    hits = evidence.get("pattern_hits") or {}
    if not isinstance(hits, dict) or not all(
        isinstance(value, int) and not isinstance(value, bool) for value in hits.values()
    ):
        return False
    basis = evidence.get("basis") or []
    return isinstance(basis, list) and all(
        isinstance(code, str) and re.fullmatch(r"[a-z_]+:[a-z0-9_.:]+", code)
        for code in basis
    )


__all__ = [
    "CLASSIFICATION_LABEL",
    "CLASSIFICATIONS",
    "ColumnClassification",
    "HUMAN_TYPE_LABEL",
    "MAX_PATTERN_COLUMNS",
    "PATTERN_SAMPLE_ROWS",
    "PII_PATTERNS",
    "RULES_VERSION",
    "SEMANTIC_TYPES",
    "SENSITIVE_STATS",
    "UPPERCASE_PATTERNS",
    "cardinality",
    "cartridge_label",
    "classify_column",
    "column_description",
    "column_label",
    "dataset_description",
    "dataset_display_name",
    "evidence_is_counts_only",
    "format_count",
    "format_date",
    "human_type_label",
    "humanize_identifier",
    "is_scope_column",
    "key_class",
    "name_hints",
    "normalize_name",
    "relationship_confidence",
    "semantic_type",
    "source_display_name",
]
