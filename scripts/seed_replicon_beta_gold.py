#!/usr/bin/env python3
"""Seed the private-beta Replicon Gold path.

This is a controlled beta seed, not an external Replicon extraction. It creates
the Gold tables needed by the packaged Replicon apps, scopes every row to one
tenant/workspace, applies native Gold RLS, and writes honest catalog/lineage
metadata so app readiness, lineage, and dataset previews agree.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, execute_values


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "infra" / ".env"
SEED_BATCH_ID = "replicon-beta-gold-seed"
SEED_SOURCE = "seed/replicon_beta_gold"
REPLICON_VAULT_KEY = "seeded_gold"
REPLICON_VAULT_MARKER: dict[str, Any] = {
    "base_url": "seeded://replicon-beta-gold",
    "auth_method": "seeded_gold",
    "status": "data_seed_only",
    "write_back_enabled": False,
    "external_write_back_enabled": False,
    "description": "Controlled beta Gold seed; no external Replicon credentials.",
}


def _d(month: int) -> date:
    return date(2026, month, 1)


EMPLOYEES = [
    {
        "usuario": "Andrea Morales",
        "nombre_completo": "Andrea Morales",
        "tipo_empleado": "Interno",
        "tipo_de_proveedor": "Empleado",
        "departamento": "Data Platform",
        "supervisor": "Laura Torres",
        "revenue_manager": "Laura Torres",
        "costo_hora": 62,
        "horas_disponibles_mensual": 168,
    },
    {
        "usuario": "Mateo Cruz",
        "nombre_completo": "Mateo Cruz",
        "tipo_empleado": "Interno",
        "tipo_de_proveedor": "Empleado",
        "departamento": "Data Platform",
        "supervisor": "Laura Torres",
        "revenue_manager": "Laura Torres",
        "costo_hora": 58,
        "horas_disponibles_mensual": 168,
    },
    {
        "usuario": "Valeria Ortiz",
        "nombre_completo": "Valeria Ortiz",
        "tipo_empleado": "Externo",
        "tipo_de_proveedor": "Contractor",
        "departamento": "SAP Advisory",
        "supervisor": "Carlos Vega",
        "revenue_manager": "Carlos Vega",
        "costo_hora": 88,
        "horas_disponibles_mensual": 160,
    },
    {
        "usuario": "Raul Jimenez",
        "nombre_completo": "Raul Jimenez",
        "tipo_empleado": "Interno",
        "tipo_de_proveedor": "Empleado",
        "departamento": "SAP Advisory",
        "supervisor": "Carlos Vega",
        "revenue_manager": "Carlos Vega",
        "costo_hora": 68,
        "horas_disponibles_mensual": 168,
    },
    {
        "usuario": "Camila Soto",
        "nombre_completo": "Camila Soto",
        "tipo_empleado": "Interno",
        "tipo_de_proveedor": "Empleado",
        "departamento": "Revenue Operations",
        "supervisor": "Fernanda Rios",
        "revenue_manager": "Fernanda Rios",
        "costo_hora": 55,
        "horas_disponibles_mensual": 168,
    },
    {
        "usuario": "Omar Nunez",
        "nombre_completo": "Omar Nunez",
        "tipo_empleado": "Externo",
        "tipo_de_proveedor": "Consultoria",
        "departamento": "Revenue Operations",
        "supervisor": "Fernanda Rios",
        "revenue_manager": "Fernanda Rios",
        "costo_hora": 92,
        "horas_disponibles_mensual": 160,
    },
]

PROJECTS = {
    "PROJ-OMEGA-A": {
        "project_name": "Retail optimization",
        "cliente": "Norte Retail",
        "revenue_manager": "Laura Torres",
        "tipo_proyecto": "FPP",
        "billing_rate_usd": 145,
    },
    "PROJ-HCM-21": {
        "project_name": "People analytics SAP",
        "cliente": "Grupo Andina",
        "revenue_manager": "Carlos Vega",
        "tipo_proyecto": "T&M",
        "billing_rate_usd": 158,
    },
    "PROJ-CRM-77": {
        "project_name": "Revenue operations",
        "cliente": "Global Foods",
        "revenue_manager": "Fernanda Rios",
        "tipo_proyecto": "AMS Baseline",
        "billing_rate_usd": 136,
    },
}

ASSIGNMENTS = [
    ("Andrea Morales", "PROJ-OMEGA-A", 4, 132, 124),
    ("Andrea Morales", "PROJ-OMEGA-A", 5, 140, 130),
    ("Andrea Morales", "PROJ-OMEGA-A", 6, 152, 118),
    ("Andrea Morales", "PROJ-OMEGA-A", 7, 128, 0),
    ("Andrea Morales", "PROJ-OMEGA-A", 8, 104, 0),
    ("Mateo Cruz", "PROJ-OMEGA-A", 4, 96, 88),
    ("Mateo Cruz", "PROJ-OMEGA-A", 5, 112, 104),
    ("Mateo Cruz", "PROJ-OMEGA-A", 6, 118, 96),
    ("Mateo Cruz", "PROJ-OMEGA-A", 7, 132, 0),
    ("Mateo Cruz", "PROJ-OMEGA-A", 8, 120, 0),
    ("Valeria Ortiz", "PROJ-HCM-21", 4, 120, 114),
    ("Valeria Ortiz", "PROJ-HCM-21", 5, 128, 120),
    ("Valeria Ortiz", "PROJ-HCM-21", 6, 142, 116),
    ("Valeria Ortiz", "PROJ-HCM-21", 7, 116, 0),
    ("Valeria Ortiz", "PROJ-HCM-21", 8, 96, 0),
    ("Raul Jimenez", "PROJ-HCM-21", 4, 88, 80),
    ("Raul Jimenez", "PROJ-HCM-21", 5, 96, 92),
    ("Raul Jimenez", "PROJ-HCM-21", 6, 104, 88),
    ("Raul Jimenez", "PROJ-HCM-21", 7, 124, 0),
    ("Raul Jimenez", "PROJ-HCM-21", 8, 116, 0),
    ("Camila Soto", "PROJ-CRM-77", 4, 112, 106),
    ("Camila Soto", "PROJ-CRM-77", 5, 120, 112),
    ("Camila Soto", "PROJ-CRM-77", 6, 132, 100),
    ("Camila Soto", "PROJ-CRM-77", 7, 140, 0),
    ("Camila Soto", "PROJ-CRM-77", 8, 132, 0),
    ("Omar Nunez", "PROJ-CRM-77", 4, 72, 68),
    ("Omar Nunez", "PROJ-CRM-77", 5, 84, 76),
    ("Omar Nunez", "PROJ-CRM-77", 6, 96, 72),
    ("Omar Nunez", "PROJ-CRM-77", 7, 88, 0),
    ("Omar Nunez", "PROJ-CRM-77", 8, 80, 0),
]

SKILL_ROWS = [
    ("Andrea Morales", "Data Engineering", "Data", 4.8, True),
    ("Andrea Morales", "Python", "Data", 4.5, False),
    ("Andrea Morales", "Analytics", "Analytics", 4.2, False),
    ("Andrea Morales", "SAP SuccessFactors", "SAP", 3.4, False),
    ("Mateo Cruz", "Data Engineering", "Data", 4.2, False),
    ("Mateo Cruz", "Python", "Data", 4.0, False),
    ("Mateo Cruz", "Forecasting", "Analytics", 3.8, False),
    ("Valeria Ortiz", "SAP HCM", "SAP", 4.7, True),
    ("Valeria Ortiz", "SAP SuccessFactors", "SAP", 4.3, False),
    ("Valeria Ortiz", "Workforce Planning", "Analytics", 3.9, False),
    ("Raul Jimenez", "SAP SuccessFactors", "SAP", 3.9, False),
    ("Raul Jimenez", "Integration", "Integration", 4.0, False),
    ("Raul Jimenez", "SAP HCM", "SAP", 3.6, False),
    ("Camila Soto", "HubSpot", "CRM", 4.6, True),
    ("Camila Soto", "Salesforce", "CRM", 4.1, False),
    ("Camila Soto", "Revenue Operations", "CRM", 4.4, False),
    ("Omar Nunez", "Salesforce", "CRM", 4.7, True),
    ("Omar Nunez", "Integration", "Integration", 3.7, False),
    ("Omar Nunez", "HubSpot", "CRM", 3.5, False),
]


def _employee_map() -> dict[str, dict[str, Any]]:
    return {str(row["usuario"]).lower(): row for row in EMPLOYEES}


def _build_consultor_asignacion_rows() -> list[tuple[Any, ...]]:
    employees = _employee_map()
    rows: list[tuple[Any, ...]] = []
    for consultant, project_code, month, assigned, billable in ASSIGNMENTS:
        emp = employees[consultant.lower()]
        project = PROJECTS[project_code]
        executed = billable + (8 if billable else 0)
        non_billable = max(0, executed - billable)
        rows.append(
            (
                _d(month),
                emp["revenue_manager"],
                emp["departamento"],
                consultant,
                consultant,
                emp["tipo_empleado"],
                emp["tipo_de_proveedor"],
                project_code,
                project["project_name"],
                project["cliente"],
                assigned,
                executed,
                billable,
                non_billable,
                round(assigned / 168 * 100, 2),
                round(executed / 168 * 100, 2) if executed else 0,
                "ejecutado" if billable else "futuro",
            )
        )
    return rows


def _build_consultor_mensual_rows() -> list[tuple[Any, ...]]:
    employees = _employee_map()
    rows: list[tuple[Any, ...]] = []
    for consultant, project_code, month, assigned, billable in ASSIGNMENTS:
        emp = employees[consultant.lower()]
        project = PROJECTS[project_code]
        executed = billable + (8 if billable else 0)
        non_billable = max(0, executed - billable)
        rate = project["billing_rate_usd"]
        cost_hour = emp["costo_hora"]
        direct_cost = round(billable * cost_hour, 2)
        non_billable_cost = round(non_billable * cost_hour, 2)
        sunk_hours = max(0, emp["horas_disponibles_mensual"] - assigned)
        sunk_cost = round(sunk_hours * cost_hour * 0.35, 2)
        total_cost = round(direct_cost + non_billable_cost + sunk_cost, 2)
        revenue = round(billable * rate, 2)
        rows.append(
            (
                _d(month),
                emp["revenue_manager"],
                emp["departamento"],
                consultant,
                emp["tipo_empleado"],
                emp["tipo_de_proveedor"],
                project_code,
                project["project_name"],
                project["cliente"],
                assigned,
                executed,
                billable,
                non_billable,
                sunk_hours,
                rate,
                cost_hour,
                direct_cost,
                non_billable_cost,
                sunk_cost,
                total_cost,
                revenue,
            )
        )
    return rows


def _build_pnl_detalle_rows() -> list[tuple[Any, ...]]:
    employees = _employee_map()
    rows: list[tuple[Any, ...]] = []
    for consultant, project_code, month, assigned, billable in ASSIGNMENTS:
        emp = employees[consultant.lower()]
        project = PROJECTS[project_code]
        non_billable = 8 if billable else 0
        total_hours = billable + non_billable
        cost_direct = round(billable * emp["costo_hora"], 2)
        cost_sunk = round(max(0, emp["horas_disponibles_mensual"] - assigned) * emp["costo_hora"] * 0.35, 2)
        revenue = round(billable * project["billing_rate_usd"], 2)
        rows.append(
            (
                _d(month),
                emp["revenue_manager"],
                project["cliente"],
                project_code,
                project["project_name"],
                consultant,
                emp["tipo_empleado"],
                emp["tipo_de_proveedor"],
                billable,
                non_billable,
                total_hours,
                cost_direct,
                cost_sunk,
                round(cost_direct + cost_sunk, 2),
                revenue,
                emp["costo_hora"],
                project["billing_rate_usd"],
            )
        )
    return rows


def _build_pnl_mensual_rows() -> list[tuple[Any, ...]]:
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for row in _build_pnl_detalle_rows():
        (
            mes,
            revenue_manager,
            cliente,
            proyecto,
            project_name,
            _consultor,
            _tipo_empleado,
            _tipo_de_proveedor,
            horas_facturables,
            horas_no_facturables,
            horas_totales,
            costo_directo,
            costo_hundido_aporte,
            costo_total,
            revenue_usd,
            _costo_hora,
            _billing_rate_usd,
        ) = row
        key = (proyecto, mes.month)
        bucket = grouped.setdefault(
            key,
            {
                "mes": mes,
                "revenue_manager": revenue_manager,
                "cliente": cliente,
                "proyecto": proyecto,
                "project_name": project_name,
                "tipo_proyecto": PROJECTS[proyecto]["tipo_proyecto"],
                "revenue_usd": 0,
                "facturacion_mes_usd": 0,
                "wip_usd": 0,
                "costo_directo": 0,
                "costo_hundido": 0,
                "costo_total": 0,
                "horas_facturables": 0,
                "horas_totales": 0,
            },
        )
        bucket["revenue_usd"] += revenue_usd
        bucket["facturacion_mes_usd"] += round(revenue_usd * 0.86, 2)
        bucket["wip_usd"] += round(revenue_usd * 0.14, 2)
        bucket["costo_directo"] += costo_directo
        bucket["costo_hundido"] += costo_hundido_aporte
        bucket["costo_total"] += costo_total
        bucket["horas_facturables"] += horas_facturables
        bucket["horas_totales"] += horas_totales
    rows: list[tuple[Any, ...]] = []
    for bucket in grouped.values():
        margin = round(bucket["revenue_usd"] - bucket["costo_total"], 2)
        margin_pct = round((margin / bucket["revenue_usd"] * 100), 2) if bucket["revenue_usd"] else 0
        rows.append(
            (
                bucket["mes"],
                bucket["revenue_manager"],
                bucket["cliente"],
                bucket["proyecto"],
                bucket["project_name"],
                bucket["tipo_proyecto"],
                bucket["revenue_usd"],
                bucket["facturacion_mes_usd"],
                bucket["wip_usd"],
                bucket["costo_directo"],
                bucket["costo_hundido"],
                bucket["costo_total"],
                margin,
                margin_pct,
                bucket["horas_facturables"],
                bucket["horas_totales"],
            )
        )
    return sorted(rows, key=lambda item: (item[0], item[3]))


def _build_capacidad_rows() -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for emp in EMPLOYEES:
        for month in range(4, 9):
            rows.append(
                (
                    _d(month),
                    emp["usuario"],
                    emp["nombre_completo"],
                    emp["tipo_empleado"],
                    emp["tipo_de_proveedor"],
                    emp["departamento"],
                    emp["supervisor"],
                    emp["horas_disponibles_mensual"],
                    emp["costo_hora"],
                    emp["horas_disponibles_mensual"] * emp["costo_hora"],
                )
            )
    return rows


def _build_empleados_rows() -> list[tuple[Any, ...]]:
    return [
        (
            emp["usuario"],
            emp["nombre_completo"],
            emp["tipo_empleado"],
            emp["tipo_de_proveedor"],
            emp["departamento"],
            emp["supervisor"],
            emp["revenue_manager"],
            emp["costo_hora"],
            emp["horas_disponibles_mensual"],
            True,
        )
        for emp in EMPLOYEES
    ]


def _build_skill_rows() -> list[tuple[Any, ...]]:
    employees = _employee_map()
    rows: list[tuple[Any, ...]] = []
    for usuario, skill_name, skill_category, skill_rating, is_mentor in SKILL_ROWS:
        emp = employees[usuario.lower()]
        rows.append(
            (
                usuario,
                skill_name,
                skill_category,
                skill_rating,
                is_mentor,
                emp["tipo_empleado"],
                emp["tipo_de_proveedor"],
                emp["departamento"],
                emp["supervisor"],
            )
        )
    return rows


def _build_skill_gap_rows() -> list[tuple[Any, ...]]:
    by_manager: dict[str, list[dict[str, Any]]] = {}
    for emp in EMPLOYEES:
        by_manager.setdefault(emp["supervisor"], []).append(emp)
    skills = _build_skill_rows()
    rows: list[tuple[Any, ...]] = []
    categories = sorted({str(row[2]) for row in skills})
    for manager, employees in sorted(by_manager.items()):
        users = {str(emp["usuario"]) for emp in employees}
        total = len(users)
        for category in categories:
            ratings = [row for row in skills if row[0] in users and row[2] == category]
            avg = round(sum(float(row[3]) for row in ratings) / len(ratings), 2) if ratings else 0
            with_skills = len({str(row[0]) for row in ratings})
            no_eval = total - with_skills
            experts = sum(1 for row in ratings if float(row[3]) >= 4)
            mentors = sum(1 for row in ratings if row[4])
            if avg >= 4 and no_eval == 0:
                gap = "SATISFACTORIA"
            elif avg >= 3 or with_skills >= max(1, total // 2):
                gap = "MODERADA"
            else:
                gap = "CRITICA"
            rows.append((manager, category, avg, with_skills, total, no_eval, experts, mentors, gap))
    return rows


DATASETS: dict[str, dict[str, Any]] = {
    "consultor_mensual": {
        "description": "Seed beta de metricas mensuales por consultor y proyecto.",
        "columns": [
            ("mes", "DATE"),
            ("revenue_manager", "TEXT"),
            ("departamento", "TEXT"),
            ("consultor", "TEXT"),
            ("tipo_empleado", "TEXT"),
            ("tipo_de_proveedor", "TEXT"),
            ("proyecto", "TEXT"),
            ("project_name", "TEXT"),
            ("cliente", "TEXT"),
            ("horas_asignadas", "NUMERIC(18,4)"),
            ("horas_ejecutadas", "NUMERIC(18,4)"),
            ("horas_facturables", "NUMERIC(18,4)"),
            ("horas_no_facturables", "NUMERIC(18,4)"),
            ("horas_hundidas_capacidad", "NUMERIC(18,4)"),
            ("billing_rate_usd", "NUMERIC(18,4)"),
            ("costo_hora", "NUMERIC(18,4)"),
            ("costo_directo", "NUMERIC(18,4)"),
            ("costo_no_facturable", "NUMERIC(18,4)"),
            ("costo_hundido", "NUMERIC(18,4)"),
            ("costo_total", "NUMERIC(18,4)"),
            ("revenue_generado", "NUMERIC(18,4)"),
        ],
        "rows": _build_consultor_mensual_rows(),
    },
    "consultor_asignacion": {
        "description": "Seed beta de asignacion mensual de consultores por proyecto.",
        "columns": [
            ("mes", "DATE"),
            ("revenue_manager", "TEXT"),
            ("departamento", "TEXT"),
            ("consultor", "TEXT"),
            ("username", "TEXT"),
            ("tipo_empleado", "TEXT"),
            ("tipo_de_proveedor", "TEXT"),
            ("proyecto", "TEXT"),
            ("project_name", "TEXT"),
            ("cliente", "TEXT"),
            ("horas_asignadas", "NUMERIC(18,4)"),
            ("horas_ejecutadas", "NUMERIC(18,4)"),
            ("horas_facturables", "NUMERIC(18,4)"),
            ("horas_no_facturables", "NUMERIC(18,4)"),
            ("pct_asignacion", "NUMERIC(18,4)"),
            ("pct_ejecucion", "NUMERIC(18,4)"),
            ("estado", "TEXT"),
        ],
        "rows": _build_consultor_asignacion_rows(),
    },
    "fact_empleado_skills": {
        "description": "Seed beta de ratings de skill por consultor.",
        "columns": [
            ("usuario", "TEXT"),
            ("skill_name", "TEXT"),
            ("skill_category", "TEXT"),
            ("skill_rating", "NUMERIC(18,4)"),
            ("is_mentor", "BOOLEAN"),
            ("tipo_empleado", "TEXT"),
            ("tipo_de_proveedor", "TEXT"),
            ("departamento", "TEXT"),
            ("supervisor", "TEXT"),
        ],
        "rows": _build_skill_rows(),
    },
    "costo_consultor_mensual": {
        "description": "Seed beta de capacidad y costo mensual por consultor.",
        "columns": [
            ("mes", "DATE"),
            ("username", "TEXT"),
            ("nombre_completo", "TEXT"),
            ("tipo_empleado", "TEXT"),
            ("tipo_de_proveedor", "TEXT"),
            ("departamento", "TEXT"),
            ("supervisor", "TEXT"),
            ("horas_disponibles", "NUMERIC(18,4)"),
            ("costo_hora", "NUMERIC(18,4)"),
            ("costo_mensual", "NUMERIC(18,4)"),
        ],
        "rows": _build_capacidad_rows(),
    },
    "empleados_maestro": {
        "description": "Seed beta de maestro de empleados Replicon.",
        "columns": [
            ("usuario", "TEXT"),
            ("nombre_completo", "TEXT"),
            ("tipo_empleado", "TEXT"),
            ("tipo_de_proveedor", "TEXT"),
            ("departamento", "TEXT"),
            ("supervisor", "TEXT"),
            ("revenue_manager", "TEXT"),
            ("costo_hora", "NUMERIC(18,4)"),
            ("horas_disponibles_mensual", "NUMERIC(18,4)"),
            ("activo", "BOOLEAN"),
        ],
        "rows": _build_empleados_rows(),
    },
    "pnl_mensual": {
        "description": "Seed beta de P&L mensual por revenue manager, cliente y proyecto.",
        "columns": [
            ("mes", "DATE"),
            ("revenue_manager", "TEXT"),
            ("cliente", "TEXT"),
            ("proyecto", "TEXT"),
            ("project_name", "TEXT"),
            ("tipo_proyecto", "TEXT"),
            ("revenue_usd", "NUMERIC(18,4)"),
            ("facturacion_mes_usd", "NUMERIC(18,4)"),
            ("wip_usd", "NUMERIC(18,4)"),
            ("costo_directo", "NUMERIC(18,4)"),
            ("costo_hundido", "NUMERIC(18,4)"),
            ("costo_total", "NUMERIC(18,4)"),
            ("margen_bruto_usd", "NUMERIC(18,4)"),
            ("margen_bruto_pct", "NUMERIC(18,4)"),
            ("horas_facturables", "NUMERIC(18,4)"),
            ("horas_totales", "NUMERIC(18,4)"),
        ],
        "rows": _build_pnl_mensual_rows(),
    },
    "pnl_detalle_consultor": {
        "description": "Seed beta de P&L prorrateado por consultor.",
        "columns": [
            ("mes", "DATE"),
            ("revenue_manager", "TEXT"),
            ("cliente", "TEXT"),
            ("proyecto", "TEXT"),
            ("project_name", "TEXT"),
            ("consultor", "TEXT"),
            ("tipo_empleado", "TEXT"),
            ("tipo_de_proveedor", "TEXT"),
            ("horas_facturables", "NUMERIC(18,4)"),
            ("horas_no_facturables", "NUMERIC(18,4)"),
            ("horas_totales", "NUMERIC(18,4)"),
            ("costo_directo", "NUMERIC(18,4)"),
            ("costo_hundido_aporte", "NUMERIC(18,4)"),
            ("costo_total", "NUMERIC(18,4)"),
            ("revenue_usd", "NUMERIC(18,4)"),
            ("costo_hora", "NUMERIC(18,4)"),
            ("billing_rate_usd", "NUMERIC(18,4)"),
        ],
        "rows": _build_pnl_detalle_rows(),
    },
    "analytic_skill_gap_by_manager": {
        "description": "Seed beta de brechas de skills por revenue manager.",
        "columns": [
            ("manager_name", "TEXT"),
            ("skill_category", "TEXT"),
            ("avg_rating", "NUMERIC(18,4)"),
            ("empleados_con_skills", "INTEGER"),
            ("total_empleados_en_equipo", "INTEGER"),
            ("empleados_sin_evaluacion", "INTEGER"),
            ("count_experts", "INTEGER"),
            ("count_mentors", "INTEGER"),
            ("brecha_nivel", "TEXT"),
        ],
        "rows": _build_skill_gap_rows(),
    },
}

REQUIRED_REPLICON_GOLD_DATASETS = frozenset(DATASETS)


def gold_table_columns(dataset: str) -> list[str]:
    payload = DATASETS[dataset]
    return ["tenant_id", "workspace_id", *[name for name, _kind in payload["columns"]]]


def _load_env_file(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _normalize_dsn(raw: str) -> str:
    return (
        (raw or "")
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgres+psycopg2://", "postgres://")
    )


def _admin_dsn(database: str, port_env: str, default_port: str) -> str:
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not password:
        return ""
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get(port_env, default_port)
    return f"postgresql://postgres:{password}@{host}:{port}/{database}"


def _operational_dsn() -> str:
    return _normalize_dsn(
        os.environ.get("OMEGA_REPLICON_SEED_DATABASE_URL")
        or os.environ.get("OMEGA_SEED_DATABASE_URL")
        or _admin_dsn("modecissions", "POSTGRES_PORT", "15432")
        or os.environ.get("DATABASE_URL", "")
    )


def _gold_dsn() -> str:
    return _normalize_dsn(
        os.environ.get("OMEGA_REPLICON_SEED_GOLD_DATABASE_URL")
        or os.environ.get("OMEGA_SEED_GOLD_DATABASE_URL")
        or _admin_dsn("modecissions_gold", "POSTGRES_GOLD_PORT", "15433")
        or os.environ.get("GOLD_DATABASE_URL")
        or os.environ.get("DATABASE_URL", "")
    )


def _connect_operational():
    dsn = _operational_dsn()
    if not dsn:
        raise SystemExit("operational database DSN missing")
    return psycopg2.connect(dsn)


def _connect_gold():
    dsn = _gold_dsn()
    if not dsn:
        raise SystemExit("Gold database DSN missing")
    return psycopg2.connect(dsn)


def _table_columns(cur, table_name: str, *, schema: str = "public") -> set[str]:
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = %s
           AND table_name = %s
        """,
        (schema, table_name),
    )
    return {str(row[0]) for row in cur.fetchall()}


