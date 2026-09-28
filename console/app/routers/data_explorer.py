from __future__ import annotations

import asyncio
import json
import math
from typing import Annotated, Any, Literal, Union

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

from app.domains.data_platform.explorer_nl import (
    ExplorerNlError,
    NL_QUESTION_MAX_LEN,
    build_spec_from_question,
    logical_source_mapping,
    spec_to_body,
)
from app.domains.data_platform.explorer_query import (
    BRONZE_LIMIT_CAP,
    DATASET_LIMIT_CAP,
    ExplorerColumn,
    ExplorerQueryError,
    ExplorerSource,
    bronze_definition_relation,
    bronze_execute_relation,
    compile_explorer_query,
    dataset_relation,
    normalize_explorer_schema,
    parse_explorer_source,
    parse_explorer_spec,
    sanitize_explorer_detail,
)
from app.domains.data_platform.refinement_errors import (
    raise_for_refinement_payload_error,
    upstream_error_detail,
)
from app.domains.data_platform.refinement_invoke import refinement_invoke
from app.domains.data_platform.scoped_reads import (
    rewrite_bronze_logical_paths,
    scoped_read_cache_get,
    scoped_read_cache_set,
    workspace_scope_from_user,
)
from app.domains.data_platform.source_visibility import require_technical_source_access
from app.domains.security.internal_auth import internal_outbound_headers
from app.middleware.request_id import request_id_var
from app.security import get_internal_api_key
from app.services import llm_client
from app.services.csrf import require_csrf
from app.services.mcp_payloads import mcp_payload
from app.services.permissions import has_permission, require_permission
from app.services.security_context import rls_user_context
from app.services.service_urls import is_production_env


router = APIRouter(tags=["Data explorer"])

Cell = Union[StrictBool, StrictInt, StrictFloat, StrictStr, None]
SCHEMA_CACHE_NAMESPACE = "explorer-schema"


class ExplorerColumnOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    kind: Literal["text", "number", "temporal", "boolean", "other"]


class ExplorerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_kind: Literal["bronze", "dataset"]
    available_columns: list[ExplorerColumnOut]
    executed: bool
    columns: list[str]
    rows: list[list[Cell]]
    row_count: int
    limit: int | None
    truncated: bool
    sql_display: str
    sql_definition: str | None
    sources: list[str]


def _hdr_for(server: str) -> dict[str, str]:
    return internal_outbound_headers(
        server,
        internal_api_key=get_internal_api_key(),
        is_production=is_production_env(),
        request_id=request_id_var.get(),
    )


def _friendly_upstream_error(status_code: int, detail: Any, fallback: str) -> tuple[int, str]:
    raw = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    lower = raw.lower()
    if "source_files_missing" in lower or "no files found" in lower:
        return 404, "Esta fuente de datos todavía no tiene archivos cargados. Ejecuta primero la extracción."
    if "exceeded timeout" in lower or "interrupt" in lower:
        return 422, "La consulta superó el tiempo máximo. Agrega filtros más específicos y vuelve a intentar."
    if status_code == 503:
        return 503, "El motor de datos no está disponible en este momento. Intenta de nuevo en unos minutos."
    clean = sanitize_explorer_detail(raw, fallback=fallback)[:160]
    if status_code == 403:
        return 403, f"El motor de datos rechazó la consulta ({clean})."
    if status_code in (404, 502):
        return 422, f"{fallback}: {clean}" if clean != fallback else fallback
    if 400 <= status_code < 500:
        return status_code, f"{fallback}: {clean}" if clean != fallback else fallback
    return status_code, fallback


async def _invoke_refinement(
    tool: str, args: dict[str, Any], user: dict, *, timeout: int, fallback: str
) -> dict[str, Any]:
    try:
        payload = await refinement_invoke(
            tool,
            args,
            timeout=timeout,
            user=user,
            httpx_module=httpx,
            hdr_for=_hdr_for,
            mcp_payload=mcp_payload,
            upstream_error_detail=upstream_error_detail,
            raise_for_refinement_payload_error=raise_for_refinement_payload_error,
        )
    except HTTPException as exc:
        status_code, message = _friendly_upstream_error(exc.status_code, exc.detail, fallback)
        raise HTTPException(status_code, message) from None
    if not isinstance(payload, dict):
        raise HTTPException(502, fallback)
    return payload


