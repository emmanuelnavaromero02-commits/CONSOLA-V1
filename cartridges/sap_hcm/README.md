# SAP HCM Cartridge

FastAPI cartridge that ingests SAP HCM (on-premise NetWeaver Gateway
OData services for SAP_HR / Personnel Administration) into the
MODecissions Bronze layer.

| Property | Value |
| --- | --- |
| **Cartridge ID** | `sap_hcm` |
| **Auth (to SAP)** | HTTP Basic + `sap-client` mandant |
| **Protocol** | NetWeaver Gateway OData v2 (JSON) |
| **Port** | `8202` (matches `Dockerfile` CMD) |
| **Internal auth** | `X-Internal-Api-Key` header |

## What it integrates

PA infotypes (Personnel Administration):
- `PA0000Set` Actions · `PA0001Set` Organizational Assignment ·
  `PA0002Set` Personal Data · `PA0006Set` Addresses · `PA0007Set`
  Planned Working Time · `PA0008Set` Basic Pay · `PA0009Set` Bank
  Details · `PA0014Set` Recurring Payments · `PA0015Set` Additional
  Payments · `PA0019Set` Monitoring of Tasks · `PA0021Set` Family ·
  `PA0041Set` Date Specifications · `PA0105Set` Communication

Time Management:
- `PA2001Set` Absences · `PA2002Set` Attendances · `PA2006Set` Absence
  Quotas

Organizational Management (HRP):
- `HRP1000Set` Object header · `HRP1001Set` Relationships

Full list with watermark / select fields:
[`app/config/entities.yaml`](app/config/entities.yaml).

> **Heads-up on `SAP_HCM_BASE_URL`.** SAP HCM exposes many small services
> (one per functional area) under `/sap/opu/odata/sap/`. Point
> `SAP_HCM_BASE_URL` at `/sap/opu/odata/sap` (NOT at one specific
> service) and use the per-entity `odata_entity:` field in
> `app/config/entities.yaml` to spell out `<SERVICE>/<EntitySet>` per
> entity. The default catalogue ships with realistic mappings (PA
> infotypes → `HRPA_EE_PA_SRV`, time → `HRESS_TEAM_SRV` /
> `HRESS_TIMEACCOUNT_SRV`, OM → `HRORG_OBJECT_SRV`). Confirm the actual
> service names against your tenant — they vary slightly between SAP
> releases / namespaces.

## Entidades extraídas (Bloque A)

Las 10 entidades alineadas en `app/config/entities.yaml` (nombre de negocio →
entityset OData):

| Entidad | OData (`odata_entity`) | Modo | Notas |
| --- | --- | --- | --- |
| `EmployeeMaster` | `HRPA_EE_PA_SRV/PA0001Set` | incremental | Asignación org (Pernr shadowed) |
| `PersonalData` | `HRPA_EE_PA_SRV/PA0002Set` | incremental | Nombre masked, fecha nac. encrypted |
| `EmployeeActions` | `HRPA_EE_PA_SRV/PA0000Set` | incremental | Altas/bajas/traslados |
| `ContractData` | `HRPA_EE_PA_SRV/PA0016Set` | incremental | Elementos de contrato |
| `WorkSchedule` | `HRPA_EE_PA_SRV/PA0007Set` | full | Horario planificado |
| `LeaveAbsence` | `HRESS_TEAM_SRV/PA2001Set` | incremental | Ausencias |
| `CostCenter` | `HRPA_EE_PA_SRV/PA0001Set` | full | Subconjunto de PA0001 (Kostl) |
| `OrgUnit` | `HRORG_OBJECT_SRV/HRP1000Set` (`Otype eq 'O'`) | full | Unidades organizacionales |
| `Position` | `HRORG_OBJECT_SRV/HRP1000Set` (`Otype eq 'S'`) | full | Posiciones |
| `JobCode` | `HRORG_OBJECT_SRV/HRP1000Set` (`Otype eq 'C'`) | full | Trabajos |

## Datasets (Bloque B)

21 datasets en `datasets/` (sembrados en la tabla `datasets` vía
`infra/init/80_sap_hcm_datasets_seed.sql`). Privacy by design: ningún gold
expone campos `shadowed`/`encrypted` en crudo; `pernr` viaja como hash estable
(FK) y los nombres llegan ya enmascarados desde bronze.

**Silver — 10 `*_latest` (una extracción más reciente, tipada por entidad):**

| Dataset | Fuente | Descripción |
| --- | --- | --- |
| `sap_hcm_employeemaster_latest` | EmployeeMaster | Asignación org tipada |
| `sap_hcm_personaldata_latest` | PersonalData | Datos personales (ya protegidos) |
| `sap_hcm_employeeactions_latest` | EmployeeActions | Acciones de personal |
| `sap_hcm_contractdata_latest` | ContractData | Elementos de contrato |
| `sap_hcm_orgunit_latest` | OrgUnit | Unidades organizacionales |
| `sap_hcm_position_latest` | Position | Posiciones |
| `sap_hcm_jobcode_latest` | JobCode | Trabajos / clasificaciones |
| `sap_hcm_costcenter_latest` | CostCenter | Asignación de centro de costo |
| `sap_hcm_leaveabsence_latest` | LeaveAbsence | Ausencias por empleado/tipo |
| `sap_hcm_workschedule_latest` | WorkSchedule | Horario de trabajo |

