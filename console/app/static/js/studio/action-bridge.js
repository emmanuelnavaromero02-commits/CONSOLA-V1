/**
 * Studio action bridge.
 * ---------------------
 * Connects the legacy :8000 Studio screen to the canonical
 * /api/studio/* endpoints. This is intentionally best-effort:
 * the original legacy handlers keep running, and this bridge
 * only adds canonical requests plus small render hooks where the
 * old UI did not have a real target yet.
 */

import { state } from "./legacy-state.js";

const ACTION_BRIDGE_VERSION = "v1.44.5";
const AI_PANEL_STORAGE_KEY = "studio.ai.panel";

function readCookie(name) {
  const prefix = `${name}=`;
  for (const raw of document.cookie.split(";")) {
    const c = raw.trim();
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
  }
  return null;
}

function currentCartridge() {
  return state._currentCartridge?.id || document.getElementById("cartridge-sel")?.value || "replicon";
}

function withCartridge(path) {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}cartridge=${encodeURIComponent(currentCartridge())}`;
}

export async function studioAction(path, { method = "GET", body = null, render = null } = {}) {
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
    const payload = await r.json().catch(() => ({}));
    if (typeof render === "function") render(payload, r);
    return r.ok ? payload : null;
  } catch (err) {
    console.warn("Studio action failed", path, err);
    return null;
  }
}

const STEP_ACTIONS = {
  2: [
    { path: "/api/studio/templates", render: renderTemplates },
    { path: "/api/studio/dag-graph", render: renderDagGraph },
  ],
  3: [
    { path: "/api/studio/entities", render: renderEntities },
  ],
  4: [
    { path: "/api/studio/silver/preview", render: data => renderLayerPreview("silver", data) },
    { path: "/api/studio/gold/preview", render: data => renderLayerPreview("gold", data) },
    { path: "/api/studio/master/preview", render: data => renderLayerPreview("master", data) },
  ],
  5: [],
  6: [
    { path: "/api/studio/semantic", render: renderSemantic },
  ],
  7: [
    { path: "/api/studio/rag", render: renderRag },
  ],
};

const CLICK_ACTIONS = [
  {
    match: /grafo/i,
    path: "/api/studio/dag-graph",
    render: renderDagGraph,
  },
  {
    match: /plantillas/i,
    path: "/api/studio/templates",
    render: renderTemplates,
  },
  {
    match: /^\+\s*entidad$/i,
    path: "/api/studio/entities",
    render: renderEntities,
    before: openEntityForm,
  },
  {
    match: /^silver$/i,
    path: "/api/studio/silver/preview",
    render: data => renderLayerPreview("silver", data),
  },
  {
    match: /^master$/i,
    path: "/api/studio/master/preview",
    render: data => renderLayerPreview("master", data),
  },
  {
    match: /^gold$/i,
    path: "/api/studio/gold/preview",
    render: data => renderLayerPreview("gold", data),
  },
];

function fireActionsForStep(n) {
  const actions = STEP_ACTIONS[Number(n)] || [];
  for (const action of actions) {
    studioAction(withCartridge(action.path), {
      method: action.method || "GET",
      body: typeof action.body === "function" ? action.body() : action.body,
      render: action.render,
    });
  }
}

function renderTemplates(data) {
  const panel = document.getElementById("tpl-list-s");
  if (!panel || !Array.isArray(data?.templates)) return;
  if (!data.templates.length) {
    panel.innerHTML = '<div style="padding:14px;color:var(--text3);font-size:11px">Sin plantillas registradas.</div>';
    return;
  }
  panel.innerHTML = data.templates.map(t => `
    <button class="btn btn-sm" type="button" style="display:block;width:100%;margin:5px 0;text-align:left"
            data-template-id="${escapeHtml(t.id || "")}">
      ${escapeHtml(t.name || t.id || "template")}
    </button>
  `).join("");
}

function renderEntities(data) {
  const area = document.getElementById("entity-list-area");
  if (!area || !Array.isArray(data?.entities)) return;
  if (document.getElementById("new-entity-row")) return;
  if (typeof window.loadEntityList === "function") return;
  area.innerHTML = data.entities.length
    ? data.entities.map(e => `<div class="et-row"><span>${escapeHtml(e.entity || e.name || "")}</span></div>`).join("")
    : '<div class="empty-card">Sin entidades registradas.</div>';
}

function renderDagGraph(data) {
  const panel = document.getElementById("dag-graph-panel");
  if (!panel || !data?.svg) return;
  panel.innerHTML = `<div style="padding:10px;overflow:auto">${data.svg}</div>`;
}

function renderLayerPreview(layer, data) {
  const area = document.getElementById("ds-list-area");
  if (!area || !data || state._activeLayer !== layer) return;
  const marker = `studio-${layer}-preview`;
  let box = document.getElementById(marker);
  if (!box) {
    box = document.createElement("div");
    box.id = marker;
    box.style.cssText = "padding:10px;border-bottom:1px solid var(--border);font-size:11px";
    area.prepend(box);
  }
  if (data.available === false) {
    box.innerHTML = `<span style="color:var(--text3)">${escapeHtml(data.reason || `Sin datos ${layer}`)}</span>`;
    return;
  }
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const cols = Array.isArray(data.columns) && data.columns.length
    ? data.columns
    : Object.keys(rows[0] || {});
  box.innerHTML = `
    <div style="color:var(--green);margin-bottom:6px">✓ Preview ${escapeHtml(layer)} · ${rows.length} filas</div>
    ${rows.length ? renderTable(cols, rows.slice(0, 5)) : '<div style="color:var(--text3)">Sin filas disponibles.</div>'}
  `;
}

function renderSemantic(data) {
  const root = document.getElementById("cat-root") || document.getElementById("step-content");
  if (!root || !data?.entities) return;
  root.dataset.semanticLoaded = String(data.entities.length);
}

function renderRag(data) {
  const root = document.getElementById("rag-sources-list");
  if (!root || !Array.isArray(data?.sources)) return;
  root.dataset.ragLoaded = String(data.sources.length);
}

function renderAssistant(data) {
  if (!data?.reply || typeof window.aiAppend !== "function") return;
  window.aiAppend("assistant", data.reply);
}

function openEntityForm() {
  if (typeof window.showAddEntityRow === "function") {
    try { window.showAddEntityRow(); } catch {}
  }
}

function renderTable(cols, rows) {
  return `<div style="overflow:auto;max-height:220px;border:1px solid var(--border)">
    <table style="border-collapse:collapse;width:100%;font-family:var(--font-mono);font-size:10px">
      <thead><tr>${cols.map(c => `<th style="text-align:left;padding:4px 6px;border-bottom:1px solid var(--border)">${escapeHtml(c)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(row => `<tr>${cols.map(c => `<td style="padding:3px 6px;border-bottom:1px solid var(--bg2)">${escapeHtml(row?.[c] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody>
    </table>
  </div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function hookGoStep() {
  const original = window.goStep;
  if (typeof original === "function" && !original.__studioActionBridgeInstalled) {
    window.goStep = function patchedGoStep(n) {
      fireActionsForStep(n);
      return original.apply(this, arguments);
    };
    window.goStep.__studioActionBridgeInstalled = true;
  }

  for (let i = 1; i <= 7; i++) {
    const el = document.getElementById(`si-${i}`);
    if (!el || el.__studioActionBridgeInstalled) continue;
    el.addEventListener("click", () => fireActionsForStep(i), true);
    el.__studioActionBridgeInstalled = true;
  }
}

function hookClicks() {
  if (document.__studioActionClickHookInstalled) return;
  document.__studioActionClickHookInstalled = true;
  document.addEventListener("click", event => {
    const target = event.target instanceof Element
      ? event.target.closest("button, a, .tab, .step-item")
      : null;
    if (!target) return;
    const text = (target.innerText || target.textContent || "").trim();
    for (const action of CLICK_ACTIONS) {
      if (!action.match.test(text)) continue;
      if (action.before) action.before(target);
      studioAction(withCartridge(action.path), {
        method: action.method || "GET",
        body: typeof action.body === "function" ? action.body() : action.body,
        render: action.render,
      });
      break;
    }
  }, true);
}

function stopInlineHandler(event) {
  event.preventDefault();
  event.stopImmediatePropagation();
}

function uploadZoneId(zone) {
  return zone?.getAttribute("data-upload-zone") || zone?.id || "";
}

function hookUploadZones() {
  if (document.__studioUploadZoneHookInstalled) return;
  document.__studioUploadZoneHookInstalled = true;

  document.addEventListener("click", event => {
    if (event.target instanceof Element && event.target.matches("input[type='file'][data-upload-input]")) {
      return;
    }
    const zone = event.target instanceof Element
      ? event.target.closest(".upload-zone[data-upload-zone], .upload-zone[id]")
      : null;
    if (!zone) return;
    stopInlineHandler(event);
    const id = uploadZoneId(zone);
    const input = document.getElementById(`fi-${id}`);
    if (input) input.click();
  }, true);

  document.addEventListener("dragover", event => {
    const zone = event.target instanceof Element
      ? event.target.closest(".upload-zone[data-upload-zone], .upload-zone[id]")
      : null;
    if (!zone) return;
    stopInlineHandler(event);
    zone.classList.add("drag-over");
  }, true);

  document.addEventListener("dragleave", event => {
    const zone = event.target instanceof Element
      ? event.target.closest(".upload-zone[data-upload-zone], .upload-zone[id]")
      : null;
    if (!zone) return;
    stopInlineHandler(event);
    zone.classList.remove("drag-over");
  }, true);

  document.addEventListener("drop", event => {
    const zone = event.target instanceof Element
      ? event.target.closest(".upload-zone[data-upload-zone], .upload-zone[id]")
      : null;
    if (!zone) return;
    stopInlineHandler(event);
    const id = uploadZoneId(zone);
    if (typeof window.handleSpecDrop === "function") {
      window.handleSpecDrop(event, id);
    }
  }, true);

  document.addEventListener("change", event => {
    const input = event.target instanceof Element
      ? event.target.closest("input[type='file'][data-upload-input], input[type='file'][id^='fi-']")
      : null;
    if (!input) return;
    stopInlineHandler(event);
    const id = input.getAttribute("data-upload-input") || String(input.id || "").replace(/^fi-/, "");
    if (typeof window.handleSpecFile === "function") {
      window.handleSpecFile(input, id);
    }
  }, true);
}

function hookRuntimeActions() {
  if (document.__studioRuntimeActionHookInstalled) return;
  document.__studioRuntimeActionHookInstalled = true;

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    const template = target.closest("[data-template-id]");
    if (template) {
      stopInlineHandler(event);
      const id = template.getAttribute("data-template-id");
      if (id && typeof window.applyDagTemplate === "function") {
        window.applyDagTemplate(id);
      }
      return;
    }

    const deploy = target.closest("#btn-deploy");
    if (deploy) {
      stopInlineHandler(event);
      if (typeof window.deployDag === "function") {
        window.deployDag();
      }
    }
  }, true);

  document.addEventListener("keydown", event => {
    const template = event.target instanceof Element
      ? event.target.closest("[data-template-id]")
      : null;
    if (!template || !["Enter", " "].includes(event.key)) return;
    stopInlineHandler(event);
    const id = template.getAttribute("data-template-id");
    if (id && typeof window.applyDagTemplate === "function") {
      window.applyDagTemplate(id);
    }
  }, true);
}

function setAssistantOpen(open) {
  const isOpen = Boolean(open);
  document.body.classList.toggle("studio-ai-open", isOpen);
  document.body.classList.toggle("studio-ai-collapsed", !isOpen);
  try {
    window.localStorage.setItem(AI_PANEL_STORAGE_KEY, isOpen ? "open" : "collapsed");
  } catch {}
  const toggle = document.getElementById("studio-ai-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", String(isOpen));
  if (isOpen) {
    setTimeout(() => document.getElementById("ai-input")?.focus(), 50);
  }
}

function hookAssistantPanel() {
  if (document.__studioAssistantPanelHookInstalled) return;
  document.__studioAssistantPanelHookInstalled = true;

  if (!document.getElementById("studio-ai-toggle")) {
    const toggle = document.createElement("button");
    toggle.id = "studio-ai-toggle";
    toggle.type = "button";
    toggle.className = "studio-ai-toggle";
    toggle.setAttribute("aria-label", "Abrir asistente de Studio");
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = "◈";
    document.body.appendChild(toggle);
  }

  const header = document.querySelector("#ai-panel .ai-hdr");
  if (header && !document.getElementById("studio-ai-close")) {
    const close = document.createElement("button");
    close.id = "studio-ai-close";
    close.type = "button";
    close.className = "btn-sm studio-ai-close";
    close.setAttribute("aria-label", "Cerrar asistente de Studio");
    close.textContent = "×";
    header.appendChild(close);
  }

  document.getElementById("studio-ai-toggle")?.addEventListener("click", () => setAssistantOpen(true));
  document.getElementById("studio-ai-close")?.addEventListener("click", () => setAssistantOpen(false));

  let initial = "collapsed";
  try {
    initial = window.localStorage.getItem(AI_PANEL_STORAGE_KEY) || "collapsed";
  } catch {}
  setAssistantOpen(initial === "open");
}

function boot() {
  hookGoStep();
  hookClicks();
  hookUploadZones();
  hookRuntimeActions();
  hookAssistantPanel();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

window.__studioActionBridge = {
  version: ACTION_BRIDGE_VERSION,
  request: studioAction,
};
