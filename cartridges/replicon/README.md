# Cartucho `replicon`

Conector portable para Replicon PSA. Define extracción de entidades, datasets
silver/gold, dashboards, instrucciones específicas para el asistente y
DAGs de Airflow.

## Estructura

```
cartridges/replicon/
├── README.md                  · este archivo
├── Dockerfile                 · imagen del REST adapter del cartucho
├── requirements.txt
├── config/
│   └── seed.sql               · INSERT idempotente para auto-instalar el
│                                cartucho en una DB nueva. Se REGENERA con
│                                cada export del cartucho (estado actual).
├── app/                       · código del REST adapter
├── apps/                      · apps analíticas (HTML + metadata)
│   ├── pnl_revenue_manager.html
│   ├── consultor_horas_costos.html
│   ├── skill_gaps_heatmap.html
│   ├── match_consultor_por_skill.html
│   └── resource_availability.html
├── dags/                      · DAGs de Airflow del cartucho
│   ├── replicon_extract.py
│   └── replicon_ses_inbox_import.py
├── datasets/                  · SQL de datasets (silver / master / gold)
│   ├── replicon_user_latest.sql
│   ├── consultor_asignacion.sql
│   ├── pnl_mensual.sql
│   ├── pnl_detalle_consultor.sql
│   └── … (~25 más)
├── hints/
│   └── assistant.md           · instrucciones específicas que el asistente
│                                debe seguir cuando este cartucho está activo
├── ap_flows/                  · flujos de extracción API por entidad
└── (config/specs/ se generan al exportar)
```

## Estado del bootstrap automático

| Pieza                                | Estado      |
|--------------------------------------|-------------|
| Datasets (silver/gold)        | ✅ Vía `seed.sql` |
| Entities (`entity_config`)           | ✅ Vía `seed.sql` |
| Connections / KBs                    | ✅ Vía `seed.sql` |
| Custom DAGs (incl. `replicon_extract`, `ses_inbox`) | ✅ Vía `seed.sql` + `dags/*.py` |
| Apps analíticas                      | ✅ Vía `seed.sql` (analytic_apps) |
| RAG documents                        | ⚠️ Auto-rebuild de `_semantic_replicon` al re-materializar; documentos manuales (PDFs, manuales) requieren re-ingest tras restore |
| Hints específicos al asistente       | ✅ Archivo en repo y carga automática en `cartridges.assistant_hints` al arrancar Console |
| Auto-import on fresh deploy          | ❌ TODO: agregar a `start.sh` un step que detecte el cartucho y dispare `/studio/import` si no existe en la DB |

## Procedimiento manual de export / import (mientras se completa el bootstrap)

**Exportar el estado actual desde AWS** (UI):
1. Studio → Step 1 (Resumen del cartucho) → **Exportar ZIP**.
2. Guardar el ZIP en `cartridges/replicon/` y commitear.

**Importar a un cliente nuevo**:
1. Subir el ZIP vía Studio → **Importar cartucho**.
2. Verificar que `airflow dags list` muestre los DAGs del cartucho.
3. Re-materializar datasets desde Studio Step 4.
4. Rebuild semantic: `POST /rag/rebuild-semantic {"cartridge":"replicon"}`.

## Re-indexado del RAG

Cualquier cambio en schema de raw o en SQL de un dataset gold debe re-indexarse:

- **Manual desde Studio**: botón **↻ RAG** en cada entidad (Step 2) o dataset (Step 4).
- **Automático**: al `materialize` un dataset, el endpoint dispara
  `/rag/rebuild-semantic` para refrescar `_semantic_replicon`.
