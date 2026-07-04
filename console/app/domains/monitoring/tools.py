from __future__ import annotations

from typing import Any


def build_monitoring_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "view_job",
            "description": (
                "Genera un deeplink para visualizar el detalle de un job: "
                "status, progreso, logs linea a linea por entidad. "
                "Retorna una URL que el usuario puede abrir directamente."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "ID del job"},
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "view_jobs",
            "description": (
                "Genera un deeplink para ver todos los jobs recientes con su estado."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "view_schema",
            "description": (
                "Genera un deeplink para visualizar el schema de una fuente Bronze: "
                "columnas, tipos, particiones disponibles y preview de filas."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Ruta de la fuente, e.g. 'raw/replicon/TimeEntry'",
                    },
                },
                "required": ["source"],
            },
        },
        {
            "name": "view_dataset",
            "description": (
                "Genera un deeplink para visualizar un dataset Silver/Gold: "
                "SQL, column mapping (terminos de negocio), lineage e historial y preview."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del dataset"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "view_datasets",
            "description": (
                "Genera un deeplink para ver todos los datasets Silver/Gold definidos."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "view_semantic",
            "description": (
                "Genera un deeplink para visualizar el modelo semantico de un cartucho: "
                "entidades, campos, modos de extraccion, watermarks y relaciones."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge": {"type": "string", "default": "replicon"},
                },
            },
        },
        {
            "name": "view_pipeline",
            "description": (
                "Genera un deeplink para el Pipeline Monitor DAG: vista completa del flujo "
                "Entidad -> Bronze -> Silver -> Gold con estado de frescura y botones de extracción. "
                "Úsalo cuando el usuario pregunte por el estado del pipeline, quiera ver qué "
                "está desactualizado, o quiera extraer/refrescar datos."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
    ]