def _resolve_scope(cur) -> tuple[str, str]:
    tenant = os.environ.get("OMEGA_SEED_TENANT_ID", "").strip()
    workspace = os.environ.get("OMEGA_SEED_WORKSPACE_ID", "").strip()
    if tenant and workspace:
        return tenant, workspace
    cur.execute(
        """
        SELECT t.id::text, w.id::text
          FROM workspaces w
          JOIN tenants t ON t.id = w.tenant_id
         ORDER BY
               (t.name = 'Default Tenant' AND w.name = 'Main Workspace') DESC,
               w.created_at ASC NULLS LAST,
               w.id ASC
         LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit("No workspace found. Set OMEGA_SEED_TENANT_ID and OMEGA_SEED_WORKSPACE_ID.")
    return str(row[0]), str(row[1])


def _ensure_gold_table(cur, table_name: str, columns: list[tuple[str, str]]) -> None:
    required = [("tenant_id", "UUID NOT NULL"), ("workspace_id", "UUID NOT NULL"), *columns]
    column_sql = [
        sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(kind))
        for name, kind in required
    ]
    cur.execute(
        sql.SQL("CREATE TABLE IF NOT EXISTS public.{} ({})").format(
            sql.Identifier(table_name),
            sql.SQL(", ").join(column_sql),
        )
    )
    existing = _table_columns(cur, table_name)
    for name, kind in required:
        if name not in existing:
            cur.execute(
                sql.SQL("ALTER TABLE public.{} ADD COLUMN {} {}").format(
                    sql.Identifier(table_name),
                    sql.Identifier(name),
                    sql.SQL(kind),
                )
            )
    cur.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON public.{} (tenant_id, workspace_id)").format(
            sql.Identifier(f"{table_name}_scope_idx"),
            sql.Identifier(table_name),
        )
    )


def _grant_gold_table(cur, table_name: str) -> None:
    cur.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement_gold')")
    if cur.fetchone()[0]:
        cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON public.{} TO omega_refinement_gold").format(sql.Identifier(table_name)))
    cur.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_gold_reader')")
    if cur.fetchone()[0]:
        cur.execute(sql.SQL("GRANT SELECT ON public.{} TO omega_gold_reader").format(sql.Identifier(table_name)))


def _seed_gold_dataset(cur, dataset: str, tenant_id: str, workspace_id: str) -> int:
    payload = DATASETS[dataset]
    table_name = f"gold_{dataset}"
    _ensure_gold_table(cur, table_name, payload["columns"])
    cur.execute(
        sql.SQL("DELETE FROM public.{} WHERE tenant_id::text = %s AND workspace_id::text = %s").format(
            sql.Identifier(table_name)
        ),
        (tenant_id, workspace_id),
    )
    target_columns = gold_table_columns(dataset)
    insert = sql.SQL("INSERT INTO public.{} ({}) VALUES %s").format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(name) for name in target_columns),
    )
    rows = [(tenant_id, workspace_id, *row) for row in payload["rows"]]
    if rows:
        execute_values(cur, insert.as_string(cur.connection), rows)
    cur.execute("SELECT public.omega_apply_gold_rls_for_table(%s)", (table_name,))
    _grant_gold_table(cur, table_name)
    return len(rows)


def _upsert_dataset_catalog(cur, dataset_counts: dict[str, int], tenant_id: str, workspace_id: str) -> None:
    dataset_columns = _table_columns(cur, "datasets")
    for dataset, count in dataset_counts.items():
        payload = DATASETS[dataset]
        cols = [
            "name",
            "description",
            "layer",
            "cartridge",
            "sources",
            "sql_def",
            "column_mapping",
            "schedule",
            "last_refresh",
            "row_count",
            "updated_at",
        ]
        values: list[Any] = [
            dataset,
            payload["description"],
            "gold",
            "replicon",
            Json([f"{SEED_SOURCE}/{dataset}"]),
            "Controlled private-beta Replicon Gold seed; not an external API extraction.",
            Json({name: {"source": SEED_SOURCE, "type": kind} for name, kind in payload["columns"]}),
            None,
            sql.SQL("NOW()"),
            count,
            sql.SQL("NOW()"),
        ]
        if "tenant_id" in dataset_columns:
            cols.append("tenant_id")
            values.append(tenant_id)
        if "workspace_id" in dataset_columns:
            cols.append("workspace_id")
            values.append(workspace_id)

        value_sql = []
        params: list[Any] = []
        for value in values:
            if isinstance(value, sql.SQL):
                value_sql.append(value)
            else:
                value_sql.append(sql.Placeholder())
                params.append(value)
        update_cols = [
            "description",
            "layer",
            "cartridge",
            "sources",
            "sql_def",
            "column_mapping",
            "schedule",
            "last_refresh",
            "row_count",
            "updated_at",
        ]
        if "tenant_id" in cols:
            update_cols.append("tenant_id")
        if "workspace_id" in cols:
            update_cols.append("workspace_id")
        cur.execute(
            sql.SQL("INSERT INTO datasets ({}) VALUES ({}) ON CONFLICT (name) DO UPDATE SET {}").format(
                sql.SQL(", ").join(sql.Identifier(col) for col in cols),
                sql.SQL(", ").join(value_sql),
                sql.SQL(", ").join(
                    sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(col), sql.Identifier(col))
                    for col in update_cols
                ),
            ),
            params,
        )


def _refresh_lineage(cur, dataset_counts: dict[str, int], tenant_id: str, workspace_id: str) -> None:
    cur.execute(
        "DELETE FROM silver_lineage WHERE cartridge_id = %s AND source_batch_id = %s",
        ("replicon", SEED_BATCH_ID),
    )
    for dataset, count in dataset_counts.items():
        payload = DATASETS[dataset]
        cur.execute(
            """
            INSERT INTO silver_lineage (
                silver_name, cartridge_id, source_entity, source_load_date,
                source_batch_id, sql_def, column_mapping, layer, row_count,
                storage_uri, created_by, created_at
            )
            VALUES (%s, %s, %s, CURRENT_DATE, %s, %s, %s, 'gold', %s, %s, %s, NOW())
            """,
            (
                dataset,
                "replicon",
                f"{SEED_SOURCE}/{dataset}",
                SEED_BATCH_ID,
                "Controlled private-beta Replicon Gold seed; no external API extraction.",
                Json({name: {"type": kind} for name, kind in payload["columns"]}),
                count,
                (
                    "postgres_gold://modecissions_gold/public/"
                    f"tenant_id={tenant_id}/workspace_id={workspace_id}/gold_{dataset}"
                ),
                "replicon_beta_seed",
            ),
        )


def _catalog_tags(column_name: str, column_type: str) -> list[str]:
    tags = ["beta_seed", "replicon"]
    lower = column_name.lower()
    if any(token in lower for token in ("costo", "revenue", "margen", "horas", "rating", "wip", "facturacion")):
        tags.append("metric")
    if column_type.startswith(("NUMERIC", "INTEGER")):
        tags.append("numeric")
    if lower in {"usuario", "username", "consultor", "manager_name", "revenue_manager"}:
        tags.append("person")
    return sorted(set(tags))


def _refresh_data_catalog(cur) -> None:
    cur.execute(
        "DELETE FROM data_catalog WHERE cartridge = %s AND dataset = ANY(%s)",
        ("replicon", list(DATASETS)),
    )
    for dataset, payload in DATASETS.items():
        for column_name, column_type in payload["columns"]:
            is_metric = "metric" in _catalog_tags(column_name, column_type)
            cur.execute(
                """
                INSERT INTO data_catalog (
                    dataset, layer, cartridge, column_name, data_type,
                    description, example_values, tags, is_key, is_metric,
                    created_at, updated_at
                )
                VALUES (%s, 'gold', 'replicon', %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (dataset, column_name) DO UPDATE SET
                    layer = EXCLUDED.layer,
                    cartridge = EXCLUDED.cartridge,
                    data_type = EXCLUDED.data_type,
                    description = EXCLUDED.description,
                    example_values = EXCLUDED.example_values,
                    tags = EXCLUDED.tags,
                    is_key = EXCLUDED.is_key,
                    is_metric = EXCLUDED.is_metric,
                    updated_at = NOW()
                """,
                (
                    dataset,
                    column_name,
                    column_type,
                    f"{column_name} in Replicon beta Gold dataset {dataset}.",
                    Json([]),
                    _catalog_tags(column_name, column_type),
                    column_name in {"usuario", "username", "consultor", "proyecto"},
                    is_metric,
                ),
            )


def _upsert_vault_marker(cur, tenant_id: str, workspace_id: str) -> None:
    columns = _table_columns(cur, "vault_entries")
    has_scope = {"tenant_id", "workspace_id"} <= columns
    if has_scope:
        cur.execute(
            """
            DELETE FROM vault_entries
             WHERE tenant_id = %s
               AND workspace_id = %s
               AND scope = 'connections'
               AND cartridge = 'replicon'
               AND key = %s
            """,
            (tenant_id, workspace_id, REPLICON_VAULT_KEY),
        )
    else:
        cur.execute(
            """
            DELETE FROM vault_entries
             WHERE scope = 'connections'
               AND cartridge = 'replicon'
               AND key = %s
            """,
            (REPLICON_VAULT_KEY,),
        )
    insert_cols = ["scope", "cartridge", "key", "value", "created_at", "updated_at"]
    values: list[Any] = ["connections", "replicon", REPLICON_VAULT_KEY, Json(REPLICON_VAULT_MARKER), sql.SQL("NOW()"), sql.SQL("NOW()")]
    if has_scope:
        insert_cols = ["tenant_id", "workspace_id", *insert_cols]
        values = [tenant_id, workspace_id, *values]
    if "value_encrypted" in columns:
        insert_cols.append("value_encrypted")
        values.append(None)

    value_sql = []
    params: list[Any] = []
    for value in values:
        if isinstance(value, sql.SQL):
            value_sql.append(value)
        else:
            value_sql.append(sql.Placeholder())
            params.append(value)
    cur.execute(
        sql.SQL("INSERT INTO vault_entries ({}) VALUES ({})").format(
            sql.SQL(", ").join(sql.Identifier(col) for col in insert_cols),
            sql.SQL(", ").join(value_sql),
        ),
        params,
    )


def seed_replicon_beta_gold() -> dict[str, Any]:
    _load_env_file()
    operational = _connect_operational()
    try:
        with operational.cursor() as cur:
            tenant_id, workspace_id = _resolve_scope(cur)
            _upsert_vault_marker(cur, tenant_id, workspace_id)
            operational.commit()
    finally:
        operational.close()

    dataset_counts: dict[str, int] = {}
    gold = _connect_gold()
    try:
        with gold.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                (tenant_id, workspace_id),
            )
            for dataset in sorted(DATASETS):
                dataset_counts[dataset] = _seed_gold_dataset(cur, dataset, tenant_id, workspace_id)
        gold.commit()
    finally:
        gold.close()

    operational = _connect_operational()
    try:
        with operational.cursor() as cur:
            _upsert_dataset_catalog(cur, dataset_counts, tenant_id, workspace_id)
            _refresh_lineage(cur, dataset_counts, tenant_id, workspace_id)
            _refresh_data_catalog(cur)
        operational.commit()
    finally:
        operational.close()

    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "datasets": dataset_counts,
        "rows": sum(dataset_counts.values()),
        "vault_marker": REPLICON_VAULT_KEY,
    }


def main() -> None:
    result = seed_replicon_beta_gold()
    counts = ", ".join(f"{name}={count}" for name, count in sorted(result["datasets"].items()))
    print(
        "seeded Replicon beta Gold "
        f"workspace={result['workspace_id']} tenant={result['tenant_id']} "
        f"rows={result['rows']} datasets=[{counts}] "
        f"vault_marker={result['vault_marker']} status=data_seed_only"
    )


if __name__ == "__main__":
    main()
