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
    if not nonce or not _NONCE_RE.fullmatch(nonce):
        raise ValueError("invalid script nonce")
    escaped = html.escape(nonce, quote=True)
    return _INLINE_SCRIPT_OPEN_RE.sub(
        lambda match: f'<script nonce="{escaped}"{match.group(1)}>', html_text or ""
    )


def app_content_headers(nonce: str) -> dict[str, str]:
    if not nonce or not _NONCE_RE.fullmatch(nonce):
        raise ValueError("invalid script nonce")
    return {
        "Content-Security-Policy": (
            "sandbox allow-scripts; "
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'none'; "
            "object-src 'none'; "
            "frame-ancestors 'self'; "
            "base-uri 'none'; "
            "form-action 'none'"
        ),
        "X-Frame-Options": "SAMEORIGIN",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store, no-cache, must-revalidate, private",
        "Pragma": "no-cache",
    }


_HEAD_OPEN_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HTML_OPEN_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)

APP_BRIDGE_MAX_BODY = 64 * 1024

APP_DATA_SUBPATHS = ("options", "query")


def app_bridge_script(nonce: str) -> str:
    if not nonce or not _NONCE_RE.fullmatch(nonce):
        raise ValueError("invalid script nonce")
    nonce_attr = html.escape(nonce, quote=True)
    return f"""<script nonce="{nonce_attr}">
(() => {{
  "use strict";
  const pending = new Map();
  let seq = 0;
  const MAX_BODY = {APP_BRIDGE_MAX_BODY};
  const SUBPATHS = new Set({json.dumps(sorted(APP_DATA_SUBPATHS))});
  const deny = (name) => {{
    const blocked = function omegaBlocked() {{
      throw new Error(
        name + " is not available to published apps; use fetch('/api/data/<dataset>')"
      );
    }};
    try {{
      Object.defineProperty(window, name, {{
        value: blocked,
        writable: false,
        configurable: false
      }});
    }} catch (err) {{ /* already locked */ }}
  }};
  // The network is closed by connect-src 'none'; these make it legible.
  ["XMLHttpRequest", "WebSocket", "EventSource"].forEach(deny);

  // An opaque origin has no storage, and merely *reading* window.localStorage
  // throws there. Two packaged apps read a saved theme during bootstrap, so
  // that throw aborted their whole inline script before it loaded any data.
  // Hand them a frame-local, in-memory Storage instead: same API, nothing
  // shared with the shell, nothing that outlives the frame. This restores the
  // apps without giving back a single byte of the storage the sandbox removed.
  const localStore = () => {{
    const map = new Map();
    return {{
      getItem: (k) => (map.has(String(k)) ? map.get(String(k)) : null),
      setItem: (k, v) => {{ map.set(String(k), String(v)); }},
      removeItem: (k) => {{ map.delete(String(k)); }},
      clear: () => map.clear(),
      key: (i) => Array.from(map.keys())[i] ?? null,
      get length() {{ return map.size; }}
    }};
  }};
  ["localStorage", "sessionStorage"].forEach((name) => {{
    let unavailable = false;
    try {{ void window[name]; }} catch (err) {{ unavailable = true; }}
    if (!unavailable) return;
    try {{
      Object.defineProperty(window, name, {{
        value: localStore(),
        writable: false,
        configurable: false
      }});
    }} catch (err) {{ /* leave the native throw in place */ }}
  }});
  try {{
    Object.defineProperty(navigator, "sendBeacon", {{
      value: function omegaBlockedBeacon() {{ return false; }},
      writable: false,
      configurable: false
    }});
  }} catch (err) {{ /* already locked */ }}

  const brokered = function omegaBrokeredFetch(input, init) {{
    const opts = init || {{}};
    const rawUrl = typeof input === "string"
      ? input
      : (input && typeof input.url === "string" ? input.url : "");
    const method = String(opts.method || "GET").toUpperCase();
    let parsed;
    try {{
      parsed = new URL(String(rawUrl), window.location.href);
    }} catch (err) {{
      return Promise.reject(new Error("Published apps must use a relative /api/data/ URL"));
    }}
    if (parsed.origin !== window.location.origin) {{
      return Promise.reject(new Error("Published apps cannot reach other origins"));
    }}
    // Coarse shape check only — the wrapper is the authority and re-validates
    // everything, including the declared-dataset allowlist it alone holds.
    const parts = parsed.pathname.split("/").filter(Boolean);
    const shapeOk =
      parts[0] === "api" && parts[1] === "data" &&
      (parts.length === 3 || (parts.length === 4 && SUBPATHS.has(parts[3])));
    if (!shapeOk) {{
      return Promise.reject(new Error("Published apps can only call /api/data/<dataset>[/options|/query]"));
    }}
    if (!["GET", "POST"].includes(method)) {{
      return Promise.reject(new Error("Published app data bridge only allows GET/POST"));
    }}
    const body = typeof opts.body === "string" ? opts.body : null;
    if (body !== null && body.length > MAX_BODY) {{
      return Promise.reject(new Error("Published app data request body is too large"));
    }}
    const id = "appfetch:" + (++seq);
    return new Promise((resolve, reject) => {{
      pending.set(id, {{ resolve, reject }});
      window.parent.postMessage({{
        type: "omega-app-fetch",
        id,
        method,
        url: parsed.pathname + parsed.search,
        body
      }}, "*");
      window.setTimeout(() => {{
        const item = pending.get(id);
        if (!item) return;
        pending.delete(id);
        item.reject(new Error("Published app data request timed out"));
      }}, 30000);
    }});
  }};
  try {{
    Object.defineProperty(window, "fetch", {{
      value: brokered,
      writable: false,
      configurable: false
    }});
  }} catch (err) {{
    window.fetch = brokered;
  }}

  window.addEventListener("message", (event) => {{
    if (event.source !== window.parent) return;
    const msg = event.data || {{}};
    if (msg.type !== "omega-app-fetch-result" || !pending.has(msg.id)) return;
    const item = pending.get(msg.id);
    pending.delete(msg.id);
    const headers = new Headers({{
      "Content-Type": typeof msg.contentType === "string" ? msg.contentType : "application/json"
    }});
    item.resolve(new Response(typeof msg.body === "string" ? msg.body : "", {{
      status: Number(msg.status) || 500,
      statusText: msg.ok ? "OK" : "ERROR",
      headers
    }}));
  }});
}})();
</script>"""


