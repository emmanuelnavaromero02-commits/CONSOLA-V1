# SAP Business One — Hints para el asistente

En el contexto del cartucho SAP Business One, sigue estas reglas además de las globales.

## 1. Identidad del cartucho

- **Cartucho:** SAP Business One 10 para grupos de empresas: maestros, venta, compra,
  asientos, inventario, lotes y producción. Fuente HANA (`hana`, por defecto) o SQL Server
  (`mssql`) según `SAP_B1_DIALECT`.
- **Empresas:** `SAP_B1_COMPANIES` lista `alias=ESQUEMA` en HANA o `alias=BASE` en SQL Server
  (una base por empresa, bajo `dbo`). En raw cada fila trae `_company`; en silver y gold, `company`.
- **Cobertura:** 48 tablas de B1 en bronze
  (`raw/sap_b1/<TABLA>/tenant_id=…/workspace_id=…/load_date=…/batch_id=…/`) → silver → gold,
  todos los datasets como `sap_b1_*`. Casos: finanzas, ventas y compras.
- **Nombres:** raw conserva los de B1 (`CardCode`, `ItemCode`, `DocEntry`, `WhsCode`); silver y
  gold usan snake_case (`card_code`, `item_code`, `doc_entry`, `warehouse` o `whs_code`).
  Filtra con el nombre exacto de la columna y responde en español de negocio.

## 2. Modelo de datos esencial

- **Maestros:** `OCRD` socios (CardType C cliente, S proveedor), `OITM` artículos, `OITB`
  grupos de artículos, `OCRG` grupos de socios (el canal), `OSLP` vendedores, `OWHS`
  almacenes, `OACT` cuentas, `OADM` monedas local y de sistema, `ORTT` tipos de cambio.
- **Ventas (ObjType):** `OINV`/`INV1` factura 13, `ORIN`/`RIN1` nota de crédito 14,
  `ODLN`/`DLN1` entrega 15, `ORDN`/`RDN1` devolución 16, `ORDR`/`RDR1` pedido 17.
- **Compras (ObjType):** `OPCH`/`PCH1` factura 18, `ORPC`/`RPC1` nota de crédito 19,
  `OPDN`/`PDN1` entrada de mercancía 20, `OPOR`/`POR1` orden de compra 22. Traslados:
  `OWTR`/`WTR1` 67.
- **Contabilidad:** `OJDT`/`JDT1` (TransType origen, StornoToTr reversa).
- **Inventario y producción:** `OITW` existencias, `OINM` movimientos,
  `OBTN`/`OBTQ` lotes, `IBT1` movimientos de lote, `OITT`/`ITT1` listas de materiales,
  `OWOR`/`WOR1` órdenes de producción.
- **Gold:** `sap_b1_margin_detail_month`, `sap_b1_margin_kpis_month`,
  `sap_b1_margin_consolidated_month`, `sap_b1_margin_reconciliation_month`,
  `sap_b1_kpi_reconciliation`, `sap_b1_sales_by_company_month`,
  `sap_b1_sales_consolidated_month`, `sap_b1_purchases_by_company_month`,
  `sap_b1_pnl_by_company_month`, `sap_b1_intercompany_reconciliation_month`,
  `sap_b1_distributor_scorecard_month`, `sap_b1_sellin_sellout_month`,
  `sap_b1_sellout_by_customer_month`, `sap_b1_batch_expiry`,
  `sap_b1_stock_by_company_warehouse`, `sap_b1_item_coverage`, `sap_b1_material_cost_variance`,
  `sap_b1_supplier_lead_time`, `sap_b1_data_quality`, `sap_b1_entity_model`,
  `sap_b1_load_reconciliation`.

## 3. Convenciones

- **CANCELED:** N vigente, Y cancelado, C documento de cancelación. Solo cuenta
  `canceled = 'N'`.
- **Venta neta** = facturas − notas de crédito, después del descuento de pie:
  `amount_local_net` = `amount_local` × (100 − `doc_discount_pct`) / 100 (en raw, `LineTotal`
  y el `DiscPrcnt` del encabezado).
- **Costo** = el que B1 registra en la línea (estándar o promedio). **Comisión** = venta neta
  × % de la línea (`commission_local`). Por empresa, `margen_contribucion` resta la comisión
  de todas las líneas, también las intercompañía; el grupo solo resta la de ventas externas.
- **Monedas:** local (`amount_local`), del documento (`amount_doc`, `doc_currency`) y de
  sistema (`amount_sys`). Nunca sumes empresas con distinta moneda local:
  `sap_b1_sales_consolidated_month` pone cada moneda en su fila.
- **Intercompañía:** `SAP_B1_INTERCOMPANY` (`empresa:CARDCODE=contraparte`) marca
  `is_intercompany`; la vista de grupo elimina las ventas entre empresas y descuenta la
  utilidad no realizada en la compradora.
