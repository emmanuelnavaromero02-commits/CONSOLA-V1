"""Pure helpers for published app embeds.

The route handlers keep the runtime and permission checks; this module owns the
same-origin wrapper, CSP, and declared dataset extraction.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from app.domains.apps.payloads import DATASET_NAME_RE

# ``secrets.token_urlsafe`` alphabet. Validating the shape keeps a caller-supplied
# value from ever reaching the policy or the markup.
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,}")


def datasets_from_app_html(html_text: str) -> list[str]:
    return sorted(
        {
            dataset
            for dataset in re.findall(
                r"/api/data/([a-zA-Z_][a-zA-Z0-9_]*)(?=[/?#'\"\\s)]|$)",
                html_text or "",
            )
            if DATASET_NAME_RE.fullmatch(dataset)
        }
    )


_INLINE_SCRIPT_OPEN_RE = re.compile(
    r"<script(?![^>]*\bsrc\s*=)(?![^>]*\bnonce\s*=)([^>]*)>",
    re.IGNORECASE,
)


def inject_script_nonce(html_text: str, nonce: str) -> str:
    """Stamp a server-owned nonce on inline scripts only.

    Scripts carrying ``src`` keep their integrity/crossorigin contract untouched:
    a nonce on an external script would widen the policy for no benefit.
    """
    if not nonce or not _NONCE_RE.fullmatch(nonce):
        raise ValueError("invalid script nonce")
    escaped = html.escape(nonce, quote=True)
    return _INLINE_SCRIPT_OPEN_RE.sub(
        lambda match: f'<script nonce="{escaped}"{match.group(1)}>', html_text or ""
    )


def app_content_headers(nonce: str) -> dict[str, str]:
    """Headers for published app HTML.

    ``script-src`` carries a per-response nonce so the app's own inline bootstrap
    runs without ``unsafe-inline``. jsDelivr stays allow-listed because the
    charting runtime is loaded from it with an integrity hash.
    """
    if not nonce or not _NONCE_RE.fullmatch(nonce):
        raise ValueError("invalid script nonce")
    return {
        "Content-Security-Policy": (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
        "X-Frame-Options": "SAMEORIGIN",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
    }


def app_embed_wrapper_html(name: str, datasets_used: list[str], nonce: str) -> str:
    title = html.escape(name.replace("_", " ").strip() or "Analytic app")
    content_src = f"/apps/{quote(name, safe='')}/content"
    content_src_json = json.dumps(content_src)
    allowed_datasets_json = json.dumps(
        sorted(
            {
                str(dataset)
                for dataset in datasets_used
                if DATASET_NAME_RE.fullmatch(str(dataset))
            }
        )
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - OMEGA</title>
  <style>
    :root {{ color-scheme: dark light; }}
    html, body {{
      margin: 0;
      min-height: 100%;
      background: #07111e;
      color: #e2e8f0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .shell {{
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
      background:
        linear-gradient(180deg, rgba(14, 165, 233, 0.12), transparent 240px),
        #07111e;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid rgba(125, 211, 252, 0.18);
      background: rgba(8, 20, 35, 0.92);
    }}
    h1 {{
      margin: 0;
      font-size: 14px;
      letter-spacing: 0;
      font-weight: 700;
    }}
    p {{
      margin: 2px 0 0;
      font-size: 12px;
      color: #94a3b8;
    }}
    a {{
      color: #7dd3fc;
      font-size: 12px;
      font-weight: 700;
      text-decoration: none;
    }}
    .frame-wrap {{
      min-width: 0;
      min-height: 0;
      padding: 0;
    }}
    iframe {{
      display: block;
      width: 100%;
      min-height: 640px;
      height: calc(100vh - 58px);
      border: 0;
      /* Matches the wrapper shell: a white canvas here flashed full-screen
         while the app was still loading. */
      background: #07111e;
    }}
    .blocked {{
      display: none;
      padding: 18px;
      color: #fecaca;
      font-size: 13px;
      border-top: 1px solid rgba(248, 113, 113, 0.24);
      background: rgba(127, 29, 29, 0.22);
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>{title}</h1>
        <p>{len(datasets_used)} datasets autorizados para esta app</p>
      </div>
    </header>
    <div class="frame-wrap">
      <iframe id="omega-app-frame" title={json.dumps(title)} sandbox="allow-scripts" referrerpolicy="same-origin" src={json.dumps(content_src)}></iframe>
      <div id="blocked" class="blocked" role="alert"></div>
    </div>
  </div>
  <script nonce={json.dumps(nonce)}>
    (() => {{
      const frame = document.getElementById("omega-app-frame");
      const blocked = document.getElementById("blocked");
      const allowedSrc = {content_src_json};
      const allowedDatasets = new Set({allowed_datasets_json});
      const csrfToken = () => {{
        const match = document.cookie.match(/(?:^|;\\s*)csrf_token=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
      }};
      const showError = (message) => {{
        if (!blocked) return;
        blocked.style.display = "block";
        blocked.textContent = message;
      }};
      window.addEventListener("message", async (event) => {{
        if (!frame || event.source !== frame.contentWindow) return;
        const msg = event.data || {{}};
        if (msg.type !== "omega-app-fetch" || !msg.id) return;
        let status = 500;
        let ok = false;
        let body = "{{}}";
        let contentType = "application/json";
        try {{
          if (!String(frame.getAttribute("src") || "").endsWith(allowedSrc)) {{
            throw new Error("app source mismatch");
          }}
          const url = new URL(String(msg.url || ""), window.location.origin);
          const method = String(msg.method || "GET").toUpperCase();
          if (!url.pathname.startsWith("/api/data/")) throw new Error("blocked app data URL");
          if (!["GET", "POST"].includes(method)) throw new Error("blocked app data method");
          const parts = url.pathname.split("/").filter(Boolean);
          const dataset = parts.length >= 3 && parts[0] === "api" && parts[1] === "data" ? parts[2] : "";
          if (!allowedDatasets.has(dataset)) throw new Error(`dataset ${{dataset || "(empty)"}} not declared by app`);
          const headers = {{}};
          let requestBody;
          if (method === "POST") {{
            headers["Content-Type"] = "application/json";
            const csrf = csrfToken();
            if (csrf) headers["X-CSRF-Token"] = csrf;
            requestBody = typeof msg.body === "string" ? msg.body : null;
          }}
          const response = await fetch(url.pathname + url.search, {{
            method,
            headers,
            body: requestBody,
            credentials: "same-origin"
          }});
          status = response.status;
          ok = response.ok;
          contentType = response.headers.get("content-type") || "application/json";
          body = await response.text();
        }} catch (err) {{
          const message = err instanceof Error ? err.message : "blocked app data request";
          status = 403;
          ok = false;
          body = JSON.stringify({{ detail: message }});
          showError(message);
        }}
        frame.contentWindow.postMessage({{
          type: "omega-app-fetch-result",
          id: msg.id,
          ok,
          status,
          contentType,
          body
        }}, "*");
      }});
    }})();
  </script>
</body>
</html>"""


