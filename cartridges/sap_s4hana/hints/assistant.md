# SAP S/4HANA — Hints para el asistente

Cuando un usuario pregunta en el contexto del cartucho SAP S/4HANA, el asistente
sigue estas reglas además de las globales.

## 1. Identidad del cartucho

- **Cartucho:** SAP S/4HANA — ERP empresarial. Dominios: Finanzas, Ventas, Compras,
  Inventario y datos maestros.
- **Idioma de los datos:** los campos vienen en inglés técnico SAP (`BusinessPartner`,
  `SalesOrder`, `BillingDocument`, `GLAccount`, `SoldToParty`...). Responde siempre en
  español de negocio y traduce el término técnico a su significado.
- **Cobertura:** 25 entidades extraídas vía OData (maestros + ventas + compras + finanzas
  + inventario).
- **Modelo de 3 capas:** raw (bronze 1:1 con OData) → silver (snapshot vigente, snake_case)
  → gold (agregados y KPIs en `pggold.gold_*`).

## 2. Modelo de datos esencial

- **Maestros:** `BusinessPartner`, `Customer`, `Supplier`, `Product`, `CompanyCode`,
  `CostCenter`, `ProfitCenter`, `GLAccount`.
- **Ventas:** `SalesOrder` + `SalesOrderItem` → `BillingDocument` + `BillingDocumentItem`.
- **Compras:** `PurchaseRequisitionHeader` + Item → `PurchaseOrder` + `PurchaseOrderItem`
  → `SupplierInvoice`.
- **Finanzas:** `JournalEntryItem` / `GLAccountLineItem` (ACDOCA, Universal Journal).
- **Inventario:** `MaterialDocumentHeader` (movimientos de material).
- **Shadowing asimétrico (importante):** `Customer`/`Supplier` en los maestros están
  shadowed (hash), pero `SoldToParty`/`Supplier` en los documentos transaccionales están
  en claro. Por eso un join maestro↔transaccional **no casa por hash**: no cruces esas
  claves directamente.
- **Golds disponibles (`pggold.gold_*`):** `gold_revenue_by_customer`,
  `gold_open_sales_orders`, `gold_overdue_billing`, `gold_purchase_spend_by_supplier`,
  `gold_gl_balance_by_account`, `gold_inventory_movement_summary`,
  `gold_cost_center_expense`, `gold_business_partner_anomalies`.

## 3. Convenciones

- **Revenue** = `NetAmount` de `BillingDocument` agregado (en el gold, `revenue` por
  `customer_code` y `revenue_month`).
- **Backlog** = pedidos de venta con líneas no completadas (`open_orders`, `open_value`).
- **Vencido** = factura cuyo vencimiento ya pasó y sigue sin pagar; hoy el vencimiento se
  **estima** a 30 días desde la fecha del documento (ver limitaciones).
- **Año fiscal** = año calendario (no hay split de año fiscal observable en el bronze);
  el gold contable agrega por `fiscal_year`.
- **Montos** siempre en `TransactionCurrency` (columna `currency` en los golds).

## 4. Reglas operativas

1. **Revenue y top clientes** → usa `gold_revenue_by_customer`. KBs:
   `kb_sap_s4hana_revenue_top_customers`, `kb_sap_s4hana_revenue_by_month`.
2. **Backlog de ventas** → usa `gold_open_sales_orders`. KB:
   `kb_sap_s4hana_open_sales_backlog`.
3. **Cartera / cobranza** → usa `gold_overdue_billing` (parcial). KB:
   `kb_sap_s4hana_overdue_invoices`.
4. **Gasto en proveedores** → usa `gold_purchase_spend_by_supplier`. KB:
   `kb_sap_s4hana_top_suppliers_spend`.
5. **Balance contable** → usa `gold_gl_balance_by_account` (real, desde `JournalEntryItem`).
   KB: `kb_sap_s4hana_gl_balance_summary`.
6. **Inventario** → usa `gold_inventory_movement_summary` (parcial, header-level). KB:
   `kb_sap_s4hana_inventory_movements_recent`.
7. **Anomalías de partners** → usa `gold_business_partner_anomalies`. KB:
   `kb_sap_s4hana_business_partner_anomalies`.
8. Antes de inventar SQL, confirma columnas reales con `search_rag` o `get_schema`. Al
   leer `raw/sap_s4hana/*` con `read_parquet`, incluye siempre
   `hive_partitioning=true, union_by_name=true`.
9. Si la pregunta no la cubre ningún gold, baja al silver correspondiente. Si tampoco el
   silver la cubre, explica la limitación y sugiere una alternativa; escala al admin con
   `request_admin_help`.

## 5. Apps publicadas

- `sap_s4hana_sales_overview` — vista comercial ejecutiva: revenue mensual, top clientes,
  backlog por antigüedad y anomalías de partners.
- `sap_s4hana_finance_dashboard` — vista CFO: saldo contable, facturas vencidas, mora
  promedio y gasto en proveedores.

## 6. Limitaciones honestas

- **PaymentStatus no extraído** (vive en partidas abiertas de FI, `A_OperationalAcctgDocItem`)
  → `gold_overdue_billing` es parcial: el vencimiento se **estima** a 30 días, no es el
  estado real de pago.
- **AccountAssignment no extraído** → `gold_cost_center_expense` queda con `total_expense`
  como TODO (stub nulo) hasta tener la asignación de centro de costo por línea de OC.
- **`gold_inventory_movement_summary` es header-level**: solo `posting_month` y conteo de
  documentos; el detalle por material/cantidad/valor (`A_MaterialDocumentItem`) no se extrae.
- **Balance contable solo por `fiscal_year`** (sin granularidad mensual en el gold).
- **Customer/Supplier maestros vs transaccionales no casan por shadowing asimétrico**:
  no cruces el hash del maestro con el `SoldToParty`/`Supplier` en claro del documento.
