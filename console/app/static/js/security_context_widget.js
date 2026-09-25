(function (global) {
  "use strict";

  const ENDPOINT = "/api/me/access";


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


  function renderHomeIdentityBar(snap) {
    const bar = el("section", "home-identity-bar");
    bar.id = "omega-sc-home-bar";
    bar.setAttribute("aria-label", "Mi contexto SaaS");

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


  function renderCopilotContextPanel(snap) {
    const panel = el("section", "copilot-context-panel");
    panel.id = "omega-sc-copilot-panel";
    panel.setAttribute("aria-label", "Contexto del copiloto");

    panel.append(el("div", "copilot-context-eyebrow", "Tu contexto"));

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

    const link = el("a", "copilot-context-cta", "Ver mis accesos →");
    link.href = "/mis-accesos";
    panel.append(link);

    return panel;
  }


  global.OmegaSecurityContext = {
    load: loadSecurityContext,
    normalize: normalize,
    emptySnapshot: emptySnapshot,
    renderHomeIdentityBar: renderHomeIdentityBar,
    renderCopilotContextPanel: renderCopilotContextPanel,
  };
})(typeof window !== "undefined" ? window : globalThis);
