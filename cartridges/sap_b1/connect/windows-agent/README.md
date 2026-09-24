# Agente OMEGA para SAP Business One (conector Windows)

Guía para el equipo de TI del cliente. El agente es un programa pequeño que
corre en un servidor Windows de la empresa, lee tablas de SAP Business One
desde la base de datos SAP HANA con un usuario de **solo lectura** y sube
archivos parquet al almacén de datos de OMEGA por **HTTPS saliente**. No
hay que abrir puertos entrantes ni montar túneles o VPN hacia la red de la
empresa: todas las conexiones las inicia el agente hacia afuera.

```
  SAP HANA (Business One)          Servidor Windows del cliente               AWS S3 (OMEGA)
  ┌───────────────────┐   SQL      ┌────────────────────────────┐   HTTPS 443   ┌──────────────────┐
  │ esquemas de las   │ ◄────────  │ agente (tarea cada 2 h)    │ ───────────►  │ bucket/raw/sap_b1/│
  │ empresas          │ solo lect. │ estado SQLite + cola local │ PutObject     │ (solo escritura)  │
  └───────────────────┘            └────────────────────────────┘               └──────────────────┘
```

## Qué instala `install.ps1`

| Elemento | Dónde | Para qué |
| --- | --- | --- |
| Python 3.12 (instalador oficial de python.org, para todos los usuarios, sin tocar `PATH`) | `C:\Program Files\Python312` | Intérprete del agente. Si ya hay un Python 3.11 o superior, se usa ese y no se descarga nada. |
| Código del agente y los módulos del cartucho OMEGA que reutiliza (`agent.py`, `app\core\b1_source.py`, `app\services\b1_queries.py`, `app\services\b1_reader.py`, `app\services\bronze_parquet.py`, `app\config\entities.yaml`) | `C:\Program Files\OmegaSapB1Agent\` | El catálogo de tablas y las reglas de lectura son exactamente los del cartucho que corre en la plataforma OMEGA. |
| Entorno virtual con `hdbcli` (cliente SAP HANA), `pyarrow`, `boto3`, `pyyaml` | `C:\Program Files\OmegaSapB1Agent\venv` | Dependencias aisladas del resto del servidor. |
| Directorio de datos: `agent.toml`, `agent-state.sqlite`, `spool\`, `logs\` | `C:\ProgramData\OmegaSapB1Agent\` | Configuración, marcas de agua y registro de corridas, cola local de archivos pendientes, registros. |
| Tarea programada `OMEGA SAP B1 Agent` | Programador de tareas de Windows | Ejecuta `run.ps1 extract-all` cada 2 horas con la cuenta de servicio. No se solapa consigo misma (`IgnoreNew`) y solo corre con red disponible. |

Permisos que deja configurados:

* `C:\ProgramData\OmegaSapB1Agent\`: `SYSTEM` y `Administradores` control
  total; la cuenta de servicio puede **modificar** (necesita escribir el
  estado, la cola y los registros).
* `agent.toml`: `SYSTEM` y `Administradores` control total; la cuenta de
  servicio **solo lectura**. Ningún otro usuario del servidor puede leerlo.
* `C:\Program Files\OmegaSapB1Agent\`: la cuenta de servicio solo lee y
  ejecuta.

La cuenta de servicio (`-ServiceAccount`) la crea TI antes de instalar: una
cuenta local o de dominio sin privilegios, o una gMSA (`-Gmsa`). Al
registrar la tarea con contraseña, Windows le concede el derecho "Iniciar
sesión como proceso por lotes"; la contraseña queda en el almacén del
Programador de tareas, no en ningún archivo del agente.

### Requisitos

* Windows Server 2016 o superior, PowerShell 5.1 o superior, ejecución como
  administrador local.
* Acceso SQL de solo lectura al tenant de HANA (puerto `3NN15`, TLS) desde
  el servidor. Un usuario de base de datos con `SELECT` sobre las tablas del
  catálogo en cada esquema de empresa (ver "Qué envía").
* Salida HTTPS (443) hacia el bucket de S3 de OMEGA. Durante la instalación,
  además, hacia `www.python.org` y PyPI, salvo que se entreguen el
  instalador (`-PythonInstaller`) y las ruedas (`-WheelDir`) descargados en
  otra máquina.
* Espacio en disco para la cola local: en operación normal está vacía; si
  la salida a S3 se corta, retiene lo extraído hasta 500 archivos (ajustable
  con `max_pending_files`).

## Instalación

1. Copie la carpeta `windows-agent` junto con la carpeta `app` del cartucho
   al servidor (tal como las entrega OMEGA; `install.ps1` busca `app` dos
   niveles arriba o en `windows-agent\cartridge`).
2. En PowerShell como administrador:

   ```powershell
   cd <carpeta>\windows-agent
   .\install.ps1 -ServiceAccount 'DOMINIO\svc-omega-b1' -PythonSha256 <hash publicado en python.org>
   ```

   Variantes: `-Gmsa` para una cuenta de servicio administrada,
   `-PythonInstaller C:\temp\python-3.12.10-amd64.exe -WheelDir C:\temp\wheels`
   sin salida a internet, `-IntervalHours 4` para otra frecuencia, `-NoTask`
   para no registrar la tarea todavía.
3. Edite `C:\ProgramData\OmegaSapB1Agent\agent.toml` y sustituya todos los
   valores entre `< >` (el agente se niega a arrancar mientras quede uno):

   | Sección | Valor | Quién lo da |
   | --- | --- | --- |
   | `[agent]` | `tenant_id`, `workspace_id` | OMEGA |
   | `[source]` | `host`, `port`, `user`, `password`, `database`, `companies` | TI del cliente (DBA de HANA) |
   | `[upload]` | `bucket`, `region`, `access_key_id`, `secret_access_key` | OMEGA (clave limitada a `raw/sap_b1/`) |

   `companies` es la lista `alias=ESQUEMA` de las empresas a leer. El alias
   (minúsculas, sin espacios) es el nombre con el que OMEGA verá a la
   empresa en la columna `_company`; el nombre del esquema nunca sale del
   servidor.

   Cualquier valor puede darse en su lugar como variable de entorno de la
   cuenta de servicio (`SAP_B1_PASSWORD`, `AWS_SECRET_ACCESS_KEY`, ...); la
   variable de entorno tiene prioridad sobre el archivo.
4. Pruebe la conexión y haga la carga inicial:

   ```powershell
   & 'C:\Program Files\OmegaSapB1Agent\windows-agent\run.ps1' test-connection
   & 'C:\Program Files\OmegaSapB1Agent\windows-agent\run.ps1' extract-all --mode full
   ```

   `test-connection` abre la sesión SQL, lee la versión de Business One en
   cada empresa y comprueba que la clave de S3 ve el prefijo `raw/sap_b1/`
   del bucket; no escribe nada. La carga inicial lee las 45 tablas
   completas y puede tardar horas en una empresa con historial largo; corre
   en primer plano y puede seguirse en el registro. A partir de ahí la tarea
   programada lee solo lo que cambió.

## Qué envía

* Las tablas del catálogo `app\config\entities.yaml` (45 tablas estándar de
  Business One: maestros de empresa y finanzas, socios, artículos,
  almacenes, documentos de venta y compra con sus líneas, asientos
  contables, movimientos de inventario, lotes y órdenes de producción) con
  **la lista exacta de columnas** declarada allí para cada tabla. El agente
  nunca ejecuta `SELECT *` ni lee tablas fuera del catálogo; TI puede
  auditar el archivo y limitar las tablas con `entities` / `exclude` en
  `agent.toml`.
* Cada fila lleva además `_company` (el alias de la empresa),
  `_source_updated_at` (fecha y hora de última modificación en Business
  One) y las columnas técnicas `_extracted_at`, `_run_id`,
  `_source_entity`, `_load_type`, `_watermark_value`.
* Un archivo parquet por lote de hasta 10 000 filas, en la ruta
  `raw/sap_b1/<tabla>/tenant_id=<t>/workspace_id=<w>/load_date=<AAAA-MM-DD>/batch_id=<corrida>/<tabla>.parquet`.
* Frecuencia: cada 2 horas, solo las filas modificadas desde la marca de
  agua anterior (con 5 minutos de solape para no perder nada); las tablas
  sin fecha de modificación (existencias, tipos de cambio, ...) se envían
  completas en cada ciclo. Un ciclo sin cambios no envía nada.

* La tabla `IntercompanyPartners`, que no existe en Business One: es el
  mapa de socios intercompañía de `agent.toml` (`[source] intercompany`),
  escrito al final de cada `extract-all` (o solo, con
  `run.ps1 refresh-intercompany`). Lleva código de socio, empresa
  contraparte y origen del mapeo; nunca datos del socio.

## Qué nunca envía ni hace

* Contraseñas, el nombre o dirección del servidor HANA, el usuario de base
  de datos, el nombre de la base o de los esquemas. No aparecen en los
  archivos, ni en los registros (se tachan como `***` antes de escribirlos),
  ni en la salida en pantalla.
* Nada fuera del prefijo `raw/sap_b1/` del bucket: la clave de acceso solo
  tiene `PutObject`/`AbortMultipartUpload` sobre ese prefijo y `ListBucket`
  limitado a él (`iam-policy.template.json`). El agente no puede leer,
  descargar ni borrar lo que ya está en el bucket, y se niega a subir a
  otra ruta aunque se le indique.
* Ninguna escritura en HANA: la sesión es de solo lectura y solo emite
  `SELECT` con parámetros. No modifica, bloquea ni borra nada en Business
  One.
* Ninguna conexión entrante ni a otros destinos: solo HANA (SQL) y el
  bucket (HTTPS). En la instalación, además, python.org y PyPI si no se
  entregan los paquetes descargados.

## Operación diaria

* **Estado**: `run.ps1 status` muestra la marca de agua de cada
  tabla@empresa, las últimas 20 corridas (tabla, modo, resultado, filas,
  error) y cuántos archivos esperan en la cola local. `status --json` da lo
  mismo en JSON.
* **Cola local (`spool\`)**: cada lote se escribe primero en disco y se sube
  a continuación; el archivo solo se borra cuando S3 confirma la subida.
  Si la subida falla (red caída, clave caducada), el archivo se queda, la
  corrida se registra como `failed` con la explicación, y la siguiente
  ejecución vuelve a intentar los pendientes antes de extraer. Nada se
  vuelve a leer de HANA por un fallo de subida. Con 500 archivos pendientes
  el agente deja de extraer hasta que las subidas funcionen.
* **Ejecuciones a mano**: `run.ps1 extract --entity OINV --mode full`
  relee una tabla completa; `run.ps1 extract --entity OINV --from-date
  2025-01-01 --to-date 2025-03-31` reenvía un rango por fecha de documento
  sin tocar las marcas de agua. Solo puede correr una ejecución a la vez:
  una segunda espera o termina con `another agent run is in progress`.
* **Códigos de salida** (los ve el Programador de tareas): `0` correcto,
  `1` alguna corrida falló o hay archivos sin subir, `2` error de
  configuración.

## Cómo leer el registro

`C:\ProgramData\OmegaSapB1Agent\logs\agent.log` (rota a 5 MB, se conservan
5 archivos). Una línea por evento:

```
2026-09-24 08:00:03,412 INFO omega-sap-b1-agent: run 3f1c...: OINV mode=incremental companies=empresa_a,empresa_b destination=s3://<bucket>/raw/sap_b1/
2026-09-24 08:00:05,020 INFO omega-sap-b1-agent: batch 1: 143 rows -> raw/sap_b1/OINV/tenant_id=.../batch_id=3f1c.../OINV.parquet
2026-09-24 08:00:05,031 INFO omega-sap-b1-agent: watermark OINV@empresa_a -> 2026-09-24T07:58:11
2026-09-24 08:00:05,900 INFO omega-sap-b1-agent: run 3f1c... finished: 143 rows in 1 batch(es)
```

Mensajes que conviene conocer:

| Mensaje | Significado | Qué hacer |
| --- | --- | --- |
| `run ... failed: B1SourceError: connection failed (...)` | No se pudo abrir la sesión con HANA. | Revisar red, puerto, TLS y el usuario en `agent.toml`; `run.ps1 test-connection`. |
| `run ... failed: B1SourceError: query failed (...)` | Una consulta falló (columna o tabla inexistente, permisos). | Confirmar `SELECT` sobre la tabla en ese esquema; avisar a OMEGA si la columna no existe en esa versión de Business One. |
| `upload attempt n/5 failed ...` / `kept in spool, will retry on the next run` | S3 no respondió; el archivo queda en la cola. | Nada si es transitorio; si persiste, revisar salida HTTPS y la clave. |
| `upload rejected (AccessDenied)` | La clave no tiene permiso o caducó. | Rotar la clave (abajo). |
| `spool: N file(s) from earlier runs still pending` | Hay lotes de ciclos anteriores sin subir. | Igual que el anterior. |
| `watermark ... is not parseable; reading the whole table` | La marca de agua guardada está corrupta; se relee la tabla completa (sin pérdida). | Nada. |
| `configuration: ...` | Falta o sobra algo en `agent.toml` (código de salida 2). | Corregir el valor que indica. |

Todo lo que el agente considera secreto (contraseña, host, usuario, base,
esquemas, claves) aparece como `***` en el registro, incluso dentro de
mensajes de error del controlador de HANA o de AWS.

## Rotación de credenciales

**Contraseña de HANA** (sin interrupción):

1. El DBA cambia la contraseña del usuario de solo lectura en HANA.
2. Un administrador edita `agent.toml` (`password`) o la variable de
   entorno `SAP_B1_PASSWORD` de la cuenta de servicio.
3. `run.ps1 test-connection`. Si una ejecución programada cayó entre 1 y 2,
   quedó como `failed` y el siguiente ciclo recupera los cambios (la marca
   de agua no avanzó).

**Clave de S3**:

1. OMEGA crea una segunda clave para el mismo usuario IAM (la política sigue
   limitada a `raw/sap_b1/`).
2. Un administrador actualiza `access_key_id` y `secret_access_key` en
   `agent.toml` (o `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`).
3. `run.ps1 test-connection` y, si hay archivos en la cola,
   `run.ps1 extract-all` los sube antes de extraer.
4. OMEGA desactiva y borra la clave anterior.

**Cuenta de servicio**: si cambia su contraseña, vuelva a registrar la
tarea (`install.ps1` con los mismos parámetros; conserva `agent.toml`) o
actualícela en el Programador de tareas.

## Desinstalación

```powershell
& 'C:\Program Files\OmegaSapB1Agent\windows-agent\uninstall.ps1'            # quita tarea y código; conserva los datos
& 'C:\Program Files\OmegaSapB1Agent\windows-agent\uninstall.ps1' -PurgeData # además borra configuración, estado y cola
```

Antes de borrar los datos compruebe con `run.ps1 status` que la cola está
vacía: lo que quede en `spool\` no llegó al lakehouse. Python y la cuenta
de servicio no se desinstalan.

## Política IAM de la clave (para OMEGA)

`iam-policy.template.json`, con `<BUCKET>` sustituido por el bucket del
lakehouse: `s3:PutObject` y `s3:AbortMultipartUpload` sobre
`arn:aws:s3:::<BUCKET>/raw/sap_b1/*`, y `s3:ListBucket` sobre el bucket
condicionado a `s3:prefix = raw/sap_b1/*` (lo usa `test-connection` para
comprobar la clave sin escribir). Nada de lectura ni de borrado. Si el
bucket exige cifrado con KMS, la clave necesita además `kms:GenerateDataKey`
sobre esa clave KMS y `agent.toml` debe llevar
`server_side_encryption = "aws:kms"`. Para un cliente concreto la política
puede ceñirse aún más al prefijo
`raw/sap_b1/*/tenant_id=<TENANT_ID>/workspace_id=<WORKSPACE_ID>/*`.
