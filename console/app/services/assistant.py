"""
Assistant — orchestrates MCP tools via the configured LLM.
Job management (extract, get_job_status, list_jobs) is the cartridge's responsibility.
"""
from __future__ import annotations

import os
import time
import httpx

from app.services import mcp_registry, llm_client

SYSTEM_BASE = """Eres el asistente de MODecissionsPaaS, una plataforma de decisiones empresariales.
Tienes acceso a herramientas MCP registradas en la plataforma.

## REGLA PRINCIPAL — Visores interactivos

El servidor "Monitoring & Deeplinks" tiene herramientas view_* que generan URLs de visores ricos.
DEBES usar esas herramientas en lugar de procesar datos directamente cuando el usuario pida:

- Ver un job, sus logs, su progreso → usa monitoring__view_job
- Ver todos los jobs → usa monitoring__view_jobs
- Ver schema, columnas, tipos, preview de una fuente Bronze → usa monitoring__view_schema
- Ver un dataset (SQL, lineage, preview, términos de negocio) → usa monitoring__view_dataset
- Ver todos los datasets → usa monitoring__view_datasets
- Ver entidades, campos, watermarks, modelo semántico → usa monitoring__view_semantic
- Ver el pipeline completo (estado Bronze/Silver/Gold, extraer entidades, freshness) → usa monitoring__view_pipeline

Cuando recibas la URL de un visor, preséntala como un enlace Markdown clickeable:
  [Ver job abc123 →](http://...)
NO muestres el JSON crudo. NO llames a preview_source, get_schema ni get_run_logs
a menos que el usuario pida explícitamente los datos en el chat.

## Jobs asíncronos

Algunas herramientas son batch: regresan inmediatamente con un job_id.
Tras lanzar un job, llama a monitoring__view_job con ese job_id para generar el deeplink
y preséntalo al usuario para que siga el progreso en el visor.

## Aplicaciones analíticas (Apps)

Cuando el usuario pida un dashboard, reporte o visualización interactiva:

1. Genera HTML completo y auto-contenido que:
   - Obtiene datos con fetch('/api/data/{dataset}') — devuelve un JSON array de rows
   - Usa Chart.js desde CDN: https://cdn.jsdelivr.net/npm/chart.js
   - Tiene estilos embebidos con tema oscuro (fondo #0d1117, verde #39ff14 o azul #7c9fff)
   - Es completamente funcional sin backend adicional

2. ESTRUCTURA recomendada para dashboards P&L:
   - Header con título y selector de Revenue Manager (o "Todos")
   - Sección KPIs: tarjetas con revenue, costo, margen (números grandes, color según umbral)
   - Tabla cronológica: meses en columnas, métricas en filas (Revenue, Facturación, WIP, Costo Directo, Costo Hundido, Costo Total, Margen Bruto USD, Margen Bruto %, Horas Facturables, Horas Totales, % Avance Real)
   - Sección desglose por proyecto: tabla con proyecto en filas, mismas métricas en columnas

3. MÉTRICAS con formato:
   - USD → $XX,XXX.xx
   - Porcentajes → XX.xx%
   - Horas → XX,XXX.x h
   - Color condicional: margen > 20% = verde, 10-20% = amarillo, < 10% = rojo

4. Tras generar el HTML, llama a refinement__publish_app(name, title, html, description)
   - name: slug snake_case, e.g. "pnl_revenue_manager"
   - Devuelve {"url": "/apps/{name}"} → preséntalo como enlace clickeable al usuario

5. Para listar apps existentes: refinement__list_apps()

Responde siempre en el idioma del usuario."""


# ── Data Catalog cache ────────────────────────────────────────────────────────

_CATALOG_TTL   = int(os.environ.get("CATALOG_TTL_SECONDS", "3600"))  # 1 hour default
_catalog_text  : str   = ""
_catalog_ts    : float = 0.0

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")


def _format_catalog(data: dict) -> str:
    """Render data_catalog as a compact context block for the system prompt."""
    lines = ["## Modelo de datos — Data Catalog\n"]
    lines.append("Usa este catálogo para generar SQL sin necesitar tool calls adicionales.")
    lines.append("Paths Parquet silver: s3://lakehouse/silver/{cartridge}/{dataset}/data.parquet")
    lines.append("Tablas Gold/Master en Postgres analítico (alias DuckDB pggold): gold_{dataset}, master_{dataset}\n")

    datasets = data.get("datasets", {})
    for ds_name, ds in datasets.items():
        layer     = ds.get("layer", "")
        cartridge = ds.get("cartridge", "")
        desc      = ds.get("description", "")
        header    = f"### {ds_name}  [{layer}]"
        if desc:
            header += f"\n{desc[:120]}"
        lines.append(header)

        cols = ds.get("columns", [])
        key_cols = [c for c in cols if c.get("is_key")]
        met_cols = [c for c in cols if c.get("is_metric")]

        for col in cols:
            flags = ""
            if col.get("is_key"):    flags += " [KEY]"
            if col.get("is_metric"): flags += " [MTR]"
            col_desc = col.get("description", "")
            tags = ",".join(col.get("tags") or [])
            tag_str = f" ({tags})" if tags else ""
            lines.append(f"  {col['name']} {col.get('type','')+flags}: {col_desc}{tag_str}")
        lines.append("")

    rels = data.get("relationships", [])
    if rels:
        lines.append("## Relaciones (JOINs)\n")
        for r in rels:
            t = f" — transform: {r['transform']}" if r.get("transform") else ""
            lines.append(
                f"  {r['from_dataset']}.{r['from_column']} →{r.get('join_hint','LEFT')} JOIN→ "
                f"{r['to_dataset']}.{r['to_column']}: {r.get('description','')}{t}"
            )
        lines.append("")

    return "\n".join(lines)


async def _get_catalog_context() -> str:
    """Return cached catalog context, refreshing if stale."""
    global _catalog_text, _catalog_ts

    if _catalog_text and (time.time() - _catalog_ts) < _CATALOG_TTL:
        return _catalog_text

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{REFINEMENT_URL}/mcp/invoke",
                json={"tool": "get_data_catalog", "args": {}},
            )
            r.raise_for_status()
            _catalog_text = _format_catalog(r.json())
            _catalog_ts   = time.time()
    except Exception:
        # On failure keep stale cache (or empty if first load)
        pass

    return _catalog_text


# ── Main chat handler ─────────────────────────────────────────────────────────

async def chat(message: str, history: list[dict]) -> dict:
    catalog_ctx = await _get_catalog_context()
    system = SYSTEM_BASE + ("\n\n" + catalog_ctx if catalog_ctx else "")

    servers = await mcp_registry.list_servers()
    tools: list[dict] = []
    tool_server_map: dict[str, str] = {}

    for server in servers:
        if not server.get("healthy"):
            continue
        for t in (server.get("tools") or []):
            full_name = f"{server['id']}__{t['name']}"
            tools.append({
                "name":         full_name,
                "description":  f"[{server['name']}] {t.get('description', '')}",
                "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
            })
            tool_server_map[full_name] = server["id"]

    messages = list(history)
    messages.append({"role": "user", "content": message})

    reply, viewer_urls, full_msgs = await llm_client.chat(
        system=system,
        messages=messages,
        tools=tools,
        invoke_tool=lambda srv, tool, a: mcp_registry.invoke(srv, tool, a),
        tool_server_map=tool_server_map,
    )

    return {"reply": reply, "viewer_urls": viewer_urls, "messages": full_msgs}
