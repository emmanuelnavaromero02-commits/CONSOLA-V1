# Deriva entre producción (AWS) y `main`

Verificado el 2026-09-17 contra la base de producción (AWS), con consultas de
solo lectura. Producción
corre una versión **más vieja** que `main` en al menos tres puntos
confirmados. Cada uno se detectó por separado, investigando otra cosa.

Esto necesita su propia misión: sincronizar producción con lo que hay en
GitHub. No se investigó más a fondo; aquí solo queda el registro.

| # | Dónde | Qué dice el repo | Qué hay en producción |
|---|---|---|---|
| 1 | `cartridges/replicon/datasets/pnl_mensual.sql` | El dataset publica `financial_status`, `base_currency` y las columnas `*_base_amount`; el bug conocido deja NULL las ocho columnas condicionadas | La tabla desplegada **no tiene** `financial_status` ni `base_currency` ni las `*_base_amount`. Publica `margen_bruto_usd` y `revenue_usd` directo, con valores reales en los 8 meses (2026-01 a 2026-08). Es una definición anterior |
| 2 | `prediction_outcomes` (migraciones de calibración) | La tabla lleva columnas de evaluación (`evaluation_status`, `evaluated_by`, `evaluated_at`) que la calibración bayesiana necesita para contar aciertos y fallos | La tabla en producción **no tiene ninguna** columna de evaluación. Solo: `id, tenant_id, workspace_id, signal_id, option_id, action_taken, predicted_value, actual_value, prediction_error, outcome_summary, learned_rule, metadata, created_at, owner_user_id` |
| 3 | `console/app/services/copilot_context_persistence.py:156-172` | Cada refresco de contexto del copiloto escribe un evento de auditoría **crítico** (`copilot.context.refresh`) en la misma transacción que la instantánea | De **17,138** instantáneas escritas por el scheduler, hay **0** eventos de auditoría. El único evento `copilot.context.refresh` de toda la base es el del refresco manual del 2026-07-09 |

## Por qué importa

- El caso 1 invalida el análisis del margen de Finanzas hecho sobre el repo:
  el agregado de Misión 1 busca columnas que en producción no existen.
- El caso 2 hace que la calibración bayesiana sea inalcanzable en producción
  por una razón distinta a la del repo: no es que falten observaciones, es que
  la tabla no puede registrar una evaluación.
- El caso 3 significa que las escrituras del scheduler no dejaron rastro
  auditable. Para la purga de retención esto es conveniente (no se destruye
  trazabilidad porque nunca existió), pero como propiedad del sistema es una
  ausencia de auditoría que nadie declaró.

## Qué haría falta para cerrarlo

Comparar el esquema desplegado contra `infra/init` completo, y revisar qué
migraciones quedaron sin aplicar y por qué. Ver
[docs/data_gaps.md](data_gaps.md) para las brechas de datos, que son un
problema distinto: aquellas son datos que no existen, estas son versiones que
no coinciden.
