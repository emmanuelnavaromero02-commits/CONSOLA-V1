from __future__ import annotations

import hashlib
import json
import uuid
import logging
from typing import Any

import asyncpg

from app.services import audit_service, cartridge_service, permissions
from app.services.db_scope import scoped_db_for_user


class MarketplaceError(RuntimeError):
    pass


_SCHEMA_READY = False


ACTIVE_ENTITLEMENT_STATUS = "active"
logger = logging.getLogger(__name__)

READY_INSTALLATION_STATUS = "ready"
CUSTOMER_PRODUCT_STATUSES = {"active"}
GLOBAL_ADMIN_ROLES = {"admin", "owner", "super_admin"}


COMMERCIAL_PROFILES: dict[str, dict[str, Any]] = {
    "hubspot": {
        "headline": "CRM, pipeline, forecast y revenue comercial conectados a OMEGA.",
        "what_it_does": [
            "Extrae deals, compañías, contactos, líneas de producto, owners y pipelines desde HubSpot CRM.",
            "Modela pipeline ponderado, forecast mensual, revenue por vendedor y deals estancados.",
            "Publica agentes especializados para vigilar forecast y perseguir oportunidades sin actividad.",
        ],
        "data_domains": ["Deals", "Compañías", "Contactos", "Owners", "Pipelines", "Forecast"],
        "dashboards": [
            "Forecast mensual",
            "Pipeline ponderado",
            "Revenue por vendedor",
            "Deals estancados",
            "Conversión por etapa",
        ],
        "sample_questions": [
            "¿Qué deals ponen en riesgo el forecast del mes?",
            "¿Qué vendedores tienen más pipeline ponderado abierto?",
            "¿Qué oportunidades llevan más días sin actividad?",
        ],
        "requirements": ["HubSpot Private App token", "Scopes CRM de lectura", "Permisos para objetos deals/companies/contacts"],
        "plan": "CRM Intelligence",
        "price_label": "Cotización por workspace CRM",
    },
    "replicon": {
        "headline": "Servicios profesionales, rentabilidad, disponibilidad y P&L en un solo cartucho.",
        "what_it_does": [
            "Conecta proyectos, clientes, tareas, facturación, costos, time entries y asignaciones.",
            "Convierte datos operativos de Replicon en modelos silver/gold listos para análisis ejecutivo.",
            "Publica apps de margen, revenue manager, disponibilidad, skills y horas/costos.",
        ],
        "data_domains": ["Proyectos", "Timesheets", "Facturación", "Costos", "Asignaciones", "Skills"],
        "dashboards": [
            "P&L por Revenue Manager",
            "Disponibilidad de recursos",
            "Consultor: horas y costos",
            "Skill gaps por manager",
            "Match de consultores por skill",
        ],
        "sample_questions": [
            "¿Qué proyectos bajaron de margen este mes?",
            "¿Qué consultores están disponibles para una demanda nueva?",
            "¿Dónde hay horas facturables contra costo fuera de rango?",
        ],
        "requirements": ["Credenciales API Replicon", "Definición de moneda/costos", "Permisos de lectura"],
        "plan": "Business Analytics",
        "price_label": "Cotización anual por workspace",
    },
    "sap_hcm": {
        "headline": "Datos maestros y organizacionales de SAP HCM listos para reporting de personas.",
        "what_it_does": [
            "Extrae empleados, unidades organizacionales, posiciones, centros de costo, ausencias y acciones.",
            "Mantiene watermarks para cargas incrementales y modelos silver/gold por workspace.",
            "Prepara la base para reportes de headcount, estructura, movilidad y ausentismo.",
        ],
        "data_domains": ["Empleados", "Organización", "Posiciones", "Cost centers", "Ausencias"],
        "dashboards": ["Headcount", "Estructura organizacional", "Movimientos", "Ausencias"],
        "sample_questions": [
            "¿Cuántos empleados activos hay por unidad?",
            "¿Qué posiciones cambiaron en el último periodo?",
            "¿Dónde hay más ausencias por centro de costo?",
        ],
        "requirements": ["SAP OData HCM", "Basic Auth o gateway técnico", "IP allowlist si aplica"],
        "plan": "HR Data Core",
        "price_label": "Cotización por volumen de empleados",
    },
    "sap_successfactors": {
        "headline": "Employee Central conectado a analítica, copiloto y modelos de datos controlados.",
        "what_it_does": [
            "Conecta entidades OData v2 de SuccessFactors como User, EmpEmployment, EmpJob y Position.",
            "Organiza compensación, posición, departamentos, divisiones, ubicaciones y eventos.",
            "Permite preguntas de negocio sobre cambios, estructura y datos maestros de personal.",
        ],
        "data_domains": ["Employee Central", "Compensación", "Posiciones", "Departamentos", "Ubicaciones"],
        "dashboards": ["Movilidad", "Estructura", "Compensación", "Cambios organizacionales"],
        "sample_questions": [
            "¿Qué empleados cambiaron de departamento?",
            "¿Qué posiciones están vacantes?",
            "¿Cómo se distribuye la organización por ubicación?",
        ],
        "requirements": ["OAuth2 client credentials", "SF company id", "Permisos OData v2"],
        "plan": "HR Cloud Core",
        "price_label": "Cotización por tenant SF",
    },
    "sap_s4hana": {
        "headline": "ERP core: ventas, compras, contabilidad y socios de negocio conectados a ΩMEGA.",
        "what_it_does": [
            "Extrae business partners, ventas, compras, journal entries y documentos financieros.",
            "Entrega modelos analíticos para gestión financiera, clientes, proveedores y operación.",
            "Prepara datos S/4HANA para RAG semántico y reportes ejecutivos.",
        ],
        "data_domains": ["Business partners", "Ventas", "Compras", "Contabilidad", "Journal entries"],
        "dashboards": ["Finanzas", "Clientes", "Proveedores", "Compras", "Ventas"],
        "sample_questions": [
            "¿Qué clientes concentran más facturación?",
            "¿Qué proveedores subieron gasto frente al periodo anterior?",
            "¿Qué journal entries requieren revisión?",
        ],
        "requirements": ["SAP S/4HANA API", "Internal API key", "Conectividad desde Airflow"],
        "plan": "ERP Intelligence",
        "price_label": "Cotización por módulo",
    },
    "sap_b1": {
        "headline": "ERP PyME: finanzas, ventas, compras e inventario de SAP Business One conectados a ΩMEGA.",
        "what_it_does": [
            "Lee las tablas de cada compañía (socios de negocio, documentos de venta y compra, asientos, inventario) directamente de la base de datos.",
            "Entrega modelos analíticos por compañía para cartera, gasto, margen y existencias.",
            "Prepara datos Business One para RAG semántico y reportes ejecutivos.",
        ],
        "data_domains": ["Socios de negocio", "Ventas", "Compras", "Contabilidad", "Inventario"],
        "dashboards": ["Finanzas", "Clientes", "Proveedores", "Compras", "Inventario"],
        "sample_questions": [
            "¿Qué clientes concentran la cartera vencida por compañía?",
            "¿Qué proveedores subieron gasto frente al periodo anterior?",
            "¿Qué artículos llevan más de 90 días sin movimiento?",
        ],
        "requirements": ["Acceso SQL a la base HANA de Business One", "Internal API key", "Conectividad desde Airflow"],
        "plan": "ERP Intelligence",
        "price_label": "Cotización por compañía",
    },
}