**Silver — 3 curados (multi-entidad):**

| Dataset | Descripción |
| --- | --- |
| `sap_hcm_employee_master_full` | Vista 360: PA0001 + PA0002 + PA0016 + nombre de org/posición |
| `sap_hcm_org_hierarchy` | Unidades org (jerarquía plana; padre pendiente de HRP1001) |
| `sap_hcm_position_assignment_latest` | Posiciones con titular y marca de vacante |

**Gold — 8 de negocio:**

| Dataset | Descripción |
| --- | --- |
| `headcount_by_department` | Empleados activos por unidad org |
| `headcount_by_costcenter` | Empleados activos por centro de costo |
| `headcount_by_position_type` | Distribución por grupo/subgrupo de personal |
| `absence_balance_by_employee` | Días de ausencia por empleado/tipo (12 meses) |
| `absence_by_type_and_month` | Tendencia mensual de ausencias por tipo |
| `manager_hierarchy` | Árbol de supervisión (manager pendiente de HRP1001/Sbrtr) |
| `employees_anomalies` | Detección automática de irregularidades (mejora propia, prep. Fase 3) |
| `workforce_cost_monthly` | Costo de nómina estimado (pendiente de extraer PA0008) |

## Apps publicadas

Dashboards HTML standalone en `apps/` (Chart.js, tema oscuro). Se registran en
`analytic_apps` vía la migración `83_sap_hcm_apps_seed.sql` y se reconcilian al
arrancar la consola (`seed_packaged_apps.py` lee `apps/*.html` + `*.json`). Las
apps leen los golds vía `GET /api/data/<dataset>` (sin prefijo `gold_`). Nombres
de archivo con prefijo `sap_hcm_` para evitar colisión con otros cartuchos.

| App | Propósito | Datasets gold |
| --- | --- | --- |
| `sap_hcm_headcount_dashboard` | Panorama operativo: plantilla activa por departamento, centro de costo y tipo de posición. KPIs + barras top 10 + donut, con filtro por departamento. | `headcount_by_department`, `headcount_by_costcenter`, `headcount_by_position_type` |
| `sap_hcm_people_quality_dashboard` | Salud operativa: anomalías de datos, tendencia mensual de ausencias y span of control. 4 KPIs + tabla de anomalías + line chart + histograma, con filtro por tipo de anomalía. | `employees_anomalies`, `absence_by_type_and_month`, `manager_hierarchy` |

> El span of control y la jerarquía de managers son parciales: el vínculo manager
> requiere extracción de HRP1001/Sbrtr (pendiente), por lo que esos widgets muestran
> estado vacío hasta que el bronze incluya la jerarquía.

## Conocimiento del dominio

Knowledge Bits en `app/config/knowledge_bits.yaml`. Se cargan en `kb_config` al
arrancar y el copiloto los ejecuta en DuckDB sobre parquet (no pggold).

**Base (Bloque A, leen bronze):** `kb_headcount_snapshot`,
`kb_employee_actions_30d`, `kb_absence_analysis`, `kb_org_hierarchy`,
`kb_contract_type_distribution`.

**Bloque C (leen los golds del Bloque B en `gold/sap_hcm/<name>/`):**

| KB | Pregunta de negocio | Gold |
| --- | --- | --- |
| `kb_sap_hcm_headcount_active_by_department` | ¿Cuántos empleados activos por departamento? | headcount_by_department |
| `kb_sap_hcm_headcount_by_costcenter` | ¿Cómo se distribuye el headcount por centro de costo? | headcount_by_costcenter |
| `kb_sap_hcm_absence_top_employees` | ¿Qué empleados tienen más días de ausencia (12m)? | absence_balance_by_employee |
| `kb_sap_hcm_absence_trend_monthly` | ¿Cómo evoluciona el ausentismo mes a mes? | absence_by_type_and_month |
| `kb_sap_hcm_employees_anomalies_active` | ¿Qué anomalías de personal tengo? | employees_anomalies |
| `kb_sap_hcm_manager_span_of_control` | ¿Span of control de mis managers? (parcial: requiere HRP1001/Sbrtr) | manager_hierarchy |
| `kb_sap_hcm_workforce_composition_by_position_type` | ¿Composición de plantilla por tipo? | headcount_by_position_type |

**Hints del asistente (Bloque E):** `hints/assistant.md` se carga en
`cartridges.assistant_hints` al arrancar (`seed_packaged_hints.py`) y el copiloto lo
inyecta a su system prompt. Cubre identidad del cartucho, modelo de datos esencial,
convenciones, reglas operativas, apps publicadas y limitaciones honestas.

## Agentes especializados

Definidos en `infra/init/84_sap_hcm_agents_seed.sql` (y espejados en
`config/seed.sql`), en la tabla `agents` (cartridge-scoped, sin `workspace_id`). La
plataforma aún no enruta por triggers; los agentes se invocan por `cartridge_id + slug`
y las frases de trigger viven en `extra` como metadata de intención.