def inject_app_bridge(html_text: str, nonce: str) -> str:
    bridge = app_bridge_script(nonce)
    text = html_text or ""
    for pattern in (_HEAD_OPEN_RE, _HTML_OPEN_RE):
        match = pattern.search(text)
        if match:
            return text[: match.end()] + bridge + text[match.end() :]
    return bridge + text


def app_embed_wrapper_html(
    name: str,
    datasets_used: list[str],
    nonce: str,
    *,
    capability: str | None = None,
) -> str:
    title = html.escape(name.replace("_", " ").strip() or "Analytic app")
    content_src = f"/apps/{quote(name, safe='')}/content"
    if capability:
        content_src = f"{content_src}?cap={quote(capability, safe='')}"
    content_src_json = json.dumps(content_src)
    app_name_json = json.dumps(str(name))
    DATA_SUBPATHS_JSON = json.dumps(sorted(APP_DATA_SUBPATHS))
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
      <!-- credentialless: extra depth on browsers that support it — the frame
           gets an ephemeral, cookie-less storage partition. Unknown attributes
           are ignored elsewhere, so this never becomes a compatibility break,
           and it is never the control: sandbox, the CSP and the capability are. -->
      <iframe id="omega-app-frame" title={json.dumps(title)} sandbox="allow-scripts" credentialless referrerpolicy="no-referrer" src={json.dumps(content_src)}></iframe>
      <div id="blocked" class="blocked" role="alert"></div>
    </div>
  </div>
  <script nonce={json.dumps(nonce)}>
    (() => {{
      const frame = document.getElementById("omega-app-frame");
      const blocked = document.getElementById("blocked");
      const allowedSrc = {content_src_json};
      const appName = {app_name_json};
      // Straight from the durable grant ledger for this tenant, workspace, app
      // and manifest digest. Nothing scraped from the app's HTML or metadata
      // reaches this set.
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
      const MAX_BODY = {APP_BRIDGE_MAX_BODY};
      const ID_RE = /^appfetch:[0-9]{{1,9}}$/;
      const DATASET_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
      // Mirrors the data API exactly: /api/data/{{dataset}},
      // /api/data/{{dataset}}/options and /api/data/{{dataset}}/query.
      const DATA_SUBPATHS = new Set({DATA_SUBPATHS_JSON});
      window.addEventListener("message", async (event) => {{
        // The frame is the only party this wrapper answers. Anything else on
        // the page — including the shell above us — is ignored outright.
        if (!frame || event.source !== frame.contentWindow) return;
        const msg = event.data || {{}};
        if (msg.type !== "omega-app-fetch") return;
        const id = typeof msg.id === "string" ? msg.id : "";
        if (!ID_RE.test(id)) return;
        let status = 500;
        let ok = false;
        let body = "{{}}";
        let contentType = "application/json";
        try {{
          if (!String(frame.getAttribute("src") || "").endsWith(allowedSrc)) {{
            throw new Error("app source mismatch");
          }}
          // Relative only. An absolute URL is refused rather than normalised,
          // so a request aimed at another origin is never quietly rewritten
          // into a same-origin one.
          const raw = String(msg.url || "");
          if (!raw.startsWith("/") || raw.startsWith("//")) throw new Error("app data URL must be relative");
          const url = new URL(raw, window.location.origin);
          const method = String(msg.method || "GET").toUpperCase();
          if (!["GET", "POST"].includes(method)) throw new Error("blocked app data method");
          // /api/data/<dataset> plus the two sub-resources the data API
          // actually exposes — nothing else. The tail is an allowlist, not a
          // prefix match, so a deeper path or a traversal segment is refused
          // rather than forwarded.
          const parts = url.pathname.split("/").filter(Boolean);
          const shapeOk =
            parts[0] === "api" &&
            parts[1] === "data" &&
            (parts.length === 3 ||
              (parts.length === 4 && DATA_SUBPATHS.has(parts[3])));
          if (!shapeOk) throw new Error("blocked app data URL");
          const dataset = parts[2];
          if (!DATASET_RE.test(dataset)) throw new Error("blocked app dataset name");
          if (!allowedDatasets.has(dataset)) throw new Error(`dataset ${{dataset || "(empty)"}} not declared by app`);
          // Headers are built here, never forwarded from the app: the app must
          // not be able to set Authorization, Cookie or a CSRF value of its own.
          const headers = {{}};
          let requestBody;
          if (method === "POST") {{
            requestBody = typeof msg.body === "string" ? msg.body : null;
            if (requestBody !== null && requestBody.length > MAX_BODY) {{
              throw new Error("app data request body too large");
            }}
            headers["Content-Type"] = "application/json";
            const csrf = csrfToken();
            if (csrf) headers["X-CSRF-Token"] = csrf;
          }}
          // Translate to the app-scoped route. The app asks for
          // /api/data/<dataset>; the server-owned app name is spliced in here,
          // never taken from the message, and the backend re-checks the grant.
          // The wrapper's filtering is convenience — that route is the
          // authority, and it refuses anything this app was not granted.
          const scoped = "/api/apps/" + encodeURIComponent(appName)
            + "/data/" + encodeURIComponent(dataset)
            + (parts.length === 4 ? "/" + parts[3] : "");
          const response = await fetch(scoped + url.search, {{
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
          // The operator sees the reason; the app gets a fixed string. A
          // rejection message can name a dataset the caller may not know
          // exists, which turns a refusal into an enumeration primitive.
          const message = err instanceof Error ? err.message : "blocked app data request";
          status = 403;
          ok = false;
          body = JSON.stringify({{ detail: "request blocked by the app data broker" }});
          showError(message);
        }}
        frame.contentWindow.postMessage({{
          type: "omega-app-fetch-result",
          id,
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
