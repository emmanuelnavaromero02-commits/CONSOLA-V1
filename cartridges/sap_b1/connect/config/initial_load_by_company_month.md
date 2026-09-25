# Carga inicial de 24 meses, por entidad y mes

Procedimiento para la primera carga de SAP Business One en la plataforma:
24 meses de historia, una corrida por (entidad, mes), dentro de la ventana de
baja carga del cliente, sin abrir el ciclo incremental hasta el final.

## Cómo lee el cartucho una ventana de fechas

* El DAG `sap_b1_extract` (`cartridges/sap_b1/dags/sap_b1_extract.py`) acepta
  en `conf`: `entity`, `mode`, `from_date`, `to_date`, `job_id`, `tenant_id`,
  `workspace_id`, `security_context`. Llama a
  `POST /entities/{entity}/extract` del cartucho y espera la respuesta
  (timeout de 3 600 s).
* Con `from_date` o `to_date` el cartucho pasa a modo `historical` sea cual
  sea `mode` (`b1_reader.effective_mode`, la misma regla que aplica el agente
  Windows). Las dos fechas son ISO y **ambas inclusivas**:
  `date_field >= from_date` y `date_field < to_date + 1 día`
  (`b1_queries.select_sql`).
* La columna de corte es `date_field` de cada entidad (`app/config/entities.yaml`):
  `DocDate` en documentos de venta y compra, en los traspasos OWTR/WTR1 y en
  OINM e IBT1; `RefDate` en OJDT/JDT1; `PostDate` en OWOR/WOR1. Las líneas
  se filtran por la fecha de su cabecera (se leen con JOIN a ella).
* Las tablas maestras y los snapshots no tienen `date_field`: se cargan una
  sola vez con `mode=full` y sin fechas. Un `historical` sobre ellas devuelve
  error 400.
* **Una corrida lee todas las empresas configuradas en `SAP_B1_COMPANIES`,
  una tras otra**; cada empresa se vuelca a Bronze antes de empezar la
  siguiente. La unidad práctica es por tanto (entidad, mes) con las tres
  empresas en secuencia. Si hace falta aislar una empresa (por ejemplo, un
  esquema mucho mayor que los otros), se restringe temporalmente
  `SAP_B1_COMPANIES` a ese alias, se reinicia el cartucho y se repite el
  bucle; es la excepción, no la regla.
* Una corrida `historical` **no fija marca de agua** (`extraction_service`
  solo la escribe en `incremental` y `full`). Por eso el paso 5 la siembra a
  mano: un ciclo incremental sin marca leería la tabla completa.
* Cada corrida pide al refinement un refresco silver de la fuente
  (`job_runner._trigger_silver_refresh`, `refresh-by-source`). Mientras no
  haya datasets silver registrados para `sap_b1` no hace nada; cuando los
  haya, 24 meses × 26 entidades con fecha de refrescos es el coste a medir
  antes de la carga real.

## Alcance (leer antes de lanzar nada)

El cartucho solo aplica el alcance tenant/workspace si la petición trae un
`security_context` firmado y confiable; sin él,
`request_context.scoped_prefix()` devuelve vacío y los parquet aterrizan en
`raw/sap_b1/<entidad>/load_date=...` **sin** `tenant_id=`/`workspace_id=`.

* `airflow dags trigger --conf` (el fragmento de abajo) pone `tenant_id` y
  `workspace_id` en `conf`; el DAG de sap_b1 los reenvía, pero **no firma**
  el contexto como hace `sap_successfactors_extract.py`
  (`_security_context_from_conf`). Hasta que el DAG lo haga, ese camino
  aterriza sin alcance.
* La ruta que hoy firma el alcance y admite fechas es la herramienta MCP
  `cartridge_extract` de mcp-infra (`mcp-infra/app/tools/cartridges.py`,
  `_attach_security_scope`), invocada desde el copiloto de la consola con la
  sesión de un usuario del workspace. La consola no expone `from_date` en su
  API de entidades.
* Regla: lanzar **un mes de una entidad pequeña** primero, esperar, y
  comprobar en `GET /runs` del cartucho que `storage_uri` contiene
  `tenant_id=.../workspace_id=...`. Si no, parar y arreglar el DAG antes de
  seguir.

## Orden de entidades

1. **Maestros y finanzas** (`mode=full`, sin fechas, una vez):
   `CINF OADM OCRN ORTT OACT OFPR OPRC OCRG OSLP OWHS OITB`, y después los
   maestros con marca `OCRD OITM OITT ITT1` (en `full` sí fijan marca de agua).
2. **Documentos de venta** por mes, cabecera antes que sus líneas:
   `OINV→INV1`, `ORIN→RIN1`, `ODLN→DLN1`, `ORDN→RDN1`, `ORDR→RDR1`.
3. **Documentos de compra** por mes: `OPCH→PCH1`, `ORPC→RPC1`, `OPDN→PDN1`,
   `OPOR→POR1`.
4. **Diario** por mes (`RefDate`): `OJDT→JDT1`.
5. **Inventario** por mes (`DocDate`): `OINM`, `IBT1` y los traspasos de
   almacén `OWTR→WTR1`. Al final, los snapshots `OITW OBTN OBTQ OIBT` con
   `mode=full`.
