// Sprint Phase-0 SaaS controls — Mis accesos.
//
// Purpose: render a single, honest, permission-aware view of the
// caller's identity, workspace, effective permissions and cartridge
// access. No fake buttons; every interactive element is gated by the
// `ui_capabilities` flags that the backend returns.

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value || "—";
}

function showErr(msg) {
  const banner = $("err-banner");
  banner.textContent = msg;
  banner.style.display = "";
}

function renderRole(pillId, label, kind) {
  const el = $(pillId);
  el.textContent = (label || "—").toString().toUpperCase();
  el.classList.remove("platform", "muted");
  if (kind === "platform") el.classList.add("platform");
  else if (!label || label === "—") el.classList.add("muted");
}

function renderPermissions(perms) {
  const list = $("perm-list");
  list.replaceChildren();
  if (!perms || perms.length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Sin permisos asignados.";
    list.appendChild(li);
    return;
  }
  for (const p of perms) {
    const li = document.createElement("li");
    li.textContent = p;
    list.appendChild(li);
  }
}

function renderCartridges(containerId, items, badgeClass, badgeText, emptyMsg) {
  const container = $(containerId);
  container.replaceChildren();
  if (!items || items.length === 0) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = emptyMsg;
    container.appendChild(div);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "cart-row";
    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "cart-name";
    name.textContent = item.product_name || item.cartridge_id;
    const id = document.createElement("div");
    id.className = "cart-id";
    id.textContent = item.cartridge_id;
    left.appendChild(name);
    left.appendChild(id);
    const right = document.createElement("div");
    const badge = document.createElement("span");
    badge.className = `badge ${badgeClass}`;
    badge.textContent = badgeText;
    right.appendChild(badge);
    if (item.status || item.installation_status) {
      const st = document.createElement("span");
      st.className = "badge status";
      st.textContent = (item.status || item.installation_status || "")
        .toString()
        .toUpperCase();
      right.appendChild(st);
    }
    row.appendChild(left);
    row.appendChild(right);
    container.appendChild(row);
  }
}

async function load() {
  let r;
  try {
    r = await fetch("/api/me/access", { credentials: "same-origin" });
  } catch (err) {
    showErr("No se pudo consultar /api/me/access: " + esc(err.message || err));
    return;
  }
  if (r.status === 401) {
    location.href = "/login";
    return;
  }
  if (!r.ok) {
    showErr("Error " + r.status + " al cargar /api/me/access.");
    return;
  }
  const data = await r.json();

  setText("me-email", data.user && data.user.email);
  setText("me-name",  data.user && data.user.name);
  setText("me-tenant",    (data.workspace && data.workspace.tenant_id) || "—");
  setText("me-workspace", (data.workspace && data.workspace.workspace_id) || "—");

  const role = (data.role && data.role.global) || "user";
  renderRole(
    "me-role",
    role,
    (data.role && data.role.is_platform_admin) ? "platform" : "default",
  );

  const wsRole = (data.workspace && data.workspace.workspace_role) || null;
  renderRole("me-ws-role", wsRole || "—", wsRole ? "default" : "muted");
  if (!wsRole) {
    $("ws-role-note").textContent =
      "No tienes un rol asignado en este workspace; solo aplican los permisos del rol global.";
  } else if (wsRole === "workspace_admin") {
    $("ws-role-note").textContent =
      "Eres administrador de este workspace. Esta NO es una cuenta de platform admin: no puedes tocar configuración global, Vault global ni la administración del Marketplace.";
  }

  renderPermissions(data.permissions);

  const allowed = (data.cartridges && data.cartridges.allowed) || [];
  const denied  = (data.cartridges && data.cartridges.denied) || [];
  renderCartridges(
    "cart-allowed",
    allowed,
    "allow",
    "ACCESO",
    "Aún no tienes cartuchos activos en este workspace. Pide al administrador del workspace que active uno desde Marketplace.",
  );
  renderCartridges(
    "cart-denied",
    denied,
    "deny",
    "BLOQUEADO",
    "No tienes ningún cartucho bloqueado por configuración explícita.",
  );

  const caps = data.ui_capabilities || {};
  if (caps.can_view_sessions)   $("link-sessions").style.display = "";
  if (caps.can_view_iam)        $("link-iam").style.display = "";
  if (caps.can_admin_marketplace) $("link-marketplace-admin").style.display = "";
}

document.addEventListener("DOMContentLoaded", load);
