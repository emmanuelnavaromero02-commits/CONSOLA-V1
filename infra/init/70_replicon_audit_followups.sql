-- Follow-ups from MEJORAS audit: keep Replicon metadata runnable after import.

UPDATE cartridges
   SET assistant_hints = $hints$# Replicon — Hints para el asistente

Cuando un usuario hace preguntas en el contexto de este cartucho, el asistente
debe seguir estas reglas específicas además de las globales.

## Modelo de datos

- **Espina** (gold): `consultor_asignacion` — un row por (mes, consultor, proyecto).
  Cruza `ResourceAllocation` (plan, incluye futuro con `estado='futuro'`) con
  `TimeEntry` (ejecución). Incluye `revenue_manager`, `tipo_empleado`,
  `tipo_de_proveedor`.
- **Capacidad** (gold): `costo_consultor_mensual` — un row por (mes, consultor).
  `horas_disponibles` ≈ 160-168h para full-time. Solo cubre hasta el mes en curso;
  para meses futuros usa default 168 h.
- **P&L** (gold): `pnl_mensual` (por revenue manager / cliente / proyecto) y
  `pnl_detalle_consultor` (prorrateado al consultor).
- **Skills** (gold): `fact_empleado_skills` (rating por user × skill_name) y
  `dim_skills`.

## Revenue por tipo de proyecto (pnl_mensual)

- `FPP` → Δ avance del mes × `valor_contrato`.
- `T&M`, `AMS (On Demand)` → `horas_facturables × billing_rate_usd`
  (fallback: `BillingItem`).
- `Iguala`, `AMS Baseline` (alias `AMS (Base Line)`), `BPO`,
  `CFDI - Timbrado` (alias `CFDI-Timbrado`) → `SUM(billableamountbasecurrency)`
  de BillingItem del mes (proporcional al consultor por share de horas
  facturables en `pnl_detalle_consultor`).

## Convenciones

- **Año fiscal** corre mar-feb. `AF2025 = mar 2025 → feb 2026`.
- Una hora full-time mensual estándar: **168 horas**.
- Identificador único del consultor: `username` (igual a `consultor` en gold).
  Para joins entre `consultor_asignacion` y `replicon_user_latest`, usar
  `LOWER(TRIM(username))`.
- Nombres de columnas en parquets raw vienen con espacios y mayúsculas
  (ej. `"Tipo de Proveedor"`, `"User Name"`). En silver/gold están
  snake_case (`tipo_de_proveedor`, `username`).
- Modelo de 3 capas: raw → silver (snapshot 1:1) → gold (dim_*, fact_*,
  agregados). La layer 'master' fue eliminada — todo lo que vivía ahí
  ahora es gold.

## Reglas operativas

1. **Antes de inventar SQL**, consulta `search_rag` contra
   `_semantic_replicon` para confirmar columnas reales del dataset/entidad.
2. **Al leer `raw/replicon/*` con `read_parquet`**, SIEMPRE incluye
   `hive_partitioning=true, union_by_name=true`. Sin `union_by_name`,
   columnas nuevas (agregadas por Replicon recientemente) fallarán binder
   aunque sí existan en la última partición.
3. Si una columna marca "no existe" con binder error: confirma con
   `SELECT load_date, COUNT(col) FROM read_parquet(...) GROUP BY 1` antes
   de concluir.
4. **No publicar** datasets layer `silver` o `master` desde el workspace —
   esos los gestiona el equipo Studio (en este cartucho, los silvers son
   1:1 con entidades raw + `empleados_maestro` que viene de Excel externo).
5. Si una pregunta cae fuera de los datos disponibles, **escalar al admin**
   con `request_admin_help`.
6. **"¿Por qué falta el proyecto X del consultor Y en el mes M?"** No te
   quedes con la respuesta de `consultor_asignacion`. Distingue 3 fuentes:
   (a) `ResourceAllocation` raw = plan, incluye `enddate`; un proyecto
   "desaparece" si su `enddate` cayó antes del primer día del mes.
   (b) `TimeEntry` raw = horas reales registradas; un consultor puede tener
   asignación pero 0 h reales si aún no llena timesheet.
   (c) Fecha de última extracción (`MAX(load_date)`) — si el dato falta
   completo, puede ser que Replicon aún no lo tiene cargado al momento del
   pull. En la respuesta, lista proyectos del cliente que cerraron
   recientemente (`enddate` < primer día del mes preguntado) para explicar
   ausencias percibidas.

