// Sprint v1.42 — Copilot chat client.
//
// Every fetch reads CSRF from cookie (the console's strict CSP blocks
// inline scripts, so no <script> body is doing this work). All dynamic
// DOM is built with textContent / createElement — no innerHTML on
// user-controlled strings — so even an injected message body can't
// inject HTML.

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

  let activeConversationId = null;

  // ── Helpers ──────────────────────────────────────────────────────────

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

  // ── Renderers ────────────────────────────────────────────────────────

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
    args.textContent = JSON.stringify(call.args || {}, null, 2);
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
    title.textContent = "⚠️ Acción destructiva — requiere tu aprobación";
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

  // ── Sidebar / conversation list ──────────────────────────────────────

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
          for (const c of m.tool_calls) {
            if (c.risk_level === "destructive" && c.approval_key) {
              // Render the pending approval card on history reload too.
              appendApprovalCard([c], m.id, id);
            } else {
              appendToolCard(c);
            }
          }
        }
      }
      // Refresh the sidebar to flip active state.
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

  // ── Send / approve ───────────────────────────────────────────────────

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
    if (out.requires_approval && Array.isArray(out.pending_actions)) {
      appendApprovalCard(out.pending_actions, out.message_id, activeConversationId);
    }
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

  // ── Wire up ──────────────────────────────────────────────────────────

  elForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = (elPrompt.value || "").trim();
    if (text) sendMessage(text);
  });

  elPrompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      elForm.requestSubmit();
    }
  });

  elNewBtn.addEventListener("click", newConversation);

  // Initial load.
  loadConversations();
})();
