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
  return state._currentCartridge?.id
    || document.getElementById("cartridge-sel")?.value
    || state._cartridges?.[0]?.id
    || "";
}

function withCartridge(path) {
  const cartridge = currentCartridge();
  if (!cartridge) return null;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}cartridge=${encodeURIComponent(cartridge)}`;
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
    if (!r.ok) {
      showStudioError(actionErrorMessage(payload, `Error HTTP ${r.status}`), path);
      return null;
    }
    return payload;
  } catch (err) {
    console.warn("Studio action failed", path, err);
    showStudioError(err instanceof Error ? err.message : "Accion de Studio fallida", path);
    return null;
  }
}

function actionErrorMessage(payload, fallback = "Accion fallida") {
  if (!payload) return fallback;
  if (typeof payload === "string") return payload;
  const detail = payload.detail ?? payload.error ?? payload.message ?? payload.reason;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    return detail.message || detail.error || detail.detail || JSON.stringify(detail);
  }
  const result = payload.result;
  if (result && typeof result === "object") return actionErrorMessage(result, fallback);
  return fallback;
}

function showStudioError(message, path) {
  let box = document.getElementById("studio-action-error");
  if (!box) {
    box = document.createElement("div");
    box.id = "studio-action-error";
    box.setAttribute("role", "alert");
    box.style.cssText = [
      "position:fixed",
      "right:24px",
      "bottom:96px",
      "z-index:10010",
      "max-width:min(520px,calc(100vw - 48px))",
      "padding:12px 14px",
      "border:1px solid var(--red,#ef4444)",
      "border-radius:8px",
      "background:rgba(127,29,29,.94)",
      "color:#fff",
      "font:12px/1.45 var(--font-sans,system-ui)",
      "box-shadow:0 12px 32px rgba(0,0,0,.35)",
    ].join(";");
    document.body.appendChild(box);
  }
  box.innerHTML = `<strong>Accion fallida</strong><br>${escapeHtml(message)}<br><small>${escapeHtml(path || "")}</small>`;
  window.clearTimeout(box.__hideTimer);
  box.__hideTimer = window.setTimeout(() => {
    if (box?.parentElement) box.remove();
  }, 9000);
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
    { path: "/api/studio/silver/preview", render: (data) => renderLayerPreview("silver", data) },
    { path: "/api/studio/gold/preview", render: (data) => renderLayerPreview("gold", data) },
    { path: "/api/studio/master/preview", render: (data) => renderLayerPreview("master", data) },
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
];

function fireActionsForStep(n) {
  const actions = STEP_ACTIONS[Number(n)] || [];
  for (const action of actions) {
    const path = withCartridge(action.path);
    if (!path) continue;
    studioAction(path, {
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

function sanitizedSvg(markup) {
  const doc = new DOMParser().parseFromString(String(markup || ""), "image/svg+xml");
  const svg = doc.documentElement;
  if (!svg || svg.nodeName.toLowerCase() !== "svg" || doc.getElementsByTagName("parsererror").length) return null;
  svg.querySelectorAll("script, foreignObject, iframe, object, embed").forEach((node) => node.remove());
  for (const node of [svg, ...svg.querySelectorAll("*")]) {
    for (const attr of [...node.attributes]) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim().toLowerCase();
      if (name.startsWith("on") || ((name === "href" || name === "xlink:href") && !value.startsWith("#"))) {
        node.removeAttribute(attr.name);
      }
    }
  }
  return document.importNode(svg, true);
}

function renderDagGraph(data) {
  const panel = document.getElementById("dag-graph-panel");
  if (!panel || !data?.svg) return;
  const svg = sanitizedSvg(data.svg);
  const frame = document.createElement("div");
  frame.style.padding = "10px";
  frame.style.overflow = "auto";
  if (svg) frame.appendChild(svg);
  panel.replaceChildren(frame);
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

function datasetNameFromSupersetButton(button) {
  const row = button?.closest?.(".ds-row");
  const rawName = row?.querySelector?.(".ds-row-name")?.textContent?.trim();
  if (!rawName) return null;
  return `gold_${rawName.replace(/^gold_/, "")}`;
}

function renderSupersetStatus(payload, response, button) {
  const row = button?.closest?.(".ds-row");
  const host = row?.parentElement || document.getElementById("gold-list");
  if (!host) return;
  host.querySelectorAll(".superset-status").forEach(node => node.remove());
  const box = document.createElement("div");
  box.className = "superset-status empty-card";
  if (!response.ok || payload?.error || payload?.detail) {
    box.innerHTML = `<span style="color:var(--red)">No se pudo crear: ${escapeHtml(actionErrorMessage(payload, `HTTP ${response.status}`))}</span>`;
  } else if (payload?.needs_materialization) {
    const table = payload?.table || payload?.table_name || datasetNameFromSupersetButton(button) || "";
    const dataset = String(table).replace(/^gold_/, "");
    box.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <span style="color:var(--amber)">${escapeHtml(payload?.message || "Materializa primero el dataset Gold y vuelve a intentar.")}</span>
        ${dataset ? `<button type="button" class="btn btn-sm btn-amber" data-superset-materialize="${escapeHtml(dataset)}">Materializar y crear</button>` : ""}
      </div>
    `;
    const materializeBtn = box.querySelector("[data-superset-materialize]");
    if (materializeBtn) {
      materializeBtn.addEventListener("click", () => materializeAndCreateSuperset(dataset, button, box));
    }
  } else {
    const table = payload?.table || payload?.table_name || payload?.dataset_name || "dataset";
    box.textContent = payload?.existing
      ? `✓ Ya existía en Superset: ${table}`
      : `✓ Dataset creado en Superset: ${table}`;
  }
  host.prepend(box);
}

