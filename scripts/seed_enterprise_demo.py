#!/usr/bin/env python3
"""
CONSOLA-BETA — Enterprise Demo Pack seeder.

Generates and registers a realistic-but-fake enterprise dataset spanning
Replicon PSA, SAP SuccessFactors / HCM and SAP S/4HANA so the console can be
exercised end-to-end (Studio, Entidades, Refinar, Analytics, IA Semántica,
RAG) without touching real systems.

Design:
  - All demo objects use the prefix `demo_` (tables) or `[DEMO PACK]` (text
    markers) so they can be wiped in one command without affecting real data.
  - Demo Bronze tables live in schema `demo_bronze` on the main DB.
  - Demo Silver tables live in schema `demo_silver` on the main DB.
  - Demo Gold tables live as `gold_demo_<name>` in the modecissions_gold DB
    (matches the materialize() naming pattern, so /api/data/{dataset} works).
  - A demo cartridge `demo_enterprise` is registered alongside the real ones.
  - Dataset metadata is registered in `datasets` so they appear in Refinar.
  - Semantic terms and RAG knowledge bits are inserted into the main DB.

Usage:
    # From host (default ports from docker-compose):
    python scripts/seed_enterprise_demo.py

    # With explicit DSNs:
    PG_DSN="postgresql://postgres:pw@localhost:15432/modecissions" \
    GOLD_DSN="postgresql://postgres:pw@localhost:15433/modecissions_gold" \
        python scripts/seed_enterprise_demo.py

    # Smaller volumes for a quick smoke run:
    python scripts/seed_enterprise_demo.py --scale 0.1

    # Reset everything (drops all demo objects):
    python scripts/seed_enterprise_demo.py --reset
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import sys
import time
from contextlib import contextmanager
from typing import Iterable

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    sys.stderr.write(
        "ERROR: psycopg2 not installed. Run: pip install psycopg2-binary\n"
    )
    sys.exit(2)


# ─── DSN resolution ──────────────────────────────────────────────────────────

def _resolve_dsn(env_var: str, fallback_port: int) -> str:
    raw = os.environ.get(env_var)
    if raw:
        return raw.replace("postgresql+psycopg2://", "postgresql://")
    # Fall back to docker-compose host port mapping
    pw = os.environ.get("POSTGRES_PASSWORD", "postgres")
    db = "modecissions_gold" if fallback_port == 15433 else "modecissions"
    return f"postgresql://postgres:{pw}@localhost:{fallback_port}/{db}"


PG_DSN   = _resolve_dsn("PG_DSN",        15432)
GOLD_DSN = _resolve_dsn("GOLD_DSN",      15433)


@contextmanager
def _conn(dsn: str):
    cn = psycopg2.connect(dsn)
    try:
        yield cn
        cn.commit()
    except Exception:
        cn.rollback()
        raise
    finally:
        cn.close()


def _log(msg: str) -> None:
    print(f"[demo-seed] {msg}", flush=True)


# ─── Deterministic faker (no external deps) ──────────────────────────────────

NAMES_M = ["Carlos", "Juan", "Luis", "Miguel", "Pedro", "Andrés", "Diego",
           "Jorge", "Pablo", "Ricardo", "Roberto", "Sergio", "Mateo", "Iván",
           "Adrián", "Daniel", "Fernando", "Alejandro", "Eduardo", "Manuel"]
NAMES_F = ["María", "Ana", "Laura", "Lucía", "Sofía", "Marta", "Elena",
           "Carmen", "Paula", "Sara", "Isabel", "Patricia", "Beatriz",
           "Cristina", "Natalia", "Andrea", "Mónica", "Raquel", "Silvia"]
SURNAMES = ["García", "Martínez", "López", "Rodríguez", "González", "Pérez",
            "Sánchez", "Romero", "Fernández", "Ramírez", "Torres", "Flores",
            "Rivera", "Castro", "Vargas", "Morales", "Reyes", "Cruz",
            "Ortega", "Jiménez", "Mendoza", "Silva", "Núñez", "Herrera"]
COUNTRIES = ["México", "España", "Argentina", "Colombia", "Chile", "Perú",
             "USA", "Brasil", "Uruguay", "Costa Rica"]
CITIES = ["CDMX", "Madrid", "Barcelona", "Buenos Aires", "Bogotá", "Santiago",
          "Lima", "São Paulo", "Monterrey", "Guadalajara", "Valencia",
          "Quito", "Montevideo", "San José"]
DEPARTMENTS = ["Tecnología", "Consultoría", "Operaciones", "Finanzas",
               "Marketing", "Ventas", "Recursos Humanos", "Legal",
               "Producto", "Data Science", "Infraestructura", "Seguridad",
               "Customer Success", "Soporte", "PMO"]
ROLES = ["Consultor Jr", "Consultor Sr", "Project Manager",
         "Tech Lead", "Architect", "Director", "Analyst", "VP",
         "Engineer", "Designer", "QA", "Scrum Master"]
INDUSTRIES = ["Banca", "Retail", "Manufactura", "Telecom", "Salud", "Energía",
              "Seguros", "Educación", "Gobierno", "Logística", "Tecnología"]
SUPPLIER_CATS = ["Software", "Cloud", "Consultoría", "Hardware",
                 "Marketing", "Legal", "Logística", "Materias Primas",
                 "Facilities", "Servicios Profesionales"]
EXPENSE_CATS = ["Viaje", "Comida", "Transporte", "Alojamiento", "Software",
                "Materiales", "Capacitación", "Otros"]
ACCOUNTS = [
    ("4000", "Revenue"), ("4100", "Service Revenue"),
    ("5000", "COGS"), ("5100", "Salaries"), ("5200", "Travel"),
    ("5300", "Office"), ("6000", "Marketing"), ("6100", "IT"),
    ("7000", "Depreciation"), ("7100", "Interest"),
]
PROJECT_STATUS = ["InProgress", "Planning", "Completed", "OnHold", "Cancelled"]
INVOICE_STATUS = ["Paid", "Open", "Overdue", "Disputed"]
PO_STATUS      = ["Open", "PartiallyReceived", "Closed", "Cancelled"]


def _name(rng: random.Random) -> str:
    first = rng.choice(NAMES_M + NAMES_F)
    return f"{first} {rng.choice(SURNAMES)} {rng.choice(SURNAMES)}"


def _company(rng: random.Random, prefix: str) -> str:
    return f"{prefix} {rng.choice(SURNAMES)} {rng.choice(['SA', 'SL', 'Group', 'Holding', 'Corp', 'Inc'])}"


# ─── Schema bootstrap ────────────────────────────────────────────────────────

DEMO_TAG = "[DEMO PACK]"

CREATE_BRONZE_SILVER_SQL = """
CREATE SCHEMA IF NOT EXISTS demo_bronze;
CREATE SCHEMA IF NOT EXISTS demo_silver;

