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
> (one per functional area) under `/sap/opu/odata/sap/`. The current
> design points `SAP_HCM_BASE_URL` at a single service root and asks for
> entities by their EntitySet name relative to that root. If your
> infotypes live across multiple services (typical), either point
> `SAP_HCM_BASE_URL` at the most-used service and override per entity in
> a follow-up patch, or extend `entities.yaml` with a per-entity
> `service:` field (not implemented in this PR).

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
