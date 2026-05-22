import {
  fetchAdminUsers,
  fetchAudit,
  fetchConfig,
  fetchCurrentUser,
  fetchMcpServers,
  fetchPermissions,
  fetchPipeline,
  fetchSystemInfo,
  fetchHealth,
} from './api.js';
import { applyPermissionsFromRole, setState, state } from './state.js';
import { renderHome, renderVersionBadge } from './render.js';
import { renderMarketplace, renderMarketplaceAdmin } from '../marketplace.js';
import { cycleTheme } from '../theme.js';
import { humanizeError, humanizeTerm } from '../i18n/labels.js';

function status(name, label, value = 'unknown') {
  return { name, label, status: value };
}

function summarizeUsers(payload) {
  const users = payload.users || [];
  return {
    total: users.length,
    active: users.filter((user) => user.is_active).length,
    inactive: users.filter((user) => !user.is_active).length,
    admins: users.filter((user) => ['owner', 'super_admin', 'admin'].includes(user.role)).length,
  };
}

function applyPermissionRegistry(registry, role) {
  const matrix = registry?.matrix || {};
  const row = matrix[role] || {};
  const keys = Object.keys(row).filter((key) => row[key]);
  if (keys.length) {
    if (['owner', 'super_admin', 'admin'].includes(role)) {
      keys.push('marketplace.read', 'marketplace.request', 'marketplace.admin');
    }
    state.effectivePermissions = new Set(keys);
  }
  setState({ permissions: registry });
}

async function optional(label, fn, fallback) {
  try {
    return await fn();
  } catch (error) {
    state.errors.push(`${label}: ${error.status || ''} ${error.message}`);
    return fallback;
  }
}

async function loadHomeData() {
  const user = await optional('current user', fetchCurrentUser, null);
  const role = user?.workspace_role || user?.role || 'anonymous';
  setState({ user, role });
  applyPermissionsFromRole(role);

  const config = await optional('config', fetchConfig, {});
  if (config.workspace_url) setState({ workspaceUrl: config.workspace_url });

  const permissions = await optional('permissions', fetchPermissions, null);
  if (permissions) applyPermissionRegistry(permissions, role);
  if (Array.isArray(user?.permissions)) {
    state.effectivePermissions = new Set(user.permissions);
  }

  const mcp = await optional('mcp', fetchMcpServers, null);
  if (mcp) {
    const servers = mcp.servers || [];
    const healthy = servers.filter((server) => server.healthy).length;
    state.statuses.mcp = status(humanizeTerm('mcp'), `${healthy}/${servers.length} servicios`, healthy ? 'online' : 'unknown');
    state.statuses.replicon = status('Replicon', servers.some((server) => String(server.name || server.id || '').includes('replicon')) ? 'Registrado' : 'No verificado', 'unknown');
  } else {
    state.statuses.mcp = status(humanizeTerm('mcp'), 'Acceso limitado', 'unknown');
    state.statuses.replicon = status('Replicon', 'No verificado', 'unknown');
  }

  state.statuses.console = status('OMEGA', 'En línea', 'online');
  state.statuses.workspace = status('Workspace', state.workspaceUrl ? 'Configurado' : 'No verificado', state.workspaceUrl ? 'online' : 'unknown');
  state.statuses.airflow = status('Airflow', 'No verificado', 'unknown');
  state.statuses.minio = status('MinIO', 'No verificado', 'unknown');

  const adminUsers = await optional('admin users', fetchAdminUsers, null);
  if (adminUsers) setState({ usersSummary: summarizeUsers(adminUsers) });

  const audit = await optional('audit', fetchAudit, null);
  state.activity.audit = audit ? `${audit.length} eventos recientes` : 'Auditoría con acceso limitado';

  const pipeline = await optional('pipeline', fetchPipeline, null);
  if (pipeline?.pipeline) {
    state.activity.pipeline = `${pipeline.pipeline.length} registros del flujo disponibles`;
  } else {
    state.activity.pipeline = 'Flujo automático no verificado';
  }

  setState({ loading: false });
}

export async function initHomeControlPlane() {
  const root = document.getElementById('home-control-plane-root');
  if (!root) return;
  try {
    await loadHomeData();
    if (window.location.pathname === '/marketplace' || window.location.pathname === '/customer/cartridges') {
      await renderMarketplace(root);
    } else if (window.location.pathname === '/admin/installations' || window.location.pathname === '/admin/licenses') {
      await renderMarketplaceAdmin(root);
    } else {
      renderHome(root);
    }
  } catch (error) {
    console.warn('Home control plane fallback:', humanizeError(error));
  }
  Promise.all([fetchSystemInfo(), fetchHealth()])
    .then(([info, health]) => renderVersionBadge(info, health))
    .catch((err) => { console.warn('version badge fallback:', err); renderVersionBadge(null, null); });
}

document.addEventListener('DOMContentLoaded', initHomeControlPlane);

document.addEventListener('click', async (event) => {
  const themeToggle = event.target.closest('[data-theme-toggle]');
  if (themeToggle) {
    const next = cycleTheme();
    themeToggle.textContent = next === 'light' ? 'Tema: claro' : next === 'dark' ? 'Tema: oscuro' : 'Tema: sistema';
    return;
  }

  const logout = event.target.closest('[data-logout]');
  if (logout) {
    // Sprint v1.9 CSRF: echo back the csrf_token cookie value on the
    // logout POST. The cookie was set on /login and rotated after a
    // successful authentication, so it's present whenever this code path
    // runs (only logged-in users reach the home).
    const csrfMatch = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    const csrf = csrfMatch ? decodeURIComponent(csrfMatch[1]) : '';
    await fetch('/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: csrf ? { 'X-CSRF-Token': csrf } : {},
    }).catch(() => null);
    window.location.href = '/login';
  }
});

document.addEventListener('toggle', (event) => {
  const menu = event.target;
  if (!menu.matches?.('.home-action-menu') || !menu.open) return;
  document.querySelectorAll('.home-action-menu[open]').forEach((item) => {
    if (item !== menu) item.removeAttribute('open');
  });
}, true);
