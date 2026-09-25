(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const elList = $("#copilot-conv-list");
  const elMessages = $("#copilot-messages");
  const elForm = $("#copilot-form");
  const elPrompt = $("#copilot-prompt");
  const elSend = $("#copilot-send");
  const elTitle = $("#copilot-title");
  const elToast = $("#copilot-toast");
  const elNewBtn = $("#copilot-new");
  const elCommandBtn = $("#copilot-command");
  const elMemoryBtn = $("#copilot-memory");
  const elDraftBtn = $("#copilot-draft");
  const elWorkflowsBtn = $("#copilot-workflows");
  const elPalette = $("#copilot-palette");
  const elCommandFilter = $("#copilot-command-filter");
  const elCommandList = $("#copilot-command-list");
  const elMemoryDrawer = $("#copilot-memory-drawer");
  const elMemoryBody = $("#copilot-memory-body");
  const elMemoryForm = $("#copilot-memory-form");
  const elMemoryFact = $("#copilot-memory-fact");
  const elMemorySource = $("#copilot-memory-source");
  const elDraftModal = $("#copilot-draft-modal");
  const elDraftForm = $("#copilot-draft-form");
  const elDraftOutput = $("#copilot-draft-output");
  const elDraftCopy = $("#copilot-draft-copy");
  const elDraftSubmit = $("#copilot-draft-submit");
  const elWorkflowModal = $("#copilot-workflow-modal");
  const elWorkflowForm = $("#copilot-workflow-form");
  const elWorkflowList = $("#copilot-workflow-list");

  let activeConversationId = null;
  let lastDraftBody = "";

  const COMMANDS = [
    {
      id: "memoria",
      group: "Basicos",
      label: "Memoria",
      description: "Ver y editar lo que sabe el copiloto.",
      run: openMemory,
    },
    {
      id: "redactar",
      group: "Basicos",
      label: "Redactar",
      description: "Generar un borrador de email, mensaje o nota.",
      run: () => openDraft(),
    },
    {
      id: "workflows",
      group: "Basicos",
      label: "Workflows",
      description: "Crear o revisar planes operativos.",
      run: openWorkflows,
    },
    {
      id: "briefing",
      group: "Basicos",
      label: "Briefing del dia",
      description: "Pedir al copiloto alertas y novedades.",
      prompt: "Dame el briefing del dia.",
    },
    {
      id: "reporte_mensual",
      group: "Reportes",
      label: "Reporte mensual",
      description: "Reporte ejecutivo del mes con KPIs principales.",
      prompt: "Genera el reporte ejecutivo del mes con KPIs principales.",
    },
    {
      id: "turnover_analysis",
      group: "Reportes",
      label: "Analisis de rotacion",
      description: "Rotacion de personal del ultimo trimestre.",
      prompt: "Analisis de rotacion de personal del ultimo trimestre.",
    },
    {
      id: "cash_position",
      group: "Reportes",
      label: "Estado de caja",
      description: "Caja por banco y moneda.",
      prompt: "Dame el estado actual de caja por banco y moneda.",
    },
    {
      id: "payroll_summary",
      group: "Reportes",
      label: "Resumen de nomina",
      description: "Nomina del ultimo periodo por departamento.",
      prompt: "Resumen de nomina del ultimo periodo por departamento.",
    },
    {
      id: "headcount",
      group: "Reportes",
      label: "Headcount",
      description: "Empleados activos por departamento y pais.",
      prompt: "Empleados activos por departamento y por pais.",
    },
  ];


  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (opts.method && opts.method !== "GET") {
      headers["X-CSRF-Token"] = csrfToken();
    }
    const r = await fetch(path, {
      credentials: "same-origin",
      ...opts,
      headers,
    });
    if (r.status === 401) {
      window.location.href = "/login?next=" + encodeURIComponent("/copilot");
      throw new Error("unauthenticated");
    }
    if (!r.ok) {
      let detail = "";
      try { detail = (await r.json()).detail || ""; } catch (_) {}
      throw new Error(detail || ("HTTP " + r.status));
    }
    return r.json();
  }

  function toast(msg, kind = "info") {
    elToast.textContent = msg;
    elToast.className = "copilot-toast copilot-toast-" + kind;
    elToast.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { elToast.hidden = true; }, 5000);
  }

  function scrollToBottom() {
    elMessages.scrollTop = elMessages.scrollHeight;
  }

  function openOverlay(el) {
    if (!el) return;
    el.hidden = false;
    const focusable = el.querySelector("input, textarea, select, button");
    if (focusable) setTimeout(() => focusable.focus(), 0);
  }

  function closeOverlay(el) {
    if (el) el.hidden = true;
  }

  function clearNode(el) {
    while (el && el.firstChild) el.removeChild(el.firstChild);
  }

  function textEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    el.textContent = text || "";
    return el;
  }

  function fmtDate(value) {
    if (!value) return "";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString();
  }


  function appendMessage(role, content) {
    const el = document.createElement("div");
    el.className = "copilot-message copilot-message-" + role;
    el.textContent = content || "";
    elMessages.appendChild(el);
    scrollToBottom();
    return el;
  }

  function appendToolCard(call) {
    const el = document.createElement("div");
    el.className = "copilot-tool-card";
    const header = document.createElement("div");
    header.className = "copilot-tool-card-header";
    header.textContent = "🔧 " + (call.tool || "tool");
    el.appendChild(header);
    const args = document.createElement("div");
    args.textContent = JSON.stringify(call.args || call.input || {}, null, 2);
    el.appendChild(args);
    elMessages.appendChild(el);
    scrollToBottom();
  }

  function appendApprovalCard(pending, messageId, conversationId) {
    const el = document.createElement("div");
    el.className = "copilot-approval-card";
    el.dataset.messageId = messageId;

    const title = document.createElement("div");
    title.className = "copilot-approval-title";
    title.textContent = "Acción sensible — requiere tu aprobación";
    el.appendChild(title);

    for (const p of pending) {
      const tool = document.createElement("div");
      tool.className = "copilot-approval-tool";
      tool.textContent = (p.tool || "?") + "  " + JSON.stringify(p.args || {});
      el.appendChild(tool);
    }

    const actions = document.createElement("div");
    actions.className = "copilot-approval-actions";

    const approveBtn = document.createElement("button");
    approveBtn.type = "button";
    approveBtn.className = "copilot-btn-primary";
    approveBtn.textContent = "Aprobar";

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "copilot-btn-secondary";
    cancelBtn.textContent = "Cancelar";

    approveBtn.addEventListener("click", () =>
      approveAction(conversationId, messageId, el)
    );
    cancelBtn.addEventListener("click", () => {
      el.remove();
      toast("Acción cancelada.", "info");
    });

    actions.appendChild(approveBtn);
    actions.appendChild(cancelBtn);
    el.appendChild(actions);
    elMessages.appendChild(el);
    scrollToBottom();
  }


  async function loadConversations() {
    try {
      const data = await api("/api/copilot/conversations");
      const convs = data.conversations || [];
      elList.replaceChildren();
      if (convs.length === 0) {
        const li = document.createElement("li");
        li.className = "copilot-empty";
        li.textContent = "Sin conversaciones todavía.";
        elList.appendChild(li);
        return;
      }
      for (const c of convs) {
        const li = document.createElement("li");
        li.className = "copilot-conv-item";
        if (c.id === activeConversationId) li.classList.add("is-active");
        li.textContent = c.title || "Sin título";
        li.dataset.id = c.id;
        li.addEventListener("click", () => selectConversation(c.id, c.title));
        elList.appendChild(li);
      }
    } catch (err) {
      toast("Error cargando conversaciones: " + err.message, "error");
    }
  }

  async function selectConversation(id, title) {
    activeConversationId = id;
    elTitle.textContent = title || "";
    elMessages.replaceChildren();
    try {
      const data = await api("/api/copilot/conversations/" + encodeURIComponent(id));
      const msgs = data.messages || [];
      for (const m of msgs) {
        if (m.role === "user" || m.role === "assistant") {
          appendMessage(m.role, m.content || "");
        }
        if (m.tool_calls && Array.isArray(m.tool_calls)) {
          const alreadyProcessed =
            m.tool_results != null &&
            !(Array.isArray(m.tool_results) && m.tool_results.length === 0);
          for (const c of m.tool_calls) {
            if (c.approval_key && !alreadyProcessed) {
              appendApprovalCard([c], m.id, id);
            } else {
              appendToolCard(c);
            }
          }
        }
        if (Array.isArray(m.citations) && m.citations.length > 0) {
          appendCitations(m.citations);
        }
      }
      loadConversations();
    } catch (err) {
      toast("Error abriendo conversación: " + err.message, "error");
    }
  }

  async function newConversation() {
    try {
      const conv = await api("/api/copilot/conversations", {
        method: "POST",
        body: JSON.stringify({ title: "Nueva conversación" }),
      });
      activeConversationId = conv.id;
      await loadConversations();
      elMessages.replaceChildren();
      elTitle.textContent = conv.title || "";
      elPrompt.focus();
    } catch (err) {
      toast("No pude crear la conversación: " + err.message, "error");
    }
  }


  async function sendMessage(text) {
    if (!activeConversationId) {
      await newConversation();
      if (!activeConversationId) return;
    }
    appendMessage("user", text);
    elPrompt.value = "";
    elSend.disabled = true;
    try {
      const out = await api(
        "/api/copilot/conversations/" +
          encodeURIComponent(activeConversationId) +
          "/messages",
        { method: "POST", body: JSON.stringify({ message: text }) }
      );
      renderTurnResponse(out);
    } catch (err) {
      toast("Error: " + err.message, "error");
    } finally {
      elSend.disabled = false;
      elPrompt.focus();
    }
  }

  function renderTurnResponse(out) {
    if (out.tool_calls && Array.isArray(out.tool_calls)) {
      for (const c of out.tool_calls) appendToolCard(c);
    }
    if (out.reply) appendMessage("assistant", out.reply);
    if (Array.isArray(out.citations) && out.citations.length > 0) {
      appendCitations(out.citations);
    }
    if (out.requires_approval && Array.isArray(out.pending_actions)) {
      appendApprovalCard(out.pending_actions, out.message_id, activeConversationId);
    }
  }


  function _formatAge(seconds) {
    if (seconds == null) return "";
    const n = Number(seconds);
    if (!Number.isFinite(n)) return "";
    if (n < 60)        return "hace " + Math.max(0, Math.floor(n)) + "s";
    if (n < 3600)      return "hace " + Math.floor(n / 60) + " min";
    if (n < 86400)     return "hace " + Math.floor(n / 3600) + "h";
    return "hace " + Math.floor(n / 86400) + "d";
  }

  const _FRESHNESS_ICONS = {
    fresh:      "✅",
    recent:     "🟢",
    stale:      "⚠️",
    very_stale: "🔴",
    unknown:    "❓",
  };

  function _appendCitationCard(c) {
    const card = document.createElement("div");
    card.className = "copilot-citation-card";
    const level = c.freshness_level || "unknown";
    card.dataset.freshness = level;

    const sourceRow = document.createElement("div");
    sourceRow.className = "citation-source";
    const icon = document.createElement("span");
    icon.className = "citation-icon";
    icon.textContent = "📊";
    icon.setAttribute("aria-hidden", "true");
    const sourceStrong = document.createElement("strong");
    sourceStrong.textContent = c.source || "?";
    sourceRow.appendChild(icon);
    sourceRow.appendChild(document.createTextNode(" "));
    sourceRow.appendChild(sourceStrong);
    if (c.entity) {
      sourceRow.appendChild(document.createTextNode(" · "));
      const ent = document.createElement("span");
      ent.className = "citation-entity";
      ent.textContent = c.entity;
      sourceRow.appendChild(ent);
    }
    card.appendChild(sourceRow);

    const metaRow = document.createElement("div");
    metaRow.className = "citation-meta";
    const parts = [];
    if (c.run_id) {
      const rid = String(c.run_id);
      parts.push("Run " + (rid.length > 8 ? rid.slice(0, 8) + "…" : rid));
    }
    const ageText = _formatAge(c.age_seconds);
    if (ageText) parts.push(ageText);
    metaRow.textContent = parts.join(" · ");
    const fIcon = document.createElement("span");
    fIcon.className = "citation-freshness";
    fIcon.textContent = " " + (_FRESHNESS_ICONS[level] || _FRESHNESS_ICONS.unknown);
    fIcon.setAttribute("aria-label", "freshness: " + level);
    fIcon.setAttribute("role", "img");
    metaRow.appendChild(fIcon);
    card.appendChild(metaRow);

    if (c.row_count != null) {
      const rows = document.createElement("div");
      rows.className = "citation-rows";
      const n = Number(c.row_count);
      const fmt = Number.isFinite(n) ? n.toLocaleString() : String(c.row_count);
      rows.textContent = fmt + " filas";
      card.appendChild(rows);
    }

    return card;
  }

  function appendCitations(citations) {
    if (!Array.isArray(citations) || citations.length === 0) return;
    const container = document.createElement("div");
    container.className = "copilot-citations";
    container.setAttribute("aria-label", "Fuentes citadas");
    for (const c of citations) {
      container.appendChild(_appendCitationCard(c));
    }
    elMessages.appendChild(container);
    scrollToBottom();
  }

  async function approveAction(conversationId, messageId, cardEl) {
    try {
      cardEl.querySelectorAll("button").forEach((b) => (b.disabled = true));
      const out = await api(
        "/api/copilot/conversations/" +
          encodeURIComponent(conversationId) +
          "/approve/" +
          encodeURIComponent(messageId),
        { method: "POST" }
      );
      cardEl.remove();
      renderTurnResponse(out);
      toast("Acción ejecutada.", "info");
    } catch (err) {
      toast("Error aprobando: " + err.message, "error");
      cardEl.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  }


  function openPalette(query = "") {
    elCommandFilter.value = query;
    renderCommands();
    openOverlay(elPalette);
  }

  function renderCommands() {
    const q = (elCommandFilter.value || "").trim().toLowerCase().replace(/^\//, "");
    clearNode(elCommandList);
    const matches = COMMANDS.filter((cmd) => {
      const haystack = [cmd.id, cmd.group, cmd.label, cmd.description].join(" ").toLowerCase();
      return !q || haystack.includes(q);
    });
    if (matches.length === 0) {
      elCommandList.appendChild(textEl("p", "copilot-muted", "Sin comandos para ese filtro."));
      return;
    }
    for (const cmd of matches) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copilot-command-item";
      const title = textEl("strong", "", "/" + cmd.id + " · " + cmd.label);
      const desc = textEl("span", "", cmd.description);
      btn.appendChild(title);
      btn.appendChild(desc);
      btn.addEventListener("click", () => {
        closeOverlay(elPalette);
        if (cmd.run) {
          cmd.run();
        } else if (cmd.prompt) {
          sendMessage(cmd.prompt);
        }
      });
      elCommandList.appendChild(btn);
    }
  }

  async function openMemory() {
    openOverlay(elMemoryDrawer);
    await loadMemory();
  }

  async function loadMemory() {
    clearNode(elMemoryBody);
    elMemoryBody.appendChild(textEl("p", "copilot-muted", "Cargando memoria..."));
    try {
      const data = await api("/api/copilot/memory");
      renderMemory(data);
    } catch (err) {
      clearNode(elMemoryBody);
      elMemoryBody.appendChild(textEl("p", "copilot-error-text", "No se pudo cargar memoria: " + err.message));
    }
  }

  function renderMemory(data) {
    clearNode(elMemoryBody);
    const facts = data.facts || [];
    const prefs = data.preferences || [];
    if (facts.length === 0 && prefs.length === 0) {
      elMemoryBody.appendChild(textEl("p", "copilot-muted", "El copiloto aun no tiene memoria guardada."));
    }
    if (facts.length > 0) {
      elMemoryBody.appendChild(textEl("h3", "copilot-section-title", "Hechos"));
      const list = document.createElement("ul");
      list.className = "copilot-memory-list";
      for (const fact of facts) {
        const li = document.createElement("li");
        const body = document.createElement("div");
        body.appendChild(textEl("strong", "", fact.fact));
        const meta = [fact.source, fmtDate(fact.created_at)].filter(Boolean).join(" · ");
        if (meta) body.appendChild(textEl("span", "", meta));
        const del = document.createElement("button");
        del.type = "button";
        del.className = "copilot-icon-btn";
        del.textContent = "x";
        del.setAttribute("aria-label", "Borrar hecho");
        del.addEventListener("click", () => deleteFact(fact.id));
        li.appendChild(body);
        li.appendChild(del);
        list.appendChild(li);
      }
      elMemoryBody.appendChild(list);
    }
    if (prefs.length > 0) {
      elMemoryBody.appendChild(textEl("h3", "copilot-section-title", "Preferencias"));
      const list = document.createElement("dl");
      list.className = "copilot-pref-list";
      for (const pref of prefs) {
        list.appendChild(textEl("dt", "", pref.pref_key));
        list.appendChild(textEl("dd", "", pref.pref_value));
      }
      elMemoryBody.appendChild(list);
    }
  }

  async function saveFact(e) {
    e.preventDefault();
    const fact = (elMemoryFact.value || "").trim();
    const source = (elMemorySource.value || "explicit").trim() || "explicit";
    if (!fact) return;
    try {
      await api("/api/copilot/memory/fact", {
        method: "POST",
        body: JSON.stringify({ fact, source }),
      });
      elMemoryFact.value = "";
      toast("Hecho guardado en memoria.", "info");
      await loadMemory();
    } catch (err) {
      toast("No se pudo guardar memoria: " + err.message, "error");
    }
  }

  async function deleteFact(id) {
    try {
      await api("/api/copilot/memory/fact/" + encodeURIComponent(id), {
        method: "DELETE",
      });
      toast("Hecho borrado.", "info");
      await loadMemory();
    } catch (err) {
      toast("No se pudo borrar: " + err.message, "error");
    }
  }

  function openDraft(seed = "") {
    $("#copilot-draft-about").value = seed || "";
    elDraftOutput.hidden = true;
    elDraftOutput.textContent = "";
    elDraftCopy.disabled = true;
    lastDraftBody = "";
    openOverlay(elDraftModal);
  }

  async function generateDraft(e) {
    e.preventDefault();
    const about = ($("#copilot-draft-about").value || "").trim();
    if (!about) return;
    elDraftSubmit.disabled = true;
    elDraftSubmit.textContent = "Generando...";
    try {
      const data = await api("/api/copilot/drafts/generate", {
        method: "POST",
        body: JSON.stringify({
          kind: $("#copilot-draft-kind").value,
          about,
          audience: ($("#copilot-draft-audience").value || "").trim() || undefined,
          title: ($("#copilot-draft-subject").value || "").trim() || undefined,
          tone: $("#copilot-draft-tone").value,
        }),
      });
      const draft = data.draft || {};
      lastDraftBody = draft.body || "";
      clearNode(elDraftOutput);
      if (draft.title) elDraftOutput.appendChild(textEl("strong", "", draft.title));
      elDraftOutput.appendChild(textEl("pre", "", lastDraftBody || "Sin contenido."));
      elDraftOutput.hidden = false;
      elDraftCopy.disabled = !lastDraftBody;
      toast("Borrador generado.", "info");
    } catch (err) {
      toast("No se pudo generar: " + err.message, "error");
    } finally {
      elDraftSubmit.disabled = false;
      elDraftSubmit.textContent = "Generar";
    }
  }

  async function copyDraft() {
    if (!lastDraftBody) return;
    try {
      await navigator.clipboard.writeText(lastDraftBody);
      toast("Borrador copiado.", "info");
    } catch (_) {
      toast("No se pudo copiar. Selecciona el texto manualmente.", "error");
    }
  }

  async function openWorkflows() {
    openOverlay(elWorkflowModal);
    await loadWorkflows();
  }

  async function loadWorkflows() {
    clearNode(elWorkflowList);
    elWorkflowList.appendChild(textEl("p", "copilot-muted", "Cargando workflows..."));
    try {
      const data = await api("/api/copilot/workflow");
      renderWorkflows(data.workflows || []);
    } catch (err) {
      clearNode(elWorkflowList);
      elWorkflowList.appendChild(textEl("p", "copilot-error-text", "No se pudieron cargar workflows: " + err.message));
    }
  }

  function renderWorkflows(workflows) {
    clearNode(elWorkflowList);
    if (workflows.length === 0) {
      elWorkflowList.appendChild(textEl("p", "copilot-muted", "Sin workflows todavia."));
      return;
    }
    for (const wf of workflows) {
      const card = document.createElement("article");
      card.className = "copilot-workflow-card";
      card.appendChild(textEl("strong", "", wf.intent || "Workflow"));
      card.appendChild(textEl("span", "", "Estado: " + (wf.status || "unknown")));
      if (wf.error) card.appendChild(textEl("span", "copilot-error-text", wf.error));
      elWorkflowList.appendChild(card);
    }
  }

  async function createWorkflow(e) {
    e.preventDefault();
    const input = $("#copilot-workflow-intent");
    const intent = (input.value || "").trim();
    if (!intent) return;
    try {
      const created = await api("/api/copilot/workflow", {
        method: "POST",
        body: JSON.stringify({ intent }),
      });
      const workflow = created.workflow || {};
      toast(workflow.id ? "Workflow creado. Planear requiere permiso de ejecucion." : "Workflow creado.", "info");
      input.value = "";
      await loadWorkflows();
    } catch (err) {
      toast("No se pudo crear workflow: " + err.message, "error");
    }
  }


  elForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = (elPrompt.value || "").trim();
    if (text) sendMessage(text);
  });

  elPrompt.addEventListener("keydown", (e) => {
    if (e.key === "/" && !elPrompt.value.trim()) {
      e.preventDefault();
      openPalette("");
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      elForm.requestSubmit();
    }
  });

  elNewBtn.addEventListener("click", newConversation);
  elCommandBtn.addEventListener("click", () => openPalette(""));
  elMemoryBtn.addEventListener("click", openMemory);
  elDraftBtn.addEventListener("click", () => openDraft(elPrompt.value.trim()));
  elWorkflowsBtn.addEventListener("click", openWorkflows);
  elCommandFilter.addEventListener("input", renderCommands);
  elMemoryForm.addEventListener("submit", saveFact);
  elDraftForm.addEventListener("submit", generateDraft);
  elDraftCopy.addEventListener("click", copyDraft);
  elWorkflowForm.addEventListener("submit", createWorkflow);

  document.addEventListener("click", (e) => {
    const closeId = e.target && e.target.getAttribute
      ? e.target.getAttribute("data-close")
      : null;
    if (closeId) closeOverlay(document.getElementById(closeId));
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      [elPalette, elMemoryDrawer, elDraftModal, elWorkflowModal].forEach(closeOverlay);
    }
  });

  async function mountContextPanel() {
    if (!window.OmegaSecurityContext || typeof window.OmegaSecurityContext.load !== "function") {
      return;
    }
    const sidebar = document.querySelector(".copilot-sidebar");
    if (!sidebar) return;
    const footer = sidebar.querySelector(".copilot-sidebar-footer");
    let snap;
    try {
      snap = await window.OmegaSecurityContext.load();
    } catch (_) {
      return;
    }
    const panel = window.OmegaSecurityContext.renderCopilotContextPanel(snap);
    if (!panel) return;
    sidebar.querySelectorAll("#omega-sc-copilot-panel").forEach((n) => n.remove());
    if (footer) {
      sidebar.insertBefore(panel, footer);
    } else {
      sidebar.appendChild(panel);
    }
  }

  loadConversations();
  mountContextPanel();
})();
