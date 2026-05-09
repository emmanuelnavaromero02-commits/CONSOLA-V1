1. **Paso 1: Infraestructura compartida**
    * He copiado los archivos base de `replicon` a `sap_hcm` y `sap_successfactors`.
    * He modificado los puertos en los `Dockerfiles`.
    * He reemplazado las referencias de "replicon" en los archivos copiados.
    * Voy a informar al usuario de los archivos creados.
2. **Paso 2: Cliente SAP y configuración (`sap_hcm`)**
    * Crear `app/core/config.py` con las variables de entorno necesarias.
    * Crear `app/core/sap_client.py` con la clase `SapHcmClient` y sus métodos requeridos (`get_entity_url`, `fetch_entity`, `list_tables`, `get_table_schema`).
    * Crear `app/config/connector.yaml` con la configuración especificada.
    * Crear `app/config/entities.yaml` con las 10 entidades y campos correctos.
    * Crear `app/config/knowledge_bits.yaml` con los 5 KBs solicitados y queries en DuckDB usando CTEs y `DISTINCT ON (id) ORDER BY id, _extracted_at DESC`.
    * Crear `app/services/extraction_service.py` basado en el original, actualizando las referencias del cliente SAP.
    * Crear `app/api/routes_skills.py` copiando el original pero importando `run_entity` desde `extraction_service`.
    * Crear `app/mcp_server.py` basado en el original, actualizando el nombre a "sap_hcm" y las herramientas.
    * Crear `app/main.py` copiando de replicon y actualizando el título.
    * Crear `dags/sap_hcm_extract.py` y `dags/sap_hcm_extract_all.py` (usando `CARTRIDGE_URL = "http://sap_hcm:8202"`).
    * Crear `ap_flows/ingest_bi_all.json` y `ap_flows/ingest_bi_entity.json` actualizando los nombres y URLs.
3. **Paso 3: Cliente SAP y configuración (`sap_successfactors`)**
    * Crear `app/core/config.py` con variables de SF.
    * Crear `app/core/sap_client.py` con `SapSfClient` implementando autenticación OAuth2 y llamadas OData v2.
    * Crear `app/config/connector.yaml` con `auth.type: oauth2_client_credentials` y demás especificaciones.
    * Crear `app/config/entities.yaml` con las 15 entidades SF y OData entities correctas.
    * Crear `app/config/knowledge_bits.yaml` con los 6 KBs DuckDB requeridos.
    * Adaptar los servicios `extraction_service.py`, `routes_skills.py`, `mcp_server.py`, `main.py`, `dags/` y `ap_flows/` para `sap_successfactors` (puerto 8203).
4. **Paso 4: Pre commit steps**
    * Ejecutar instrucciones pre-commit para confirmar que no haya problemas de linting ni fallos de pruebas o convenciones.
5. **Paso 5: Submit**
    * Finalizar los cambios y hacer el commit.
