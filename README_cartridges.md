# SAP Cartridge Family

Este repositorio contiene la familia de cartuchos de SAP y Replicon, endurecida con medidas de seguridad robustas, incluyendo:
- Eliminación de secretos hardcodeados (requiriendo variables de entorno).
- Inclusión del header \`X-Internal-Api-Key\` para asegurar puntos de conexión.
- Endurecimiento de DuckDB deshabilitando acceso a archivos externos (\`SET enable_external_access=false;\`).
- Usuarios no-root (appuser) en Docker.

## Cartuchos
- \`sap_hcm\` (8202)
- \`sap_successfactors\` (8203)
- \`sap_payroll\` (8204)
- \`sap_time\` (8205)
- \`sap_checkin\` (8206)
- \`sap_fi_co\` (8207)
- \`sap_analytics\` (8208)
- \`replicon\` (8201)

## Construcción y pruebas
Para ejecutar pruebas:
\`\`\`bash
docker build -t sap_hcm cartridges/sap_hcm/
docker run -p 8202:8202 --env-file .env sap_hcm
curl http://localhost:8202/health
curl -H "X-Internal-Api-Key: \$INTERNAL_API_KEY" http://localhost:8202/skills/entities
\`\`\`