def _scope(user: dict) -> tuple[str, str]:
    tenant_id = (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id")
    workspace_id = (user or {}).get("active_workspace_id") or (user or {}).get("workspace_id")
    if not tenant_id or not workspace_id:
        raise MarketplaceError("workspace context required")
    return str(tenant_id), str(workspace_id)


def _admin_scoped_user(
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    requested_tenant = str(tenant_id or "").strip()
    requested_workspace = str(workspace_id or "").strip()
    if not requested_tenant and not requested_workspace:
        return user
    if not requested_tenant or not requested_workspace:
        raise MarketplaceError("tenant_id and workspace_id are required together")

    workspaces = user.get("workspaces") or []
    match = next(
        (
            ws
            for ws in workspaces
            if str(ws.get("tenant_id") or "") == requested_tenant
            and str(ws.get("workspace_id") or "") == requested_workspace
        ),
        None,
    )
    if not match:
        raise MarketplaceError("workspace access forbidden")

    active_workspace = str(user.get("active_workspace_id") or user.get("workspace_id") or "")
    if requested_workspace != active_workspace and user.get("role") not in GLOBAL_ADMIN_ROLES:
        raise MarketplaceError("workspace access forbidden")

    scoped = dict(user)
    scoped.update(
        {
            "active_tenant_id": requested_tenant,
            "active_workspace_id": requested_workspace,
            "workspace_role": match.get("workspace_role") or user.get("workspace_role"),
        }
    )
    return scoped


def _can_admin_marketplace(user: dict | None) -> bool:
    # Enforced by the marketplace.admin permission, not a hardcoded global
    # role. owner/super_admin/admin already carry it via ROLE_PERMISSIONS;
    # a custom role granted marketplace.admin is honored too.
    return permissions.has_permission(user, "marketplace.admin")


def _row(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        else:
            data[key] = value
    return data


async def _ensure_schema(conn: asyncpg.Connection) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    missing = await conn.fetchval(
        """
        SELECT bool_or(to_regclass(table_name) IS NULL)
          FROM (VALUES
              ('public.marketplace_products'),
              ('public.marketplace_orders'),
              ('public.tenant_entitlements'),
              ('public.cartridge_installations'),
              ('public.cartridge_installation_events'),
              ('public.user_cartridge_overrides')
          ) AS required(table_name)
        """
    )
    if missing:
        raise MarketplaceError("marketplace schema is not migrated")
    _SCHEMA_READY = True


async def _ensure_products(conn: asyncpg.Connection) -> None:
    await _ensure_schema(conn)
    await conn.execute(
        """
        INSERT INTO marketplace_products (id, cartridge_id, name, description, category, status, metadata)
        SELECT
            c.id,
            c.id,
            c.name,
            c.description,
            COALESCE(NULLIF(c.category, ''), 'connector'),
            CASE WHEN c.id = 'platform' THEN 'internal' ELSE 'active' END,
            jsonb_build_object(
              'version', COALESCE(c.version, ''),
              'pattern', COALESCE(c.pattern, ''),
              'bronze_path', COALESCE(c.bronze_path, '')
            )
        FROM cartridges c
        ON CONFLICT (id) DO UPDATE
        SET name = EXCLUDED.name,
            cartridge_id = EXCLUDED.cartridge_id,
            description = EXCLUDED.description,
            category = EXCLUDED.category,
            metadata = marketplace_products.metadata || EXCLUDED.metadata,
            updated_at = NOW()
        """
    )


async def list_products(user: dict) -> dict[str, Any]:
    tenant_id, workspace_id = _scope(user)
    user_id = user.get("id")
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        await _ensure_products(conn)
        rows = await conn.fetch(
            """
            SELECT
                p.id,
                p.cartridge_id,
                p.name,
                p.description,
                p.category,
                p.status AS product_status,
                p.metadata,
                c.version,
                c.pattern,
                COALESCE(ec.entity_count, 0) AS entity_count,
                COALESCE(ds.dataset_count, 0) AS dataset_count,
                0 AS app_count,
                0 AS connection_count,
                te.status AS entitlement_status,
                mo.id AS order_id,
                mo.status AS order_status,
                ci.id AS installation_id,
                ci.status AS installation_status,
                ci.current_step,
                ci.error_message,
                ci.ready_at,
                ci.updated_at AS installation_updated_at
            FROM marketplace_products p
            JOIN cartridges c ON c.id = p.cartridge_id
            LEFT JOIN (
                SELECT cartridge_id, COUNT(*) AS entity_count
                FROM entity_config
                GROUP BY cartridge_id
            ) ec ON ec.cartridge_id = p.cartridge_id
            LEFT JOIN (
                SELECT cartridge, COUNT(*) AS dataset_count
                FROM datasets
                WHERE workspace_id = $1
                GROUP BY cartridge
            ) ds ON ds.cartridge = p.cartridge_id
            LEFT JOIN tenant_entitlements te
              ON te.tenant_id = $2
             AND te.workspace_id = $1
             AND te.cartridge_id = p.cartridge_id
            LEFT JOIN (
                SELECT DISTINCT ON (workspace_id, cartridge_id)
                    id, workspace_id, cartridge_id, status
                FROM marketplace_orders
                WHERE tenant_id = $2 AND workspace_id = $1
                ORDER BY workspace_id, cartridge_id, updated_at DESC
            ) mo ON mo.workspace_id = $1 AND mo.cartridge_id = p.cartridge_id
            LEFT JOIN cartridge_installations ci
              ON ci.tenant_id = $2
             AND ci.workspace_id = $1
             AND ci.cartridge_id = p.cartridge_id
            WHERE p.status = ANY($3::text[])
              AND lower(COALESCE(p.metadata->>'internal_only', 'false')) NOT IN ('true', '1', 'yes', 'on')
              AND NOT EXISTS (
                SELECT 1
                  FROM user_cartridge_overrides uco
                 WHERE uco.tenant_id = $2
                   AND uco.workspace_id = $1
                   AND uco.cartridge_id = p.cartridge_id
                   AND uco.user_id = $4
                   AND uco.mode = 'deny'
              )
            ORDER BY p.sort_order ASC, lower(p.name) ASC
            """,
            workspace_id,
            tenant_id,
            sorted(CUSTOMER_PRODUCT_STATUSES),
            user_id,
        )
    can_request_permission = permissions.has_permission(user, "marketplace.request")
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "products": [
            _decorate_product(_row(row) or {}, can_request_permission=can_request_permission)
            for row in rows
        ],
    }


async def get_product(cartridge_id: str, user: dict) -> dict[str, Any]:
    data = await list_products(user)
    for product in data["products"]:
        if product.get("cartridge_id") == cartridge_id:
            return product
    raise MarketplaceError("product not found")


def _commercial_profile(cartridge_id: str, item: dict[str, Any]) -> dict[str, Any]:
    profile = COMMERCIAL_PROFILES.get(cartridge_id)
    if profile:
        return profile
    category = item.get("category") or "connector"
    name = item.get("name") or cartridge_id
    return {
        "headline": f"{name} conecta datos empresariales a ΩMEGA con control por workspace.",
        "what_it_does": [
            "Activa el cartucho para habilitar datos, herramientas y resultados dentro del workspace autorizado.",
            "Prepara entidades, datasets, apps y contexto semántico para el copiloto según la configuración del cartucho.",
        ],
        "data_domains": [category],
        "dashboards": ["Apps y reportes según el paquete contratado"],
        "sample_questions": [
            f"¿Qué información puede responder {name}?",
            "¿Qué datasets están listos para análisis?",
        ],
        "requirements": ["Credenciales o conectividad según el sistema origen", "Permisos aprobados por el admin"],
        "plan": "Enterprise add-on",
        "price_label": "Contacta a soporte",
    }


def _access_status(item: dict[str, Any]) -> str:
    ent = item.get("entitlement_status")
    inst = item.get("installation_status")
    order = item.get("order_status")
    if ent == ACTIVE_ENTITLEMENT_STATUS and inst == READY_INSTALLATION_STATUS:
        return "active"
    if ent == "requested" or inst == "requested" or order == "pending":
        return "pending_approval"
    if inst in {"pending_connection", "waiting_credentials"}:
        return "pending_connection"
    if ent in {"paused", "revoked", "expired", "suspended"}:
        return ent
    if inst in {"paused", "revoked", "expired", "suspended", "failed"}:
        return inst
    return "available"


def _decorate_product(item: dict[str, Any], *, can_request_permission: bool = True) -> dict[str, Any]:
    status = _access_status(item)
    item["access_status"] = status
    item["installed"] = status == "active"
    item["ready"] = status == "active"
    item["requires_credentials"] = int(item.get("connection_count") or 0) > 0
    item["commercial"] = _commercial_profile(str(item.get("cartridge_id") or ""), item)
    item["can_request"] = can_request_permission and status in {"available", "failed"}
    item["can_retry"] = can_request_permission and status in {"failed", "pending_connection"}
    return item


async def request_product(cartridge_id: str, user: dict, *, source: str = "marketplace") -> dict[str, Any]:
    cartridge_id = (cartridge_id or "").strip()
    if not cartridge_id:
        raise MarketplaceError("cartridge_id required")
    tenant_id, workspace_id = _scope(user)
    user_id = user.get("id")
    fingerprint = hashlib.sha256(f"{tenant_id}:{workspace_id}:{cartridge_id}".encode("utf-8")).hexdigest()
    idempotency_key = f"marketplace-request:{workspace_id}:{cartridge_id}"
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        async with conn.transaction():
            await _ensure_products(conn)
            product = await conn.fetchrow(
                """
                SELECT p.id, p.cartridge_id, p.name, p.status
                FROM marketplace_products p
                WHERE p.cartridge_id = $1
                  AND p.status = 'active'
                  AND lower(COALESCE(p.metadata->>'internal_only', 'false')) NOT IN ('true', '1', 'yes', 'on')
                """,
                cartridge_id,
            )
            if not product:
                raise MarketplaceError(f"cartridge '{cartridge_id}' is not available in marketplace")
            blocked = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM user_cartridge_overrides
                     WHERE tenant_id = $1
                       AND workspace_id = $2
                       AND cartridge_id = $3
                       AND user_id = $4
                       AND mode = 'deny'
                )
                """,
                tenant_id,
                workspace_id,
                cartridge_id,
                user_id,
            )
            if blocked:
                raise MarketplaceError("cartridge not allowed for this user")

            existing_ready = await conn.fetchrow(
                """
                SELECT ci.id
                  FROM tenant_entitlements te
                  JOIN cartridge_installations ci
                    ON ci.tenant_id = te.tenant_id
                   AND ci.workspace_id = te.workspace_id
                   AND ci.cartridge_id = te.cartridge_id
                 WHERE te.tenant_id = $1
                   AND te.workspace_id = $2
                   AND te.cartridge_id = $3
                   AND te.status = 'active'
                   AND ci.status = 'ready'
                """,
                tenant_id,
                workspace_id,
                cartridge_id,
            )
            if existing_ready:
                return {"installation": await _installation_row(conn, existing_ready["id"]), "product": dict(product), "already_active": True}

            blocked_scope = await conn.fetchrow(
                """
                SELECT COALESCE(te.status, ci.status) AS status
                  FROM tenant_entitlements te
                  FULL OUTER JOIN cartridge_installations ci
                    ON ci.tenant_id = te.tenant_id
                   AND ci.workspace_id = te.workspace_id
                   AND ci.cartridge_id = te.cartridge_id
                 WHERE COALESCE(te.tenant_id, ci.tenant_id) = $1
                   AND COALESCE(te.workspace_id, ci.workspace_id) = $2
                   AND COALESCE(te.cartridge_id, ci.cartridge_id) = $3
                   AND (
                        te.status IN ('paused', 'revoked', 'expired', 'suspended')
                     OR ci.status IN ('paused', 'revoked', 'expired', 'suspended')
                   )
                 LIMIT 1
                """,
                tenant_id,
                workspace_id,
                cartridge_id,
            )
            if blocked_scope:
                raise MarketplaceError(
                    f"cartridge is {blocked_scope['status']}; ask an admin to reactivate it"
                )

            order_id = await conn.fetchval(
                """
                INSERT INTO marketplace_orders
                    (id, tenant_id, workspace_id, product_id, cartridge_id, status,
                     source, idempotency_key, created_by_id, metadata)
                VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9::jsonb)
                ON CONFLICT (workspace_id, product_id, idempotency_key)
                DO UPDATE SET
                    status = 'pending',
                    updated_at = NOW(),
                    metadata = marketplace_orders.metadata || EXCLUDED.metadata
                RETURNING id
                """,
                uuid.uuid4().hex,
                tenant_id,
                workspace_id,
                product["id"],
                cartridge_id,
                source,
                idempotency_key,
                user_id,
                json.dumps({"requested_by": user.get("email") or user_id}),
            )

            await conn.execute(
                """
                INSERT INTO tenant_entitlements
                    (tenant_id, workspace_id, cartridge_id, product_id, status,
                     source_order_id, activated_by_id)
                VALUES ($1, $2, $3, $4, 'requested', $5, $6)
                ON CONFLICT (tenant_id, workspace_id, cartridge_id)
                DO UPDATE SET
                    status = CASE
                        WHEN tenant_entitlements.status = 'active' THEN 'active'
                        WHEN tenant_entitlements.status IN ('paused', 'revoked', 'expired', 'suspended') THEN tenant_entitlements.status
                        ELSE 'requested'
                    END,
                    product_id = EXCLUDED.product_id,
                    source_order_id = EXCLUDED.source_order_id,
                    updated_at = NOW()
                """,
                tenant_id,
                workspace_id,
                cartridge_id,
                product["id"],
                order_id,
                user_id,
            )

            installation = await conn.fetchrow(
                """
                INSERT INTO cartridge_installations
                    (id, tenant_id, workspace_id, cartridge_id, product_id,
                     order_id, status, current_step, install_fingerprint,
                     created_by_id)
                VALUES ($1, $2, $3, $4, $5, $6, 'requested', 'pending_admin_approval',
                        $7, $8)
                ON CONFLICT (tenant_id, workspace_id, cartridge_id)
                DO UPDATE SET
                    product_id = EXCLUDED.product_id,
                    order_id = EXCLUDED.order_id,
                    status = CASE
                        WHEN cartridge_installations.status = 'ready' THEN 'ready'
                        WHEN cartridge_installations.status IN ('paused', 'revoked', 'expired', 'suspended') THEN cartridge_installations.status
                        ELSE 'requested'
                    END,
                    current_step = CASE
                        WHEN cartridge_installations.status = 'ready' THEN cartridge_installations.current_step
                        WHEN cartridge_installations.status IN ('paused', 'revoked', 'expired', 'suspended') THEN cartridge_installations.current_step
                        ELSE 'pending_admin_approval'
                    END,
                    error_message = NULL,
                    install_fingerprint = EXCLUDED.install_fingerprint,
                    updated_at = NOW()
                RETURNING id
                """,
                uuid.uuid4().hex,
                tenant_id,
                workspace_id,
                cartridge_id,
                product["id"],
                order_id,
                fingerprint,
                user_id,
            )
            installation_id = installation["id"]
            await _record_install_event(
                conn,
                installation_id,
                "activation_requested",
                "requested",
                "Solicitud enviada al administrador para habilitar el cartucho.",
                {"order_id": order_id, "product_id": product["id"], "source": source},
            )
            row = await _installation_row(conn, installation_id)

    await _audit(
        user,
        "cartridge_activation_requested",
        "cartridge",
        cartridge_id,
        "success",
        {"workspace_id": workspace_id, "tenant_id": tenant_id, "installation_id": installation_id, "new_status": "requested"},
    )
    return {"order_id": order_id, "installation": row, "product": dict(product)}


async def activate_product(cartridge_id: str, user: dict, *, source: str = "console_admin") -> dict[str, Any]:
    """Admin/direct activation. Customer activation requests use request_product."""
    if not _can_admin_marketplace(user):
        raise MarketplaceError("admin role required")
    tenant_id, workspace_id = _scope(user)
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        await _ensure_products(conn)
        # CRITICAL: re-validate marketplace_products.status and internal_only
        # before approving. Without this an existing installation in
        # 'requested' state could be force-approved by an admin even after
        # the product was marked internal_only / paused / revoked.
        await _assert_product_activatable(conn, cartridge_id)
        installation = await conn.fetchrow(
            """
            SELECT ci.id
              FROM cartridge_installations ci
             WHERE ci.tenant_id = $1 AND ci.workspace_id = $2 AND ci.cartridge_id = $3
            """,
            tenant_id,
            workspace_id,
            cartridge_id,
        )
    if not installation:
        requested = await request_product(cartridge_id, user, source=source)
        installation_id = requested["installation"]["id"]
    else:
        installation_id = installation["id"]
    return await approve_installation(installation_id, user, source=source)


async def list_installations(user: dict) -> dict[str, Any]:
    tenant_id, workspace_id = _scope(user)
    user_id = user.get("id")
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        rows = await conn.fetch(
            """
            SELECT
                ci.id,
                ci.tenant_id::text AS tenant_id,
                ci.workspace_id::text AS workspace_id,
                ci.cartridge_id,
                ci.product_id,
                ci.order_id,
                ci.status,
                ci.current_step,
                ci.error_message,
                ci.created_at,
                ci.updated_at,
                ci.ready_at,
                te.status AS entitlement_status,
                p.name AS product_name,
                c.version
            FROM cartridge_installations ci
            JOIN marketplace_products p ON p.id = ci.product_id
            JOIN cartridges c ON c.id = ci.cartridge_id
            LEFT JOIN tenant_entitlements te
              ON te.tenant_id = ci.tenant_id
             AND te.workspace_id = ci.workspace_id
             AND te.cartridge_id = ci.cartridge_id
            WHERE ci.tenant_id = $1 AND ci.workspace_id = $2
              AND NOT EXISTS (
                SELECT 1
                  FROM user_cartridge_overrides uco
                 WHERE uco.tenant_id = ci.tenant_id
                   AND uco.workspace_id = ci.workspace_id
                   AND uco.cartridge_id = ci.cartridge_id
                   AND uco.user_id = $3
                   AND uco.mode = 'deny'
              )
            ORDER BY ci.updated_at DESC
            """,
            tenant_id,
            workspace_id,
            user_id,
        )
    can_retry_permission = permissions.has_permission(user, "marketplace.request")
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "installations": [
            _decorate_installation(_row(r) or {}, can_retry_permission=can_retry_permission)
            for r in rows
        ],
    }


async def list_admin_installations(
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    user = _admin_scoped_user(user, tenant_id=tenant_id, workspace_id=workspace_id)
    if not _can_admin_marketplace(user):
        raise MarketplaceError("admin role required")
    tenant_id, workspace_id = _scope(user)
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        await _ensure_products(conn)
        rows = await conn.fetch(
            """
            SELECT
                ci.id,
                ci.tenant_id::text AS tenant_id,
                t.name AS tenant_name,
                ci.workspace_id::text AS workspace_id,
                w.name AS workspace_name,
                ci.cartridge_id,
                ci.product_id,
                ci.order_id,
                ci.status,
                ci.current_step,
                ci.error_message,
                ci.created_at,
                ci.updated_at,
                ci.ready_at,
                te.status AS entitlement_status,
                p.name AS product_name,
                c.version,
                u.email AS created_by_email
            FROM cartridge_installations ci
            JOIN tenants t ON t.id = ci.tenant_id
            JOIN workspaces w ON w.id = ci.workspace_id
            JOIN marketplace_products p ON p.id = ci.product_id
            JOIN cartridges c ON c.id = ci.cartridge_id
            LEFT JOIN users u ON u.id = ci.created_by_id
            LEFT JOIN tenant_entitlements te
              ON te.tenant_id = ci.tenant_id
             AND te.workspace_id = ci.workspace_id
             AND te.cartridge_id = ci.cartridge_id
            WHERE ci.tenant_id = $1
              AND ci.workspace_id = $2
            ORDER BY
              CASE ci.status
                WHEN 'requested' THEN 0
                WHEN 'failed' THEN 1
                WHEN 'pending_connection' THEN 2
                WHEN 'ready' THEN 3
                ELSE 4
              END,
              ci.updated_at DESC
            """,
            tenant_id,
            workspace_id,
        )
    return {"installations": [_decorate_installation(_row(r) or {}) for r in rows]}


async def get_admin_installation(
    installation_id: str,
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    user = _admin_scoped_user(user, tenant_id=tenant_id, workspace_id=workspace_id)
    if not _can_admin_marketplace(user):
        raise MarketplaceError("admin role required")
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        row = await _installation_row(conn, installation_id, admin=True)
    if not row:
        raise MarketplaceError("installation not found")
    return {"installation": _decorate_installation(row)}


async def approve_installation(
    installation_id: str,
    user: dict,
    *,
    source: str = "console_admin",
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    return await _set_installation_state(
        installation_id,
        user,
        entitlement_status="active",
        installation_status="ready",
        current_step="approved_ready",
        action="cartridge_activated",
        message="Cartucho aprobado por el administrador y habilitado para el workspace.",
        source=source,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        allowed_installation_statuses={"requested", "pending_connection", "waiting_credentials", "failed", "ready"},
        # CISO R3 hardening: re-check internal_only/status inside the
        # transaction so a concurrent metadata flip cannot slip through
        # between activate_product's pre-validation and the approval.
        assert_product_activatable=True,
    )


async def pause_installation(
    installation_id: str,
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    return await _set_installation_state(
        installation_id,
        user,
        entitlement_status="paused",
        installation_status="paused",
        current_step="paused_by_admin",
        action="cartridge_paused",
        message="Acceso pausado; la configuración se conserva.",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        allowed_installation_statuses={"ready", "pending_connection", "failed", "paused"},
    )


async def revoke_installation(
    installation_id: str,
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    return await _set_installation_state(
        installation_id,
        user,
        entitlement_status="revoked",
        installation_status="revoked",
        current_step="revoked_by_admin",
        action="cartridge_revoked",
        message="Acceso revocado; historial y configuración permanecen para soporte.",
        ends_now=True,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        allowed_installation_statuses={"requested", "pending_connection", "waiting_credentials", "ready", "failed", "paused", "expired", "suspended", "revoked"},
    )


async def _assert_product_activatable(conn, cartridge_id: str) -> None:
    """Reject reactivation/approval if the marketplace product is no longer
    eligible: archived/disabled status or `internal_only=true`. Without this
    check an admin could reopen access to a cartridge that has been
    classified internal-only after the original installation was created."""
    product = await conn.fetchrow(
        """
        SELECT p.status,
               COALESCE(p.metadata->>'internal_only', 'false') AS internal_only
          FROM marketplace_products p
         WHERE p.cartridge_id = $1
        """,
        cartridge_id,
    )
    if not product:
        raise MarketplaceError(f"cartridge '{cartridge_id}' is not registered in marketplace")
    if str(product["status"] or "").lower() != "active":
        raise MarketplaceError(
            f"cartridge '{cartridge_id}' is not available (product status: {product['status']})"
        )
    if str(product["internal_only"] or "").lower() in {"true", "1", "yes", "on"}:
        raise MarketplaceError(
            f"cartridge '{cartridge_id}' is internal-only and cannot be activated"
        )


async def reactivate_installation(
    installation_id: str,
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    # Phase-0 P0 + CISO R3 hardening:
    # 1) Reactivation must NOT bypass internal_only / status checks.
    # 2) The validation must run INSIDE the same transaction as the state
    #    change (`_set_installation_state` with `assert_product_activatable
    #    =True`) so a concurrent admin cannot flip the product metadata in
    #    the gap between the check and the approval.
    return await _set_installation_state(
        installation_id,
        user,
        entitlement_status="active",
        installation_status="ready",
        current_step="reactivated_ready",
        action="cartridge_reactivated",
        message="Acceso reactivado para el workspace.",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        allowed_installation_statuses={"paused", "revoked", "expired", "suspended"},
        assert_product_activatable=True,
    )


async def retry_installation(installation_id: str, user: dict) -> dict[str, Any]:
    tenant_id, workspace_id = _scope(user)
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT
                    ci.id,
                    ci.cartridge_id,
                    ci.status,
                    COALESCE(te.status, 'missing') AS entitlement_status
                FROM cartridge_installations ci
                LEFT JOIN tenant_entitlements te
                  ON te.tenant_id = ci.tenant_id
                 AND te.workspace_id = ci.workspace_id
                 AND te.cartridge_id = ci.cartridge_id
                WHERE ci.id = $1 AND ci.tenant_id = $2 AND ci.workspace_id = $3
                """,
                installation_id,
                tenant_id,
                workspace_id,
            )
            if not row:
                raise MarketplaceError("installation not found")
            if row["entitlement_status"] != "active":
                raise MarketplaceError("admin approval required before retry")
            if row["status"] in {"paused", "revoked", "expired", "suspended", "requested"}:
                raise MarketplaceError("installation is not retryable in its current status")
            if row["status"] == "ready":
                installation = await _installation_row(conn, installation_id)
                return {"installation": _decorate_installation(installation)}
            await conn.execute(
                """
                UPDATE cartridge_installations
                   SET status = 'pending_connection',
                       current_step = 'retry_requested',
                       error_message = NULL,
                       updated_at = NOW()
                 WHERE id = $1
                """,
                installation_id,
            )
            await _record_install_event(
                conn,
                installation_id,
                "retry",
                "pending_connection",
                "Reintento solicitado; no concede acceso nuevo ni salta aprobación admin.",
                {},
            )
            installation = await _installation_row(conn, installation_id)

    await _audit(
        user,
        "cartridge_installation_retry",
        "cartridge_installation",
        installation_id,
        "success",
        {"workspace_id": workspace_id, "tenant_id": tenant_id},
    )
    return {"installation": _decorate_installation(installation)}


