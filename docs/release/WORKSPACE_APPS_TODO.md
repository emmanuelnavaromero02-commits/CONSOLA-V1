# Workspace apps — estado y pendientes

Sprint v1.1 — primera app conectada a datos reales como prueba de
concepto. El resto pasa a v1.2.

## Cómo se sirven las apps

`workspace/app/main.py:309` resuelve `GET /apps/{name}` en dos pasos:

1. **Apps estáticas (v1.1 →)**: si existe
   `workspace/app/static/apps/<name>.html` en el contenedor, se sirve
   ese archivo. Esto permite versionar las apps junto con el código.
2. **Apps en BD**: si no hay archivo estático, se lee
   `analytic_apps.html` desde Postgres (las generadas por el asistente
   Studio).

Las apps consumen `/api/data/<dataset>/query` y
`/api/data/<dataset>/options`, que internamente proxy-pasan a refinement.

## Apps disponibles

| Nombre interno | URL | Fuente | Estado |
| --- | --- | --- | --- |
| `pnl_ejecutivo` | `/apps/pnl_ejecutivo` | Estática (v1.1) | ✅ Conectada a `pnl_mensual` con filtros (RM, AF, Cliente), KPIs, evolución mensual con Chart.js y tabla. |
| `pnl_revenue_manager` | `/apps/pnl_revenue_manager` | BD seed (v0.x) | ⚠️ Funcional, multi-select y cascada (ya existente). |
| `pnl_consultor` | `/apps/pnl_consultor` | BD seed (v0.x) | ⚠️ Requiere validación. |
| `asignacion_consultor` | `/apps/asignacion_consultor` | Pendiente | ❌ Sin app, ver propuesta abajo. |

## Pendiente — v1.2

### App 1: P&L por Revenue Manager (estático)

Reescribir el actual del seed como archivo estático
(`pnl_revenue_manager.html`) para tenerlo bajo control de versiones.
Dataset: `pnl_mensual`. Columnas: `revenue_manager`, `fiscal_year`,
`mes`, `revenue_usd`, `costo_total`, `margen_bruto_usd`.

### App 2: Asignación de consultores

Crear `asignacion_consultor.html`. Dataset propuesto:
`consultor_asignacion`. Vista: tabla cruzada
(consultor × mes × proyecto) con porcentaje de asignación.

### App 3: Consultores (utilización)

Crear `consultores.html`. Dataset propuesto: `consultor_mensual`.
KPIs: horas facturables, no facturables, % utilización, top 10 por
margen aportado. Filtros: consultor, mes, cliente.

### Patrón sugerido

Cada nueva app debe:
- Vivir como archivo estático en `workspace/app/static/apps/<slug>.html`.
- Usar `credentials: 'same-origin'` en todos los `fetch`.
- Llamar a `/api/data/<dataset>/options?columns=<col1,col2>` para
  poblar selects.
- Llamar a `/api/data/<dataset>/query` (POST) con
  `{ filters, limit, columns }` para los resultados.
- Mostrar un placeholder hasta que el usuario haga clic en
  **Ver resultados** y un mensaje claro cuando el resultado está vacío.

## Notas de seguridad

- El endpoint `/api/data/{dataset}` valida el nombre con
  `DATASET_NAME_RE` antes de proxy-pasar a refinement.
- Las apps estáticas son visibles para cualquier usuario autenticado
  (no requieren visibility check). Si una app tiene que ser privada,
  guardarla en `analytic_apps` con `visibility='private'`.