- **`OINM`:** una fila por línea; la llave es (TransNum, TransSeq).
- **Diario:** las reversas caen en el mes en que se contabilizan (el periodo cuadra). El P&L
  usa solo ActType I (ingreso) y E (gasto).
- **Leer datos:** usa los silver `sap_b1_*_latest` y `sap_b1_*_lines`, no raw. Si hace falta
  raw (`raw/sap_b1/<TABLA>/**/*.parquet`, `hive_partitioning = true`), deduplica como silver:
  `ROW_NUMBER() OVER (PARTITION BY _company, <llave> ORDER BY
  _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC)` y, en líneas, deja
  solo las de `_source_updated_at` igual a
  `MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry)`. Las tablas de modo full
  se leen solo de la última corrida de cada empresa.
- **Parámetros:** `SAP_B1_BUSINESS_PARAMETERS`, entradas `kind:company:period:key=value`
  (kind account, threshold, setting o branch). Umbrales sin valor en el catálogo:
  `margin_min_pct`, `sellout_sellin_min_pct`, `channel_days_max`, `sellout_growth_min_pct`,
  `distributor_margin_min_pct`, `expiry_exposed_max_pct` y `dq_min_pct` (o por control
  `dq_min_pct_<control>`; si falta, `sap_b1_data_quality` usa 95). Valores por defecto:
  `expiry_red_days` = 30, `expiry_yellow_days` = 60, `expiry_horizon_days` = 90,
  `coverage_red_days` = 30, `coverage_yellow_days` = 60, `planning_horizon_days` = 90,
  `critical_materials_top_n` = 30, `cost_variance_max_pct` = 5,
  `reconciliation_tolerance_pct` = 1, `default_lead_time_days` = 7, `safety_days` = 7,
  `review_period_days` = 14, `lead_time_tolerance_days` = 0. Umbral sin configurar:
  dilo, no lo inventes.

## 4. Reglas operativas

1. **Finanzas:** `margen_bruto`, `margen_contribucion`, `destructores`, `concentracion_top20`
   y `margen_vendedor` → `sap_b1_margin_kpis_month` (columna `indicator`). Detalle en
   `sap_b1_margin_detail_month`; grupo en `sap_b1_margin_consolidated_month`.
2. **Ventas:** `ratio_sellout_sellin` (`sellout_sellin_3m_pct`) y `dias_inventario`
   (`channel_days`) → `sap_b1_distributor_scorecard_month`; `sellout_clinica` →
   `sap_b1_sellout_by_customer_month`; `caducidad_lotes` → `sap_b1_batch_expiry`
   (`alert_level` rojo, amarillo, verde o vencido; `action_option` traslado_filial,
   traslado_empresa o promocion).
3. **Compras:** `dias_cobertura` (`coverage_with_orders_days`) y `oc_vs_necesidad`
   (`open_po_vs_need_pct`) → `sap_b1_item_coverage` (`consumption_basis`: consumo de 90 días
   o plan de producción, el mayor).
   `costo_real_vs_estandar` (`variance_pct`) → `sap_b1_material_cost_variance`. Entregas
   tardías → `sap_b1_supplier_lead_time`.
4. **Herramientas:** en Studio, `query_dataset` está en Modelado y Limpieza (Plata) y en
   Indicadores y KPIs (Oro); `get_schema`, `describe_silver` y `cartridge_run_kb` solo en
   Modelado y Limpieza (Plata); en otra sección pide pasar a esa. En Workspace: `query_dataset`, `get_schema` y
   `describe_silver`. Confirma columnas antes de escribir SQL.
   `control_room__sap_b1_kpis_read` (case margen, ventas, caducidad, abasto, aprendizaje o
   semaforo) solo está en los agentes que la tienen asignada.
5. **KBs:** `kb_sales_by_company_month` (facturas por empresa y mes) y `kb_stock_on_hand`
   (existencias por empresa y almacén).

## 5. Apps publicadas

- `sap_b1_margen` — los cinco indicadores de margen por empresa y grupo, y reconciliaciones.
- `sap_b1_sellout` — semáforo por distribuidora, sell-out por clínica y caducidad por lote.
- `sap_b1_abasto` — cobertura, compras contra necesidad, costo contra estándar y proveedores.

## 6. Limitaciones honestas

- `OSRI`, `SRI1` y el `LogEntry` de `IBT1` están por validar en HANA.
- En `sap_b1_distributor_scorecard_month`, si las vendedoras del mes tienen monedas locales
  distintas, `sell_in_amount_local` y `sell_in_currency` quedan vacíos (las unidades no).
- La existencia de cierre de mes en `sap_b1_sellin_sellout_month` se reconstruye hacia atrás.
- La identidad de cliente entre empresas usa el RFC válido (no el genérico); si no, empresa
  y código (`sap_b1_customer_crosswalk`).
- La corrida de Finanzas cuadra si la diferencia es estrictamente menor que
  `reconciliation_tolerance_pct` (`sap_b1_kpi_reconciliation`).
- No hay conversión de monedas y nada se escribe de vuelta en Business One.