6. **Producción** por mes (`PostDate`): `OWOR→WOR1`.

Las tres listas del fragmento (`MASTERS`, `DATED`, `SNAPSHOTS_LAST`) cubren
las 45 entidades de `entities.yaml`; `DATED` son las 26 con `date_field`.

El bucle de abajo recorre mes a mes (del más reciente al más antiguo, para
que los tableros tengan algo cuanto antes) y, dentro de cada mes, las
entidades en este orden. Así cada mes queda completo y se puede conciliar con
el cliente antes de seguir.

## Ventana de baja carga

* Acordar con el cliente la ventana (hora local suya) y pasarla al script
  como `WINDOW_START`/`WINDOW_END` en hora del host. El script no lanza nada
  fuera de la ventana; guarda un fichero de progreso y continúa la noche
  siguiente donde se quedó.
* Una corrida = un DAG run; el script **espera a que termine** antes de
  lanzar la siguiente. Nunca dos corridas de la misma entidad a la vez: el
  cartucho no lo impide y el DAG reintenta (2 reintentos con retroceso) si su
  cliente HTTP agota los 3 600 s aunque el cartucho siga leyendo.
* Si un mes de una entidad grande (INV1, JDT1, OINM) tarda más de ~40 min,
  partirlo en dos quincenas con `from_date`/`to_date`: la granularidad es el
  día.

## Fragmento

Ejecutar en el host de la plataforma, desde la raíz del repositorio, con el
compose que corresponda (`infra/docker-compose.yml` en local;
`infra/terraform/deploy/docker-compose.aws.yml` en AWS).

```bash
#!/usr/bin/env bash
# Initial load: one (entity, month) at a time, inside the low-load window.
set -euo pipefail

TENANT_ID="${TENANT_ID:?export TENANT_ID=<TENANT_UUID>}"
WORKSPACE_ID="${WORKSPACE_ID:?export WORKSPACE_ID=<WORKSPACE_UUID>}"
COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
MONTHS_BACK="${MONTHS_BACK:-24}"
WINDOW_START="${WINDOW_START:-22:00}"     # host local time, low-load window
WINDOW_END="${WINDOW_END:-05:30}"
CHECKPOINT="${CHECKPOINT:-$HOME/sap_b1_initial_load.done}"
POLL_SECONDS="${POLL_SECONDS:-30}"

# `airflow` here is the CLI inside the airflow container.
airflow() { docker compose -f "$COMPOSE_FILE" exec -T airflow airflow "$@"; }

MASTERS="CINF OADM OCRN ORTT OACT OFPR OPRC OCRG OSLP OWHS OITB OCRD OITM OITT ITT1"
DATED="OINV INV1 ORIN RIN1 ODLN DLN1 ORDN RDN1 ORDR RDR1 OPCH PCH1 ORPC RPC1 OPDN PDN1 OPOR POR1 OJDT JDT1 OINM IBT1 OWTR WTR1 OWOR WOR1"
SNAPSHOTS_LAST="OITW OBTN OBTQ OIBT"

touch "$CHECKPOINT"

in_window() {
    local now; now="$(date +%H:%M)"
    if [[ "$WINDOW_START" > "$WINDOW_END" ]]; then      # window crosses midnight
        [[ "$now" > "$WINDOW_START" || "$now" < "$WINDOW_END" ]]
    else
        [[ "$now" > "$WINDOW_START" && "$now" < "$WINDOW_END" ]]
    fi
}

run_state() {   # $1 = run_id -> prints the DAG run state
    airflow dags list-runs -d sap_b1_extract -o json 2>/dev/null \
        | python3 -c 'import json,sys; rid=sys.argv[1]; print(next((r["state"] for r in json.load(sys.stdin) if r["run_id"]==rid), "unknown"))' "$1"
}

trigger_and_wait() {   # $1 = run_id, $2 = conf JSON
    if grep -qx "$1" "$CHECKPOINT"; then echo "skip $1 (done)"; return 0; fi
    if ! in_window; then echo "outside the window; resume tonight from $CHECKPOINT"; exit 0; fi
    echo "trigger $1"
    airflow dags trigger sap_b1_extract --run-id "$1" --conf "$2" >/dev/null
    local state
    while :; do
        sleep "$POLL_SECONDS"
        state="$(run_state "$1")"
        case "$state" in
            success) echo "$1" >> "$CHECKPOINT"; echo "done $1"; return 0 ;;
            failed)  echo "FAILED $1: inspect the run in Airflow before continuing"; exit 1 ;;
            *)       ;;
        esac
    done
}

conf() {   # $1 = entity, $2 = mode, $3 = from_date (optional), $4 = to_date (optional)
    python3 - "$@" <<'PY'
import json, os, sys
entity, mode = sys.argv[1], sys.argv[2]
conf = {"entity": entity, "mode": mode, "cartridge_id": "sap_b1", "triggered_by": "initial_load",
        "tenant_id": os.environ["TENANT_ID"], "workspace_id": os.environ["WORKSPACE_ID"]}
if len(sys.argv) > 3 and sys.argv[3]:
    conf["from_date"], conf["to_date"] = sys.argv[3], sys.argv[4]
print(json.dumps(conf))
PY
}

# 1. Masters and finance, once, whole table.
for entity in $MASTERS; do
    trigger_and_wait "init_${entity}_full" "$(TENANT_ID="$TENANT_ID" WORKSPACE_ID="$WORKSPACE_ID" conf "$entity" full)"
done

# 2-6. Dated entities, month by month, newest first, headers before lines.
for ((i = 0; i < MONTHS_BACK; i++)); do
    from="$(date -d "$(date +%Y-%m-01) -${i} month" +%F)"
    to="$(date -d "$from +1 month -1 day" +%F)"
    for entity in $DATED; do
        trigger_and_wait "init_${entity}_${from:0:7}" \
            "$(TENANT_ID="$TENANT_ID" WORKSPACE_ID="$WORKSPACE_ID" conf "$entity" historical "$from" "$to")"
    done
done

# 5b. Inventory snapshots last: they describe today, not history.
for entity in $SNAPSHOTS_LAST; do
    trigger_and_wait "init_${entity}_full" "$(TENANT_ID="$TENANT_ID" WORKSPACE_ID="$WORKSPACE_ID" conf "$entity" full)"
done
echo "initial load complete; now seed the watermarks (step 5) and enable the schedule."
```