## Apps publicadas

- `pnl_revenue_manager` — P&L por Revenue Manager con drill por proyecto.
- `consultor_horas_costos` — horas y costo por consultor (RM × consultor × mes).
- `skill_gaps_heatmap` — brechas de skills por manager.
- `match_consultor_por_skill` — matching de consultores disponibles para
  un skill + horas + mes (interno/externo).
- `resource_availability` — FTE / horas / % por RM, tipo empleado o
  tipo de proveedor; pasado = facturables, futuro = asignadas.$hints$,
       updated_at = NOW()
 WHERE id = 'replicon';

DELETE FROM entity_config
 WHERE cartridge_id = 'replicon'
   AND entity = 'ResourceAssignment';

INSERT INTO entity_config
    (cartridge_id, entity, display_name, mode, primary_key, dag_id, description, enabled, trigger_type)
VALUES
    ('replicon', 'ResourceAllocation', 'Asignaciones de Recursos', 'full', 'allocation_id', 'replicon_extract', 'Asignaciones de recursos a proyectos', TRUE, 'manual'),
    ('replicon', 'ResourceRequest', 'Solicitudes de Recursos', 'full', 'request_id', 'replicon_extract', 'Solicitudes abiertas de recursos', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        mode = EXCLUDED.mode,
        primary_key = EXCLUDED.primary_key,
        dag_id = EXCLUDED.dag_id,
        description = EXCLUDED.description,
        enabled = EXCLUDED.enabled,
        trigger_type = EXCLUDED.trigger_type;

INSERT INTO cartridge_dags
    (cartridge_id, dag_id, file, description, trigger, params, dag_role, dag_params_example)
VALUES
    ('replicon', 'replicon_ses_inbox_import', 'replicon_ses_inbox_import.py', 'Ingesta adjuntos recibidos por Amazon SES en S3.', 'scheduled', '["bucket","prefix"]'::jsonb, 'worker', '{"bucket":"ses_inbox","prefix":"replicon/"}'::jsonb),
    ('replicon', 'replicon_outlook_audit_report_import', 'replicon_outlook_audit_report_import.py', 'Ingesta reporte de auditoría recibido por Outlook.', 'scheduled', '["mailbox","subject_filter"]'::jsonb, 'worker', '{"mailbox":"inbox","subject_filter":"Replicon"}'::jsonb)
ON CONFLICT (cartridge_id, dag_id) DO UPDATE
    SET file = EXCLUDED.file,
        description = EXCLUDED.description,
        trigger = EXCLUDED.trigger,
        params = EXCLUDED.params,
        dag_role = EXCLUDED.dag_role,
        dag_params_example = EXCLUDED.dag_params_example,
        updated_at = NOW();

UPDATE datasets
   SET sources = '["raw/replicon/ResourceRequest"]'::jsonb,
       sql_def = 'SELECT * FROM read_parquet(''s3://{bucket}/raw/replicon/ResourceRequest/**/*.parquet'', hive_partitioning=true, union_by_name=true)',
       description = 'ResourceRequest - Ultima extraccion Replicon Bronze',
       updated_at = NOW()
 WHERE name = 'replicon_resourcerequest_latest'
   AND cartridge = 'replicon';

UPDATE analytic_apps
   SET datasets_used = ARRAY['consultor_mensual','consultor_asignacion']::text[],
       updated_at = NOW()
 WHERE name = 'consultor_horas_costos';

UPDATE analytic_apps
   SET datasets_used = ARRAY['fact_empleado_skills','consultor_asignacion','costo_consultor_mensual','empleados_maestro']::text[],
       updated_at = NOW()
 WHERE name = 'match_consultor_por_skill';

UPDATE analytic_apps
   SET datasets_used = ARRAY['pnl_mensual','pnl_detalle_consultor']::text[],
       updated_at = NOW()
 WHERE name = 'pnl_revenue_manager';

UPDATE analytic_apps
   SET datasets_used = ARRAY['consultor_asignacion']::text[],
       updated_at = NOW()
 WHERE name = 'resource_availability';

UPDATE analytic_apps
   SET datasets_used = ARRAY['analytic_skill_gap_by_manager']::text[],
       updated_at = NOW()
 WHERE name = 'skill_gaps_heatmap';
