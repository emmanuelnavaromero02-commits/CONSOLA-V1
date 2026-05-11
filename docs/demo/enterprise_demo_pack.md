# Enterprise Demo Pack

Reproducible end-to-end demo data for **Replicon PSA + SAP SuccessFactors / HCM + SAP S/4HANA**, designed to exercise every surface of the console:

- Studio / Resumen
- Entidades
- Refinar (Bronze / Silver / Gold)
- Analytics
- IA Semántica
- RAG
- Asistente IA

All demo objects are clearly marked (`demo_*` prefix on tables, `[DEMO PACK]` text marker on metadata, dedicated `demo_enterprise` cartridge) and can be wiped in a single command. **Real Replicon / SAP data is never touched.**

---

## 1. What the seed creates

| Surface | Object | Where it lives |
|---|---|---|
| Cartridges | `demo_enterprise` row in `cartridges` | main DB (`modecissions`) |
| Entities | `Demo*` rows in `entity_config` for `replicon`, `sap_successfactors`, `sap_hcm`, `sap_s4hana`, `demo_enterprise` | main DB |
| Bronze | Tables in schema `demo_bronze.*` | main DB |
| Silver | Views in schema `demo_silver.*` | main DB |
| Gold | Tables `gold_demo_*` in `public` schema | gold DB (`modecissions_gold`) |
| Dataset registry | Rows in `datasets` (silver + gold) | main DB |
| Semantic terms | 18 terms under cartridge `demo_enterprise` | main DB (`semantic_terms`) |
| RAG | 11 knowledge bits as `rag_sources` (parent chunks only) | main DB (`rag_sources`, `rag_chunks`) |

### Bronze tables (`demo_bronze.*`)

| Table | Default volume |
|---|---|
| `replicon_users` | 2,000 |
| `replicon_clients` | 300 |
| `replicon_projects` | 2,000 |
| `replicon_tasks` | 10,000 |
| `replicon_assignments` | 8,000 |
| `replicon_timesheets` | 100,000 |
| `replicon_expenses` | 20,000 |
| `replicon_invoices` | 15,000 |
| `sap_departamentos` | 40 |
| `sap_centros_coste` | 300 |
| `sap_posiciones` | 5,000 |
| `sap_empleados` | 20,000 |
| `sap_compensaciones` | 20,000 |
| `sap_ausencias` | 50,000 |
| `sap_proveedores` | 5,000 |
| `sap_customers` | 5,000 |
| `sap_materiales` | 10,000 |
| `sap_ordenes_compra` | 50,000 |
| `sap_facturas_proveedor` | 30,000 |
| `sap_ventas` | 40,000 |
| `sap_journal_entries` | 100,000 |

**Total:** ~470k rows. Scale with `--scale 0.1` (~47k) for a quick smoke run.

### Silver views (`demo_silver.*`)

- `replicon_timesheets_limpios` — billable / coste pre-calculados
- `replicon_project_finance` — revenue real, coste real, margen, `sobre_presupuesto`
- `sap_empleados_limpios` — empleados con `status` no nulo
- `sap_proveedores_riesgo` — score numérico 1-3 sobre riesgo
- `sap_ordenes_compra_limpias` — POs con monto > 0
- `sap_finanzas_limpias` — journal entries normalizados

### Gold tables (`pggold.gold_demo_*`)

29 tablas materializadas. Mapeo a los KPIs solicitados:

| KPI / Pregunta | Tabla |
|---|---|
| Revenue por cliente | `gold_demo_revenue_por_cliente`, `gold_demo_top_clientes_revenue`, `gold_demo_revenue_por_customer` |
| Revenue por proyecto | `gold_demo_revenue_por_proyecto` |
| Margen por proyecto | `gold_demo_margen_por_proyecto` |
| Utilización por consultor | `gold_demo_utilizacion_consultores`, `gold_demo_consultores_baja_utilizacion` |
| Horas facturables vs no facturables | `gold_demo_horas_facturables` |
| Gastos por proyecto | `gold_demo_gastos_por_proyecto` |
| Facturas vencidas | `gold_demo_facturas_vencidas_replicon`, `gold_demo_facturas_vencidas_sap` |
| Proyectos sobre presupuesto | `gold_demo_proyectos_sobre_presupuesto` |
| Headcount activo / por dep / ubicación | `gold_demo_headcount_por_departamento`, `gold_demo_headcount_por_ubicacion` |
| Salario promedio por dep | `gold_demo_salario_promedio` |
| Empleados sin centro de coste | `gold_demo_empleados_sin_centro_coste` |
| Bajas recientes | `gold_demo_bajas_recientes` |
| Ausencias por departamento | `gold_demo_ausencias_por_departamento` |
| Managers con muchos reportes | `gold_demo_managers_demasiados_reportes` |
| Posiciones vacantes | `gold_demo_posiciones_vacantes` |
| Spend por proveedor / categoría | `gold_demo_spend_por_proveedor`, `gold_demo_spend_por_categoria` |
| Órdenes abiertas | `gold_demo_ordenes_abiertas` |
| Proveedores alto riesgo | `gold_demo_proveedores_riesgo_alto` |
| Margen por categoría (S/4) | `gold_demo_margen_por_categoria` |
| GL balance por cuenta | `gold_demo_gl_balance_por_cuenta` |
| Gastos por centro de coste | `gold_demo_gastos_por_centro_coste` |
| Top proveedores por spend | `gold_demo_top_proveedores_spend` |
| Resumen CFO | `gold_demo_resumen_cfo` |
| Resumen COO | `gold_demo_resumen_operativo` |
| Resumen RRHH | `gold_demo_resumen_hr` |

