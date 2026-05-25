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

## Entidades extraídas (Bloque A)

25 entidades ERP alineadas en `app/config/entities.yaml` (nombre de negocio →
entityset OData), cubriendo Maestros, Ventas (SD), Compras (MM), Finanzas (FI/CO)
e Inventario:

| Dominio | Entidades |
| --- | --- |
| Partners | `BusinessPartner`, `Customer`, `Supplier`, `BusinessPartnerAddress` |
| Productos | `Product`, `ProductDescription` |
| Organización | `CompanyCode`, `CostCenter`, `ProfitCenter`, `GLAccount` |
| Ventas | `SalesOrder`, `SalesOrderItem`, `SalesOrderScheduleLine`, `BillingDocument`, `BillingDocumentItem` |
| Compras | `PurchaseOrder`, `PurchaseOrderItem`, `PurchaseRequisitionHeader`, `PurchaseRequisitionItem`, `SupplierInvoice` |
| Finanzas | `GLAccountLineItem`, `OPLAcctgDocItemCube`, `JournalEntryItem` |
| Inventario | `MatlStkInAcctMod`, `MaterialDocumentHeader` |

Protección por campo: ids de partner (`BusinessPartner`/`Customer`/`Supplier`/
`InvoicingParty`) `shadowed`; impuestos `masked`; banca (`IBAN`/`BankAccount`/
`SwiftCode`/…) `encrypted`; dirección `masked`.

## Datasets (Bloque B)

27 datasets en `datasets/` (sembrados vía `infra/init/81_sap_s4hana_datasets_seed.sql`,
con `workspace_id` en cada fila). Privacy by design: ningún gold expone campos
`encrypted`; los ids viajan como hash estable (FK).

**Silver — 15 `*_latest` (por entidad, tipados):** `businesspartner`, `customer`,
`supplier`, `businesspartneraddress`, `product`, `companycode`, `costcenter`,
`glaccount`, `salesorder`, `salesorderitem`, `billingdocument`,
`billingdocumentitem`, `purchaseorder`, `purchaseorderitem`, `supplierinvoice`.

**Silver — 4 curados:**

| Dataset | Descripción |
| --- | --- |
| `sap_s4hana_sales_orders_full` | Pedidos de venta a nivel línea + cabecera |
| `sap_s4hana_purchase_orders_full` | Órdenes de compra a nivel línea + cabecera |
| `sap_s4hana_invoices_full` | Facturas de venta a nivel línea + cabecera |
| `sap_s4hana_business_partner_full` | Vista 360 del partner (roles cliente/proveedor + dirección) |

**Gold — 8 de negocio:**

| Dataset | Descripción |
| --- | --- |
| `revenue_by_customer` | Ingresos por cliente y mes |
| `open_sales_orders` | Backlog de pedidos abiertos por cliente |
| `overdue_billing` | Aging de cartera (vencimiento estimado; estado de pago pendiente de FI) |
| `purchase_spend_by_supplier` | Gasto de compras por proveedor y mes |
| `gl_balance_by_account` | Saldo contable por sociedad/cuenta/ejercicio (ACDOCA) |
| `inventory_movement_summary` | Movimientos de inventario por mes (cabecera; cantidades pendientes de item) |
| `cost_center_expense` | Gasto por centro de costo (pendiente de asignación contable de compra) |
| `business_partner_anomalies` | Detección automática de irregularidades (mejora propia, prep. Fase 3) |

> **Nota de privacidad/modelo:** `Customer`/`Supplier` están `shadowed` en sus
> maestros, pero `SoldToParty`/`Supplier` en documentos transaccionales van en
> claro (esas entidades no tienen regla de protección), así que no se puede unir
> el maestro de cliente/proveedor por id desde ventas/compras; se usa el código
> del documento como dimensión. La vista 360 de partner sí une bien porque
> BusinessPartner/Customer/Supplier comparten el id con el mismo hash.

## Apps publicadas

Dashboards HTML standalone en `apps/` (Chart.js, tema oscuro). Se registran en
`analytic_apps` vía la migración `85_sap_s4hana_apps_seed.sql` y se reconcilian al
arrancar la consola (`seed_packaged_apps.py` lee `apps/*.html` + `*.json`). Las
apps leen los golds vía `GET /api/data/<dataset>` (sin prefijo `gold_`). Nombres
de archivo con prefijo `sap_s4hana_` para evitar colisión con otros cartuchos.

| App | Propósito | Datasets gold |
| --- | --- | --- |
| `sap_s4hana_sales_overview` | Dashboard ejecutivo de ventas: revenue mensual, top clientes, backlog abierto por antigüedad y anomalías de business partners. | `revenue_by_customer`, `open_sales_orders`, `business_partner_anomalies` |
| `sap_s4hana_finance_dashboard` | Dashboard ejecutivo financiero: saldo contable por cuenta/año fiscal, facturas vencidas, mora promedio y gasto en proveedores. | `gl_balance_by_account`, `overdue_billing`, `purchase_spend_by_supplier` |

> Adaptaciones a columnas reales: `open_sales_orders` no expone estado de pedido, así
> que el donut muestra backlog **por antigüedad**; `gl_balance_by_account` solo tiene
> grano de año fiscal (no mensual), así que la serie es **por año fiscal**. `overdue_billing`
> estima el vencimiento a 30 días (PaymentStatus no extraído). El filtro por compañía
> aplica a los widgets de saldo contable (los demás golds no llevan `company_code`).

## Conocimiento del dominio

Knowledge Bits en `app/config/knowledge_bits.yaml`. Se cargan en `kb_config` al
arrancar y el copiloto los ejecuta en DuckDB sobre parquet (no pggold).

**Base (leen bronze, materializan a silver):** `kb_open_sales_orders`,
`kb_overdue_invoices`, `kb_purchase_spend_by_supplier`, `kb_gl_balance_by_account`.

**Bloque C (leen los golds del Bloque B en `gold/sap_s4hana/<name>/`):**

| KB | Pregunta de negocio | Gold |
| --- | --- | --- |
| `kb_sap_s4hana_revenue_top_customers` | ¿Top 10 clientes por revenue YTD? | revenue_by_customer |
| `kb_sap_s4hana_revenue_by_month` | ¿Cómo evoluciona el revenue mes a mes? | revenue_by_customer |
| `kb_sap_s4hana_open_sales_backlog` | ¿Cuál es el backlog de pedidos abiertos? | open_sales_orders |
| `kb_sap_s4hana_overdue_invoices` | ¿Qué facturas tengo vencidas? (parcial) | overdue_billing |
| `kb_sap_s4hana_top_suppliers_spend` | ¿En qué proveedores gasto más este año? | purchase_spend_by_supplier |
| `kb_sap_s4hana_gl_balance_summary` | ¿Balance de cuentas contables principales? | gl_balance_by_account |
| `kb_sap_s4hana_inventory_movements_recent` | ¿Movimientos de inventario recientes? (parcial) | inventory_movement_summary |
| `kb_sap_s4hana_business_partner_anomalies` | ¿Problemas de calidad en business partners? | business_partner_anomalies |

> Parciales por bronze pendiente: `overdue_billing` estima el vencimiento a 30 días
> (PaymentStatus/partidas FI no extraído); `inventory_movement_summary` es conteo a
> nivel de documento (detalle por material en A_MaterialDocumentItem no extraído).

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
