# Paso 1 — Auditoría de paridad: contenedor por cartucho vs runtime genérico

> Pregunta: ¿qué hace el contenedor FastAPI de cada cartucho que NO esté
> ya cubierto por `mcp-infra` (tools genéricas) + Airflow (extracción)?
> Solo lectura. Evidencia de la DB viva (`omega_beta`) y el código.

## A. Superficie de tools MCP

**Tools estáticas por cartucho** (`app/mcp_server.py`):

| Cartucho | Tools |
|---|---|
| replicon, hubspot, sap_hcm, sap_s4hana, sap_successfactors | list_entities, get_schema, preview, list_kbs, run_kb, query_kb |
| salesforce | + get_watermarks, get_entity_status, run_all_kb |

**Tools genéricas en `mcp-infra/app/tools/cartridges.py`** (parametrizadas
por `cartridge_id`, 16 tools):

cartridge_list_entities, cartridge_get_schema, cartridge_preview,
cartridge_list_kbs, cartridge_run_kb, cartridge_query_kb,
cartridge_extract, cartridge_extract_all, cartridge_get_manifest,
cartridge_get_semantic, cartridge_get_hints, cartridge_get_run_logs,
cartridge_get_job_status, cartridge_list_jobs, cartridge_search_term,
cartridge_sync_semantic_to_rag.

### Mapa de paridad

| Tool por-cartucho | Equivalente genérico | Estado |
|---|---|---|
| list_entities | cartridge_list_entities (incluye watermark) | ✅ |
| get_schema | cartridge_get_schema | ✅ |
| preview | cartridge_preview | ✅ |
| list_kbs | cartridge_list_kbs | ✅ |
| run_kb | cartridge_run_kb | ✅ |
| query_kb | cartridge_query_kb | ✅ |
| get_watermarks (SF) | embebido en cartridge_list_entities | ✅ |
| get_entity_status (SF) | componible (list_entities + list_jobs + get_run_logs) | ⚠️ falta como tool única |
| run_all_kb (SF) | bucle sobre cartridge_run_kb | ⚠️ falta batch |

`mcp-infra` es **superset**: agrega extract, extract_all, manifest,
semantic, hints, run_logs, job_status, list_jobs, search_term,
sync_semantic_to_rag — que el contenedor por cartucho NO expone.

## B. Extracción (lo que realmente justificaba el contenedor)

- `cartridge_extract` en mcp-infra **no extrae inline**: busca
  `entity_config.dag_id` y **dispara el DAG de Airflow**
  (`mcp-infra/app/tools/cartridges.py`).
- Cobertura de `dag_id` en la DB viva — **100% de las entidades tienen
  DAG**:

| Cartucho | Entidades | Con dag_id |
|---|---|---|
| replicon | 20 | 20 |
| hubspot | 6 | 6 |
| salesforce | 14 | 14 |
| sap_hcm | 10 | 10 |
| sap_s4hana | 25 | 25 |
| sap_successfactors | 31 | 31 |

⇒ Toda la extracción puede correr por Airflow + tool genérica. El
`job_runner.py` del contenedor por cartucho es **redundante** con el DAG.

## C. Custom tools dinámicas (`mcp_custom_tools`)

- El contenedor por cartucho corre `load_custom_tools()` que lee
  `mcp_custom_tools` (tipos sql_query/extract/run_kb).
- En la práctica: **0 custom tools** en todos los `seed.sql` y la tabla
  `mcp_custom_tools` está **vacía** en la DB. El mecanismo existe pero no
  se usa.
- Además los 3 tipos mapean 1:1 a `cartridge_query_kb` / `cartridge_extract`
  / `cartridge_run_kb`. ⇒ Gap nulo en la práctica.

## Veredicto

La paridad es **prácticamente total**. El contenedor por cartucho no
aporta nada que no esté cubierto por mcp-infra + Airflow:

| Capacidad | Cubierta por | Riesgo al borrar contenedor |
|---|---|---|
| 6 tools comunes | mcp-infra genérico | ninguno ✅ |
| Extracción | Airflow DAG (100% dag_id) | ninguno ✅ |
| Custom tools | tabla vacía / mapean a genéricas | ninguno ✅ |
| get_entity_status, run_all_kb (solo SF) | componibles | bajo ⚠️ |

### Huecos a cerrar antes de borrar (mínimos)

1. **salesforce `get_entity_status`**: agregar tool genérica
   `cartridge_get_entity_status(cartridge_id, entity)` que componga
   list_entities + last run + watermark. (~30 líneas en mcp-infra.)
2. **salesforce `run_all_kb`**: agregar `cartridge_run_all_kb(cartridge_id)`
   que itere los KBs. (~15 líneas.)
3. **Verificación de invocación**: confirmar que console/copilot llaman
   las tools vía mcp-infra (server `mcp-infra`) y no vía el server
   per-cartucho registrado en `mcp_servers`. Reapuntar el registro.

### No es hueco (ya cubierto)
- get_watermarks, extract, todas las KB/preview/schema, manifest,
  semantic, hints, run_logs.

## Siguiente paso (Paso 2 del plan)

Cerrar los 2 gaps de salesforce en mcp-infra, luego **piloto replicon**:
apagar el contenedor `replicon`, validar que list/preview/kb/extract
funcionan vía mcp-infra + DAG, correr smoke/e2e del flujo replicon.
Reversible (re-levantar el contenedor) hasta el borrado final.
