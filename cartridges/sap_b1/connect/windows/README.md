# Alternativa: conector Windows en el servidor del cliente

Resumen de la segunda opción para traer los datos de SAP Business One, por
si la TI del cliente prefiere no mantener un túnel permanente. Se describe
aquí a alto nivel; la implementación vive en `../windows-agent/`, que otro
ingeniero construye en paralelo (este directorio no la contiene).

## Qué es

Un servicio de Windows nuestro, instalado en el servidor Windows del cliente
con una cuenta de servicio dedicada, que:

1. lee HANA **localmente**, en la red del cliente, con el mismo usuario de
   solo lectura de `../hana/01_create_readonly_user.sql` y las mismas 45
   tablas y reglas incrementales del cartucho;
2. escribe los datos en archivos Parquet (por empresa, entidad y periodo)
   con manifiesto y sumas de verificación;
3. los envía por **HTTPS (443) de salida** a nuestro almacén con credenciales
   de escritura limitadas a una carpeta, y manda un latido de salud;
4. la plataforma ingiere lo que aterriza (valida el manifiesto y después
   corre silver, gold y los KPIs, igual que con el túnel).

## En qué se diferencia del túnel (`../vpn/`)

| | Túnel WireGuard (recomendado) | Conector Windows |
|---|---|---|
| Software en el servidor del cliente | Cliente WireGuard oficial | Servicio nuestro, firmado, con actualizaciones firmadas |
| Red del cliente | Una salida UDP; sin entradas | Solo salidas HTTPS 443; sin túnel ni entradas |
| Credenciales de HANA | En nuestro vault, cifradas, revocables por el cliente | **Nunca salen** del servidor del cliente |
| Dónde corre la extracción | En la plataforma (bajo demanda desde la consola) | En el servidor del cliente (la programación vive en el servicio) |
| Cambios y soporte | Centralizados en la plataforma | Cada cambio es una actualización en el servidor del cliente |

Los requisitos comunes no cambian: usuario HANA de solo lectura sobre los
tres esquemas (`../hana/`), puerto SQL del tenant confirmado
(`../hana/00_find_tenant_sql_port.sql`), confirmación del partner SAP sobre
la lectura directa de HANA, y prueba previa en un servidor nuestro antes de
tocar el del cliente.

## Cuándo elegirlo

Cuando la TI del cliente no acepte un camino de red permanente hacia su red
aunque esté limitado a un puerto, o cuando exija que las credenciales de
HANA no salgan de su servidor. A cambio acepta ejecutar y actualizar
software nuestro en su servidor.
