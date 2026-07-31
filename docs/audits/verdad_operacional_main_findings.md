# Verdad Operacional — hallazgos sobre `main`

Base: `main@cffaca3a`. Auditoría read-only seguida de correcciones acotadas.

Este documento separa deliberadamente tres cosas: lo que se corrigió en esta
rama, lo que **no** se tocó porque es una decisión de arquitectura, y lo que no
se pudo ejecutar. Cada hallazgo indica si fue **reproducido** o solo **leído**.

---

## 1. Corregido en esta rama

### F1 · Falso verde en `dataset_refresh_chain` — P0, reproducido

`airflow/dags/dataset_refresh_chain.py:487-489` (antes del cambio):

```python
status = "success" if total == materialized else "partial" if materialized else "failed"
if total == 0 and not materialize_failed and not inv.get("error"):
    status = "success"
```

La guarda de la línea 488 es **letra muerta**. Cuando `materialize_in_order`
muere antes de publicar su XCom, `record_run` (que corre bajo
`trigger_rule=TriggerRule.ALL_DONE`, hoy línea 557, así que **sí se ejecuta**)
sintetiza `{"materialized": 0, "results": [], "error": ...}`. Entonces
`total == materialized == 0` y el ternario de la línea 487 ya resolvió a
`"success"`. El `if` de abajo solo puede *poner* success, nunca quitarlo.

Reproducción con la lógica original tal cual:

```
codigo VIEJO, fallo duro pre-XCom -> success
codigo VIEJO, sin XCom            -> success
```

Radio de impacto más allá del estado escrito: el `raise` de
`if status != "success" and not allow_partial` (hoy línea 548) nunca disparaba,
y `_trigger_gold_refresh_intelligence(...)` (hoy línea 538) **sí** disparaba,
lanzando la cadena de inteligencia sobre un conjunto vacío de datasets.

**Corrección:** `_chain_status()`, una función pura que lee las señales de
fallo *antes* que los contadores. `total == materialized == 0` deja de ser
evidencia de éxito. Un plan vacío publicado por una tarea que terminó limpia
sigue siendo `success` (es un no-op legítimo).

### F2a · Lazo cortado del `agent_runner` — P0, reproducido

`airflow/dags/agent_runner.py`. Cadena completa, verificada eslabón por
eslabón contra `main`:

| Eslabón | Evidencia |
|---|---|
| El DAG lee `agents` con SQL directo, sin `set_config` | `find_due_agents`, líneas 228-231 |
| Se conecta como `omega_airflow_dag` | `infra/docker-compose.yml:1208`, `infra/terraform/deploy/docker-compose.aws.yml:717,810` |
| Ese rol **sí** tiene `GRANT SELECT ON agents` | `infra/init/36_cartridge_and_meta_roles.sql:246-251` |
| La tabla corre `ENABLE` + `FORCE ROW LEVEL SECURITY` | `infra/init/99s_remaining_operational_rls.sql:233-234` |
| El rol es `NOBYPASSRLS` | `infra/init/99f_native_rls_completion.sql:258` |
| **Ninguna** de las tres policies lo incluye | `99s:242,251,260` y `99x:182` — todas `TO omega_console, omega_refinement` |

Con GRANT pero sin policy aplicable, PostgreSQL devuelve **cero filas sin
error**. No es un `permission denied` ruidoso: es ceguera silenciosa. De ahí:
`due=[]` → `invoke_each` retorna `{"invoked":0,"results":[]}` sin error →
`_pipeline_status` retornaba `"success"` porque `if not results: return
"success"` → un row verde en `pipeline_runs` cada 5 minutos sobre cero agentes
ejecutados.

El comentario de `36_cartridge_and_meta_roles.sql:237-243` explica cómo se
produjo: dice *"no DAG actually consumes it (grep … returns zero hits for
PostgresHook / psycopg2)"*. Era cierto cuando se escribió; `agent_runner.py`
empezó a usar ese rol después y la RLS no se actualizó.

**Corrección (deliberadamente parcial):** no toco la RLS. Lo que sí hago es
que la ceguera **deje de ser invisible**. `_agents_visibility()` distingue las
dos causas de un cero que a nivel de query son idénticas:

- 0 agentes programados en esta ventana de 5 min → normal, `success`.
- 0 filas *visibles* porque no hay policy aplicable → `failed`, con el motivo.

La distinción se resuelve consultando el catálogo (`pg_class.relrowsecurity`,
`pg_roles.rolbypassrls`, `pg_policies`), no adivinando. El mensaje resultante:

```
role omega_airflow_dag has no row level security policy on public.agents;
every SELECT returns zero rows silently
```

> **Advertencia operativa:** en producción hoy esto hará que `agent_runner`
> reporte `failed` cada 5 minutos. Eso es correcto — está roto desde hace
> tiempo — pero es un cambio de ruido visible. La corrección de fondo es F2b,
> abajo, y es de C1.

### F3 · Impacto en USD fabricado — P0, reproducido

`console/app/services/intelligence/persistence.py:514-517` (antes):

```python
impact_estimate = num(expected_impact.get("value"))
if impact_estimate is None:
    impact_estimate = abs(float(signal["deviation_value"]))
impact_currency = str(expected_impact.get("currency") or "USD")
```

El motor de decisión **ya era honesto**:
`decision_intelligence._expected_impact()` (línea 628-635) resuelve
`unit_value = 1.0 if _impact_currency(metric) else 0.0`, de modo que una
métrica no monetaria produce `impact = 0.0`; y las líneas 338-341, 397-400 y
546-549 emiten `value=None, currency=None` cuando `impact <= 0`. El
`basis` que acompaña dice literalmente *"No monetary impact configured for
{field}"*.

`persistence.py` veía ese `None` y **resucitaba la cifra que el motor se había
negado a publicar**, etiquetándola "USD". Una desviación de 37 empleados se
persistía como `impact_estimate=37.0, impact_currency='USD'`.

Segundo daño, más sutil: `control_room.business_impact_rules.calculate_item_impact()`
arranca en la línea 19-20 con `stored = number(item.get("impact_estimate"))` /
`if stored is not None and stored > 0`. Al escribir siempre un número, esa
rama cortocircuitaba **todas** las reglas reales de cost-basis (replicon,
sap_s4hana, sap_hcm, successfactors) y también el fallback honesto de la línea
266-274, cuya explicación es *"falta cost basis para convertirla a dinero sin
inventar cifras"*. Es decir: la fabricación del productor anulaba la honestidad
que el consumidor ya tenía construida.

**Corrección:** `_declared_money()`. Solo se persiste un monto si el motor
declaró monto **y** moneda. Un número sin su unidad no es dinero. Con
`impact_estimate = NULL`, el consumidor recupera su comportamiento previsto:
cae a las reglas de cost-basis reales y, si ninguna aplica, a
`status="unavailable"`.

Nota de alcance: además del fallback a `deviation_value`, esto también corrige
el caso "monto declarado sin moneda", que heredaba el `'USD'` por defecto —
la misma invención por otra ruta. `impact_currency` es `NOT NULL DEFAULT 'USD'`
(`infra/init/91_control_room_v1_operational.sql:10`), así que se conserva
`'USD'` como relleno de columna, pero sin monto no se afirma nada.

---

## 2. NO corregido — decisión de arquitectura, corresponde a C1

Estos dos son reales y están reproducidos, pero corregirlos exige decidir
política, no arreglar un descuido. No los toco.

### F2b · `omega_airflow_dag` no tiene policy sobre `agents`

Evidencia: la tabla de F2a, arriba. El rol tiene GRANT pero ninguna policy.

Por qué no lo arreglo: **no hay una respuesta mecánicamente correcta**.
`agent_runner` escanea *globalmente* todos los tenants para encontrar crons que
disparan en la ventana; una policy basada en `omega_rls_workspace_matches()`
con GUCs por tenant no encaja con un escaneo global. Las opciones tienen
consecuencias distintas de seguridad:

- **A.** Policy nueva para `omega_airflow_dag` limitada a las columnas de
  agendamiento, sin GUC de tenant. Simple, pero abre lectura cross-tenant de
  metadatos de agentes a un rol de DAG.
- **B.** Que el escaneo pase por MCP/console, que ya corre como `omega_console`
  con policies. Mantiene el scoping en un solo lugar; cuesta una llamada más.
