"""
LLM SQL generation — Claude genera SQL DuckDB dado descripción + esquemas de fuentes.
"""
from __future__ import annotations

import json
import os
import re
import anthropic

SQL_MODEL = os.environ.get("SQL_LLM_MODEL", "claude-sonnet-4-6")

_client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
_SQL_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_SQL_FORBIDDEN_RE = re.compile(
    r"\b(attach|call|copy|create|delete|drop|export|import|insert|install|load|pragma|set|truncate|update|alter)\b",
    re.IGNORECASE,
)
_SQL_COMMENT_RE = re.compile(r"(--|/\*)")
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)


class GeneratedSQLValidationError(ValueError):
    """Raised when the LLM does not return strict, safe SQL JSON."""


def _mask_single_quoted(sql: str) -> str:
    return _SINGLE_QUOTED_RE.sub("''", sql or "")


_VALID_LAYERS = {"silver", "gold"}


def _normalise_layer(layer: str | None) -> str:
    value = str(layer or "silver").strip().lower()
    if value not in _VALID_LAYERS:
        raise GeneratedSQLValidationError("layer must be one of: silver, gold")
    return value


def validate_generated_sql(sql: str, layer: str = "silver") -> str:
    layer = _normalise_layer(layer)
    sql = (sql or "").strip()
    if not sql:
        raise GeneratedSQLValidationError("LLM SQL response is empty")
    masked = _mask_single_quoted(sql)
    if not _SQL_START_RE.search(masked):
        raise GeneratedSQLValidationError("LLM SQL must start with SELECT or WITH")
    if ";" in masked or _SQL_COMMENT_RE.search(masked) or _SQL_FORBIDDEN_RE.search(masked):
        raise GeneratedSQLValidationError("LLM SQL contains unsafe statements, comments, or multiple statements")
    if layer == "silver" and "read_parquet" not in masked.lower():
        raise GeneratedSQLValidationError("LLM SQL must read parquet sources explicitly")
    if layer == "silver" and ("load_date" not in masked.lower() or "{latest_date}" not in sql):
        raise GeneratedSQLValidationError("LLM SQL must filter the latest load_date using {latest_date}")
    return sql

SYSTEM_SILVER = """Eres un experto en SQL para DuckDB y arquitecturas lakehouse.
Generas consultas SQL limpias, eficientes y correctas para DuckDB.
Las fuentes están en MinIO accesibles vía read_parquet('s3://...', hive_partitioning=true, union_by_name=true).

## REGLA CRÍTICA — Partición latest
Los archivos Bronze tienen columnas de partición Hive: load_date y batch_id.
Para capa Silver, SIEMPRE filtra a la partición más reciente usando el placeholder {latest_date}:
  WHERE load_date = '{latest_date}'
El engine sustituye {latest_date} automáticamente en runtime con el load_date más reciente.
Nunca uses fechas hardcodeadas. Nunca omitas el filtro load_date en Silver.

Devuelve SOLO JSON con campos: sql (string), explanation (string en español).
No incluyas markdown ni explicaciones fuera del JSON."""

SYSTEM_GOLD = """Eres un experto en SQL analítico para DuckDB/Postgres Gold.
Generas transformaciones Gold a partir de datasets Silver registrados,
tablas pggold permitidas o fuentes que el motor ya expone al contexto.

Reglas:
- Devuelve SOLO una consulta SELECT o WITH.
- Lee datasets Silver registrados mediante read_parquet('s3://lakehouse/silver/<cartridge>/<dataset>/data.parquet') solo si aparecen en las fuentes disponibles.
- Lee tablas Gold registradas como pggold.gold_<dataset>.
- NO uses DDL/DML, comentarios, punto y coma ni múltiples statements.
- NO es obligatorio usar read_parquet ni el placeholder {latest_date}; esas reglas son sólo de Silver.

Devuelve SOLO JSON con campos: sql (string), explanation (string en español).
No incluyas markdown ni explicaciones fuera del JSON."""

SYSTEM_BY_LAYER = {
    "silver": SYSTEM_SILVER,
    "gold": SYSTEM_GOLD,
}


async def generate_sql(description: str, schemas: dict[str, dict], layer: str = "silver") -> tuple[str, str]:
    layer = _normalise_layer(layer)
    schema_text = "\n".join(
        f"Fuente: {src}\n"
        f"  Ruta/tabla: {('s3://lakehouse/' + src + '/**/*.parquet') if layer == 'silver' and str(src).startswith('raw/') else src}\n"
        f"  Campos: {', '.join(f['name'] + ':' + f['type'] for f in s.get('fields', []))}"
        for src, s in schemas.items()
    )

    layer_hint = (
        "Genera el SQL DuckDB para esta transformación Silver. "
        "Recuerda: usa WHERE load_date = '{latest_date}' para filtrar solo la última extracción."
        if layer == "silver"
        else "Genera el SQL para esta transformación GOLD usando rutas Silver registradas declaradas en fuentes "
        "o tablas pggold.gold_<dataset>. No añadas filtros Bronze de load_date salvo que el usuario lo pida explícitamente."
    )

    prompt = f"""Descripción del dataset requerido:
{description}

Esquemas disponibles:
{schema_text}

{layer_hint}"""

    resp = await _client.messages.create(
        model=SQL_MODEL,
        max_tokens=2048,
        system=SYSTEM_BY_LAYER[layer],
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeneratedSQLValidationError(f"LLM SQL response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GeneratedSQLValidationError("LLM SQL response must be a JSON object")
    sql = data.get("sql")
    explanation = data.get("explanation", "")
    if not isinstance(sql, str):
        raise GeneratedSQLValidationError("LLM SQL JSON field 'sql' must be a string")
    if not isinstance(explanation, str):
        explanation = ""
    return validate_generated_sql(sql, layer=layer), explanation
