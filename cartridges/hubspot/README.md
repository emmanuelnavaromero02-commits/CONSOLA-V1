# Cartucho `hubspot`

Conector portable para HubSpot CRM. Define extracción de objetos (deals,
empresas, contactos, líneas de producto, vendedores, pipelines), datasets
silver/gold, instrucciones específicas para el asistente y DAGs de Airflow.

Construido siguiendo la **lógica del cartucho Replicon** (medallion,
config-en-datos, espina plan-vs-real, capa de agentes con guardarraíles
anti-alucinación) — **NO es un clon**: las entidades, la espina
(`pipeline_salud`) y los agentes son propios de CRM/ventas. Diferencias
deliberadas frente a Replicon:

- **Pattern A** (como SAP): DAG fino → microservicio del cartucho. La lógica de
  extracción (paginación cursor, watermark, parquet) vive en `app/`, no embebida
  en el DAG. Evita la duplicación de extracción que tiene `replicon_extract.py`.
- **`sources` bien declarados** en cada dataset, para que la espina **sí** entre
  a la auto-propagación de `dataset_refresh_chain` (grieta de Replicon corregida).
- **Reads estandarizados** a `…/data.parquet`; **3 capas limpias** sin etiquetas
  `(master)` muertas.

## Estructura

```
cartridges/hubspot/
├── README.md                  · este archivo
├── Dockerfile                 · imagen del REST adapter del cartucho (uvicorn :8210)
├── requirements.txt
├── config/
│   └── seed.sql               · INSERT idempotente: cartridge, dags, entity_config,
│                                semantic_terms, agents
├── app/                       · código del REST adapter (FastAPI + MCP)
│   ├── core/hubspot_client.py · cliente HubSpot CRM v3 (cursor + bearer)
│   ├── services/extraction_service.py · extracción → parquet bronze
│   └── config/entities.yaml   · catálogo de objetos (api_path, properties, watermark)
├── dags/
│   ├── hubspot_extract.py     · DAG fino → microservicio + trigger refresh_chain
│   └── hubspot_extract_all.py · fan-out a todas las entidades
├── datasets/                  · SQL de datasets (silver / gold)
│   ├── hubspot_*_latest.sql   · silver 1:1 (deals, companies, contacts, …)
│   ├── pipeline_salud.sql     · GOLD — la espina (plan vs. real)
│   ├── forecast_mensual.sql   · GOLD
│   ├── revenue_por_vendedor.sql
│   ├── conversion_por_etapa.sql
│   └── deals_estancados.sql
├── hints/
│   └── assistant.md           · instrucciones para el asistente cuando el cartucho está activo
├── apps/                      · apps analíticas (HTML + metadata)
└── ap_flows/                  · flujos de ingesta por entidad / todas
```

## Capas (medallion)

```
BRONZE  raw/hubspot/{entity}/load_date=.../batch_id=.../*.parquet   (inmutable)
SILVER  silver/hubspot/{name}/data.parquet                          (1:1, dedup a última foto)
GOLD    pggold.gold_{name}  +  gold/hubspot/{name}/data.parquet      (espina + agregados)
```

La espina `pipeline_salud` se materializa primero; `forecast_mensual`,
`revenue_por_vendedor`, `conversion_por_etapa` y `deals_estancados` la leen
(gold-sobre-gold) y se materializan después por el orden topológico de
`dataset_refresh_chain` (gracias a los `sources` declarados).

## Configuración (Vault / env)

| Variable             | Uso                                              |
|----------------------|--------------------------------------------------|
| `HUBSPOT_API_TOKEN`  | Private App token con scopes de lectura de CRM   |
| `HUBSPOT_BASE_URL`   | opcional (default `https://api.hubapi.com`)      |

Scopes mínimos del Private App: `crm.objects.deals.read`,
`crm.objects.companies.read`, `crm.objects.contacts.read`,
`crm.objects.line_items.read`, `crm.schemas.deals.read`.

## Preguntas que responde

| Pregunta | Cómo |
|----------|------|
| ¿Qué oportunidades cierran este mes? | `pipeline_salud` (estado=forecast, mes_cierre actual) |
| ¿Qué cliente/deal tiene riesgo de caerse? | agente `forecast_watchdog` |
| ¿Qué oportunidades no tienen actividad reciente? | `deals_estancados` / agente `stale_deal_chaser` |
| ¿Qué vendedores venden más? | `revenue_por_vendedor` |
| ¿Vendedores con peor **margen**? | revenue ✅; margen necesita costo (finanzas) → fase 2 |
| ¿Forecast vs. **capacidad operativa**? | forecast ✅; cruce con Replicon → fase 2 |