- **C.** Una función `SECURITY DEFINER` con `search_path` fijo que devuelva
  solo `(id, slug, tenant_id, workspace_id, extra->'schedule')`. La más
  restrictiva; es la que más se parece al resto del sistema.

Mi lectura, no vinculante: **C**, por consistencia con cómo el repo ya resuelve
accesos privilegiados. Pero es llamada de C1.

### F4 · `app.platform_admin` se activa por *ausencia* de configuración

`cartridges/replicon/app/services/watermark_service.py:23-26`:

```python
cur.execute("SELECT set_config('app.platform_admin', %s, true)",
            ("false" if tenant_id and workspace_id else "true",))
```

`tenant_id`/`workspace_id` salen de variables de entorno (`OMEGA_TENANT_ID`,
`OMEGA_WORKSPACE_ID`). Si faltan o vienen vacías, el GUC se pone en `'true'`.

Ese GUC **es** un bypass real de RLS, no un flag decorativo. La policy
compartida en `infra/init/99n_rag_viewer_scoped_rls.sql:206-214` aplica a todos
los roles de cartridge:

```sql
USING (
    current_setting('app.platform_admin', true) = 'true'
    OR omega_rls_workspace_matches(tenant_id, workspace_id)
)
```

El patrón es **fail-open**: una variable de entorno mal escrita o no
propagada no falla, *escala privilegios*. Bajo Verdad Operacional el modo
plataforma debería declararse explícitamente, nunca inferirse de una ausencia.

Detalle que conviene revisar aparte: el encabezado de
`infra/init/99zr_banxico_entity_watermarks_rls.sql:5` afirma *"with no
platform-owner bypass"*, pero la policy que crea (líneas 17-23) contiene
exactamente ese bypass. El comentario contradice a su propio código.

**No verificado por mí:** si los despliegues de Replicon efectivamente definen
esas variables. Sin eso no puedo afirmar que se esté explotando en la práctica
— solo que la ruta existe y es fail-open.

---

## 3. Fuera de alcance: hallazgos que viven solo en ramas de PR

Verificado con `git cat-file` contra `origin/main`: estos archivos **no
existen** en `main`, así que sus defectos no están en producción y no se pueden
corregir desde aquí. Son de sus autores.

| Hallazgo | Rama |
|---|---|
| `NULL::VARCHAR AS base_currency` deja en blanco el módulo financiero de Replicon | `feat/…` de PR #552 |
| `_financial_row_ready()` retorna False para toda fila productiva | idem #552 |
| El test valida dos rutas de código independientes | PR #554 |
| `bool_and` retorna NULL y `COALESCE(…, true)` lo deja pasar | PR #555 |
| Canarios de `staged_publication` ausentes de CI | PR #555 |

---

## 4. Lo que NO pude ejecutar

Honestidad sobre el límite de esta verificación. En este entorno no hay
`pytest`, ni `fastapi`, ni demonio Docker, ni PostgreSQL.

- **Los tests nuevos no se han corrido con pytest.** La lógica de cada función
  pura se verificó ejecutando las mismas aserciones con el intérprete
  (`_chain_status` 10/10, `_pipeline_status` + `_agents_visibility` 13/13,
  `_declared_money` 9/9), y los tres archivos compilan. Pero la suite completa
  está pendiente.
- **La consulta SQL de `_agents_visibility()` no se ha ejecutado contra
  PostgreSQL.** Su sintaxis fue revisada a mano (`pg_class.relrowsecurity`,
  `pg_roles.rolbypassrls`, `pg_policies.roles` como `name[]`, `pg_has_role`
  excluyendo `'public'`), pero **no probada**. Es lo primero que hay que
  correr contra un Postgres real antes de aterrizar esto.
- **No se verificó el comportamiento de la UI** con `impact_estimate = NULL`
  en volumen. El contrato lo admite (`impact_estimate?: number | null` en
  `console-next/src/lib/control-room/types.ts:873`) y el consumidor tiene su
  rama `unavailable`, pero no se observó renderizado real.

El veredicto de estos cambios está acotado a lo que se reprodujo. Lo no
verificado no puede sostener un GO.
