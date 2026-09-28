from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from app.domains.apps.embed import datasets_from_app_html
from app.domains.apps.manifests import APP_NAME_RE
from app.domains.apps.payloads import DATASET_NAME_RE
from app.services import app_publication, llm_client, mcp_registry, permissions
from app.services.app_html_prompt import (
    build_app_html_system_prompt,
    build_app_html_user_prompt,
)

logger = logging.getLogger(__name__)

REFINEMENT_SERVER_ID = "refinement"
MAX_NAME_CHARS = 80
MAX_DATASETS = 5
MIN_HTML_CHARS = 200

# `/` is a valid attribute separator in HTML (<script/src=...>); CSP remains
# the enforcement boundary, this validator is the honest first gate.
_EXTERNAL_SCRIPT_RE = re.compile(r"<script[^>]*[\s/]src\s*=", re.IGNORECASE)
_DYNAMIC_IMPORT_RE = re.compile(r"\bimport\s*\(")
_FENCE_RE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.IGNORECASE)


class AppForgeError(ValueError):
    """A copilot app generation request could not be completed."""


def _clean_html(raw: str) -> str:
    return _FENCE_RE.sub("", str(raw or "")).strip()


def _validate_generated_html(html: str, datasets: list[str]) -> None:
    if len(html) < MIN_HTML_CHARS:
        raise AppForgeError(
            "La generación devolvió un HTML demasiado corto; no se publicó nada."
        )
    if _EXTERNAL_SCRIPT_RE.search(html):
        raise AppForgeError(
            "La app generada referencia scripts externos y fue rechazada; "
            "no se publicó nada."
        )
    if _DYNAMIC_IMPORT_RE.search(html):
        raise AppForgeError(
            "La app generada usa import() dinámico y fue rechazada; "
            "no se publicó nada."
        )
    referenced = set(datasets_from_app_html(html))
    extra = referenced - set(datasets)
    if extra:
        raise AppForgeError(
            "La app generada referencia datasets no autorizados "
            f"({', '.join(sorted(extra))}); no se publicó nada."
        )


def _validated_inputs(
    *, name: str | None, title: str | None, objective: str, datasets: list[str]
) -> tuple[str, str, list[str]]:
    clean_datasets = [str(item or "").strip() for item in (datasets or [])]
    clean_datasets = [item for item in clean_datasets if item]
    if not clean_datasets:
        raise AppForgeError("Se requiere al menos un dataset autorizado.")
    if len(clean_datasets) > MAX_DATASETS:
        raise AppForgeError(f"Máximo {MAX_DATASETS} datasets por app.")
    for item in clean_datasets:
        if len(item) > MAX_NAME_CHARS or not DATASET_NAME_RE.fullmatch(item):
            raise AppForgeError(f"Nombre de dataset inválido: {item[:80]}")
    if not str(objective or "").strip():
        raise AppForgeError("Se requiere el objetivo de negocio de la app.")

    app_name = str(name or "").strip()
    if not app_name:
        base = str(title or objective or "").strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:MAX_NAME_CHARS]
        app_name = slug or "app"
        if not APP_NAME_RE.fullmatch(app_name):
            app_name = f"app_{app_name}"
    if len(app_name) > MAX_NAME_CHARS or not APP_NAME_RE.fullmatch(app_name):
        raise AppForgeError("El nombre de la app es inválido (usa slug snake_case).")
    app_title = str(title or "").strip()[:MAX_NAME_CHARS] or app_name.replace("_", " ")
    return app_name, app_title, sorted(dict.fromkeys(clean_datasets))


async def _dataset_schemas(user: dict, datasets: list[str]) -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    for dataset in datasets:
        try:
            result = await mcp_registry.invoke(
                REFINEMENT_SERVER_ID, "get_schema", {"name": dataset}, user=user
            )
        except Exception as exc:  # noqa: BLE001
            raise AppForgeError(
                f"No se pudo leer el esquema del dataset '{dataset}'."
            ) from exc
        if isinstance(result, dict) and result.get("error"):
            raise AppForgeError(
                f"El dataset '{dataset}' no está disponible en este workspace."
            )
        schemas[dataset] = result
    return schemas


async def generate_and_publish_app(
    user: dict,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str = "",
    objective: str,
    datasets: list[str],
) -> dict[str, Any]:
    if not permissions.has_permission(user or {}, "apps.write"):
        raise AppForgeError(
            "Tu usuario no tiene el permiso 'apps.write' necesario para "
            "publicar aplicaciones en este workspace."
        )
    tenant_id, workspace_id = app_publication.scope_from_user(user)
    if not tenant_id or not workspace_id:
        raise AppForgeError("Se requiere un workspace activo para crear la app.")

    app_name, app_title, clean_datasets = _validated_inputs(
        name=name, title=title, objective=objective, datasets=datasets
    )
    schemas = await _dataset_schemas(user or {}, clean_datasets)

    try:
        reply, _viewer_urls, _msgs = await llm_client.chat(
            system=build_app_html_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": build_app_html_user_prompt(
                        title=app_title,
                        description=str(description or ""),
                        objective=str(objective),
                        dataset_schemas=schemas,
                    ),
                }
            ],
            tools=[],
            invoke_tool=lambda *_args, **_kwargs: {},
            tool_server_map={},
            user_context=user,
        )
    except (llm_client.LLMConfigurationError, llm_client.LLMProviderError) as exc:
        raise AppForgeError(
            f"El proveedor LLM no pudo generar la app: {exc}. No se publicó nada."
        ) from exc

    html = _clean_html(reply)
    _validate_generated_html(html, clean_datasets)

    # HTML travels server-to-server only; it never enters copilot tool args.
    publish_result = await mcp_registry.invoke(
        REFINEMENT_SERVER_ID,
        "publish_app",
        {
            "name": app_name,
            "title": app_title,
            "html": html,
            "description": str(description or ""),
            "visibility": "shared",
        },
        user=user,
    )
    if not isinstance(publish_result, dict) or not publish_result.get("published"):
        detail = ""
        if isinstance(publish_result, dict):
            detail = str(publish_result.get("error") or "")
        raise AppForgeError(
            f"La publicación de la app falló: {detail or 'sin detalle'}."
        )

    await app_publication.register_workspace_app(
        (tenant_id, workspace_id), app_name, html, clean_datasets
    )
    url = f"/analytics/viewer?app={quote(app_name, safe='')}"
    logger.info("[app-forge] published workspace app %s", app_name)
    return {
        "name": app_name,
        "title": app_title,
        "url": url,
        "datasets": clean_datasets,
    }
