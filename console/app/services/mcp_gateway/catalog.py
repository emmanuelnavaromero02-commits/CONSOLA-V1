from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import cached_property
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SCOPE_READ = "lectura"
SCOPE_ACTIONS = "acciones"
MAX_DESCRIPTION_CHARS = 280
TableName = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsultarContextoArgs(_Args):
    pass


class ConsultarSaludPipelinesArgs(_Args):
    fuente: str = Field(
        default="",
        max_length=60,
        description="Nombre de la fuente de datos para filtrar; vacío para todas.",
    )
    solo_con_problemas: bool = Field(
        default=False, description="Mostrar solo automatizaciones con problemas."
    )


class ConsultarControlRoomResumenArgs(_Args):
    pass


class ConsultarMatrizTalento9boxArgs(_Args):
    collar: Literal["confianza", "sindicalizado"] = Field(
        default="confianza", description="Segmento de personal a consultar."
    )


class ConsultarKpisSapB1Args(_Args):
    area: Literal["finanzas", "ventas", "compras", "aprendizaje", "semaforo"] = Field(
        description="Área de indicadores de SAP Business One."
    )
    top_n: int = Field(
        default=5, ge=0, le=10, description="Cantidad máxima de filas destacadas por indicador."
    )


class BuscarDocumentosEmpresaArgs(_Args):
    consulta: str = Field(
        min_length=3, max_length=500, description="Pregunta o texto a buscar."
    )
    max_resultados: int = Field(default=5, ge=1, le=10, description="Fragmentos a devolver.")


class ListarTablasDisponiblesArgs(_Args):
    fuente: str = Field(
        default="",
        max_length=60,
        description="Nombre de la fuente de datos para filtrar; vacío para todas.",
    )
    capa: Literal["gold", "silver", "todas"] = Field(
        default="gold", description="Capa de las tablas a listar."
    )


class ConsultarExtraccionArgs(_Args):
    fuente: str = Field(min_length=1, max_length=60, description="Nombre de la fuente de datos.")
    ejecucion_id: str = Field(
        pattern=r"^[A-Za-z0-9_.:-]{1,128}$",
        description="Identificador devuelto al iniciar la extracción.",
    )


class EjecutarExtraccionArgs(_Args):
    fuente: str = Field(min_length=1, max_length=60, description="Nombre de la fuente de datos.")
    clave_idempotencia: str = Field(
        default="",
        pattern=r"^[A-Za-z0-9_-]{0,64}$",
        description="Clave opcional para no repetir la misma extracción al reintentar.",
    )


class CrearAppAnaliticaArgs(_Args):
    nombre: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_]{2,79}$",
        description="Identificador de la aplicación en minúsculas con guiones bajos.",
    )
    objetivo: str = Field(
        min_length=10, max_length=600, description="Objetivo de negocio que resuelve la aplicación."
    )
    tablas: list[TableName] = Field(
        min_length=1,
        max_length=5,
        description="Entre una y cinco tablas disponibles que usará la aplicación.",
    )
    descripcion: str = Field(default="", max_length=600, description="Descripción opcional.")


@dataclass(frozen=True)
class GatewayAction:
    name: str
    title: str
    description: str
    scope: Literal["lectura", "acciones"]
    permission: str | None
    args_model: type[BaseModel]
    underlying: tuple[str, str] | None
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    long_running: bool = False
    timeout_seconds: float = 45.0
    required_source: str | None = None
    extra_permissions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def consequential(self) -> bool:
        return not self.read_only

    @property
    def mcp_annotations(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": False,
        }

    @cached_property
    def input_schema(self) -> dict[str, Any]:
        return strip_titles(self.args_model.model_json_schema())

    def schema_copy(self) -> dict[str, Any]:
        return copy.deepcopy(self.input_schema)


def strip_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        cleaned: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "title" and isinstance(value, str):
                continue
            if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
                cleaned[key] = {name: strip_titles(item) for name, item in value.items()}
            else:
                cleaned[key] = strip_titles(value)
        return cleaned
    if isinstance(schema, list):
        return [strip_titles(item) for item in schema]
    return schema