async function materializeAndCreateSuperset(datasetName, originalButton, statusBox) {
  if (!datasetName) return;
  if (statusBox) {
    statusBox.innerHTML = `<span style="color:var(--amber)">⟳ Materializando Gold '${escapeHtml(datasetName)}'...</span>`;
  }
  const result = await studioAction(`/datasets/${encodeURIComponent(datasetName)}/refresh`, {
    method: "POST",
    render: (payload, response) => {
      if (!response.ok && statusBox) {
        statusBox.innerHTML = `<span style="color:var(--red)">${escapeHtml(actionErrorMessage(payload, `HTTP ${response.status}`))}</span>`;
      }
    },
  });
  if (!result) return;
  if (result?.error || result?.detail) {
    if (statusBox) {
      statusBox.innerHTML = `<span style="color:var(--red)">${escapeHtml(actionErrorMessage(result, "No se pudo materializar"))}</span>`;
    }
    return;
  }
  if (statusBox) {
    const rows = result?.row_count ?? result?.result?.row_count;
    statusBox.innerHTML = `<span style="color:var(--green)">✓ Materializado${rows != null ? ` · ${Number(rows).toLocaleString("es")} filas` : ""}. Creando dataset en Superset...</span>`;
  }
  await createSupersetDatasetFromButton(originalButton);
}

function createSupersetDatasetFromButton(button) {
  const tableName = datasetNameFromSupersetButton(button);
  const host = button?.closest?.(".ds-row")?.parentElement || document.getElementById("gold-list");
  if (host) {
    host.querySelectorAll(".superset-status").forEach(node => node.remove());
    host.insertAdjacentHTML("afterbegin", '<div class="superset-status empty-card">⟳ Creando dataset en Superset...</div>');
  }
  return studioAction("/api/studio/superset/dataset", {
    method: "POST",
    body: { table_name: tableName, schema: "public" },
    render: (payload, response) => renderSupersetStatus(payload, response, button),
  });
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
      if (window.__studioActionBridgePassthrough) {
        return original.apply(this, arguments);
      }
      const result = original.apply(this, arguments);
      Promise.resolve(result).finally(() => fireActionsForStep(n));
      return result;
    };
    window.goStep.__studioActionBridgeInstalled = true;
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
      stopInlineHandler(event);
      if (action.before) action.before(target);
      const path = withCartridge(action.path);
      if (!path) return;
      studioAction(path, {
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

    const actionEl = target.closest("[data-studio-action]");
    if (actionEl) {
      const action = actionEl.getAttribute("data-studio-action");
      const cartridge = actionEl.getAttribute("data-cartridge") || currentCartridge();
      const entity = actionEl.getAttribute("data-entity") || "";
      if (action === "extract" && typeof window.extractNow === "function") {
        stopInlineHandler(event);
        const mode = actionEl.getAttribute("data-mode") || "";
        const dagId = actionEl.getAttribute("data-dag-id") || "";
        window.extractNow(cartridge, entity, mode, dagId);
        return;
      }
      if (action === "entity-preview" && typeof window.toggleEntityPreview === "function") {
        stopInlineHandler(event);
        window.toggleEntityPreview(cartridge, entity, actionEl);
        return;
      }
      if (action === "entity-logs" && typeof window.toggleEntityLogs === "function") {
        stopInlineHandler(event);
        window.toggleEntityLogs(cartridge, entity, actionEl);
        return;
      }
      if (action === "open-dag-editor" && typeof window.openDagEditor === "function") {
        stopInlineHandler(event);
        const dagId = actionEl.getAttribute("data-dag-id") || "";
        if (dagId) window.openDagEditor(dagId);
        return;
      }
      if (action === "rag-reindex" && typeof window.reindexSource === "function") {
        stopInlineHandler(event);
        const kind = actionEl.getAttribute("data-rag-kind") || "";
        const name = actionEl.getAttribute("data-rag-name") || "";
        const ragCartridge = actionEl.getAttribute("data-cartridge") || cartridge;
        if (kind && name) window.reindexSource(kind, name, ragCartridge, actionEl);
        return;
      }
      if (action === "open-cartridge-entities" && typeof window.openCartridgeEntities === "function") {
        stopInlineHandler(event);
        if (cartridge) window.openCartridgeEntities(cartridge);
        return;
      }
      if (action === "export-cartridge" && typeof window.exportCartridge === "function") {
        stopInlineHandler(event);
        if (cartridge) window.exportCartridge(cartridge);
        return;
      }
    }

    const dagItem = target.closest(".dag-sidebar-item[data-dag-id]");
    if (dagItem && typeof window.selectDag === "function") {
      stopInlineHandler(event);
      const dagId = dagItem.getAttribute("data-dag-id") || "";
      if (dagId) window.selectDag(dagId);
      return;
    }

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
      return;
    }

    const supersetCreate = target.closest("button");
    const supersetText = (supersetCreate?.innerText || supersetCreate?.textContent || "").trim();
    if (supersetCreate && /crear\s+en\s+superset/i.test(supersetText)) {
      stopInlineHandler(event);
      createSupersetDatasetFromButton(supersetCreate);
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

window.studioSetAssistantOpen = setAssistantOpen;

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
