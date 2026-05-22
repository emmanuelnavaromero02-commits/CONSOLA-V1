// console/app/static/js/my_access.js
//
// Phase-3 — render the "Mis accesos" page from the shared
// OmegaSecurityContext widget. Everything here is permission-aware and
// strictly read-only: there are no mutating actions on this page; the
// links that DO appear (sessions, IAM, marketplace admin) are gated by
// the same ui_capabilities flags the backend exposes, so the user
// never sees a link that would 403.
//
// No innerHTML on backend strings. Every dynamic node uses textContent
// + createElement. The static <a href="/me"> for password change is
// authored in the HTML.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------------
  // Permission grouping — collapses ~30+ permission keys into a small
  // set of human-meaningful domains. The role of this view is to TELL
  // the user "what you can do here", not to dump a registry.
  // ---------------------------------------------------------------------

  const DOMAIN_ORDER = [
    "copilot",
    "workspace",
    "marketplace",
    "datasets",
    "pipelines",
    "studio",
    "monitor",
    "apps",
    "iam",
    "security",
    "vault",
    "settings",
    "operations",
    "cartridges",
    "mcp",
  ];

  const DOMAIN_LABELS = {
    copilot:     "Copiloto",
    workspace:   "Workspace",
    marketplace: "Marketplace",
    datasets:    "Datos",
    pipelines:   "Flujos",
    studio:      "Studio (interno)",
    monitor:     "Monitor",
    apps:        "Apps publicadas",
    iam:         "IAM / Usuarios",
    security:    "Seguridad",
    vault:       "Vault",
    settings:    "Configuración",
    operations:  "Operaciones",
    cartridges:  "Cartuchos",
    mcp:         "MCP",
  };

  function groupPermissions(perms) {
    const groups = new Map();
    DOMAIN_ORDER.forEach((key) => groups.set(key, []));
    groups.set("_other", []);
    (perms || []).forEach((p) => {
      const dot = String(p).indexOf(".");
      const domain = dot > 0 ? String(p).slice(0, dot) : "_other";
      if (groups.has(domain)) groups.get(domain).push(p);
      else groups.get("_other").push(p);
    });
    return groups;
  }

  function renderPermissionsByDomain(perms) {
    const host = $("perm-groups");
    if (!host) return;
    host.replaceChildren();
    const groups = groupPermissions(perms);
    let rendered = 0;
    for (const domain of DOMAIN_ORDER) {
      const keys = groups.get(domain) || [];
      if (keys.length === 0) continue;
      rendered += 1;
      const block = document.createElement("section");
      block.className = "perm-group";

      const head = document.createElement("div");
      head.className = "perm-group-head";
      const title = document.createElement("span");
      title.className = "perm-group-title";
      title.textContent = DOMAIN_LABELS[domain] || domain;
      const count = document.createElement("span");
      count.className = "perm-group-count";
      count.textContent = String(keys.length);
      head.append(title, count);
      block.append(head);

      const list = document.createElement("ul");
      list.className = "perm-list";
      keys.sort().forEach((p) => {
        const li = document.createElement("li");
        li.textContent = p;
        list.append(li);
      });
      block.append(list);
      host.append(block);
    }
    const other = groups.get("_other") || [];
    if (other.length > 0) {
      const block = document.createElement("section");
      block.className = "perm-group";
      const head = document.createElement("div");
      head.className = "perm-group-head";
      const title = document.createElement("span");
      title.className = "perm-group-title";
      title.textContent = "Otros";
      const count = document.createElement("span");
      count.className = "perm-group-count";
      count.textContent = String(other.length);
      head.append(title, count);
      block.append(head);
      const list = document.createElement("ul");
      list.className = "perm-list";
      other.sort().forEach((p) => {
        const li = document.createElement("li");
        li.textContent = p;
        list.append(li);
      });
      block.append(list);
      host.append(block);
    }
    if (rendered === 0 && other.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Sin permisos asignados.";
      host.append(empty);
    }
  }

  // ---------------------------------------------------------------------
  // Role helpers — explain the difference between global role and
  // workspace_role in copy the user can act on.
  // ---------------------------------------------------------------------

  const GLOBAL_ROLE_HINT = {
    owner:          "Eres dueño de la plataforma. Tienes acceso total.",
    super_admin:    "Eres administrador global. Acceso total cross-tenant.",
    admin:          "Eres administrador global. Operas la plataforma para todos los workspaces.",
    security_admin: "Administras seguridad e IAM, sin permisos productivos por defecto.",
    auditor:        "Acceso de solo lectura para auditoría.",
    workspace_admin: "Tu rol global es de workspace. No puedes administrar la plataforma; sí tu workspace.",
    analyst:        "Puedes consultar datos y monitoreo. Sin cambios de configuración.",
    viewer:         "Solo lectura.",
    workspace_user: "Acceso restringido a tu workspace.",
    user:           "Usuario estándar.",
    anonymous:      "Sesión no autenticada.",
  };

  const WS_ROLE_HINT = {
    workspace_admin:
      "Administras este workspace: usuarios, cartuchos y datos del workspace. " +
      "NO administras la plataforma global ni otros workspaces.",
    analyst:
      "Puedes consultar datos del workspace; sin cambios destructivos.",
    viewer:
      "Solo lectura en este workspace.",
    workspace_user:
      "Acceso operativo limitado al workspace.",
  };

  function setRolePill(id, label, kind) {
    const el = $(id);
    if (!el) return;
    el.textContent = (label || "—").toUpperCase();
    el.classList.remove("platform", "muted", "workspace", "scoped");
    if (kind) el.classList.add(kind);
  }

  // ---------------------------------------------------------------------
  // Cartridge cards — cleaner layout than the previous status badges.
  // ---------------------------------------------------------------------

  const STATUS_COPY = {
    active: ["Activo", "ok"],
    ready: ["Activo", "ok"],
    pending_approval: ["Pendiente de aprobación", "warn"],
    pending_connection: ["Pendiente de conexión", "warn"],
    waiting_credentials: ["Requiere credenciales", "warn"],
    requested: ["Solicitado", "warn"],
    failed: ["Con falla", "deny"],
    paused: ["Pausado", "warn"],
    revoked: ["Revocado", "deny"],
    expired: ["Expirado", "deny"],
    suspended: ["Suspendido", "deny"],
  };

  function statusBadge(status) {
    const key = String(status || "").toLowerCase();
    const meta = STATUS_COPY[key];
    const badge = document.createElement("span");
    badge.className = "cart-status " + (meta ? meta[1] : "muted");
    badge.textContent = meta ? meta[0] : (status ? status.toUpperCase() : "—");
    return badge;
  }

  function renderCartridgeList(containerId, items, mode, emptyMsg) {
    const host = $(containerId);
    if (!host) return;
    host.replaceChildren();
    if (!items || items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = emptyMsg;
      host.append(empty);
      return;
    }
    const grid = document.createElement("div");
    grid.className = "cart-grid";
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "cart-card " + (mode === "deny" ? "cart-card-deny" : "cart-card-allow");

      const head = document.createElement("div");
      head.className = "cart-card-head";
      const name = document.createElement("div");
      name.className = "cart-card-name";
      name.textContent = item.product_name || item.cartridge_id;
      const id = document.createElement("div");
      id.className = "cart-card-id";
      id.textContent = item.cartridge_id;
      head.append(name, id);

      const meta = document.createElement("div");
      meta.className = "cart-card-meta";
      if (mode === "deny") {
        const reason = document.createElement("span");
        reason.className = "cart-status deny";
        reason.textContent = "BLOQUEADO";
        meta.append(reason);
        if (item.installation_status) {
          const sub = document.createElement("span");
          sub.className = "cart-substatus";
          sub.textContent = "Instalación: " + (item.installation_status || "—");
          meta.append(sub);
        }
      } else {
        meta.append(statusBadge(item.status));
      }
      card.append(head, meta);
      grid.append(card);
    });
    host.append(grid);
  }

  // ---------------------------------------------------------------------
  // Capability link wiring — every link below the page footer is hidden
  // by default and only revealed when the corresponding ui_capabilities
  // flag (mirrored from backend) is true. The flags already replicate
  // the full guard chain of the destination page (#174).
  // ---------------------------------------------------------------------

  function applyCapLinks(caps) {
    const showIf = (id, flag) => {
      const el = $(id);
      if (!el) return;
      el.style.display = flag ? "" : "none";
    };
    showIf("link-sessions", caps.can_view_sessions);
    showIf("link-iam", caps.can_view_iam);
    showIf("link-marketplace-admin", caps.can_admin_marketplace);
    showIf("link-audit", caps.can_view_audit);
    showIf("link-workspace-admin", caps.can_admin_workspace);
  }

  // ---------------------------------------------------------------------
  // Error / loading state
  // ---------------------------------------------------------------------

  function showError(msg) {
    const banner = $("err-banner");
    if (!banner) return;
    banner.textContent = msg;
    banner.style.display = "";
  }

  // ---------------------------------------------------------------------
  // Bootstrap
  // ---------------------------------------------------------------------

  async function load() {
    if (!window.OmegaSecurityContext || typeof window.OmegaSecurityContext.load !== "function") {
      showError(
        "No se pudo inicializar el cliente de contexto SaaS. Recarga la página.",
      );
      return;
    }
    let snap;
    try {
      snap = await window.OmegaSecurityContext.load();
    } catch (err) {
      showError("No se pudo consultar /api/me/access: " + (err && err.message ? err.message : err));
      return;
    }
    if (!snap.authenticated) {
      // Either the user was logged out or the endpoint returned 401/403.
      // We redirect to /login because the rest of the page would be empty.
      window.location.href = "/login";
      return;
    }

    // Identity block (matches the existing static HTML containers).
    const setText = (id, value) => {
      const el = $(id);
      if (el) el.textContent = value || "—";
    };
    setText("me-email", snap.user.email);
    setText("me-name", snap.user.name);
    setText("me-tenant", snap.workspace.tenant_id);
    setText("me-workspace", snap.workspace.workspace_id);

    setRolePill(
      "me-role",
      snap.role.global,
      snap.role.is_platform_admin ? "platform" : "scoped",
    );
    setRolePill(
      "me-ws-role",
      snap.workspace.workspace_role || "—",
      snap.workspace.workspace_role ? "workspace" : "muted",
    );

    // Inline hint that explains the two role kinds in plain language.
    const noteEl = $("ws-role-note");
    if (noteEl) {
      const globalHint = GLOBAL_ROLE_HINT[snap.role.global] || "";
      const wsKey = snap.workspace.workspace_role;
      const wsHint = wsKey ? (WS_ROLE_HINT[wsKey] || "") : "No tienes un rol asignado en este workspace; solo aplican los permisos del rol global.";
      noteEl.replaceChildren();
      if (globalHint) {
        const p = document.createElement("p");
        p.className = "role-hint";
        const tag = document.createElement("strong");
        tag.textContent = "Rol global: ";
        p.append(tag, document.createTextNode(globalHint));
        noteEl.append(p);
      }
      if (wsHint) {
        const p = document.createElement("p");
        p.className = "role-hint";
        const tag = document.createElement("strong");
        tag.textContent = "Rol en workspace: ";
        p.append(tag, document.createTextNode(wsHint));
        noteEl.append(p);
      }
    }

    // Permission domains.
    renderPermissionsByDomain(snap.permissions);

    // Cartridge cards.
    //
    // R1-UX gap #2: when BOTH lists are empty, the page used to render
    // two stacked empty cards which felt broken. Collapse the deny card
    // entirely and surface a single, actionable empty state on the
    // allowed card.
    const hasAny =
      snap.cartridges.allowed.length > 0 || snap.cartridges.denied.length > 0;
    if (!hasAny) {
      renderCartridgeList(
        "cart-allowed",
        [],
        "allow",
        "No hay cartuchos visibles para tu usuario en este workspace. " +
        "Si esperabas alguno, pídelo en Marketplace (un administrador deberá " +
        "aprobarlo) o pide a tu administrador que revise tus permisos.",
      );
      const deniedCard = document.getElementById("cart-denied");
      if (deniedCard && deniedCard.parentElement) {
        const section = deniedCard.closest("section.card");
        if (section) section.style.display = "none";
      }
    } else {
      renderCartridgeList(
        "cart-allowed",
        snap.cartridges.allowed,
        "allow",
        "Aún no tienes cartuchos activos en este workspace. " +
        "Pide al administrador del workspace que active uno desde Marketplace.",
      );
      renderCartridgeList(
        "cart-denied",
        snap.cartridges.denied,
        "deny",
        "No tienes ningún cartucho bloqueado por configuración explícita.",
      );
      // If the deny list is empty but allowed has rows, hide the deny
      // card too — there is nothing useful to show.
      if (snap.cartridges.denied.length === 0) {
        const deniedCard = document.getElementById("cart-denied");
        if (deniedCard) {
          const section = deniedCard.closest("section.card");
          if (section) section.style.display = "none";
        }
      }
    }

    // Cap-gated links.
    applyCapLinks(snap.ui_capabilities);
  }

  document.addEventListener("DOMContentLoaded", load);
})();
