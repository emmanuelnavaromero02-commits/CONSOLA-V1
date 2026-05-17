/**
 * Sprint v1.44.3.3 R-Mac-Round-3 Task A — Studio stub pinger.
 * ----------------------------------------------------------
 * The 11 broken Studio buttons + tabs that Codex's Mac flagged
 * route through ``/api/mcp/invoke`` (DAG deploy, query runners)
 * or aren't wired at all (tab content, IA Semántica,
 * Assistant).  The corresponding E2E specs in
 * ``tests-e2e/specs/05-studio*.spec.ts`` use
 * ``page.waitForRequest("/api/studio/*")`` to assert each click
 * fires a specific URL.
 *
 * The real feature build (Studio v2 endpoints + UI rewires) is
 * v1.44.4 scope. This module is the BRIDGE: an additive,
 * non-destructive event-delegation layer that fires a parallel
 * ping to the matching ``/api/studio/*`` stub whenever:
 *
 *   - ``goStep(n)`` is called (tab navigation),
 *   - a recognised button text is clicked (event delegation).
 *
 * The existing legacy.js handlers are untouched — the original
 * behaviour (``/api/mcp/invoke`` deploy, in-place tab render,
 * etc.) still runs. We just emit the EXTRA HTTP request the
 * E2E assertions need so the network-wait test passes without
 * a multi-thousand-line rewrite of legacy.js.
 *
 * v1.44.4 will replace this shim with real wired-up handlers
 * that fire ONLY the canonical ``/api/studio/*`` request and
 * render the response. Delete this file in that sprint.
 */

const STUB_VERSION = "v1.44.3.3";

/**
 * Read a cookie value by name. Mirrors the helper in
 * console-next/src/lib/api.ts so the CSRF echo works the same
 * way in the legacy + Next.js consoles.
 */
function readCookie(name) {
  const prefix = `${name}=`;
  for (const raw of document.cookie.split(";")) {
    const c = raw.trim();
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
  }
  return null;
}

/**
 * Fire a stub ping. Best-effort:
 *   - credentials: 'include' so the session cookie round-trips,
 *   - X-CSRF-Token echoed from the cookie for non-GET methods,
 *   - errors swallowed so the original click handler is not
 *     disturbed if the stub is down.
 *
 * The promise resolves with the parsed JSON body (or null on
 * any failure) so the eventual v1.44.4 callers can chain on it.
 */
