# SAP SuccessFactors Cartridge

FastAPI cartridge that ingests SAP SuccessFactors (Employee Central /
HXM Core) data into the MODecissions Bronze layer (MinIO parquet) and
exposes a console + MCP API for orchestration.

| Property | Value |
| --- | --- |
| **Cartridge ID** | `sap_successfactors` |
| **Auth (to SAP)** | OAuth2 `client_credentials` |
| **Protocol** | OData v2 (JSON) |
| **Port** | `8203` (matches `Dockerfile` CMD) |
| **Internal auth** | `X-Internal-Api-Key` header |

## What it integrates

Employee Central:
- People: `User`, `PerPerson`, `PerPersonal`, `PerEmail`, `PerPhone`,
  `PerAddressDEFLT`, `PerNationalId`
- Employment: `EmpEmployment`, `EmpJob`, `EmpCompensation`,
  `EmpPayCompRecurring`, `EmpPayCompNonRecurring`,
  `EmpEmploymentTermination`
- Org Foundation Objects: `FOCompany`, `FODepartment`, `FODivision`,
  `FOLocation`, `FOBusinessUnit`, `FOCostCenter`, `FOJobCode`, `Position`
- Time off: `EmployeeTime`, `TimeAccount`, `WorkSchedule`

Full list with watermark / select fields:
[`app/config/entities.yaml`](app/config/entities.yaml).

## Entidades extraídas (Bloque A)

30 entidades en `app/config/entities.yaml` cubriendo 4 módulos HXM:

| Módulo | Entidades |
| --- | --- |
| Employee Central | `User`, `PerPerson`, `PerPersonal`, `PerEmail`, `PerPhone`, `PerAddressDEFLT`, `PerNationalId`, `EmpEmployment`, `EmpJob`, `EmpJob_History`, `EmpCompensation`, `EmpPayCompRecurring`, `EmpPayCompNonRecurring`, `EmpEmploymentTermination` |
| Foundation Objects | `FOCompany`, `FODepartment`, `FODivision`, `FOLocation`, `FOBusinessUnit`, `FOCostCenter`, `FOJobCode`, `Position` |
| Recruiting | `Candidate`, `JobRequisition` |
| Performance & Goals | `GoalPlan` (entityset `Goal`), `PerformanceReview` (entityset `FormHeader`) |
| Learning | `LearningItem` (entityset `Item`) |

Protección: `userId`/`personIdExternal`/`candidateId` `shadowed`; nombre/email
`masked`; `dateOfBirth`/`nationalId`/`paycompValue` `encrypted`.

## Datasets (Bloque B)

30 datasets en `datasets/` (sembrados vía `infra/init/82_sap_successfactors_datasets_seed.sql`,
con `workspace_id` en cada fila). **Todos los nombres llevan prefijo
`sap_successfactors_`** porque `datasets.name` es PK global (HCM ya usa
`headcount_by_department`, `manager_hierarchy`, `employees_anomalies`).

**Silver — 18 `*_latest`:** user, perperson, perpersonal, empemployment, empjob,
empcompensation, emppaycomprecurring, emppaycompnonrecurring,
empemploymenttermination, focompany, fodepartment, fodivision, folocation,
fobusinessunit, fojobcode, position, candidate, jobrequisition.

**Silver — 4 curados:** `employee_360`, `org_structure`, `compensation_full`,
`recruitment_pipeline`.

**Gold — 8:** `headcount_by_department`, `headcount_by_location`,
`headcount_by_company`, `compensation_distribution` (TODO: paycompValue
encrypted), `recruitment_funnel`, `turnover_by_period`, `manager_hierarchy`
(real: EmpJob.managerId), `employees_anomalies` (mejora propia).

> **Nota de privacidad/modelo:** `User.userId` y `PerPerson.personIdExternal`
> están `shadowed`, pero `EmpEmployment/EmpJob.userId` y
> `PerPersonal.personIdExternal` van en claro → el 360 se construye por las
> claves planas de Emp*/PerPersonal y **excluye** User/PerPerson (sus hashes no
> casan). `paycompValue`/`dateOfBirth`/`nationalId` son encrypted: viajan como
> caja negra en silvers y nunca se agregan en golds.

## Apps publicadas

_(Pendiente — Bloque D.)_

## Conocimiento del dominio

Knowledge Bits actuales en `app/config/knowledge_bits.yaml`. KBs sobre los golds
de este bloque llegan en el Bloque C.

## Environment variables

Required to talk to SAP:

| Variable | Purpose |
| --- | --- |
| `SF_BASE_URL` | OData service root, e.g. `https://api{N}.sapsf.{eu|com}/odata/v2` |
| `SF_COMPANY_ID` | SuccessFactors company id (datacenter tenant) |
| `SF_CLIENT_ID` | OAuth client id |
| `SF_CLIENT_SECRET` | OAuth client secret |
| `SF_TOKEN_URL` | OAuth token endpoint, typically `<base>/oauth/token` |

Storage / infra (shared across cartridges):

