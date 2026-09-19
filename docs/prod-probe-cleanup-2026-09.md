# Limpieza de datos de sondas en producción (AWS) — septiembre 2026

Cerrada el 2026-09-19. Producción corre `v1.45.205-beta` (`ee35b035`), en
`mode_postgres` (base `modecissions`) y `mode_postgres_gold` (`modecissions_gold`, puerto 5433).
Cada borrado se aprobó paso a paso por el owner, con SQL exacto, respaldo
previo, guarda de conteos y verificación de solo lectura.

## Qué se borró

| Origen (script) | Qué | Filas |
|---|---|---|
| `aws_decision_orchestrator_execution_probe` | `calibration_states` | 7 |
| | simulaciones Monte Carlo, ejecuciones y corridas de orquestación, señales, items y eventos (`decision-orchestrator-exec-%`) | según Paso 2 |
| `aws_decision_orchestrator_probe` (hermana) | items `decision-orchestrator-(risk\|action)-<uuid>` + corridas + evento | 20 + 20 + 1 |
| `aws_control_room_mock_volume_probe` (corrida `r_20260621t050201z`) | 3 tablas `gold_mock_*` en Gold (1 003 240 filas) + `silver_lineage` 4, `data_catalog` 10, `studio_entities` 1 | Gold 335 MB → 34 MB |
| `aws_action_framework_probe` + `aws_decision_orchestrator_probe` | todas las `external_actions` (50), sus eventos (161) y claves de idempotencia (146), con 357 lápidas en `audit_deletes` (motivo `limpieza-residuo-sondas-2026-09-19`) | 50 / 161 / 146 |
| `tenant_isolation_aws_probe` | `login_attempts` de `tenant-isolation-20b-…@example.invalid` | 12 (347 → 335) |

Notas:
- `external_action_events` es append-only por trigger. Se borró dentro de **una sola
  transacción** con `SET LOCAL session_replication_role = replica` únicamente
  alrededor de ese DELETE. El trigger nunca se desactivó (sigue en `O`) y una
  sesión nueva arranca en `origin`.
- Las 10 acciones del orquestador quedaron huérfanas tras el Paso 3 (sus
  corridas apuntaban a ellas por `external_action_id`); se detectó y se limpió
  junto con las otras 40.

## Qué se conservó a propósito

- **Auditoría (`audit_events`):** los 146 registros de las acciones externas
  (huella `md5 6a37a11689f9b86cae06ae9db89691da`, idéntica antes y después), el
  registro 1923 (clic real sobre una señal falsa) y los 2 de la corrida mock.
  La auditoría es historial y no se toca.
- **Zona gris — resultados reales del motor disparados desde una prueba.**
  `aws_control_room_gold_engine_probe` ejecutó el **motor real sobre datos reales
  de Gold** en «Main Workspace» entre el 2026-06-24 y el 2026-06-26, con un
  identificador de corrida de prueba (`run_ref` `…:replicon:control-room-gold-engine-probe-…`,
  `metadata.pipeline_run_id` `control-room-gold-engine-probe:%`). Dejó 3 de las
  29 `intelligence_runs` (81 señales generadas), 81 eventos de items, 81 paquetes
  y 81 ítems de evidencia, 81 `decision_intelligence_snapshots` y 4 registros de
  auditoría, y fue el último proceso en escribir sobre 27 items/señales reales.
  **Decisión del owner (2026-09-19): no se borran.** Son resultados verdaderos del
  motor sobre datos verdaderos; solo su disparo fue una prueba.
- Los 2 tenants/workspaces «Decision Orchestrator … Probe»: contienen datos de un
  usuario real y quedan pendientes de una decisión con él.
- Las 6 `decisions` y 183 `decision_options`: son reales.
- Sondas que no dejaron nada: `aws_bayesian_calibration_probe`,
  `aws_bayesian_loop_probe`, `bayesian_loop_probe`, `aws_monte_carlo_probe`,
  `cartridge_kb_scope_aws_probe`, `p0_security_aws_probe`, `aws_superset_probe`,
  `superset_tenant_probe`.

## Respaldos en el host de producción (`/tmp`, 146 MB en total)

`calibration_states_backup.csv`, `monte_carlo_sonda_backup.csv`,
`orq_executions_sonda_backup.csv`, `orq_runs_sonda_backup.csv`,
`signals_sonda_backup.csv`, `items_sonda_backup.csv`,
`item_events_sonda_backup.csv`, `orq_runs_hermana_backup.csv`,
`items_hermana_backup.csv`, `item_events_hermana_backup.csv`,
`gold_mock_r_20260621t050201z.dump` (pg_dump -Fc),
`mock_{silver_lineage,data_catalog,studio_entities}_backup.csv`,
`ea_{actions,events,keys}_backup.csv`, `ea_audit_snapshot_intacto.csv`,
`login_attempts_sonda_backup.csv`; y de la misión aparte del copiloto,
`snapshots_purga_backup.csv.gz` y `/opt/modecissions/infra/terraform/deploy/.env.bak-20260917`.

Pendiente: borrar `calibration_states_backup.csv` a partir del 2026-09-19 22:26 UTC
(48 h tras el Paso 1) y el resto cuando el owner lo decida.

## Relacionado

- Hallazgos sin corregir (llave simétrica del `security_context`, errores de
  «Acciones Supervisadas» y un hallazgo de seguridad tratado por canal privado):
  [`pending-findings.md`](pending-findings.md).
- Deriva producción vs `main`: [`prod-vs-main-drift.md`](prod-vs-main-drift.md).
- Las sondas `aws_decision_orchestrator_probe.py` y
  `aws_decision_orchestrator_execution_probe.py` ya limpian lo que crean
  (rama `feat/quantitative-inputs`, sin commit).
