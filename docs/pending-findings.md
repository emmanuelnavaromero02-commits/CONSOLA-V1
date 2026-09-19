# Hallazgos pendientes (documentados, sin corregir)

Actualizado 2026-09-19. Cada punto es una misión o tarea aparte; ninguno se
corrigió durante la limpieza de datos de sondas en producción. La evidencia de
producción viene de consultas de solo lectura sobre AWS (`v1.45.205-beta`,
commit `ee35b035`); la de código cita la tag o `main` según se indica.

## 1. PRIORITARIO (seguridad) — hallazgo de endurecimiento de la base de datos

- Detectado el 2026-09-19 durante la limpieza de producción. Por tratarse de
  producción y seguir sin corregir, el detalle se documenta fuera de este
  repositorio público y se atiende como misión de seguridad propia.
- Afecta al orden de otra tarea: no endurecer los triggers de
  `external_action_events` con `ENABLE ALWAYS` sin revisar antes esa misión.

## 2. Firma asimétrica del `security_context` (llave simétrica compartida)

- `SECURITY_CONTEXT_SIGNING_KEY` llega a 16 servicios en local y a 9 en AWS,
  cartuchos incluidos. Es simétrica: quien puede verificar puede falsificar.
- Detectado en la Misión 5 (#647); diferido a su propia misión de firma asimétrica.

## 3. Deriva producción vs `main`

- Producción corre `v1.45.205-beta` (`ee35b035`); `main` está en `d9eb6344`.
- Los 3 casos confirmados están en [`prod-vs-main-drift.md`](prod-vs-main-drift.md).

## Tareas de pantalla — «Acciones Supervisadas» (`/supervised-actions`)

Las tres siguen en `main` (`d9eb6344`).

### T1. Los recuadros de resumen no cuentan los estados reales
- «Pendientes», «Ejecutadas» y «Con revisión» cuentan estados que el backend no
  usa (el único en común es `failed`). Con 9 acciones en `pending_approval`, el
  recuadro «Pendientes» mostraba 0.
- `console-next/src/app/(shell)/supervised-actions/page.tsx:145-147` (`main`) y
  el cálculo de `summary` en el mismo archivo.

### T2. El panel de detalle queda en blanco al elegir una fila
- `GET /api/actions/{id}` devuelve `{action: {..., events}}`
  (`console/app/services/external_actions.py:563-572`, tag) y
  `getSupervisedAction` no lo desenvuelve
  (`console-next/src/lib/supervised-actions/client.ts`, `main`). Resultado:
  `currentId` vacío, botones deshabilitados y los eventos nunca se muestran.
  En la práctica solo se puede operar sobre la primera fila.

### T3. «succeeded» aparece sin traducir
- `stateLabel` (`console-next/src/app/(shell)/supervised-actions/page.tsx:47`,
  `main`) no tiene caso para `succeeded` y cae al valor crudo.

### Nota relacionada (no es de pantalla)
- La expiración perezosa (`_mark_expired`) se revierte siempre junto con el 409,
  así que una acción vencida nunca queda guardada como `expired`
  (`console/app/services/external_actions.py:385-415`, tag).