| Variable | Purpose |
| --- | --- |
| `INTERNAL_API_KEY` | Required for every authenticated route |
| `DATABASE_URL` | `postgresql+psycopg2://user:pass@host:5432/db` |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | MinIO / lakehouse |
| `MINIO_BUCKET` | default `lakehouse` |
| `MINIO_SECURE` | `true`/`false` |

Optional:

| Variable | Purpose |
| --- | --- |
| `REFINEMENT_URL` | Silver refresh trigger after extracts |
| `AIRFLOW_URL` / `AIRFLOW_USER` / `AIRFLOW_PASSWORD` | DAG delegation |

## Endpoints

### Public

```
GET  /health                          → {ok: true, service: "sap_successfactors"}
```

### Authenticated (`X-Internal-Api-Key` required)

Console-style aliases:

```
GET  /entities                        → list catalogue
GET  /entities/{entity}/schema        → per-entity config
GET  /entities/{entity}/preview?limit=N
POST /entities/{entity}/extract?mode=full|incremental|historical&from_date=&to_date=
POST /extract-all?mode=incremental|full
GET  /runs                            → recent runs (optional ?entity=)
GET  /runs/latest                     → newest run
GET  /watermarks                      → all per-entity watermarks
GET  /health/sap_successfactors       → SAP reachability check
```

Legacy `/skills/*` (kept for back-compat; same handlers):

```
GET  /skills/entities
POST /skills/run_full_load/{entity}
POST /skills/run_incremental/{entity}
POST /skills/run_full_load_all
POST /skills/run_incremental_all
POST /skills/run_historical_load/{entity}?from_date=&to_date=
POST /skills/run_historical_load_all?from_date=&to_date=
GET  /skills/get_last_run_status
GET  /skills/get_watermarks
GET  /skills/list_tables
GET  /skills/get_table_schema/{table_id}
GET  /skills/knowledge_bits
POST /skills/run_knowledge_bits/{kb_id}
POST /skills/run_all_knowledge_bits
GET  /skills/get_kb_status
```

MCP:

```
GET  /mcp/tools                       → discovery
POST /mcp/invoke                      → tool execution
POST /mcp-reload                      → reload custom tools
GET  /mcp/rpc/                        → FastMCP Streamable HTTP (JSON-RPC 2.0)
```

## Run locally (without Docker)

```bash
cd cartridges/sap_successfactors
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Minimum env to boot (auth still enforced; routes will return 401 without the key):
export INTERNAL_API_KEY=$(openssl rand -hex 32)
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/modecissions"
# SAP creds — leave unset to see "degraded" responses
export SF_BASE_URL=""

uvicorn app.main:app --host 0.0.0.0 --port 8203 --reload
```

## Run with Docker

```bash
docker build -t sap-successfactors cartridges/sap_successfactors
docker run --rm -p 8203:8203 \
  -e INTERNAL_API_KEY=$INTERNAL_API_KEY \
  -e DATABASE_URL=$DATABASE_URL \
  -e MINIO_ENDPOINT=$MINIO_ENDPOINT \
  -e MINIO_ACCESS_KEY=$MINIO_ACCESS_KEY \
  -e MINIO_SECRET_KEY=$MINIO_SECRET_KEY \
  -e MINIO_BUCKET=lakehouse \
  -e SF_BASE_URL=$SF_BASE_URL \
  -e SF_COMPANY_ID=$SF_COMPANY_ID \
  -e SF_CLIENT_ID=$SF_CLIENT_ID \
  -e SF_CLIENT_SECRET=$SF_CLIENT_SECRET \
  -e SF_TOKEN_URL=$SF_TOKEN_URL \
  sap-successfactors
```

## Smoke tests

```bash
# 1. Liveness (no key needed)
curl -s http://localhost:8203/health

# 2. Without the key — must return 401
curl -i http://localhost:8203/entities | head -1

# 3. With the key — catalogue
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8203/entities | jq '.entities | length'

# 4. Schema for one entity
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8203/entities/User/schema | jq

# 5. Preview Bronze rows (returns degraded if MinIO is empty)
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     "http://localhost:8203/entities/User/preview?limit=5"

# 6. SAP reachability — returns status: degraded when creds are missing
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8203/health/sap_successfactors
```

## What happens when credentials are missing

Endpoints that need SAP (`extract`, `extract-all`, the SAP-reaching health
check) return:

```json
{
  "status": "degraded",
  "configured": false,
  "missing": ["SF_BASE_URL", "SF_CLIENT_ID", ...]
}
```

The cartridge **never** invents data. All `Mock`-style entity fallbacks
were deleted; the only response when SAP is unreachable is the structured
``degraded`` JSON above.

## What's still pending for a real-SAP run

- Provision an SF instance + OAuth client (SF documentation calls this
  "Manage OAuth2 Client Applications").
- Confirm the watermark field names match your tenant — SF allows
  customisation of `lastModifiedDateTime` per entity in some cases.
- Wire `REFINEMENT_URL` so silver builds run after each Bronze land.
- Schedule the DAGs in `dags/sap_successfactors_extract*.py` via Airflow.