| Agente (slug) | Nombre | Especialidad | Golds / KBs primarios | Triggers (intención) |
| --- | --- | --- | --- | --- |
| `sap_hcm_auditor_org_chart` | Auditor de Organigrama | Detección de problemas estructurales y de calidad de datos | `gold_employees_anomalies`, `gold_manager_hierarchy` · `kb_sap_hcm_employees_anomalies_active`, `kb_sap_hcm_manager_span_of_control` | "qué problemas tengo en mi plantilla", "auditoría de personal", "empleados sin centro de costo", "anomalías de empleados", "calidad de datos de empleados" |
| `sap_hcm_analista_workforce` | Analista de Plantilla | Composición y dinámica de la plantilla | `gold_headcount_by_*`, `gold_absence_by_type_and_month` · `kb_sap_hcm_headcount_active_by_department`, `kb_sap_hcm_headcount_by_costcenter`, `kb_sap_hcm_workforce_composition_by_position_type`, `kb_sap_hcm_absence_trend_monthly` | "cómo se compone mi plantilla", "headcount por departamento", "evolución de ausencias", "distribución de empleados", "tendencias de personal" |

## Environment variables

Required to talk to SAP:

| Variable | Purpose |
| --- | --- |
| `SAP_HCM_BASE_URL` | Service root, e.g. `https://sap.example.com:44300/sap/opu/odata/sap/HRPA_EE_PA_SRV` |
| `SAP_HCM_USER` | Technical user |
| `SAP_HCM_PASS` | Password |

Optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SAP_HCM_CLIENT_MANDANT` | `100` | Logical client / mandant |
| `SAP_HCM_CLIENT` | — | Legacy alias of `SAP_HCM_CLIENT_MANDANT` (still accepted) |

Storage / infra — same as the other cartridges:
`INTERNAL_API_KEY`, `DATABASE_URL`, `MINIO_*`, optional `REFINEMENT_URL`,
`AIRFLOW_*`.

## Endpoints

Same shape as the SuccessFactors cartridge.

### Public

```
GET  /health
```

### Authenticated (`X-Internal-Api-Key` required)

Console-style aliases:

```
GET  /entities
GET  /entities/{entity}/schema
GET  /entities/{entity}/preview?limit=N
POST /entities/{entity}/extract?mode=full|incremental|historical&from_date=&to_date=
POST /extract-all?mode=incremental|full
GET  /runs
GET  /runs/latest
GET  /watermarks
GET  /health/sap_hcm
```

Legacy `/skills/*` mirrors all the above.

MCP:

```
GET  /mcp/tools
POST /mcp/invoke
POST /mcp-reload
GET  /mcp/rpc/
```

## Run locally (without Docker)

```bash
cd cartridges/sap_hcm
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export INTERNAL_API_KEY=$(openssl rand -hex 32)
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/modecissions"
export SAP_HCM_BASE_URL=""   # leave empty to see "degraded" responses

uvicorn app.main:app --host 0.0.0.0 --port 8202 --reload
```

## Run with Docker

```bash
docker build -t sap-hcm cartridges/sap_hcm
docker run --rm -p 8202:8202 \
  -e INTERNAL_API_KEY=$INTERNAL_API_KEY \
  -e DATABASE_URL=$DATABASE_URL \
  -e MINIO_ENDPOINT=$MINIO_ENDPOINT \
  -e MINIO_ACCESS_KEY=$MINIO_ACCESS_KEY \
  -e MINIO_SECRET_KEY=$MINIO_SECRET_KEY \
  -e MINIO_BUCKET=lakehouse \
  -e SAP_HCM_BASE_URL=$SAP_HCM_BASE_URL \
  -e SAP_HCM_USER=$SAP_HCM_USER \
  -e SAP_HCM_PASS=$SAP_HCM_PASS \
  -e SAP_HCM_CLIENT_MANDANT=$SAP_HCM_CLIENT_MANDANT \
  sap-hcm
```

## Smoke tests

```bash
curl -s http://localhost:8202/health
curl -i http://localhost:8202/entities | head -1                    # → 401
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8202/entities | jq '.entities[].entity'
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8202/entities/PA0001Set/schema | jq
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8202/health/sap_hcm
```

## What happens when credentials are missing

```json
{
  "status": "degraded",
  "configured": false,
  "missing": ["SAP_HCM_BASE_URL", "SAP_HCM_USER", "SAP_HCM_PASS"]
}
```

No mocks, no fake rows.

## What's still pending for a real-SAP run

- Activate the SAP OData services in `/n SICF` and confirm the user has
  PA / PT / OM read authority.
- Verify the OData entity set names against your specific gateway —
  customers occasionally rename `PA0001Set` → `EmployeeBasicData`, etc.
- Decide whether to keep a single `SAP_HCM_BASE_URL` (one service) or
  introduce a per-entity `service:` override (see note above).
- Wire `REFINEMENT_URL` so silver builds run after each Bronze land.
