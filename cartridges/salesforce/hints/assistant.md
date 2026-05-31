# Salesforce Sales Cloud — Hints para el asistente

Cuando un usuario pregunta en el contexto de este cartucho, el asistente sigue
estas reglas además de las globales.

## Modelo de datos

- **Espina (gold)**: `salesforce_pipeline_forecast` — un row por (mes, vendedor).
  Cruza oportunidades abiertas (`IsClosed=false`) ponderadas por `Probability`
  con lo ya ganado del mes (`IsWon=true`). Campos: `deals_abiertos`,
  `monto_pipeline_usd`, `forecast_ponderado_usd`, `deals_ganados`,
  `monto_ganado_usd`.
- **Riesgo (gold)**: `salesforce_deals_en_riesgo` — oportunidades abiertas con
  `dias_sin_actividad` y `motivo_riesgo` (cierre vencido / sin actividad /
  nunca hubo actividad). La actividad sale de `Task` y `Event` ligados por
  `WhatId` a la oportunidad.
- **Velocidad (gold)**: `salesforce_velocidad_pipeline` — días promedio en cada
  etapa, derivado de `OpportunityHistory` (un row por cambio de etapa).
- **Margen (gold)**: `salesforce_vendedor_margen` — monto ganado y
  `descuento_promedio_pct` por vendedor (proxy: `1 - TotalPrice/(ListPrice×Qty)`).
- **Cuota (gold)**: `salesforce_cobertura_cuota` — ganado y forecast ponderado
  por vendedor. `cuota_usd`/`attainment_pct` son NULL (ver limitaciones).
- **Cross-cartucho (gold)**: `salesforce_forecast_vs_capacidad` — forecast
  ponderado (Salesforce) vs capacidad en horas (Replicon) por mes.

## Convenciones

- **Forecast ponderado** = `SUM(Amount × Probability/100)` de oportunidades
  abiertas. Nunca mezcles esto con lo ganado (`IsWon`): son columnas distintas.
- **Pipeline abierto** = `IsClosed = false`. **Ganado** = `IsWon = true`.
  **Perdido** = `IsClosed = true AND IsWon = false`.
- Identificador de la oportunidad: `opportunity_id` (= `Id` en raw). El dueño
  es `owner_id` (= `OwnerId`, FK a `User.Id`).
- Columnas raw vienen en PascalCase de Salesforce (`Amount`, `StageName`,
  `CloseDate`). En silver/gold están snake_case (`amount`, `stage_name`,
  `close_date`).
- Modelo de 3 capas: raw → silver (`*_latest`, dedup por `Id` a la última
  versión) → gold (agregados de negocio, prefijo `salesforce_`).

## Reglas operativas

1. **Antes de inventar SQL**, consulta `search_rag` contra `_semantic_salesforce`
   para confirmar columnas reales del dataset/entidad.
2. **Al leer `raw/salesforce/*` con `read_parquet`**, SIEMPRE incluye
   `hive_partitioning=true, union_by_name=true`. Bronze es incremental: la última
   versión de una fila sin cambios vive en un `load_date` anterior — dedup por
   `Id` con `DISTINCT ON (Id) ... ORDER BY Id, load_date DESC`.
3. Si `Amount` o `Probability` es NULL, **excluye** esa oportunidad del forecast;
   no asumas 0 ni 100.
4. **No publicar** datasets layer `silver` desde el workspace — esos los gestiona
   el equipo Studio.
5. Si una pregunta cae fuera de los datos disponibles, **escala al admin** con
   `request_admin_help`.
6. **"¿Por qué este deal está en riesgo?"** Distingue 3 causas: (a) `close_date`
   ya pasó (cierre vencido); (b) sin `Task`/`Event` reciente (frío); (c) lleva
   mucho en la misma etapa (`salesforce_velocidad_pipeline` da el promedio para
   comparar). No las mezcles en una sola etiqueta.

## Agentes

- **El Pronosticador** (`salesforce_pipeline_forecaster`): analiza `salesforce_pipeline_forecast`. Responde sobre forecast, pipeline abierto y lo ganado por mes/vendedor.
- **Centinela de Deals** (`salesforce_deal_risk_sentinel`): analiza `salesforce_deals_en_riesgo`. Detecta opps sin actividad, con cierre vencido o estancadas.
- **Vigía de Cuota** (`salesforce_quota_watchdog`): analiza `salesforce_cobertura_cuota`. La cuota es NULL — compara ganado vs promedio del equipo.
- **Analista de Margen** (`salesforce_margin_analyst`): analiza `salesforce_vendedor_margen`. Descuento como proxy de margen; no hay costo real cargado.
- **Enlace Operativo** (`salesforce_ops_liaison`): analiza `salesforce_forecast_vs_capacidad`. Cruza Salesforce con Replicon — requiere gold de Replicon materializado.

## Apps publicadas

- `salesforce_pipeline_forecast` — pipeline y forecast ponderado por vendedor.
- `salesforce_deals_en_riesgo` — oportunidades abiertas en riesgo, por monto.
- `salesforce_velocidad_pipeline` — días promedio por etapa del pipeline.
- `salesforce_vendedor_margen` — volumen ganado vs descuento por vendedor.
- `salesforce_cobertura_cuota` — ganado y forecast por vendedor; cuota NULL.
- `salesforce_forecast_vs_capacidad` — forecast vs capacidad operativa (Replicon).

## Limitaciones honestas

- **Cuota**: Sales Cloud no tiene un objeto de cuota estándar extraído aquí, así
  que `attainment_pct` es NULL. El Vigía de Cuota compara contra el promedio del
  equipo y lo dice explícitamente.
- **Margen**: es un PROXY por descuento (`ListPrice` vs `TotalPrice`), no margen
  real — no hay costo del producto cargado.
- **Forecast vs capacidad**: depende del gold de Replicon
  (`costo_consultor_mensual`). Si ese cartucho no está poblado, `capacidad_horas`
  viene NULL y el Enlace Operativo no estima la holgura. La `demanda_horas_estimada`
  usa una tarifa supuesta de 150 USD/hora (documentada, no real).
- **PII**: nombres, email y teléfono de `Contact`/`Lead` están `masked`, y sus
  `Id` `shadowed`, desde bronze — no cuentes con esos campos para deduplicar
  contactos.
