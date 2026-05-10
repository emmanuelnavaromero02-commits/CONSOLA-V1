import {
  fetchAdminUsers,
  fetchAudit,
  fetchConfig,
  fetchCurrentUser,
  fetchMcpServers,
  fetchPermissions,
  fetchPipeline,
} from './api.js';
import { applyPermissionsFromRole, setState, state } from './state.js';
import { renderHome } from './render.js';

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
  const role = user?.role || 'anonymous';
  setState({ user, role });
  applyPermissionsFromRole(role);

  const config = await optional('config', fetchConfig, {});
  if (config.workspace_url) setState({ workspaceUrl: config.workspace_url });

  const permissions = await optional('permissions', fetchPermissions, null);
  if (permissions) applyPermissionRegistry(permissions, role);

  const mcp = await optional('mcp', fetchMcpServers, null);
  if (mcp) {
    const servers = mcp.servers || [];
    const healthy = servers.filter((server) => server.healthy).length;
    state.statuses.mcp = status('MCP', `${healthy}/${servers.length} services`, healthy ? 'online' : 'unknown');
    state.statuses.replicon = status('Replicon', servers.some((server) => String(server.name || server.id || '').includes('replicon')) ? 'Registered' : 'Not checked', 'unknown');
  } else {
    state.statuses.mcp = status('MCP', 'Limited access', 'unknown');
    state.statuses.replicon = status('Replicon', 'Not checked', 'unknown');
  }

  state.statuses.console = status('Console', 'Online', 'online');
  state.statuses.workspace = status('Workspace', state.workspaceUrl ? 'Configured' : 'Not checked', state.workspaceUrl ? 'online' : 'unknown');
  state.statuses.airflow = status('Airflow', 'Not checked', 'unknown');
  state.statuses.minio = status('MinIO / Bronze', 'Not checked', 'unknown');

  const adminUsers = await optional('admin users', fetchAdminUsers, null);
  if (adminUsers) setState({ usersSummary: summarizeUsers(adminUsers) });

  const audit = await optional('audit', fetchAudit, null);
  state.activity.audit = audit ? `${audit.length} recent events` : 'Audit limited';

  const pipeline = await optional('pipeline', fetchPipeline, null);
  if (pipeline?.pipeline) {
    state.activity.pipeline = `${pipeline.pipeline.length} pipeline rows available`;
  } else {
    state.activity.pipeline = 'Pipeline not checked';
  }

  setState({ loading: false });
}

export async function initHomeControlPlane() {
  const root = document.getElementById('home-control-plane-root');
  if (!root) return;
  try {
    await loadHomeData();
    renderHome(root);
  } catch (error) {
    console.warn('Home control plane fallback:', error);
  }
}

document.addEventListener('DOMContentLoaded', initHomeControlPlane);