Notas del fragmento:

* `--run-id` estable por (entidad, mes): un segundo `trigger` con el mismo id
  es rechazado por Airflow y el fichero de progreso evita repetirlo.
* `date -d` es GNU date (Ubuntu). En macOS usar `gdate` de coreutils.
* `conf` lleva `tenant_id`/`workspace_id`; véase el apartado de alcance.

## Paso 5: sembrar las marcas de agua

Antes de habilitar el ciclo incremental (`schedule_entities_every_2h.sql`),
en la base de datos de la plataforma, como el superusuario de migraciones
(`entity_watermarks` tiene RLS; el superusuario la salta):

```sql
-- psql "$DATABASE_URL" -v tenant_id=<TENANT_UUID> -v workspace_id=<WORKSPACE_UUID> \
--      -v cutoff='<YYYY-MM-DDTHH:MM:SS>'   (reloj de HANA al INICIO de la carga inicial,
--                                          SELECT CURRENT_TIMESTAMP FROM DUMMY)
\set ON_ERROR_STOP on
INSERT INTO entity_watermarks
    (cartridge_id, entity_name, watermark_field, last_watermark_value, last_run_id,
     tenant_id, workspace_id, watermark_scope)
SELECT 'sap_b1', e.entity || '@' || c.alias, e.watermark_field, :'cutoff', 'initial-load',
       :'tenant_id'::uuid, :'workspace_id'::uuid,
       'tenant:' || :'tenant_id' || ':workspace:' || :'workspace_id'
FROM entity_config AS e
CROSS JOIN (VALUES ('mx_mfg'), ('mx_dist_a'), ('mx_dist_b')) AS c(alias)   -- the aliases in SAP_B1_COMPANIES
WHERE e.cartridge_id = 'sap_b1'
  AND e.watermark_format = 'b1_update_ts'
ON CONFLICT (watermark_scope, cartridge_id, entity_name) DO NOTHING;         -- keeps the ones `full` already wrote
```

* Formato de la marca: `YYYY-MM-DDTHH:MM:SS` en el reloj de la fuente
  (`README`, "Incremental reads"). Con el corte al inicio de la carga, todo
  documento editado durante o después de ella entra en el primer ciclo.
  Documentos anteriores a los 24 meses que se editen más tarde también entran
  (su `UpdateDate` es reciente); es el comportamiento deseado.
* `OINM` y `IBT1` usan marca entera: sembrar por empresa con el máximo leído
  en HANA en el momento del corte, como texto:
  `SELECT MAX("TransNum") FROM "<COMPANY_DB_1>"."OINM"` y
  `SELECT MAX("LogEntry") FROM "<COMPANY_DB_1>"."IBT1"`, e insertar
  `('sap_b1', 'OINM@mx_mfg', 'TransNum', '<valor>', ...)` y
  `('sap_b1', 'IBT1@mx_mfg', 'LogEntry', '<valor>', ...)` con el mismo
  `watermark_scope`. Los movimientos posteriores tienen `TransNum` mayor
  aunque su fecha sea anterior (asientos retroactivos), así que no se pierden.

## Verificación por mes

* `GET /runs` del cartucho: una fila por corrida con `status`,
  `records_extracted` y `storage_uri`; ninguna corrida `failed` queda como
  "0 filas".
* Conteo por empresa y mes en Bronze (DuckDB sobre el lakehouse) contra el
  conteo en HANA para el mismo rango de `DocDate`:
  `SELECT COUNT(*) FROM "<COMPANY_DB_1>"."OINV" WHERE "DocDate" >= '<from>' AND "DocDate" < '<to + 1 día>'`.
* Conciliar con el cliente al menos ventas (OINV/ORIN) y diario (OJDT) de
  un mes cerrado antes de continuar con el resto.