> Cada tabla gold incluye una columna `revenue_manager TEXT DEFAULT 'N/D'` para que el filtro RLS de `refinement/app/duckdb_engine.py` devuelva filas a cualquier usuario autenticado.

---

## 2. Cómo cargar el demo

### Pre-requisitos

1. La pila debe estar arriba: `docker compose up -d` en `infra/`.
2. Las DBs deben estar accesibles desde el host (ports 15432 y 15433 por defecto en `docker-compose.yml`).
3. `psycopg2-binary` instalado en el entorno donde corras el script:
   ```bash
   pip install psycopg2-binary
   ```

### Cargar (full volume)

```bash
# Desde la raíz del repo, con POSTGRES_PASSWORD en el entorno:
export POSTGRES_PASSWORD="<la_misma_que_en_infra/.env>"
make demo-seed
```

O directamente:

```bash
PG_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@localhost:15432/modecissions" \
GOLD_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@localhost:15433/modecissions_gold" \
    python3 scripts/seed_enterprise_demo.py
```

Tarda aproximadamente **1-3 min** dependiendo del hardware (la mayoría del tiempo lo consumen los 100k timesheets y 100k journal entries).

### Cargar versión reducida (smoke test)

```bash
make demo-seed-smoke      # scale=0.1 → ~47k filas total, < 30 s
```

### Sólo recomputar gold (sin regenerar bronze)

```bash
make demo-rebuild-gold
```

### Sólo re-registrar metadata (datasets / entidades / semantic / RAG)

```bash
make demo-only-meta
```

### Reset completo

```bash
make demo-reset
```

Esto borra:

- Schemas `demo_bronze`, `demo_silver` (CASCADE)
- Todas las tablas `gold_demo_*` en `pggold`
- Filas `datasets WHERE name LIKE 'demo_%'`
- Entidades demo en `entity_config`
- Cartridge `demo_enterprise`
- Términos en `semantic_terms WHERE cartridge_id='demo_enterprise'`
- Sources/chunks en `rag_sources WHERE name LIKE '[DEMO PACK]%'`

No toca nada que no tenga marcador demo.

---

## 3. Qué se ve en cada pestaña

### Studio / Resumen
- El dropdown de cartridges muestra ahora: `replicon`, `sap_successfactors`, `sap_hcm`, `sap_s4hana`, **`demo_enterprise`**.
- Al elegir cualquiera de los 4 cartridges originales, las nuevas entidades `Demo*` aparecen en la lista (con prefijo `[DEMO]` y `enabled=FALSE`).

### Entidades
- Bajo cada cartridge: lista de entidades demo cargadas, con `display_name = '[DEMO] DemoUsers'` etc.
- Bajo `demo_enterprise`: 3 entidades agregadoras (`AllReplicon`, `AllSAPHR`, `AllSAPS4`).

### Refinar
- Filtrando por capa = `silver`: 6 silver views demo (prefijo `demo_silver_*`).
- Filtrando por capa = `gold`: 29 datasets demo (prefijo `demo_*`).

### Analytics
- `GET /api/data/{dataset}` funciona con cualquiera de los datasets `demo_*` registrados.
  Ejemplos:
  ```bash
  curl -H "Cookie: ..." http://localhost:8000/api/data/demo_resumen_cfo
  curl -H "Cookie: ..." http://localhost:8000/api/data/demo_margen_por_proyecto?limit=50
  curl -X POST http://localhost:8000/api/data/demo_facturas_vencidas_replicon/query \
       -H "Content-Type: application/json" -d '{"filters":{},"limit":20}'
  ```

### IA Semántica
- `GET /api/semantic?cartridge=demo_enterprise` devuelve los 18 términos.
- En el resto de cartridges (replicon, sap_*) NO se inyectan términos demo — los términos demo viven todos bajo `demo_enterprise` para no contaminar el glosario real.