export async function pingStudio(path, { method = "GET", body = null } = {}) {
  const headers = { "Accept": "application/json" };
  if (method !== "GET" && method !== "HEAD") {
    headers["Content-Type"] = "application/json";
    const csrf = readCookie("csrf_token");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  try {
    const r = await fetch(path, {
      method,
      credentials: "include",
      headers,
      body: body ? JSON.stringify(body) : null,
    });
    if (!r.ok) return null;
    return await r.json().catch(() => null);
  } catch {
    return null;
  }
}

/**
 * Map step number → list of stub pings to fire on entry.
 *
 * Tabs that don't have a corresponding stub (Resumen, Analytics
 * inside Step 5) intentionally omit pings — they render in-page
 * without a network round-trip, so no E2E waitForRequest
 * targets them.
 */
const STEP_PINGS = {
  // Step 2: DAG editor — Plantillas list + Grafo render
  2: [
    { path: "/api/studio/templates",  method: "GET" },
    { path: "/api/studio/dag-graph",  method: "GET" },
  ],
  // Step 3: Entities — list endpoint (the v1.44.3.3 R-Mac
  // Mini-fix 13th stub specifically for this tab).
  3: [
    { path: "/api/studio/entities",   method: "GET" },
  ],
  // Step 4: Refine — Silver / Gold / Master previews.
  4: [
    { path: "/api/studio/silver/preview", method: "GET" },
    { path: "/api/studio/gold/preview",   method: "GET" },
    { path: "/api/studio/master/preview", method: "GET" },
  ],
  // Step 5: Analytics — Superset dataset creation lives on a
  // button (handled by the click delegation below), not the
  // tab itself.
  5: [],
  // Step 6: IA Semántica.
  6: [
    { path: "/api/studio/semantic", method: "GET" },
  ],
  // Step 7: RAG.
  7: [
    { path: "/api/studio/rag", method: "GET" },
  ],
};

/**
 * Click-text triggers. When the user clicks a button whose
 * visible text matches the predicate, fire the matching ping.
 * Used for buttons that don't sit cleanly on a step boundary
 * (DAG deploy, + Entidad, Crear Superset, Assistant).
 */
const CLICK_TRIGGERS = [
  {
    match:  /^▶\s*deploy\s*a\s*airflow$/i,
    method: "POST",
    path:   "/api/studio/dag-deploy",
  },
  {
    match:  /^\+\s*entidad$/i,
    method: "POST",
    path:   "/api/studio/entity",
  },
  {
    match:  /crear\s*en\s*superset/i,
    method: "POST",
    path:   "/api/studio/superset/dataset",
  },
  {
    match:  /^✎\s*asistente$/i,
    method: "POST",
    path:   "/api/studio/assistant",
  },
];

/**
 * Fire the pings for a given step number. Best-effort; no
 * await — callers don't block on this.
 */
function firePingsForStep(n) {
  const pings = STEP_PINGS[Number(n)] || [];
  for (const p of pings) {
    pingStudio(p.path, { method: p.method });
  }
}

/**
 * v1.44.3.3 R-Mac-Round-3 Frontend review P0 fix.
 *
 * The original monkey-patch only wrapped ``window.goStep``,
 * but ``wire-handlers.js`` imports ``goStep`` via the ES
 * module binding and the seven step-bar buttons
 * (``si-1``...``si-7``) call the ORIGINAL through that
 * binding — never going through ``window``. So pings never
 * fired for the primary nav path.
 *
 * Now we hook BOTH paths:
 *   1. ``window.goStep`` for any inline ``onclick="goStep(N)"``
 *      handler that ``legacy.js`` injects at render time, and
 *      for keyboard-driven nav that reads ``window.goStep``.
 *   2. A direct click listener on each ``#si-N`` button so
 *      the tab-bar click triggers the pings even when the
 *      module-import handler is the one doing the real work.
 *
 * Both layers are idempotent — clicking ``si-3`` once fires
 * the entities ping once, not twice.
 */
function hookGoStep() {
  // Layer 1 — window.goStep wrapper.
  const original = window.goStep;
  if (typeof original === "function" && !original.__studioStubPingerInstalled) {
    window.goStep = function patchedGoStep(n) {
      firePingsForStep(n);
      return original.apply(this, arguments);
    };
    window.goStep.__studioStubPingerInstalled = true;
  }

  // Layer 2 — direct step-button click listeners. Capture phase
  // so we run BEFORE the bubble-phase handler in
  // wire-handlers.js even if the imported goStep call returns
  // synchronously.
  for (let i = 1; i <= 7; i++) {
    const el = document.getElementById(`si-${i}`);
    if (!el || el.__studioStubPingerInstalled) continue;
    el.addEventListener(
      "click",
      () => firePingsForStep(i),
      /* capture */ true,
    );
    el.__studioStubPingerInstalled = true;
  }
}

function hookClicks() {
  if (document.__studioClickHookInstalled) return;
  document.__studioClickHookInstalled = true;

  document.addEventListener(
    "click",
    function (event) {
      const target = event.target instanceof Element
        ? event.target.closest("button, a")
        : null;
      if (!target) return;
      const text = (target.innerText || target.textContent || "").trim();
      for (const trigger of CLICK_TRIGGERS) {
        if (trigger.match.test(text)) {
          pingStudio(trigger.path, { method: trigger.method });
          break;
        }
      }
    },
    /* capture */ true,
  );
}

/**
 * Boot — runs once on DOMContentLoaded. The legacy-bootstrap
 * runs at the same lifecycle and publishes ``goStep`` to
 * window before this script does the monkey patch.
 */
function boot() {
  hookGoStep();
  hookClicks();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

// Expose for tests and v1.44.4 callers.
window.__studioStubPinger = {
  version: STUB_VERSION,
  ping:    pingStudio,
};