def app_embed_csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


def workspace_server_url(
    *,
    environ: Mapping[str, str] | None = None,
    docker_env_exists: Callable[[], bool] | None = None,
    is_production_env: Callable[[], bool] | None = None,
    logger_warning: Callable[[str], None] | None = None,
) -> str:
    # Respect an explicitly-passed empty mapping. `environ or os.environ` would
    # treat {} as falsy and leak the real process env, so a caller asking for
    # "no config" (e.g. the production-safety check) would still read WORKSPACE_*
    # from the ambient environment (green locally, red in the full stack).
    env = environ if environ is not None else os.environ
    raw = env.get("WORKSPACE_INTERNAL_URL") or env.get("WORKSPACE_BACKEND_URL")
    if raw:
        return raw.rstrip("/")
    public = env.get("WORKSPACE_PUBLIC_URL") or env.get("WORKSPACE_URL")
    in_docker = (
        docker_env_exists() if docker_env_exists else Path("/.dockerenv").exists()
    )
    if (
        in_docker
        and public
        and re.match(r"^https?://(localhost|127\.0\.0\.1)(:|/|$)", public)
    ):
        return "http://workspace:8001"
    if public:
        return public.rstrip("/")
    production = is_production_env() if is_production_env else False
    if production:
        if logger_warning:
            logger_warning("WORKSPACE_INTERNAL_URL is not configured in production")
        return ""
    return "http://localhost:8001"