ACTIONS: tuple[GatewayAction, ...] = (
    GatewayAction(
        name="consultar_contexto",
        title="Consultar contexto",
        description=(
            "Devuelve el espacio de trabajo al que está ligado el token, su alcance, su "
            "vencimiento y las fuentes de datos habilitadas. Úsala primero para saber qué "
            "puedes consultar."
        ),
        scope=SCOPE_READ,
        permission=None,
        args_model=ConsultarContextoArgs,
        underlying=None,
        timeout_seconds=15.0,
    ),
    GatewayAction(
        name="consultar_salud_pipelines",
        title="Consultar salud de automatizaciones",
        description=(
            "Lista las automatizaciones de extracción del espacio de trabajo con su estado, "
            "frecuencia y última corrida. Permite filtrar por fuente o ver solo las que "
            "tienen problemas."
        ),
        scope=SCOPE_READ,
        permission="pipelines.read",
        args_model=ConsultarSaludPipelinesArgs,
        underlying=("GET", "/api/pipelines/automations"),
    ),
    GatewayAction(
        name="consultar_control_room_resumen",
        title="Consultar resumen del Control Room",
        description=(
            "Resumen agregado del Control Room: anomalías por severidad y dominio, decisiones "
            "abiertas, alertas, fuentes con datos y cifras financieras disponibles."
        ),
        scope=SCOPE_READ,
        permission="datasets.read",
        args_model=ConsultarControlRoomResumenArgs,
        underlying=("GET", "/api/control-room/summary"),
    ),
    GatewayAction(
        name="consultar_matriz_talento_9box",
        title="Consultar matriz de talento 9-box",
        description=(
            "Matriz 9-box agregada del segmento de confianza (conteos por cuadrante, sin "
            "listas de personas). El segmento sindicalizado informa las fuentes que faltan "
            "y solo la cobertura global de certificaciones."
        ),
        scope=SCOPE_READ,
        permission="datasets.read",
        args_model=ConsultarMatrizTalento9boxArgs,
        underlying=("GET", "/api/control-room/sap-successfactors/talent/9box"),
        required_source="sap_successfactors",
    ),
    GatewayAction(
        name="consultar_kpis_sap_b1",
        title="Consultar indicadores de SAP Business One",
        description=(
            "Indicadores agregados de SAP Business One por área: finanzas, ventas (incluye "
            "caducidad de lotes), compras, aprendizaje o semáforo general."
        ),
        scope=SCOPE_READ,
        permission="datasets.read",
        args_model=ConsultarKpisSapB1Args,
        underlying=("GET", "/api/control-room/sap-b1/views/{view}"),
        required_source="sap_b1",
    ),
    GatewayAction(
        name="buscar_documentos_empresa",
        title="Buscar en documentos de la empresa",
        description=(
            "Busca fragmentos relevantes en los documentos que la empresa cargó en la "
            "consola y devuelve el nombre del documento con un extracto breve."
        ),
        scope=SCOPE_READ,
        permission="datasets.read",
        args_model=BuscarDocumentosEmpresaArgs,
        underlying=("POST", "/api/rag/search"),
    ),
    GatewayAction(
        name="listar_tablas_disponibles",
        title="Listar tablas disponibles",
        description=(
            "Lista hasta 100 tablas publicadas del espacio de trabajo con su capa, fuente y "
            "descripción. Útil antes de crear una aplicación analítica."
        ),
        scope=SCOPE_READ,
        permission="datasets.read",
        args_model=ListarTablasDisponiblesArgs,
        underlying=("GET", "/api/datasets"),
    ),
    GatewayAction(
        name="consultar_extraccion",
        title="Consultar una extracción",
        description=(
            "Consulta el avance y resultado de una extracción iniciada antes, usando la fuente "
            "y el identificador de ejecución que devolvió ejecutar_extraccion."
        ),
        scope=SCOPE_READ,
        permission="pipelines.read",
        args_model=ConsultarExtraccionArgs,
        underlying=("GET", "/api/cartridges/{cartridge_id}/sync-runs/{run_id}"),
    ),
    GatewayAction(
        name="ejecutar_extraccion",
        title="Ejecutar extracción incremental",
        description=(
            "Inicia una extracción incremental de una fuente de datos. Si ya hay una en curso, "
            "informa su identificador en lugar de iniciar otra. Consulta el avance con "
            "consultar_extraccion."
        ),
        scope=SCOPE_ACTIONS,
        permission="pipelines.run",
        args_model=EjecutarExtraccionArgs,
        underlying=("POST", "/api/cartridges/{cartridge_id}/sync-now"),
        read_only=False,
        idempotent=True,
        long_running=True,
        timeout_seconds=120.0,
    ),
    GatewayAction(
        name="crear_app_analitica",
        title="Crear aplicación analítica",
        description=(
            "Genera y publica una aplicación analítica en el espacio de trabajo a partir de "
            "un objetivo y de una a cinco tablas disponibles. Falla si el nombre ya existe."
        ),
        scope=SCOPE_ACTIONS,
        permission="apps.write",
        args_model=CrearAppAnaliticaArgs,
        underlying=None,
        read_only=False,
        idempotent=False,
        long_running=True,
        timeout_seconds=280.0,
    ),
)
CATALOG: dict[str, GatewayAction] = {action.name: action for action in ACTIONS}
LONG_RUNNING_ACTIONS = frozenset(action.name for action in ACTIONS if action.long_running)


def get_action(name: str) -> GatewayAction | None:
    return CATALOG.get(str(name or ""))


def token_scopes(user: dict[str, Any] | None) -> frozenset[str]:
    return frozenset(str(item) for item in (user or {}).get("access_token_scopes") or [])


def scope_allows(action: GatewayAction, scopes: frozenset[str]) -> bool:
    if action.scope == SCOPE_READ:
        return SCOPE_READ in scopes or SCOPE_ACTIONS in scopes
    return SCOPE_ACTIONS in scopes


def permission_allows(action: GatewayAction, user: dict[str, Any] | None) -> bool:
    from app.services import permissions

    return action.permission is None or permissions.has_permission(user, action.permission)


def source_allows(action: GatewayAction, user: dict[str, Any] | None) -> bool:
    from app.services.control_room.business_cartridge_scope import business_cartridge_allowed

    return action.required_source is None or business_cartridge_allowed(user, action.required_source)


def visible_actions(user: dict[str, Any] | None) -> list[GatewayAction]:
    scopes = token_scopes(user)
    return [
        action
        for action in ACTIONS
        if scope_allows(action, scopes)
        and permission_allows(action, user)
        and source_allows(action, user)
    ]


__all__ = (
    "ACTIONS",
    "CATALOG",
    "GatewayAction",
    "LONG_RUNNING_ACTIONS",
    "MAX_DESCRIPTION_CHARS",
    "SCOPE_ACTIONS",
    "SCOPE_READ",
    "get_action",
    "permission_allows",
    "scope_allows",
    "source_allows",
    "strip_titles",
    "token_scopes",
    "visible_actions",
)
