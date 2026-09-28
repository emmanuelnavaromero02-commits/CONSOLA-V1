from __future__ import annotations

import json
from typing import Any


APP_HTML_STYLE_RULES = """\
REGLAS DE ESTILO (apps HTML):
- Al servir cada app, la plataforma inyecta el tema (variables, Inter y estilos base) y
  theme-switch.js, que pone data-theme="light" o "dark" en <html>. NO agregues botón de
  tema propio ni leas localStorage para el tema.
- Colores SOLO con variables: fondo var(--bg); tarjetas var(--card) (alias var(--bg2));
  superficie secundaria var(--bg3); bordes var(--border) y var(--border-strong); texto
  var(--text-primary) (alias var(--text1)), var(--text-secondary) (alias var(--text2)) y
  var(--text-muted) (alias var(--text3)); acento var(--primary), var(--primary-hover) y
  var(--primary-soft); fondo de botón con texto var(--on-primary): var(--primary-strong) y
  var(--primary-strong-hover); estados var(--green), var(--amber), var(--red), var(--blue),
  var(--purple). PROHIBIDO fijar #fff, #000, white, black u otro hex en fondos, textos o bordes.
- color-scheme lo declara la app (la plataforma no lo fija): :root { color-scheme: light } y
  :root[data-theme="dark"] { color-scheme: dark }; da color explícito (var(--text-primary)) a
  body y a cada panel con fondo propio. Nunca prefers-color-scheme ni clases .dark.
- Tipografía: no declares font-family en body (la plataforma aplica Inter, var(--font-sans));
  cifras con font-variant-numeric: tabular-nums; código con var(--font-mono). En Chart.js usa
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily.
- Componentes listos: class="omega-card", "omega-table", "omega-btn" y "omega-btn-secondary".
  El foco visible y las barras de desplazamiento ya vienen con el tema.
- Gráficas: lee colores con getComputedStyle(document.documentElement).getPropertyValue('--text2').trim()
  y redibuja al cambiar data-theme (MutationObserver sobre <html>).
- No cargues fuentes ni hojas de estilo externas; no uses @import ni @layer.
"""


APP_HTML_CONTRACT_RULES = """\
CONTRATO DE LA APP:
- Devuelve UN documento HTML completo y auto-contenido (<style> y <script> inline).
- Datos SOLO con fetch('/api/data/<dataset>') sobre los datasets autorizados listados abajo.
- PROHIBIDO cargar scripts externos (<script src=...>), iframes o recursos de otros origenes.
- No inventes cifras ni etiquetas: todo valor mostrado sale de los datos devueltos por la API.
  Si un campo falta o la consulta no devuelve filas, muestra "Sin información".
- Maneja estados de carga y de error de forma visible y honesta.
- Responde SOLO con el HTML, sin markdown, sin explicaciones antes o después.
"""


def build_app_html_system_prompt() -> str:
    return (
        "Eres el generador de aplicaciones analíticas HTML de OMEGA.\n\n"
        + APP_HTML_CONTRACT_RULES
        + "\n"
        + APP_HTML_STYLE_RULES
    )


def build_app_html_user_prompt(
    *,
    title: str,
    description: str,
    objective: str,
    dataset_schemas: dict[str, Any],
) -> str:
    schemas = json.dumps(dataset_schemas, ensure_ascii=False, sort_keys=True, default=str)
    return (
        f"Título de la app: {title}\n"
        f"Descripción: {description or 'Sin información'}\n"
        f"Objetivo de negocio: {objective}\n\n"
        "Datasets autorizados y sus esquemas reales (no uses otros):\n"
        f"{schemas}\n\n"
        "Genera ahora el HTML completo de la app."
    )
