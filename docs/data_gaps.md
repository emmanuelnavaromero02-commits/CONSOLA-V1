# Brechas de datos detectadas en Misión 1 (agregados Finance / Operations / Risk)

Inventario verificado sobre `origin/main` 1.45.228-beta el 2026-09-13. Solo los
datasets de capa **gold** (`cartridges/<c>/datasets/*.sql` con header `(gold)`)
se materializan a Postgres Gold y se resuelven vía
`omega_publication.dataset_publication_heads`; los `*_latest` silver son
parquet y no son consultables desde console. Cada brecha de esta lista es una
misión de extracción o de dataset aparte; ninguna se resolvió en Misión 1 y
ninguna función de agregado la simula.

## Métricas eliminadas (sin tabla Gold que las soporte)

| Métrica | Tabla / columna que falta | Qué habría que extraer |
|---|---|---|
| `finance.budget_vs_actual_by_cost_center` | No existe ninguna columna de presupuesto/plan en ningún cartucho. El gasto real tampoco: `cost_center_expense.total_expense` es `CAST(NULL AS DECIMAL(15,2))` con TODO (`cartridges/sap_s4hana/datasets/cost_center_expense.sql:17`). | **Presupuesto de centro de costo**: plan de CO por centro de costo y período (valores plan de COSP/COSS o el CDS de plan vs real de S/4HANA). **Gasto real**: el enlace compra → centro de costo vive en `A_PurchaseOrderAccountAssignment` (no extraído). Alternativa más corta: `JournalEntryItem` (ACDOCA, `API_JOURNALENTRYITEM_SRV/A_JournalEntryItem`) ya se extrae completo y trae `CostCenter`; un dataset gold `expense_by_cost_center` que agregue `AmountInCompanyCodeCurrency` por `CostCenter`/período daría el "real" sin nueva extracción. El "presupuesto" sigue faltando. |
| `risk.cost_center_overrun` | Misma dependencia que la anterior (presupuesto + real por centro de costo). | Igual que arriba. Cuando existan ambos, implementar una sola CTE compartida y que Risk filtre desviación > 10 % mientras Finance agrega el conteo. |
| `operations.shift_coverage` | No existe ninguna relación de asistencia o turno. `sap_hcm_workschedule_latest` (PA0007) es silver y solo trae `work_schedule_rule` y `employment_percent`; no hay horas asistidas. | **PA2002 Attendances** (`Awart` tipo, `Stdaz` horas, `Begda/Endda`) en `cartridges/sap_hcm/app/config/entities.yaml`, y un dataset gold que cruce horas asistidas contra el horario planificado de PA0007 por unidad organizativa y mes. |

## Métricas entregadas como proxy (lo que falta para la versión completa)

