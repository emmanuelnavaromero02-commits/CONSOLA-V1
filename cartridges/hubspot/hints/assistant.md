# HubSpot — Hints para el asistente

Cuando un usuario hace preguntas en el contexto de este cartucho, el asistente
debe seguir estas reglas específicas además de las globales.

## Modelo de datos

- **Espina** (gold): `pipeline_salud` — un row por deal enriquecido (vendedor +
  etapa + probabilidad). Es la columna vertebral del CRM. Cruza el **plan**
  (deals abiertos = forecast) con lo **real** (ganado / perdido). El campo
  `estado` distingue: `forecast` (abierto, aún sin cerrar) | `ganado` |
  `perdido`. Análogo a la espina `consultor_asignacion` de Replicon, donde el
  futuro = ausencia de ejecución; aquí el forecast = deal aún sin cerrar.
- **Forecast** (gold): `forecast_mensual` — pipeline ponderado (monto ×
  probabilidad) vs. ganado, por mes y vendedor.
- **Revenue** (gold): `revenue_por_vendedor` — ganado / perdido, ticket
  promedio y win rate por vendedor y mes.
- **Conversión** (gold): `conversion_por_etapa` — distribución y tasa de
  ganados por etapa de pipeline.
- **Seguimiento** (gold): `deals_estancados` — deals abiertos sin actividad
  reciente.

Todo lo de arriba **cuelga de `pipeline_salud`** (los 4 agregados leen la
espina, no el silver). El silver (`hubspot_*_latest`) es la foto limpia 1:1 de
cada objeto de HubSpot.

## Revenue / forecast por estado (pipeline_salud)

- `forecast` (deal abierto) → `monto_ponderado_usd = monto × probabilidad`.
- `ganado` (`hs_is_closed_won = true`) → cuenta como revenue real del mes de cierre.
- `perdido` (`hs_is_closed = true` y no won) → cuenta en el win rate.
- La **probabilidad** sale de la etapa del pipeline (`hubspot_pipelines_latest`),
  con fallback a `hs_deal_stage_probability` del deal.

## Convenciones

- **Grano de la espina**: (mes_cierre × vendedor × deal). `mes_cierre` =
  `DATE_TRUNC('month', closedate)` (esperada si está abierto, real si cerró).
- Identificador del vendedor: `owner_id` (= `hubspot_owner_id` del deal); el
  nombre legible viene de `hubspot_owners_latest`.
- **Modelo de 3 capas**: raw → silver (snapshot 1:1, dedup por `hubspot_id` a la
  última `hs_lastmodifieddate`) → gold (espina + agregados). No hay capa
  `master`.
- Las fechas de HubSpot vienen como texto ISO 8601 → usa `TRY_CAST(... AS
  TIMESTAMP)`. Los montos y probabilidades vienen como texto → `TRY_CAST(... AS
  DOUBLE)`.

## Reglas operativas

1. **Al leer `raw/hubspot/*` con `read_parquet`**, SIEMPRE incluye
   `hive_partitioning=true, union_by_name=true`. Sin `union_by_name`,
   propiedades nuevas que HubSpot agregue romperán el binder aunque existan en
   la última partición. (Lección heredada de Replicon.)
2. **Antes de inventar SQL**, consulta `search_rag` contra `_semantic_hubspot`
   para confirmar nombres reales de propiedades.
3. Si una columna marca "no existe": confirma con
   `SELECT load_date, COUNT(col) FROM read_parquet(...) GROUP BY 1` antes de
   concluir (la propiedad pudo no estar pedida en `select`/`properties`).
4. Si una pregunta cae fuera de los datos disponibles, **escala al admin** con
   `request_admin_help`.

## Límites conocidos (marcados, NO escondidos)

- **Margen por deal**: HubSpot solo trae revenue; el **costo** vive en el
  cartucho de finanzas. "Vendedores que venden más pero con peor margen"
  requiere cruzar `revenue_por_vendedor` con costo → fase 2.
- **Forecast vs. capacidad operativa**: requiere cruzar con Replicon (capacidad)
  → fase 2 (Replicon ya existe en la plataforma).
- **Empresa/cuenta en la espina**: el vínculo deal→empresa vive en la API de
  *associations* de HubSpot (no extraída aún). Hoy la espina no trae nombre de
  empresa → fase 2.
- **Velocidad entre etapas**: requiere historial de cambios de etapa (deal stage
  history) → fase 2. La conversión actual es sobre el estado vigente.
- **Moneda**: los montos se asumen en USD. Si el portal usa otra moneda, hay que
  normalizar (como Replicon hace MXN÷20) → pendiente de confirmar el portal.

## Apps publicadas

- `pipeline_forecast_dashboard` — pipeline ponderado y forecast por vendedor y mes.

## Agentes

- `forecast_watchdog` (Vigía de Forecast) — deals en riesgo de no cerrar; lee
  `gold_forecast_mensual` + `gold_pipeline_salud`. Corre Lun 9am.
- `stale_deal_chaser` (Perseguidor de Deals) — deals abiertos sin actividad
  reciente; lee `gold_deals_estancados`. Corre Lun 9am.