-- ── Replicon Bronze ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo_bronze.replicon_users (
    user_id        TEXT PRIMARY KEY,
    nombre         TEXT,
    rol            TEXT,
    departamento   TEXT,
    ubicacion      TEXT,
    tarifa_hora    NUMERIC,
    coste_hora     NUMERIC,
    status         TEXT,
    fecha_alta     DATE,
    last_modified  TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_bronze.replicon_clients (
    client_id      TEXT PRIMARY KEY,
    nombre         TEXT,
    industria      TEXT,
    pais           TEXT,
    riesgo         TEXT,
    last_modified  TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_bronze.replicon_projects (
    project_id        TEXT PRIMARY KEY,
    client_id         TEXT,
    nombre            TEXT,
    estado            TEXT,
    presupuesto       NUMERIC,
    revenue_estimado  NUMERIC,
    coste_estimado    NUMERIC,
    fecha_inicio      DATE,
    fecha_fin         DATE,
    manager           TEXT,
    last_modified     TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_bronze.replicon_tasks (
    task_id        TEXT PRIMARY KEY,
    project_id     TEXT,
    nombre         TEXT,
    horas_est      NUMERIC,
    estado         TEXT,
    last_modified  TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_bronze.replicon_assignments (
    assignment_id  TEXT PRIMARY KEY,
    user_id        TEXT,
    project_id     TEXT,
    fecha_inicio   DATE,
    fecha_fin      DATE,
    horas_semana   NUMERIC,
    last_modified  TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_bronze.replicon_timesheets (
    timesheet_id        TEXT PRIMARY KEY,
    user_id             TEXT,
    project_id          TEXT,
    task_id             TEXT,
    fecha               DATE,
    horas               NUMERIC,
    billable            BOOLEAN,
    tarifa_hora         NUMERIC,
    coste_hora          NUMERIC,
    revenue_calculado   NUMERIC,
    coste_calculado     NUMERIC
);
CREATE INDEX IF NOT EXISTS ix_demo_timesheets_project ON demo_bronze.replicon_timesheets(project_id);
CREATE INDEX IF NOT EXISTS ix_demo_timesheets_user    ON demo_bronze.replicon_timesheets(user_id);
CREATE INDEX IF NOT EXISTS ix_demo_timesheets_fecha   ON demo_bronze.replicon_timesheets(fecha);

CREATE TABLE IF NOT EXISTS demo_bronze.replicon_expenses (
    expense_id     TEXT PRIMARY KEY,
    user_id        TEXT,
    project_id     TEXT,
    categoria      TEXT,
    monto          NUMERIC,
    fecha          DATE,
    reimbursable   BOOLEAN
);
CREATE TABLE IF NOT EXISTS demo_bronze.replicon_invoices (
    invoice_id        TEXT PRIMARY KEY,
    client_id         TEXT,
    project_id        TEXT,
    monto             NUMERIC,
    estado            TEXT,
    fecha_emision     DATE,
    fecha_vencimiento DATE,
    dias_vencida      INTEGER
);

-- ── SAP HCM / SuccessFactors Bronze ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo_bronze.sap_departamentos (
    dept_id        TEXT PRIMARY KEY,
    nombre         TEXT,
    parent_id      TEXT,
    ubicacion      TEXT
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_centros_coste (
    centro_coste_id TEXT PRIMARY KEY,
    nombre          TEXT,
    departamento    TEXT,
    pais            TEXT
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_posiciones (
    posicion_id    TEXT PRIMARY KEY,
    titulo         TEXT,
    departamento   TEXT,
    nivel          INTEGER,
    vacante        BOOLEAN
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_empleados (
    employee_id      TEXT PRIMARY KEY,
    nombre           TEXT,
    departamento     TEXT,
    posicion         TEXT,
    manager          TEXT,
    centro_coste     TEXT,
    ubicacion        TEXT,
    salario          NUMERIC,
    status           TEXT,
    fecha_alta       DATE,
    fecha_baja       DATE,
    last_modified    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_demo_empleados_dept ON demo_bronze.sap_empleados(departamento);

CREATE TABLE IF NOT EXISTS demo_bronze.sap_compensaciones (
    comp_id        TEXT PRIMARY KEY,
    employee_id    TEXT,
    salario_base   NUMERIC,
    bono           NUMERIC,
    fecha_efectiva DATE
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_ausencias (
    absence_id     TEXT PRIMARY KEY,
    employee_id    TEXT,
    tipo           TEXT,
    fecha_inicio   DATE,
    fecha_fin      DATE,
    dias           NUMERIC
);
CREATE INDEX IF NOT EXISTS ix_demo_ausencias_emp ON demo_bronze.sap_ausencias(employee_id);

-- ── SAP S/4HANA Bronze ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demo_bronze.sap_proveedores (
    supplier_id        TEXT PRIMARY KEY,
    nombre             TEXT,
    pais               TEXT,
    categoria          TEXT,
    spend_anual        NUMERIC,
    facturas_vencidas  INTEGER,
    riesgo             TEXT
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_customers (
    customer_id    TEXT PRIMARY KEY,
    nombre         TEXT,
    pais           TEXT,
    industria      TEXT
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_materiales (
    material_id    TEXT PRIMARY KEY,
    nombre         TEXT,
    categoria      TEXT,
    precio_unit    NUMERIC,
    stock          INTEGER
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_ordenes_compra (
    po_id          TEXT PRIMARY KEY,
    supplier_id    TEXT,
    comprador      TEXT,
    categoria      TEXT,
    monto          NUMERIC,
    estado         TEXT,
    fecha          DATE
);
CREATE INDEX IF NOT EXISTS ix_demo_po_supplier ON demo_bronze.sap_ordenes_compra(supplier_id);
CREATE INDEX IF NOT EXISTS ix_demo_po_estado   ON demo_bronze.sap_ordenes_compra(estado);

CREATE TABLE IF NOT EXISTS demo_bronze.sap_facturas_proveedor (
    invoice_id        TEXT PRIMARY KEY,
    supplier_id       TEXT,
    monto             NUMERIC,
    estado            TEXT,
    fecha_emision     DATE,
    fecha_vencimiento DATE,
    dias_vencida      INTEGER
);
CREATE TABLE IF NOT EXISTS demo_bronze.sap_ventas (
    sales_order_id  TEXT PRIMARY KEY,
    customer_id     TEXT,
    material_id     TEXT,
    cantidad        INTEGER,
    monto           NUMERIC,
    margen          NUMERIC,
    fecha           DATE
);
CREATE INDEX IF NOT EXISTS ix_demo_ventas_cust ON demo_bronze.sap_ventas(customer_id);

CREATE TABLE IF NOT EXISTS demo_bronze.sap_journal_entries (
    journal_id        TEXT PRIMARY KEY,
    cuenta_contable   TEXT,
    centro_coste      TEXT,
    monto             NUMERIC,
    moneda            TEXT,
    tipo              TEXT,
    fecha             DATE,
    descripcion       TEXT
);
CREATE INDEX IF NOT EXISTS ix_demo_journal_cuenta ON demo_bronze.sap_journal_entries(cuenta_contable);
CREATE INDEX IF NOT EXISTS ix_demo_journal_cc     ON demo_bronze.sap_journal_entries(centro_coste);
"""


# Silver views — clean / typed views over Bronze
CREATE_SILVER_VIEWS_SQL = """
CREATE OR REPLACE VIEW demo_silver.replicon_timesheets_limpios AS
SELECT timesheet_id, user_id, project_id, task_id, fecha, horas,
       billable, tarifa_hora, coste_hora,
       horas * tarifa_hora AS revenue,
       horas * coste_hora  AS coste
FROM   demo_bronze.replicon_timesheets
WHERE  horas > 0;

CREATE OR REPLACE VIEW demo_silver.replicon_project_finance AS
SELECT  p.project_id,
        p.nombre                              AS proyecto,
        p.client_id,
        p.presupuesto,
        p.revenue_estimado,
        p.coste_estimado,
        COALESCE(ts.revenue_real, 0)          AS revenue_real,
        COALESCE(ts.coste_real, 0)            AS coste_real,
        COALESCE(ts.revenue_real, 0) - COALESCE(ts.coste_real, 0) AS margen_real,
        CASE WHEN COALESCE(ts.revenue_real, 0) = 0 THEN 0
             ELSE (COALESCE(ts.revenue_real, 0) - COALESCE(ts.coste_real, 0))
                  / NULLIF(ts.revenue_real, 0)
        END                                   AS margen_pct,
        CASE WHEN COALESCE(ts.coste_real, 0) > p.presupuesto
             THEN TRUE ELSE FALSE END         AS sobre_presupuesto,
        p.estado,
        p.manager
FROM    demo_bronze.replicon_projects p
LEFT JOIN (
    SELECT project_id,
           SUM(horas * tarifa_hora) AS revenue_real,
           SUM(horas * coste_hora)  AS coste_real
    FROM   demo_bronze.replicon_timesheets
    GROUP BY project_id
) ts ON ts.project_id = p.project_id;

CREATE OR REPLACE VIEW demo_silver.sap_empleados_limpios AS
SELECT employee_id, nombre, departamento, posicion, manager, centro_coste,
       ubicacion, salario, status, fecha_alta, fecha_baja
FROM   demo_bronze.sap_empleados
WHERE  status IS NOT NULL;

CREATE OR REPLACE VIEW demo_silver.sap_proveedores_riesgo AS
SELECT supplier_id, nombre, pais, categoria, spend_anual,
       facturas_vencidas, riesgo,
       CASE WHEN riesgo = 'Alto' THEN 3
            WHEN riesgo = 'Medio' THEN 2
            ELSE 1 END AS riesgo_score
FROM   demo_bronze.sap_proveedores;

CREATE OR REPLACE VIEW demo_silver.sap_ordenes_compra_limpias AS
SELECT po_id, supplier_id, comprador, categoria, monto, estado, fecha
FROM   demo_bronze.sap_ordenes_compra
WHERE  monto > 0;

CREATE OR REPLACE VIEW demo_silver.sap_finanzas_limpias AS
SELECT journal_id, cuenta_contable, centro_coste, monto, moneda, tipo,
       fecha, descripcion
FROM   demo_bronze.sap_journal_entries;
"""


# ─── Gold definitions (registered in `datasets` and materialized in pggold) ─

GOLD_DEFINITIONS = [
    # (dataset_name, cartridge, description, source_path, ddl, populate_sql)
    {
        "name": "demo_revenue_por_cliente",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Revenue total y count de proyectos por cliente.",
        "sources": ["demo_bronze.replicon_invoices", "demo_bronze.replicon_clients"],
    },
    {
        "name": "demo_revenue_por_proyecto",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Revenue por proyecto + cliente.",
        "sources": ["demo_silver.replicon_project_finance"],
    },
    {
        "name": "demo_margen_por_proyecto",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Margen real y porcentual por proyecto, marca sobre_presupuesto.",
        "sources": ["demo_silver.replicon_project_finance"],
    },
    {
        "name": "demo_utilizacion_consultores",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Utilización por consultor: horas facturables / totales.",
        "sources": ["demo_silver.replicon_timesheets_limpios", "demo_bronze.replicon_users"],
    },
    {
        "name": "demo_horas_facturables",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Resumen mensual de horas facturables vs no facturables.",
        "sources": ["demo_silver.replicon_timesheets_limpios"],
    },
    {
        "name": "demo_gastos_por_proyecto",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Gastos totales y reembolsables por proyecto.",
        "sources": ["demo_bronze.replicon_expenses"],
    },
    {
        "name": "demo_facturas_vencidas_replicon",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Facturas vencidas con días de atraso.",
        "sources": ["demo_bronze.replicon_invoices"],
    },
    {
        "name": "demo_proyectos_sobre_presupuesto",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Proyectos cuyo coste real supera el presupuesto.",
        "sources": ["demo_silver.replicon_project_finance"],
    },
    {
        "name": "demo_top_clientes_revenue",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Top 20 clientes por revenue facturado.",
        "sources": ["demo_bronze.replicon_invoices"],
    },
    {
        "name": "demo_consultores_baja_utilizacion",
        "cartridge": "replicon",
        "description": "[DEMO PACK] Consultores con utilización < 50%.",
        "sources": ["demo_silver.replicon_timesheets_limpios"],
    },
    # ── HR ──
    {
        "name": "demo_headcount_por_departamento",
        "cartridge": "sap_successfactors",
        "description": "[DEMO PACK] Headcount activo por departamento.",
        "sources": ["demo_silver.sap_empleados_limpios"],
    },
    {
        "name": "demo_headcount_por_ubicacion",
        "cartridge": "sap_successfactors",
        "description": "[DEMO PACK] Headcount activo por ubicación.",
        "sources": ["demo_silver.sap_empleados_limpios"],
    },
    {
        "name": "demo_salario_promedio",
        "cartridge": "sap_successfactors",
        "description": "[DEMO PACK] Salario promedio por departamento.",
        "sources": ["demo_silver.sap_empleados_limpios"],
    },
    {
        "name": "demo_empleados_sin_centro_coste",
        "cartridge": "sap_hcm",
        "description": "[DEMO PACK] Empleados activos sin centro de coste asignado.",
        "sources": ["demo_silver.sap_empleados_limpios"],
    },
    {
        "name": "demo_bajas_recientes",
        "cartridge": "sap_hcm",
        "description": "[DEMO PACK] Bajas en los últimos 90 días.",
        "sources": ["demo_silver.sap_empleados_limpios"],
    },
    {
        "name": "demo_ausencias_por_departamento",
        "cartridge": "sap_hcm",
        "description": "[DEMO PACK] Días de ausencia totales por departamento.",
        "sources": ["demo_bronze.sap_ausencias", "demo_silver.sap_empleados_limpios"],
    },
    {
        "name": "demo_managers_demasiados_reportes",
        "cartridge": "sap_successfactors",
        "description": "[DEMO PACK] Managers con más de 15 reportes directos.",
        "sources": ["demo_silver.sap_empleados_limpios"],
    },
    {
        "name": "demo_posiciones_vacantes",
        "cartridge": "sap_successfactors",
        "description": "[DEMO PACK] Posiciones marcadas como vacantes.",
        "sources": ["demo_bronze.sap_posiciones"],
    },
    # ── S/4HANA ──
    {
        "name": "demo_spend_por_proveedor",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Spend anual por proveedor.",
        "sources": ["demo_silver.sap_proveedores_riesgo"],
    },
    {
        "name": "demo_spend_por_categoria",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Spend total por categoría de proveedor.",
        "sources": ["demo_silver.sap_proveedores_riesgo"],
    },
    {
        "name": "demo_ordenes_abiertas",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Órdenes de compra abiertas con monto.",
        "sources": ["demo_silver.sap_ordenes_compra_limpias"],
    },
    {
        "name": "demo_facturas_vencidas_sap",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Facturas de proveedor vencidas.",
        "sources": ["demo_bronze.sap_facturas_proveedor"],
    },
    {
        "name": "demo_proveedores_riesgo_alto",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Proveedores marcados como Alto riesgo.",
        "sources": ["demo_silver.sap_proveedores_riesgo"],
    },
    {
        "name": "demo_revenue_por_customer",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Revenue por cliente SAP.",
        "sources": ["demo_bronze.sap_ventas", "demo_bronze.sap_customers"],
    },
    {
        "name": "demo_margen_por_categoria",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Margen por categoría de material.",
        "sources": ["demo_bronze.sap_ventas", "demo_bronze.sap_materiales"],
    },
    {
        "name": "demo_gl_balance_por_cuenta",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Balance de GL por cuenta contable.",
        "sources": ["demo_bronze.sap_journal_entries"],
    },
    {
        "name": "demo_gastos_por_centro_coste",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Gasto por centro de coste.",
        "sources": ["demo_bronze.sap_journal_entries"],
    },
    {
        "name": "demo_top_proveedores_spend",
        "cartridge": "sap_s4hana",
        "description": "[DEMO PACK] Top 20 proveedores por spend anual.",
        "sources": ["demo_silver.sap_proveedores_riesgo"],
    },
    # ── Executive summaries ──
    {
        "name": "demo_resumen_cfo",
        "cartridge": "demo_enterprise",
        "description": "[DEMO PACK] Resumen ejecutivo CFO: revenue, coste, margen, facturas vencidas.",
        "sources": ["all"],
    },
    {
        "name": "demo_resumen_operativo",
        "cartridge": "demo_enterprise",
        "description": "[DEMO PACK] Resumen ejecutivo COO: headcount, utilización, proyectos, gasto operativo.",
        "sources": ["all"],
    },
    {
        "name": "demo_resumen_hr",
        "cartridge": "demo_enterprise",
        "description": "[DEMO PACK] Resumen ejecutivo RRHH: headcount, ausencias, bajas, sin centro de coste.",
        "sources": ["all"],
    },
]


# DDL + populate SQL for each gold table.
# Tables include `revenue_manager TEXT DEFAULT 'N/D'` so the RLS regex used by
# refinement (see refinement/app/duckdb_engine.py) returns rows for every user.
def _gold_ddl_and_populate(bronze_cn) -> list[tuple[str, str, str, list]]:
    """Returns list of (name, ddl, populate_sql_for_pggold, rows_to_insert)."""

    out: list[tuple[str, str, str, list]] = []
    cur = bronze_cn.cursor()

    def fetchall(sql: str, params: tuple = ()) -> list[tuple]:
        cur.execute(sql, params)
        return cur.fetchall()

    # demo_revenue_por_cliente
    rows = fetchall("""
        SELECT c.client_id, c.nombre, c.industria, c.pais,
               COALESCE(SUM(i.monto), 0) AS revenue_total,
               COUNT(DISTINCT i.project_id) AS proyectos
        FROM   demo_bronze.replicon_clients c
        LEFT JOIN demo_bronze.replicon_invoices i ON i.client_id = c.client_id
        GROUP BY c.client_id, c.nombre, c.industria, c.pais
        ORDER BY revenue_total DESC
    """)
    out.append((
        "demo_revenue_por_cliente",
        """CREATE TABLE IF NOT EXISTS gold_demo_revenue_por_cliente (
            client_id     TEXT,
            nombre        TEXT,
            industria     TEXT,
            pais          TEXT,
            revenue_total NUMERIC,
            proyectos     INTEGER,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_revenue_por_cliente
           (client_id, nombre, industria, pais, revenue_total, proyectos)
           VALUES %s""",
        rows,
    ))

    # demo_revenue_por_proyecto
    rows = fetchall("""
        SELECT project_id, proyecto, client_id, presupuesto,
               revenue_estimado, coste_estimado,
               revenue_real, coste_real, margen_real, margen_pct,
               sobre_presupuesto, estado, manager
        FROM   demo_silver.replicon_project_finance
    """)
    out.append((
        "demo_revenue_por_proyecto",
        """CREATE TABLE IF NOT EXISTS gold_demo_revenue_por_proyecto (
            project_id        TEXT,
            proyecto          TEXT,
            client_id         TEXT,
            presupuesto       NUMERIC,
            revenue_estimado  NUMERIC,
            coste_estimado    NUMERIC,
            revenue_real      NUMERIC,
            coste_real        NUMERIC,
            margen_real       NUMERIC,
            margen_pct        NUMERIC,
            sobre_presupuesto BOOLEAN,
            estado            TEXT,
            manager           TEXT,
            revenue_manager   TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_revenue_por_proyecto
           (project_id, proyecto, client_id, presupuesto, revenue_estimado,
            coste_estimado, revenue_real, coste_real, margen_real, margen_pct,
            sobre_presupuesto, estado, manager) VALUES %s""",
        rows,
    ))

    # demo_margen_por_proyecto
    rows = fetchall("""
        SELECT project_id, proyecto, margen_real, margen_pct,
               sobre_presupuesto, estado, manager
        FROM   demo_silver.replicon_project_finance
        ORDER BY margen_real DESC
    """)
    out.append((
        "demo_margen_por_proyecto",
        """CREATE TABLE IF NOT EXISTS gold_demo_margen_por_proyecto (
            project_id        TEXT,
            proyecto          TEXT,
            margen_real       NUMERIC,
            margen_pct        NUMERIC,
            sobre_presupuesto BOOLEAN,
            estado            TEXT,
            manager           TEXT,
            revenue_manager   TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_margen_por_proyecto
           (project_id, proyecto, margen_real, margen_pct,
            sobre_presupuesto, estado, manager) VALUES %s""",
        rows,
    ))

    # demo_utilizacion_consultores
    rows = fetchall("""
        SELECT u.user_id, u.nombre, u.departamento, u.rol,
               COALESCE(SUM(t.horas), 0)                              AS horas_totales,
               COALESCE(SUM(CASE WHEN t.billable THEN t.horas ELSE 0 END), 0) AS horas_facturables,
               CASE WHEN COALESCE(SUM(t.horas), 0) = 0 THEN 0
                    ELSE SUM(CASE WHEN t.billable THEN t.horas ELSE 0 END)::numeric
                         / NULLIF(SUM(t.horas), 0)
               END                                                    AS utilizacion
        FROM   demo_bronze.replicon_users u
        LEFT JOIN demo_silver.replicon_timesheets_limpios t ON t.user_id = u.user_id
        GROUP BY u.user_id, u.nombre, u.departamento, u.rol
    """)
    out.append((
        "demo_utilizacion_consultores",
        """CREATE TABLE IF NOT EXISTS gold_demo_utilizacion_consultores (
            user_id          TEXT,
            nombre           TEXT,
            departamento     TEXT,
            rol              TEXT,
            horas_totales    NUMERIC,
            horas_facturables NUMERIC,
            utilizacion      NUMERIC,
            revenue_manager  TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_utilizacion_consultores
           (user_id, nombre, departamento, rol, horas_totales,
            horas_facturables, utilizacion) VALUES %s""",
        rows,
    ))

    # demo_horas_facturables (monthly)
    rows = fetchall("""
        SELECT DATE_TRUNC('month', fecha)::date AS mes,
               SUM(CASE WHEN billable THEN horas ELSE 0 END) AS horas_facturables,
               SUM(CASE WHEN NOT billable THEN horas ELSE 0 END) AS horas_no_facturables,
               SUM(horas) AS horas_totales,
               SUM(CASE WHEN billable THEN horas * tarifa_hora ELSE 0 END) AS revenue
        FROM   demo_bronze.replicon_timesheets
        GROUP BY 1 ORDER BY 1
    """)
    out.append((
        "demo_horas_facturables",
        """CREATE TABLE IF NOT EXISTS gold_demo_horas_facturables (
            mes                  DATE,
            horas_facturables    NUMERIC,
            horas_no_facturables NUMERIC,
            horas_totales        NUMERIC,
            revenue              NUMERIC,
            revenue_manager      TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_horas_facturables
           (mes, horas_facturables, horas_no_facturables, horas_totales, revenue) VALUES %s""",
        rows,
    ))

    # demo_gastos_por_proyecto
    rows = fetchall("""
        SELECT project_id,
               COUNT(*) AS num_gastos,
               SUM(monto) AS gasto_total,
               SUM(CASE WHEN reimbursable THEN monto ELSE 0 END) AS reembolsable
        FROM   demo_bronze.replicon_expenses
        GROUP BY project_id
        ORDER BY gasto_total DESC NULLS LAST
    """)
    out.append((
        "demo_gastos_por_proyecto",
        """CREATE TABLE IF NOT EXISTS gold_demo_gastos_por_proyecto (
            project_id      TEXT,
            num_gastos      INTEGER,
            gasto_total     NUMERIC,
            reembolsable    NUMERIC,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_gastos_por_proyecto
           (project_id, num_gastos, gasto_total, reembolsable) VALUES %s""",
        rows,
    ))

    # demo_facturas_vencidas_replicon
    rows = fetchall("""
        SELECT i.invoice_id, i.client_id, c.nombre AS cliente,
               i.project_id, i.monto, i.fecha_emision, i.fecha_vencimiento,
               i.dias_vencida, i.estado
        FROM   demo_bronze.replicon_invoices i
        LEFT JOIN demo_bronze.replicon_clients c ON c.client_id = i.client_id
        WHERE  i.estado = 'Overdue'
        ORDER BY i.dias_vencida DESC
    """)
    out.append((
        "demo_facturas_vencidas_replicon",
        """CREATE TABLE IF NOT EXISTS gold_demo_facturas_vencidas_replicon (
            invoice_id        TEXT,
            client_id         TEXT,
            cliente           TEXT,
            project_id        TEXT,
            monto             NUMERIC,
            fecha_emision     DATE,
            fecha_vencimiento DATE,
            dias_vencida      INTEGER,
            estado            TEXT,
            revenue_manager   TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_facturas_vencidas_replicon
           (invoice_id, client_id, cliente, project_id, monto,
            fecha_emision, fecha_vencimiento, dias_vencida, estado) VALUES %s""",
        rows,
    ))

    # demo_proyectos_sobre_presupuesto
    rows = fetchall("""
        SELECT project_id, proyecto, presupuesto, coste_real,
               coste_real - presupuesto AS exceso,
               margen_pct, estado, manager
        FROM   demo_silver.replicon_project_finance
        WHERE  sobre_presupuesto = TRUE
        ORDER BY exceso DESC
    """)
    out.append((
        "demo_proyectos_sobre_presupuesto",
        """CREATE TABLE IF NOT EXISTS gold_demo_proyectos_sobre_presupuesto (
            project_id      TEXT,
            proyecto        TEXT,
            presupuesto     NUMERIC,
            coste_real      NUMERIC,
            exceso          NUMERIC,
            margen_pct      NUMERIC,
            estado          TEXT,
            manager         TEXT,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_proyectos_sobre_presupuesto
           (project_id, proyecto, presupuesto, coste_real, exceso,
            margen_pct, estado, manager) VALUES %s""",
        rows,
    ))

    # demo_top_clientes_revenue
    rows = fetchall("""
        SELECT c.client_id, c.nombre, c.industria,
               COALESCE(SUM(i.monto), 0) AS revenue
        FROM   demo_bronze.replicon_clients c
        LEFT JOIN demo_bronze.replicon_invoices i ON i.client_id = c.client_id
        GROUP BY c.client_id, c.nombre, c.industria
        ORDER BY revenue DESC
        LIMIT 20
    """)
    out.append((
        "demo_top_clientes_revenue",
        """CREATE TABLE IF NOT EXISTS gold_demo_top_clientes_revenue (
            client_id       TEXT,
            nombre          TEXT,
            industria       TEXT,
            revenue         NUMERIC,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_top_clientes_revenue
           (client_id, nombre, industria, revenue) VALUES %s""",
        rows,
    ))

    # demo_consultores_baja_utilizacion
    rows = fetchall("""
        SELECT u.user_id, u.nombre, u.departamento,
               COALESCE(SUM(t.horas), 0) AS horas_totales,
               COALESCE(SUM(CASE WHEN t.billable THEN t.horas ELSE 0 END), 0) AS horas_facturables,
               CASE WHEN COALESCE(SUM(t.horas), 0) = 0 THEN 0
                    ELSE SUM(CASE WHEN t.billable THEN t.horas ELSE 0 END)::numeric
                         / NULLIF(SUM(t.horas), 0)
               END AS utilizacion
        FROM   demo_bronze.replicon_users u
        LEFT JOIN demo_silver.replicon_timesheets_limpios t ON t.user_id = u.user_id
        GROUP BY u.user_id, u.nombre, u.departamento
        HAVING COALESCE(SUM(t.horas), 0) > 0
        AND    SUM(CASE WHEN t.billable THEN t.horas ELSE 0 END)::numeric
               / NULLIF(SUM(t.horas), 0) < 0.5
    """)
    out.append((
        "demo_consultores_baja_utilizacion",
        """CREATE TABLE IF NOT EXISTS gold_demo_consultores_baja_utilizacion (
            user_id           TEXT,
            nombre            TEXT,
            departamento      TEXT,
            horas_totales     NUMERIC,
            horas_facturables NUMERIC,
            utilizacion       NUMERIC,
            revenue_manager   TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_consultores_baja_utilizacion
           (user_id, nombre, departamento, horas_totales,
            horas_facturables, utilizacion) VALUES %s""",
        rows,
    ))

    # ─── HR ───
    rows = fetchall("""
        SELECT departamento, COUNT(*) AS headcount
        FROM   demo_silver.sap_empleados_limpios
        WHERE  status = 'Active'
        GROUP BY departamento
        ORDER BY headcount DESC
    """)
    out.append((
        "demo_headcount_por_departamento",
        """CREATE TABLE IF NOT EXISTS gold_demo_headcount_por_departamento (
            departamento    TEXT,
            headcount       INTEGER,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_headcount_por_departamento
           (departamento, headcount) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT ubicacion, COUNT(*) AS headcount
        FROM   demo_silver.sap_empleados_limpios
        WHERE  status = 'Active'
        GROUP BY ubicacion
        ORDER BY headcount DESC
    """)
    out.append((
        "demo_headcount_por_ubicacion",
        """CREATE TABLE IF NOT EXISTS gold_demo_headcount_por_ubicacion (
            ubicacion       TEXT,
            headcount       INTEGER,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_headcount_por_ubicacion
           (ubicacion, headcount) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT departamento,
               COUNT(*) AS headcount,
               AVG(salario)::numeric(12,2) AS salario_promedio,
               MIN(salario) AS salario_min,
               MAX(salario) AS salario_max
        FROM   demo_silver.sap_empleados_limpios
        WHERE  status = 'Active'
        GROUP BY departamento
        ORDER BY salario_promedio DESC
    """)
    out.append((
        "demo_salario_promedio",
        """CREATE TABLE IF NOT EXISTS gold_demo_salario_promedio (
            departamento     TEXT,
            headcount        INTEGER,
            salario_promedio NUMERIC,
            salario_min      NUMERIC,
            salario_max      NUMERIC,
            revenue_manager  TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_salario_promedio
           (departamento, headcount, salario_promedio, salario_min, salario_max) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT employee_id, nombre, departamento, posicion, manager,
               ubicacion, salario, fecha_alta
        FROM   demo_silver.sap_empleados_limpios
        WHERE  status = 'Active'
        AND   (centro_coste IS NULL OR centro_coste = '')
    """)
    out.append((
        "demo_empleados_sin_centro_coste",
        """CREATE TABLE IF NOT EXISTS gold_demo_empleados_sin_centro_coste (
            employee_id     TEXT,
            nombre          TEXT,
            departamento    TEXT,
            posicion        TEXT,
            manager         TEXT,
            ubicacion       TEXT,
            salario         NUMERIC,
            fecha_alta      DATE,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_empleados_sin_centro_coste
           (employee_id, nombre, departamento, posicion, manager, ubicacion,
            salario, fecha_alta) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT employee_id, nombre, departamento, fecha_baja, manager
        FROM   demo_silver.sap_empleados_limpios
        WHERE  fecha_baja IS NOT NULL
        AND    fecha_baja >= CURRENT_DATE - INTERVAL '90 days'
        ORDER BY fecha_baja DESC
    """)
    out.append((
        "demo_bajas_recientes",
        """CREATE TABLE IF NOT EXISTS gold_demo_bajas_recientes (
            employee_id     TEXT,
            nombre          TEXT,
            departamento    TEXT,
            fecha_baja      DATE,
            manager         TEXT,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_bajas_recientes
           (employee_id, nombre, departamento, fecha_baja, manager) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT e.departamento, COUNT(a.absence_id) AS num_ausencias,
               COALESCE(SUM(a.dias), 0) AS dias_totales
        FROM   demo_silver.sap_empleados_limpios e
        LEFT JOIN demo_bronze.sap_ausencias a ON a.employee_id = e.employee_id
        GROUP BY e.departamento
        ORDER BY dias_totales DESC
    """)
    out.append((
        "demo_ausencias_por_departamento",
        """CREATE TABLE IF NOT EXISTS gold_demo_ausencias_por_departamento (
            departamento    TEXT,
            num_ausencias   INTEGER,
            dias_totales    NUMERIC,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_ausencias_por_departamento
           (departamento, num_ausencias, dias_totales) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT manager, COUNT(*) AS reportes
        FROM   demo_silver.sap_empleados_limpios
        WHERE  status = 'Active' AND manager IS NOT NULL
        GROUP BY manager
        HAVING COUNT(*) > 15
        ORDER BY reportes DESC
    """)
    out.append((
        "demo_managers_demasiados_reportes",
        """CREATE TABLE IF NOT EXISTS gold_demo_managers_demasiados_reportes (
            manager         TEXT,
            reportes        INTEGER,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_managers_demasiados_reportes
           (manager, reportes) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT posicion_id, titulo, departamento, nivel
        FROM   demo_bronze.sap_posiciones
        WHERE  vacante = TRUE
        ORDER BY departamento, nivel
    """)
    out.append((
        "demo_posiciones_vacantes",
        """CREATE TABLE IF NOT EXISTS gold_demo_posiciones_vacantes (
            posicion_id     TEXT,
            titulo          TEXT,
            departamento    TEXT,
            nivel           INTEGER,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_posiciones_vacantes
           (posicion_id, titulo, departamento, nivel) VALUES %s""",
        rows,
    ))

    # ─── S/4HANA ───
    rows = fetchall("""
        SELECT supplier_id, nombre, pais, categoria, spend_anual,
               facturas_vencidas, riesgo
        FROM   demo_silver.sap_proveedores_riesgo
        ORDER BY spend_anual DESC
    """)
    out.append((
        "demo_spend_por_proveedor",
        """CREATE TABLE IF NOT EXISTS gold_demo_spend_por_proveedor (
            supplier_id        TEXT,
            nombre             TEXT,
            pais               TEXT,
            categoria          TEXT,
            spend_anual        NUMERIC,
            facturas_vencidas  INTEGER,
            riesgo             TEXT,
            revenue_manager    TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_spend_por_proveedor
           (supplier_id, nombre, pais, categoria, spend_anual,
            facturas_vencidas, riesgo) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT categoria,
               COUNT(*) AS proveedores,
               SUM(spend_anual) AS spend_total,
               AVG(spend_anual)::numeric(12,2) AS spend_promedio
        FROM   demo_silver.sap_proveedores_riesgo
        GROUP BY categoria
        ORDER BY spend_total DESC
    """)
    out.append((
        "demo_spend_por_categoria",
        """CREATE TABLE IF NOT EXISTS gold_demo_spend_por_categoria (
            categoria       TEXT,
            proveedores     INTEGER,
            spend_total     NUMERIC,
            spend_promedio  NUMERIC,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_spend_por_categoria
           (categoria, proveedores, spend_total, spend_promedio) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT po_id, supplier_id, comprador, categoria, monto, estado, fecha
        FROM   demo_silver.sap_ordenes_compra_limpias
        WHERE  estado IN ('Open', 'PartiallyReceived')
        ORDER BY monto DESC
    """)
    out.append((
        "demo_ordenes_abiertas",
        """CREATE TABLE IF NOT EXISTS gold_demo_ordenes_abiertas (
            po_id           TEXT,
            supplier_id     TEXT,
            comprador       TEXT,
            categoria       TEXT,
            monto           NUMERIC,
            estado          TEXT,
            fecha           DATE,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_ordenes_abiertas
           (po_id, supplier_id, comprador, categoria, monto, estado, fecha) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT invoice_id, supplier_id, monto, estado, fecha_emision,
               fecha_vencimiento, dias_vencida
        FROM   demo_bronze.sap_facturas_proveedor
        WHERE  estado = 'Overdue'
        ORDER BY dias_vencida DESC
    """)
    out.append((
        "demo_facturas_vencidas_sap",
        """CREATE TABLE IF NOT EXISTS gold_demo_facturas_vencidas_sap (
            invoice_id        TEXT,
            supplier_id       TEXT,
            monto             NUMERIC,
            estado            TEXT,
            fecha_emision     DATE,
            fecha_vencimiento DATE,
            dias_vencida      INTEGER,
            revenue_manager   TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_facturas_vencidas_sap
           (invoice_id, supplier_id, monto, estado, fecha_emision,
            fecha_vencimiento, dias_vencida) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT supplier_id, nombre, pais, categoria, spend_anual,
               facturas_vencidas
        FROM   demo_silver.sap_proveedores_riesgo
        WHERE  riesgo = 'Alto'
        ORDER BY spend_anual DESC
    """)
    out.append((
        "demo_proveedores_riesgo_alto",
        """CREATE TABLE IF NOT EXISTS gold_demo_proveedores_riesgo_alto (
            supplier_id        TEXT,
            nombre             TEXT,
            pais               TEXT,
            categoria          TEXT,
            spend_anual        NUMERIC,
            facturas_vencidas  INTEGER,
            revenue_manager    TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_proveedores_riesgo_alto
           (supplier_id, nombre, pais, categoria, spend_anual,
            facturas_vencidas) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT c.customer_id, c.nombre, c.industria, c.pais,
               COALESCE(SUM(v.monto), 0) AS revenue,
               COUNT(v.sales_order_id) AS num_ventas
        FROM   demo_bronze.sap_customers c
        LEFT JOIN demo_bronze.sap_ventas v ON v.customer_id = c.customer_id
        GROUP BY c.customer_id, c.nombre, c.industria, c.pais
        ORDER BY revenue DESC
    """)
    out.append((
        "demo_revenue_por_customer",
        """CREATE TABLE IF NOT EXISTS gold_demo_revenue_por_customer (
            customer_id     TEXT,
            nombre          TEXT,
            industria       TEXT,
            pais            TEXT,
            revenue         NUMERIC,
            num_ventas      INTEGER,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_revenue_por_customer
           (customer_id, nombre, industria, pais, revenue, num_ventas) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT m.categoria,
               COUNT(v.sales_order_id) AS ventas,
               SUM(v.monto) AS revenue,
               SUM(v.margen) AS margen_total,
               CASE WHEN SUM(v.monto) = 0 THEN 0
                    ELSE SUM(v.margen) / NULLIF(SUM(v.monto), 0)
               END AS margen_pct
        FROM   demo_bronze.sap_ventas v
        JOIN   demo_bronze.sap_materiales m ON m.material_id = v.material_id
        GROUP BY m.categoria
        ORDER BY revenue DESC
    """)
    out.append((
        "demo_margen_por_categoria",
        """CREATE TABLE IF NOT EXISTS gold_demo_margen_por_categoria (
            categoria       TEXT,
            ventas          INTEGER,
            revenue         NUMERIC,
            margen_total    NUMERIC,
            margen_pct      NUMERIC,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_margen_por_categoria
           (categoria, ventas, revenue, margen_total, margen_pct) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT cuenta_contable,
               COUNT(*) AS num_asientos,
               SUM(CASE WHEN tipo = 'Debit' THEN monto ELSE -monto END) AS balance,
               SUM(CASE WHEN tipo = 'Debit' THEN monto ELSE 0 END) AS debitos,
               SUM(CASE WHEN tipo = 'Credit' THEN monto ELSE 0 END) AS creditos
        FROM   demo_bronze.sap_journal_entries
        GROUP BY cuenta_contable
        ORDER BY ABS(SUM(CASE WHEN tipo = 'Debit' THEN monto ELSE -monto END)) DESC
    """)
    out.append((
        "demo_gl_balance_por_cuenta",
        """CREATE TABLE IF NOT EXISTS gold_demo_gl_balance_por_cuenta (
            cuenta_contable TEXT,
            num_asientos    INTEGER,
            balance         NUMERIC,
            debitos         NUMERIC,
            creditos        NUMERIC,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_gl_balance_por_cuenta
           (cuenta_contable, num_asientos, balance, debitos, creditos) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT centro_coste,
               COUNT(*) AS num_asientos,
               SUM(CASE WHEN tipo = 'Debit' THEN monto ELSE 0 END) AS gasto_total
        FROM   demo_bronze.sap_journal_entries
        WHERE  centro_coste IS NOT NULL
        GROUP BY centro_coste
        ORDER BY gasto_total DESC
    """)
    out.append((
        "demo_gastos_por_centro_coste",
        """CREATE TABLE IF NOT EXISTS gold_demo_gastos_por_centro_coste (
            centro_coste    TEXT,
            num_asientos    INTEGER,
            gasto_total     NUMERIC,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_gastos_por_centro_coste
           (centro_coste, num_asientos, gasto_total) VALUES %s""",
        rows,
    ))

    rows = fetchall("""
        SELECT supplier_id, nombre, pais, categoria, spend_anual, riesgo
        FROM   demo_silver.sap_proveedores_riesgo
        ORDER BY spend_anual DESC
        LIMIT 20
    """)
    out.append((
        "demo_top_proveedores_spend",
        """CREATE TABLE IF NOT EXISTS gold_demo_top_proveedores_spend (
            supplier_id     TEXT,
            nombre          TEXT,
            pais            TEXT,
            categoria       TEXT,
            spend_anual     NUMERIC,
            riesgo          TEXT,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_top_proveedores_spend
           (supplier_id, nombre, pais, categoria, spend_anual, riesgo) VALUES %s""",
        rows,
    ))

    # ─── Executive summaries ───
    summary = fetchall("""
        SELECT
            (SELECT COALESCE(SUM(monto), 0) FROM demo_bronze.replicon_invoices)               AS replicon_revenue,
            (SELECT COALESCE(SUM(horas * coste_hora), 0) FROM demo_bronze.replicon_timesheets) AS replicon_coste,
            (SELECT COALESCE(SUM(monto), 0) FROM demo_bronze.replicon_invoices WHERE estado='Overdue') AS replicon_facturas_vencidas,
            (SELECT COUNT(*) FROM demo_bronze.replicon_invoices WHERE estado='Overdue')       AS replicon_num_vencidas,
            (SELECT COALESCE(SUM(monto), 0) FROM demo_bronze.sap_ventas)                     AS sap_revenue,
            (SELECT COALESCE(SUM(monto), 0) FROM demo_bronze.sap_facturas_proveedor WHERE estado='Overdue') AS sap_facturas_vencidas,
            (SELECT COUNT(*) FROM demo_silver.replicon_project_finance WHERE sobre_presupuesto)            AS proyectos_sobre_presupuesto,
            (SELECT COUNT(*) FROM demo_silver.sap_proveedores_riesgo WHERE riesgo='Alto')     AS proveedores_alto_riesgo
    """)
    s = summary[0] if summary else (0,)*8
    cfo_rows = [
        ("Revenue Replicon",         float(s[0] or 0), "USD", "Total facturado en Replicon"),
        ("Coste Replicon",           float(s[1] or 0), "USD", "Coste total registrado en timesheets"),
        ("Margen bruto Replicon",    float((s[0] or 0)-(s[1] or 0)), "USD", "Revenue - Coste"),
        ("Facturas vencidas Replicon", float(s[2] or 0), "USD", f"{s[3]} facturas pendientes"),
        ("Revenue SAP S/4HANA",      float(s[4] or 0), "USD", "Total de sales orders"),
        ("Facturas vencidas SAP",    float(s[5] or 0), "USD", "Facturas de proveedor en mora"),
        ("Proyectos sobre presupuesto", float(s[6] or 0), "count", "Proyectos cuyo coste supera el presupuesto"),
        ("Proveedores de alto riesgo", float(s[7] or 0), "count", "Proveedores marcados como Alto riesgo"),
    ]
    out.append((
        "demo_resumen_cfo",
        """CREATE TABLE IF NOT EXISTS gold_demo_resumen_cfo (
            kpi             TEXT,
            valor           NUMERIC,
            unidad          TEXT,
            detalle         TEXT,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_resumen_cfo (kpi, valor, unidad, detalle) VALUES %s""",
        cfo_rows,
    ))

    op = fetchall("""
        SELECT
            (SELECT COUNT(*) FROM demo_silver.sap_empleados_limpios WHERE status='Active') AS headcount_activo,
            (SELECT COUNT(*) FROM demo_silver.replicon_project_finance WHERE estado='InProgress') AS proyectos_activos,
            (SELECT COUNT(*) FROM demo_silver.sap_ordenes_compra_limpias WHERE estado='Open') AS ordenes_abiertas,
            (SELECT COALESCE(SUM(monto), 0) FROM demo_silver.sap_ordenes_compra_limpias WHERE estado='Open') AS monto_ordenes_abiertas,
            (SELECT COALESCE(AVG(
                CASE WHEN total_h = 0 THEN 0 ELSE bill_h::numeric / NULLIF(total_h, 0) END
            ), 0) FROM (
                SELECT SUM(horas) AS total_h,
                       SUM(CASE WHEN billable THEN horas ELSE 0 END) AS bill_h
                FROM demo_bronze.replicon_timesheets
                GROUP BY user_id
            ) u) AS utilizacion_promedio
    """)
    op_s = op[0] if op else (0,)*5
    op_rows = [
        ("Headcount activo",          float(op_s[0] or 0), "count", "Empleados con status Active"),
        ("Proyectos activos",         float(op_s[1] or 0), "count", "Proyectos en InProgress"),
        ("Órdenes de compra abiertas", float(op_s[2] or 0), "count", f"Monto total: {float(op_s[3] or 0):,.0f} USD"),
        ("Monto órdenes abiertas",    float(op_s[3] or 0), "USD", ""),
        ("Utilización promedio",      float(op_s[4] or 0), "ratio", "Promedio de utilización facturable por consultor"),
    ]
    out.append((
        "demo_resumen_operativo",
        """CREATE TABLE IF NOT EXISTS gold_demo_resumen_operativo (
            kpi             TEXT,
            valor           NUMERIC,
            unidad          TEXT,
            detalle         TEXT,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_resumen_operativo (kpi, valor, unidad, detalle) VALUES %s""",
        op_rows,
    ))

    hr = fetchall("""
        SELECT
            (SELECT COUNT(*) FROM demo_silver.sap_empleados_limpios WHERE status='Active') AS headcount,
            (SELECT COUNT(*) FROM demo_silver.sap_empleados_limpios WHERE status='Active' AND (centro_coste IS NULL OR centro_coste='')) AS sin_cc,
            (SELECT COUNT(*) FROM demo_silver.sap_empleados_limpios WHERE fecha_baja >= CURRENT_DATE - INTERVAL '90 days') AS bajas_90d,
            (SELECT COALESCE(SUM(dias), 0) FROM demo_bronze.sap_ausencias) AS dias_ausencia,
            (SELECT COUNT(*) FROM demo_bronze.sap_posiciones WHERE vacante) AS vacantes
    """)
    hr_s = hr[0] if hr else (0,)*5
    hr_rows = [
        ("Headcount activo",       float(hr_s[0] or 0), "count", "Empleados con status Active"),
        ("Empleados sin centro de coste", float(hr_s[1] or 0), "count", "Necesitan asignación de centro de coste"),
        ("Bajas últimos 90 días",  float(hr_s[2] or 0), "count", "Empleados con fecha_baja en últimos 90 días"),
        ("Días de ausencia total", float(hr_s[3] or 0), "días", "Suma de todos los registros de ausencia"),
        ("Posiciones vacantes",    float(hr_s[4] or 0), "count", "Posiciones marcadas como vacantes"),
    ]
    out.append((
        "demo_resumen_hr",
        """CREATE TABLE IF NOT EXISTS gold_demo_resumen_hr (
            kpi             TEXT,
            valor           NUMERIC,
            unidad          TEXT,
            detalle         TEXT,
            revenue_manager TEXT DEFAULT 'N/D'
        );""",
        """INSERT INTO gold_demo_resumen_hr (kpi, valor, unidad, detalle) VALUES %s""",
        hr_rows,
    ))

    cur.close()
    return out


# ─── Semantic terms and RAG knowledge bits ───────────────────────────────────

SEMANTIC_TERMS = [
    # (cartridge, term, definition, maps_to)
    ("demo_enterprise", "revenue",
     "Ingresos facturados o reconocidos. En Replicon: suma de monto en invoices o horas*tarifa en timesheets billable. En SAP S/4HANA: suma de monto en sales orders.",
     "demo_bronze.replicon_invoices.monto + demo_bronze.sap_ventas.monto"),
    ("demo_enterprise", "coste",
     "Coste registrado (horas * coste_hora en Replicon, asientos de tipo Debit en cuentas 5000-5999 en GL).",
     "demo_bronze.replicon_timesheets.horas*coste_hora + demo_bronze.sap_journal_entries WHERE cuenta IN (5000..)"),
    ("demo_enterprise", "margen",
     "Diferencia entre revenue y coste por proyecto, cliente o categoría.",
     "revenue - coste"),
    ("demo_enterprise", "margen_bruto",
     "Margen agregado a nivel compañía: revenue total - coste total.",
     "SUM(revenue) - SUM(coste)"),
    ("demo_enterprise", "utilización",
     "Porcentaje de horas facturables sobre horas totales por consultor.",
     "SUM(horas WHERE billable) / SUM(horas)"),
    ("demo_enterprise", "horas_facturables",
     "Horas registradas con flag billable=TRUE en demo_bronze.replicon_timesheets.",
     "demo_bronze.replicon_timesheets.horas WHERE billable=TRUE"),
    ("demo_enterprise", "proyecto_sobre_presupuesto",
     "Proyecto cuyo coste real ha excedido el presupuesto aprobado.",
     "demo_silver.replicon_project_finance.sobre_presupuesto=TRUE"),
    ("demo_enterprise", "factura_vencida",
     "Factura cuya fecha de vencimiento es anterior a hoy y aún no está pagada.",
     "estado='Overdue' OR (estado='Open' AND fecha_vencimiento < CURRENT_DATE)"),
    ("demo_enterprise", "headcount_activo",
     "Empleados con status='Active' y fecha_baja IS NULL.",
     "demo_silver.sap_empleados_limpios WHERE status='Active'"),
    ("demo_enterprise", "empleado_sin_centro_coste",
     "Empleado activo cuyo campo centro_coste está vacío o NULL.",
     "status='Active' AND (centro_coste IS NULL OR centro_coste='')"),
    ("demo_enterprise", "spend",
     "Gasto total con un proveedor o categoría (suma de spend_anual en proveedores, o monto en POs cerradas).",
     "demo_silver.sap_proveedores_riesgo.spend_anual"),
    ("demo_enterprise", "proveedor_riesgo_alto",
     "Proveedor cuya columna riesgo está marcada como 'Alto' (por país, scoring interno o concentración).",
     "demo_silver.sap_proveedores_riesgo.riesgo='Alto'"),
    ("demo_enterprise", "orden_compra_abierta",
     "Purchase order con estado Open o PartiallyReceived (aún no completamente recibida ni cerrada).",
     "estado IN ('Open','PartiallyReceived')"),
    ("demo_enterprise", "centro_coste",
     "Unidad organizativa a la que se imputan costes/gastos. Asignada a empleados y a líneas de journal entry.",
     "demo_bronze.sap_centros_coste"),
    ("demo_enterprise", "cuenta_contable",
     "Cuenta del plan contable. 4xxx revenue, 5xxx coste/COGS, 6xxx gasto operativo, 7xxx no operativo.",
     "demo_bronze.sap_journal_entries.cuenta_contable"),
    ("demo_enterprise", "CFO_summary",
     "KPIs financieros agregados: revenue, coste, margen, facturas vencidas, proveedores alto riesgo.",
     "gold_demo_resumen_cfo"),
    ("demo_enterprise", "COO_summary",
     "KPIs operativos: headcount, utilización, proyectos activos, órdenes de compra abiertas.",
     "gold_demo_resumen_operativo"),
    ("demo_enterprise", "HR_summary",
     "KPIs de RRHH: headcount, sin centro de coste, bajas recientes, ausencias, vacantes.",
     "gold_demo_resumen_hr"),
]


KNOWLEDGE_BITS = [
    # (name, description, content)
    ("[DEMO PACK] Margen por proyecto",
     "Cómo interpretar margen por proyecto Replicon",
     """Cómo interpretar el margen por proyecto (demo enterprise)

El dataset gold_demo_margen_por_proyecto contiene el margen real (revenue_real
- coste_real) y el margen porcentual por cada proyecto Replicon.

Interpretación:
- margen_pct > 25%: proyecto saludable
- margen_pct entre 10% y 25%: aceptable, monitorear
- margen_pct < 10%: en riesgo, requiere revisión
- margen_real negativo: proyecto en pérdida — escalar a finanzas

La columna sobre_presupuesto=TRUE indica que el coste registrado ya superó
el presupuesto aprobado.

Preguntas típicas:
- "¿Qué proyectos tienen margen negativo?"
- "Top 10 proyectos con peor margen"
- "Margen promedio por manager"
"""),
    ("[DEMO PACK] Utilización de consultores",
     "Cómo interpretar utilización Replicon",
     """Cómo interpretar la utilización de consultores

Utilización = horas_facturables / horas_totales registradas.

Benchmarks típicos en consultoría:
- > 80%: alta utilización (riesgo de burnout)
- 60-80%: rango saludable
- 40-60%: consultor parcialmente subutilizado
- < 40%: subutilización crítica — revisar pipeline o reasignar

Dataset: gold_demo_utilizacion_consultores.
Dataset filtrado: gold_demo_consultores_baja_utilizacion (< 50%).

Considerar también horas_no_facturables (administrativas, capacitación,
pre-venta) antes de marcar a alguien como baja utilización.
"""),
    ("[DEMO PACK] Proyectos sobre presupuesto",
     "Cómo detectar y actuar sobre proyectos sobre presupuesto",
     """Cómo detectar proyectos sobre presupuesto

gold_demo_proyectos_sobre_presupuesto lista proyectos cuyo coste_real
ya excedió el presupuesto aprobado. Columna `exceso` = coste_real - presupuesto.

Acciones recomendadas:
1. Revisar scope creep (cambios no facturados)
2. Verificar bloqueos/dependencias que extendieron timeline
3. Validar tarifas y costes_hora cargados
4. Evaluar Change Request hacia el cliente

KPI relacionado: margen_pct. Un proyecto sobre presupuesto con margen_pct
todavía positivo puede ser aceptable. Con margen_pct < 0 es prioritario.
"""),
    ("[DEMO PACK] Empleado sin centro de coste",
     "Qué significa empleado sin centro de coste",
     """Qué significa un empleado sin centro de coste

Cada empleado debe estar asignado a un centro_coste para que el gasto
salarial se impute correctamente a la unidad organizativa que lo consume.

Dataset: gold_demo_empleados_sin_centro_coste.

Implicaciones:
- El coste salarial cae en una bolsa general sin atribución
- Reporting de gastos por departamento queda incompleto
- Auditorías financieras lo marcan como hallazgo

Acción:
- RRHH debe completar centro_coste antes del cierre del mes
- Validar nuevas altas en el flujo de onboarding
"""),
    ("[DEMO PACK] Proveedor de alto riesgo",
     "Qué significa proveedor de alto riesgo",
     """Qué significa un proveedor de alto riesgo

Dataset: gold_demo_proveedores_riesgo_alto.

Un proveedor es marcado como riesgo Alto cuando uno o más factores aplican:
- Concentración de spend (>20% del spend de su categoría)
- País con score geopolítico bajo
- Facturas vencidas recurrentes
- Sin contrato vigente o vencido

Recomendaciones:
- Buscar 2-3 alternativas por categoría
- Validar continuidad de servicio
- Renegociar términos de pago
- Activar revisión de compliance
"""),
    ("[DEMO PACK] Facturas vencidas",
     "Qué revisar en facturas vencidas",
     """Qué revisar en facturas vencidas

Datasets: gold_demo_facturas_vencidas_replicon (cobranza),
          gold_demo_facturas_vencidas_sap        (cuentas por pagar)

Para Cuentas por cobrar (Replicon):
- Priorizar por monto * dias_vencida
- Cliente recurrente con problemas → escalado comercial
- Validar disputa o problema técnico antes de cobranza dura

Para Cuentas por pagar (SAP):
- Riesgo de penalización contractual o corte de servicio
- Aprovechar para renegociar términos si hay liquidez
"""),
    ("[DEMO PACK] Órdenes de compra abiertas",
     "Qué revisar en órdenes de compra abiertas",
     """Qué revisar en órdenes de compra abiertas

Dataset: gold_demo_ordenes_abiertas.

Una PO con estado Open o PartiallyReceived consume compromiso presupuestal
pero todavía no se reflejó como gasto. Revisar:
- Antigüedad > 90 días: posiblemente debería cerrarse o cancelarse
- Monto alto + sin recepción parcial: validar con el comprador
- Compromiso vs presupuesto disponible del centro de coste
"""),
    ("[DEMO PACK] Qué debe mirar un CFO",
     "Checklist CFO con datasets demo enterprise",
     """Checklist CFO con datasets demo enterprise

1. Revenue y margen
   - gold_demo_revenue_por_cliente
   - gold_demo_margen_por_proyecto
   - gold_demo_resumen_cfo

2. Cobranzas y pagos
   - gold_demo_facturas_vencidas_replicon (AR)
   - gold_demo_facturas_vencidas_sap (AP)

3. Coste y eficiencia
   - gold_demo_gl_balance_por_cuenta
   - gold_demo_gastos_por_centro_coste
   - gold_demo_spend_por_categoria

4. Riesgos
   - gold_demo_proveedores_riesgo_alto
   - gold_demo_proyectos_sobre_presupuesto
"""),
    ("[DEMO PACK] Qué debe mirar un COO",
     "Checklist COO con datasets demo enterprise",
     """Checklist COO con datasets demo enterprise

Operación:
- gold_demo_resumen_operativo (KPIs agregados)
- gold_demo_utilizacion_consultores
- gold_demo_horas_facturables (tendencia mensual)
- gold_demo_proyectos_sobre_presupuesto

Cadena de suministro:
- gold_demo_ordenes_abiertas
- gold_demo_top_proveedores_spend

Top riesgos:
- Consultores con baja utilización (gold_demo_consultores_baja_utilizacion)
- Proveedores únicos en categorías críticas
- Managers con > 15 reportes directos (cuello de botella)
"""),
    ("[DEMO PACK] Qué debe mirar RRHH",
     "Checklist HR con datasets demo enterprise",
     """Checklist HR con datasets demo enterprise

Headcount y estructura:
- gold_demo_headcount_por_departamento
- gold_demo_headcount_por_ubicacion
- gold_demo_managers_demasiados_reportes
- gold_demo_posiciones_vacantes

Compensación:
- gold_demo_salario_promedio

Calidad de datos / compliance:
- gold_demo_empleados_sin_centro_coste

Rotación y ausencias:
- gold_demo_bajas_recientes
- gold_demo_ausencias_por_departamento

Resumen ejecutivo:
- gold_demo_resumen_hr
"""),
    ("[DEMO PACK] Top 10 riesgos operativos",
     "Riesgos operativos típicos a vigilar",
     """Top 10 riesgos operativos a vigilar (demo enterprise)

1. Concentración de revenue en un cliente — ver gold_demo_top_clientes_revenue
2. Proyectos sobre presupuesto con margen negativo
3. Consultores con > 90% utilización (riesgo de burnout)
4. Consultores con < 40% utilización (subutilización)
5. Facturas vencidas a clientes con > 90 días
6. Proveedores únicos de alto riesgo en categorías críticas
7. Empleados sin centro de coste asignado
8. Managers con > 20 reportes directos
9. Centros de coste con gasto anómalo (>2x mediana)
10. Posiciones vacantes > 90 días en departamentos críticos
"""),
]


# ─── Generation helpers ──────────────────────────────────────────────────────

def _batch(rows: list, size: int) -> Iterable[list]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _insert(cn, sql: str, rows: list, page_size: int = 5000) -> int:
    if not rows:
        return 0
    n = 0
    with cn.cursor() as cur:
        for chunk in _batch(rows, page_size):
            execute_values(cur, sql, chunk, page_size=page_size)
            n += len(chunk)
    return n


# ─── Replicon data ───────────────────────────────────────────────────────────

def gen_replicon(cn, scale: float) -> dict:
    rng = random.Random(42)
    counts = {
        "users":       int(2_000   * scale),
        "clients":     int(300     * scale),
        "projects":    int(2_000   * scale),
        "tasks":       int(10_000  * scale),
        "assignments": int(8_000   * scale),
        "timesheets":  int(100_000 * scale),
        "expenses":    int(20_000  * scale),
        "invoices":    int(15_000  * scale),
    }
    now = dt.datetime.now(dt.timezone.utc)

    _log(f"Replicon: generating users ({counts['users']:,})")
    users = []
    for i in range(counts["users"]):
        rate = rng.choice([45, 65, 85, 110, 140, 180, 220])
        coste = rate * rng.uniform(0.45, 0.7)
        users.append((
            f"demo_user_{i:05d}",
            _name(rng),
            rng.choice(ROLES),
            rng.choice(DEPARTMENTS),
            rng.choice(CITIES),
            float(rate),
            round(float(coste), 2),
            rng.choices(["Active", "Inactive"], weights=[92, 8])[0],
            (now - dt.timedelta(days=rng.randint(30, 365*5))).date(),
            now - dt.timedelta(hours=rng.randint(1, 24*30)),
        ))

    _log(f"Replicon: generating clients ({counts['clients']:,})")
    clients = []
    for i in range(counts["clients"]):
        clients.append((
            f"demo_client_{i:04d}",
            _company(rng, "Cliente"),
            rng.choice(INDUSTRIES),
            rng.choice(COUNTRIES),
            rng.choices(["Bajo", "Medio", "Alto"], weights=[70, 22, 8])[0],
            now - dt.timedelta(hours=rng.randint(1, 24*60)),
        ))

    _log(f"Replicon: generating projects ({counts['projects']:,})")
    projects = []
    for i in range(counts["projects"]):
        client = rng.choice(clients)
        manager = rng.choice(users)
        presupuesto = float(rng.choice([50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000]))
        revenue_est = presupuesto * rng.uniform(1.05, 1.4)
        coste_est = presupuesto * rng.uniform(0.6, 0.95)
        start = (now - dt.timedelta(days=rng.randint(0, 365*2))).date()
        end = start + dt.timedelta(days=rng.randint(30, 365))
        projects.append((
            f"demo_proj_{i:05d}",
            client[0],
            f"{rng.choice(['Implementación', 'Migración', 'Auditoría', 'Roll-out', 'Diseño', 'Optimización'])} {client[1].split()[0]} {rng.randint(1, 9)}",
            rng.choices(PROJECT_STATUS, weights=[55, 15, 20, 7, 3])[0],
            presupuesto,
            round(revenue_est, 2),
            round(coste_est, 2),
            start, end,
            manager[1],
            now - dt.timedelta(hours=rng.randint(1, 24*30)),
        ))

    _log(f"Replicon: generating tasks ({counts['tasks']:,})")
    tasks = []
    task_pool_by_proj = {}
    for i in range(counts["tasks"]):
        proj = rng.choice(projects)
        tid = f"demo_task_{i:06d}"
        tasks.append((
            tid,
            proj[0],
            rng.choice(["Análisis", "Diseño", "Desarrollo", "Testing", "Documentación", "Despliegue", "Capacitación", "Soporte"]),
            float(rng.randint(8, 200)),
            rng.choice(["Open", "InProgress", "Done"]),
            now - dt.timedelta(hours=rng.randint(1, 24*60)),
        ))
        task_pool_by_proj.setdefault(proj[0], []).append(tid)

    _log(f"Replicon: generating assignments ({counts['assignments']:,})")
    assignments = []
    for i in range(counts["assignments"]):
        user = rng.choice(users)
        proj = rng.choice(projects)
        start = (now - dt.timedelta(days=rng.randint(0, 365))).date()
        end = start + dt.timedelta(days=rng.randint(30, 180))
        assignments.append((
            f"demo_assign_{i:06d}",
            user[0], proj[0],
            start, end,
            float(rng.choice([10, 20, 30, 40])),
            now - dt.timedelta(hours=rng.randint(1, 24*30)),
        ))

    _log(f"Replicon: generating timesheets ({counts['timesheets']:,}) — this is the biggest set")
    timesheets = []
    for i in range(counts["timesheets"]):
        user = rng.choice(users)
        proj = rng.choice(projects)
        tasks_in_proj = task_pool_by_proj.get(proj[0])
        task_id = rng.choice(tasks_in_proj) if tasks_in_proj else None
        fecha = (now - dt.timedelta(days=rng.randint(0, 365))).date()
        horas = round(rng.uniform(0.5, 9), 2)
        billable = rng.random() < 0.75
        tarifa = float(user[5])
        coste  = float(user[6])
        timesheets.append((
            f"demo_ts_{i:07d}",
            user[0], proj[0], task_id, fecha,
            horas, billable, tarifa, coste,
            round(horas * tarifa, 2),
            round(horas * coste, 2),
        ))

    _log(f"Replicon: generating expenses ({counts['expenses']:,})")
    expenses = []
    for i in range(counts["expenses"]):
        user = rng.choice(users)
        proj = rng.choice(projects)
        expenses.append((
            f"demo_exp_{i:06d}",
            user[0], proj[0],
            rng.choice(EXPENSE_CATS),
            round(rng.uniform(15, 1500), 2),
            (now - dt.timedelta(days=rng.randint(0, 365))).date(),
            rng.random() < 0.6,
        ))

    _log(f"Replicon: generating invoices ({counts['invoices']:,})")
    invoices = []
    for i in range(counts["invoices"]):
        proj = rng.choice(projects)
        client_id = proj[1]
        monto = round(rng.uniform(1_000, 250_000), 2)
        emis  = (now - dt.timedelta(days=rng.randint(0, 365))).date()
        venc  = emis + dt.timedelta(days=30)
        days_overdue = (now.date() - venc).days
        if days_overdue > 0 and rng.random() < 0.35:
            estado = "Overdue"
        elif rng.random() < 0.7:
            estado = "Paid"
            days_overdue = 0
        else:
            estado = "Open"
            days_overdue = max(0, days_overdue)
        invoices.append((
            f"demo_inv_{i:06d}",
            client_id, proj[0], monto, estado,
            emis, venc, days_overdue,
        ))

    _log("Replicon: inserting into demo_bronze...")
    inserted = {}
    inserted["users"]       = _insert(cn, """INSERT INTO demo_bronze.replicon_users
        (user_id, nombre, rol, departamento, ubicacion, tarifa_hora, coste_hora, status, fecha_alta, last_modified)
        VALUES %s ON CONFLICT (user_id) DO NOTHING""", users)
    inserted["clients"]     = _insert(cn, """INSERT INTO demo_bronze.replicon_clients
        (client_id, nombre, industria, pais, riesgo, last_modified)
        VALUES %s ON CONFLICT (client_id) DO NOTHING""", clients)
    inserted["projects"]    = _insert(cn, """INSERT INTO demo_bronze.replicon_projects
        (project_id, client_id, nombre, estado, presupuesto, revenue_estimado, coste_estimado,
         fecha_inicio, fecha_fin, manager, last_modified)
        VALUES %s ON CONFLICT (project_id) DO NOTHING""", projects)
    inserted["tasks"]       = _insert(cn, """INSERT INTO demo_bronze.replicon_tasks
        (task_id, project_id, nombre, horas_est, estado, last_modified)
        VALUES %s ON CONFLICT (task_id) DO NOTHING""", tasks)
    inserted["assignments"] = _insert(cn, """INSERT INTO demo_bronze.replicon_assignments
        (assignment_id, user_id, project_id, fecha_inicio, fecha_fin, horas_semana, last_modified)
        VALUES %s ON CONFLICT (assignment_id) DO NOTHING""", assignments)
    inserted["timesheets"]  = _insert(cn, """INSERT INTO demo_bronze.replicon_timesheets
        (timesheet_id, user_id, project_id, task_id, fecha, horas, billable,
         tarifa_hora, coste_hora, revenue_calculado, coste_calculado)
        VALUES %s ON CONFLICT (timesheet_id) DO NOTHING""", timesheets)
    inserted["expenses"]    = _insert(cn, """INSERT INTO demo_bronze.replicon_expenses
        (expense_id, user_id, project_id, categoria, monto, fecha, reimbursable)
        VALUES %s ON CONFLICT (expense_id) DO NOTHING""", expenses)
    inserted["invoices"]    = _insert(cn, """INSERT INTO demo_bronze.replicon_invoices
        (invoice_id, client_id, project_id, monto, estado, fecha_emision, fecha_vencimiento, dias_vencida)
        VALUES %s ON CONFLICT (invoice_id) DO NOTHING""", invoices)
    cn.commit()
    return inserted


# ─── SAP HR / SuccessFactors data ────────────────────────────────────────────

def gen_sap_hr(cn, scale: float) -> dict:
    rng = random.Random(101)
    counts = {
        "deps":        40,
        "cost_centers": int(300    * scale),
        "positions":   int(5_000   * scale),
        "employees":   int(20_000  * scale),
        "comps":       int(20_000  * scale),
        "absences":    int(50_000  * scale),
    }
    now = dt.datetime.now(dt.timezone.utc)

    _log(f"SAP HR: deps {counts['deps']}, cc {counts['cost_centers']}, pos {counts['positions']}, "
         f"emp {counts['employees']:,}, absences {counts['absences']:,}")

    deps = []
    for i in range(counts["deps"]):
        name = DEPARTMENTS[i % len(DEPARTMENTS)] + (f" {i//len(DEPARTMENTS)+1}" if i >= len(DEPARTMENTS) else "")
        deps.append((f"demo_dept_{i:03d}", name, None, rng.choice(CITIES)))

    cost_centers = []
    for i in range(counts["cost_centers"]):
        cost_centers.append((
            f"demo_cc_{i:04d}",
            f"CC {rng.choice(DEPARTMENTS)} {i:04d}",
            rng.choice(DEPARTMENTS),
            rng.choice(COUNTRIES),
        ))

    positions = []
    for i in range(counts["positions"]):
        positions.append((
            f"demo_pos_{i:05d}",
            rng.choice(ROLES) + f" L{rng.randint(1,6)}",
            rng.choice(DEPARTMENTS),
            rng.randint(1, 6),
            rng.random() < 0.06,  # 6% vacantes
        ))

    employees = []
    # First pass: ids and managers
    for i in range(counts["employees"]):
        employees.append([
            f"demo_emp_{i:06d}",
            _name(rng),
            rng.choice(DEPARTMENTS),
            rng.choice(ROLES) + f" L{rng.randint(1,6)}",
            None,  # manager — filled later
            rng.choice(cost_centers)[0] if rng.random() < 0.93 else "",  # 7% sin centro de coste
            rng.choice(CITIES),
            round(rng.uniform(25_000, 220_000), 2),
            rng.choices(["Active", "Terminated", "OnLeave"], weights=[88, 9, 3])[0],
            (now - dt.timedelta(days=rng.randint(60, 365*10))).date(),
            None,
            now - dt.timedelta(hours=rng.randint(1, 24*60)),
        ])
    # Assign managers (manager nombre)
    manager_pool = [e for e in employees if 'Manager' in e[3] or 'Director' in e[3] or 'Lead' in e[3] or 'VP' in e[3]]
    if not manager_pool:
        manager_pool = employees[:max(1, len(employees)//20)]
    for e in employees:
        if rng.random() < 0.96:
            mgr = rng.choice(manager_pool)
            e[4] = mgr[1]
    # Set fecha_baja for Terminated
    for e in employees:
        if e[8] == "Terminated":
            base = max(e[9], (now.date() - dt.timedelta(days=365*3)))
            offset_days = (now.date() - base).days
            if offset_days > 0:
                e[10] = base + dt.timedelta(days=rng.randint(0, offset_days))
    employees = [tuple(e) for e in employees]

    comps = []
    for i in range(counts["comps"]):
        emp = rng.choice(employees)
        comps.append((
            f"demo_comp_{i:06d}",
            emp[0],
            float(emp[7]),
            round(float(emp[7]) * rng.uniform(0, 0.25), 2),
            (now - dt.timedelta(days=rng.randint(0, 365*3))).date(),
        ))

    absences = []
    for i in range(counts["absences"]):
        emp = rng.choice(employees)
        start = (now - dt.timedelta(days=rng.randint(0, 365*2))).date()
        dias = rng.randint(1, 15)
        end = start + dt.timedelta(days=dias)
        absences.append((
            f"demo_abs_{i:07d}",
            emp[0],
            rng.choice(["Vacaciones", "Enfermedad", "Personal", "Maternidad", "Paternidad", "Capacitación"]),
            start, end, float(dias),
        ))

    _log("SAP HR: inserting into demo_bronze...")
    inserted = {}
    inserted["departamentos"] = _insert(cn, """INSERT INTO demo_bronze.sap_departamentos
        (dept_id, nombre, parent_id, ubicacion) VALUES %s ON CONFLICT (dept_id) DO NOTHING""", deps)
    inserted["centros_coste"] = _insert(cn, """INSERT INTO demo_bronze.sap_centros_coste
        (centro_coste_id, nombre, departamento, pais) VALUES %s ON CONFLICT (centro_coste_id) DO NOTHING""", cost_centers)
    inserted["posiciones"]    = _insert(cn, """INSERT INTO demo_bronze.sap_posiciones
        (posicion_id, titulo, departamento, nivel, vacante) VALUES %s ON CONFLICT (posicion_id) DO NOTHING""", positions)
    inserted["empleados"]     = _insert(cn, """INSERT INTO demo_bronze.sap_empleados
        (employee_id, nombre, departamento, posicion, manager, centro_coste,
         ubicacion, salario, status, fecha_alta, fecha_baja, last_modified)
        VALUES %s ON CONFLICT (employee_id) DO NOTHING""", employees)
    inserted["compensaciones"] = _insert(cn, """INSERT INTO demo_bronze.sap_compensaciones
        (comp_id, employee_id, salario_base, bono, fecha_efectiva) VALUES %s ON CONFLICT (comp_id) DO NOTHING""", comps)
    inserted["ausencias"]     = _insert(cn, """INSERT INTO demo_bronze.sap_ausencias
        (absence_id, employee_id, tipo, fecha_inicio, fecha_fin, dias) VALUES %s ON CONFLICT (absence_id) DO NOTHING""", absences)
    cn.commit()
    return inserted


# ─── SAP S/4HANA data ────────────────────────────────────────────────────────

def gen_sap_s4(cn, scale: float) -> dict:
    rng = random.Random(202)
    counts = {
        "suppliers":  int(5_000   * scale),
        "customers":  int(5_000   * scale),
        "materials":  int(10_000  * scale),
        "pos":        int(50_000  * scale),
        "invoices":   int(30_000  * scale),
        "sales":      int(40_000  * scale),
        "journals":   int(100_000 * scale),
    }
    now = dt.datetime.now(dt.timezone.utc)
    _log(f"SAP S/4: suppliers {counts['suppliers']:,}, customers {counts['customers']:,}, "
         f"POs {counts['pos']:,}, invoices {counts['invoices']:,}, sales {counts['sales']:,}, journals {counts['journals']:,}")

    suppliers = []
    for i in range(counts["suppliers"]):
        spend = round(rng.uniform(5_000, 5_000_000), 2)
        suppliers.append((
            f"demo_sup_{i:05d}",
            _company(rng, "Proveedor"),
            rng.choice(COUNTRIES),
            rng.choice(SUPPLIER_CATS),
            spend,
            rng.randint(0, 12),
            rng.choices(["Bajo", "Medio", "Alto"], weights=[60, 30, 10])[0],
        ))

    customers = []
    for i in range(counts["customers"]):
        customers.append((
            f"demo_cust_{i:05d}",
            _company(rng, "Customer"),
            rng.choice(COUNTRIES),
            rng.choice(INDUSTRIES),
        ))

    materials = []
    for i in range(counts["materials"]):
        materials.append((
            f"demo_mat_{i:06d}",
            f"Material {rng.choice(['CPU','RAM','SSD','PSU','Cable','Software License','Service Pack','Kit','Module','Component'])} {i:06d}",
            rng.choice(["Hardware", "Software", "Servicio", "Materias Primas", "Consumibles"]),
            round(rng.uniform(5, 5_000), 2),
            rng.randint(0, 1_000),
        ))

    purchase_orders = []
    for i in range(counts["pos"]):
        sup = rng.choice(suppliers)
        purchase_orders.append((
            f"demo_po_{i:07d}",
            sup[0],
            _name(rng),
            sup[3],
            round(rng.uniform(500, 250_000), 2),
            rng.choices(PO_STATUS, weights=[40, 20, 35, 5])[0],
            (now - dt.timedelta(days=rng.randint(0, 365))).date(),
        ))

    supplier_invoices = []
    for i in range(counts["invoices"]):
        sup = rng.choice(suppliers)
        emis = (now - dt.timedelta(days=rng.randint(0, 365))).date()
        venc = emis + dt.timedelta(days=30)
        days_overdue = (now.date() - venc).days
        if days_overdue > 0 and rng.random() < 0.3:
            estado = "Overdue"
        else:
            estado = rng.choices(["Paid", "Open"], weights=[80, 20])[0]
            if estado == "Paid":
                days_overdue = 0
            else:
                days_overdue = max(0, days_overdue)
        supplier_invoices.append((
            f"demo_sinv_{i:06d}",
            sup[0],
            round(rng.uniform(500, 250_000), 2),
            estado, emis, venc, days_overdue,
        ))

    sales = []
    for i in range(counts["sales"]):
        cust = rng.choice(customers)
        mat = rng.choice(materials)
        qty = rng.randint(1, 100)
        monto = round(qty * float(mat[3]) * rng.uniform(0.85, 1.4), 2)
        margen = round(monto * rng.uniform(0.1, 0.45), 2)
        sales.append((
            f"demo_so_{i:07d}",
            cust[0], mat[0], qty, monto, margen,
            (now - dt.timedelta(days=rng.randint(0, 365))).date(),
        ))

    # Generate cost centers for journals (subset)
    cc_ids = [f"demo_cc_{i:04d}" for i in range(max(50, int(300 * scale)))]

    journals = []
    for i in range(counts["journals"]):
        acct = rng.choice(ACCOUNTS)
        tipo = rng.choices(["Debit", "Credit"], weights=[55, 45])[0]
        journals.append((
            f"demo_j_{i:08d}",
            acct[0],
            rng.choice(cc_ids) if rng.random() < 0.85 else None,
            round(rng.uniform(50, 200_000), 2),
            rng.choice(["USD", "EUR", "MXN"]),
            tipo,
            (now - dt.timedelta(days=rng.randint(0, 365))).date(),
            acct[1],
        ))

    _log("SAP S/4HANA: inserting into demo_bronze...")
    inserted = {}
    inserted["proveedores"] = _insert(cn, """INSERT INTO demo_bronze.sap_proveedores
        (supplier_id, nombre, pais, categoria, spend_anual, facturas_vencidas, riesgo)
        VALUES %s ON CONFLICT (supplier_id) DO NOTHING""", suppliers)
    inserted["customers"]   = _insert(cn, """INSERT INTO demo_bronze.sap_customers
        (customer_id, nombre, pais, industria) VALUES %s ON CONFLICT (customer_id) DO NOTHING""", customers)
    inserted["materiales"]  = _insert(cn, """INSERT INTO demo_bronze.sap_materiales
        (material_id, nombre, categoria, precio_unit, stock) VALUES %s ON CONFLICT (material_id) DO NOTHING""", materials)
    inserted["ordenes_compra"] = _insert(cn, """INSERT INTO demo_bronze.sap_ordenes_compra
        (po_id, supplier_id, comprador, categoria, monto, estado, fecha)
        VALUES %s ON CONFLICT (po_id) DO NOTHING""", purchase_orders)
    inserted["facturas_proveedor"] = _insert(cn, """INSERT INTO demo_bronze.sap_facturas_proveedor
        (invoice_id, supplier_id, monto, estado, fecha_emision, fecha_vencimiento, dias_vencida)
        VALUES %s ON CONFLICT (invoice_id) DO NOTHING""", supplier_invoices)
    inserted["ventas"]      = _insert(cn, """INSERT INTO demo_bronze.sap_ventas
        (sales_order_id, customer_id, material_id, cantidad, monto, margen, fecha)
        VALUES %s ON CONFLICT (sales_order_id) DO NOTHING""", sales)
    inserted["journal_entries"] = _insert(cn, """INSERT INTO demo_bronze.sap_journal_entries
        (journal_id, cuenta_contable, centro_coste, monto, moneda, tipo, fecha, descripcion)
        VALUES %s ON CONFLICT (journal_id) DO NOTHING""", journals)
    cn.commit()
    return inserted


# ─── Cartridge & entity registration ─────────────────────────────────────────

def register_cartridge_metadata(cn) -> None:
    _log("Registering cartridge + entities + datasets + semantic terms")
    with cn.cursor() as cur:
        # Demo enterprise cartridge header
        cur.execute("""
            INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                description = EXCLUDED.description,
                updated_at  = NOW()
        """, (
            "demo_enterprise",
            "[DEMO PACK] Enterprise Demo",
            "1.0.0",
            "[DEMO PACK] Datos demo enterprise (Replicon + SAP) para probar Studio, Refinar, Analytics, IA y RAG end-to-end.",
            "dag-based",
            "cartridge",
            "demo_bronze/{entity}",
        ))

        # Demo entities — visible in Studio/Entidades for each cartridge
        # These are marked [DEMO PACK] in description and have enabled=FALSE so they
        # don't trigger any real DAG. They serve as catalog entries only.
        demo_entities = [
            # Replicon
            ("replicon",           "DemoUsers",       "user_id"),
            ("replicon",           "DemoClients",     "client_id"),
            ("replicon",           "DemoProjects",    "project_id"),
            ("replicon",           "DemoTasks",       "task_id"),
            ("replicon",           "DemoAssignments", "assignment_id"),
            ("replicon",           "DemoTimesheets",  "timesheet_id"),
            ("replicon",           "DemoExpenses",    "expense_id"),
            ("replicon",           "DemoInvoices",    "invoice_id"),
            # SAP SuccessFactors
            ("sap_successfactors", "DemoEmployees",     "employee_id"),
            ("sap_successfactors", "DemoDepartments",   "dept_id"),
            ("sap_successfactors", "DemoPositions",     "posicion_id"),
            ("sap_successfactors", "DemoCompensations", "comp_id"),
            # SAP HCM
            ("sap_hcm",            "DemoEmployeesHCM",  "employee_id"),
            ("sap_hcm",            "DemoAbsences",      "absence_id"),
            ("sap_hcm",            "DemoCostCenters",   "centro_coste_id"),
            # SAP S/4HANA
            ("sap_s4hana",         "DemoSuppliers",     "supplier_id"),
            ("sap_s4hana",         "DemoCustomers",     "customer_id"),
            ("sap_s4hana",         "DemoMaterials",     "material_id"),
            ("sap_s4hana",         "DemoPurchaseOrders","po_id"),
            ("sap_s4hana",         "DemoSupplierInvoices","invoice_id"),
            ("sap_s4hana",         "DemoSalesOrders",   "sales_order_id"),
            ("sap_s4hana",         "DemoJournalEntries","journal_id"),
            # demo_enterprise hub
            ("demo_enterprise",    "AllReplicon",       "row_id"),
            ("demo_enterprise",    "AllSAPHR",          "row_id"),
            ("demo_enterprise",    "AllSAPS4",          "row_id"),
        ]

        execute_values(cur, """
            INSERT INTO entity_config
                (cartridge_id, entity, display_name, mode, description, enabled,
                 primary_key, dag_id, trigger_type)
            VALUES %s
            ON CONFLICT (cartridge_id, entity) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description  = EXCLUDED.description,
                primary_key  = EXCLUDED.primary_key
        """, [
            (cart, ent, f"[DEMO] {ent}", "full",
             f"[DEMO PACK] Entidad de datos demo enterprise — ver tabla demo_bronze.*",
             False, pk, None, "manual")
            for cart, ent, pk in demo_entities
        ])

        # Datasets (silver views + gold tables) registered in `datasets` for
        # discoverability in Refinar. For silver entries the sql_def references
        # the postgres view directly (DuckDB ATTACH pgdb can read it via cross-DB).
        # Silver SQLs use the DuckDB ATTACH alias `pgdb` to reach the postgres
        # views in schema demo_silver (see refinement/app/duckdb_engine.py).
        silver_datasets = [
            ("demo_silver_replicon_timesheets_limpios", "silver", "replicon",
             ["demo_bronze.replicon_timesheets"],
             "SELECT * FROM pgdb.demo_silver.replicon_timesheets_limpios",
             "[DEMO PACK] Silver: timesheets con horas > 0, revenue y coste pre-calculados."),
            ("demo_silver_replicon_project_finance", "silver", "replicon",
             ["demo_bronze.replicon_projects", "demo_bronze.replicon_timesheets"],
             "SELECT * FROM pgdb.demo_silver.replicon_project_finance",
             "[DEMO PACK] Silver: finanzas por proyecto (revenue real, coste real, margen, sobre_presupuesto)."),
            ("demo_silver_sap_empleados_limpios", "silver", "sap_successfactors",
             ["demo_bronze.sap_empleados"],
             "SELECT * FROM pgdb.demo_silver.sap_empleados_limpios",
             "[DEMO PACK] Silver: empleados con status no nulo."),
            ("demo_silver_sap_proveedores_riesgo", "silver", "sap_s4hana",
             ["demo_bronze.sap_proveedores"],
             "SELECT * FROM pgdb.demo_silver.sap_proveedores_riesgo",
             "[DEMO PACK] Silver: proveedores con riesgo_score numérico."),
            ("demo_silver_sap_ordenes_compra_limpias", "silver", "sap_s4hana",
             ["demo_bronze.sap_ordenes_compra"],
             "SELECT * FROM pgdb.demo_silver.sap_ordenes_compra_limpias",
             "[DEMO PACK] Silver: órdenes de compra con monto > 0."),
            ("demo_silver_sap_finanzas_limpias", "silver", "sap_s4hana",
             ["demo_bronze.sap_journal_entries"],
             "SELECT * FROM pgdb.demo_silver.sap_finanzas_limpias",
             "[DEMO PACK] Silver: journal entries normalizados."),
        ]
        execute_values(cur, """
            INSERT INTO datasets
                (name, description, layer, cartridge, sources, sql_def,
                 column_mapping, schedule, updated_at)
            VALUES %s
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                layer       = EXCLUDED.layer,
                cartridge   = EXCLUDED.cartridge,
                sources     = EXCLUDED.sources,
                sql_def     = EXCLUDED.sql_def,
                updated_at  = NOW()
        """, [
            (n, d, l, c, json.dumps(srcs), sql, "{}", None)
            for (n, l, c, srcs, sql, d) in silver_datasets
        ])

        # Gold datasets — referenced as pggold.gold_<short_name> in their sql_def.
        # DuckDB ATTACH pggold lets the refinement engine query them through
        # /api/data/{dataset}. Each gold table has a revenue_manager column that
        # defaults to 'N/D' so RLS returns rows for any user.
        for g in GOLD_DEFINITIONS:
            name      = g["name"]
            short     = name[len("demo_"):]   # used in pggold table name
            cur.execute("""
                INSERT INTO datasets
                    (name, description, layer, cartridge, sources, sql_def,
                     column_mapping, schedule, updated_at)
                VALUES (%s, %s, 'gold', %s, %s::jsonb, %s, '{}'::jsonb, NULL, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description,
                    layer       = EXCLUDED.layer,
                    cartridge   = EXCLUDED.cartridge,
                    sources     = EXCLUDED.sources,
                    sql_def     = EXCLUDED.sql_def,
                    updated_at  = NOW()
            """, (
                name,                # registered name == "demo_..."
                g["description"],
                g["cartridge"],
                json.dumps(g["sources"]),
                f"SELECT * FROM pggold.gold_{name}",
            ))

        # Semantic terms
        execute_values(cur, """
            INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
            VALUES %s
            ON CONFLICT (cartridge_id, term) DO UPDATE SET
                definition = EXCLUDED.definition,
                maps_to    = EXCLUDED.maps_to
        """, SEMANTIC_TERMS)

    cn.commit()


def register_rag_knowledge(cn) -> None:
    """Insert RAG documents as parent-only chunks (no embeddings).

    For semantic vector search, the user can re-run /api/rag/ingest on these
    documents to populate child chunks with embeddings via the Gemini API. They
    are inserted here so they show up in /api/rag/sources and can be fetched as
    plain text by the Asistente IA via /api/rag/ask (when embeddings exist).
    """
    _log(f"Registering {len(KNOWLEDGE_BITS)} RAG knowledge bits (text-only)")
    with cn.cursor() as cur:
        for name, desc, content in KNOWLEDGE_BITS:
            cur.execute("""
                INSERT INTO rag_sources (name, description, mime_type, size_chars, chunk_count)
                VALUES (%s, %s, 'text/plain', %s, 1)
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description,
                    size_chars  = EXCLUDED.size_chars,
                    chunk_count = EXCLUDED.chunk_count,
                    updated_at  = NOW()
                RETURNING id
            """, (name, desc, len(content)))
            src_id = cur.fetchone()[0]
            cur.execute("DELETE FROM rag_chunks WHERE source_id = %s", (src_id,))
            cur.execute("""
                INSERT INTO rag_chunks (source_id, chunk_type, chunk_index, content, metadata)
                VALUES (%s, 'parent', 0, %s, %s::jsonb)
            """, (src_id, content, json.dumps({"demo_pack": True, "name": name})))
    cn.commit()


# ─── Gold materialization ────────────────────────────────────────────────────

def materialize_gold(bronze_cn, gold_cn) -> dict:
    _log("Building gold tables from Bronze/Silver and writing to pggold...")
    items = _gold_ddl_and_populate(bronze_cn)
    counts = {}
    with gold_cn.cursor() as cur:
        for name, ddl, insert_sql, rows in items:
            cur.execute(f"DROP TABLE IF EXISTS gold_{name}")
            cur.execute(ddl)
            n = 0
            if rows:
                for chunk in _batch(list(rows), 5000):
                    execute_values(cur, insert_sql, chunk, page_size=5000)
                    n += len(chunk)
            counts[name] = n
            _log(f"  gold_{name}: {n:,} rows")
    gold_cn.commit()
    return counts


# ─── Reset ───────────────────────────────────────────────────────────────────

def reset(bronze_cn, gold_cn) -> None:
    _log("Resetting demo pack — dropping demo objects...")
    with bronze_cn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS demo_silver CASCADE")
        cur.execute("DROP SCHEMA IF EXISTS demo_bronze CASCADE")
        # Remove demo registrations
        cur.execute("DELETE FROM datasets WHERE name LIKE 'demo_%'")
        cur.execute("DELETE FROM entity_config WHERE cartridge_id = 'demo_enterprise'")
        cur.execute("DELETE FROM entity_config WHERE entity LIKE 'Demo%'")
        cur.execute("DELETE FROM semantic_terms WHERE cartridge_id = 'demo_enterprise'")
        cur.execute("DELETE FROM cartridges WHERE id = 'demo_enterprise'")
        cur.execute("DELETE FROM rag_sources WHERE name LIKE '[DEMO PACK]%'")
    bronze_cn.commit()

    with gold_cn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'gold_demo_%'
        """)
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
    gold_cn.commit()
    _log(f"Reset done. Dropped {len(tables)} gold tables and all demo bronze/silver/registrations.")


# ─── Counts and validation ───────────────────────────────────────────────────

def print_counts(bronze_cn, gold_cn) -> None:
    _log("─── Final counts ─────────────────────────────────────────────")
    with bronze_cn.cursor() as cur:
        cur.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('demo_bronze', 'demo_silver')
            ORDER BY table_schema, table_name
        """)
        for sch, tab in cur.fetchall():
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{sch}"."{tab}"')
                _log(f"  {sch}.{tab}: {cur.fetchone()[0]:,}")
            except Exception as e:
                _log(f"  {sch}.{tab}: ERROR {e}")

        cur.execute("SELECT name, layer, cartridge FROM datasets WHERE name LIKE 'demo_%' ORDER BY layer, name")
        ds = cur.fetchall()
        _log(f"  Datasets registered: {len(ds)}")
        cur.execute("SELECT COUNT(*) FROM entity_config WHERE entity LIKE 'Demo%' OR cartridge_id='demo_enterprise'")
        _log(f"  Demo entities in entity_config: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM semantic_terms WHERE cartridge_id='demo_enterprise'")
        _log(f"  Demo semantic terms: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM rag_sources WHERE name LIKE '[DEMO PACK]%'")
        _log(f"  Demo RAG sources: {cur.fetchone()[0]}")

    with gold_cn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'gold_demo_%'
            ORDER BY table_name
        """)
        for (t,) in cur.fetchall():
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            _log(f"  pggold.{t}: {cur.fetchone()[0]:,}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="CONSOLA-BETA Enterprise Demo Pack seeder")
    ap.add_argument("--reset", action="store_true", help="Drop all demo pack objects and exit.")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="Scale factor for row counts (1.0=full enterprise volume, 0.1=smoke).")
    ap.add_argument("--skip-data", action="store_true",
                    help="Skip Bronze data generation (only re-register metadata + rebuild gold from existing bronze).")
    ap.add_argument("--only", choices=["replicon", "hr", "s4", "gold", "meta"], default=None,
                    help="Only run a single phase.")
    args = ap.parse_args()

    _log(f"Main DB:  {PG_DSN.split('@')[-1]}")
    _log(f"Gold DB:  {GOLD_DSN.split('@')[-1]}")

    t0 = time.time()
    with _conn(PG_DSN) as bronze_cn, _conn(GOLD_DSN) as gold_cn:
        if args.reset:
            reset(bronze_cn, gold_cn)
            return

        # Bootstrap demo schemas + silver views in main DB
        with bronze_cn.cursor() as cur:
            cur.execute(CREATE_BRONZE_SILVER_SQL)
            cur.execute(CREATE_SILVER_VIEWS_SQL)
        bronze_cn.commit()

        if not args.skip_data:
            if args.only in (None, "replicon"):
                gen_replicon(bronze_cn, args.scale)
            if args.only in (None, "hr"):
                gen_sap_hr(bronze_cn, args.scale)
            if args.only in (None, "s4"):
                gen_sap_s4(bronze_cn, args.scale)

        # Re-create silver views (idempotent — handles schema changes)
        with bronze_cn.cursor() as cur:
            cur.execute(CREATE_SILVER_VIEWS_SQL)
        bronze_cn.commit()

        if args.only in (None, "gold"):
            materialize_gold(bronze_cn, gold_cn)

        if args.only in (None, "meta"):
            register_cartridge_metadata(bronze_cn)
            register_rag_knowledge(bronze_cn)

        print_counts(bronze_cn, gold_cn)

    _log(f"Done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