async def list_installation_access(
    installation_id: str,
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    user = _admin_scoped_user(user, tenant_id=tenant_id, workspace_id=workspace_id)
    if not _can_admin_marketplace(user):
        raise MarketplaceError("admin role required")
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        await _ensure_schema(conn)
        installation = await _installation_row(conn, installation_id, admin=True)
        if not installation:
            raise MarketplaceError("installation not found")
        rows = await conn.fetch(
            """
            SELECT
                u.id,
                u.email,
                u.name,
                u.role AS global_role,
                u.is_active,
                r.name AS workspace_role,
                uco.mode AS override_mode,
                uco.reason,
                uco.updated_at,
                actor.email AS updated_by_email
            FROM user_workspace_roles uwr
            JOIN users u ON u.id = uwr.user_id
            JOIN roles r ON r.id = uwr.role_id
            LEFT JOIN user_cartridge_overrides uco
              ON uco.tenant_id = $1
             AND uco.workspace_id = $2
             AND uco.cartridge_id = $3
             AND uco.user_id = u.id
            LEFT JOIN users actor ON actor.id = uco.updated_by_id
            WHERE uwr.workspace_id = $2
            ORDER BY u.is_active DESC, lower(u.email), r.name
            """,
            installation["tenant_id"],
            installation["workspace_id"],
            installation["cartridge_id"],
        )
    base_usable = (
        installation.get("entitlement_status") == ACTIVE_ENTITLEMENT_STATUS
        and installation.get("status") == READY_INSTALLATION_STATUS
    )
    users: list[dict[str, Any]] = []
    for row in rows:
        item = _row(row) or {}
        mode = item.get("override_mode") or "inherit"
        item["mode"] = mode
        item["effective_access"] = bool(base_usable and item.get("is_active") and mode != "deny")
        item["base_access"] = base_usable
        users.append(item)
    return {"installation": _decorate_installation(installation), "users": users}


async def set_installation_user_access(
    installation_id: str,
    target_user_id: int | str,
    mode: str,
    reason: str | None,
    user: dict,
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    user = _admin_scoped_user(user, tenant_id=tenant_id, workspace_id=workspace_id)
    if not _can_admin_marketplace(user):
        raise MarketplaceError("admin role required")
    try:
        target_id = int(target_user_id)
    except (TypeError, ValueError) as exc:
        raise MarketplaceError("user_id invalid") from exc
    mode = (mode or "").strip().lower()
    if mode not in {"inherit", "deny"}:
        raise MarketplaceError("mode must be inherit or deny")
    reason = (reason or "").strip()[:500] or None

    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        async with conn.transaction():
            await _ensure_schema(conn)
            installation = await conn.fetchrow(
                """
                SELECT
                    ci.id,
                    ci.tenant_id::text AS tenant_id,
                    ci.workspace_id::text AS workspace_id,
                    ci.cartridge_id,
                    ci.status,
                    COALESCE(te.status, 'missing') AS entitlement_status
                FROM cartridge_installations ci
                LEFT JOIN tenant_entitlements te
                  ON te.tenant_id = ci.tenant_id
                 AND te.workspace_id = ci.workspace_id
                 AND te.cartridge_id = ci.cartridge_id
                WHERE ci.id = $1
                """,
                installation_id,
            )
            if not installation:
                raise MarketplaceError("installation not found")
            target = await conn.fetchrow(
                """
                SELECT u.id, u.email
                  FROM user_workspace_roles uwr
                  JOIN users u ON u.id = uwr.user_id
                 WHERE uwr.workspace_id = $1
                   AND u.id = $2
                 LIMIT 1
                """,
                installation["workspace_id"],
                target_id,
            )
            if not target:
                raise MarketplaceError("user is not assigned to this workspace")
            old_mode = await conn.fetchval(
                """
                SELECT mode
                  FROM user_cartridge_overrides
                 WHERE tenant_id = $1
                   AND workspace_id = $2
                   AND cartridge_id = $3
                   AND user_id = $4
                """,
                installation["tenant_id"],
                installation["workspace_id"],
                installation["cartridge_id"],
                target_id,
            )
            if mode == "inherit":
                await conn.execute(
                    """
                    DELETE FROM user_cartridge_overrides
                     WHERE tenant_id = $1
                       AND workspace_id = $2
                       AND cartridge_id = $3
                       AND user_id = $4
                    """,
                    installation["tenant_id"],
                    installation["workspace_id"],
                    installation["cartridge_id"],
                    target_id,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO user_cartridge_overrides
                        (tenant_id, workspace_id, cartridge_id, user_id, mode,
                         reason, created_by_id, updated_by_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
                    ON CONFLICT (tenant_id, workspace_id, cartridge_id, user_id)
                    DO UPDATE SET
                        mode = EXCLUDED.mode,
                        reason = EXCLUDED.reason,
                        updated_by_id = EXCLUDED.updated_by_id,
                        updated_at = NOW()
                    """,
                    installation["tenant_id"],
                    installation["workspace_id"],
                    installation["cartridge_id"],
                    target_id,
                    mode,
                    reason,
                    user.get("id"),
                )
            await _record_install_event(
                conn,
                installation_id,
                "user_access_updated",
                installation["status"],
                "Acceso de usuario actualizado para el cartucho.",
                {
                    "target_user_id": target_id,
                    "target_email": target["email"],
                    "old_mode": old_mode or "inherit",
                    "new_mode": mode,
                    "reason": reason,
                },
            )

    await _audit(
        user,
        "cartridge_user_access_updated",
        "cartridge_installation",
        installation_id,
        "success",
        {
            "tenant_id": installation["tenant_id"],
            "workspace_id": installation["workspace_id"],
            "cartridge_id": installation["cartridge_id"],
            "target_user_id": target_id,
            "target_email": target["email"],
            "old_mode": old_mode or "inherit",
            "new_mode": mode,
        },
        critical=True,
    )
    return await list_installation_access(
        installation_id,
        user,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def _reconcile_packaged_app_grants(
    conn,
    *,
    tenant_id: Any,
    workspace_id: Any,
    cartridge_id: Any,
    installation_status: str,
) -> None:
    """Keep this workspace's app grants in step with the installation state.

    Entering ready reconciles from the registry; leaving it revokes everything
    the cartridge granted, in the same transaction that made the change. A
    grant that outlived its installation is an authority the operator believes
    they withdrew.

    Strict on purpose: errors propagate. Swallowing them let an installation be
    marked ready with its grants half-written while the audit trail recorded
    success. A workspace whose Gold is not materialised yet, and a user-created
    app, are handled inside the SQL and are not errors.
    """
    if not tenant_id or not workspace_id or not cartridge_id:
        return
    from app.domains.apps import grants as app_grants

    await app_grants.lock_workspace_reconciliation(
        conn,
        workspace_id=str(workspace_id),
    )
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, true), "
        "set_config('app.workspace_id', $2, true)",
        str(tenant_id),
        str(workspace_id),
    )
    if installation_status != READY_INSTALLATION_STATUS:
        revoked = await app_grants.revoke_cartridge_grants(
            conn,
            cartridge_id=str(cartridge_id),
            reason=f"installation_{installation_status}",
        )
        logger.info(
            "[app-grants] %s/%s %s left ready (%s): revoked=%s",
            tenant_id, workspace_id, cartridge_id, installation_status, revoked,
        )
        return

    summary = await app_grants.reconcile_workspace(
        conn, cartridge_id=str(cartridge_id)
    )
    logger.info(
        "[app-grants] %s/%s %s: granted=%s revoked=%s",
        tenant_id, workspace_id, cartridge_id,
        summary.get("granted"), summary.get("revoked"),
    )


async def _set_installation_state(
    installation_id: str,
    user: dict,
    *,
    entitlement_status: str,
    installation_status: str,
    current_step: str,
    action: str,
    message: str,
    source: str = "console_admin",
    ends_now: bool = False,
    allowed_installation_statuses: set[str] | None = None,
    assert_product_activatable: bool = False,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    user = _admin_scoped_user(user, tenant_id=tenant_id, workspace_id=workspace_id)
    if not _can_admin_marketplace(user):
        raise MarketplaceError("admin role required")
    user_id = user.get("id")
    p = await cartridge_service.pool()
    async with scoped_db_for_user(p, user) as (conn, _tenant_id, _workspace_id):
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT
                    ci.id,
                    ci.tenant_id::text AS tenant_id,
                    ci.workspace_id::text AS workspace_id,
                    ci.cartridge_id,
                    ci.product_id,
                    ci.order_id,
                    ci.status AS old_installation_status,
                    te.status AS old_entitlement_status
                FROM cartridge_installations ci
                LEFT JOIN tenant_entitlements te
                  ON te.tenant_id = ci.tenant_id
                 AND te.workspace_id = ci.workspace_id
                 AND te.cartridge_id = ci.cartridge_id
                WHERE ci.id = $1
                FOR UPDATE OF ci
                """,
                installation_id,
            )
            if not row:
                raise MarketplaceError("installation not found")
            if allowed_installation_statuses is not None and row["old_installation_status"] not in allowed_installation_statuses:
                raise MarketplaceError("installation transition not allowed from current status")
            # CISO Round-3 hardening: TOCTOU between an external
            # `_assert_product_activatable(conn, ...)` and this transaction
            # let an admin reactivate an internal_only cartridge if another
            # admin flipped the metadata in the small window between the
            # validation and the state change. Re-check here, INSIDE the
            # same transaction that holds the FOR UPDATE row lock, so the
            # check-and-set is atomic from the marketplace_products POV.
            if assert_product_activatable:
                product = await conn.fetchrow(
                    """
                    SELECT p.status,
                           COALESCE(p.metadata->>'internal_only', 'false') AS internal_only
                      FROM marketplace_products p
                     WHERE p.cartridge_id = $1
                     FOR SHARE
                    """,
                    row["cartridge_id"],
                )
                if not product:
                    raise MarketplaceError(
                        f"cartridge '{row['cartridge_id']}' is not registered in marketplace"
                    )
                if str(product["status"] or "").lower() != "active":
                    raise MarketplaceError(
                        f"cartridge '{row['cartridge_id']}' is not available "
                        f"(product status: {product['status']})"
                    )
                if str(product["internal_only"] or "").lower() in {"true", "1", "yes", "on"}:
                    raise MarketplaceError(
                        f"cartridge '{row['cartridge_id']}' is internal-only "
                        f"and cannot be activated"
                    )

            await conn.execute(
                """
                UPDATE marketplace_orders
                   SET status = CASE WHEN $2 = 'active' THEN 'completed' ELSE status END,
                       updated_at = NOW()
                 WHERE id = $1
                """,
                row["order_id"],
                entitlement_status,
            )
            await conn.execute(
                """
                INSERT INTO tenant_entitlements
                    (tenant_id, workspace_id, cartridge_id, product_id, status,
                     source_order_id, activated_by_id, ends_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, CASE WHEN $8 THEN NOW() ELSE NULL END)
                ON CONFLICT (tenant_id, workspace_id, cartridge_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    product_id = EXCLUDED.product_id,
                    source_order_id = EXCLUDED.source_order_id,
                    activated_by_id = EXCLUDED.activated_by_id,
                    ends_at = CASE WHEN $8 THEN NOW() ELSE NULL END,
                    updated_at = NOW()
                """,
                row["tenant_id"],
                row["workspace_id"],
                row["cartridge_id"],
                row["product_id"],
                entitlement_status,
                row["order_id"],
                user_id,
                ends_now,
            )
            await conn.execute(
                """
                UPDATE cartridge_installations
                   SET status = $2,
                       current_step = $3,
                       error_message = NULL,
                       updated_at = NOW(),
                       ready_at = CASE WHEN $2 = 'ready' THEN COALESCE(ready_at, NOW()) ELSE ready_at END
                 WHERE id = $1
                """,
                installation_id,
                installation_status,
                current_step,
            )
            await _record_install_event(
                conn,
                installation_id,
                action,
                installation_status,
                message,
                {
                    "source": source,
                    "old_status": row["old_installation_status"],
                    "new_status": installation_status,
                    "old_entitlement_status": row["old_entitlement_status"],
                    "new_entitlement_status": entitlement_status,
                },
            )
            installation = await _installation_row(conn, installation_id, admin=True)
            # Reconcile the packaged apps' dataset grants inside the same
            # transaction that made the cartridge usable.
            #
            # Every activation path — approve, reactivate, retry, admin
            # activate — funnels through this function, so hooking it here
            # covers all of them rather than one. Doing it on activation, and
            # only here, is what keeps a plain read from ever widening
            # authority: opening an app must not be what approves it.
            #
            # In-transaction on purpose: a partial reconciliation rolls back
            # with the state change, so a workspace is never left marked ready
            # with half its grants.
            await _reconcile_packaged_app_grants(
                conn,
                tenant_id=installation.get("tenant_id"),
                workspace_id=installation.get("workspace_id"),
                cartridge_id=installation.get("cartridge_id"),
                installation_status=installation_status,
            )

    await _audit(
        user,
        action,
        "cartridge_installation",
        installation_id,
        "success",
        {
            "tenant_id": installation.get("tenant_id"),
            "workspace_id": installation.get("workspace_id"),
            "cartridge_id": installation.get("cartridge_id"),
            "old_status": row["old_installation_status"],
            "new_status": installation_status,
            "old_entitlement_status": row["old_entitlement_status"],
            "new_entitlement_status": entitlement_status,
        },
        critical=True,
    )
    return {"installation": _decorate_installation(installation)}


def _decorate_installation(item: dict[str, Any], *, can_retry_permission: bool = True) -> dict[str, Any]:
    status = item.get("status")
    entitlement = item.get("entitlement_status")
    item["usable"] = entitlement == ACTIVE_ENTITLEMENT_STATUS and status == READY_INSTALLATION_STATUS
    item["blocked"] = status in {"paused", "revoked", "expired", "suspended"} or entitlement in {"paused", "revoked", "expired", "suspended"}
    item["access_status"] = _access_status({"installation_status": status, "entitlement_status": entitlement})
    item["can_retry"] = (
        can_retry_permission
        and entitlement == ACTIVE_ENTITLEMENT_STATUS
        and status in {"failed", "pending_connection", "waiting_credentials"}
    )
    return item


async def _installation_row(conn: asyncpg.Connection, installation_id: str, *, admin: bool = False) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT
            ci.id,
            ci.tenant_id::text AS tenant_id,
            CASE WHEN $2 THEN t.name ELSE NULL END AS tenant_name,
            ci.workspace_id::text AS workspace_id,
            CASE WHEN $2 THEN w.name ELSE NULL END AS workspace_name,
            ci.cartridge_id,
            ci.product_id,
            ci.order_id,
            ci.status,
            ci.current_step,
            ci.error_message,
            ci.created_at,
            ci.updated_at,
            ci.ready_at,
            te.status AS entitlement_status,
            p.name AS product_name,
            c.version
        FROM cartridge_installations ci
        JOIN marketplace_products p ON p.id = ci.product_id
        JOIN cartridges c ON c.id = ci.cartridge_id
        JOIN tenants t ON t.id = ci.tenant_id
        JOIN workspaces w ON w.id = ci.workspace_id
        LEFT JOIN tenant_entitlements te
          ON te.tenant_id = ci.tenant_id
         AND te.workspace_id = ci.workspace_id
         AND te.cartridge_id = ci.cartridge_id
        WHERE ci.id = $1
        """,
        installation_id,
        admin,
    )
    return _row(row) or {}


async def _record_install_event(
    conn: asyncpg.Connection,
    installation_id: str,
    step: str,
    status: str,
    message: str,
    metadata: dict[str, Any],
) -> None:
    await conn.execute(
        """
        INSERT INTO cartridge_installation_events
            (installation_id, step, status, message, metadata)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        installation_id,
        step,
        status,
        message,
        json.dumps(metadata),
    )


async def _audit(
    user: dict,
    action: str,
    resource_type: str,
    resource_id: str,
    status: str,
    metadata: dict[str, Any],
    *,
    critical: bool = False,
) -> None:
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        metadata=metadata,
        critical=critical,
    )
