// console/app/static/js/security_context_widget.js
//
// Reusable SaaS-context client for the legacy console UI.
//
// Single source of truth: GET /api/me/access (shipped in #174). Every
// page that wants to show the caller's identity, role, workspace and
// cartridge entitlements imports from here so the network call happens
// once per page-load and the rendering rules stay consistent.
//
// Rules enforced by this module:
//   - Never throws on 401/403/5xx; returns a normalized "anonymous" or
//     "limited" snapshot the caller can render gracefully.
//   - Uses fetch with credentials: "same-origin" (cookie auth).
//   - Never inserts user-controlled strings via innerHTML; everything
//     goes through createElement + textContent.
//   - Never persists state in localStorage; the snapshot lives on
//     window.__omegaSecurityContext for cross-script reads on the same
//     page, but is rebuilt on every page-load.

(function (global) {
  "use strict";

  const ENDPOINT = "/api/me/access";

  // ---------------------------------------------------------------------
  // Normalization — collapse the backend payload into a stable shape so
  // every UI surface reads from the same fields, even when the backend
  // returns partial data (e.g. marketplace pool down -> empty cartridges).
  // ---------------------------------------------------------------------

  function _str(value) {
    return value == null ? "" : String(value);
  }

  function normalize(payload) {
    payload = payload || {};
    const user = payload.user || {};
    const role = payload.role || {};
    const workspace = payload.workspace || {};
    const cartridges = payload.cartridges || {};
    const caps = payload.ui_capabilities || {};
    const permissions = Array.isArray(payload.permissions)
      ? payload.permissions.slice()
      : [];

    return {
      authenticated: Boolean(user.email || user.id),
      user: {
        id: user.id == null ? null : user.id,
        email: _str(user.email),
        name: _str(user.name) || _str(user.email),
      },
      role: {
        global: _str(role.global) || "anonymous",
        is_platform_admin: Boolean(role.is_platform_admin),
      },
      workspace: {
        tenant_id: _str(workspace.tenant_id),
        workspace_id: _str(workspace.workspace_id),
        workspace_role: workspace.workspace_role
          ? _str(workspace.workspace_role)
          : null,
      },
      permissions: permissions.map(_str),
      cartridges: {
        allowed: Array.isArray(cartridges.allowed)
          ? cartridges.allowed.map((row) => ({
              cartridge_id: _str(row && row.cartridge_id),
              product_name: _str(row && row.product_name) || _str(row && row.cartridge_id),
              status: _str(row && row.status),
            }))
          : [],
        denied: Array.isArray(cartridges.denied)
          ? cartridges.denied.map((row) => ({
              cartridge_id: _str(row && row.cartridge_id),
              product_name: _str(row && row.product_name) || _str(row && row.cartridge_id),
              reason: _str(row && row.reason) || "user_deny",
              installation_status: _str(row && row.installation_status),
            }))
          : [],
      },
      ui_capabilities: {
        can_view_iam: Boolean(caps.can_view_iam),
        can_admin_marketplace: Boolean(caps.can_admin_marketplace),
        can_admin_workspace: Boolean(caps.can_admin_workspace),
        can_view_audit: Boolean(caps.can_view_audit),
        can_view_sessions: Boolean(caps.can_view_sessions),
      },
      _source: "api",
    };
  }

  function emptySnapshot(reason) {
    return {
      authenticated: false,
      user: { id: null, email: "", name: "" },
      role: { global: "anonymous", is_platform_admin: false },
      workspace: { tenant_id: "", workspace_id: "", workspace_role: null },
      permissions: [],
      cartridges: { allowed: [], denied: [] },
      ui_capabilities: {
        can_view_iam: false,
        can_admin_marketplace: false,
        can_admin_workspace: false,
        can_view_audit: false,
        can_view_sessions: false,
      },
      _source: reason || "empty",
    };
  }

  // ---------------------------------------------------------------------
  // Fetch — single in-flight promise per page so multiple widgets on the
  // same page (Home banner + Copilot panel) share one network round-trip.
  // ---------------------------------------------------------------------

  let _inflight = null;

  async function loadSecurityContext({ force = false } = {}) {
    if (!force && _inflight) return _inflight;
    _inflight = (async () => {
      let response;
      try {
        response = await fetch(ENDPOINT, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
      } catch (_) {
        // Network unreachable. Return a benign empty snapshot so the
        // page still renders.
        const snap = emptySnapshot("network_error");
        global.__omegaSecurityContext = snap;
        return snap;
      }
      if (response.status === 401 || response.status === 403) {
        const snap = emptySnapshot("unauthorized");
        global.__omegaSecurityContext = snap;
        return snap;
      }
      if (!response.ok) {
        const snap = emptySnapshot("http_" + response.status);
        global.__omegaSecurityContext = snap;
        return snap;
      }
      let payload = null;
      try {
        payload = await response.json();
      } catch (_) {
        const snap = emptySnapshot("invalid_json");
        global.__omegaSecurityContext = snap;
        return snap;
      }
      const snap = normalize(payload);
      global.__omegaSecurityContext = snap;
      return snap;
    })();
    return _inflight;
  }

  // ---------------------------------------------------------------------
  // Render helpers — generic factories used by the page-specific
  // mount points. Every text node uses textContent; every element is
  // built with createElement; class names are static strings; ids are
  // namespaced under `omega-sc-`.
  // ---------------------------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function chip(text, variant) {
    const span = el("span", "omega-sc-chip" + (variant ? " omega-sc-chip-" + variant : ""), text);
    return span;
  }

  function rolePill(label, kind) {
    const span = el("span", "omega-sc-role" + (kind ? " omega-sc-role-" + kind : ""));
    span.textContent = (label || "—").toString();
    return span;
  }

  // ---------------------------------------------------------------------
  // Home compact identity bar.
  //
  // Anchored full-width above the hero. Three columns:
  //   left  -> identity (email + global role)
  //   mid   -> workspace context (workspace_id + workspace_role)
  //   right -> chips (permissions / allowed / denied) + "Mis accesos" link
  //
  // No actions are wired here besides the link to /mis-accesos which is
  // always safe for any authenticated user.
  // ---------------------------------------------------------------------

  function renderHomeIdentityBar(snap) {
    const bar = el("section", "home-identity-bar");
    bar.id = "omega-sc-home-bar";
    bar.setAttribute("aria-label", "Mi contexto SaaS");

    // Left — identity
    const left = el("div", "home-identity-left");
    if (snap.authenticated) {
      left.append(
        el("div", "home-identity-eyebrow", "Sesión activa"),
        el("div", "home-identity-email", snap.user.email || "(sin email)"),
      );
      const roleRow = el("div", "home-identity-roles");
      roleRow.append(
        el("span", "home-identity-label", "Rol global"),
        rolePill(
          snap.role.global,
          snap.role.is_platform_admin ? "platform" : "scoped",
        ),
      );
      left.append(roleRow);
    } else {
      left.append(
        el("div", "home-identity-eyebrow", "Sesión limitada"),
        el(
          "div",
          "home-identity-email",
          "No se pudo cargar tu contexto. Verifica tu sesión.",
        ),
      );
    }

    // Middle — workspace
    const mid = el("div", "home-identity-mid");
    if (snap.workspace.workspace_id) {
      mid.append(
        el("div", "home-identity-label", "Workspace activo"),
        el("div", "home-identity-workspace", snap.workspace.workspace_id),
      );
      const tenantLine = el("div", "home-identity-tenant");
      tenantLine.append(
        el("span", "home-identity-label", "Tenant"),
        el("span", null, snap.workspace.tenant_id || "—"),
      );
      mid.append(tenantLine);
      const wsRoleRow = el("div", "home-identity-roles");
      wsRoleRow.append(el("span", "home-identity-label", "Rol en workspace"));
      if (snap.workspace.workspace_role) {
        wsRoleRow.append(rolePill(snap.workspace.workspace_role, "workspace"));
      } else {
        wsRoleRow.append(rolePill("Sin rol asignado", "muted"));
      }
      mid.append(wsRoleRow);
    } else {
      // R1-UX gap #1: a user with no workspace assignment used to see
      // a silent "Sin workspace seleccionado" with no path forward.
      // Give them a clear next step.
      mid.append(
        el("div", "home-identity-label", "Workspace activo"),
        el("div", "home-identity-workspace home-identity-muted", "Sin workspace asignado"),
        el(
          "p",
          "home-identity-warning",
          "Pide a un administrador acceso a un workspace antes de operar. " +
          "Sin workspace activo Copiloto y Marketplace estarán limitados.",
        ),
      );
    }

    // Right — chips + link
    const right = el("div", "home-identity-right");
    const chipsWrap = el("div", "home-identity-chips");
    chipsWrap.append(
      chip(snap.permissions.length + " permisos", "perms"),
      chip(snap.cartridges.allowed.length + " cartuchos activos", "allow"),
    );
    if (snap.cartridges.denied.length > 0) {
      chipsWrap.append(
        chip(snap.cartridges.denied.length + " bloqueados", "deny"),
      );
    }
    right.append(chipsWrap);

    const link = el("a", "home-identity-cta", "Ver mis accesos →");
    link.href = "/mis-accesos";
    right.append(link);

    bar.append(left, mid, right);
    return bar;
  }

  // ---------------------------------------------------------------------
  // Copilot context panel — compact, fits in the existing sidebar
  // between the conversation list and the footer.
  //
  // Surfaces only what the copilot caller actually has at hand:
  //   - workspace + workspace_role
  //   - cartuchos disponibles (clickable -> filtran nada hoy, son label
  //     visual; el backend del copilot ya recibe el security_context
  //     en cada invoke).
  //   - cartuchos bloqueados con razón.
  //   - link a /mis-accesos.
  //
  // No new actions, no fake buttons, no LLM prompt changes.
  // ---------------------------------------------------------------------

  function renderCopilotContextPanel(snap) {
    const panel = el("section", "copilot-context-panel");
    panel.id = "omega-sc-copilot-panel";
    panel.setAttribute("aria-label", "Contexto del copiloto");

    panel.append(el("div", "copilot-context-eyebrow", "Tu contexto"));

    // Workspace row
    if (snap.workspace.workspace_id) {
      const wsRow = el("div", "copilot-context-row");
      wsRow.append(
        el("span", "copilot-context-label", "Workspace"),
        el("span", "copilot-context-value", snap.workspace.workspace_id),
      );
      panel.append(wsRow);
      if (snap.workspace.workspace_role) {
        const roleRow = el("div", "copilot-context-row");
        roleRow.append(
          el("span", "copilot-context-label", "Rol"),
          el("span", "copilot-context-value", snap.workspace.workspace_role),
        );
        panel.append(roleRow);
      }
    } else {
      // R1-UX gap #3: make the no-workspace state unmissable, not a soft
      // muted line. The copilot will run with restricted context so the
      // user needs to know up-front.
      const warn = el("div", "copilot-context-warning");
      warn.append(
        el("strong", null, "Sin workspace asignado"),
        el(
          "p",
          null,
          "El copiloto operará con contexto limitado. Pide a un administrador " +
          "acceso a un workspace antes de continuar.",
        ),
      );
      panel.append(warn);
    }

    // Allowed cartridges
    const allowed = snap.cartridges.allowed;
    panel.append(el("div", "copilot-context-section-title", "Cartuchos disponibles"));
    if (allowed.length === 0) {
      panel.append(
        el(
          "p",
          "copilot-context-muted",
          "No hay cartuchos activos para este workspace.",
        ),
      );
    } else {
      const list = el("ul", "copilot-context-list");
      allowed.forEach((row) => {
        const li = el("li", "copilot-context-item");
        li.append(
          el("span", "copilot-context-dot copilot-context-dot-ok"),
          el("span", "copilot-context-item-name", row.product_name),
          el("span", "copilot-context-item-id", row.cartridge_id),
        );
        list.append(li);
      });
      panel.append(list);
    }

    // Denied cartridges
    const denied = snap.cartridges.denied;
    if (denied.length > 0) {
      panel.append(el("div", "copilot-context-section-title", "Bloqueados para tu usuario"));
      const list = el("ul", "copilot-context-list");
      denied.forEach((row) => {
        const li = el("li", "copilot-context-item");
        li.append(
          el("span", "copilot-context-dot copilot-context-dot-deny"),
          el("span", "copilot-context-item-name", row.product_name),
          el("span", "copilot-context-item-id", row.cartridge_id),
        );
        list.append(li);
      });
      panel.append(list);
      panel.append(
        el(
          "p",
          "copilot-context-muted",
          "El copiloto no consultará estos cartuchos. Si crees que es un error, contacta al administrador.",
        ),
      );
    }

    // Link to full view
    const link = el("a", "copilot-context-cta", "Ver mis accesos →");
    link.href = "/mis-accesos";
    panel.append(link);

    return panel;
  }

  // ---------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------

  global.OmegaSecurityContext = {
    load: loadSecurityContext,
    normalize: normalize,
    emptySnapshot: emptySnapshot,
    renderHomeIdentityBar: renderHomeIdentityBar,
    renderCopilotContextPanel: renderCopilotContextPanel,
  };
})(typeof window !== "undefined" ? window : globalThis);
