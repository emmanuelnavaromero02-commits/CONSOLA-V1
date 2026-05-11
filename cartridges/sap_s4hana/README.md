# SAP S/4HANA Cartridge

FastAPI cartridge that ingests SAP S/4HANA (on-premise or Cloud
edition) master and transactional data into the MODecissions Bronze
layer via the public S/4HANA OData APIs.

| Property | Value |
| --- | --- |
| **Cartridge ID** | `sap_s4hana` |
| **Auth (to SAP)** | HTTP Basic + optional `APIKey` (SAP API Hub trial) |
| **Protocol** | OData v2 (JSON) |
| **Port** | `8204` (matches `Dockerfile` CMD) |
| **Internal auth** | `X-Internal-Api-Key` header |

## What it integrates

Master data:
- `BusinessPartner`, `Customer`, `Supplier`, `BusinessPartnerAddress`
- `Product`, `ProductDescription`
- `CompanyCode`, `CostCenter`, `ProfitCenter`, `GLAccount`

Sales (SD): `SalesOrder`, `SalesOrderItem`, `SalesOrderScheduleLine`,
`BillingDocument`, `BillingDocumentItem`.

Procurement (MM): `PurchaseOrder`, `PurchaseOrderItem`,
`PurchaseRequisitionHeader`, `PurchaseRequisitionItem`, `SupplierInvoice`.

Finance (FI / CO): `GLAccountLineItem`, `OPLAcctgDocItemCube`,
`JournalEntryItem` (ACDOCA).

Inventory: `MatlStkInAcctMod`, `MaterialDocumentHeader`.

Full list, including the real OData path in `odata_entity:` and the
URL-safe id in `entity:`, lives in
[`app/config/entities.yaml`](app/config/entities.yaml).

> S/4HANA standard APIs live under separate service paths
> (`API_BUSINESS_PARTNER`, `API_SALES_ORDER_SRV`, etc). The cartridge
> stores the OData path in `odata_entity:` so HTTP routes can use a
> short, URL-safe `entity:` id (e.g. `BusinessPartner`) without breaking
> on slashes.

## Environment variables

Required to talk to SAP (canonical names):

| Variable | Purpose |
| --- | --- |
| `SAP_S4_BASE_URL` | API service root, e.g. `https://my-s4.example.com:44300/sap/opu/odata/sap` |
| `SAP_S4_USER` | Technical user |
| `SAP_S4_PASS` | Password |

Optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SAP_S4_CLIENT_MANDANT` | `100` | Logical client / mandant |
| `SAP_S4_API_KEY` | — | `APIKey` header for SAP API Hub trial / sandbox |

Back-compat (legacy short names, still honoured): `S4_BASE_URL`,
`S4_USER`, `S4_PASS`, `S4_CLIENT_MANDANT`, `S4_API_KEY`.

Storage / infra: `INTERNAL_API_KEY`, `DATABASE_URL`, `MINIO_*`,
optional `REFINEMENT_URL`, `AIRFLOW_*`.

## Endpoints

Same shape as the SuccessFactors and HCM cartridges.

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
GET  /health/sap_s4hana
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
cd cartridges/sap_s4hana
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export INTERNAL_API_KEY=$(openssl rand -hex 32)
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/modecissions"
export SAP_S4_BASE_URL=""    # leave empty to see "degraded" responses

uvicorn app.main:app --host 0.0.0.0 --port 8204 --reload
```

## Run with Docker

```bash
docker build -t sap-s4hana cartridges/sap_s4hana
docker run --rm -p 8204:8204 \
  -e INTERNAL_API_KEY=$INTERNAL_API_KEY \
  -e DATABASE_URL=$DATABASE_URL \
  -e MINIO_ENDPOINT=$MINIO_ENDPOINT \
  -e MINIO_ACCESS_KEY=$MINIO_ACCESS_KEY \
  -e MINIO_SECRET_KEY=$MINIO_SECRET_KEY \
  -e MINIO_BUCKET=lakehouse \
  -e SAP_S4_BASE_URL=$SAP_S4_BASE_URL \
  -e SAP_S4_USER=$SAP_S4_USER \
  -e SAP_S4_PASS=$SAP_S4_PASS \
  -e SAP_S4_CLIENT_MANDANT=$SAP_S4_CLIENT_MANDANT \
  -e SAP_S4_API_KEY=$SAP_S4_API_KEY \
  sap-s4hana
```

## Smoke tests

```bash
curl -s http://localhost:8204/health
curl -i http://localhost:8204/entities | head -1                    # → 401
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8204/entities | jq '.entities[].entity'
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8204/entities/BusinessPartner/schema | jq
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
     http://localhost:8204/health/sap_s4hana
```

## What happens when credentials are missing

```json
{
  "status": "degraded",
  "configured": false,
  "missing": ["SAP_S4_BASE_URL", "SAP_S4_USER", "SAP_S4_PASS"]
}
```

No mocks, no fake rows.

## What's still pending for a real-SAP run

- Activate the standard S/4HANA OData APIs you need
  (`API_BUSINESS_PARTNER`, `API_SALES_ORDER_SRV`, `API_JOURNALENTRYITEM_SRV`,
  ...) in your tenant — they're standard but per-tenant gated.
- Confirm the technical user has read authority on each service.
- For SAP API Hub trials, request an API key and pass it as
  `SAP_S4_API_KEY`.
- Verify watermark field names (`LastChangeDate` vs `LastChangeDateTime`)
  on your release — they differ slightly between S/4HANA releases.
- Wire `REFINEMENT_URL` so silver builds run after each Bronze land.