### RAG
- `GET /api/rag/sources` lista los 11 documentos `[DEMO PACK] ...`.
- **Importante:** los documentos se insertan como chunks de tipo `parent` SIN embeddings. Para que la búsqueda semántica funcione, vuelve a ingestarlos vía `/api/rag/ingest` (esto invocará al embedder de Gemini si está configurado):
  ```bash
  for src in "Margen por proyecto" "Utilización de consultores" "Proyectos sobre presupuesto" \
             "Empleado sin centro de coste" "Proveedor de alto riesgo" "Facturas vencidas" \
             "Órdenes de compra abiertas" "Qué debe mirar un CFO" "Qué debe mirar un COO" \
             "Qué debe mirar RRHH" "Top 10 riesgos operativos"; do
      curl -X POST http://localhost:8000/api/rag/ingest \
           -H "Content-Type: application/json" \
           -d "{\"source_id_or_name\":\"[DEMO PACK] $src\"}"
  done
  ```
  (Ajustar el body según el contrato actual del endpoint — el formato exacto está en `mcp-infra/app/tools/rag.py`.)

---

## 4. Pruebas manuales recomendadas

1. **Abrir** http://localhost:8000/studio
2. **Verificar** que aparece el cartridge `[DEMO PACK] Enterprise Demo` en el listado.
3. **Entrar a Entidades** → seleccionar `replicon` → ver `[DEMO] DemoUsers` … `[DEMO] DemoInvoices` (8 entidades).
4. **Entrar a Refinar** → filtrar layer=`gold` → ver los 29 datasets `demo_*`.
5. **Click** en `demo_resumen_cfo` → "Preview" → debe mostrar 8 KPIs financieros (revenue, coste, margen, etc.).
6. **Analytics**: probar el endpoint:
   ```
   GET /api/data/demo_margen_por_proyecto?limit=5
   ```
7. **Asistente IA** — probar las preguntas de la sección 5.

---

## 5. Preguntas para probar la IA

Las siguientes preguntas deberían tener respuesta concreta basada en los datasets `demo_*`. Si la IA no las contesta apropiadamente, ver "Troubleshooting" más abajo.

```
Dame un resumen CFO de la empresa demo
¿Cuánto revenue hay por cliente?
¿Cuál es el margen por proyecto?
¿Qué proyectos están sobre presupuesto?
¿Qué consultores tienen baja utilización?
¿Cuántas horas son facturables vs no facturables?
¿Qué facturas están vencidas?
¿Qué empleados no tienen centro de coste?
¿Qué proveedores tienen alto riesgo?
¿Cuál es el spend por categoría?
Dame los 10 riesgos principales de la operación
Dame recomendaciones ejecutivas
```

---

## 6. Seguridad y reproducibilidad

- **Marcado demo:** todos los IDs van prefijados con `demo_*`, las descripciones llevan `[DEMO PACK]`, y los datasets viven en schemas dedicados (`demo_bronze`, `demo_silver`) o tablas con nombre `gold_demo_*`.
- **No toca SAP real:** las entidades reales de SAP (autocargadas desde `cartridges/sap_*/app/config/entities.yaml`) siguen intactas. Las entidades demo se agregan como filas adicionales en `entity_config` con `enabled=FALSE`.
- **No toca Replicon real:** lo mismo aplica para Replicon.
- **Reset idempotente:** `make demo-reset` deja la DB exactamente como estaba antes (los inserts del seed son `ON CONFLICT DO NOTHING/UPDATE`).
- **Determinismo:** los generadores usan `random.Random(seed)` con seeds fijos (42 para Replicon, 101 para HR, 202 para S/4) — dos ejecuciones generan exactamente los mismos datos.

---

## 7. Troubleshooting

**El IA no encuentra los datos demo.**
- La IA usa principalmente RAG + las métricas en `datasets`. Si no encuentras respuestas semánticamente:
  1. Reingestar los documentos RAG con embeddings (ver sección 3 → RAG).
  2. Verificar que `/api/semantic?cartridge=demo_enterprise` devuelve los 18 términos.
  3. El asistente puede invocar `/api/data/{dataset}` directamente — confirmar que `gold_demo_*` está poblado.

**`/api/data/demo_xxx` devuelve 0 filas.**
- El RLS exige `revenue_manager='N/D'` o que el usuario coincida con el RM. Las tablas se crean con `revenue_manager TEXT DEFAULT 'N/D'`, así que cualquier usuario las ve. Si modificaste manualmente el valor, restaurarlo a `'N/D'` o re-ejecutar `make demo-rebuild-gold`.

**El script tarda demasiado.**
- Reducir el volumen con `--scale 0.1`.
- O cargar por fases: `python3 scripts/seed_enterprise_demo.py --only replicon`, luego `--only hr`, `--only s4`, `--only gold`, `--only meta`.

**`psycopg2.OperationalError: could not connect`.**
- Verificar `docker compose ps` que `mode_postgres` y `mode_postgres_gold` están healthy.
- Exportar `POSTGRES_PASSWORD` o `PG_DSN`/`GOLD_DSN` con valores válidos.

---

## 8. Archivos del demo pack

```
scripts/seed_enterprise_demo.py      # Loader reproducible — fuente de verdad
Makefile                              # Targets demo-seed / demo-reset / etc.
docs/demo/enterprise_demo_pack.md     # Este documento
```

Toda la lógica vive en el Python script. No se modifican archivos de inicialización en `infra/init/` — el demo pack es **completamente opt-in** y no se ejecuta automáticamente al levantar la pila.