| Métrica entregada | Lo que mide | Lo que falta para la métrica original |
|---|---|---|
| `finance.billable_hours_logged` (por `unbilled_validated_hours`) | Horas `isbillable` de Replicon por proyecto valoradas a tarifa (`consultor_mensual`). | Estado de aprobación del timesheet y enlace a factura. `replicon_timesheet_latest` e `replicon_invoiceitem_latest` son silver `SELECT *`; el WIP real (facturable − facturado) solo existe en las tablas KB `knowledge_bits.replicon_wip_mensual` / `replicon_wip_resumen`, que van a la base principal sin publication head. Falta un dataset **gold** `replicon_wip_mensual` con `billable_amount`, `billed_amount`, `wip_amount` y estado de aprobación. |
| `finance.labor_cost_by_department` (por `payroll_cost_by_org_unit`) | Horas ejecutadas × tarifa de costo por departamento Replicon (`costo_consultor_mensual`). | **PA0008 BasicPay** (`Betrg` importe, `Lga` clase de pago, `Waers`, `Begda/Endda`) no está en `entities.yaml` de sap_hcm; `workforce_cost_monthly.total_base_salary` es NULL con TODO (`cartridges/sap_hcm/datasets/workforce_cost_monthly.sql:21`). En SuccessFactors `paycomp_value` llega cifrado desde bronze y no es agregable (`sap_successfactors_compensation_full.sql`). |
| `operations.absence_rate_company_by_type` (por `absence_rate` por unidad org) | Días hábiles de ausencia por tipo a nivel empresa / (headcount actual × días hábiles). | `absence_by_type_and_month` no trae `org_unit`; el puente `pernr → org_unit_id` está en `sap_hcm_employee_master_full` (silver). Falta un dataset gold `absence_by_org_unit_and_month` que una `sap_hcm_leaveabsence_latest` con `sap_hcm_employee_master_full` agrupado por (mes, org_unit_id) y un denominador de headcount del mismo mes. |
| `operations.data_freshness_by_cartridge` | Horas desde el último `success` en `extraction_runs` / `pipeline_runs`. | No existe SLA de frescura configurable en OMEGA (todos los umbrales están hardcoded en 24 h / 72 h). El agregado usa `sla_hours` o `OPERATIONS_FRESHNESS_SLA_HOURS`. |
| `risk.employment_end_expiry` (por `contract_expiry`) | `employee_360.end_date` de SuccessFactors dentro de 30/60/90 días, excluyendo el centinela 2030+. | **PA0016 ContractData** ya se extrae (`sap_hcm_contractdata_latest`: `contract_type`, `valid_to`) pero es silver. Falta un dataset gold `contract_expiry_by_org_unit` sobre PA0016 + PA0001 (org unit) con vencimientos por ventana. |

## Bug de dataset: `pnl_mensual` nunca alcanza `financial_status = 'ready'`

`cartridges/replicon/datasets/pnl_mensual.sql`:

- `base_currency` está hardcodeado como `NULL::VARCHAR` (línea 291).
- El `CASE` de `financial_status` termina en `ELSE 'missing_base_currency'`
  (líneas 244-250): cuando contrato y facturación están `ready`, el resultado
  es `missing_base_currency`, nunca `ready`.
- En consecuencia **todas** las columnas condicionadas a `financial_status =
  'ready'` son NULL en cada fila publicada: `revenue_usd`,
  `facturacion_mes_usd`, `wip_usd`, `costo_directo`, `costo_hundido`,
  `costo_total`, `margen_bruto_usd`, `margen_bruto_pct`.

`finance.project_margin` (Misión 1) usa por eso las columnas incondicionales
`revenue_base_amount`, `cost_direct_base_amount`, `cost_sunk_base_amount` y
reporta `original_currency` en `evidence_refs`. Arreglo pendiente (otra
misión): resolver la moneda base desde la configuración `replicon_base_currency`
como ya hace `app/config/sql/kb_wip_mensual_v3.sql`, y devolver `'ready'` en
la rama final del `CASE`. Igual para `pnl_detalle_consultor.sql`, que comparte
el mismo `CASE`.

## Otros hallazgos operativos (fuera del alcance de Misión 1)

- `cartridges/salesforce/app/services/runlog_service.py` inserta
  `extraction_runs` sin `tenant_id`/`workspace_id`, por lo que esas filas
  quedan `scope_status='legacy_unscoped'` e invisibles para sesiones con
  workspace activo; `operations.pipeline_health` y `data_freshness` no las ven.
- Solo el cartucho SuccessFactors espeja `extraction_runs` en `pipeline_runs`
  (`runlog_service.py` + `infra/init/99zzw`); Replicon escribe `pipeline_runs`
  directamente vía mcp-infra desde el DAG `replicon_ses_inbox_import.py`; los
  demás cartuchos escriben únicamente en `extraction_runs`. Los agregados de
  Operations leen ambas tablas por eso.
- `headcount_by_department` y `workforce_cost_monthly` son snapshots
  calculados con `CURRENT_DATE` en la materialización (el denominador de
  `absence_rate_company_by_type` es el headcount actual, no el del mes
  analizado); `absence_by_type_and_month` sí es un histórico mensual completo.
  La fecha del snapshot depende del `published_at` del head y viaja en
  `evidence_refs`.
