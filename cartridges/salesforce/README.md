# Salesforce Sales Cloud — MODecissions Cartridge

Extrae datos de **Salesforce Sales Cloud** vía SOQL (REST Query API) y los
modela en medallion (raw → silver → gold) para responder preguntas de pipeline,
forecast, riesgo de deals, velocidad por etapa, margen y cobertura de cuota.

## Patrón

`dag-based`, Pattern A (igual que Replicon / SAP). El microservicio FastAPI
expone `/skills/*` y un servidor MCP en `/mcp`; Airflow dispara la extracción
llamando a `/entities/{entity}/extract`. Puerto del servicio: **8205**.

## Autenticación

OAuth2 contra Salesforce. Por defecto el flujo *username-password* (server a
server); también soporta *client_credentials* y *bearer token* estático, según
el `auth_method` de la conexión en el Vault. Secrets:

| Vault key            | Descripción                                  |
|----------------------|----------------------------------------------|
| `SF_BASE_URL`        | Instance URL del org (`https://…my.salesforce.com`) |
| `SF_TOKEN_URL`       | `https://login.salesforce.com/services/oauth2/token` |
| `SF_CLIENT_ID`       | Consumer Key de la Connected App             |
| `SF_CLIENT_SECRET`   | Consumer Secret de la Connected App          |
| `SF_USERNAME`        | Usuario de integración (flujo password)      |
| `SF_PASSWORD`        | Password del usuario                          |
| `SF_SECURITY_TOKEN`  | Security token (se concatena al password)    |

Sin credenciales el cliente entra en modo **degradado**: rehúsa extraer y nunca
inventa datos.

## Entidades

Sales Cloud core (15): `Account`, `Contact`, `Lead`, `Opportunity`,
`OpportunityLineItem`, `OpportunityHistory`, `OpportunityContactRole`, `User`,
`Product2`, `PricebookEntry`, `Campaign`, `CampaignMember`, `Task`, `Event`.
Incremental por `SystemModstamp` (historial por `CreatedDate`); datos de
referencia en `full`. PII de `Contact`/`Lead` `masked`/`shadowed` en bronze.

## Gold y apps

| Gold dataset                        | Pregunta                                   |
|-------------------------------------|--------------------------------------------|
| `salesforce_pipeline_forecast`      | ¿Qué vamos a cerrar este mes/trimestre?    |
| `salesforce_deals_en_riesgo`        | ¿Qué oportunidades se van a caer?          |
| `salesforce_velocidad_pipeline`     | ¿Cuánto tarda cada etapa?                  |
| `salesforce_vendedor_margen`        | ¿Quién vende mucho con peor margen?        |
| `salesforce_cobertura_cuota`        | ¿Cómo va cada vendedor vs su objetivo?     |
| `salesforce_forecast_vs_capacidad`  | ¿El forecast cuadra contra capacidad? (cruza con Replicon) |

Apps funcionales (`fetch('/api/data/<dataset>')`): `salesforce_pipeline_forecast`,
`salesforce_deals_en_riesgo`, `salesforce_velocidad_pipeline`,
`salesforce_vendedor_margen`.

## Agentes

`El Pronosticador` (forecast), `Centinela de Deals` (riesgo), `Vigía de Cuota`
(attainment), `Analista de Margen` (descuento/margen), `Enlace Operativo`
(cross-cartucho forecast vs capacidad Replicon).
