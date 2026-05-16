# 08 — Usar el copiloto de OMEGA

> Audiencia: usuario final (consultor, analista, admin) que va a
> interactuar con el copiloto IA central por primera vez.

## Qué hace el copiloto

Entiende lenguaje natural y orquesta los cartuchos de la plataforma.
Las acciones posibles caen en tres categorías:

| Categoría | Ejemplo | Comportamiento |
|---|---|---|
| **read** | "list dags", "describe table", "preview entity" | Ejecuta sola |
| **write** | "set variable", "trigger run" | Ejecuta si tienes permiso |
| **destructive** | "delete dag", "drop table", "create dag" | Pide confirmación |

Las reglas inviolables (codificadas en el prompt del sistema, no en
el modelo):

- **No inventa datos**. Si la tool no devuelve resultados, lo dice
  ("No encontré ese dato"), no rellena con plausibles.
- **Cita la fuente**: cartucho, entidad, `run_id`, timestamp.
- **Acciones destructivas requieren aprobación explícita**: el copiloto
  no las ejecuta directo; te muestra un card amarillo con el detalle y
  un botón "Aprobar".
- **Auditoría completa**: cada llamada a tool queda en `audit_events`
  con tu IP, user-agent, conversación y `risk_level`.

## Cómo acceder

1. Login en http://localhost:8000.
2. Ir a http://localhost:8000/copilot (también está en el menú lateral).
3. Click en **"+ Nueva conversación"**.

Tu rol determina qué puede hacer el copiloto en tu nombre:

| Rol | `copilot.use` (read) | `copilot.write` | `copilot.execute` |
|---|---|---|---|
| owner / super_admin / admin | ✅ | ✅ | ✅ |
| workspace_admin             | ✅ | ✅ | ✅ |
| analyst / auditor / viewer  | ✅ | ❌ | ❌ |

Si pides una acción que no tienes permiso para ejecutar, el copiloto
te lo explica: "No tengo permiso `copilot.write` para invocar X.
Pídele a tu admin que te asigne el permiso `copilot.write` (es el que
habilita acciones de escritura en cartuchos)."

## Ejemplos de prompts

**Read** (ejecuta solo):
- "¿Qué DAGs hay activos en Airflow?"
- "Preview de 5 filas de la entidad `pa0001` de SAP HCM"
- "¿Cuál es el watermark actual de Replicon TimeEntry?"
- "Lista las tablas de Postgres en el esquema `bronze`"

**Write** (ejecuta si tienes permiso):
- "Lanza extracción incremental de SAP HCM pa0001"
- "Pausa el DAG `replicon_users_full`"
- "Re-corre el run fallido de las 14:30"

**Destructive** (presenta approval card):
- "Borra el DAG `sap_hcm_test`"
- "Trunca la tabla `staging_test`"

## Anatomía de la respuesta

Cada turno del copiloto puede contener tres componentes:

1. **Tool cards** (rectángulos con borde azul) — qué tool se llamó,
   con qué args (con secretos enmascarados como `***`).
2. **Texto del asistente** — la explicación en lenguaje natural.
3. **Approval card** (rectángulo con borde ámbar, solo en destructive)
   — qué acción quiere ejecutar; tú decides con `Aprobar` o `Cancelar`.

## Aprobar una acción destructiva

1. El copiloto muestra: "Necesito tu aprobación para borrar el DAG `X`".
2. Aparece el approval card con `airflow_delete_dag {dag_id: "X"}`.
3. Lees el tool + args. Si está OK → click **Aprobar**.
4. La plataforma ejecuta, audita con `status=success`, devuelve el
   resultado al copiloto, y éste te confirma en lenguaje natural.
5. Si NO está OK → click **Cancelar**. La acción queda registrada en
   `audit_events` con `status=pending_approval` y no se ejecuta.

## Garantías

- **No interfiere con `studio_assistant`**: el wizard de cartuchos
  sigue intacto en su propio servicio (`studio_assistant.py`). Los dos
  asistentes no comparten estado.
- **Persistencia**: cada turno (user + assistant) queda en
  `conversation_messages` con su `tool_calls` JSONB. Puedes auditar
  la conversación entera más tarde.
- **Aislamiento por usuario**: nadie más puede leer tus conversaciones
  excepto un admin (con su propia auditoría).
- **Sin streaming todavía**: la respuesta aparece completa cuando
  termina el turno. Streaming token-por-token llega en v1.43.

## Limitaciones (v1.42)

- Sin streaming (respuesta completa al final del turno).
- Sin charts inline — solo texto + tool cards (v1.44).
- Sin proactividad (alertas que el copiloto te lanza sin que preguntes,
  v1.44).
- Sin form interactivo para configurar cartuchos via prompt — sigue
  usándose `/cartridges` para eso, v1.44 lo integrará.

## Si algo falla

| Síntoma | Causa probable | Fix |
|---|---|---|
| 401 al cargar `/copilot` | sesión expiró | re-login |
| El copiloto dice "permission_denied: copilot.write" | tu rol no tiene write | habla con tu admin para promoverte |
| Approval card aparece sin acción que aprobar | tool_calls JSONB corrupto en DB | borra esa conversación y arranca una nueva |
| El copiloto inventa datos | bug grave — el prompt del sistema falló | reportar al equipo con el `X-Request-ID` (ver runbook 07) |
| Respuesta lenta (>30s) | el proveedor LLM (Anthropic / Gemini) está lento | reintentar; si persiste, revisar `CHAT_LLM_PROVIDER` en `.env` |

## Auditoría

Cada llamada a tool del copiloto queda en `audit_events`:

```sql
SELECT created_at, action, tool_name, risk_level, status, tool_args
FROM audit_events
WHERE conversation_id = '<UUID de tu conversación>'
ORDER BY created_at;
```

Lo verás también en `/operations` cuando v1.44 enchufe el panel
correspondiente. Por ahora, SQL directo.