async def _load_schema(source: ExplorerSource, user: dict) -> tuple[ExplorerColumn, ...]:
    cached = scoped_read_cache_get(SCHEMA_CACHE_NAMESPACE, user, source.logical)
    if cached:
        return tuple(ExplorerColumn(**column) for column in cached)
    schema = await _fetch_schema(source, user)
    scoped_read_cache_set(
        SCHEMA_CACHE_NAMESPACE,
        user,
        [{"name": column.name, "type": column.type, "kind": column.kind} for column in schema],
        source.logical,
    )
    return schema


async def _fetch_schema(source: ExplorerSource, user: dict) -> tuple[ExplorerColumn, ...]:
    if source.kind == "bronze":
        payload = await _invoke_refinement(
            "describe_source",
            {"source": source.logical, "schema_only": True},
            user,
            timeout=60,
            fallback="No se pudo leer el esquema de la fuente",
        )
        fields = payload.get("fields")
    else:
        payload = await _invoke_refinement(
            "preview_transform",
            {
                "sql": f"SELECT * FROM {dataset_relation(source)} WHERE 1 = 0",
                "limit": 1,
                "user_context": rls_user_context(user),
            },
            user,
            timeout=60,
            fallback="No se pudo leer el esquema del dataset",
        )
        fields = payload.get("schema")
    schema = normalize_explorer_schema(fields)
    if not schema:
        raise ExplorerQueryError("La fuente no tiene columnas legibles todavía.", status_code=404)
    return schema


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _authorize_source(source: ExplorerSource, user: dict) -> int:
    if source.kind == "dataset":
        return DATASET_LIMIT_CAP
    if not has_permission(user, "datasets.write"):
        raise HTTPException(403, "permission required: datasets.write")
    require_technical_source_access(user, source.logical)
    workspace_scope_from_user(user)
    return BRONZE_LIMIT_CAP


async def explore_payload(body: Any, user: dict) -> ExplorerResponse:
    if not isinstance(body, dict):
        raise ExplorerQueryError("El cuerpo de la consulta debe ser un objeto.")
    source = parse_explorer_source(body.get("source"))
    execute = body.get("execute", True)
    if not isinstance(execute, bool):
        raise ExplorerQueryError("'execute' debe ser verdadero o falso.")
    limit_cap = _authorize_source(source, user)
    spec = parse_explorer_spec(body, limit_cap=limit_cap)
    schema = await _load_schema(source, user)

    bronze = source.kind == "bronze"
    if bronze:
        relation = bronze_execute_relation(source) if execute else bronze_definition_relation(source)
    else:
        relation = dataset_relation(source)
    query = compile_explorer_query(
        spec,
        schema,
        relation_sql=relation,
        limit_cap=limit_cap,
        mode="execute" if execute else "definition",
        allow_latest_only=bronze,
    )
    sources = [source.logical] if bronze else []
    available = [
        ExplorerColumnOut(name=column.name, type=column.type, kind=column.kind)
        for column in schema
    ]
    if not execute:
        return ExplorerResponse(
            source=source.logical,
            source_kind=source.kind,
            available_columns=available,
            executed=False,
            columns=[],
            rows=[],
            row_count=0,
            limit=None,
            truncated=False,
            sql_display=query.sql_display,
            sql_definition=query.sql,
            sources=sources,
        )

    try:
        sql_definition: str | None = compile_explorer_query(
            spec,
            schema,
            relation_sql=bronze_definition_relation(source) if bronze else relation,
            limit_cap=limit_cap,
            mode="definition",
            allow_latest_only=bronze,
        ).sql
    except ExplorerQueryError:
        sql_definition = None

    sql = rewrite_bronze_logical_paths(query.sql, user) if bronze else query.sql
    args: dict[str, Any] = {
        "sql": sql,
        "params": list(query.params),
        "limit": query.limit,
        "user_context": rls_user_context(user),
    }
    if bronze:
        args["sources"] = sources
    payload = await _invoke_refinement(
        "preview_transform", args, user, timeout=120, fallback="No se pudo ejecutar la consulta"
    )
    data = payload.get("data")
    rows_in = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    if spec.columns:
        columns = list(spec.columns)
    else:
        result_schema = normalize_explorer_schema(payload.get("schema"))
        columns = [column.name for column in result_schema] or [column.name for column in schema]
    limit = query.limit or 0
    rows = [[_cell(row.get(column)) for column in columns] for row in rows_in[:limit]]
    return ExplorerResponse(
        source=source.logical,
        source_kind=source.kind,
        available_columns=available,
        executed=True,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        limit=query.limit,
        truncated=len(rows) >= limit,
        sql_display=query.sql_display,
        sql_definition=sql_definition,
        sources=sources,
    )


