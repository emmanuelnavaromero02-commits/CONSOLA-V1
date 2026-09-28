from __future__ import annotations

import logging
from typing import Any

from app.services import app_forge

logger = logging.getLogger(__name__)

LOCAL_SERVER_ID = "copilot_local"
GENERATE_APP_TOOL = "generar_app_analitica"

_MAX_NAME_CHARS = 80
_MAX_TEXT_CHARS = 600

# Small argument surface on purpose: the generated HTML never travels through
# copilot tool args; it stays server-side inside app_forge.
GENERATE_APP_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Slug opcional de la app (snake_case, <=80 chars).",
        },
        "title": {
            "type": "string",
            "description": "Título corto de la app (<=80 chars).",
        },
        "description": {
            "type": "string",
            "description": "Descripción breve opcional.",
        },
        "objetivo": {
            "type": "string",
            "description": "Objetivo de negocio que la app debe resolver.",
        },
        "datasets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Datasets gold autorizados (máximo 5 ids).",
        },
    },
    "required": ["objetivo", "datasets"],
    "additionalProperties": False,
}

_ALLOWED_KEYS = frozenset(GENERATE_APP_INPUT_SCHEMA["properties"])


def local_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": GENERATE_APP_TOOL,
            "description": (
                "Genera y publica una aplicación analítica HTML para este "
                "workspace a partir de un objetivo y hasta 5 datasets gold. "
                "El HTML se genera y publica del lado del servidor; el "
                "resultado incluye la URL para abrir la app."
            ),
            "input_schema": GENERATE_APP_INPUT_SCHEMA,
        }
    ]


def is_local_tool(server_id: str, tool: str) -> bool:
    return str(server_id) == LOCAL_SERVER_ID and str(tool) == GENERATE_APP_TOOL


def validate_generate_app_args(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ValueError("los argumentos deben ser un objeto JSON")
    unknown = set(args) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"argumentos no permitidos: {', '.join(sorted(str(k) for k in unknown))}"
        )
    for key in ("name", "title"):
        value = args.get(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > _MAX_NAME_CHARS:
                raise ValueError(f"'{key}' debe ser un string de máximo 80 caracteres")
    for key in ("description", "objetivo"):
        value = args.get(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > _MAX_TEXT_CHARS:
                raise ValueError(f"'{key}' debe ser un string de máximo 600 caracteres")
    if not str(args.get("objetivo") or "").strip():
        raise ValueError("'objetivo' es obligatorio")
    datasets = args.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("'datasets' debe ser una lista con al menos un dataset")
    if len(datasets) > app_forge.MAX_DATASETS:
        raise ValueError(f"'datasets' admite máximo {app_forge.MAX_DATASETS} ids")
    for item in datasets:
        if not isinstance(item, str) or len(item) > _MAX_NAME_CHARS:
            raise ValueError("cada dataset debe ser un string de máximo 80 caracteres")
    return args


async def invoke_local_tool(
    tool: str, args: dict[str, Any], *, user: dict | None
) -> dict[str, Any]:
    if str(tool) != GENERATE_APP_TOOL:
        return {"error": f"local tool '{tool}' is not registered"}
    try:
        clean = validate_generate_app_args(args or {})
    except ValueError as exc:
        return {"error": f"tool_args_rejected: {exc}"}
    try:
        result = await app_forge.generate_and_publish_app(
            user or {},
            name=clean.get("name"),
            title=clean.get("title"),
            description=str(clean.get("description") or ""),
            objective=str(clean.get("objetivo") or ""),
            datasets=list(clean.get("datasets") or []),
        )
    except app_forge.AppForgeError as exc:
        return {"error": str(exc)}
    return {
        "tool": GENERATE_APP_TOOL,
        "published": True,
        "name": result["name"],
        "title": result["title"],
        "url": result["url"],
        "app_url": result["url"],
        "datasets": result["datasets"],
        "message": (
            f"Aplicación '{result['title']}' publicada. Ábrela en {result['url']}."
        ),
    }
