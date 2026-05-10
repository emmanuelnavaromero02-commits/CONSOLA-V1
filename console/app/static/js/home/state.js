export const state = {
  user: null,
  role: 'anonymous',
  workspaceUrl: '/workspace',
  permissions: null,
  effectivePermissions: new Set(),
  usersSummary: null,
  statuses: {},
  activity: {},
  loading: true,
  errors: [],
};

export function setState(patch) {
  Object.assign(state, patch);
}

const fallbackPermissions = {
  admin: [
    'workspace.access', 'monitor.read', 'studio.read', 'studio.write',
    'pipelines.read', 'pipelines.run', 'datasets.read', 'datasets.write',
    'iam.users.read', 'iam.users.write', 'iam.roles.read',
    'security.audit.read', 'security.sessions.read',
    'vault.connections.read', 'vault.connections.write',
  ],
  security_admin: ['monitor.read', 'iam.users.read', 'iam.users.write', 'iam.roles.read', 'security.audit.read', 'security.sessions.read', 'vault.connections.read'],
  workspace_admin: ['workspace.access', 'monitor.read', 'studio.read', 'studio.write', 'pipelines.read', 'pipelines.run', 'datasets.read', 'datasets.write', 'vault.connections.read'],
  analyst: ['workspace.access', 'monitor.read', 'studio.read', 'pipelines.read', 'datasets.read'],
  auditor: ['monitor.read', 'security.audit.read', 'security.sessions.read', 'iam.roles.read'],
  viewer: ['workspace.access', 'monitor.read', 'studio.read', 'pipelines.read', 'datasets.read'],
  workspace_user: ['workspace.access'],
  user: ['workspace.access', 'monitor.read', 'studio.read'],
};

export function applyPermissionsFromRole(role) {
  const keys = fallbackPermissions[role] || [];
  state.effectivePermissions = new Set(keys);
}

export function hasPermission(permission) {
  return state.effectivePermissions.has(permission);
}
