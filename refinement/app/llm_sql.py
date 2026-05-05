"""
LLM SQL generation — Claude genera SQL DuckDB dado descripción + esquemas de fuentes.
"""
from __future__ import annotations

import os
import anthropic

SQL_MODEL = os.environ.get("SQL_LLM_MODEL", "claude-sonnet-4-6")

_client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

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

    import json
    text = resp.content[0].text.strip()
    try:
        data = json.loads(text)
        return data.get("sql", ""), data.get("explanation", "")
    except Exception:
        return text, ""
