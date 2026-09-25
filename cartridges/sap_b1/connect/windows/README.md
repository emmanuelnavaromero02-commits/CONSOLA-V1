# Alternativa: agente Windows en el servidor del cliente

Segunda opción para traer los datos de SAP Business One cuando la TI del
cliente prefiere no mantener un túnel permanente (`../vpn/`). La
implementación y la guía para la TI del cliente viven en
[`../windows-agent/README.md`](../windows-agent/README.md); este documento
solo resume qué es, en qué se diferencia del túnel y cuándo elegirlo.

## Qué es

Un programa pequeño en Python (`../windows-agent/agent.py`) que corre en un
servidor Windows del cliente con una cuenta de servicio sin privilegios.
`install.ps1` lo instala con el Python oficial de python.org en un entorno
virtual propio y registra una tarea del Programador de tareas de Windows
que lo ejecuta cada 2 horas. En cada ejecución:

1. lee HANA **localmente**, en la red del cliente, con el mismo usuario de
   solo lectura de `../hana/01_create_readonly_user.sql` y el mismo catálogo
   de 45 tablas y reglas incrementales del cartucho (`app/config/entities.yaml`,
   `b1_queries`, `b1_reader`, `bronze_parquet`; no es una bifurcación);
2. escribe cada lote como parquet en una cola local (`spool\`) y guarda las
   marcas de agua y el registro de corridas en un SQLite local;
3. sube los archivos por **HTTPS (443) de salida** al bucket del lakehouse
   con una clave limitada al prefijo `raw/sap_b1/` (solo escritura;
   `iam-policy.template.json`). Un archivo se borra de la cola solo cuando
   S3 acepta la subida; si la salida falla, la siguiente ejecución
   reintenta los pendientes antes de extraer;
4. la plataforma refina lo que aterriza (silver, gold y KPIs), igual que con
   el túnel.

Lo que **no** hay, para no prometerlo: ningún servicio de Windows ni
paquete instalable propios (es el Python oficial más una tarea programada),
ningún canal desde la plataforma hacia el agente, y ninguna supervisión
remota más allá de lo que aterriza en el bucket. El estado se consulta en
el servidor del cliente (`run.ps1 status`) y en su registro local.

## En qué se diferencia del túnel (`../vpn/`)

| | Túnel WireGuard (recomendado) | Agente Windows |
|---|---|---|
| Software en el servidor del cliente | Cliente WireGuard oficial | Python oficial (python.org), el agente en un entorno virtual y una tarea programada |
| Red del cliente | Una salida UDP; sin entradas | Solo salidas HTTPS 443; sin túnel ni entradas |
| Credenciales de HANA | En nuestro vault, cifradas, revocables por el cliente | **Nunca salen** del servidor del cliente (`agent.toml`, legible solo por la cuenta de servicio y los administradores) |
| Dónde corre la extracción | En la plataforma (bajo demanda desde la consola) | En el servidor del cliente (la tarea programada, cada 2 h) |
| Marcas de agua y registro de corridas | En la base de datos de la plataforma | En un SQLite local del servidor del cliente |
| Cambios y soporte | Centralizados en la plataforma | Cada versión nueva del agente se copia al servidor del cliente y se vuelve a ejecutar `install.ps1` (conserva `agent.toml`) |

Los requisitos comunes no cambian: usuario HANA de solo lectura sobre los
tres esquemas (`../hana/`), puerto SQL del tenant comprobado
(`../hana/00_find_tenant_sql_port.sql`), acuerdo del partner SAP sobre la
lectura directa de HANA, y prueba previa en un servidor nuestro antes de
tocar el del cliente.

## Cuándo elegirlo

Cuando la TI del cliente no acepte un camino de red permanente hacia su red
aunque esté limitado a un puerto, o cuando exija que las credenciales de
HANA no salgan de su servidor. A cambio acepta ejecutar y actualizar
software nuestro en su servidor.
