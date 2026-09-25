from __future__ import annotations

import html
import uuid
from typing import Any

from fastapi.responses import HTMLResponse


def status_page_title(status_code: int) -> str:
    if status_code == 403:
        return "No tienes acceso a esta sección"
    if status_code == 404:
        return "Página no disponible"
    if status_code == 503:
        return "Fuente temporalmente no disponible"
    return "No se pudo abrir esta sección"


def status_page_body(status_code: int, detail: object) -> str:
    if status_code == 403:
        return "Tu usuario no tiene los permisos necesarios para abrir esta pantalla."
    if status_code == 404:
        return "La pantalla o recurso solicitado no está materializado en este entorno."
    if status_code == 503:
        return "La fuente necesaria no respondió a tiempo. Intenta de nuevo en unos minutos."
    return str(detail or "La solicitud no pudo completarse.")


def functional_status_page(status_code: int, detail: object) -> HTMLResponse:
    title = html.escape(status_page_title(status_code))
    body = html.escape(status_page_body(status_code, detail))
    return HTMLResponse(
        f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #0f172a; }}
    main {{ min-height: 100vh; display: grid; place-items: center; padding: 24px; }}
    section {{ max-width: 560px; border: 1px solid #cbd5e1; background: white; padding: 28px; border-radius: 8px; box-shadow: 0 10px 30px rgba(15, 23, 42, .08); }}
    h1 {{ margin: 0 0 12px; font-size: 24px; line-height: 1.2; }}
    p {{ margin: 0 0 18px; color: #475569; }}
    a {{ display: inline-flex; min-height: 40px; align-items: center; border: 1px solid #0f172a; border-radius: 6px; padding: 0 14px; color: #0f172a; text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <main>
    <section role="alert">
      <h1>{title}</h1>
      <p>{body}</p>
      <a href="/my-access">Ver mis accesos</a>
    </section>
  </main>
</body>
</html>""",
        status_code=status_code,
    )


def internal_error_request_id(request: Any | None = None) -> str:
    candidate = getattr(getattr(request, "state", None), "request_id", None)
    try:
        return str(uuid.UUID(str(candidate)))
    except Exception:
        return str(uuid.uuid4())