@router.post(
    "/api/data/explore",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))],
    response_model=ExplorerResponse,
)
async def api_data_explore(
    body: dict, user: dict = Depends(require_permission("datasets.read"))
) -> ExplorerResponse:
    try:
        return await explore_payload(body, user)
    except ExplorerQueryError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


class ExplorerNlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, Field(min_length=1, max_length=300)]
    question: Annotated[str, Field(min_length=1, max_length=NL_QUESTION_MAX_LEN)]


class ExplorerNlFilterOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    op: str
    value: Cell = None
    values: list[Cell] | None = None


class ExplorerNlSortOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    direction: Literal["asc", "desc"]


class ExplorerNlSpecOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    filters: list[ExplorerNlFilterOut]
    sort: list[ExplorerNlSortOut]
    limit: int | None
    latest_only: bool


class ExplorerNlResponse(ExplorerResponse):
    spec: ExplorerNlSpecOut
    question: str


NL_LLM_TIMEOUT_SECONDS = 45.0
NL_UNAVAILABLE_DETAIL = "Sin conexión al asistente"


async def _nl_noop_invoke_tool(*_args: Any, **_kwargs: Any) -> dict:
    return {"error": "tools_disabled_in_this_context"}


async def _nl_llm_text(system: str, question: str, *, user: dict) -> str:
    try:
        reply, _viewer_urls, _msgs = await asyncio.wait_for(
            llm_client.chat(
                system=system,
                messages=[{"role": "user", "content": question}],
                tools=[],
                invoke_tool=_nl_noop_invoke_tool,
                tool_server_map={},
                on_event=None,
                user_context=user,
            ),
            timeout=NL_LLM_TIMEOUT_SECONDS,
        )
    except (llm_client.LLMConfigurationError, llm_client.LLMProviderError):
        raise HTTPException(503, NL_UNAVAILABLE_DETAIL) from None
    except asyncio.TimeoutError:
        raise HTTPException(504, "La consulta al asistente tardó demasiado.") from None
    return reply or ""


@router.post(
    "/api/data/explore/nl",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))],
    response_model=ExplorerNlResponse,
)
async def api_data_explore_nl(
    body: ExplorerNlRequest, user: dict = Depends(require_permission("datasets.read"))
) -> ExplorerNlResponse:
    try:
        source = parse_explorer_source(logical_source_mapping(body.source))
        limit_cap = _authorize_source(source, user)
        schema = await _load_schema(source, user)

        async def llm_text(system: str, question: str) -> str:
            return await _nl_llm_text(system, question, user=user)

        spec = await build_spec_from_question(
            schema, body.question, llm_text, limit_cap=limit_cap
        )
        spec_body = spec_to_body(spec)
        # Compile-only: the user reviews the spec in the builder and executes once.
        result = await explore_payload(
            {
                "source": logical_source_mapping(body.source),
                "execute": False,
                **spec_body,
            },
            user,
        )
    except ExplorerNlError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    except ExplorerQueryError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return ExplorerNlResponse(
        **result.model_dump(),
        spec=ExplorerNlSpecOut(**spec_body),
        question=body.question,
    )
