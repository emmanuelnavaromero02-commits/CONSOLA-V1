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


def validate_generated_sql(sql: str) -> str:
    sql = (sql or "").strip()
    if not sql:
        raise GeneratedSQLValidationError("LLM SQL response is empty")
    masked = _mask_single_quoted(sql)
    if not _SQL_START_RE.search(masked):
        raise GeneratedSQLValidationError("LLM SQL must start with SELECT or WITH")
    if ";" in masked or _SQL_COMMENT_RE.search(masked) or _SQL_FORBIDDEN_RE.search(masked):
        raise GeneratedSQLValidationError("LLM SQL contains unsafe statements, comments, or multiple statements")
    if "read_parquet" not in masked.lower():
        raise GeneratedSQLValidationError("LLM SQL must read parquet sources explicitly")
    if "load_date" not in masked.lower() or "{latest_date}" not in sql:
        raise GeneratedSQLValidationError("LLM SQL must filter the latest load_date using {latest_date}")
    return sql

SYSTEM = """Eres un experto en SQL para DuckDB y arquitecturas lakehouse.
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


async def generate_sql(description: str, schemas: dict[str, dict]) -> tuple[str, str]:
    schema_text = "\n".join(
        f"Fuente: {src}\n"
        f"  Ruta S3: s3://lakehouse/{src}/**/*.parquet\n"
        f"  Campos: {', '.join(f['name'] + ':' + f['type'] for f in s.get('fields', []))}"
        for src, s in schemas.items()
    )

    prompt = f"""Descripción del dataset requerido:
{description}

Esquemas disponibles:
{schema_text}

Genera el SQL DuckDB para esta transformación Silver.
Recuerda: usa WHERE load_date = '{{latest_date}}' para filtrar solo la última extracción."""

    resp = await _client.messages.create(
        model=SQL_MODEL,
        max_tokens=2048,
        system=SYSTEM,
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
    return validate_generated_sql(sql), explanation
