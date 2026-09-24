-- 00_find_tenant_sql_port.sql
-- Localiza el puerto SQL del tenant de SAP HANA donde viven las empresas de
-- SAP Business One.
--
-- Dónde ejecutarlo: conectado a SYSTEMDB (no al tenant), con un usuario que
-- pueda leer el catálogo de la instancia (por ejemplo SYSTEM). Herramientas:
-- HANA Studio, HANA Cockpit (SQL Console) o hdbsql.
--
-- Por qué hace falta: la cadena de conexión que muestran el SLD y el cliente
-- de Business One tiene la forma <TENANT_DB>@<HANA_HOST>:3NN13, donde NN es
-- el número de instancia (30013 en la instancia 00). Ese puerto 3NN13 es el
-- puerto SQL de SYSTEMDB: el cliente de B1 entra por SYSTEMDB y HANA lo
-- redirige al tenant. El tenant tiene su propio puerto SQL (su indexserver),
-- normalmente 3NN15 para el primer tenant (30015 en la instancia 00); un
-- segundo o tercer tenant usa 3NN41, 3NN44, etc. Esta consulta lo muestra sin
-- adivinar.
--
-- La plataforma admite las dos rutas de entrada:
--   * SAP_B1_PORT=<TENANT_SQL_PORT> y SAP_B1_DATABASE vacío   (directo al tenant)
--   * SAP_B1_PORT=3NN13 y SAP_B1_DATABASE=<TENANT_DB>          (vía SYSTEMDB)
-- Anote DATABASE_NAME y SQL_PORT del resultado: son los valores que van en
-- config/env.sap_b1.template.

SELECT
    DATABASE_NAME,
    HOST,
    SERVICE_NAME,
    SQL_PORT,
    ACTIVE_STATUS
FROM SYS_DATABASES.M_SERVICES
WHERE SERVICE_NAME = 'indexserver'
  AND DATABASE_NAME <> 'SYSTEMDB'
ORDER BY DATABASE_NAME;

-- Comprobación cruzada (opcional), ya conectado al tenant: el nombre debe
-- coincidir con DATABASE_NAME de arriba.
SELECT DATABASE_NAME FROM SYS.M_DATABASE;
